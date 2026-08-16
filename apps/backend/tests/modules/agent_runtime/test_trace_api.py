"""HTTP contract tests for the safe Agent Learning Workbench Trace projection."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response as HttpxResponse

from industry_platform.core.config import Settings
from industry_platform.core.http import PROBLEM_MEDIA_TYPE
from industry_platform.main import create_app
from industry_platform.modules.agent_runtime.adapters.trace_query import (
    AgentTraceDataError,
    AgentTraceNotFoundError,
    AgentTraceQueryError,
)
from industry_platform.modules.agent_runtime.context import (
    ContextBudgetSnapshot,
    ContextDecisionReason,
    ContextManifest,
    ContextSourceKind,
    ContextSourceManifestEntry,
)
from industry_platform.modules.agent_runtime.domain import (
    AgentRunStatus,
    AgentRunType,
    AgentStepKind,
    AgentStepStatus,
    RunStopReason,
)
from industry_platform.modules.agent_runtime.events import AgentEventType
from industry_platform.modules.agent_runtime.model import ModelRole
from industry_platform.modules.agent_runtime.resources import AgentTraceQuery
from industry_platform.modules.agent_runtime.router import get_agent_trace_query
from industry_platform.modules.agent_runtime.trace import (
    AgentTrace,
    TraceEvent,
    TraceRun,
    TraceStep,
    TraceUsage,
)
from industry_platform.modules.identity.domain import (
    AccessToken,
    AuthenticatedPrincipal,
    AuthenticatedWorkspace,
    NormalizedEmail,
    TraceId,
)
from industry_platform.modules.identity.http_auth import get_principal_resolver
from industry_platform.modules.workspaces.domain import WorkspaceScope

NOW = datetime(2026, 8, 14, 15, 0, tzinfo=UTC)
USER_ID = UUID("11111111-1111-4111-8111-111111111111")
SESSION_ID = UUID("22222222-2222-4222-8222-222222222222")
WORKSPACE_ID = UUID("33333333-3333-4333-8333-333333333333")
OTHER_WORKSPACE_ID = UUID("44444444-4444-4444-8444-444444444444")
RUN_ID = UUID("55555555-5555-4555-8555-555555555555")
CONVERSATION_ID = UUID("66666666-6666-4666-8666-666666666666")
TURN_ID = UUID("77777777-7777-4777-8777-777777777777")
STREAM_ID = UUID("88888888-8888-4888-8888-888888888888")
STEP_ID = UUID("99999999-9999-4999-8999-999999999999")
MANIFEST_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
RAW_ACCESS_VALUE = ".".join(("header", "payload", "signature"))


@dataclass(slots=True)
class StubPrincipalResolver:
    value: AuthenticatedPrincipal

    async def resolve(self, _token: AccessToken) -> AuthenticatedPrincipal:
        return self.value


@dataclass(slots=True)
class StubTraceQuery:
    failure: Exception | None = None
    calls: list[tuple[WorkspaceScope, UUID]] = field(default_factory=list)

    async def get(self, *, scope: WorkspaceScope, run_id: UUID) -> AgentTrace:
        self.calls.append((scope, run_id))
        if self.failure is not None:
            raise self.failure
        return trace()


def principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        user_id=USER_ID,
        session_id=SESSION_ID,
        email=NormalizedEmail("member@example.com"),
        workspaces=(AuthenticatedWorkspace(WORKSPACE_ID, "Workspace", "member"),),
    )


def usage() -> TraceUsage:
    return TraceUsage(
        input_tokens=80,
        output_tokens=20,
        cached_input_tokens=10,
        cost_micro_usd=250,
    )


def context_manifest() -> ContextManifest:
    return ContextManifest(
        schema_version=1,
        manifest_id=MANIFEST_ID,
        workspace_id=WORKSPACE_ID,
        run_id=RUN_ID,
        step_id=STEP_ID,
        compiler_version="context-v0",
        prompt_version="prompt-v0",
        runtime_projection_version="runtime-context-projection-v0",
        token_counter_version="utf8-upper-bound-v0",  # noqa: S106  # gitleaks:allow
        created_at=NOW + timedelta(seconds=1),
        budget=ContextBudgetSnapshot(
            run_max_total_tokens=1_000,
            tokens_used_before_step=0,
            max_input_tokens=512,
            estimated_input_tokens=100,
            allowed_output_tokens=200,
            unreserved_run_tokens=700,
        ),
        sources=(
            ContextSourceManifestEntry(
                ordinal=1,
                source_kind=ContextSourceKind.SYSTEM_INSTRUCTIONS,
                source_id="system-instructions",
                source_version="v1",
                included=True,
                decision_reason=ContextDecisionReason.INCLUDED,
                estimated_token_count=20,
                message_role=ModelRole.SYSTEM,
            ),
            ContextSourceManifestEntry(
                ordinal=2,
                source_kind=ContextSourceKind.RUNTIME_CONTEXT_PROJECTION,
                source_id="runtime-context",
                source_version="v1",
                included=True,
                decision_reason=ContextDecisionReason.INCLUDED,
                estimated_token_count=10,
                message_role=ModelRole.SYSTEM,
            ),
            ContextSourceManifestEntry(
                ordinal=3,
                source_kind=ContextSourceKind.CONVERSATION_SUMMARY,
                source_id="conversation-summary",
                source_version="v1",
                included=False,
                decision_reason=ContextDecisionReason.NOT_AVAILABLE,
                estimated_token_count=0,
                message_role=None,
            ),
            ContextSourceManifestEntry(
                ordinal=4,
                source_kind=ContextSourceKind.USER_QUESTION,
                source_id="current-question",
                source_version="v1",
                included=True,
                decision_reason=ContextDecisionReason.INCLUDED,
                estimated_token_count=70,
                message_role=ModelRole.USER,
            ),
        ),
    )


def trace() -> AgentTrace:
    return AgentTrace(
        schema_version=1,
        run=TraceRun(
            schema_version=1,
            run_id=RUN_ID,
            workspace_id=WORKSPACE_ID,
            conversation_id=CONVERSATION_ID,
            turn_id=TURN_ID,
            event_stream_id=STREAM_ID,
            trace_id=TraceId("safe-trace-id"),
            run_type=AgentRunType.DIRECT_ANSWER,
            status=AgentRunStatus.COMPLETED,
            stop_reason=RunStopReason.FINAL,
            runtime_version="direct-answer-runtime-v0",
            harness_version="harness-v0",
            state_revision=2,
            max_steps=2,
            max_total_tokens=1_000,
            max_cost_micro_usd=100_000,
            deadline=NOW + timedelta(minutes=5),
            event_count=2,
            step_count=1,
            usage=usage(),
            created_at=NOW,
            started_at=NOW,
            terminal_at=NOW + timedelta(seconds=2),
        ),
        steps=(
            TraceStep(
                step_id=STEP_ID,
                sequence=1,
                kind=AgentStepKind.MODEL,
                status=AgentStepStatus.COMPLETED,
                last_event_sequence=2,
                started_at=NOW,
                completed_at=NOW + timedelta(seconds=2),
                usage=usage(),
                error_code=None,
            ),
        ),
        context_manifests=(context_manifest(),),
        events=(
            TraceEvent(
                schema_version=1,
                sequence=1,
                occurred_at=NOW,
                event_type=AgentEventType.RUN_QUEUED,
                details={"runtime_version": "direct-answer-runtime-v0"},
            ),
            TraceEvent(
                schema_version=1,
                sequence=2,
                occurred_at=NOW + timedelta(seconds=2),
                event_type=AgentEventType.RUN_COMPLETED,
                details={"stop_reason": "final"},
            ),
        ),
    )


@contextmanager
def trace_client(settings: Settings, query: AgentTraceQuery) -> Iterator[TestClient]:
    application = create_app(settings=settings)
    application.dependency_overrides[get_principal_resolver] = lambda: StubPrincipalResolver(
        principal()
    )
    application.dependency_overrides[get_agent_trace_query] = lambda: query
    with TestClient(application, base_url="https://localhost") as client:
        yield client


def bearer_header() -> dict[str, str]:
    return {"Authorization": f"Bearer {RAW_ACCESS_VALUE}"}


def assert_problem(response: HttpxResponse, status_code: int, code: str) -> None:
    assert response.status_code == status_code
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["code"] == code


def test_trace_returns_only_the_safe_workspace_scoped_projection(
    test_settings: Settings,
) -> None:
    query = StubTraceQuery()
    path = f"/api/v1/workspaces/{WORKSPACE_ID}/agent-runs/{RUN_ID}/trace"
    with trace_client(test_settings, query) as client:
        response = client.get(path, headers=bearer_header())
        openapi = client.get("/openapi.json").json()

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    document = response.json()
    assert document["run"]["status"] == "completed"
    assert document["run"]["stop_reason"] == "final"
    assert document["run"]["usage"] == {
        "input_tokens": 80,
        "output_tokens": 20,
        "cached_input_tokens": 10,
        "cost_micro_usd": 250,
    }
    assert document["steps"][0]["kind"] == "model"
    assert document["context_manifests"][0]["sources"][-1] == {
        "ordinal": 4,
        "source_kind": "user_question",
        "source_id": "current-question",
        "source_version": "v1",
        "included": True,
        "decision_reason": "included",
        "estimated_token_count": 70,
        "message_role": "user",
        "source_sha256": None,
    }
    assert document["events"][-1]["details"] == {"stop_reason": "final"}
    assert "private user question" not in response.text.casefold()
    assert "private model answer" not in response.text.casefold()
    assert query.calls == [(WorkspaceScope(WORKSPACE_ID, USER_ID, "member"), RUN_ID)]

    operation = openapi["paths"]["/api/v1/workspaces/{workspace_id}/agent-runs/{run_id}/trace"][
        "get"
    ]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/AgentTraceResponse"
    }
    assert set(operation["responses"]) >= {"200", "404", "500", "503"}
    assert openapi["components"]["schemas"]["AgentTraceResponse"]["additionalProperties"] is False


def test_trace_rejects_authentication_and_workspace_mismatch_before_query(
    test_settings: Settings,
) -> None:
    query = StubTraceQuery()
    root = "/api/v1/workspaces"
    with trace_client(test_settings, query) as client:
        unauthenticated = client.get(f"{root}/{WORKSPACE_ID}/agent-runs/{RUN_ID}/trace")
        outside_scope = client.get(
            f"{root}/{OTHER_WORKSPACE_ID}/agent-runs/{RUN_ID}/trace",
            headers=bearer_header(),
        )

    assert_problem(unauthenticated, 401, "INVALID_AUTHENTICATED_SESSION")
    assert_problem(outside_scope, 403, "WORKSPACE_ACCESS_DENIED")
    assert query.calls == []


@pytest.mark.parametrize(
    ("failure", "status_code", "code"),
    [
        (AgentTraceNotFoundError(), 404, "AGENT_RUN_NOT_FOUND"),
        (AgentTraceDataError(), 500, "AGENT_TRACE_DATA_INVALID"),
        (
            AgentTraceQueryError(sqlstate="do-not-expose-08006"),
            503,
            "AGENT_TRACE_UNAVAILABLE",
        ),
    ],
)
def test_trace_failures_use_stable_non_sensitive_problem_responses(
    test_settings: Settings,
    failure: Exception,
    status_code: int,
    code: str,
) -> None:
    query = StubTraceQuery(failure=failure)
    with trace_client(test_settings, query) as client:
        response = client.get(
            f"/api/v1/workspaces/{WORKSPACE_ID}/agent-runs/{RUN_ID}/trace",
            headers=bearer_header(),
        )

    assert_problem(response, status_code, code)
    assert "do-not-expose" not in response.text
