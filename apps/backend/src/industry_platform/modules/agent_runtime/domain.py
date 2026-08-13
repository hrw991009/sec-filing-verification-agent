"""Technology-independent contracts for one versioned Agent run."""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final
from uuid import UUID

from industry_platform.modules.identity.domain import TraceId

AGENT_RUNTIME_SCHEMA_VERSION: Final = 1
MAX_RUN_STEPS: Final = 1_000
MAX_RUN_TOKENS: Final = 10_000_000
MAX_RUN_COST_MICRO_USD: Final = 1_000_000_000

_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,127}$")
_ERROR_CODE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class AgentRunType(StrEnum):
    """Execution shape sharing the same public Runtime semantics."""

    DIRECT_ANSWER = "direct_answer"
    TOOL_LOOP = "tool_loop"
    RESEARCH = "research"


class AgentRunStatus(StrEnum):
    """Persisted lifecycle of one logical Agent run."""

    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentStepKind(StrEnum):
    """Auditable kinds of work inside the unified Runtime."""

    MODEL = "model"
    TOOL = "tool"
    APPROVAL = "approval"
    CHECKPOINT = "checkpoint"
    FINAL = "final"


class AgentStepStatus(StrEnum):
    """Lifecycle of one persisted Agent step."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunStopReason(StrEnum):
    """Stable reason explaining why a run will no longer advance."""

    FINAL = "final"
    CANCELLED = "cancelled"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_RATE_LIMITED = "provider_rate_limited"
    PROVIDER_ERROR = "provider_error"
    INVALID_PROVIDER_RESPONSE = "invalid_provider_response"
    INCOMPLETE_PROVIDER_RESPONSE = "incomplete_provider_response"
    MAX_STEPS = "max_steps"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    TOKEN_BUDGET_EXCEEDED = "token_budget_exceeded"  # noqa: S105 - public reason
    COST_BUDGET_EXCEEDED = "cost_budget_exceeded"
    TOOL_DENIED = "tool_denied"
    TOOL_ERROR = "tool_error"
    NO_PROGRESS = "no_progress"
    APPROVAL_REQUIRED = "approval_required"


class RunArtifactKind(StrEnum):
    """Deliverables distinct from an ordinary final chat message."""

    REPORT = "report"
    TABLE = "table"
    CHART = "chart"
    FILE = "file"
    EVIDENCE_SET = "evidence_set"


TERMINAL_RUN_STATUSES: Final = frozenset(
    {
        AgentRunStatus.COMPLETED,
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
    }
)


def require_current_schema_version(value: int) -> None:
    """Reject contracts that this Runtime cannot interpret safely."""

    if isinstance(value, bool) or value != AGENT_RUNTIME_SCHEMA_VERSION:
        raise ValueError(f"Agent contract schema version must be {AGENT_RUNTIME_SCHEMA_VERSION}")


def require_non_nil_uuid(value: UUID, *, field_name: str) -> None:
    """Reject sentinel UUIDs at domain boundaries."""

    if value.int == 0:
        raise ValueError(f"{field_name} must not use a nil UUID")


def require_utc(value: datetime, *, field_name: str) -> None:
    """Require normalized, timezone-aware UTC timestamps."""

    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field_name} must use timezone-aware UTC")


def snapshot_json_mapping(
    value: Mapping[str, object],
    *,
    error_message: str,
) -> Mapping[str, object]:
    """Deep-copy JSON data into a canonical immutable top-level mapping."""

    try:
        encoded = json.dumps(
            dict(value),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        decoded = json.loads(encoded)
    except (TypeError, ValueError):
        raise ValueError(error_message) from None
    if not isinstance(decoded, dict):
        raise ValueError(error_message)
    return MappingProxyType(decoded)


def validate_stop_reason(
    status: AgentRunStatus,
    stop_reason: RunStopReason | None,
) -> None:
    """Keep terminal status and its public explanation inseparable."""

    is_terminal = status in TERMINAL_RUN_STATUSES
    if is_terminal != (stop_reason is not None):
        raise ValueError("Exactly terminal runs require a stop reason")
    if status is AgentRunStatus.COMPLETED and stop_reason is not RunStopReason.FINAL:
        raise ValueError("Completed runs must stop with the final reason")
    if status is AgentRunStatus.CANCELLED and stop_reason is not RunStopReason.CANCELLED:
        raise ValueError("Cancelled runs must stop with the cancelled reason")
    if status is AgentRunStatus.FAILED and stop_reason in {
        RunStopReason.FINAL,
        RunStopReason.CANCELLED,
    }:
        raise ValueError("Failed runs require a failure stop reason")


def _require_positive_bounded_integer(
    value: int,
    *,
    maximum: int,
    field_name: str,
) -> None:
    if isinstance(value, bool) or not 1 <= value <= maximum:
        raise ValueError(f"{field_name} must be between 1 and {maximum}")


def _require_non_negative_integer(value: int, *, field_name: str) -> None:
    if isinstance(value, bool) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")


def _require_version(value: str, *, field_name: str) -> None:
    if not _VERSION_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} is invalid")


def _require_optional_uuid(value: UUID | None, *, field_name: str) -> None:
    if value is not None:
        require_non_nil_uuid(value, field_name=field_name)


def _snapshot_uuid_references(
    values: Sequence[UUID],
    *,
    field_name: str,
) -> tuple[UUID, ...]:
    snapshot = tuple(values)
    if len(snapshot) != len(set(snapshot)):
        raise ValueError(f"{field_name} must not contain duplicate references")
    for value in snapshot:
        require_non_nil_uuid(value, field_name=field_name)
    return snapshot


@dataclass(frozen=True, slots=True)
class RunBudget:
    """Trusted hard ceilings that the model cannot enlarge."""

    schema_version: int
    max_steps: int
    max_total_tokens: int
    max_cost_micro_usd: int
    deadline: datetime

    def __post_init__(self) -> None:
        require_current_schema_version(self.schema_version)
        _require_positive_bounded_integer(
            self.max_steps,
            maximum=MAX_RUN_STEPS,
            field_name="Run max steps",
        )
        _require_positive_bounded_integer(
            self.max_total_tokens,
            maximum=MAX_RUN_TOKENS,
            field_name="Run max total tokens",
        )
        _require_positive_bounded_integer(
            self.max_cost_micro_usd,
            maximum=MAX_RUN_COST_MICRO_USD,
            field_name="Run max cost",
        )
        require_utc(self.deadline, field_name="Run deadline")


@dataclass(frozen=True, slots=True)
class AgentRun:
    """Immutable snapshot of one run at a known optimistic revision."""

    schema_version: int
    run_id: UUID
    event_stream_id: UUID
    workspace_id: UUID
    user_id: UUID
    run_type: AgentRunType
    runtime_version: str
    harness_version: str
    budget: RunBudget
    trace_id: TraceId
    status: AgentRunStatus
    state_revision: int
    created_at: datetime
    started_at: datetime | None
    terminal_at: datetime | None
    stop_reason: RunStopReason | None
    thread_id: UUID | None = None
    turn_id: UUID | None = None
    job_id: UUID | None = None

    def __post_init__(self) -> None:
        require_current_schema_version(self.schema_version)
        for required_identifier, field_name in (
            (self.run_id, "Run ID"),
            (self.event_stream_id, "Event stream ID"),
            (self.workspace_id, "Run workspace ID"),
            (self.user_id, "Run user ID"),
        ):
            require_non_nil_uuid(required_identifier, field_name=field_name)
        for optional_identifier, field_name in (
            (self.thread_id, "Run thread ID"),
            (self.turn_id, "Run turn ID"),
            (self.job_id, "Run job ID"),
        ):
            _require_optional_uuid(optional_identifier, field_name=field_name)
        if self.turn_id is not None and self.thread_id is None:
            raise ValueError("A run turn reference requires its thread reference")
        _require_version(self.runtime_version, field_name="Runtime version")
        _require_version(self.harness_version, field_name="Harness version")
        if not str(self.trace_id).strip() or len(str(self.trace_id)) > 128:
            raise ValueError("Run trace ID is invalid")
        _require_non_negative_integer(self.state_revision, field_name="Run state revision")
        require_utc(self.created_at, field_name="Run creation time")
        if self.started_at is not None:
            require_utc(self.started_at, field_name="Run start time")
            if self.started_at < self.created_at:
                raise ValueError("Run start time must not precede creation")
        if self.terminal_at is not None:
            require_utc(self.terminal_at, field_name="Run terminal time")
            lower_bound = self.started_at or self.created_at
            if self.terminal_at < lower_bound:
                raise ValueError("Run terminal time is out of order")
        if self.budget.deadline <= self.created_at:
            raise ValueError("Run deadline must be after creation")

        is_terminal = self.status in TERMINAL_RUN_STATUSES
        if is_terminal != (self.terminal_at is not None):
            raise ValueError("Exactly terminal runs require a terminal time")
        if self.status in {AgentRunStatus.RUNNING, AgentRunStatus.PAUSED} and (
            self.started_at is None
        ):
            raise ValueError("An active run requires a start time")
        if self.status is AgentRunStatus.QUEUED and self.started_at is not None:
            raise ValueError("A queued run cannot already have a start time")
        if self.status is AgentRunStatus.COMPLETED and self.started_at is None:
            raise ValueError("A completed run requires a start time")
        validate_stop_reason(self.status, self.stop_reason)


@dataclass(frozen=True, slots=True)
class AgentStep:
    """Sanitized, auditable work performed at one Run sequence number."""

    schema_version: int
    step_id: UUID
    run_id: UUID
    workspace_id: UUID
    sequence: int
    kind: AgentStepKind
    status: AgentStepStatus
    state_revision: int
    started_at: datetime
    completed_at: datetime | None = None
    input_summary: Mapping[str, object] = field(default_factory=dict, repr=False)
    output_summary: Mapping[str, object] = field(default_factory=dict, repr=False)
    input_artifact_ids: tuple[UUID, ...] = ()
    output_artifact_ids: tuple[UUID, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    cost_micro_usd: int = 0
    latency_ms: int | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        require_current_schema_version(self.schema_version)
        for step_identifier, field_name in (
            (self.step_id, "Step ID"),
            (self.run_id, "Step run ID"),
            (self.workspace_id, "Step workspace ID"),
        ):
            require_non_nil_uuid(step_identifier, field_name=field_name)
        _require_positive_bounded_integer(
            self.sequence,
            maximum=MAX_RUN_STEPS,
            field_name="Step sequence",
        )
        _require_positive_bounded_integer(
            self.state_revision,
            maximum=2_147_483_647,
            field_name="Step state revision",
        )
        require_utc(self.started_at, field_name="Step start time")
        if self.completed_at is not None:
            require_utc(self.completed_at, field_name="Step completion time")
            if self.completed_at < self.started_at:
                raise ValueError("Step completion time is out of order")

        is_running = self.status is AgentStepStatus.RUNNING
        if is_running == (self.completed_at is not None):
            raise ValueError("Exactly settled steps require a completion time")
        if is_running and self.error_code is not None:
            raise ValueError("A running step cannot have an error code")
        if self.status is AgentStepStatus.FAILED:
            if self.error_code is None or not _ERROR_CODE_PATTERN.fullmatch(self.error_code):
                raise ValueError("A failed step requires a stable error code")
        elif self.error_code is not None:
            raise ValueError("Only failed steps may have an error code")

        for usage_value, field_name in (
            (self.input_tokens, "Step input tokens"),
            (self.output_tokens, "Step output tokens"),
            (self.cost_micro_usd, "Step cost"),
        ):
            _require_non_negative_integer(usage_value, field_name=field_name)
        if self.latency_ms is not None:
            _require_non_negative_integer(self.latency_ms, field_name="Step latency")
        if is_running != (self.latency_ms is None):
            raise ValueError("Exactly settled steps require a latency")

        object.__setattr__(
            self,
            "input_summary",
            snapshot_json_mapping(
                self.input_summary,
                error_message="Step input summary must be canonical JSON data",
            ),
        )
        object.__setattr__(
            self,
            "output_summary",
            snapshot_json_mapping(
                self.output_summary,
                error_message="Step output summary must be canonical JSON data",
            ),
        )
        object.__setattr__(
            self,
            "input_artifact_ids",
            _snapshot_uuid_references(
                self.input_artifact_ids,
                field_name="Step input artifact IDs",
            ),
        )
        object.__setattr__(
            self,
            "output_artifact_ids",
            _snapshot_uuid_references(
                self.output_artifact_ids,
                field_name="Step output artifact IDs",
            ),
        )


@dataclass(frozen=True, slots=True)
class RunArtifact:
    """Versioned deliverable referenced by a Run rather than embedded in State."""

    schema_version: int
    artifact_id: UUID
    run_id: UUID
    workspace_id: UUID
    kind: RunArtifactKind
    resource_ref: str = field(repr=False)
    content_sha256: str
    version: int
    created_at: datetime
    originating_step_id: UUID | None = None

    def __post_init__(self) -> None:
        require_current_schema_version(self.schema_version)
        for value, name in (
            (self.artifact_id, "Artifact ID"),
            (self.run_id, "Artifact run ID"),
            (self.workspace_id, "Artifact workspace ID"),
        ):
            require_non_nil_uuid(value, field_name=name)
        _require_optional_uuid(
            self.originating_step_id,
            field_name="Artifact originating step ID",
        )
        if (
            not self.resource_ref.strip()
            or self.resource_ref != self.resource_ref.strip()
            or len(self.resource_ref) > 1_024
            or any(character in self.resource_ref for character in "\r\n")
        ):
            raise ValueError("Artifact resource reference is invalid")
        if not _SHA256_PATTERN.fullmatch(self.content_sha256):
            raise ValueError("Artifact content hash must be lowercase SHA-256")
        _require_positive_bounded_integer(
            self.version,
            maximum=2_147_483_647,
            field_name="Artifact version",
        )
        require_utc(self.created_at, field_name="Artifact creation time")


def validate_step_sequence(steps: Sequence[AgentStep], run: AgentRun) -> None:
    """Validate one complete ordered Step projection for a Run."""

    completed_final_seen = False
    cancelled_seen = False
    previous_revision = 0
    for expected_sequence, step in enumerate(steps, start=1):
        if step.run_id != run.run_id or step.workspace_id != run.workspace_id:
            raise ValueError("Step belongs to another run or workspace")
        if step.sequence != expected_sequence:
            raise ValueError("Step sequence must be contiguous and start at one")
        if step.state_revision <= previous_revision:
            raise ValueError("Step state revisions must increase monotonically")
        if completed_final_seen or cancelled_seen:
            raise ValueError("No step may follow a terminal step")
        if step.status is AgentStepStatus.RUNNING and expected_sequence != len(steps):
            raise ValueError("Only the final projected step may still be running")
        if step.kind is AgentStepKind.FINAL and step.status is AgentStepStatus.COMPLETED:
            completed_final_seen = True
        if step.status is AgentStepStatus.CANCELLED:
            cancelled_seen = True
        previous_revision = step.state_revision

    if len(steps) > run.budget.max_steps:
        raise ValueError("Step sequence exceeds the trusted Run budget")
    if steps and run.state_revision < steps[-1].state_revision:
        raise ValueError("Run revision cannot precede its latest Step revision")
    if run.status is AgentRunStatus.COMPLETED and not completed_final_seen:
        raise ValueError("A completed run requires one completed final step")
    if run.status not in TERMINAL_RUN_STATUSES and (completed_final_seen or cancelled_seen):
        raise ValueError("A non-terminal run cannot contain a terminal step")


def validate_artifact_references(
    artifacts: Sequence[RunArtifact],
    steps: Sequence[AgentStep],
    run: AgentRun,
) -> None:
    """Reject cross-run, dangling, or contradictory Artifact references."""

    artifact_by_id: dict[UUID, RunArtifact] = {}
    for artifact in artifacts:
        if artifact.run_id != run.run_id or artifact.workspace_id != run.workspace_id:
            raise ValueError("Artifact belongs to another run or workspace")
        if artifact.artifact_id in artifact_by_id:
            raise ValueError("Artifact IDs must be unique within a Run")
        artifact_by_id[artifact.artifact_id] = artifact

    step_by_id = {step.step_id: step for step in steps}
    if len(step_by_id) != len(steps):
        raise ValueError("Step IDs must be unique within a Run")
    for step in steps:
        if step.run_id != run.run_id or step.workspace_id != run.workspace_id:
            raise ValueError("Step belongs to another run or workspace")
        for artifact_id in (*step.input_artifact_ids, *step.output_artifact_ids):
            if artifact_id not in artifact_by_id:
                raise ValueError("Step contains a dangling Artifact reference")
        for artifact_id in step.output_artifact_ids:
            if artifact_by_id[artifact_id].originating_step_id != step.step_id:
                raise ValueError("Output Artifact must identify its originating Step")

    for artifact in artifacts:
        if artifact.originating_step_id is None:
            continue
        origin = step_by_id.get(artifact.originating_step_id)
        if origin is None:
            raise ValueError("Artifact contains a dangling originating Step reference")
        if artifact.artifact_id not in origin.output_artifact_ids:
            raise ValueError("Originating Step must reference its output Artifact")
