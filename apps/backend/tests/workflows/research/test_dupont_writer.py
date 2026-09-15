"""The Research task writer fails closed; graph port failures remain auditable."""

from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from industry_platform.modules.agent_runtime.domain import RunBudget, RunStopReason
from industry_platform.modules.agent_runtime.events import AgentEventType
from industry_platform.modules.agent_runtime.tool_runtime_contracts import ToolLoopFinalDecision
from industry_platform.workflows.research.contracts import (
    ResearchExecutionState,
    initial_graph_state,
)
from industry_platform.workflows.research.runtime import _ResearchExecution

from .test_runtime import (
    NOW,
    QueueModelProvider,
    RecordingEvidenceService,
    RecordingWorkflowStore,
    build_runtime,
    research_command,
    runtime_context,
    sec_financial_scope,
)


def _budget() -> RunBudget:
    return RunBudget(1, 20, 5_000, 10_000, NOW + timedelta(minutes=10))


@pytest.mark.asyncio
async def test_plan_persistence_failure_has_one_auditable_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(RecordingWorkflowStore, "save_plan", AsyncMock(side_effect=ValueError))
    provider = QueueModelProvider(())
    store = RecordingWorkflowStore()
    runtime, tool, committer = build_runtime(provider, store, RecordingEvidenceService())
    budget = _budget()
    events = [e async for e in runtime.run(research_command(budget), runtime_context(budget))]
    assert events == committer.events
    assert events[-1].event_type is AgentEventType.RUN_FAILED
    assert events[-1].payload["stop_reason"] == RunStopReason.RUNTIME_ERROR.value
    assert sum(e.event_type is AgentEventType.RESEARCH_NODE_FAILED for e in events) == 1
    assert sum(e.event_type is AgentEventType.RUN_FAILED for e in events) == 1
    assert provider.requests == []
    assert tool.invocations == []
    assert store.drafts == []


@pytest.mark.parametrize("has_scope", [False, True])
def test_dupont_writer_cannot_publish_model_numbers_without_calculation_evidence(
    has_scope: bool,
) -> None:
    budget = _budget()
    command = research_command(budget)
    runtime, _, _ = build_runtime(
        QueueModelProvider(()), RecordingWorkflowStore(), RecordingEvidenceService()
    )
    command = replace(
        command,
        brief=replace(
            command.brief,
            input=replace(
                command.brief.input,
                financial_scope=sec_financial_scope() if has_scope else None,
            ),
        ),
    )
    graph_state = ResearchExecutionState(initial_graph_state(command))
    assert runtime._financial_research_workflow is not None
    execution = _ResearchExecution(
        runtime=runtime._financial_research_workflow,
        command=command,
        runtime_context=runtime_context(budget),
        run=replace(command.run, harness_version="skill:sec.dupont-analysis:v1"),
        state=command.state,
        events=[],
        steps=[],
        graph_state=graph_state,
        scope=runtime_context(budget).workspace_scope,
        checkpoint_revision=None,
    )
    decision = ToolLoopFinalDecision(schema_version=1, content_markdown="ROE = 999%")
    if not has_scope:
        with pytest.raises(ValueError, match="financial scope"):
            execution._render_task_decision(decision)
    else:
        rendered = execution._render_task_decision(decision)
        assert "999" not in rendered.content_markdown
        assert "尚未完成" in rendered.content_markdown
        assert graph_state.final_decision == rendered
