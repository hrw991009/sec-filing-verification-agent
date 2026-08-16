"""Tests for the safe, immutable Workbench Trace contract."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from industry_platform.modules.agent_runtime.domain import (
    AgentRunStatus,
    AgentRunType,
    AgentStepKind,
    AgentStepStatus,
)
from industry_platform.modules.agent_runtime.events import AgentEventType
from industry_platform.modules.agent_runtime.trace import (
    TraceEvent,
    TraceRun,
    TraceStep,
    TraceUsage,
)
from industry_platform.modules.identity.domain import TraceId

NOW = datetime(2026, 8, 13, 9, 0, tzinfo=UTC)
RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
WORKSPACE_ID = UUID("22222222-2222-4222-8222-222222222222")
CONVERSATION_ID = UUID("33333333-3333-4333-8333-333333333333")
TURN_ID = UUID("44444444-4444-4444-8444-444444444444")
STREAM_ID = UUID("55555555-5555-4555-8555-555555555555")
STEP_ID = UUID("66666666-6666-4666-8666-666666666666")


def usage() -> TraceUsage:
    return TraceUsage(
        input_tokens=20,
        output_tokens=5,
        cached_input_tokens=3,
        cost_micro_usd=40,
    )


def test_trace_usage_counts_cached_tokens_inside_input_tokens() -> None:
    value = usage()

    assert value.total_tokens == 25
    with pytest.raises(ValueError, match="cannot exceed"):
        TraceUsage(
            input_tokens=2,
            output_tokens=1,
            cached_input_tokens=3,
            cost_micro_usd=1,
        )


def test_trace_event_keeps_only_an_immutable_scalar_details_mapping() -> None:
    original: dict[str, str | int] = {"model_sequence": 1}
    event = TraceEvent(
        schema_version=1,
        sequence=4,
        occurred_at=NOW,
        event_type=AgentEventType.MODEL_DELTA,
        details=original,
    )

    original["model_sequence"] = 99
    assert event.details == {"model_sequence": 1}
    with pytest.raises(TypeError):
        event.details["model_sequence"] = 2  # type: ignore[index]


def test_run_and_step_contract_reject_inconsistent_timeline_facts() -> None:
    run = TraceRun(
        schema_version=1,
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        conversation_id=CONVERSATION_ID,
        turn_id=TURN_ID,
        event_stream_id=STREAM_ID,
        trace_id=TraceId("trace-v0"),
        run_type=AgentRunType.DIRECT_ANSWER,
        status=AgentRunStatus.RUNNING,
        stop_reason=None,
        runtime_version="runtime-v0",
        harness_version="harness-v0",
        state_revision=2,
        max_steps=2,
        max_total_tokens=1_000,
        max_cost_micro_usd=10_000,
        deadline=NOW + timedelta(minutes=5),
        event_count=3,
        step_count=1,
        usage=usage(),
        created_at=NOW,
        started_at=NOW,
        terminal_at=None,
    )
    assert run.step_count == 1

    with pytest.raises(ValueError, match="failed Trace Step requires"):
        TraceStep(
            step_id=STEP_ID,
            sequence=1,
            kind=AgentStepKind.MODEL,
            status=AgentStepStatus.FAILED,
            last_event_sequence=3,
            started_at=NOW,
            completed_at=NOW,
            usage=usage(),
            error_code=None,
        )
