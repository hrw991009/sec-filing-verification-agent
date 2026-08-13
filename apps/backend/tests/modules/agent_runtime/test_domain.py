"""Permanent tests for unified Agent Runtime domain contracts."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from industry_platform.modules.agent_runtime.domain import (
    AGENT_RUNTIME_SCHEMA_VERSION,
    TERMINAL_RUN_STATUSES,
    AgentRun,
    AgentRunStatus,
    AgentRunType,
    AgentStep,
    AgentStepKind,
    AgentStepStatus,
    RunArtifact,
    RunArtifactKind,
    RunBudget,
    RunStopReason,
    validate_artifact_references,
    validate_step_sequence,
)
from industry_platform.modules.identity.domain import TraceId

RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
STREAM_ID = UUID("22222222-2222-4222-8222-222222222222")
WORKSPACE_ID = UUID("33333333-3333-4333-8333-333333333333")
USER_ID = UUID("44444444-4444-4444-8444-444444444444")
THREAD_ID = UUID("55555555-5555-4555-8555-555555555555")
TURN_ID = UUID("66666666-6666-4666-8666-666666666666")
STEP_ONE_ID = UUID("77777777-7777-4777-8777-777777777777")
STEP_TWO_ID = UUID("88888888-8888-4888-8888-888888888888")
ARTIFACT_ID = UUID("99999999-9999-4999-8999-999999999999")
NOW = datetime(2026, 8, 13, 1, 0, tzinfo=UTC)


def budget(*, max_steps: int = 8) -> RunBudget:
    return RunBudget(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        max_steps=max_steps,
        max_total_tokens=4_096,
        max_cost_micro_usd=500_000,
        deadline=NOW + timedelta(minutes=10),
    )


def run(
    *,
    status: AgentRunStatus = AgentRunStatus.QUEUED,
    state_revision: int = 0,
    stop_reason: RunStopReason | None = None,
    started_at: datetime | None = None,
    terminal_at: datetime | None = None,
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
        budget=budget(),
        trace_id=TraceId("trace-day2-domain"),
        status=status,
        state_revision=state_revision,
        created_at=NOW,
        started_at=started_at,
        terminal_at=terminal_at,
        stop_reason=stop_reason,
        thread_id=THREAD_ID,
        turn_id=TURN_ID,
    )


def model_step(*, output_artifact_ids: tuple[UUID, ...] = ()) -> AgentStep:
    return AgentStep(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        step_id=STEP_ONE_ID,
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        sequence=1,
        kind=AgentStepKind.MODEL,
        status=AgentStepStatus.COMPLETED,
        state_revision=1,
        started_at=NOW + timedelta(seconds=1),
        completed_at=NOW + timedelta(seconds=2),
        input_summary={"message_count": 2},
        output_summary={"finish_reason": "stop"},
        output_artifact_ids=output_artifact_ids,
        input_tokens=12,
        output_tokens=8,
        cost_micro_usd=25,
        latency_ms=1_000,
    )


def final_step() -> AgentStep:
    return AgentStep(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        step_id=STEP_TWO_ID,
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        sequence=2,
        kind=AgentStepKind.FINAL,
        status=AgentStepStatus.COMPLETED,
        state_revision=2,
        started_at=NOW + timedelta(seconds=2),
        completed_at=NOW + timedelta(seconds=3),
        output_summary={"message_id": str(TURN_ID)},
        latency_ms=1_000,
    )


def test_run_budget_is_versioned_bounded_and_uses_integer_cost() -> None:
    assert budget().max_cost_micro_usd == 500_000

    with pytest.raises(ValueError, match="schema version"):
        replace(budget(), schema_version=2)
    with pytest.raises(ValueError, match="max steps"):
        replace(budget(), max_steps=0)
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        replace(budget(), deadline=NOW.replace(tzinfo=None))


def test_run_requires_consistent_references_versions_and_terminal_metadata() -> None:
    queued = run()
    completed = run(
        status=AgentRunStatus.COMPLETED,
        state_revision=3,
        stop_reason=RunStopReason.FINAL,
        started_at=NOW + timedelta(seconds=1),
        terminal_at=NOW + timedelta(seconds=3),
    )

    assert queued.status is AgentRunStatus.QUEUED
    assert completed.status in TERMINAL_RUN_STATUSES

    with pytest.raises(ValueError, match="turn reference"):
        replace(queued, thread_id=None)
    with pytest.raises(ValueError, match="Runtime version"):
        replace(queued, runtime_version=" invalid ")
    with pytest.raises(ValueError, match="terminal runs require a stop reason"):
        replace(completed, stop_reason=None)
    with pytest.raises(ValueError, match="final reason"):
        replace(completed, stop_reason=RunStopReason.PROVIDER_ERROR)
    with pytest.raises(ValueError, match="failure stop reason"):
        replace(
            completed,
            status=AgentRunStatus.FAILED,
            stop_reason=RunStopReason.FINAL,
        )


def test_step_snapshots_summaries_without_leaking_them_in_repr() -> None:
    sensitive_text = "do not log this prompt"
    step = replace(model_step(), input_summary={"prompt": sensitive_text})

    assert step.input_summary == {"prompt": sensitive_text}
    assert sensitive_text not in repr(step)
    with pytest.raises(TypeError):
        step.input_summary["prompt"] = "changed"  # type: ignore[index]
    with pytest.raises(ValueError, match="stable error code"):
        replace(step, status=AgentStepStatus.FAILED, error_code=None)
    with pytest.raises(ValueError, match="Only failed"):
        replace(step, error_code="provider_timeout")


def test_step_sequence_is_contiguous_monotonic_and_has_one_final_step() -> None:
    completed_run = run(
        status=AgentRunStatus.COMPLETED,
        state_revision=3,
        stop_reason=RunStopReason.FINAL,
        started_at=NOW + timedelta(seconds=1),
        terminal_at=NOW + timedelta(seconds=3),
    )
    steps = (model_step(), final_step())

    validate_step_sequence(steps, completed_run)

    with pytest.raises(ValueError, match="contiguous"):
        validate_step_sequence((steps[0], replace(steps[1], sequence=3)), completed_run)
    with pytest.raises(ValueError, match="revisions"):
        validate_step_sequence(
            (steps[0], replace(steps[1], state_revision=1)),
            completed_run,
        )
    with pytest.raises(ValueError, match="completed final step"):
        validate_step_sequence((steps[0],), completed_run)
    with pytest.raises(ValueError, match="non-terminal"):
        validate_step_sequence(steps, run(state_revision=3))


def test_artifact_references_are_closed_within_the_same_run() -> None:
    step = model_step(output_artifact_ids=(ARTIFACT_ID,))
    artifact = RunArtifact(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        artifact_id=ARTIFACT_ID,
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        kind=RunArtifactKind.REPORT,
        resource_ref="minio://artifacts/day2/report.json",
        content_sha256="a" * 64,
        version=1,
        created_at=NOW + timedelta(seconds=2),
        originating_step_id=STEP_ONE_ID,
    )

    validate_artifact_references((artifact,), (step,), run(state_revision=1))
    assert "minio://" not in repr(artifact)

    with pytest.raises(ValueError, match="dangling Artifact"):
        validate_artifact_references((), (step,), run(state_revision=1))
    with pytest.raises(ValueError, match="originating Step"):
        validate_artifact_references(
            (replace(artifact, originating_step_id=STEP_TWO_ID),),
            (step,),
            run(state_revision=1),
        )
