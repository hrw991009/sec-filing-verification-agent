"""HTTP acceptance contracts for Research L3 creation and inspection."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
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
from industry_platform.modules.research.router import (
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
RAW_ACCESS_VALUE = ".".join(("header", "payload", "signature"))


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
) -> Iterator[TestClient]:
    application = create_app(settings=settings)
    application.dependency_overrides[get_principal_resolver] = lambda: StubPrincipalResolver(
        principal()
    )
    application.dependency_overrides[get_submission_service] = lambda: cast(
        ResearchSubmissionService, submission
    )
    application.dependency_overrides[get_query_service] = lambda: cast(ResearchQueryService, query)
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
