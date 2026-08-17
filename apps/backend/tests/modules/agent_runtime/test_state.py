"""Tests for typed Run State, budgets, and optimistic transitions."""

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
from industry_platform.modules.agent_runtime.state import (
    RunState,
    exhausted_budget_reason,
    validate_run_state,
    validate_state_transition,
)
from industry_platform.modules.identity.domain import TraceId

RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
STREAM_ID = UUID("22222222-2222-4222-8222-222222222222")
WORKSPACE_ID = UUID("33333333-3333-4333-8333-333333333333")
USER_ID = UUID("44444444-4444-4444-8444-444444444444")
ARTIFACT_ID = UUID("55555555-5555-4555-8555-555555555555")
NOW = datetime(2026, 8, 13, 2, 0, tzinfo=UTC)


def make_budget() -> RunBudget:
    return RunBudget(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        max_steps=4,
        max_total_tokens=100,
        max_cost_micro_usd=1_000,
        deadline=NOW + timedelta(minutes=5),
    )


def make_run(
    *,
    status: AgentRunStatus,
    revision: int,
    stop_reason: RunStopReason | None = None,
    terminal_at: datetime | None = None,
) -> AgentRun:
    started_at = None if status is AgentRunStatus.QUEUED else NOW + timedelta(seconds=1)
    return AgentRun(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        run_id=RUN_ID,
        event_stream_id=STREAM_ID,
        workspace_id=WORKSPACE_ID,
        user_id=USER_ID,
        run_type=AgentRunType.DIRECT_ANSWER,
        runtime_version="runtime-v0",
        harness_version="direct-answer-v0",
        budget=make_budget(),
        trace_id=TraceId("trace-day2-state"),
        status=status,
        state_revision=revision,
        created_at=NOW,
        started_at=started_at,
        terminal_at=terminal_at,
        stop_reason=stop_reason,
    )


def make_state(
    *,
    status: AgentRunStatus,
    revision: int,
    event_count: int,
    step_count: int = 0,
    stop_reason: RunStopReason | None = None,
    updated_at: datetime = NOW,
) -> RunState:
    return RunState(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        revision=revision,
        status=status,
        step_count=step_count,
        event_count=event_count,
        input_tokens_used=0,
        output_tokens_used=0,
        cost_micro_usd=0,
        updated_at=updated_at,
        stop_reason=stop_reason,
    )


def test_run_and_state_must_share_identity_revision_and_terminal_metadata() -> None:
    queued_run = make_run(status=AgentRunStatus.QUEUED, revision=0)
    queued_state = make_state(
        status=AgentRunStatus.QUEUED,
        revision=0,
        event_count=1,
    )

    validate_run_state(queued_run, queued_state)

    with pytest.raises(ValueError, match="revisions"):
        validate_run_state(queued_run, replace(queued_state, revision=1))
    with pytest.raises(ValueError, match="terminal metadata"):
        validate_run_state(
            make_run(status=AgentRunStatus.RUNNING, revision=0),
            queued_state,
        )


def test_budget_exhaustion_is_deterministic_and_requires_terminalization() -> None:
    running_run = make_run(status=AgentRunStatus.RUNNING, revision=2)
    exhausted_state = replace(
        make_state(
            status=AgentRunStatus.RUNNING,
            revision=2,
            event_count=4,
            step_count=4,
            updated_at=NOW + timedelta(seconds=10),
        ),
        output_tokens_used=100,
    )

    assert exhausted_budget_reason(exhausted_state, make_budget()) is RunStopReason.MAX_STEPS
    with pytest.raises(ValueError, match="must be terminalized"):
        validate_run_state(running_run, exhausted_state)

    failed_run = make_run(
        status=AgentRunStatus.FAILED,
        revision=2,
        stop_reason=RunStopReason.MAX_STEPS,
        terminal_at=NOW + timedelta(seconds=10),
    )
    failed_state = replace(
        exhausted_state,
        status=AgentRunStatus.FAILED,
        stop_reason=RunStopReason.MAX_STEPS,
    )
    validate_run_state(failed_run, failed_state)


def test_cost_budget_preflight_rejection_is_explicit_without_faking_spend() -> None:
    failed_run = make_run(
        status=AgentRunStatus.FAILED,
        revision=2,
        stop_reason=RunStopReason.COST_BUDGET_EXCEEDED,
        terminal_at=NOW + timedelta(seconds=10),
    )
    failed_state = replace(
        make_state(
            status=AgentRunStatus.FAILED,
            revision=2,
            event_count=4,
            stop_reason=RunStopReason.COST_BUDGET_EXCEEDED,
            updated_at=NOW + timedelta(seconds=10),
        ),
        cost_micro_usd=20,
        cost_budget_preflight_rejected=True,
    )

    validate_run_state(failed_run, failed_state)

    with pytest.raises(ValueError, match="exhaustion or a recorded preflight rejection"):
        validate_run_state(
            failed_run,
            replace(failed_state, cost_budget_preflight_rejected=False),
        )
    with pytest.raises(ValueError, match="Only a failed cost preflight"):
        replace(
            failed_state,
            stop_reason=RunStopReason.TOOL_ERROR,
            cost_budget_preflight_rejected=True,
        )


def test_max_steps_preflight_rejection_reserves_a_complete_future_transition() -> None:
    failed_run = make_run(
        status=AgentRunStatus.FAILED,
        revision=2,
        stop_reason=RunStopReason.MAX_STEPS,
        terminal_at=NOW + timedelta(seconds=10),
    )
    failed_state = replace(
        make_state(
            status=AgentRunStatus.FAILED,
            revision=2,
            step_count=2,
            event_count=4,
            stop_reason=RunStopReason.MAX_STEPS,
            updated_at=NOW + timedelta(seconds=10),
        ),
        max_steps_preflight_rejected=True,
    )

    validate_run_state(failed_run, failed_state)

    with pytest.raises(ValueError, match="exhausted step budget"):
        validate_run_state(
            failed_run,
            replace(failed_state, max_steps_preflight_rejected=False),
        )
    with pytest.raises(ValueError, match="Only a failed max-steps preflight"):
        replace(
            failed_state,
            stop_reason=RunStopReason.NO_PROGRESS,
            max_steps_preflight_rejected=True,
        )


def test_state_transition_uses_exact_cas_and_append_only_progress() -> None:
    previous = make_state(
        status=AgentRunStatus.QUEUED,
        revision=0,
        event_count=1,
    )
    successor = replace(
        previous,
        revision=1,
        status=AgentRunStatus.RUNNING,
        event_count=2,
        updated_at=NOW + timedelta(seconds=1),
    )

    validate_state_transition(previous, successor, expected_revision=0)

    with pytest.raises(ValueError, match="stale"):
        validate_state_transition(previous, successor, expected_revision=1)
    with pytest.raises(ValueError, match="exactly one"):
        validate_state_transition(
            previous,
            replace(successor, revision=2),
            expected_revision=0,
        )
    with pytest.raises(ValueError, match="append at least one Event"):
        validate_state_transition(
            previous,
            replace(successor, event_count=1),
            expected_revision=0,
        )


def test_terminal_state_cannot_advance_and_artifact_references_are_append_only() -> None:
    previous = replace(
        make_state(
            status=AgentRunStatus.RUNNING,
            revision=3,
            event_count=5,
            updated_at=NOW + timedelta(seconds=3),
        ),
        artifact_ids=(ARTIFACT_ID,),
    )
    removed_artifact = replace(
        previous,
        revision=4,
        event_count=6,
        artifact_ids=(),
    )
    with pytest.raises(ValueError, match="append-only"):
        validate_state_transition(previous, removed_artifact, expected_revision=3)

    terminal = replace(
        previous,
        status=AgentRunStatus.CANCELLED,
        stop_reason=RunStopReason.CANCELLED,
    )
    with pytest.raises(ValueError, match="terminal Run State"):
        validate_state_transition(
            terminal,
            replace(terminal, revision=4, event_count=6),
            expected_revision=3,
        )
