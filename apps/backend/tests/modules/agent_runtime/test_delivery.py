"""Application tests for authorized committed-event delivery and cancellation."""

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from industry_platform.modules.agent_runtime.delivery import (
    AgentRunDeliveryService,
    AgentRunNotFoundError,
    AgentRunStreamDescriptor,
)
from industry_platform.modules.agent_runtime.domain import AgentRunStatus
from industry_platform.modules.agent_runtime.events import AgentEvent, AgentEventType
from industry_platform.modules.agent_runtime.streaming import (
    CommittedEventWindow,
    StreamResetRequiredError,
)
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.workspaces.domain import (
    WorkspaceAccessDeniedError,
    WorkspaceScope,
)

NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
USER_ID = UUID("22222222-2222-4222-8222-222222222222")
OTHER_USER_ID = UUID("33333333-3333-4333-8333-333333333333")
RUN_ID = UUID("44444444-4444-4444-8444-444444444444")
STREAM_ID = UUID("55555555-5555-4555-8555-555555555555")
TRACE_ID = TraceId("agent-delivery-test")


def event(sequence: int, event_type: AgentEventType) -> AgentEvent:
    return AgentEvent(
        schema_version=1,
        stream_id=STREAM_ID,
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        sequence=sequence,
        occurred_at=NOW,
        trace_id=TRACE_ID,
        event_type=event_type,
        payload=(
            {"stop_reason": "runtime_error"} if event_type is AgentEventType.RUN_FAILED else {}
        ),
    )


def descriptor(*, status: AgentRunStatus = AgentRunStatus.RUNNING) -> AgentRunStreamDescriptor:
    return AgentRunStreamDescriptor(
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        user_id=USER_ID,
        stream_id=STREAM_ID,
        trace_id=TRACE_ID,
        status=status,
        latest_committed_sequence=2 if status in {AgentRunStatus.FAILED} else 1,
    )


@dataclass(slots=True)
class RecordingReader:
    descriptor_value: AgentRunStreamDescriptor | None
    window: CommittedEventWindow
    next_events: tuple[AgentEvent, ...] = ()
    calls: list[tuple[str, object]] = field(default_factory=list)

    async def find_run(
        self, *, run_id: UUID, workspace_id: UUID
    ) -> AgentRunStreamDescriptor | None:
        self.calls.append(("find", (run_id, workspace_id)))
        return self.descriptor_value

    async def load_window(self, *, stream_id: UUID, workspace_id: UUID) -> CommittedEventWindow:
        self.calls.append(("window", (stream_id, workspace_id)))
        return self.window

    async def load_events_after(
        self,
        *,
        run_id: UUID,
        stream_id: UUID,
        workspace_id: UUID,
        after_sequence: int,
        limit: int,
    ) -> tuple[AgentEvent, ...]:
        self.calls.append(
            (
                "after",
                (run_id, stream_id, workspace_id, after_sequence, limit),
            )
        )
        return self.next_events


@dataclass(slots=True)
class RecordingCancellationController:
    accepted: bool = True
    calls: list[tuple[UUID, UUID, datetime]] = field(default_factory=list)

    async def request_cancel(
        self, *, run_id: UUID, workspace_id: UUID, requested_at: datetime
    ) -> bool:
        self.calls.append((run_id, workspace_id, requested_at))
        return self.accepted


def reader_for(
    run: AgentRunStreamDescriptor,
    *events: AgentEvent,
) -> RecordingReader:
    return RecordingReader(
        descriptor_value=run,
        window=CommittedEventWindow(
            stream_id=STREAM_ID,
            workspace_id=WORKSPACE_ID,
            earliest_available_sequence=1 if events else 0,
            latest_committed_sequence=events[-1].sequence if events else 0,
            events=tuple(events),
        ),
    )


@pytest.mark.asyncio
async def test_prepare_replays_only_committed_events_after_the_browser_cursor() -> None:
    queued = event(1, AgentEventType.RUN_QUEUED)
    failed = event(2, AgentEventType.RUN_FAILED)
    reader = reader_for(descriptor(status=AgentRunStatus.FAILED), queued, failed)
    service = AgentRunDeliveryService(reader, RecordingCancellationController())

    prepared = await service.prepare_stream(
        WorkspaceScope(WORKSPACE_ID, USER_ID, "member"),
        run_id=RUN_ID,
        last_event_id="1",
    )

    assert prepared.descriptor.stream_id == STREAM_ID
    assert prepared.replay.events == (failed,)
    assert [call[0] for call in reader.calls] == ["find", "window"]


@pytest.mark.asyncio
async def test_live_follow_rejects_a_committed_sequence_gap() -> None:
    reader = reader_for(descriptor(), event(1, AgentEventType.RUN_QUEUED))
    reader.next_events = (event(3, AgentEventType.MODEL_DELTA),)
    service = AgentRunDeliveryService(reader, RecordingCancellationController())

    with pytest.raises(StreamResetRequiredError):
        await service.load_events_after(
            WorkspaceScope(WORKSPACE_ID, USER_ID, "member"),
            descriptor=descriptor(),
            after_sequence=1,
        )


@pytest.mark.asyncio
async def test_member_can_cancel_only_their_own_non_terminal_run() -> None:
    reader = reader_for(descriptor(), event(1, AgentEventType.RUN_QUEUED))
    controller = RecordingCancellationController()
    service = AgentRunDeliveryService(reader, controller, clock=lambda: NOW)
    scope = WorkspaceScope(WORKSPACE_ID, USER_ID, "member")

    await service.request_cancel(scope, run_id=RUN_ID)
    assert controller.calls == [(RUN_ID, WORKSPACE_ID, NOW)]

    reader.descriptor_value = replace(descriptor(), user_id=OTHER_USER_ID)
    with pytest.raises(WorkspaceAccessDeniedError):
        await service.request_cancel(scope, run_id=RUN_ID)


@pytest.mark.asyncio
async def test_terminal_cancel_is_idempotent_and_missing_run_is_not_disclosed() -> None:
    reader = reader_for(
        descriptor(status=AgentRunStatus.FAILED),
        event(1, AgentEventType.RUN_QUEUED),
        event(2, AgentEventType.RUN_FAILED),
    )
    controller = RecordingCancellationController()
    service = AgentRunDeliveryService(reader, controller, clock=lambda: NOW)

    await service.request_cancel(
        WorkspaceScope(WORKSPACE_ID, USER_ID, "member"),
        run_id=RUN_ID,
    )
    assert controller.calls == []

    reader.descriptor_value = None
    with pytest.raises(AgentRunNotFoundError):
        await service.prepare_stream(
            WorkspaceScope(WORKSPACE_ID, USER_ID, "viewer"),
            run_id=RUN_ID,
            last_event_id=None,
        )
