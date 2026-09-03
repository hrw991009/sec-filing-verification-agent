"""Versioned domain contracts shared by Evidence Research L3 and durable L4."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final
from uuid import UUID, uuid5

from industry_platform.modules.agent_runtime.domain import (
    AgentRun,
    AgentRunStatus,
    AgentRunType,
    RunBudget,
    RunStopReason,
    require_non_nil_uuid,
    require_utc,
)
from industry_platform.modules.financial_verification.domain import FinancialScope
from industry_platform.modules.identity.domain import TraceId

RESEARCH_GRAPH_VERSION: Final = "research-l5-graph-v1"
RESEARCH_STATE_SCHEMA_VERSION: Final = 2
RESEARCH_GRAPH_SCHEMA_VERSIONS: Final = {
    "research-l4-graph-v1": 1,
    RESEARCH_GRAPH_VERSION: RESEARCH_STATE_SCHEMA_VERSION,
}
RESEARCH_VERIFICATION_STATUSES: Final = frozenset(
    {"verified", "partial", "conflict", "insufficient_evidence"}
)
RESEARCH_VERIFICATION_ACTIONS: Final = frozenset({"targeted_retrieve", "recalculate"})
RESEARCH_RUNTIME_VERSION: Final = "agent-runtime-v1"
RESEARCH_HARNESS_VERSION: Final = "harness-research-v1"
RESEARCH_TASK_NAME: Final = "agent.run.research"
RESEARCH_QUEUE_NAME: Final = "agents"
MAX_RESEARCH_LIST_ITEMS: Final = 16
MAX_RESEARCH_TEXT_LENGTH: Final = 4_000
MAX_RESEARCH_DRAFT_LENGTH: Final = 60_000
_RESEARCH_RUN_NAMESPACE: Final = UUID("b8990b32-12c8-4692-b895-4a3626ae6a13")


def research_queued_event_payload(run: AgentRun) -> dict[str, object]:
    """Build the stable Research identity committed before a Worker owns the Run."""

    if run.run_type is not AgentRunType.RESEARCH:
        raise ValueError("Research queued Event requires a Research Run")
    return {
        "run_type": run.run_type.value,
        "runtime_version": run.runtime_version,
        "harness_version": run.harness_version,
        "graph_version": RESEARCH_GRAPH_VERSION,
    }


class ResearchRunStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ResearchNode(StrEnum):
    CLARIFY_SCOPE = "clarify_scope"
    WRITE_RESEARCH_BRIEF = "write_research_brief"
    PLAN = "plan"
    RESEARCH_LOOP = "research_loop"
    NORMALIZE_EVIDENCE = "normalize_evidence"
    SYNTHESIZE_CLAIMS = "synthesize_claims"
    OUTLINE = "outline"
    DRAFT = "draft"
    VERIFY = "verify"
    REVISE = "revise"
    FINALIZE = "finalize"


RESEARCH_NODE_ORDER: Final = tuple(node for node in ResearchNode if node is not ResearchNode.REVISE)


class ResearchApprovalReason(StrEnum):
    COMPANY_OR_PERIOD_AMBIGUITY = "company_or_period_ambiguity"
    MONITOR_SUBSCRIPTION = "monitor_subscription"


class ResearchApprovalStatus(StrEnum):
    PENDING = "pending"
    ALLOWED = "allowed"
    DENIED = "denied"
    TIMED_OUT = "timed_out"


class ResearchApprovalOutcome(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class ResearchSideEffectStatus(StrEnum):
    INTENT = "intent"
    COMPLETED = "completed"
    FAILED = "failed"


def research_run_id_for_agent_run(agent_run_id: UUID) -> UUID:
    require_non_nil_uuid(agent_run_id, field_name="Research Agent Run ID")
    return uuid5(_RESEARCH_RUN_NAMESPACE, f"research-run:{agent_run_id}")


def research_brief_id_for_run(research_run_id: UUID, revision: int = 1) -> UUID:
    require_non_nil_uuid(research_run_id, field_name="Research Run ID")
    if isinstance(revision, bool) or revision < 1:
        raise ValueError("Research Brief revision is invalid")
    return uuid5(research_run_id, f"research-brief:{revision}")


def research_draft_id_for_run(research_run_id: UUID, revision: int = 1) -> UUID:
    require_non_nil_uuid(research_run_id, field_name="Research Run ID")
    if isinstance(revision, bool) or revision < 1:
        raise ValueError("Research Draft revision is invalid")
    return uuid5(research_run_id, f"research-draft:{revision}")


def research_claim_id_for_run(research_run_id: UUID, revision: int = 1) -> UUID:
    require_non_nil_uuid(research_run_id, field_name="Research Run ID")
    if isinstance(revision, bool) or revision < 1:
        raise ValueError("Research Claim revision is invalid")
    return uuid5(research_run_id, f"research-claim:{revision}")


def initial_research_state_document(
    *,
    research_run_id: UUID,
    agent_run_id: UUID,
    workspace_id: UUID,
    brief_revision: int = 1,
    approval_reason: ResearchApprovalReason | None = None,
) -> dict[str, object]:
    """Return the JSON-safe Research state created atomically with the accepted Run."""

    for value, name in (
        (research_run_id, "Research State aggregate ID"),
        (agent_run_id, "Research State Agent Run ID"),
        (workspace_id, "Research State Workspace ID"),
    ):
        require_non_nil_uuid(value, field_name=name)
    if isinstance(brief_revision, bool) or brief_revision < 1:
        raise ValueError("Research State Brief revision is invalid")
    return {
        "schema_version": RESEARCH_STATE_SCHEMA_VERSION,
        "graph_version": RESEARCH_GRAPH_VERSION,
        "research_run_id": str(research_run_id),
        "run_id": str(agent_run_id),
        "workspace_id": str(workspace_id),
        "brief_revision": brief_revision,
        "plan_id": None,
        "current_node": None,
        "pending_actions": [],
        "evidence_refs": [],
        "claim_refs": [],
        "artifact_refs": [],
        "status": AgentRunStatus.QUEUED.value,
        "step_count": 0,
        "input_tokens_used": 0,
        "output_tokens_used": 0,
        "cost_micro_usd": 0,
        "revise_count": 0,
        "verification_report_id": None,
        "verification_revision": 0,
        "verification_status": None,
        "verification_issue_digest": None,
        "verification_action": None,
        "verification_action_digest": None,
        "verification_observation_digest": None,
        "approval_status": (
            ResearchApprovalStatus.PENDING.value if approval_reason is not None else "not_required"
        ),
        "approval_reason": None if approval_reason is None else approval_reason.value,
        "cancel_requested": False,
        "stop_reason": None,
        "error_summary": None,
    }


class ResearchDraftStatus(StrEnum):
    EXPLAINABLE_DRAFT = "explainable_draft"
    UNCERTAIN_DRAFT = "uncertain_draft"


@dataclass(frozen=True, slots=True)
class ResearchBriefInput:
    original_question: str = field(repr=False)
    confirmed_scope: tuple[str, ...]
    exclusions: tuple[str, ...]
    completion_criteria: tuple[str, ...]
    financial_scope: FinancialScope | None = None
    approval_reason: ResearchApprovalReason | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "original_question",
            _bounded_text(self.original_question, "Research original question"),
        )
        object.__setattr__(
            self,
            "confirmed_scope",
            _bounded_items(self.confirmed_scope, "Research confirmed scope", required=True),
        )
        object.__setattr__(
            self,
            "exclusions",
            _bounded_items(self.exclusions, "Research exclusions", required=False),
        )
        object.__setattr__(
            self,
            "completion_criteria",
            _bounded_items(
                self.completion_criteria,
                "Research completion criteria",
                required=True,
            ),
        )
        if self.approval_reason is not None:
            if self.approval_reason is not ResearchApprovalReason.COMPANY_OR_PERIOD_AMBIGUITY:
                raise ValueError("Research Brief approval reason is unsupported")
            if self.financial_scope is None:
                raise ValueError("Research approval reason requires a Financial Scope")


@dataclass(frozen=True, slots=True)
class ResearchBrief:
    brief_id: UUID
    research_run_id: UUID
    workspace_id: UUID
    revision: int
    input: ResearchBriefInput
    budget: RunBudget
    confirmed_by_user_id: UUID
    confirmed_at: datetime
    created_at: datetime

    def __post_init__(self) -> None:
        for value, name in (
            (self.brief_id, "Research Brief ID"),
            (self.research_run_id, "Research Run ID"),
            (self.workspace_id, "Research Brief Workspace ID"),
            (self.confirmed_by_user_id, "Research Brief confirmer ID"),
        ):
            require_non_nil_uuid(value, field_name=name)
        if isinstance(self.revision, bool) or self.revision < 1:
            raise ValueError("Research Brief revision is invalid")
        require_utc(self.confirmed_at, field_name="Research Brief confirmation time")
        require_utc(self.created_at, field_name="Research Brief creation time")
        if self.confirmed_at < self.created_at:
            raise ValueError("Research Brief confirmation cannot precede creation")


@dataclass(frozen=True, slots=True)
class ResearchPlanAction:
    ordinal: int
    objective: str
    allowed_tool_names: tuple[str, ...]

    def __post_init__(self) -> None:
        if isinstance(self.ordinal, bool) or not 1 <= self.ordinal <= MAX_RESEARCH_LIST_ITEMS:
            raise ValueError("Research Plan action ordinal is invalid")
        object.__setattr__(self, "objective", _bounded_text(self.objective, "Plan objective"))
        object.__setattr__(
            self,
            "allowed_tool_names",
            _bounded_items(self.allowed_tool_names, "Plan Tool names", required=True),
        )


@dataclass(frozen=True, slots=True)
class ResearchPlan:
    plan_id: UUID
    research_run_id: UUID
    workspace_id: UUID
    brief_revision: int
    revision: int
    actions: tuple[ResearchPlanAction, ...]
    planner_summary: str
    created_at: datetime

    def __post_init__(self) -> None:
        for value, name in (
            (self.plan_id, "Research Plan ID"),
            (self.research_run_id, "Research Run ID"),
            (self.workspace_id, "Research Plan Workspace ID"),
        ):
            require_non_nil_uuid(value, field_name=name)
        for numeric_value, name in (
            (self.brief_revision, "Research Brief revision"),
            (self.revision, "Research Plan revision"),
        ):
            if isinstance(numeric_value, bool) or numeric_value < 1:
                raise ValueError(f"{name} is invalid")
        actions = tuple(self.actions)
        if not actions or tuple(item.ordinal for item in actions) != tuple(
            range(1, len(actions) + 1)
        ):
            raise ValueError("Research Plan actions are invalid")
        object.__setattr__(self, "actions", actions)
        object.__setattr__(
            self,
            "planner_summary",
            _bounded_text(self.planner_summary, "Research planner summary"),
        )
        require_utc(self.created_at, field_name="Research Plan creation time")


@dataclass(frozen=True, slots=True)
class ResearchDraft:
    draft_id: UUID
    research_run_id: UUID
    workspace_id: UUID
    plan_id: UUID
    status: ResearchDraftStatus
    content_markdown: str = field(repr=False)
    outline: tuple[str, ...]
    evidence_refs: tuple[UUID, ...]
    claim_refs: tuple[UUID, ...]
    uncertainty_summary: str | None
    created_at: datetime
    updated_at: datetime
    revision: int = 1

    def __post_init__(self) -> None:
        for value, name in (
            (self.draft_id, "Research Draft ID"),
            (self.research_run_id, "Research Run ID"),
            (self.workspace_id, "Research Draft Workspace ID"),
            (self.plan_id, "Research Draft Plan ID"),
        ):
            require_non_nil_uuid(value, field_name=name)
        if isinstance(self.revision, bool) or self.revision < 1:
            raise ValueError("Research Draft revision is invalid")
        markdown = self.content_markdown.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not markdown or len(markdown) > MAX_RESEARCH_DRAFT_LENGTH or "\x00" in markdown:
            raise ValueError("Research Draft Markdown is invalid")
        object.__setattr__(self, "content_markdown", markdown)
        object.__setattr__(self, "outline", _bounded_items(self.outline, "Draft outline", True))
        object.__setattr__(self, "evidence_refs", _unique_ids(self.evidence_refs, "Evidence refs"))
        object.__setattr__(self, "claim_refs", _unique_ids(self.claim_refs, "Claim refs"))
        if self.uncertainty_summary is not None:
            object.__setattr__(
                self,
                "uncertainty_summary",
                _bounded_text(self.uncertainty_summary, "Draft uncertainty summary"),
            )
        require_utc(self.created_at, field_name="Research Draft creation time")
        require_utc(self.updated_at, field_name="Research Draft update time")
        if self.updated_at < self.created_at:
            raise ValueError("Research Draft update cannot precede creation")


@dataclass(frozen=True, slots=True)
class ResearchState:
    schema_version: int
    graph_version: str
    research_run_id: UUID
    run_id: UUID
    workspace_id: UUID
    brief_revision: int
    plan_id: UUID | None
    current_node: ResearchNode | None
    pending_actions: tuple[int, ...]
    evidence_refs: tuple[UUID, ...]
    claim_refs: tuple[UUID, ...]
    artifact_refs: tuple[UUID, ...]
    approval_status: str
    step_count: int
    input_tokens_used: int
    output_tokens_used: int
    cost_micro_usd: int
    revise_count: int
    verification_report_id: UUID | None
    verification_revision: int
    verification_status: str | None
    verification_issue_digest: str | None
    verification_action: str | None
    verification_action_digest: str | None
    verification_observation_digest: str | None
    cancel_requested: bool
    status: AgentRunStatus
    stop_reason: RunStopReason | None
    error_summary: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.schema_version != RESEARCH_STATE_SCHEMA_VERSION:
            raise ValueError("Research State schema version is unsupported")
        if self.graph_version != RESEARCH_GRAPH_VERSION:
            raise ValueError("Research graph version is unsupported")
        for value, name in (
            (self.research_run_id, "Research State aggregate ID"),
            (self.run_id, "Research State Agent Run ID"),
            (self.workspace_id, "Research State Workspace ID"),
        ):
            require_non_nil_uuid(value, field_name=name)
        if self.plan_id is not None:
            require_non_nil_uuid(self.plan_id, field_name="Research State Plan ID")
        if isinstance(self.brief_revision, bool) or self.brief_revision < 1:
            raise ValueError("Research State Brief revision is invalid")
        if any(isinstance(value, bool) or value < 1 for value in self.pending_actions):
            raise ValueError("Research State pending actions are invalid")
        if self.approval_status not in {"not_required", "required"}:
            raise ValueError("Research State approval status is invalid")
        for numeric_value, name in (
            (self.step_count, "Research State step count"),
            (self.input_tokens_used, "Research State input tokens"),
            (self.output_tokens_used, "Research State output tokens"),
            (self.cost_micro_usd, "Research State cost"),
            (self.revise_count, "Research State revise count"),
        ):
            if isinstance(numeric_value, bool) or numeric_value < 0:
                raise ValueError(f"{name} is invalid")
        if self.revise_count > 1:
            raise ValueError("Research L5 revise count exceeds the bounded limit")
        if self.verification_report_id is not None:
            require_non_nil_uuid(
                self.verification_report_id,
                field_name="Research Verification report ID",
            )
        if (
            isinstance(self.verification_revision, bool)
            or self.verification_revision < 0
            or self.verification_revision > 2
        ):
            raise ValueError("Research Verification revision is invalid")
        if (
            self.verification_status is not None
            and self.verification_status not in RESEARCH_VERIFICATION_STATUSES
        ):
            raise ValueError("Research Verification status is invalid")
        if (
            self.verification_action is not None
            and self.verification_action not in RESEARCH_VERIFICATION_ACTIONS
        ):
            raise ValueError("Research Verification action is invalid")
        for digest, field_name in (
            (self.verification_issue_digest, "Research Verification issue digest"),
            (self.verification_action_digest, "Research Verification action digest"),
            (self.verification_observation_digest, "Research Verification observation digest"),
        ):
            if digest is not None and (
                len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise ValueError(f"{field_name} is invalid")
        if self.verification_revision == 0:
            if any(
                value is not None
                for value in (
                    self.verification_report_id,
                    self.verification_status,
                    self.verification_issue_digest,
                    self.verification_action,
                    self.verification_action_digest,
                    self.verification_observation_digest,
                )
            ):
                raise ValueError("Unverified Research State contains Verification data")
        elif (
            self.verification_report_id is None
            or self.verification_status is None
            or self.verification_issue_digest is None
        ):
            raise ValueError("Verified Research State is incomplete")
        if (self.verification_action is None) != (self.verification_action_digest is None):
            raise ValueError("Research Verification action digest is inconsistent")
        if self.verification_action is not None and self.revise_count != 0:
            raise ValueError("Consumed Research Verification action is still pending")
        if self.verification_observation_digest is not None and self.revise_count != 1:
            raise ValueError("Research Verification observation has no revise")
        object.__setattr__(self, "evidence_refs", _unique_ids(self.evidence_refs, "Evidence refs"))
        object.__setattr__(self, "claim_refs", _unique_ids(self.claim_refs, "Claim refs"))
        object.__setattr__(self, "artifact_refs", _unique_ids(self.artifact_refs, "Artifact refs"))
        if self.error_summary is not None:
            object.__setattr__(
                self,
                "error_summary",
                _bounded_text(self.error_summary, "Research error summary", maximum=500),
            )


@dataclass(frozen=True, slots=True)
class CreateResearchRun:
    """Legacy ownership command retained for ledger-level callers."""

    agent_run_id: UUID
    trace_id: TraceId

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.agent_run_id, field_name="Research Agent Run ID")


@dataclass(frozen=True, slots=True)
class ResearchRun:
    research_run_id: UUID
    workspace_id: UUID
    owner_user_id: UUID
    agent_run_id: UUID
    status: ResearchRunStatus
    revision: int
    graph_version: str = RESEARCH_GRAPH_VERSION
    state_schema_version: int = RESEARCH_STATE_SCHEMA_VERSION
    current_node: ResearchNode | None = None
    created_at: datetime = field(kw_only=True)
    updated_at: datetime = field(kw_only=True)

    def __post_init__(self) -> None:
        for identifier, field_name in (
            (self.research_run_id, "Research Run ID"),
            (self.workspace_id, "Research Workspace ID"),
            (self.owner_user_id, "Research owner ID"),
            (self.agent_run_id, "Research Agent Run ID"),
        ):
            require_non_nil_uuid(identifier, field_name=field_name)
        if isinstance(self.revision, bool) or self.revision < 1:
            raise ValueError("Research Run revision is invalid")
        expected_schema = RESEARCH_GRAPH_SCHEMA_VERSIONS.get(self.graph_version)
        if expected_schema is None:
            raise ValueError("Research Run graph version is unsupported")
        if self.state_schema_version != expected_schema:
            raise ValueError("Research Run state schema version is unsupported")
        require_utc(self.created_at, field_name="Research Run creation time")
        require_utc(self.updated_at, field_name="Research Run update time")


@dataclass(frozen=True, slots=True)
class ResearchStartReceipt:
    research_run_id: UUID
    agent_run_id: UUID
    conversation_id: UUID
    turn_id: UUID
    job_id: UUID
    created: bool

    def __post_init__(self) -> None:
        for value, name in (
            (self.research_run_id, "Research Run ID"),
            (self.agent_run_id, "Research Agent Run ID"),
            (self.conversation_id, "Research Conversation ID"),
            (self.turn_id, "Research Turn ID"),
            (self.job_id, "Research Job ID"),
        ):
            require_non_nil_uuid(value, field_name=name)


@dataclass(frozen=True, slots=True)
class ResearchRunView:
    research_run: ResearchRun
    brief: ResearchBrief
    plan: ResearchPlan | None
    draft: ResearchDraft | None
    agent_status: AgentRunStatus
    stop_reason: RunStopReason | None
    step_count: int
    event_count: int
    input_tokens_used: int
    output_tokens_used: int
    cost_micro_usd: int

    def __post_init__(self) -> None:
        if self.research_run.research_run_id != self.brief.research_run_id:
            raise ValueError("Research view Brief belongs to another Research Run")
        if self.plan is not None and self.plan.research_run_id != self.research_run.research_run_id:
            raise ValueError("Research view Plan belongs to another Research Run")
        if (
            self.draft is not None
            and self.draft.research_run_id != self.research_run.research_run_id
        ):
            raise ValueError("Research view Draft belongs to another Research Run")
        for value, name in (
            (self.step_count, "Research view step count"),
            (self.event_count, "Research view event count"),
            (self.input_tokens_used, "Research view input tokens"),
            (self.output_tokens_used, "Research view output tokens"),
            (self.cost_micro_usd, "Research view cost"),
        ):
            if isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} is invalid")


def _bounded_text(value: str, field_name: str, maximum: int = MAX_RESEARCH_TEXT_LENGTH) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized or len(normalized) > maximum or "\x00" in normalized:
        raise ValueError(f"{field_name} is invalid")
    return normalized


def _bounded_items(values: tuple[str, ...], field_name: str, required: bool) -> tuple[str, ...]:
    selected = tuple(_bounded_text(value, field_name, maximum=500) for value in values)
    if (required and not selected) or len(selected) > MAX_RESEARCH_LIST_ITEMS:
        raise ValueError(f"{field_name} is invalid")
    if len(set(selected)) != len(selected):
        raise ValueError(f"{field_name} must be unique")
    return selected


def _unique_ids(values: tuple[UUID, ...], field_name: str) -> tuple[UUID, ...]:
    selected = tuple(values)
    if len(selected) > 64 or len(set(selected)) != len(selected):
        raise ValueError(f"Research {field_name} are invalid")
    for value in selected:
        require_non_nil_uuid(value, field_name=f"Research {field_name}")
    return selected
