"""Recover auditable model attempts after a graph checkpoint without replaying writes."""

from dataclasses import replace
from uuid import UUID

from industry_platform.modules.agent_runtime.domain import AgentStep, AgentStepKind, AgentStepStatus
from industry_platform.modules.agent_runtime.events import AgentEvent, AgentEventType
from industry_platform.modules.research.domain import ResearchNode


def restore_model_attempts(
    steps: tuple[AgentStep, ...],
    tail: tuple[AgentEvent, ...],
    *,
    next_node: ResearchNode | None,
    revision: int,
) -> tuple[AgentStep, ...]:
    """Retain attempted work and usage; unknown Tool effects must fail closed.

    A model attempt can be retried with the next bounded model slot. A Tool request
    cannot be assumed safe to repeat merely because its node has no checkpoint.
    """
    restored = list(steps)
    for event in tail:
        payload = event.payload
        if event.event_type is AgentEventType.RESEARCH_NODE_STARTED:
            if next_node is None or payload.get("node") != next_node.value:
                raise ValueError("Recovery tail belongs to another graph node")
            revision = max(revision, int(str(payload["state_revision"])))
        elif event.event_type is AgentEventType.RUN_RESUMED:
            if payload.get("resume_kind") != "recovery":
                raise ValueError("Unexpected approval in recovery tail")
            revision = max(revision, int(str(payload["state_revision"])))
        elif event.event_type is AgentEventType.STEP_STARTED:
            if payload.get("step_kind") != AgentStepKind.MODEL.value:
                raise ValueError("Uncheckpointed Tool execution requires effect reconciliation")
            if restored and restored[-1].status is AgentStepStatus.RUNNING:
                raise ValueError("Overlapping interrupted model attempts")
            sequence = int(str(payload["step_sequence"]))
            if sequence != len(restored) + 1:
                raise ValueError("Recovery Step sequence is not contiguous")
            revision += 1
            if any(str(step.step_id) == payload.get("step_id") for step in restored):
                raise ValueError("Recovery Step identity was reused")
            restored.append(
                AgentStep(
                    schema_version=event.schema_version,
                    step_id=UUID(str(payload["step_id"])),
                    run_id=event.run_id,
                    workspace_id=event.workspace_id,
                    sequence=sequence,
                    kind=AgentStepKind.MODEL,
                    status=AgentStepStatus.RUNNING,
                    state_revision=revision,
                    started_at=event.occurred_at,
                )
            )
        elif event.event_type in {AgentEventType.MODEL_STARTED, AgentEventType.MODEL_COMPLETED}:
            if (
                not restored
                or restored[-1].status is not AgentStepStatus.RUNNING
                or str(restored[-1].step_id) != payload.get("step_id")
            ):
                raise ValueError("Model event without its running Step")
        elif event.event_type in {AgentEventType.STEP_COMPLETED, AgentEventType.STEP_FAILED}:
            if (
                not restored
                or restored[-1].status is not AgentStepStatus.RUNNING
                or str(restored[-1].step_id) != payload.get("step_id")
            ):
                raise ValueError("Model settlement does not match its Step")
            revision += 1
            failed = event.event_type is AgentEventType.STEP_FAILED
            restored[-1] = replace(
                restored[-1],
                status=AgentStepStatus.FAILED if failed else AgentStepStatus.COMPLETED,
                state_revision=revision,
                completed_at=event.occurred_at,
                latency_ms=max(
                    0, int((event.occurred_at - restored[-1].started_at).total_seconds() * 1000)
                ),
                input_tokens=int(str(payload.get("input_tokens", 0))),
                output_tokens=int(str(payload.get("output_tokens", 0))),
                cost_micro_usd=int(str(payload.get("cost_micro_usd", 0))),
                error_code=str(payload["error_code"]) if failed else None,
            )
        else:
            raise ValueError("Recovery tail requires a durable effect checkpoint")
    return tuple(restored)
