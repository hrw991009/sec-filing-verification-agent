"""Stable policy and command for the Day 3 L1 single-Tool Runtime slice."""

import re
from dataclasses import dataclass, field
from typing import Final
from uuid import UUID

from industry_platform.modules.agent_runtime.context import (
    CONTEXT_COMPILER_V1,
    MAX_CONTEXT_QUESTION_LENGTH,
    MAX_CONTEXT_SUMMARY_LENGTH,
    AttachmentContextSource,
    validate_attachment_context_sources,
)
from industry_platform.modules.agent_runtime.domain import (
    MAX_RUN_TOKENS,
    AgentRun,
    AgentRunStatus,
    AgentRunType,
    require_current_schema_version,
    require_non_nil_uuid,
)
from industry_platform.modules.agent_runtime.final_output import (
    FINAL_MARKDOWN_CONTRACT_VERSION,
)
from industry_platform.modules.agent_runtime.state import RunState, validate_run_state
from industry_platform.modules.tools.domain import (
    ToolReference,
    side_effect_idempotency_key_sha256,
    tool_references,
)

TOOL_L1_RUNTIME_VERSION: Final = "agent-runtime-v1"

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
