"""SSE wire, committed-event replay, cursor, snapshot, and backpressure contracts."""

import json
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final, Protocol
from uuid import UUID

from industry_platform.modules.agent_runtime.domain import (
    require_current_schema_version,
    require_non_nil_uuid,
    require_utc,
    snapshot_json_mapping,
)
from industry_platform.modules.agent_runtime.events import AgentEvent
from industry_platform.modules.identity.domain import TraceId

MAX_STREAM_CURSOR: Final = 9_223_372_036_854_775_807
DEFAULT_HEARTBEAT_SECONDS: Final = 15


class StreamErrorCode(StrEnum):
    """Stable errors mapped to HTTP status by the later API layer."""

    INVALID_CURSOR = "INVALID_STREAM_CURSOR"
    CURSOR_AHEAD = "STREAM_CURSOR_AHEAD"
    RESET_REQUIRED = "STREAM_RESET_REQUIRED"
    BACKPRESSURE = "STREAM_BACKPRESSURE"


class StreamContractError(RuntimeError):
    """Base error containing only a stable public code."""

    def __init__(self, code: StreamErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


class InvalidStreamCursorError(StreamContractError):
    def __init__(self) -> None:
        super().__init__(StreamErrorCode.INVALID_CURSOR)


class StreamCursorAheadError(StreamContractError):
    def __init__(self) -> None:
        super().__init__(StreamErrorCode.CURSOR_AHEAD)


class StreamResetRequiredError(StreamContractError):
    def __init__(self) -> None:
        super().__init__(StreamErrorCode.RESET_REQUIRED)


class StreamBackpressureError(StreamContractError):
    """Close this subscription; reconnect from last_delivered_sequence."""

    def __init__(self, *, last_delivered_sequence: int) -> None:
        super().__init__(StreamErrorCode.BACKPRESSURE)
        self.last_delivered_sequence = last_delivered_sequence


def parse_last_event_id(value: str | None) -> int:
    """Parse the current stream's decimal cursor; zero means start from available data."""

    if value is None:
        return 0
    if not value or not value.isascii() or not value.isdecimal():
        raise InvalidStreamCursorError
    try:
        cursor = int(value)
    except ValueError:
        raise InvalidStreamCursorError from None
    if not 0 <= cursor <= MAX_STREAM_CURSOR:
        raise InvalidStreamCursorError
    return cursor


@dataclass(frozen=True, slots=True)
class StreamSnapshot:
    """A non-business control frame aligned to an already committed sequence."""

    schema_version: int
    stream_id: UUID
    workspace_id: UUID
    trace_id: TraceId
    last_sequence: int
    occurred_at: datetime
    payload: Mapping[str, object] = field(repr=False)

    def __post_init__(self) -> None:
        require_current_schema_version(self.schema_version)
        require_non_nil_uuid(self.stream_id, field_name="Snapshot stream ID")
        require_non_nil_uuid(self.workspace_id, field_name="Snapshot Workspace ID")
        if isinstance(self.last_sequence, bool) or not 0 <= self.last_sequence <= MAX_STREAM_CURSOR:
            raise ValueError("Snapshot sequence is invalid")
        require_utc(self.occurred_at, field_name="Snapshot occurrence time")
        if not str(self.trace_id).strip() or len(str(self.trace_id)) > 128:
            raise ValueError("Snapshot trace ID is invalid")
        object.__setattr__(
            self,
            "payload",
            snapshot_json_mapping(
                self.payload,
                error_message="Snapshot payload must be canonical JSON data",
            ),
        )


@dataclass(frozen=True, slots=True)
class CommittedEventWindow:
    """Authoritative committed tail loaded without invoking AgentRuntime."""

    stream_id: UUID
    workspace_id: UUID
    earliest_available_sequence: int
    latest_committed_sequence: int
    events: tuple[AgentEvent, ...]
    snapshot: StreamSnapshot | None = None

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.stream_id, field_name="Committed stream ID")
        require_non_nil_uuid(self.workspace_id, field_name="Committed stream Workspace ID")
        for value, field_name in (
            (self.earliest_available_sequence, "Earliest stream sequence"),
            (self.latest_committed_sequence, "Latest stream sequence"),
        ):
            if isinstance(value, bool) or value < 0:
                raise ValueError(f"{field_name} is invalid")
        if self.latest_committed_sequence == 0:
            if self.earliest_available_sequence not in {0, 1} or self.events:
                raise ValueError("An empty committed stream cannot contain Events")
        elif not 1 <= self.earliest_available_sequence <= self.latest_committed_sequence:
            raise ValueError("Committed Event bounds are invalid")

        events = tuple(self.events)
        run_id: UUID | None = None
        trace_id: TraceId | None = None
        for expected, event in enumerate(events, start=self.earliest_available_sequence):
            if event.stream_id != self.stream_id or event.workspace_id != self.workspace_id:
                raise ValueError("Committed Event belongs to another stream or Workspace")
            if event.sequence != expected:
                raise ValueError("Committed Event window must be contiguous")
            if run_id is None:
                run_id = event.run_id
                trace_id = event.trace_id
            elif event.run_id != run_id or event.trace_id != trace_id:
                raise ValueError("Committed Event window cannot mix Runs or traces")
        if events and events[-1].sequence != self.latest_committed_sequence:
            raise ValueError("Committed Event window must end at the latest sequence")
        if not events and self.latest_committed_sequence > 0:
            raise ValueError("A non-empty committed stream requires its available Event tail")
        object.__setattr__(self, "events", events)

        if self.snapshot is not None and (
            self.snapshot.stream_id != self.stream_id
            or self.snapshot.workspace_id != self.workspace_id
            or self.snapshot.last_sequence > self.latest_committed_sequence
            or (trace_id is not None and self.snapshot.trace_id != trace_id)
        ):
            raise ValueError("Snapshot does not match its committed Event window")


class CommittedEventSource(Protocol):
    """Read only persisted Events; this boundary must never call Runtime or Provider."""

    async def load_window(self, *, stream_id: UUID, workspace_id: UUID) -> CommittedEventWindow:
        """Reauthorize and load one authoritative stream window."""

        ...


@dataclass(frozen=True, slots=True)
class StreamReplay:
    """A snapshot replacement followed by committed Events after its sequence."""

    cursor: int
    snapshot: StreamSnapshot | None
    events: tuple[AgentEvent, ...]


def select_committed_replay(window: CommittedEventWindow, *, cursor: int) -> StreamReplay:
    """Select replay data without executing or resuming the current Model Step."""

    if isinstance(cursor, bool) or not 0 <= cursor <= MAX_STREAM_CURSOR:
        raise InvalidStreamCursorError
    if cursor > window.latest_committed_sequence:
        raise StreamCursorAheadError

    snapshot: StreamSnapshot | None = None
    replay_after = cursor
    if window.latest_committed_sequence > 0 and cursor < window.earliest_available_sequence - 1:
        snapshot = window.snapshot
        if snapshot is None or snapshot.last_sequence < cursor:
            raise StreamResetRequiredError
        replay_after = snapshot.last_sequence
    events = tuple(event for event in window.events if event.sequence > replay_after)
    if events and events[0].sequence != replay_after + 1:
        raise StreamResetRequiredError
    return StreamReplay(cursor=cursor, snapshot=snapshot, events=events)


async def load_committed_replay(
    source: CommittedEventSource,
    *,
    stream_id: UUID,
    workspace_id: UUID,
    last_event_id: str | None,
) -> StreamReplay:
    """Load a replay exclusively through the committed-event source."""

    cursor = parse_last_event_id(last_event_id)
    window = await source.load_window(stream_id=stream_id, workspace_id=workspace_id)
    if window.stream_id != stream_id or window.workspace_id != workspace_id:
        raise ValueError("Committed Event source returned another authorized stream")
    return select_committed_replay(window, cursor=cursor)


def _json_line(value: Mapping[str, object]) -> str:
    return json.dumps(
        dict(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def encode_agent_event_sse(event: AgentEvent) -> bytes:
    """Encode one committed business Event into the fixed three-field SSE frame."""

    envelope: dict[str, object] = {
        "schema_version": event.schema_version,
        "stream_id": str(event.stream_id),
        "sequence": event.sequence,
        "occurred_at": event.occurred_at.isoformat().replace("+00:00", "Z"),
        "trace_id": str(event.trace_id),
        "type": event.event_type.value,
        "payload": dict(event.payload),
    }
    return (
        f"id: {event.sequence}\nevent: {event.event_type.value}\ndata: {_json_line(envelope)}\n\n"
    ).encode()


def encode_snapshot_sse(snapshot: StreamSnapshot) -> bytes:
    """Replace client state at an existing cursor without creating a business Event."""

    envelope: dict[str, object] = {
        "schema_version": snapshot.schema_version,
        "stream_id": str(snapshot.stream_id),
        "sequence": snapshot.last_sequence,
        "occurred_at": snapshot.occurred_at.isoformat().replace("+00:00", "Z"),
        "trace_id": str(snapshot.trace_id),
        "type": "stream.snapshot",
        "payload": dict(snapshot.payload),
    }
    return (
        f"id: {snapshot.last_sequence}\nevent: stream.snapshot\ndata: {_json_line(envelope)}\n\n"
    ).encode()


def encode_heartbeat_sse(*, last_sequence: int) -> bytes:
    """Emit a comment with no id, so the browser cursor never advances."""

    if isinstance(last_sequence, bool) or not 0 <= last_sequence <= MAX_STREAM_CURSOR:
        raise ValueError("Heartbeat sequence is invalid")
    return f": heartbeat last_sequence={last_sequence}\n\n".encode()


class BoundedCommittedEventBuffer:
    """Never drop numbered Events; close a slow subscription and require replay."""

    def __init__(self, *, capacity: int, last_delivered_sequence: int) -> None:
        if isinstance(capacity, bool) or not 1 <= capacity <= 10_000:
            raise ValueError("Stream buffer capacity is invalid")
        if (
            isinstance(last_delivered_sequence, bool)
            or not 0 <= last_delivered_sequence <= MAX_STREAM_CURSOR
        ):
            raise ValueError("Stream buffer cursor is invalid")
        self._capacity = capacity
        self._last_delivered_sequence = last_delivered_sequence
        self._last_enqueued_sequence = last_delivered_sequence
        self._events: deque[AgentEvent] = deque()
        self._stream_id: UUID | None = None

    @property
    def last_delivered_sequence(self) -> int:
        return self._last_delivered_sequence

    def offer(self, event: AgentEvent) -> None:
        if self._stream_id is None:
            self._stream_id = event.stream_id
        if event.stream_id != self._stream_id:
            raise ValueError("A subscription buffer cannot mix streams")
        if event.sequence != self._last_enqueued_sequence + 1:
            raise ValueError("Subscription Events must be contiguous")
        if len(self._events) >= self._capacity:
            raise StreamBackpressureError(last_delivered_sequence=self._last_delivered_sequence)
        self._events.append(event)
        self._last_enqueued_sequence = event.sequence

    def pop(self) -> AgentEvent | None:
        if not self._events:
            return None
        event = self._events.popleft()
        self._last_delivered_sequence = event.sequence
        return event

    def __len__(self) -> int:
        return len(self._events)
