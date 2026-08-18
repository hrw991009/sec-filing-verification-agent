"""HTTP authorization and contract tests for Data Explorer delivery."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from fastapi.testclient import TestClient

from industry_platform.core.config import Settings
from industry_platform.main import create_app
from industry_platform.modules.data_explorer.domain import (
    ChartRequest,
    QueryRunResult,
    QueryRunStatus,
)
from industry_platform.modules.data_explorer.resources import (
    DataExplorerResources,
    get_data_explorer_resources,
)
from industry_platform.modules.identity.domain import (
    AccessToken,
    AuthenticatedPrincipal,
    AuthenticatedWorkspace,
    NormalizedEmail,
    TraceId,
    WorkspaceRoleName,
)
from industry_platform.modules.identity.http_auth import get_principal_resolver
from industry_platform.modules.workspaces.domain import WorkspaceScope

USER_ID = UUID("11111111-1111-4111-8111-111111111111")
SESSION_ID = UUID("22222222-2222-4222-8222-222222222222")
WORKSPACE_ID = UUID("33333333-3333-4333-8333-333333333333")
CONNECTION_ID = UUID("44444444-4444-4444-8444-444444444444")
QUERY_RUN_ID = UUID("55555555-5555-4555-8555-555555555555")
RAW_ACCESS_VALUE = ".".join(("header", "payload", "signature"))


@dataclass(slots=True)
class StubPrincipalResolver:
    value: AuthenticatedPrincipal

    async def resolve(self, _token: AccessToken) -> AuthenticatedPrincipal:
        return self.value


@dataclass(slots=True)
class StubDataExplorerService:
    calls: list[tuple[WorkspaceScope, UUID, TraceId]] = field(default_factory=list)

    async def execute_direct_query(
        self,
        scope: WorkspaceScope,
        *,
        connection_id: UUID,
        question: str,
        generated_sql: str,
        chart: ChartRequest,
        trace_id: TraceId,
    ) -> QueryRunResult:
        del chart
        self.calls.append((scope, connection_id, trace_id))
        return QueryRunResult(
            query_run_id=QUERY_RUN_ID,
            connection_id=connection_id,
            workspace_id=scope.workspace_id,
            status=QueryRunStatus.FAILED,
            question=question,
            generated_sql=generated_sql,
            error_code="sql_statement_type_rejected",
            terminal_at=_now(),
            created_at=_now(),
        )


@dataclass(frozen=True, slots=True)
class StubResources:
    service: StubDataExplorerService


def _now() -> datetime:
    return datetime(2026, 8, 17, 5, 0, tzinfo=UTC)


def _principal(role: WorkspaceRoleName) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        user_id=USER_ID,
        session_id=SESSION_ID,
        email=NormalizedEmail("data-explorer@example.test"),
        workspaces=(
            AuthenticatedWorkspace(
                workspace_id=WORKSPACE_ID,
                name="Data Explorer",
                role=role,
            ),
        ),
    )


@contextmanager
def _client(
    settings: Settings,
    service: StubDataExplorerService,
    *,
    role: WorkspaceRoleName,
) -> Iterator[TestClient]:
    application = create_app(settings=settings)
    application.dependency_overrides[get_principal_resolver] = lambda: StubPrincipalResolver(
        _principal(role)
    )
    resources = cast(DataExplorerResources, StubResources(service))
    application.dependency_overrides[get_data_explorer_resources] = lambda: resources
    with TestClient(application, base_url="https://localhost") as client:
        yield client


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {RAW_ACCESS_VALUE}"}


def _payload() -> dict[str, object]:
    return {
        "connection_id": str(CONNECTION_ID),
        "question": "Delete data",
        "generated_sql": "DELETE FROM public.sample_company_metrics",
        "chart": {"chart_type": "table"},
    }


def test_member_query_uses_authenticated_scope_and_returns_audited_rejection(
    test_settings: Settings,
) -> None:
    service = StubDataExplorerService()
    with _client(test_settings, service, role="member") as client:
        response = client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/query-runs",
            headers=_headers(),
            json=_payload(),
        )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["status"] == "failed"
    assert response.json()["error_code"] == "sql_statement_type_rejected"
    assert len(service.calls) == 1
    scope, connection_id, trace_id = service.calls[0]
    assert scope == WorkspaceScope(WORKSPACE_ID, USER_ID, "member")
    assert connection_id == CONNECTION_ID
    assert trace_id == response.headers["X-Trace-ID"]


def test_viewer_cannot_execute_query_and_invalid_chart_fails_before_service(
    test_settings: Settings,
) -> None:
    service = StubDataExplorerService()
    with _client(test_settings, service, role="viewer") as client:
        denied = client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/query-runs",
            headers=_headers(),
            json=_payload(),
        )
    with _client(test_settings, service, role="member") as client:
        invalid_payload = _payload()
        invalid_payload["chart"] = {"chart_type": "bar", "x_column": "industry"}
        invalid = client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/query-runs",
            headers=_headers(),
            json=invalid_payload,
        )

    assert denied.status_code == 403
    assert denied.json()["code"] == "WORKSPACE_ACCESS_DENIED"
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "REQUEST_VALIDATION_FAILED"
    assert service.calls == []
