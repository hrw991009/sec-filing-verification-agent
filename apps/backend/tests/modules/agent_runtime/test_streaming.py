"""Tests for committed-event SSE replay, cursors, snapshots, and backpressure."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from industry_platform.modules.agent_runtime.domain import AGENT_RUNTIME_SCHEMA_VERSION
from industry_platform.modules.agent_runtime.events import AgentEvent, AgentEventType
from industry_platform.modules.agent_runtime.streaming import (
    BoundedCommittedEventBuffer,
    CommittedEventWindow,
    InvalidStreamCursorError,
    StreamBackpressureError,
    StreamCursorAheadError,
    StreamResetRequiredError,
    StreamSnapshot,
    encode_agent_event_sse,
    encode_heartbeat_sse,
    encode_snapshot_sse,
    load_committed_replay,
    parse_last_event_id,
    select_committed_replay,
)
from industry_platform.modules.identity.domain import TraceId

RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
STREAM_ID = UUID("22222222-2222-4222-8222-222222222222")
WORKSPACE_ID = UUID("33333333-3333-4333-8333-333333333333")
NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


def event(sequence: int, event_type: AgentEventType = AgentEventType.MODEL_DELTA) -> AgentEvent:
    return AgentEvent(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        stream_id=STREAM_ID,
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        trace_id=TraceId("trace-stream-v1"),
        sequence=sequence,
        occurred_at=NOW + timedelta(seconds=sequence),
        event_type=event_type,
        payload={"delta": f"part-{sequence}"},
    )


def snapshot(last_sequence: int) -> StreamSnapshot:
    return StreamSnapshot(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        stream_id=STREAM_ID,
        workspace_id=WORKSPACE_ID,
        trace_id=TraceId("trace-stream-v1"),
        last_sequence=last_sequence,
        occurred_at=NOW,
        payload={"content_markdown": "committed partial"},
    )


def window(*, earliest: int = 1, latest: int = 3) -> CommittedEventWindow:
    return CommittedEventWindow(
        stream_id=STREAM_ID,
        workspace_id=WORKSPACE_ID,
        earliest_available_sequence=earliest,
        latest_committed_sequence=latest,
        events=tuple(event(sequence) for sequence in range(earliest, latest + 1)),
        snapshot=snapshot(earliest - 1) if earliest > 1 else None,
    )


def test_sse_frame_has_exact_id_event_and_one_line_json_data() -> None:
    encoded = encode_agent_event_sse(event(1)).decode()
    lines = encoded.splitlines()

    assert lines[0] == "id: 1"
    assert lines[1] == "event: agent.model.delta"
    assert lines[2].startswith("data: {")
    assert json.loads(lines[2].removeprefix("data: "))["sequence"] == 1
    assert encoded.endswith("\n\n")
    heartbeat = encode_heartbeat_sse(last_sequence=1).decode()
    assert heartbeat == ": heartbeat last_sequence=1\n\n"
    assert "id:" not in heartbeat


@pytest.mark.parametrize("value", ["", "-1", " 1", "1.0", "+1", "\uff11"])
def test_cursor_parser_rejects_everything_except_ascii_decimal(value: str) -> None:
    with pytest.raises(InvalidStreamCursorError):
        parse_last_event_id(value)
    assert parse_last_event_id(None) == 0
    assert parse_last_event_id("0") == 0
    assert parse_last_event_id("12") == 12


def test_replay_returns_only_committed_events_after_cursor() -> None:
    replay = select_committed_replay(window(), cursor=1)

    assert [item.sequence for item in replay.events] == [2, 3]
    assert replay.snapshot is None
    with pytest.raises(StreamCursorAheadError):
        select_committed_replay(window(), cursor=4)


def test_expired_cursor_uses_snapshot_or_requires_reset() -> None:
    available = window(earliest=3, latest=5)
    replay = select_committed_replay(available, cursor=0)

    assert replay.snapshot == available.snapshot
    assert replay.snapshot is not None
    assert replay.snapshot.last_sequence == 2
    assert [item.sequence for item in replay.events] == [3, 4, 5]
    snapshot_wire = encode_snapshot_sse(replay.snapshot).decode()
    assert snapshot_wire.startswith("id: 2\nevent: stream.snapshot\n")

    with pytest.raises(StreamResetRequiredError):
        select_committed_replay(
            CommittedEventWindow(
                stream_id=STREAM_ID,
                workspace_id=WORKSPACE_ID,
                earliest_available_sequence=3,
                latest_committed_sequence=5,
                events=(event(3), event(4), event(5)),
            ),
            cursor=0,
        )


@pytest.mark.asyncio
async def test_reconnect_reads_source_once_and_has_no_runtime_dependency() -> None:
    class Source:
        def __init__(self) -> None:
            self.calls = 0

        async def load_window(self, *, stream_id: UUID, workspace_id: UUID) -> CommittedEventWindow:
            assert stream_id == STREAM_ID
            assert workspace_id == WORKSPACE_ID
            self.calls += 1
            return window()

    source = Source()
    replay = await load_committed_replay(
        source,
        stream_id=STREAM_ID,
        workspace_id=WORKSPACE_ID,
        last_event_id="2",
    )

    assert source.calls == 1
    assert [item.sequence for item in replay.events] == [3]


def test_slow_subscription_closes_without_dropping_a_numbered_event() -> None:
    buffer = BoundedCommittedEventBuffer(capacity=2, last_delivered_sequence=0)
    buffer.offer(event(1))
    buffer.offer(event(2))

    with pytest.raises(StreamBackpressureError) as caught:
        buffer.offer(event(3))

    assert caught.value.last_delivered_sequence == 0
    assert [buffer.pop(), buffer.pop()] == [event(1), event(2)]
    assert buffer.last_delivered_sequence == 2
