"""HTTP acceptance contracts for Research creation, inspection, and L4 recovery."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import cast
from uuid import UUID

from fastapi.testclient import TestClient

from industry_platform.core.config import Settings
from industry_platform.main import create_app
from industry_platform.modules.agent_runtime.domain import (
    AgentRunStatus,
    RunBudget,
    RunStopReason,
)
from industry_platform.modules.financial_verification.domain import (
    FinancialForm,
    FinancialScope,
)
from industry_platform.modules.identity.domain import (
    AccessToken,
    AuthenticatedPrincipal,
    AuthenticatedWorkspace,
    NormalizedEmail,
)
from industry_platform.modules.identity.http_auth import get_principal_resolver
from industry_platform.modules.research.domain import (
    RESEARCH_GRAPH_VERSION,
    RESEARCH_STATE_SCHEMA_VERSION,
    ResearchApprovalOutcome,
    ResearchApprovalReason,
    ResearchApprovalStatus,
    ResearchBrief,
    ResearchBriefInput,
    ResearchDraft,
    ResearchDraftStatus,
    ResearchNode,
    ResearchPlan,
    ResearchPlanAction,
    ResearchRun,
    ResearchRunStatus,
    ResearchRunView,
    ResearchStartReceipt,
)
from industry_platform.modules.research.durability import (
    DecideResearchApproval,
    ResearchApprovalRequest,
    ResearchCheckpointSummary,
    ResearchDurabilityService,
    ResearchDurabilityTimeline,
    ResearchResumeReceipt,
    ResumeResearch,
)
from industry_platform.modules.research.router import (
    get_durability_service,
    get_query_service,
    get_submission_service,
)
from industry_platform.modules.research.service import (
    ResearchQueryService,
    ResearchSubmissionService,
    StartResearch,
)
from industry_platform.modules.workspaces.domain import WorkspaceScope

NOW = datetime(2026, 8, 21, 8, 0, tzinfo=UTC)
WORKSPACE_ID = UUID("30000000-0000-4000-8000-000000000001")
OTHER_WORKSPACE_ID = UUID("30000000-0000-4000-8000-000000000002")
USER_ID = UUID("30000000-0000-4000-8000-000000000003")
SESSION_ID = UUID("30000000-0000-4000-8000-000000000004")
INDUSTRY_ID = UUID("30000000-0000-4000-8000-000000000005")
RESEARCH_RUN_ID = UUID("30000000-0000-4000-8000-000000000006")
RUN_ID = UUID("30000000-0000-4000-8000-000000000007")
CONVERSATION_ID = UUID("30000000-0000-4000-8000-000000000008")
TURN_ID = UUID("30000000-0000-4000-8000-000000000009")
JOB_ID = UUID("30000000-0000-4000-8000-000000000010")
BRIEF_ID = UUID("30000000-0000-4000-8000-000000000011")
PLAN_ID = UUID("30000000-0000-4000-8000-000000000012")
DRAFT_ID = UUID("30000000-0000-4000-8000-000000000013")
EVIDENCE_ID = UUID("30000000-0000-4000-8000-000000000014")
CLAIM_ID = UUID("30000000-0000-4000-8000-000000000015")
KNOWLEDGE_BASE_ID = UUID("30000000-0000-4000-8000-000000000016")
CHECKPOINT_ID = UUID("30000000-0000-4000-8000-000000000017")
APPROVAL_ID = UUID("30000000-0000-4000-8000-000000000018")
RESUME_JOB_ID = UUID("30000000-0000-4000-8000-000000000019")
RAW_ACCESS_VALUE = ".".join(("header", "payload", "signature"))
TEST_RESUME_PROOF = "r" * 43


@dataclass(slots=True)
class StubPrincipalResolver:
    value: AuthenticatedPrincipal

    async def resolve(self, _token: AccessToken) -> AuthenticatedPrincipal:
        return self.value


@dataclass
class StubSubmissionService:
    calls: list[tuple[WorkspaceScope, StartResearch]] = field(default_factory=list)

    async def start(
        self,
        scope: WorkspaceScope,
        request: StartResearch,
    ) -> ResearchStartReceipt:
        self.calls.append((scope, request))
        return ResearchStartReceipt(
            research_run_id=RESEARCH_RUN_ID,
            agent_run_id=RUN_ID,
            conversation_id=CONVERSATION_ID,
            turn_id=TURN_ID,
            job_id=JOB_ID,
            created=True,
        )


@dataclass
class StubQueryService:
    calls: list[tuple[str, WorkspaceScope, object]] = field(default_factory=list)

    async def get(self, scope: WorkspaceScope, research_run_id: UUID) -> ResearchRunView:
        self.calls.append(("get", scope, research_run_id))
        return research_view()

    async def list(self, scope: WorkspaceScope, *, limit: int) -> tuple[ResearchRunView, ...]:
        self.calls.append(("list", scope, limit))
        return (research_view(),)


@dataclass
class StubDurabilityService:
    calls: list[tuple[str, WorkspaceScope, object]] = field(default_factory=list)

    async def timeline(
        self,
        scope: WorkspaceScope,
        research_run_id: UUID,
    ) -> ResearchDurabilityTimeline:
        self.calls.append(("timeline", scope, research_run_id))
        return ResearchDurabilityTimeline(
            checkpoints=(
                ResearchCheckpointSummary(
                    checkpoint_id=CHECKPOINT_ID,
                    revision=2,
                    run_state_revision=7,
                    node=ResearchNode.PLAN,
                    next_node=ResearchNode.RESEARCH_LOOP,
                    saved_at=NOW,
                    state_diff={"approval_status": "pending"},
                ),
            ),
            approvals=(approval(),),
            duplicate_side_effect_count=0,
        )

    async def decide(
        self,
        scope: WorkspaceScope,
        command: DecideResearchApproval,
    ) -> ResearchApprovalRequest:
        self.calls.append(("decide", scope, command))
        return approval(status=ResearchApprovalStatus.ALLOWED, decided=True)

    async def resume(
        self,
        scope: WorkspaceScope,
        command: ResumeResearch,
    ) -> ResearchResumeReceipt:
        self.calls.append(("resume", scope, command))
        return ResearchResumeReceipt(run_id=RUN_ID, job_id=RESUME_JOB_ID, created=True)

    def token_for(self, _approval: ResearchApprovalRequest) -> str:
        return TEST_RESUME_PROOF


def approval(
    *,
    status: ResearchApprovalStatus = ResearchApprovalStatus.PENDING,
    decided: bool = False,
) -> ResearchApprovalRequest:
    return ResearchApprovalRequest(
        approval_request_id=APPROVAL_ID,
        run_id=RUN_ID,
        checkpoint_id=CHECKPOINT_ID,
        checkpoint_revision=2,
        reason=ResearchApprovalReason.COMPANY_OR_PERIOD_AMBIGUITY,
        status=status,
        requested_by_user_id=USER_ID,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
        decided_by_user_id=USER_ID if decided else None,
        decided_at=NOW + timedelta(minutes=1) if decided else None,
    )


def principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        user_id=USER_ID,
        session_id=SESSION_ID,
        email=NormalizedEmail("research-member@example.test"),
        workspaces=(AuthenticatedWorkspace(WORKSPACE_ID, "Workspace", "member"),),
    )


def research_view() -> ResearchRunView:
    budget = RunBudget(
        schema_version=1,
        max_steps=20,
        max_total_tokens=12_000,
        max_cost_micro_usd=300_000,
        deadline=NOW + timedelta(minutes=10),
    )
    return ResearchRunView(
        research_run=ResearchRun(
            research_run_id=RESEARCH_RUN_ID,
            workspace_id=WORKSPACE_ID,
            owner_user_id=USER_ID,
            agent_run_id=RUN_ID,
            status=ResearchRunStatus.COMPLETED,
            revision=30,
            graph_version=RESEARCH_GRAPH_VERSION,
            state_schema_version=RESEARCH_STATE_SCHEMA_VERSION,
            current_node=ResearchNode.DRAFT,
            created_at=NOW,
            updated_at=NOW + timedelta(seconds=3),
        ),
        brief=ResearchBrief(
            brief_id=BRIEF_ID,
            research_run_id=RESEARCH_RUN_ID,
            workspace_id=WORKSPACE_ID,
            revision=1,
            input=ResearchBriefInput(
                original_question="Compare steel and copper changes.",
                confirmed_scope=("Public market sources",),
                exclusions=("Investment advice",),
                completion_criteria=("Produce an attributable L3 draft",),
            ),
            budget=budget,
            confirmed_by_user_id=USER_ID,
            confirmed_at=NOW,
            created_at=NOW,
        ),
        plan=ResearchPlan(
            plan_id=PLAN_ID,
            research_run_id=RESEARCH_RUN_ID,
            workspace_id=WORKSPACE_ID,
            brief_revision=1,
            revision=1,
            actions=(
                ResearchPlanAction(
                    ordinal=1,
                    objective="Use the public market lookup Tool",
                    allowed_tool_names=("fake.industry_lookup",),
                ),
            ),
            planner_summary="Preserve the confirmed scope.",
            created_at=NOW,
        ),
        draft=ResearchDraft(
            draft_id=DRAFT_ID,
            research_run_id=RESEARCH_RUN_ID,
            workspace_id=WORKSPACE_ID,
            plan_id=PLAN_ID,
            status=ResearchDraftStatus.EXPLAINABLE_DRAFT,
            content_markdown="# L3 draft\n\nAttributable finding.",
            outline=("Question", "Finding", "Limitations"),
            evidence_refs=(EVIDENCE_ID,),
            claim_refs=(CLAIM_ID,),
            uncertainty_summary=None,
            created_at=NOW + timedelta(seconds=2),
            updated_at=NOW + timedelta(seconds=2),
        ),
        agent_status=AgentRunStatus.COMPLETED,
        stop_reason=RunStopReason.FINAL,
        step_count=4,
        event_count=30,
        input_tokens_used=20,
        output_tokens_used=10,
        cost_micro_usd=40,
    )


@contextmanager
def research_client(
    settings: Settings,
    submission: StubSubmissionService,
    query: StubQueryService,
    durability: StubDurabilityService | None = None,
) -> Iterator[TestClient]:
    application = create_app(settings=settings)
    application.dependency_overrides[get_principal_resolver] = lambda: StubPrincipalResolver(
        principal()
    )
    application.dependency_overrides[get_submission_service] = lambda: cast(
        ResearchSubmissionService, submission
    )
    application.dependency_overrides[get_query_service] = lambda: cast(ResearchQueryService, query)
    if durability is not None:
        application.dependency_overrides[get_durability_service] = lambda: cast(
            ResearchDurabilityService, durability
        )
    with TestClient(application, base_url="https://localhost") as client:
        yield client


def headers(**additional: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {RAW_ACCESS_VALUE}", **additional}


def start_payload() -> dict[str, object]:
    return {
        "original_question": "Compare steel and copper changes.",
        "confirmed_scope": ["Public market sources"],
        "exclusions": ["Investment advice"],
        "completion_criteria": ["Produce an attributable L3 draft"],
        "industry_id": str(INDUSTRY_ID),
        "max_steps": 20,
        "max_total_tokens": 12_000,
        "max_cost_micro_usd": 300_000,
        "timeout_seconds": 600,
    }


def financial_scope() -> FinancialScope:
    return FinancialScope(
        cik="0000320193",
        accession="0000320193-23-000106",
        form=FinancialForm.TEN_K,
        report_period=date(2023, 9, 30),
        as_of=datetime(2023, 11, 3, 12, tzinfo=UTC),
        unit="USD",
        scale=6,
    )


def local_start_payload() -> dict[str, object]:
    payload = start_payload()
    payload.pop("industry_id")
    payload.update(
        {
            "mode": "local",
            "knowledge_base_ids": [str(KNOWLEDGE_BASE_ID)],
            "financial_scope": dict(financial_scope().to_mapping()),
        }
    )
    return payload


def test_post_accepts_an_explicit_brief_and_idempotency_key(
    test_settings: Settings,
) -> None:
    submission = StubSubmissionService()
    query = StubQueryService()
    with research_client(test_settings, submission, query) as client:
        response = client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/research-runs",
            headers=headers(**{"Idempotency-Key": "research-request-1"}),
            json=start_payload(),
        )

    assert response.status_code == 202
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "research_run_id": str(RESEARCH_RUN_ID),
        "agent_run_id": str(RUN_ID),
        "conversation_id": str(CONVERSATION_ID),
        "turn_id": str(TURN_ID),
        "job_id": str(JOB_ID),
        "created": True,
    }
    assert len(submission.calls) == 1
    scope, request = submission.calls[0]
    assert scope == WorkspaceScope(WORKSPACE_ID, USER_ID, "member")
    assert request.idempotency_key == "research-request-1"
    assert request.brief.original_question == start_payload()["original_question"]


def test_post_pins_local_knowledge_and_financial_scope(test_settings: Settings) -> None:
    submission = StubSubmissionService()
    query = StubQueryService()
    with research_client(test_settings, submission, query) as client:
        response = client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/research-runs",
            headers=headers(**{"Idempotency-Key": "local-research-request-1"}),
            json=local_start_payload(),
        )

    assert response.status_code == 202
    request = submission.calls[0][1]
    assert request.search_mode.value == "local"
    assert request.industry_id is None
    assert request.knowledge_base_ids == (KNOWLEDGE_BASE_ID,)
    assert request.brief.financial_scope == financial_scope()


def test_local_post_accepts_explicit_ambiguity_interrupt(test_settings: Settings) -> None:
    submission = StubSubmissionService()
    query = StubQueryService()
    payload = local_start_payload()
    payload["approval_reason"] = ResearchApprovalReason.COMPANY_OR_PERIOD_AMBIGUITY.value
    with research_client(test_settings, submission, query) as client:
        response = client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/research-runs",
            headers=headers(**{"Idempotency-Key": "local-research-approval-1"}),
            json=payload,
        )

    assert response.status_code == 202
    assert (
        submission.calls[0][1].brief.approval_reason
        is ResearchApprovalReason.COMPANY_OR_PERIOD_AMBIGUITY
    )


def test_get_exposes_brief_plan_draft_and_runtime_budget(test_settings: Settings) -> None:
    submission = StubSubmissionService()
    query = StubQueryService()
    with research_client(test_settings, submission, query) as client:
        response = client.get(
            f"/api/v1/workspaces/{WORKSPACE_ID}/research-runs/{RESEARCH_RUN_ID}",
            headers=headers(),
        )

    assert response.status_code == 200
    document = response.json()
    assert document["graph_version"] == RESEARCH_GRAPH_VERSION
    assert document["current_node"] == "draft"
    assert document["agent_status"] == "completed"
    assert document["brief"]["original_question"] == start_payload()["original_question"]
    assert document["brief"]["budget"]["max_steps"] == 20
    assert document["plan"]["actions"][0]["allowed_tool_names"] == ["fake.industry_lookup"]
    assert document["draft"]["status"] == "explainable_draft"
    assert document["draft"]["evidence_refs"] == [str(EVIDENCE_ID)]
    assert query.calls == [
        ("get", WorkspaceScope(WORKSPACE_ID, USER_ID, "member"), RESEARCH_RUN_ID)
    ]


def test_durability_timeline_decision_and_resume_contracts(test_settings: Settings) -> None:
    submission = StubSubmissionService()
    query = StubQueryService()
    durability = StubDurabilityService()
    scope = WorkspaceScope(WORKSPACE_ID, USER_ID, "member")
    with research_client(test_settings, submission, query, durability) as client:
        timeline = client.get(
            f"/api/v1/workspaces/{WORKSPACE_ID}/research-runs/{RESEARCH_RUN_ID}/durability",
            headers=headers(),
        )
        decision = client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/research-runs/{RESEARCH_RUN_ID}/approval-decisions",
            headers=headers(),
            json={
                "approval_request_id": str(APPROVAL_ID),
                "checkpoint_revision": 2,
                "outcome": ResearchApprovalOutcome.ALLOW.value,
            },
        )
        resumed = client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/research-runs/{RESEARCH_RUN_ID}/resume",
            headers=headers(),
            json={
                "approval_request_id": str(APPROVAL_ID),
                "checkpoint_revision": 2,
                "resume_token": TEST_RESUME_PROOF,
            },
        )

    assert timeline.status_code == 200
    assert timeline.json()["checkpoints"][0]["node"] == ResearchNode.PLAN.value
    assert timeline.json()["approvals"][0]["resume_token"] == TEST_RESUME_PROOF
    assert timeline.json()["duplicate_side_effect_count"] == 0
    assert decision.status_code == 200
    assert decision.json()["status"] == ResearchApprovalStatus.ALLOWED.value
    assert resumed.status_code == 202
    assert resumed.json() == {
        "agent_run_id": str(RUN_ID),
        "job_id": str(RESUME_JOB_ID),
        "created": True,
    }
    assert [name for name, _scope, _value in durability.calls] == [
        "timeline",
        "decide",
        "resume",
    ]
    assert all(call_scope == scope for _name, call_scope, _value in durability.calls)


def test_cross_workspace_and_invalid_briefs_do_not_reach_services(
    test_settings: Settings,
) -> None:
    submission = StubSubmissionService()
    query = StubQueryService()
    invalid = start_payload()
    invalid["confirmed_scope"] = []
    with research_client(test_settings, submission, query) as client:
        denied = client.post(
            f"/api/v1/workspaces/{OTHER_WORKSPACE_ID}/research-runs",
            headers=headers(**{"Idempotency-Key": "research-request-1"}),
            json=start_payload(),
        )
        rejected = client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/research-runs",
            headers=headers(**{"Idempotency-Key": "research-request-1"}),
            json=invalid,
        )

    assert denied.status_code == 403
    assert rejected.status_code == 422
    assert submission.calls == []
    assert query.calls == []
