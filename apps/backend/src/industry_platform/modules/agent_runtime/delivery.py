"""Application boundary for authorized Agent event delivery and cancellation."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from industry_platform.modules.agent_runtime.domain import (
    TERMINAL_RUN_STATUSES,
    AgentRunStatus,
    require_non_nil_uuid,
)
from industry_platform.modules.agent_runtime.events import (
    TERMINAL_AGENT_EVENT_TYPES,
    AgentEvent,
)
from industry_platform.modules.agent_runtime.streaming import (
    MAX_STREAM_CURSOR,
    CommittedEventSource,
    StreamReplay,
    StreamResetRequiredError,
    load_committed_replay,
)
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.workspaces.domain import (
    WorkspaceAccessDeniedError,
    WorkspaceAction,
    WorkspaceScope,
)
from industry_platform.modules.workspaces.policy import scope_allows

MAX_DELIVERY_EVENT_BATCH = 256
type UtcClock = Callable[[], datetime]


def utc_now() -> datetime:
    """Return a timezone-aware instant through an injectable boundary."""

    return datetime.now(UTC)


class AgentRunNotFoundError(RuntimeError):
    """Raised without revealing whether a Run exists in another Workspace."""


class AgentRunDeliveryStateError(RuntimeError):
    """Raised when committed Run and Event facts cannot form a safe stream."""


class AgentRunDeliveryUnavailableError(RuntimeError):
    """Sanitized infrastructure failure at the Agent delivery boundary."""

    def __init__(self, *, sqlstate: str | None = None) -> None:
        super().__init__("Agent event delivery is unavailable")
        self.sqlstate = sqlstate


@dataclass(frozen=True, slots=True)
class AgentRunStreamDescriptor:
    """Minimum trusted Run facts needed to authorize one delivery request."""

    run_id: UUID
    workspace_id: UUID
    user_id: UUID
    stream_id: UUID
    trace_id: TraceId
    status: AgentRunStatus
    latest_committed_sequence: int

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.run_id, field_name="Agent Run ID")
        require_non_nil_uuid(self.workspace_id, field_name="Agent Run Workspace ID")
        require_non_nil_uuid(self.user_id, field_name="Agent Run user ID")
        require_non_nil_uuid(self.stream_id, field_name="Agent stream ID")
        if not str(self.trace_id).strip() or len(str(self.trace_id)) > 128:
            raise ValueError("Agent Run trace ID is invalid")
        if isinstance(self.latest_committed_sequence, bool) or self.latest_committed_sequence < 1:
            raise ValueError("Agent Run committed sequence is invalid")

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_RUN_STATUSES


class AgentRunEventReader(CommittedEventSource, Protocol):
    """Read Workspace-scoped Run metadata and committed Event batches."""

    async def find_run(
        self, *, run_id: UUID, workspace_id: UUID
    ) -> AgentRunStreamDescriptor | None: ...

    async def load_events_after(
        self,
        *,
        run_id: UUID,
        stream_id: UUID,
        workspace_id: UUID,
        after_sequence: int,
        limit: int,
    ) -> tuple[AgentEvent, ...]: ...


class AgentRunCancellationController(Protocol):
    """Persist an explicit cooperative cancellation request."""

    async def request_cancel(
        self, *, run_id: UUID, workspace_id: UUID, requested_at: datetime
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class PreparedAgentEventStream:
    """Authorized initial replay prepared before HTTP response headers are sent."""

    descriptor: AgentRunStreamDescriptor
    replay: StreamReplay

    @property
    def is_terminal(self) -> bool:
        """Use the newer authoritative snapshot when the metadata read raced completion."""

        snapshot = self.replay.snapshot
        return self.descriptor.is_terminal or (
            snapshot is not None and snapshot.payload.get("terminal") is True
        )


class AgentRunDeliveryUseCase(Protocol):
    """Operations used by the HTTP adapter without invoking AgentRuntime."""

    async def prepare_stream(
        self,
        scope: WorkspaceScope,
        *,
        run_id: UUID,
        last_event_id: str | None,
    ) -> PreparedAgentEventStream: ...

    async def load_events_after(
        self,
        scope: WorkspaceScope,
        *,
        descriptor: AgentRunStreamDescriptor,
        after_sequence: int,
    ) -> tuple[AgentEvent, ...]: ...

    async def request_cancel(self, scope: WorkspaceScope, *, run_id: UUID) -> None: ...


@dataclass(frozen=True, slots=True)
class AgentRunDeliveryService:
    """Authorize delivery, replay committed Events, and request cancellation."""

    event_reader: AgentRunEventReader
    cancellation_controller: AgentRunCancellationController
    clock: UtcClock = utc_now

    async def prepare_stream(
        self,
        scope: WorkspaceScope,
        *,
        run_id: UUID,
        last_event_id: str | None,
    ) -> PreparedAgentEventStream:
        _require_action(scope, WorkspaceAction.VIEW)
        descriptor = await self._find_run(scope, run_id)
        replay = await load_committed_replay(
            self.event_reader,
            stream_id=descriptor.stream_id,
            workspace_id=scope.workspace_id,
            last_event_id=last_event_id,
        )
        _validate_replay(descriptor, replay)
        return PreparedAgentEventStream(descriptor=descriptor, replay=replay)

    async def load_events_after(
        self,
        scope: WorkspaceScope,
        *,
        descriptor: AgentRunStreamDescriptor,
        after_sequence: int,
    ) -> tuple[AgentEvent, ...]:
        _require_action(scope, WorkspaceAction.VIEW)
        if descriptor.workspace_id != scope.workspace_id:
            raise WorkspaceAccessDeniedError
        if isinstance(after_sequence, bool) or not 0 <= after_sequence <= MAX_STREAM_CURSOR:
            raise StreamResetRequiredError
        events = await self.event_reader.load_events_after(
            run_id=descriptor.run_id,
            stream_id=descriptor.stream_id,
            workspace_id=scope.workspace_id,
            after_sequence=after_sequence,
            limit=MAX_DELIVERY_EVENT_BATCH,
        )
        _validate_event_batch(descriptor, events, after_sequence=after_sequence)
        return events

    async def request_cancel(self, scope: WorkspaceScope, *, run_id: UUID) -> None:
        _require_action(scope, WorkspaceAction.UPDATE_RESOURCE)
        descriptor = await self._find_run(scope, run_id)
        if scope.user_id != descriptor.user_id and scope.role not in {"owner", "admin"}:
            raise WorkspaceAccessDeniedError
        if descriptor.is_terminal:
            return
        requested_at = self.clock()
        if requested_at.tzinfo is None or requested_at.utcoffset() != UTC.utcoffset(requested_at):
            raise ValueError("Agent cancellation clock must return UTC")
        accepted = await self.cancellation_controller.request_cancel(
            run_id=run_id,
            workspace_id=scope.workspace_id,
            requested_at=requested_at,
        )
        if not accepted:
            raise AgentRunNotFoundError

    async def _find_run(self, scope: WorkspaceScope, run_id: UUID) -> AgentRunStreamDescriptor:
        descriptor = await self.event_reader.find_run(
            run_id=run_id,
            workspace_id=scope.workspace_id,
        )
        if descriptor is None:
            raise AgentRunNotFoundError
        if descriptor.workspace_id != scope.workspace_id:
            raise AgentRunDeliveryStateError
        return descriptor


def _require_action(scope: WorkspaceScope, action: WorkspaceAction) -> None:
    if not scope_allows(scope, action):
        raise WorkspaceAccessDeniedError


def _validate_replay(descriptor: AgentRunStreamDescriptor, replay: StreamReplay) -> None:
    events = replay.events
    for event in events:
        _require_event_identity(descriptor, event)
    terminal_indexes = [
        index
        for index, event in enumerate(events)
        if event.event_type in TERMINAL_AGENT_EVENT_TYPES
    ]
    if len(terminal_indexes) > 1 or (terminal_indexes and terminal_indexes[0] != len(events) - 1):
        raise AgentRunDeliveryStateError
    replay_position = (
        replay.snapshot.last_sequence if replay.snapshot is not None else replay.cursor
    )
    if (
        descriptor.is_terminal
        and not terminal_indexes
        and replay_position < descriptor.latest_committed_sequence
    ):
        raise AgentRunDeliveryStateError


def _validate_event_batch(
    descriptor: AgentRunStreamDescriptor,
    events: tuple[AgentEvent, ...],
    *,
    after_sequence: int,
) -> None:
    if len(events) > MAX_DELIVERY_EVENT_BATCH:
        raise AgentRunDeliveryStateError
    expected_sequence = after_sequence + 1
    terminal_seen = False
    for event in events:
        _require_event_identity(descriptor, event)
        if event.sequence != expected_sequence or terminal_seen:
            raise StreamResetRequiredError
        terminal_seen = event.event_type in TERMINAL_AGENT_EVENT_TYPES
        expected_sequence += 1


def _require_event_identity(
    descriptor: AgentRunStreamDescriptor,
    event: AgentEvent,
) -> None:
    if (
        event.run_id != descriptor.run_id
        or event.workspace_id != descriptor.workspace_id
        or event.stream_id != descriptor.stream_id
        or event.trace_id != descriptor.trace_id
    ):
        raise AgentRunDeliveryStateError
