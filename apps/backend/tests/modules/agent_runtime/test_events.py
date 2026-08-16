"""Tests for versioned Agent Event envelopes and complete stream history."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from industry_platform.modules.agent_runtime.domain import (
    AGENT_RUNTIME_SCHEMA_VERSION,
    AgentRun,
    AgentRunStatus,
    AgentRunType,
    RunBudget,
    RunStopReason,
)
from industry_platform.modules.agent_runtime.events import (
    AgentEvent,
    AgentEventType,
    validate_event_stream,
)
from industry_platform.modules.identity.domain import TraceId

RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
STREAM_ID = UUID("22222222-2222-4222-8222-222222222222")
WORKSPACE_ID = UUID("33333333-3333-4333-8333-333333333333")
USER_ID = UUID("44444444-4444-4444-8444-444444444444")
OTHER_STREAM_ID = UUID("55555555-5555-4555-8555-555555555555")
TRACE_ID = TraceId("trace-day2-events")
NOW = datetime(2026, 8, 13, 3, 0, tzinfo=UTC)


def make_run(
    *,
    status: AgentRunStatus,
    revision: int,
    started_at: datetime | None = None,
    terminal_at: datetime | None = None,
    stop_reason: RunStopReason | None = None,
) -> AgentRun:
    return AgentRun(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        run_id=RUN_ID,
        event_stream_id=STREAM_ID,
        workspace_id=WORKSPACE_ID,
        user_id=USER_ID,
        run_type=AgentRunType.DIRECT_ANSWER,
        runtime_version="runtime-v0",
        harness_version="direct-answer-v0",
        budget=RunBudget(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            max_steps=8,
            max_total_tokens=4_096,
            max_cost_micro_usd=500_000,
            deadline=NOW + timedelta(minutes=10),
        ),
        trace_id=TRACE_ID,
        status=status,
        state_revision=revision,
        created_at=NOW,
        started_at=started_at,
        terminal_at=terminal_at,
        stop_reason=stop_reason,
    )


def event(
    sequence: int,
    event_type: AgentEventType,
    occurred_at: datetime,
) -> AgentEvent:
    return AgentEvent(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        stream_id=STREAM_ID,
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        sequence=sequence,
        occurred_at=occurred_at,
        trace_id=TRACE_ID,
        event_type=event_type,
        payload={"sequence_copy": sequence},
    )


def test_event_payload_is_canonical_immutable_and_not_in_repr() -> None:
    sensitive_marker = "not-for-event-repr"
    queued = replace(
        event(1, AgentEventType.RUN_QUEUED, NOW),
        payload={"text": sensitive_marker},
    )

    assert queued.payload == {"text": sensitive_marker}
    assert sensitive_marker not in repr(queued)
    with pytest.raises(TypeError):
        queued.payload["text"] = "changed"  # type: ignore[index]
    with pytest.raises(ValueError, match="schema version"):
        replace(queued, schema_version=2)


def test_completed_stream_is_contiguous_and_matches_run_lifecycle() -> None:
    started_at = NOW + timedelta(seconds=1)
    terminal_at = NOW + timedelta(seconds=4)
    completed_run = make_run(
        status=AgentRunStatus.COMPLETED,
        revision=3,
        started_at=started_at,
        terminal_at=terminal_at,
        stop_reason=RunStopReason.FINAL,
    )
    events = (
        event(1, AgentEventType.RUN_QUEUED, NOW),
        event(2, AgentEventType.RUN_STARTED, started_at),
        event(3, AgentEventType.MODEL_STARTED, NOW + timedelta(seconds=2)),
        event(4, AgentEventType.MODEL_COMPLETED, NOW + timedelta(seconds=3)),
        event(5, AgentEventType.RUN_COMPLETED, terminal_at),
    )

    validate_event_stream(events, completed_run)

    with pytest.raises(ValueError, match="contiguous"):
        validate_event_stream(
            (*events[:2], replace(events[2], sequence=4), *events[3:]),
            completed_run,
        )
    with pytest.raises(ValueError, match="another stream"):
        validate_event_stream(
            (replace(events[0], stream_id=OTHER_STREAM_ID), *events[1:]),
            completed_run,
        )


def test_terminal_event_is_unique_last_and_matches_terminal_metadata() -> None:
    started_at = NOW + timedelta(seconds=1)
    terminal_at = NOW + timedelta(seconds=3)
    failed_run = make_run(
        status=AgentRunStatus.FAILED,
        revision=2,
        started_at=started_at,
        terminal_at=terminal_at,
        stop_reason=RunStopReason.PROVIDER_TIMEOUT,
    )
    prefix = (
        event(1, AgentEventType.RUN_QUEUED, NOW),
        event(2, AgentEventType.RUN_STARTED, started_at),
    )

    validate_event_stream(
        (*prefix, event(3, AgentEventType.RUN_FAILED, terminal_at)),
        failed_run,
    )

    with pytest.raises(ValueError, match="No Event may follow"):
        validate_event_stream(
            (
                *prefix,
                event(3, AgentEventType.RUN_FAILED, terminal_at),
                event(4, AgentEventType.RUN_CANCELLED, terminal_at),
            ),
            failed_run,
        )
    with pytest.raises(ValueError, match="match exactly one terminal Event"):
        validate_event_stream(
            (*prefix, event(3, AgentEventType.RUN_COMPLETED, terminal_at)),
            failed_run,
        )


def test_queued_run_still_requires_its_initial_persisted_event() -> None:
    queued_run = make_run(status=AgentRunStatus.QUEUED, revision=0)
    validate_event_stream((event(1, AgentEventType.RUN_QUEUED, NOW),), queued_run)

    with pytest.raises(ValueError, match="at least its queued Event"):
        validate_event_stream((), queued_run)
    with pytest.raises(ValueError, match="first Event"):
        validate_event_stream(
            (event(1, AgentEventType.MODEL_STARTED, NOW),),
            queued_run,
        )
