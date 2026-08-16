"""Safe read models for the Day 2 Agent Learning Workbench."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Final
from uuid import UUID

from industry_platform.modules.agent_runtime.context import ContextManifest, ContextSourceKind
from industry_platform.modules.agent_runtime.domain import (
    AgentRunStatus,
    AgentRunType,
    AgentStepKind,
    AgentStepStatus,
    RunStopReason,
    require_current_schema_version,
    require_non_nil_uuid,
    require_utc,
    validate_stop_reason,
)
from industry_platform.modules.agent_runtime.events import AgentEventType
from industry_platform.modules.identity.domain import TraceId

TRACE_VIEW_SCHEMA_VERSION: Final = 1


def _require_non_negative(value: int, *, field_name: str) -> None:
    if isinstance(value, bool) or value < 0:
        raise ValueError(f"{field_name} is invalid")


@dataclass(frozen=True, slots=True)
class TraceUsage:
    """Token and integer micro-USD totals shown without Provider response bodies."""

    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cost_micro_usd: int

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.input_tokens, "Trace input tokens"),
            (self.output_tokens, "Trace output tokens"),
            (self.cached_input_tokens, "Trace cached input tokens"),
            (self.cost_micro_usd, "Trace cost"),
        ):
            _require_non_negative(value, field_name=field_name)
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("Trace cached input tokens cannot exceed input tokens")

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True, slots=True)
class TraceRun:
    """Sanitized Run projection; it deliberately omits the user's question and answer."""

    schema_version: int
    run_id: UUID
    workspace_id: UUID
    conversation_id: UUID
    turn_id: UUID
    event_stream_id: UUID
    trace_id: TraceId
    run_type: AgentRunType
    status: AgentRunStatus
    stop_reason: RunStopReason | None
    runtime_version: str
    harness_version: str
    state_revision: int
    max_steps: int
    max_total_tokens: int
    max_cost_micro_usd: int
    deadline: datetime
    event_count: int
    step_count: int
    usage: TraceUsage
    created_at: datetime
    started_at: datetime | None
    terminal_at: datetime | None

    def __post_init__(self) -> None:
        require_current_schema_version(self.schema_version)
        for identifier, field_name in (
            (self.run_id, "Trace Run ID"),
            (self.workspace_id, "Trace Workspace ID"),
            (self.conversation_id, "Trace Conversation ID"),
            (self.turn_id, "Trace Turn ID"),
            (self.event_stream_id, "Trace Event stream ID"),
        ):
            require_non_nil_uuid(identifier, field_name=field_name)
        if not str(self.trace_id).strip() or len(str(self.trace_id)) > 128:
            raise ValueError("Trace ID is invalid")
        for count, field_name in (
            (self.state_revision, "Trace state revision"),
            (self.event_count, "Trace Event count"),
            (self.step_count, "Trace Step count"),
        ):
            _require_non_negative(count, field_name=field_name)
        for limit, field_name in (
            (self.max_steps, "Trace Step limit"),
            (self.max_total_tokens, "Trace token limit"),
            (self.max_cost_micro_usd, "Trace cost limit"),
        ):
            if isinstance(limit, bool) or limit < 1:
                raise ValueError(f"{field_name} is invalid")
        for timestamp, field_name in (
            (self.created_at, "Trace creation time"),
            (self.deadline, "Trace deadline"),
        ):
            require_utc(timestamp, field_name=field_name)
        for optional_timestamp, field_name in (
            (self.started_at, "Trace start time"),
            (self.terminal_at, "Trace terminal time"),
        ):
            if optional_timestamp is not None:
                require_utc(optional_timestamp, field_name=field_name)
        if self.deadline <= self.created_at:
            raise ValueError("Trace deadline must be after creation")
        validate_stop_reason(self.status, self.stop_reason)


@dataclass(frozen=True, slots=True)
class TraceStep:
    """One Step row suitable for a Workbench timeline."""

    step_id: UUID
    sequence: int
    kind: AgentStepKind
    status: AgentStepStatus
    last_event_sequence: int
    started_at: datetime
    completed_at: datetime | None
    usage: TraceUsage
    error_code: str | None

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.step_id, field_name="Trace Step ID")
        if isinstance(self.sequence, bool) or self.sequence < 1:
            raise ValueError("Trace Step sequence is invalid")
        if isinstance(self.last_event_sequence, bool) or self.last_event_sequence < 1:
            raise ValueError("Trace Step Event sequence is invalid")
        require_utc(self.started_at, field_name="Trace Step start time")
        if self.completed_at is not None:
            require_utc(self.completed_at, field_name="Trace Step completion time")
        if self.status is AgentStepStatus.RUNNING:
            if self.completed_at is not None or self.error_code is not None:
                raise ValueError("A running Trace Step cannot be settled")
        elif self.completed_at is None:
            raise ValueError("A settled Trace Step requires a completion time")
        if self.status is AgentStepStatus.FAILED:
            if self.error_code is None:
                raise ValueError("A failed Trace Step requires an error code")
        elif self.error_code is not None:
            raise ValueError("Only a failed Trace Step may expose an error code")


@dataclass(frozen=True, slots=True)
class TraceEvent:
    """One Event with only explicitly approved metadata from its payload."""

    schema_version: int
    sequence: int
    occurred_at: datetime
    event_type: AgentEventType
    details: Mapping[str, str | int] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        require_current_schema_version(self.schema_version)
        if isinstance(self.sequence, bool) or self.sequence < 1:
            raise ValueError("Trace Event sequence is invalid")
        require_utc(self.occurred_at, field_name="Trace Event occurrence time")
        details = dict(self.details)
        if any(
            not isinstance(key, str) or isinstance(value, bool) or not isinstance(value, (str, int))
            for key, value in details.items()
        ):
            raise ValueError("Trace Event details must contain safe scalar metadata")
        object.__setattr__(self, "details", MappingProxyType(details))


@dataclass(frozen=True, slots=True)
class AgentTrace:
    """One complete PostgreSQL-backed Workbench view of a Run."""

    schema_version: int
    run: TraceRun
    steps: tuple[TraceStep, ...]
    context_manifests: tuple[ContextManifest, ...]
    events: tuple[TraceEvent, ...]

    def __post_init__(self) -> None:
        require_current_schema_version(self.schema_version)
        steps = tuple(self.steps)
        manifests = tuple(self.context_manifests)
        events = tuple(self.events)
        if len(steps) != self.run.step_count or any(
            step.sequence != expected for expected, step in enumerate(steps, start=1)
        ):
            raise ValueError("Trace Steps do not match the Run projection")
        if len(events) != self.run.event_count or any(
            event.sequence != expected for expected, event in enumerate(events, start=1)
        ):
            raise ValueError("Trace Events do not match the Run projection")
        step_ids = {step.step_id for step in steps}
        if any(
            manifest.run_id != self.run.run_id
            or manifest.workspace_id != self.run.workspace_id
            or manifest.step_id not in step_ids
            for manifest in manifests
        ):
            raise ValueError("Trace Context manifests do not belong to its Run Steps")
        completed_observations: dict[str, str] = {}
        for event in events:
            if event.event_type is not AgentEventType.TOOL_COMPLETED:
                continue
            observation_id = event.details.get("observation_id")
            envelope_sha256 = event.details.get("observation_envelope_sha256")
            if (
                not isinstance(observation_id, str)
                or not isinstance(envelope_sha256, str)
                or len(envelope_sha256) != 64
                or any(character not in "0123456789abcdef" for character in envelope_sha256)
                or observation_id in completed_observations
            ):
                raise ValueError("Trace Tool completion metadata is invalid")
            completed_observations[observation_id] = envelope_sha256
        for manifest in manifests:
            for source in manifest.sources:
                if source.source_kind is not ContextSourceKind.TOOL_OBSERVATION:
                    continue
                if completed_observations.get(source.source_id) != source.source_sha256:
                    raise ValueError(
                        "Trace Tool Observation source does not match a completed Tool Event"
                    )
        object.__setattr__(self, "steps", steps)
        object.__setattr__(self, "context_manifests", manifests)
        object.__setattr__(self, "events", events)
