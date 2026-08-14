"""Typed inputs and audit records for compiling one model-visible context."""

import re
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
)
from industry_platform.modules.agent_runtime.model import ModelRequest, ModelRole
from industry_platform.modules.agent_runtime.state import RunState, validate_run_state
from industry_platform.modules.identity.domain import AuthenticatedWorkspace
from industry_platform.modules.workspaces.domain import WorkspaceAction, WorkspaceScope
from industry_platform.modules.workspaces.policy import WORKSPACE_ROLE_ACTIONS

CONTEXT_MANIFEST_SCHEMA_VERSION: Final = 1
CONTEXT_COMPILER_V0: Final = "context-v0"
RUNTIME_CONTEXT_PROJECTION_V0: Final = "runtime-context-projection-v0"

MAX_CONTEXT_SYSTEM_INSTRUCTIONS_LENGTH: Final = 20_000
MAX_CONTEXT_QUESTION_LENGTH: Final = 20_000
MAX_CONTEXT_SUMMARY_LENGTH: Final = 50_000
MAX_CONTEXT_WORKSPACE_NAME_LENGTH: Final = 256

_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,127}$")
_SECRET_REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,199}$")


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
    """The four inputs understood by the Day 2 compiler."""

    SYSTEM_INSTRUCTIONS = "system_instructions"
    RUNTIME_CONTEXT_PROJECTION = "runtime_context_projection"
    CONVERSATION_SUMMARY = "conversation_summary"
    USER_QUESTION = "user_question"


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
class ContextCompilationInput:
    """All explicit inputs needed to compile one Direct Answer model request."""

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

    def __post_init__(self) -> None:
        if isinstance(self.ordinal, bool) or not 1 <= self.ordinal <= 4:
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
        expected_kinds = tuple(ContextSourceKind)
        if len(sources) != len(expected_kinds) or any(
            entry.ordinal != ordinal or entry.source_kind is not expected_kind
            for ordinal, (entry, expected_kind) in enumerate(
                zip(sources, expected_kinds, strict=True),
                start=1,
            )
        ):
            raise ValueError("Context manifest sources must use the fixed v0 order")
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
