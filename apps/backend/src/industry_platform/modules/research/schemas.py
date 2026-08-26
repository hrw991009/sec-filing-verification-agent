"""Strict HTTP contracts for Research creation, inspection, and L4 recovery."""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from industry_platform.modules.agent_runtime.domain import AgentRunStatus, RunStopReason
from industry_platform.modules.financial_verification.schemas import FinancialScopePayload
from industry_platform.modules.research.domain import (
    ResearchApprovalOutcome,
    ResearchApprovalReason,
    ResearchApprovalStatus,
    ResearchDraftStatus,
    ResearchNode,
    ResearchRunStatus,
)


def _non_nil_uuid(value: UUID) -> UUID:
    if value.int == 0:
        raise ValueError("ID must not be nil")
    return value


type NonNilUuid = Annotated[UUID, AfterValidator(_non_nil_uuid)]


class StrictResearchModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StartResearchRequest(StrictResearchModel):
    original_question: str = Field(min_length=1, max_length=4_000)
    confirmed_scope: list[str] = Field(min_length=1, max_length=16)
    exclusions: list[str] = Field(default_factory=list, max_length=16)
    completion_criteria: list[str] = Field(min_length=1, max_length=16)
    mode: Literal["web", "local"] = "web"
    industry_id: NonNilUuid | None = None
    knowledge_base_ids: list[NonNilUuid] = Field(default_factory=list, max_length=100)
    financial_scope: FinancialScopePayload | None = None
    approval_reason: ResearchApprovalReason | None = None
    max_steps: int = Field(default=20, ge=12, le=64)
    max_total_tokens: int = Field(default=16_384, ge=1_024, le=100_000)
    max_cost_micro_usd: int = Field(default=500_000, ge=0, le=10_000_000)
    timeout_seconds: int = Field(default=600, ge=30, le=1_500)

    @field_validator(
        "original_question",
        "confirmed_scope",
        "exclusions",
        "completion_criteria",
    )
    @classmethod
    def validate_text(cls, value: str | list[str]) -> str | list[str]:
        values = [value] if isinstance(value, str) else value
        if any(not item.strip() or item != item.strip() or "\x00" in item for item in values):
            raise ValueError("Research text must be normalized")
        if not isinstance(value, str) and len(set(value)) != len(value):
            raise ValueError("Research list items must be unique")
        return value

    @model_validator(mode="after")
    def validate_source_scope(self) -> Self:
        if len(set(self.knowledge_base_ids)) != len(self.knowledge_base_ids):
            raise ValueError("Knowledge Base IDs must be unique")
        if self.mode == "web":
            if (
                self.industry_id is None
                or self.knowledge_base_ids
                or self.financial_scope
                or self.approval_reason
            ):
                raise ValueError("Web Research source scope is invalid")
        elif self.industry_id is not None or not self.knowledge_base_ids:
            raise ValueError("Local Research source scope is invalid")
        elif self.financial_scope is None:
            raise ValueError("Local Research Financial Scope is required")
        return self


class StartResearchResponse(StrictResearchModel):
    research_run_id: UUID
    agent_run_id: UUID
    conversation_id: UUID
    turn_id: UUID
    job_id: UUID
    created: bool


class ResearchBudgetResponse(StrictResearchModel):
    max_steps: int
    max_total_tokens: int
    max_cost_micro_usd: int
    deadline: datetime


class ResearchBriefResponse(StrictResearchModel):
    id: UUID
    revision: int
    original_question: str
    confirmed_scope: list[str]
    exclusions: list[str]
    completion_criteria: list[str]
    financial_scope: FinancialScopePayload | None
    approval_reason: ResearchApprovalReason | None
    budget: ResearchBudgetResponse
    confirmed_by_user_id: UUID
    confirmed_at: datetime


class ResearchPlanActionResponse(StrictResearchModel):
    ordinal: int
    objective: str
    allowed_tool_names: list[str]


class ResearchPlanResponse(StrictResearchModel):
    id: UUID
    brief_revision: int
    revision: int
    actions: list[ResearchPlanActionResponse]
    planner_summary: str
    created_at: datetime


class ResearchDraftResponse(StrictResearchModel):
    id: UUID
    status: ResearchDraftStatus
    content_markdown: str
    outline: list[str]
    evidence_refs: list[UUID]
    claim_refs: list[UUID]
    uncertainty_summary: str | None
    created_at: datetime
    updated_at: datetime


class ResearchRunDetailResponse(StrictResearchModel):
    id: UUID
    workspace_id: UUID
    owner_user_id: UUID
    agent_run_id: UUID
    status: ResearchRunStatus
    revision: int
    graph_version: str
    state_schema_version: int
    current_node: ResearchNode | None
    agent_status: AgentRunStatus
    stop_reason: RunStopReason | None
    step_count: int
    event_count: int
    input_tokens_used: int
    output_tokens_used: int
    cost_micro_usd: int
    brief: ResearchBriefResponse
    plan: ResearchPlanResponse | None
    draft: ResearchDraftResponse | None
    created_at: datetime
    updated_at: datetime


class ResearchRunCollectionResponse(StrictResearchModel):
    research_runs: list[ResearchRunDetailResponse]


class ResearchCheckpointResponse(StrictResearchModel):
    checkpoint_id: UUID
    revision: int
    run_state_revision: int
    node: ResearchNode
    next_node: ResearchNode | None
    saved_at: datetime
    state_diff: dict[str, object]


class ResearchApprovalResponse(StrictResearchModel):
    approval_request_id: UUID
    run_id: UUID
    checkpoint_id: UUID
    checkpoint_revision: int
    reason: ResearchApprovalReason
    status: ResearchApprovalStatus
    requested_by_user_id: UUID
    created_at: datetime
    expires_at: datetime
    decided_by_user_id: UUID | None
    decided_at: datetime | None
    resume_claimed: bool
    resume_job_id: UUID | None
    resumed_at: datetime | None
    resume_token: str | None = Field(default=None, min_length=40, max_length=100)


class ResearchDurabilityTimelineResponse(StrictResearchModel):
    checkpoints: list[ResearchCheckpointResponse]
    approvals: list[ResearchApprovalResponse]
    duplicate_side_effect_count: int = Field(ge=0)


class DecideResearchApprovalRequest(StrictResearchModel):
    approval_request_id: NonNilUuid
    checkpoint_revision: int = Field(ge=0)
    outcome: ResearchApprovalOutcome


class ResumeResearchRequest(StrictResearchModel):
    approval_request_id: NonNilUuid
    checkpoint_revision: int = Field(ge=0)
    resume_token: str = Field(min_length=40, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class ResumeResearchResponse(StrictResearchModel):
    agent_run_id: UUID
    job_id: UUID
    created: bool
