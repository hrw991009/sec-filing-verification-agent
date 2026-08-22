"""Typed command and state passed from Unified Runtime to the Research graph."""

from dataclasses import dataclass, field
from typing import TypedDict, cast
from uuid import UUID

from industry_platform.modules.agent_runtime.context import ToolObservationContextSource
from industry_platform.modules.agent_runtime.domain import (
    AgentRun,
    AgentRunStatus,
    AgentRunType,
    require_non_nil_uuid,
)
from industry_platform.modules.agent_runtime.model import ModelResponse
from industry_platform.modules.agent_runtime.state import RunState, validate_run_state
from industry_platform.modules.agent_runtime.tool_runtime_contracts import (
    ToolL2RunCommand,
    ToolLoopFinalDecision,
)
from industry_platform.modules.research.domain import (
    ResearchBrief,
    initial_research_state_document,
)


class ResearchGraphState(TypedDict):
    schema_version: int
    graph_version: str
    research_run_id: str
    run_id: str
    workspace_id: str
    brief_revision: int
    plan_id: str | None
    current_node: str | None
    pending_actions: list[int]
    evidence_refs: list[str]
    claim_refs: list[str]
    artifact_refs: list[str]
    status: str
    step_count: int
    input_tokens_used: int
    output_tokens_used: int
    cost_micro_usd: int
    revise_count: int
    approval_status: str
    cancel_requested: bool
    stop_reason: str | None
    error_summary: str | None


@dataclass(frozen=True, slots=True)
class ResearchL3RunCommand:
    run: AgentRun
    state: RunState
    research_run_id: UUID
    brief: ResearchBrief
    loop_command: ToolL2RunCommand
    plan_id: UUID
    draft_id: UUID

    def __post_init__(self) -> None:
        validate_run_state(self.run, self.state)
        if (
            self.run.run_type is not AgentRunType.RESEARCH
            or self.run.status is not AgentRunStatus.QUEUED
            or self.state.status is not AgentRunStatus.QUEUED
            or self.state.revision != 0
            or self.state.step_count != 0
            or self.state.event_count != 1
            or not self.loop_command.embedded_in_research
            or self.loop_command.run != self.run
            or self.loop_command.state != self.state
        ):
            raise ValueError("Research L3 Runtime requires one fresh queued Research Run")
        if self.brief.research_run_id != self.research_run_id:
            raise ValueError("Research Brief belongs to another Research Run")
        if self.brief.workspace_id != self.run.workspace_id:
            raise ValueError("Research Brief belongs to another Workspace")
        identifiers = (
            self.research_run_id,
            self.plan_id,
            self.draft_id,
        )
        for value in identifiers:
            require_non_nil_uuid(value, field_name="Research execution ID")
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Research execution IDs must be unique")


@dataclass(slots=True)
class ResearchExecutionState:
    graph: ResearchGraphState
    observations: list[ToolObservationContextSource] = field(default_factory=list)
    final_decision: ToolLoopFinalDecision | None = None
    final_response: ModelResponse | None = None
    final_markdown: str | None = field(default=None, repr=False)
    outline: tuple[str, ...] = ()
    terminated: bool = False


def initial_graph_state(command: ResearchL3RunCommand) -> ResearchGraphState:
    return cast(
        ResearchGraphState,
        initial_research_state_document(
            research_run_id=command.research_run_id,
            agent_run_id=command.run.run_id,
            workspace_id=command.run.workspace_id,
            brief_revision=command.brief.revision,
        ),
    )
