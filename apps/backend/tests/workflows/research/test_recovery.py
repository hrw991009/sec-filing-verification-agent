"""Checkpoint-tail replay keeps attempts bounded and rejects unknown effects."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from industry_platform.modules.agent_runtime.domain import AgentStepStatus
from industry_platform.modules.agent_runtime.events import AgentEvent, AgentEventType
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.research.domain import ResearchNode
from industry_platform.workflows.research.recovery import restore_model_attempts


def tail() -> tuple[AgentEvent, ...]:
    first = AgentEvent(
        schema_version=1,
        stream_id=uuid4(),
        run_id=uuid4(),
        workspace_id=uuid4(),
        sequence=10,
        occurred_at=datetime(2026, 9, 17, tzinfo=UTC),
        trace_id=TraceId("recovery-tail"),
        event_type=AgentEventType.RESEARCH_NODE_STARTED,
        payload={"node": "research_loop", "state_revision": 8},
    )
    step_id = str(uuid4())
    return (
        first,
        replace(
            first,
            sequence=11,
            event_type=AgentEventType.STEP_STARTED,
            payload={"step_id": step_id, "step_sequence": 1, "step_kind": "model"},
        ),
        replace(
            first,
            sequence=12,
            event_type=AgentEventType.MODEL_STARTED,
            payload={"step_id": step_id},
        ),
    )


def test_interrupted_attempt_is_retained_and_completed_usage_is_not_reset() -> None:
    events = tail()
    steps = restore_model_attempts((), events, next_node=ResearchNode.RESEARCH_LOOP, revision=7)
    assert len(steps) == 1
    assert steps[0].status is AgentStepStatus.RUNNING
    settled = replace(
        events[-1],
        sequence=13,
        event_type=AgentEventType.STEP_COMPLETED,
        occurred_at=events[-1].occurred_at + timedelta(seconds=1),
        payload={
            "step_id": str(steps[0].step_id),
            "input_tokens": 10,
            "output_tokens": 2,
            "cost_micro_usd": 3,
        },
    )
    restored = restore_model_attempts(
        (), (*events, settled), next_node=ResearchNode.RESEARCH_LOOP, revision=7
    )
    assert restored[0].status is AgentStepStatus.COMPLETED
    assert restored[0].input_tokens == 10
    assert restored[0].cost_micro_usd == 3


@pytest.mark.parametrize(
    "event_type",
    [
        AgentEventType.TOOL_REQUESTED,
        AgentEventType.TOOL_STARTED,
        AgentEventType.TOOL_COMPLETED,
        AgentEventType.ARTIFACT_CREATED,
        AgentEventType.RUN_FAILED,
        AgentEventType.APPROVAL_DECIDED,
    ],
)
def test_unknown_effects_and_terminal_history_are_not_replayed(event_type: AgentEventType) -> None:
    events = tail()
    with pytest.raises(ValueError, match="durable effect"):
        restore_model_attempts(
            (),
            (*events, replace(events[-1], event_type=event_type)),
            next_node=ResearchNode.RESEARCH_LOOP,
            revision=7,
        )


def test_model_step_identity_and_graph_node_must_match() -> None:
    events = tail()
    with pytest.raises(ValueError, match="another graph"):
        restore_model_attempts((), events, next_node=ResearchNode.DRAFT, revision=7)
    with pytest.raises(ValueError, match="running Step"):
        restore_model_attempts(
            (),
            (*events[:-1], replace(events[-1], payload={"step_id": str(uuid4())})),
            next_node=ResearchNode.RESEARCH_LOOP,
            revision=7,
        )


@pytest.mark.parametrize(
    "invalid", ["tool", "overlap", "sequence", "orphan", "settlement", "approval"]
)
def test_recovery_rejects_inconsistent_attempt_history(invalid: str) -> None:
    events = tail()
    if invalid == "tool":
        changed = (
            *events[:1],
            replace(events[1], payload={**events[1].payload, "step_kind": "tool"}),
        )
    elif invalid == "overlap":
        changed = (*events, events[1])
    elif invalid == "sequence":
        changed = (
            *events[:1],
            replace(events[1], payload={**events[1].payload, "step_sequence": 2}),
        )
    elif invalid == "orphan":
        changed = (events[-1],)
    elif invalid == "settlement":
        changed = (
            *events,
            replace(
                events[-1],
                event_type=AgentEventType.STEP_COMPLETED,
                payload={"step_id": str(uuid4())},
            ),
        )
    else:
        changed = (
            replace(
                events[0],
                event_type=AgentEventType.RUN_RESUMED,
                payload={"resume_kind": "approval"},
            ),
        )
    with pytest.raises(ValueError, match=r"Tool|attempts|sequence|Model|approval"):
        restore_model_attempts((), changed, next_node=ResearchNode.RESEARCH_LOOP, revision=7)


def test_repeated_recovery_retains_failed_attempt_and_rejects_identity_reuse() -> None:
    events = tail()
    failed = replace(
        events[-1],
        sequence=13,
        event_type=AgentEventType.STEP_FAILED,
        payload={"step_id": events[1].payload["step_id"], "error_code": "worker_interrupted"},
    )
    resumed = replace(
        events[0],
        sequence=14,
        event_type=AgentEventType.RUN_RESUMED,
        payload={"resume_kind": "recovery", "state_revision": 11},
    )
    restored = restore_model_attempts(
        (), (*events, failed, resumed), next_node=ResearchNode.RESEARCH_LOOP, revision=7
    )
    assert restored[0].status is AgentStepStatus.FAILED
    reused = replace(events[1], sequence=15, payload={**events[1].payload, "step_sequence": 2})
    with pytest.raises(ValueError, match="reused"):
        restore_model_attempts(
            (), (*events, failed, resumed, reused), next_node=ResearchNode.RESEARCH_LOOP, revision=7
        )
