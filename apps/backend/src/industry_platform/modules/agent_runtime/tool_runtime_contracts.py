"""Stable policies and commands for Day 3 bounded Tool Runtime levels."""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final
from uuid import UUID

from industry_platform.modules.agent_runtime.context import (
    CONTEXT_COMPILER_V1,
    MAX_CONTEXT_QUESTION_LENGTH,
    MAX_CONTEXT_SUMMARY_LENGTH,
    MAX_CONTEXT_TOOL_OBSERVATIONS,
    AttachmentContextSource,
    MemoryContextBundle,
    validate_attachment_context_sources,
)
from industry_platform.modules.agent_runtime.domain import (
    MAX_RUN_TOKENS,
    AgentRun,
    AgentRunStatus,
    AgentRunType,
    require_current_schema_version,
    require_non_nil_uuid,
    snapshot_json_mapping,
)
from industry_platform.modules.agent_runtime.final_output import (
    FINAL_MARKDOWN_CONTRACT_VERSION,
    MAX_FINAL_MARKDOWN_LENGTH,
)
from industry_platform.modules.agent_runtime.state import RunState, validate_run_state
from industry_platform.modules.tools.domain import (
    MAX_TOOL_ACTION_BYTES,
    TOOL_ACTION_SCHEMA_VERSION,
    ToolAction,
    ToolDefinition,
    ToolReference,
    side_effect_idempotency_key_sha256,
    tool_action_response_schema,
    tool_references,
)

TOOL_L1_RUNTIME_VERSION: Final = "agent-runtime-v1"
TOOL_L2_RUNTIME_VERSION: Final = TOOL_L1_RUNTIME_VERSION
MAX_TOOL_LOOP_DECISION_BYTES: Final = MAX_FINAL_MARKDOWN_LENGTH * 4 + MAX_TOOL_ACTION_BYTES

_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,127}$")
_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,199}$")


@dataclass(frozen=True, slots=True)
class ToolL1RuntimePolicy:
    """Trusted single-Tool surface and model/context budgets for one L1 profile."""

    schema_version: int
    profile_version: str
    prompt_version: str
    context_compiler_version: str
    output_contract_version: str
    toolset_version: str
    model: str
    max_input_tokens: int
    max_action_output_tokens: int
    max_final_output_tokens: int
    system_instructions: str = field(repr=False)
    available_tools: tuple[ToolReference, ...]
    model_call_limit: int = field(default=2, init=False)
    tool_call_limit: int = field(default=1, init=False)
    allows_implicit_retry: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        require_current_schema_version(self.schema_version)
        for value, field_name in (
            (self.profile_version, "Tool L1 profile version"),
            (self.prompt_version, "Tool L1 prompt version"),
            (self.context_compiler_version, "Tool L1 Context Compiler version"),
            (self.output_contract_version, "Tool L1 output contract version"),
            (self.toolset_version, "Tool L1 toolset version"),
        ):
            if not _VERSION_PATTERN.fullmatch(value):
                raise ValueError(f"{field_name} is invalid")
        if self.context_compiler_version != CONTEXT_COMPILER_V1:
            raise ValueError("Tool L1 requires Context Compiler v1")
        if self.output_contract_version != FINAL_MARKDOWN_CONTRACT_VERSION:
            raise ValueError("Tool L1 final output contract is unsupported")
        if not _MODEL_PATTERN.fullmatch(self.model):
            raise ValueError("Tool L1 model is invalid")
        for token_limit, field_name in (
            (self.max_input_tokens, "Tool L1 max input tokens"),
            (self.max_action_output_tokens, "Tool L1 action output tokens"),
            (self.max_final_output_tokens, "Tool L1 final output tokens"),
        ):
            if isinstance(token_limit, bool) or not 1 <= token_limit <= MAX_RUN_TOKENS:
                raise ValueError(f"{field_name} is invalid")
        instructions = self.system_instructions.strip()
        if not instructions or len(instructions) > 20_000:
            raise ValueError("Tool L1 system instructions are invalid")
        references = tool_references(self.available_tools)
        if len(references) != 1:
            raise ValueError("The L1 profile must expose exactly one Tool version")
        object.__setattr__(self, "system_instructions", instructions)
        object.__setattr__(self, "available_tools", references)


@dataclass(frozen=True, slots=True)
class ToolL2RuntimePolicy:
    """Trusted bounded-loop surface for repeated use of an exact Tool allowlist."""

    schema_version: int
    profile_version: str
    prompt_version: str
    context_compiler_version: str
    output_contract_version: str
    toolset_version: str
    model: str
    max_input_tokens: int
    max_decision_output_tokens: int
    max_tool_calls: int
    system_instructions: str = field(repr=False)
    available_tools: tuple[ToolReference, ...]
    allows_implicit_retry: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        require_current_schema_version(self.schema_version)
        for value, field_name in (
            (self.profile_version, "Tool L2 profile version"),
            (self.prompt_version, "Tool L2 prompt version"),
            (self.context_compiler_version, "Tool L2 Context Compiler version"),
            (self.output_contract_version, "Tool L2 output contract version"),
            (self.toolset_version, "Tool L2 toolset version"),
        ):
            if not _VERSION_PATTERN.fullmatch(value):
                raise ValueError(f"{field_name} is invalid")
        if self.context_compiler_version != CONTEXT_COMPILER_V1:
            raise ValueError("Tool L2 requires Context Compiler v1")
        if self.output_contract_version != FINAL_MARKDOWN_CONTRACT_VERSION:
            raise ValueError("Tool L2 final output contract is unsupported")
        if not _MODEL_PATTERN.fullmatch(self.model):
            raise ValueError("Tool L2 model is invalid")
        for token_limit, field_name in (
            (self.max_input_tokens, "Tool L2 max input tokens"),
            (self.max_decision_output_tokens, "Tool L2 decision output tokens"),
        ):
            if isinstance(token_limit, bool) or not 1 <= token_limit <= MAX_RUN_TOKENS:
                raise ValueError(f"{field_name} is invalid")
        instructions = self.system_instructions.strip()
        if not instructions or len(instructions) > 20_000:
            raise ValueError("Tool L2 system instructions are invalid")
        references = tool_references(self.available_tools)
        if not 1 <= len(references) <= 8:
            raise ValueError("The L2 profile must expose between one and eight Tool versions")
        if (
            isinstance(self.max_tool_calls, bool)
            or not 2 <= self.max_tool_calls <= MAX_CONTEXT_TOOL_OBSERVATIONS
        ):
            raise ValueError(
                f"Tool L2 max calls must be between 2 and {MAX_CONTEXT_TOOL_OBSERVATIONS}"
            )
        object.__setattr__(self, "system_instructions", instructions)
        object.__setattr__(self, "available_tools", references)

    @property
    def model_call_limit(self) -> int:
        return self.max_tool_calls + 1

    @property
    def tool_call_limit(self) -> int:
        return self.max_tool_calls


def _reject_duplicate_decision_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("Tool loop decision contains duplicate object keys")
        document[key] = value
    return document


def _reject_non_finite_decision(value: str) -> object:
    raise ValueError(f"Tool loop decision contains a non-finite number: {value}")


@dataclass(frozen=True, slots=True)
class ToolLoopFinalDecision:
    """One structured L2 request to stop the loop with Markdown."""

    schema_version: int
    content_markdown: str = field(repr=False)

    def __post_init__(self) -> None:
        require_current_schema_version(self.schema_version)
        normalized = self.content_markdown.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized or len(normalized) > MAX_FINAL_MARKDOWN_LENGTH:
            raise ValueError("Tool loop final Markdown is invalid")
        object.__setattr__(self, "content_markdown", normalized)


def tool_loop_decision_response_schema(
    definitions: ToolDefinition | Sequence[ToolDefinition],
) -> Mapping[str, object]:
    """Return a strict decision schema for an exact trusted Tool surface or final answer."""

    selected = (definitions,) if isinstance(definitions, ToolDefinition) else tuple(definitions)
    if not selected or len(selected) != len({item.reference for item in selected}):
        raise ValueError("Tool loop decision schema requires unique Tool definitions")
    action_schemas = [dict(tool_action_response_schema(definition)) for definition in selected]
    final_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "kind", "content_markdown"],
        "properties": {
            "schema_version": {"type": "integer", "const": TOOL_ACTION_SCHEMA_VERSION},
            "kind": {"type": "string", "const": "final"},
            "content_markdown": {"type": "string"},
        },
    }
    return snapshot_json_mapping(
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["decision"],
            "properties": {"decision": {"anyOf": [*action_schemas, final_schema]}},
        },
        error_message="Tool loop decision schema must be canonical JSON data",
    )


def decode_tool_loop_decision(serialized: str) -> ToolAction | ToolLoopFinalDecision:
    """Decode one strict L2 decision without accepting prose or ambiguous fields."""

    try:
        encoded = serialized.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Tool loop decision is not valid UTF-8") from None
    if len(encoded) > MAX_TOOL_LOOP_DECISION_BYTES:
        raise ValueError("Tool loop decision exceeds the size limit")
    try:
        loaded = json.loads(
            serialized,
            object_pairs_hook=_reject_duplicate_decision_keys,
            parse_constant=_reject_non_finite_decision,
        )
    except (json.JSONDecodeError, RecursionError):
        raise ValueError("Tool loop decision is not valid JSON") from None
    if not isinstance(loaded, dict) or set(loaded) != {"decision"}:
        raise ValueError("Tool loop decision envelope is invalid")
    decision = loaded["decision"]
    if not isinstance(decision, dict):
        raise ValueError("Tool loop decision body is invalid")
    kind = decision.get("kind")
    if kind == "tool_call":
        try:
            action_json = json.dumps(
                decision,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError):
            raise ValueError("Tool loop Action is invalid") from None
        return ToolAction.from_json(action_json)
    if kind != "final" or set(decision) != {
        "schema_version",
        "kind",
        "content_markdown",
    }:
        raise ValueError("Tool loop decision kind is invalid")
    schema_version = decision["schema_version"]
    content_markdown = decision["content_markdown"]
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or not isinstance(content_markdown, str)
    ):
        raise ValueError("Tool loop final decision field types are invalid")
    return ToolLoopFinalDecision(
        schema_version=schema_version,
        content_markdown=content_markdown,
    )


@dataclass(frozen=True, slots=True)
class ToolL1RunCommand:
    """Stable IDs and user data needed for one fresh L1 Action→Observation run."""

    run: AgentRun
    state: RunState
    policy: ToolL1RuntimePolicy
    action_model_step_id: UUID
    tool_step_id: UUID
    answer_model_step_id: UUID
    final_step_id: UUID
    action_manifest_id: UUID
    answer_manifest_id: UUID
    tool_call_id: UUID
    approval_request_id: UUID
    user_question: str = field(repr=False)
    conversation_summary: str | None = field(default=None, repr=False)
    conversation_summary_version: str | None = None
    attachments: tuple[AttachmentContextSource, ...] = field(default=(), repr=False)
    memory_context: MemoryContextBundle = field(default_factory=MemoryContextBundle, repr=False)
    side_effect_idempotency_key: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        validate_run_state(self.run, self.state)
        if (
            self.run.run_type is not AgentRunType.TOOL_LOOP
            or self.run.status is not AgentRunStatus.QUEUED
            or self.state.status is not AgentRunStatus.QUEUED
            or self.state.revision != 0
            or self.state.step_count != 0
            or self.state.event_count != 1
        ):
            raise ValueError("Tool L1 Runtime requires a fresh queued Tool Run")
        if self.run.runtime_version != TOOL_L1_RUNTIME_VERSION:
            raise ValueError("Run runtime version does not match Tool L1 Runtime")
        if self.run.budget.max_steps < 4:
            raise ValueError("Tool L1 requires two Model, one Tool, and one Final Step")
        identifiers = (
            self.action_model_step_id,
            self.tool_step_id,
            self.answer_model_step_id,
            self.final_step_id,
            self.action_manifest_id,
            self.answer_manifest_id,
            self.tool_call_id,
            self.approval_request_id,
        )
        for identifier in identifiers:
            require_non_nil_uuid(identifier, field_name="Tool L1 execution ID")
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Tool L1 execution IDs must be distinct")
        question = self.user_question.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not question or len(question) > MAX_CONTEXT_QUESTION_LENGTH:
            raise ValueError("Tool L1 user question is invalid")
        object.__setattr__(self, "user_question", question)
        if self.conversation_summary is None:
            if self.conversation_summary_version is not None:
                raise ValueError("A missing conversation summary cannot have a version")
        else:
            summary = self.conversation_summary.replace("\r\n", "\n").replace("\r", "\n").strip()
            if (
                not summary
                or len(summary) > MAX_CONTEXT_SUMMARY_LENGTH
                or self.conversation_summary_version is None
                or not _VERSION_PATTERN.fullmatch(self.conversation_summary_version)
            ):
                raise ValueError("Tool L1 conversation summary is invalid")
            object.__setattr__(self, "conversation_summary", summary)
        object.__setattr__(
            self,
            "attachments",
            validate_attachment_context_sources(
                self.attachments,
                workspace_id=self.run.workspace_id,
            ),
        )
        if self.side_effect_idempotency_key is not None:
            try:
                side_effect_idempotency_key_sha256(self.side_effect_idempotency_key)
            except ValueError:
                raise ValueError("Tool L1 side-effect idempotency key is invalid") from None


@dataclass(frozen=True, slots=True)
class ToolL2RunCommand:
    """Stable bounded IDs and user data for one fresh L2 Tool loop."""

    run: AgentRun
    state: RunState
    policy: ToolL2RuntimePolicy
    decision_model_step_ids: tuple[UUID, ...]
    tool_step_ids: tuple[UUID, ...]
    decision_manifest_ids: tuple[UUID, ...]
    tool_call_ids: tuple[UUID, ...]
    approval_request_ids: tuple[UUID, ...]
    final_step_id: UUID
    user_question: str = field(repr=False)
    conversation_summary: str | None = field(default=None, repr=False)
    conversation_summary_version: str | None = None
    attachments: tuple[AttachmentContextSource, ...] = field(default=(), repr=False)
    memory_context: MemoryContextBundle = field(default_factory=MemoryContextBundle, repr=False)
    side_effect_idempotency_keys: tuple[str | None, ...] = field(default=(), repr=False)
    embedded_in_research: bool = False

    def __post_init__(self) -> None:
        validate_run_state(self.run, self.state)
        if (
            self.run.run_type
            not in (
                {AgentRunType.RESEARCH} if self.embedded_in_research else {AgentRunType.TOOL_LOOP}
            )
            or self.run.status is not AgentRunStatus.QUEUED
            or self.state.status is not AgentRunStatus.QUEUED
            or self.state.revision != 0
            or self.state.step_count != 0
            or self.state.event_count != 1
        ):
            raise ValueError("Tool L2 Runtime requires a fresh queued Tool Run")
        if self.run.runtime_version != TOOL_L2_RUNTIME_VERSION:
            raise ValueError("Run runtime version does not match Tool L2 Runtime")
        if self.run.budget.max_steps < 2:
            raise ValueError("Tool L2 requires one decision Model and one Final Step")

        expected_model_calls = self.policy.model_call_limit
        expected_tool_calls = self.policy.tool_call_limit
        collections = (
            (self.decision_model_step_ids, expected_model_calls, "decision Step IDs"),
            (self.decision_manifest_ids, expected_model_calls, "manifest IDs"),
            (self.tool_step_ids, expected_tool_calls, "Tool Step IDs"),
            (self.tool_call_ids, expected_tool_calls, "Tool Call IDs"),
            (self.approval_request_ids, expected_tool_calls, "approval request IDs"),
            (
                self.side_effect_idempotency_keys,
                expected_tool_calls,
                "side-effect idempotency keys",
            ),
        )
        for values, expected_length, field_name in collections:
            if len(values) != expected_length:
                raise ValueError(f"Tool L2 {field_name} length is invalid")

        identifiers = (
            *self.decision_model_step_ids,
            *self.decision_manifest_ids,
            *self.tool_step_ids,
            *self.tool_call_ids,
            *self.approval_request_ids,
            self.final_step_id,
        )
        for identifier in identifiers:
            require_non_nil_uuid(identifier, field_name="Tool L2 execution ID")
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Tool L2 execution IDs must be distinct")

        question = self.user_question.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not question or len(question) > MAX_CONTEXT_QUESTION_LENGTH:
            raise ValueError("Tool L2 user question is invalid")
        object.__setattr__(self, "user_question", question)
        if self.conversation_summary is None:
            if self.conversation_summary_version is not None:
                raise ValueError("A missing conversation summary cannot have a version")
        else:
            summary = self.conversation_summary.replace("\r\n", "\n").replace("\r", "\n").strip()
            if (
                not summary
                or len(summary) > MAX_CONTEXT_SUMMARY_LENGTH
                or self.conversation_summary_version is None
                or not _VERSION_PATTERN.fullmatch(self.conversation_summary_version)
            ):
                raise ValueError("Tool L2 conversation summary is invalid")
            object.__setattr__(self, "conversation_summary", summary)
        object.__setattr__(
            self,
            "attachments",
            validate_attachment_context_sources(
                self.attachments,
                workspace_id=self.run.workspace_id,
            ),
        )
        for key in self.side_effect_idempotency_keys:
            if key is None:
                continue
            try:
                side_effect_idempotency_key_sha256(key)
            except ValueError:
                raise ValueError("Tool L2 side-effect idempotency key is invalid") from None
