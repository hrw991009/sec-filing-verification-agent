"""Versioned Agent Event envelope and stream-level invariants."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from industry_platform.modules.agent_runtime.domain import (
    TERMINAL_RUN_STATUSES,
    AgentRun,
    AgentRunStatus,
    require_current_schema_version,
    require_non_nil_uuid,
    require_utc,
    snapshot_json_mapping,
)
from industry_platform.modules.identity.domain import TraceId


class AgentEventType(StrEnum):
    """Versioned Event vocabulary shared by persistence, SSE, and Trace."""

    RUN_QUEUED = "agent.run.queued"
    RUN_STARTED = "agent.run.started"
    RUN_PAUSED = "agent.run.paused"
    RUN_RESUMED = "agent.run.resumed"
    RUN_COMPLETED = "agent.run.completed"
    RUN_FAILED = "agent.run.failed"
    RUN_CANCELLED = "agent.run.cancelled"
    STEP_STARTED = "agent.step.started"
    STEP_COMPLETED = "agent.step.completed"
    STEP_FAILED = "agent.step.failed"
    MODEL_STARTED = "agent.model.started"
    MODEL_DELTA = "agent.model.delta"
    MODEL_COMPLETED = "agent.model.completed"
    TOOL_REQUESTED = "agent.tool.requested"
    TOOL_APPROVAL_REQUIRED = "agent.tool.approval_required"
    TOOL_DENIED = "agent.tool.denied"
    TOOL_STARTED = "agent.tool.started"
    TOOL_COMPLETED = "agent.tool.completed"
    TOOL_FAILED = "agent.tool.failed"
    TOOL_CANCELLED = "agent.tool.cancelled"
    ARTIFACT_CREATED = "agent.artifact.created"
    CHECKPOINT_SAVED = "agent.checkpoint.saved"
    RESEARCH_NODE_STARTED = "agent.research.node_started"
    RESEARCH_NODE_COMPLETED = "agent.research.node_completed"
    RESEARCH_NODE_FAILED = "agent.research.node_failed"


TERMINAL_AGENT_EVENT_TYPES: Final = frozenset(
    {
        AgentEventType.RUN_COMPLETED,
        AgentEventType.RUN_FAILED,
        AgentEventType.RUN_CANCELLED,
    }
)

_TERMINAL_EVENT_BY_STATUS: Final = {
    AgentRunStatus.COMPLETED: AgentEventType.RUN_COMPLETED,
    AgentRunStatus.FAILED: AgentEventType.RUN_FAILED,
    AgentRunStatus.CANCELLED: AgentEventType.RUN_CANCELLED,
}


@dataclass(frozen=True, slots=True)
class AgentEvent:
    """One immutable event in the public versioned SSE envelope."""

    schema_version: int
    stream_id: UUID
    run_id: UUID
    workspace_id: UUID
    sequence: int
    occurred_at: datetime
    trace_id: TraceId
    event_type: AgentEventType
    payload: Mapping[str, object] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        require_current_schema_version(self.schema_version)
        for value, name in (
            (self.stream_id, "Event stream ID"),
            (self.run_id, "Event run ID"),
            (self.workspace_id, "Event workspace ID"),
        ):
            require_non_nil_uuid(value, field_name=name)
        if isinstance(self.sequence, bool) or self.sequence < 1:
            raise ValueError("Event sequence must be a positive integer")
        require_utc(self.occurred_at, field_name="Event occurrence time")
        if not str(self.trace_id).strip() or len(str(self.trace_id)) > 128:
            raise ValueError("Event trace ID is invalid")
        object.__setattr__(
            self,
            "payload",
            snapshot_json_mapping(
                self.payload,
                error_message="Event payload must be canonical JSON data",
            ),
        )


def validate_event_stream(events: Sequence[AgentEvent], run: AgentRun) -> None:
    """Validate the complete persisted Event history for one Run."""

    if not events:
        raise ValueError("A persisted Run requires at least its queued Event")

    terminal_events: list[AgentEvent] = []
    started_events: list[AgentEvent] = []
    previous_time = run.created_at
    for expected_sequence, event in enumerate(events, start=1):
        if (
            event.stream_id != run.event_stream_id
            or event.run_id != run.run_id
            or event.workspace_id != run.workspace_id
            or event.trace_id != run.trace_id
        ):
            raise ValueError("Event belongs to another stream, run, workspace, or trace")
        if event.sequence != expected_sequence:
            raise ValueError("Event sequence must be contiguous and start at one")
        if event.occurred_at < previous_time:
            raise ValueError("Event occurrence time cannot move backwards")
        if terminal_events:
            raise ValueError("No Event may follow a terminal Event")
        if event.event_type in TERMINAL_AGENT_EVENT_TYPES:
            terminal_events.append(event)
        if event.event_type is AgentEventType.RUN_STARTED:
            started_events.append(event)
        previous_time = event.occurred_at

    first = events[0]
    if first.event_type is not AgentEventType.RUN_QUEUED or first.occurred_at != run.created_at:
        raise ValueError("The first Event must queue the Run at its creation time")
    if len(started_events) > 1:
        raise ValueError("A Run may have only one initial started Event")
    if run.started_at is not None:
        if not started_events or started_events[0].occurred_at != run.started_at:
            raise ValueError("Run start metadata must match its started Event")
    elif started_events:
        raise ValueError("A Run without a start time cannot have a started Event")

    if run.status in TERMINAL_RUN_STATUSES:
        expected_terminal = _TERMINAL_EVENT_BY_STATUS[run.status]
        if len(terminal_events) != 1 or terminal_events[0].event_type is not expected_terminal:
            raise ValueError("Run status must match exactly one terminal Event")
        if terminal_events[0] is not events[-1]:
            raise ValueError("The terminal Event must be last")
        if terminal_events[0].occurred_at != run.terminal_at:
            raise ValueError("Run terminal metadata must match its terminal Event")
    elif terminal_events:
        raise ValueError("A non-terminal Run cannot contain a terminal Event")
