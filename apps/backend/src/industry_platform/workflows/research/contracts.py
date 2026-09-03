"""Typed command and state passed from Unified Runtime to the Research graph."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypedDict, cast
from uuid import UUID

from industry_platform.modules.agent_runtime.context import ToolObservationContextSource
from industry_platform.modules.agent_runtime.domain import (
    AgentRun,
    AgentRunStatus,
    AgentRunType,
    AgentStep,
    require_non_nil_uuid,
)
from industry_platform.modules.agent_runtime.events import AgentEvent
from industry_platform.modules.agent_runtime.model import ModelResponse
from industry_platform.modules.agent_runtime.state import RunState, validate_run_state
from industry_platform.modules.agent_runtime.tool_runtime import _PendingToolApproval
from industry_platform.modules.agent_runtime.tool_runtime_contracts import (
    ToolL2RunCommand,
    ToolLoopFinalDecision,
)
from industry_platform.modules.research.domain import (
    ResearchBrief,
    ResearchNode,
    initial_research_state_document,
)
from industry_platform.modules.tools.domain import ToolAction


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
    verification_report_id: str | None
    verification_revision: int
    verification_status: str | None
    verification_issue_digest: str | None
    verification_action: str | None
    verification_action_digest: str | None
    verification_observation_digest: str | None
    approval_status: str
    approval_reason: str | None
    cancel_requested: bool
    stop_reason: str | None
    error_summary: str | None


class ResearchResumeKind(StrEnum):
    APPROVAL = "approval"
    RECOVERY = "recovery"


@dataclass(frozen=True, slots=True)
class ResearchResumeSnapshot:
    kind: ResearchResumeKind
    checkpoint_revision: int
    next_node: ResearchNode | None
    graph: ResearchGraphState
    event_history: tuple[AgentEvent, ...]
    steps: tuple[AgentStep, ...] = ()
    observations: tuple[ToolObservationContextSource, ...] = ()
    final_decision: ToolLoopFinalDecision | None = None
    final_response: ModelResponse | None = None
    final_markdown: str | None = field(default=None, repr=False)
    outline: tuple[str, ...] = ()
    approved_tool_action: ToolAction | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if isinstance(self.checkpoint_revision, bool) or self.checkpoint_revision < 0:
            raise ValueError("Research resume Checkpoint revision is invalid")
        if not self.event_history:
            raise ValueError("Research resume requires committed Event history")
        if self.approved_tool_action is not None:
            if not self.observations:
                raise ValueError("Approved Tool resume requires its Observation")
            approved_observation = self.observations[-1]
            if (
                approved_observation.tool_name != self.approved_tool_action.name
                or approved_observation.tool_version != self.approved_tool_action.version
            ):
                raise ValueError("Approved Tool resume Observation is inconsistent")


@dataclass(frozen=True, slots=True)
class ResearchL3RunCommand:
    run: AgentRun
    state: RunState
    research_run_id: UUID
    brief: ResearchBrief
    loop_command: ToolL2RunCommand
    plan_id: UUID
    draft_id: UUID
    resume: ResearchResumeSnapshot | None = None

    def __post_init__(self) -> None:
        validate_run_state(self.run, self.state)
        fresh = self.resume is None
        if (
            self.run.run_type is not AgentRunType.RESEARCH
            or not self.loop_command.embedded_in_research
            or self.loop_command.run != self.run
            or self.loop_command.state != self.state
            or (
                fresh
                and (
                    self.run.status is not AgentRunStatus.QUEUED
                    or self.state.status is not AgentRunStatus.QUEUED
                    or self.state.revision != 0
                    or self.state.step_count != 0
                    or self.state.event_count != 1
                )
            )
            or (not fresh and not self._valid_resume())
        ):
            raise ValueError("Research Runtime requires a fresh or resumable Research Run")
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

    def _valid_resume(self) -> bool:
        if self.resume is None:
            return False
        expected_status = {
            ResearchResumeKind.APPROVAL: AgentRunStatus.PAUSED,
            ResearchResumeKind.RECOVERY: AgentRunStatus.RUNNING,
        }[self.resume.kind]
        tail_type = self.resume.event_history[-1].event_type.value
        expected_tail = {
            ResearchResumeKind.APPROVAL: "agent.approval.decided",
            ResearchResumeKind.RECOVERY: "agent.checkpoint.saved",
        }[self.resume.kind]
        return (
            self.run.status is expected_status
            and self.state.status is expected_status
            and tail_type == expected_tail
            and self.resume.event_history[-1].sequence == self.state.event_count
        )


@dataclass(slots=True)
class ResearchExecutionState:
    graph: ResearchGraphState
    observations: list[ToolObservationContextSource] = field(default_factory=list)
    final_decision: ToolLoopFinalDecision | None = None
    final_response: ModelResponse | None = None
    final_markdown: str | None = field(default=None, repr=False)
    outline: tuple[str, ...] = ()
    pending_tool_approval: _PendingToolApproval | None = field(default=None, repr=False)
    terminated: bool = False


def initial_graph_state(command: ResearchL3RunCommand) -> ResearchGraphState:
    if command.resume is not None:
        return cast(ResearchGraphState, dict(command.resume.graph))
    return cast(
        ResearchGraphState,
        initial_research_state_document(
            research_run_id=command.research_run_id,
            agent_run_id=command.run.run_id,
            workspace_id=command.run.workspace_id,
            brief_revision=command.brief.revision,
            approval_reason=command.brief.input.approval_reason,
        ),
    )
