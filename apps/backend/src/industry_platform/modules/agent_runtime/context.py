"""Typed inputs and audit records for compiling one model-visible context."""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final, Protocol
from uuid import UUID

from industry_platform.modules.agent_runtime.domain import (
    MAX_RUN_TOKENS,
    AgentRun,
    AgentStep,
    AgentStepKind,
    AgentStepStatus,
    RunBudget,
    RunStopReason,
    require_current_schema_version,
    require_non_nil_uuid,
    require_utc,
    snapshot_json_mapping,
)
from industry_platform.modules.agent_runtime.model import (
    ModelImageMediaType,
    ModelImagePart,
    ModelRequest,
    ModelRole,
)
from industry_platform.modules.agent_runtime.state import RunState, validate_run_state
from industry_platform.modules.identity.domain import AuthenticatedWorkspace
from industry_platform.modules.workspaces.domain import WorkspaceAction, WorkspaceScope
from industry_platform.modules.workspaces.policy import WORKSPACE_ROLE_ACTIONS

CONTEXT_MANIFEST_SCHEMA_VERSION: Final = 1
CONTEXT_COMPILER_V0: Final = "context-v0"
CONTEXT_COMPILER_V1: Final = "context-v1"
RUNTIME_CONTEXT_PROJECTION_V0: Final = "runtime-context-projection-v0"
TOOL_OBSERVATION_CONTEXT_VERSION: Final = "tool-observation-v1"

MAX_CONTEXT_SYSTEM_INSTRUCTIONS_LENGTH: Final = 20_000
MAX_CONTEXT_QUESTION_LENGTH: Final = 20_000
MAX_CONTEXT_SUMMARY_LENGTH: Final = 50_000
MAX_CONTEXT_WORKSPACE_NAME_LENGTH: Final = 256
MAX_CONTEXT_ATTACHMENT_TEXT_LENGTH: Final = 500_000
MAX_CONTEXT_ATTACHMENTS: Final = 4
MAX_CONTEXT_TOOL_OBSERVATION_TEXT_LENGTH: Final = 50_000
# The Tool contract permits up to sixteen 2 KiB Unicode source locators. Keep
# the aggregate envelope hard-bounded while accepting every domain-valid
# Observation; the Context token budget remains the stricter model-call gate.
MAX_CONTEXT_TOOL_OBSERVATION_LOCATOR_BYTES: Final = 256 * 1_024
MAX_CONTEXT_TOOL_OBSERVATIONS: Final = 8
MAX_CONTEXT_RESPONSE_SCHEMA_BYTES: Final = 100_000
MAX_CONTEXT_MANIFEST_SOURCES: Final = 4 + MAX_CONTEXT_ATTACHMENTS + MAX_CONTEXT_TOOL_OBSERVATIONS

_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,127}$")
_SECRET_REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,199}$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_TEXT_ATTACHMENT_MEDIA_TYPES: Final = frozenset({"text/plain", "text/markdown"})


class RuntimePrincipal(Protocol):
    """Small identity shape shared by authenticated requests and background Runs."""

    @property
    def user_id(self) -> UUID: ...

    @property
    def workspaces(self) -> tuple[AuthenticatedWorkspace, ...]: ...


@dataclass(frozen=True, slots=True)
class BackgroundRunPrincipal:
    """Current database-backed identity for a Worker; it is not a browser Session."""

    user_id: UUID
    workspaces: tuple[AuthenticatedWorkspace, ...]

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.user_id, field_name="Background Run user ID")
        workspaces = tuple(self.workspaces)
        if not workspaces or len({workspace.workspace_id for workspace in workspaces}) != len(
            workspaces
        ):
            raise ValueError("Background Run Workspaces are invalid")
        object.__setattr__(self, "workspaces", workspaces)


class ContextSourceKind(StrEnum):
    """The explicit, versioned sources considered for one model request."""

    SYSTEM_INSTRUCTIONS = "system_instructions"
    RUNTIME_CONTEXT_PROJECTION = "runtime_context_projection"
    CONVERSATION_SUMMARY = "conversation_summary"
    ATTACHMENT = "attachment"
    USER_QUESTION = "user_question"
    TOOL_OBSERVATION = "tool_observation"


class ContextDecisionReason(StrEnum):
    """Why one declared input was or was not sent to the model."""

    INCLUDED = "included"
    NOT_AVAILABLE = "not_available"
    EXCLUDED_TOKEN_BUDGET = "excluded_token_budget"  # noqa: S105 - audit decision code


class ContextBudgetExceededError(RuntimeError):
    """Stop before Provider invocation when required input cannot fit safely."""

    stop_reason = RunStopReason.TOKEN_BUDGET_EXCEEDED

    def __init__(self) -> None:
        super().__init__("Context exceeds the available token budget")


def _require_version(value: str, *, field_name: str) -> None:
    if not _VERSION_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} is invalid")


def _require_positive_token_count(value: int, *, field_name: str) -> None:
    if isinstance(value, bool) or not 1 <= value <= MAX_RUN_TOKENS:
        raise ValueError(f"{field_name} is invalid")


def _normalize_text(value: str, *, maximum: int, field_name: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{field_name} is invalid")
    if any(ord(character) < 32 and character not in {"\n", "\t"} for character in normalized):
        raise ValueError(f"{field_name} contains unsupported control characters")
    return normalized


@dataclass(frozen=True, slots=True)
class RuntimeContextProjectionV0:
    """The small, explicit part of trusted server context allowed into model input."""

    workspace_display_name: str = field(repr=False)
    version: str = RUNTIME_CONTEXT_PROJECTION_V0

    def __post_init__(self) -> None:
        workspace_name = _normalize_text(
            self.workspace_display_name,
            maximum=MAX_CONTEXT_WORKSPACE_NAME_LENGTH,
            field_name="Runtime Context workspace display name",
        )
        _require_version(self.version, field_name="Runtime Context projection version")
        object.__setattr__(self, "workspace_display_name", workspace_name)


@dataclass(frozen=True, slots=True)
class TrustedRuntimeContext:
    """Server-verified authorization state that must never be serialized as a prompt."""

    principal: RuntimePrincipal = field(repr=False)
    workspace_scope: WorkspaceScope = field(repr=False)
    capabilities: frozenset[WorkspaceAction] = field(repr=False)
    budget: RunBudget = field(repr=False)
    secret_references: tuple[str, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        if self.principal.user_id != self.workspace_scope.user_id:
            raise ValueError("Runtime Context principal and Workspace scope do not match")
        matching_workspaces = tuple(
            workspace
            for workspace in self.principal.workspaces
            if workspace.workspace_id == self.workspace_scope.workspace_id
        )
        if len(matching_workspaces) != 1:
            raise ValueError("Runtime Context requires one currently authorized Workspace")
        selected_workspace = matching_workspaces[0]
        if selected_workspace.role != self.workspace_scope.role:
            raise ValueError("Runtime Context Workspace role is stale")

        capabilities = frozenset(self.capabilities)
        if not capabilities.issubset(WORKSPACE_ROLE_ACTIONS[self.workspace_scope.role]):
            raise ValueError("Runtime Context capabilities exceed the current Workspace role")
        object.__setattr__(self, "capabilities", capabilities)

        secret_references = tuple(self.secret_references)
        if len(secret_references) != len(set(secret_references)) or any(
            not _SECRET_REFERENCE_PATTERN.fullmatch(reference) for reference in secret_references
        ):
            raise ValueError("Runtime Context Secret references are invalid")
        object.__setattr__(self, "secret_references", secret_references)

        RuntimeContextProjectionV0(selected_workspace.name)

    def project_for_model(self) -> RuntimeContextProjectionV0:
        """Return only the current Workspace display name, never auth or dependencies."""

        selected_workspace = next(
            workspace
            for workspace in self.principal.workspaces
            if workspace.workspace_id == self.workspace_scope.workspace_id
        )
        return RuntimeContextProjectionV0(selected_workspace.name)


@dataclass(frozen=True, slots=True)
class AttachmentContextSource:
    """One verified text or image attachment selected for the current message."""

    file_id: UUID
    workspace_id: UUID
    ordinal: int
    media_type: str
    sha256: str
    parser_version: str
    extracted_text: str | None = field(default=None, repr=False)
    image_part: ModelImagePart | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.file_id, field_name="Attachment Context file ID")
        require_non_nil_uuid(self.workspace_id, field_name="Attachment Context Workspace ID")
        if isinstance(self.ordinal, bool) or not 1 <= self.ordinal <= MAX_CONTEXT_ATTACHMENTS:
            raise ValueError("Attachment Context ordinal is invalid")
        if not _SHA256_PATTERN.fullmatch(self.sha256):
            raise ValueError("Attachment Context digest is invalid")
        _require_version(self.parser_version, field_name="Attachment Context parser version")

        media_type = self.media_type.strip().lower()
        if media_type != self.media_type:
            raise ValueError("Attachment Context media type is not canonical")
        if media_type in _TEXT_ATTACHMENT_MEDIA_TYPES:
            if self.extracted_text is None or self.image_part is not None:
                raise ValueError("Text Attachment Context requires only extracted text")
            text = self.extracted_text
            if (
                not text.strip()
                or len(text) > MAX_CONTEXT_ATTACHMENT_TEXT_LENGTH
                or "\r" in text
                or any(ord(character) < 32 and character not in {"\n", "\t"} for character in text)
            ):
                raise ValueError("Attachment Context extracted text is invalid")
            if hashlib.sha256(text.encode("utf-8")).hexdigest() != self.sha256:
                raise ValueError("Attachment Context text does not match its digest")
            return

        try:
            image_media_type = ModelImageMediaType(media_type)
        except ValueError:
            raise ValueError("Attachment Context media type is unsupported") from None
        if self.extracted_text is not None or self.image_part is None:
            raise ValueError("Image Attachment Context requires only an image part")
        if (
            self.image_part.file_id != self.file_id
            or self.image_part.media_type is not image_media_type
            or self.image_part.sha256 != self.sha256
        ):
            raise ValueError("Attachment Context image does not match its metadata")


def validate_attachment_context_sources(
    attachments: tuple[AttachmentContextSource, ...],
    *,
    workspace_id: UUID,
) -> tuple[AttachmentContextSource, ...]:
    """Freeze and validate the complete ordered attachment selection."""

    selected = tuple(attachments)
    if len(selected) > MAX_CONTEXT_ATTACHMENTS:
        raise ValueError("Attachment Context exceeds the attachment count limit")
    if len({attachment.file_id for attachment in selected}) != len(selected):
        raise ValueError("Attachment Context file IDs must be unique")
    if any(
        attachment.workspace_id != workspace_id or attachment.ordinal != ordinal
        for ordinal, attachment in enumerate(selected, start=1)
    ):
        raise ValueError("Attachment Context order or Workspace is invalid")
    return selected


@dataclass(frozen=True, slots=True)
class ToolObservationContextSource:
    """One normalized, bounded Tool Observation selected as untrusted model data."""

    observation_id: UUID
    tool_call_id: UUID
    workspace_id: UUID
    ordinal: int
    tool_name: str
    tool_version: str
    source_name: str
    source_version: str
    observed_at: datetime
    locator: Mapping[str, object] = field(repr=False)
    content_sha256: str
    model_text: str = field(repr=False)
    observation_version: str = TOOL_OBSERVATION_CONTEXT_VERSION
    envelope_sha256: str = ""

    def __post_init__(self) -> None:
        for identifier, field_name in (
            (self.observation_id, "Tool Observation ID"),
            (self.tool_call_id, "Tool Observation call ID"),
            (self.workspace_id, "Tool Observation Workspace ID"),
        ):
            require_non_nil_uuid(identifier, field_name=field_name)
        if self.observation_id == self.tool_call_id:
            raise ValueError("Tool Observation and call IDs must be distinct")
        if isinstance(self.ordinal, bool) or not 1 <= self.ordinal <= MAX_CONTEXT_TOOL_OBSERVATIONS:
            raise ValueError("Tool Observation ordinal is invalid")
        for value, field_name in (
            (self.tool_name, "Tool Observation tool name"),
            (self.tool_version, "Tool Observation tool version"),
            (self.source_name, "Tool Observation source name"),
            (self.source_version, "Tool Observation source version"),
            (self.observation_version, "Tool Observation version"),
        ):
            _require_version(value, field_name=field_name)
        if self.observation_version != TOOL_OBSERVATION_CONTEXT_VERSION:
            raise ValueError("Tool Observation version is unsupported")
        require_utc(self.observed_at, field_name="Tool Observation time")

        locator = snapshot_json_mapping(
            self.locator,
            error_message="Tool Observation locator must be canonical JSON data",
        )
        if not locator:
            raise ValueError("Tool Observation locator must not be empty")
        encoded_locator = json.dumps(
            dict(locator),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(encoded_locator) > MAX_CONTEXT_TOOL_OBSERVATION_LOCATOR_BYTES:
            raise ValueError("Tool Observation locator exceeds its size limit")
        object.__setattr__(self, "locator", locator)

        model_text = _normalize_text(
            self.model_text,
            maximum=MAX_CONTEXT_TOOL_OBSERVATION_TEXT_LENGTH,
            field_name="Tool Observation model text",
        )
        if (
            not _SHA256_PATTERN.fullmatch(self.content_sha256)
            or hashlib.sha256(model_text.encode("utf-8")).hexdigest() != self.content_sha256
        ):
            raise ValueError("Tool Observation model text does not match its digest")
        object.__setattr__(self, "model_text", model_text)
        computed_envelope_sha256 = hashlib.sha256(
            json.dumps(
                dict(self.to_model_visible_envelope()),
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        if self.envelope_sha256 and self.envelope_sha256 != computed_envelope_sha256:
            raise ValueError("Tool Observation envelope does not match its digest")
        object.__setattr__(self, "envelope_sha256", computed_envelope_sha256)

    def to_model_visible_envelope(self) -> Mapping[str, object]:
        """Return the exact untrusted payload injected by Context Compiler v1."""

        return snapshot_json_mapping(
            {
                "observation_id": str(self.observation_id),
                "tool_call_id": str(self.tool_call_id),
                "tool": {"name": self.tool_name, "version": self.tool_version},
                "source": {"name": self.source_name, "version": self.source_version},
                "observed_at": self.observed_at.isoformat().replace("+00:00", "Z"),
                "locator": dict(self.locator),
                "content_sha256": self.content_sha256,
                "content": self.model_text,
            },
            error_message="Tool Observation model-visible envelope is invalid",
        )


def validate_tool_observation_context_sources(
    observations: tuple[ToolObservationContextSource, ...],
    *,
    workspace_id: UUID,
) -> tuple[ToolObservationContextSource, ...]:
    """Freeze one ordered, Workspace-scoped set of bounded Tool Observations."""

    selected = tuple(observations)
    if len(selected) > MAX_CONTEXT_TOOL_OBSERVATIONS:
        raise ValueError("Tool Observation Context exceeds the observation count limit")
    if len({observation.observation_id for observation in selected}) != len(selected):
        raise ValueError("Tool Observation Context IDs must be unique")
    if any(
        observation.workspace_id != workspace_id or observation.ordinal != ordinal
        for ordinal, observation in enumerate(selected, start=1)
    ):
        raise ValueError("Tool Observation Context order or Workspace is invalid")
    return selected


@dataclass(frozen=True, slots=True)
class ContextCompilationInput:
    """All explicit inputs needed to compile one bounded model request."""

    manifest_id: UUID
    run: AgentRun
    step: AgentStep
    state: RunState
    runtime_context: TrustedRuntimeContext = field(repr=False)
    compiler_version: str
    prompt_version: str
    model: str
    system_instructions: str = field(repr=False)
    user_question: str = field(repr=False)
    max_input_tokens: int
    max_output_tokens: int
    compiled_at: datetime
    conversation_summary: str | None = field(default=None, repr=False)
    conversation_summary_version: str | None = None
    attachments: tuple[AttachmentContextSource, ...] = field(default=(), repr=False)
    tool_observations: tuple[ToolObservationContextSource, ...] = field(
        default=(),
        repr=False,
    )
    response_schema: Mapping[str, object] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.manifest_id, field_name="Context manifest ID")
        validate_run_state(self.run, self.state)
        if (
            self.step.run_id != self.run.run_id
            or self.step.workspace_id != self.run.workspace_id
            or self.step.state_revision != self.state.revision
        ):
            raise ValueError("Context Model Step does not match its Run State")
        if (
            self.step.kind is not AgentStepKind.MODEL
            or self.step.status is not AgentStepStatus.RUNNING
        ):
            raise ValueError("Context can only be compiled for a running Model Step")
        if (
            self.runtime_context.principal.user_id != self.run.user_id
            or self.runtime_context.workspace_scope.workspace_id != self.run.workspace_id
            or self.runtime_context.budget != self.run.budget
        ):
            raise ValueError("Trusted Runtime Context does not match the Agent Run")

        _require_version(self.compiler_version, field_name="Context Compiler version")
        _require_version(self.prompt_version, field_name="Context prompt version")
        _require_positive_token_count(self.max_input_tokens, field_name="Context input limit")
        _require_positive_token_count(self.max_output_tokens, field_name="Context output limit")
        require_utc(self.compiled_at, field_name="Context compilation time")
        if self.compiled_at < self.step.started_at or self.compiled_at >= self.run.budget.deadline:
            raise ValueError("Context compilation time is outside the Run window")

        object.__setattr__(
            self,
            "system_instructions",
            _normalize_text(
                self.system_instructions,
                maximum=MAX_CONTEXT_SYSTEM_INSTRUCTIONS_LENGTH,
                field_name="Context system instructions",
            ),
        )
        object.__setattr__(
            self,
            "user_question",
            _normalize_text(
                self.user_question,
                maximum=MAX_CONTEXT_QUESTION_LENGTH,
                field_name="Context user question",
            ),
        )
        if self.conversation_summary is None:
            if self.conversation_summary_version is not None:
                raise ValueError("A missing conversation summary cannot have a version")
        else:
            if self.conversation_summary_version is None:
                raise ValueError("A conversation summary requires a version")
            _require_version(
                self.conversation_summary_version,
                field_name="Conversation summary version",
            )
            object.__setattr__(
                self,
                "conversation_summary",
                _normalize_text(
                    self.conversation_summary,
                    maximum=MAX_CONTEXT_SUMMARY_LENGTH,
                    field_name="Conversation summary",
                ),
            )
        object.__setattr__(
            self,
            "attachments",
            validate_attachment_context_sources(
                self.attachments,
                workspace_id=self.run.workspace_id,
            ),
        )
        observations = validate_tool_observation_context_sources(
            self.tool_observations,
            workspace_id=self.run.workspace_id,
        )
        if self.compiler_version == CONTEXT_COMPILER_V0 and observations:
            raise ValueError("Context Compiler v0 cannot include Tool Observations")
        if any(
            observation.observed_at < self.run.created_at
            or observation.observed_at > self.compiled_at
            for observation in observations
        ):
            raise ValueError("Tool Observation time is outside the Context compilation window")
        object.__setattr__(self, "tool_observations", observations)
        if self.response_schema is not None:
            response_schema = snapshot_json_mapping(
                self.response_schema,
                error_message="Context response schema must be canonical JSON data",
            )
            encoded_schema = json.dumps(
                dict(response_schema),
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            if not response_schema or len(encoded_schema) > MAX_CONTEXT_RESPONSE_SCHEMA_BYTES:
                raise ValueError("Context response schema exceeds its size limit")
            object.__setattr__(self, "response_schema", response_schema)


@dataclass(frozen=True, slots=True)
class ContextSourceManifestEntry:
    """One source decision without storing the source's original text."""

    ordinal: int
    source_kind: ContextSourceKind
    source_id: str
    source_version: str
    included: bool
    decision_reason: ContextDecisionReason
    estimated_token_count: int
    message_role: ModelRole | None
    source_sha256: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.ordinal, bool) or not 1 <= self.ordinal <= MAX_CONTEXT_MANIFEST_SOURCES:
            raise ValueError("Context source ordinal is invalid")
        _require_version(self.source_id, field_name="Context source ID")
        _require_version(self.source_version, field_name="Context source version")
        if isinstance(self.estimated_token_count, bool) or self.estimated_token_count < 0:
            raise ValueError("Context source token estimate is invalid")
        if self.included:
            if (
                self.decision_reason is not ContextDecisionReason.INCLUDED
                or self.estimated_token_count < 1
                or self.message_role is None
            ):
                raise ValueError("An included Context source requires role and token estimate")
        elif (
            self.decision_reason is ContextDecisionReason.INCLUDED
            or self.estimated_token_count != 0
            or self.message_role is not None
        ):
            raise ValueError("An excluded Context source cannot claim model input")
        if self.source_kind in {
            ContextSourceKind.ATTACHMENT,
            ContextSourceKind.TOOL_OBSERVATION,
        }:
            if not _SHA256_PATTERN.fullmatch(self.source_sha256 or ""):
                raise ValueError("Hashed Context source digest is invalid")
        elif self.source_sha256 is not None:
            raise ValueError("Only hashed Context sources may record a digest")


@dataclass(frozen=True, slots=True)
class ContextBudgetSnapshot:
    """Token limits used before the Provider call was allowed."""

    run_max_total_tokens: int
    tokens_used_before_step: int
    max_input_tokens: int
    estimated_input_tokens: int
    allowed_output_tokens: int
    unreserved_run_tokens: int

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.run_max_total_tokens, "Context Run token limit"),
            (self.max_input_tokens, "Context model input limit"),
            (self.estimated_input_tokens, "Context estimated input tokens"),
            (self.allowed_output_tokens, "Context allowed output tokens"),
        ):
            _require_positive_token_count(value, field_name=field_name)
        for value, field_name in (
            (self.tokens_used_before_step, "Context previously used tokens"),
            (self.unreserved_run_tokens, "Context unreserved Run tokens"),
        ):
            if isinstance(value, bool) or value < 0:
                raise ValueError(f"{field_name} is invalid")
        if (
            self.tokens_used_before_step
            + self.estimated_input_tokens
            + self.allowed_output_tokens
            + self.unreserved_run_tokens
            != self.run_max_total_tokens
        ):
            raise ValueError("Context token reservation does not match the Run budget")
        if self.estimated_input_tokens > self.max_input_tokens:
            raise ValueError("Context input exceeds the model input limit")


@dataclass(frozen=True, slots=True)
class ContextManifest:
    """Auditable list of what was considered and actually sent to the model."""

    schema_version: int
    manifest_id: UUID
    workspace_id: UUID
    run_id: UUID
    step_id: UUID
    compiler_version: str
    prompt_version: str
    runtime_projection_version: str
    token_counter_version: str
    created_at: datetime
    budget: ContextBudgetSnapshot
    sources: tuple[ContextSourceManifestEntry, ...]

    def __post_init__(self) -> None:
        require_current_schema_version(self.schema_version)
        for identifier, field_name in (
            (self.manifest_id, "Context manifest ID"),
            (self.workspace_id, "Context manifest Workspace ID"),
            (self.run_id, "Context manifest Run ID"),
            (self.step_id, "Context manifest Step ID"),
        ):
            require_non_nil_uuid(identifier, field_name=field_name)
        for value, field_name in (
            (self.compiler_version, "Context Compiler version"),
            (self.prompt_version, "Context prompt version"),
            (self.runtime_projection_version, "Runtime Context projection version"),
            (self.token_counter_version, "Context token counter version"),
        ):
            _require_version(value, field_name=field_name)
        require_utc(self.created_at, field_name="Context manifest creation time")

        sources = tuple(self.sources)
        expected_prefix = (
            ContextSourceKind.SYSTEM_INSTRUCTIONS,
            ContextSourceKind.RUNTIME_CONTEXT_PROJECTION,
            ContextSourceKind.CONVERSATION_SUMMARY,
        )
        kinds = tuple(source.source_kind for source in sources)
        attachment_count = kinds.count(ContextSourceKind.ATTACHMENT)
        observation_count = kinds.count(ContextSourceKind.TOOL_OBSERVATION)
        common_order_is_invalid = (
            not 4 <= len(sources) <= MAX_CONTEXT_MANIFEST_SOURCES
            or kinds[:3] != expected_prefix
            or attachment_count > MAX_CONTEXT_ATTACHMENTS
            or observation_count > MAX_CONTEXT_TOOL_OBSERVATIONS
            or any(source.ordinal != ordinal for ordinal, source in enumerate(sources, start=1))
        )
        if self.compiler_version == CONTEXT_COMPILER_V0:
            version_order_is_invalid = kinds[-1] is not ContextSourceKind.USER_QUESTION or any(
                kind is not ContextSourceKind.ATTACHMENT for kind in kinds[3:-1]
            )
        elif self.compiler_version == CONTEXT_COMPILER_V1:
            question_indexes = tuple(
                index for index, kind in enumerate(kinds) if kind is ContextSourceKind.USER_QUESTION
            )
            version_order_is_invalid = len(question_indexes) != 1
            if not version_order_is_invalid:
                question_index = question_indexes[0]
                version_order_is_invalid = (
                    question_index < 3
                    or any(
                        kind is not ContextSourceKind.ATTACHMENT for kind in kinds[3:question_index]
                    )
                    or any(
                        kind is not ContextSourceKind.TOOL_OBSERVATION
                        for kind in kinds[question_index + 1 :]
                    )
                )
        else:
            version_order_is_invalid = True
        if common_order_is_invalid or version_order_is_invalid:
            raise ValueError("Context manifest sources use an invalid compiler-version order")
        attachment_ids = tuple(
            source.source_id
            for source in sources
            if source.source_kind is ContextSourceKind.ATTACHMENT
        )
        if len(attachment_ids) != len(set(attachment_ids)):
            raise ValueError("Context manifest Attachment source IDs must be unique")
        observation_ids = tuple(
            source.source_id
            for source in sources
            if source.source_kind is ContextSourceKind.TOOL_OBSERVATION
        )
        if len(observation_ids) != len(set(observation_ids)):
            raise ValueError("Context manifest Tool Observation source IDs must be unique")
        object.__setattr__(self, "sources", sources)


@dataclass(frozen=True, slots=True)
class CompiledContext:
    """The Provider request paired with the exact manifest that explains it."""

    request: ModelRequest = field(repr=False)
    manifest: ContextManifest

    def __post_init__(self) -> None:
        if (
            self.request.run_id != self.manifest.run_id
            or self.request.step_id != self.manifest.step_id
            or self.request.workspace_id != self.manifest.workspace_id
        ):
            raise ValueError("Compiled model request and Context manifest do not match")
