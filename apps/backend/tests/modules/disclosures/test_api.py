"""HTTP authorization and error contracts for SEC filer discovery."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from fastapi.testclient import TestClient

from industry_platform.core.config import Settings
from industry_platform.main import create_app
from industry_platform.modules.disclosures.adapters.sec_edgar import (
    FrozenSecEdgarAdapter,
    UnavailableSecEdgarAdapter,
)
from industry_platform.modules.disclosures.diff import SecFilingDiffResult, SecFilingDiffStatus
from industry_platform.modules.disclosures.domain import (
    SecAmendmentPolicy,
    SecFilerResolution,
    SecFilingContentStatus,
    SecFilingImportStatus,
    SecWorkspaceFilingImport,
    SecXbrlFactQuery,
    SecXbrlFactResult,
    SecXbrlSourceKind,
    SecXbrlSyncResult,
)
from industry_platform.modules.disclosures.resources import (
    DisclosureResources,
    get_disclosure_resources,
)
from industry_platform.modules.disclosures.service import SecFilerResolutionService
from industry_platform.modules.financial_verification.domain import FinancialScope
from industry_platform.modules.identity.domain import (
    AccessToken,
    AuthenticatedPrincipal,
    AuthenticatedWorkspace,
    NormalizedEmail,
)
from industry_platform.modules.identity.http_auth import get_principal_resolver
from industry_platform.modules.workspaces.domain import WorkspaceScope

from .support import InMemoryFilerCatalogRepository, catalog_snapshot

USER_ID = UUID("11111111-1111-4111-8111-111111111111")
SESSION_ID = UUID("22222222-2222-4222-8222-222222222222")
WORKSPACE_ID = UUID("33333333-3333-4333-8333-333333333333")
OTHER_WORKSPACE_ID = UUID("44444444-4444-4444-8444-444444444444")
RAW_ACCESS_VALUE = ".".join(("header", "payload", "signature"))


@dataclass(slots=True)
class StubPrincipalResolver:
    value: AuthenticatedPrincipal

    async def resolve(self, _token: AccessToken) -> AuthenticatedPrincipal:
        return self.value


@dataclass(slots=True)
class TrackingResolutionService:
    inner: SecFilerResolutionService
    scopes: list[WorkspaceScope] = field(default_factory=list)

    async def resolve(
        self,
        scope: WorkspaceScope,
        *,
        query: str,
        limit: int = 5,
    ) -> SecFilerResolution:
        self.scopes.append(scope)
        return await self.inner.resolve(scope, query=query, limit=limit)


@dataclass(frozen=True, slots=True)
class StubResources:
    resolution_service: object | None = None
    filing_selection_service: object | None = None
    filing_import_service: object | None = None
    filing_content_service: object | None = None
    xbrl_service: object | None = None
    filing_diff_service: object | None = None
    monitor_subscription_service: object | None = None


@dataclass(slots=True)
class TrackingMonitorSubscriptionService:
    scopes: list[WorkspaceScope] = field(default_factory=list)

    async def list_monitors(self, scope: WorkspaceScope) -> tuple[object, ...]:
        self.scopes.append(scope)
        return ()

    async def list_cases(
        self,
        scope: WorkspaceScope,
        *,
        monitor_id: UUID | None = None,
    ) -> tuple[object, ...]:
        del monitor_id
        self.scopes.append(scope)
        return ()


@dataclass(slots=True)
class TrackingImportService:
    calls: list[tuple[WorkspaceScope, str, UUID]] = field(default_factory=list)

    async def import_filing(
        self,
        scope: WorkspaceScope,
        *,
        accession: str,
        knowledge_base_id: UUID,
        as_of: datetime,
        trace_id: object,
    ) -> SecWorkspaceFilingImport:
        assert as_of == datetime(2026, 8, 26, 4, 0, tzinfo=UTC)
        assert str(trace_id)
        self.calls.append((scope, accession, knowledge_base_id))
        now = datetime(2026, 8, 26, 4, 0, tzinfo=UTC)
        return SecWorkspaceFilingImport(
            id=UUID("55555555-5555-4555-8555-555555555555"),
            workspace_id=scope.workspace_id,
            filing_id=UUID("66666666-6666-4666-8666-666666666666"),
            accession=accession,
            knowledge_base_id=knowledge_base_id,
            primary_snapshot_id=UUID("77777777-7777-4777-8777-777777777777"),
            complete_submission_snapshot_id=UUID("88888888-8888-4888-8888-888888888888"),
            file_id=UUID("99999999-9999-4999-8999-999999999999"),
            document_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
            document_version_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
            ingestion_job_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
            status=SecFilingImportStatus.QUEUED,
            created_at=now,
            updated_at=now,
        )


@dataclass(slots=True)
class TrackingXbrlService:
    sync_calls: list[tuple[WorkspaceScope, str, UUID]] = field(default_factory=list)
    query_calls: list[tuple[WorkspaceScope, str, UUID, datetime, SecXbrlFactQuery]] = field(
        default_factory=list
    )

    async def sync(
        self,
        scope: WorkspaceScope,
        *,
        accession: str,
        knowledge_base_id: UUID,
    ) -> SecXbrlSyncResult:
        self.sync_calls.append((scope, accession, knowledge_base_id))
        return SecXbrlSyncResult(
            accession=accession,
            source_count=1,
            context_count=0,
            fact_count=1,
            source_versions=("sec-xbrl-companyfacts-v1",),
        )

    async def get_imported_facts(
        self,
        scope: WorkspaceScope,
        *,
        knowledge_base_ids: tuple[UUID, ...],
        accession: str,
        as_of: datetime,
        query: SecXbrlFactQuery,
    ) -> SecXbrlFactResult:
        self.query_calls.append((scope, accession, knowledge_base_ids[0], as_of, query))
        return SecXbrlFactResult(SecFilingContentStatus.NO_RESULT, accession)


@dataclass(slots=True)
class TrackingDiffService:
    calls: list[tuple[WorkspaceScope, FinancialScope, str, str]] = field(default_factory=list)

    async def compare(
        self,
        scope: WorkspaceScope,
        *,
        financial_scope: FinancialScope,
        comparison_accession: str,
        section_query: str,
        **_values: object,
    ) -> SecFilingDiffResult:
        self.calls.append((scope, financial_scope, comparison_accession, section_query))
        return SecFilingDiffResult(
            status=SecFilingDiffStatus.NOT_COMPARABLE,
            requested_accession=financial_scope.accession,
            comparison_accession=comparison_accession,
            error_code="filing_scope_not_comparable",
        )


def principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        user_id=USER_ID,
        session_id=SESSION_ID,
        email=NormalizedEmail("sec-research@example.test"),
        workspaces=(AuthenticatedWorkspace(WORKSPACE_ID, "SEC Research", "member"),),
    )


@contextmanager
def client_with_service(
    settings: Settings,
    service: object,
) -> Iterator[TestClient]:
    application = create_app(settings=settings)
    application.dependency_overrides[get_principal_resolver] = lambda: StubPrincipalResolver(
        principal()
    )
    resources = cast(DisclosureResources, StubResources(service))
    application.dependency_overrides[get_disclosure_resources] = lambda: resources
    with TestClient(application, base_url="https://localhost") as client:
        yield client


def headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {RAW_ACCESS_VALUE}"}


def test_authenticated_workspace_resolves_ticker_and_rejects_cross_workspace(
    test_settings: Settings,
) -> None:
    service = TrackingResolutionService(
        SecFilerResolutionService(
            repository=InMemoryFilerCatalogRepository(),
            source=FrozenSecEdgarAdapter(catalog_snapshot()),
        )
    )
    with client_with_service(test_settings, service) as client:
        resolved = client.get(
            f"/api/v1/workspaces/{WORKSPACE_ID}/disclosures/filers/resolve",
            headers=headers(),
            params={"query": "AAPL", "limit": 5},
        )
        denied = client.get(
            f"/api/v1/workspaces/{OTHER_WORKSPACE_ID}/disclosures/filers/resolve",
            headers=headers(),
            params={"query": "AAPL"},
        )

    assert resolved.status_code == 200
    assert resolved.headers["cache-control"] == "no-store"
    assert resolved.json()["status"] == "resolved"
    assert resolved.json()["candidates"][0]["cik"] == "0000320193"
    assert service.scopes == [WorkspaceScope(WORKSPACE_ID, USER_ID, "member")]
    assert denied.status_code == 403
    assert denied.json()["code"] == "WORKSPACE_ACCESS_DENIED"


def test_unconfigured_source_is_explicit_and_not_no_result(test_settings: Settings) -> None:
    service = SecFilerResolutionService(
        repository=InMemoryFilerCatalogRepository(),
        source=UnavailableSecEdgarAdapter(),
    )
    with client_with_service(test_settings, service) as client:
        response = client.get(
            f"/api/v1/workspaces/{WORKSPACE_ID}/disclosures/filers/resolve",
            headers=headers(),
            params={"query": "AAPL"},
        )

    assert response.status_code == 503
    assert response.json()["code"] == "SEC_SOURCE_NOT_CONFIGURED"
    assert "no_result" not in response.text


def test_authenticated_workspace_lists_point_in_time_filings(test_settings: Settings) -> None:
    from .test_filing_selection_service import NOW, selection_scope, service

    scope = selection_scope(policy=SecAmendmentPolicy.LATEST_KNOWN_BY_AS_OF)
    filing_service, _, _ = service(scope)
    application = create_app(settings=test_settings)
    application.dependency_overrides[get_principal_resolver] = lambda: StubPrincipalResolver(
        principal()
    )
    application.dependency_overrides[get_disclosure_resources] = lambda: cast(
        DisclosureResources,
        StubResources(filing_selection_service=filing_service),
    )
    with TestClient(application, base_url="https://localhost") as client:
        response = client.get(
            f"/api/v1/workspaces/{WORKSPACE_ID}/disclosures/filings",
            headers=headers(),
            params={
                "cik": "320193",
                "forms": ["10-K", "10-K/A"],
                "report_period_start": "2024-01-01",
                "report_period_end": "2024-12-31",
                "as_of": NOW.isoformat(),
                "amendment_policy": "latest_amendment_known_by_as_of",
            },
        )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["scope"]["cik"] == "0000320193"
    assert response.json()["filings"][0]["accession"] == "0000320193-24-000002"


def test_invalid_filing_scope_is_a_sanitized_validation_failure(test_settings: Settings) -> None:
    application = create_app(settings=test_settings)
    application.dependency_overrides[get_principal_resolver] = lambda: StubPrincipalResolver(
        principal()
    )
    with TestClient(application, base_url="https://localhost") as client:
        response = client.get(
            f"/api/v1/workspaces/{WORKSPACE_ID}/disclosures/filings",
            headers=headers(),
            params={
                "cik": "0",
                "forms": ["10-K"],
                "report_period_start": "2024-01-01",
                "report_period_end": "2024-12-31",
                "as_of": "2026-08-26T03:00:00Z",
            },
        )

    assert response.status_code == 422
    assert response.json()["code"] == "REQUEST_VALIDATION_FAILED"
    assert "CIK is invalid" not in response.text


def test_authenticated_workspace_can_queue_locked_filing_import_and_cross_workspace_cannot(
    test_settings: Settings,
) -> None:
    knowledge_base_id = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
    accession = "0000320193-23-000106"
    service = TrackingImportService()
    application = create_app(settings=test_settings)
    application.dependency_overrides[get_principal_resolver] = lambda: StubPrincipalResolver(
        principal()
    )
    application.dependency_overrides[get_disclosure_resources] = lambda: cast(
        DisclosureResources,
        StubResources(filing_import_service=service),
    )
    with TestClient(application, base_url="https://localhost") as client:
        response = client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/disclosures/filings/{accession}/imports",
            headers=headers(),
            json={
                "knowledge_base_id": str(knowledge_base_id),
                "as_of": "2026-08-26T04:00:00Z",
            },
        )
        denied = client.post(
            f"/api/v1/workspaces/{OTHER_WORKSPACE_ID}/disclosures/filings/{accession}/imports",
            headers=headers(),
            json={
                "knowledge_base_id": str(knowledge_base_id),
                "as_of": "2026-08-26T04:00:00Z",
            },
        )

    assert response.status_code == 202
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["status"] == "queued"
    assert service.calls == [
        (WorkspaceScope(WORKSPACE_ID, USER_ID, "member"), accession, knowledge_base_id)
    ]
    assert denied.status_code == 403
    assert denied.json()["code"] == "WORKSPACE_ACCESS_DENIED"


def test_authenticated_workspace_can_sync_and_query_typed_xbrl_facts(
    test_settings: Settings,
) -> None:
    knowledge_base_id = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
    accession = "0000320193-23-000106"
    service = TrackingXbrlService()
    application = create_app(settings=test_settings)
    application.dependency_overrides[get_principal_resolver] = lambda: StubPrincipalResolver(
        principal()
    )
    application.dependency_overrides[get_disclosure_resources] = lambda: cast(
        DisclosureResources,
        StubResources(xbrl_service=service),
    )
    with TestClient(application, base_url="https://localhost") as client:
        synced = client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/disclosures/filings/{accession}/xbrl/sync",
            headers=headers(),
            json={"knowledge_base_id": str(knowledge_base_id)},
        )
        queried = client.get(
            f"/api/v1/workspaces/{WORKSPACE_ID}/disclosures/filings/{accession}/xbrl/facts",
            headers=headers(),
            params={
                "knowledge_base_id": str(knowledge_base_id),
                "as_of": "2026-08-26T04:00:00Z",
                "taxonomy": "us-gaap",
                "concept": "Revenue",
                "source_kinds": "companyfacts_aggregate",
                "limit": 20,
            },
        )
        denied = client.post(
            f"/api/v1/workspaces/{OTHER_WORKSPACE_ID}/disclosures/filings/{accession}/xbrl/sync",
            headers=headers(),
            json={"knowledge_base_id": str(knowledge_base_id)},
        )

    assert synced.status_code == 200
    assert synced.headers["cache-control"] == "no-store"
    assert synced.json()["fact_count"] == 1
    assert queried.status_code == 200
    assert queried.json() == {
        "status": "no_result",
        "accession": accession,
        "facts": [],
        "error_code": None,
    }
    assert service.query_calls[0][4].source_kinds == (SecXbrlSourceKind.COMPANYFACTS_AGGREGATE,)
    assert denied.status_code == 403


def test_authenticated_workspace_diff_uses_explicit_financial_scope(
    test_settings: Settings,
) -> None:
    service = TrackingDiffService()
    accession = "0000320193-23-000106"
    comparison_accession = "0000320193-20-000096"
    application = create_app(settings=test_settings)
    application.dependency_overrides[get_principal_resolver] = lambda: StubPrincipalResolver(
        principal()
    )
    application.dependency_overrides[get_disclosure_resources] = lambda: cast(
        DisclosureResources,
        StubResources(filing_diff_service=service),
    )
    with TestClient(application, base_url="https://localhost") as client:
        response = client.get(
            f"/api/v1/workspaces/{WORKSPACE_ID}/disclosures/filings/{accession}/diff",
            headers=headers(),
            params={
                "knowledge_base_id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                "comparison_accession": comparison_accession,
                "cik": "0000320193",
                "form": "10-K",
                "report_period": "2023-09-30",
                "as_of": "2026-08-26T04:00:00Z",
                "unit": "USD",
                "scale": 0,
                "section_query": "risk factors",
            },
        )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["status"] == "not_comparable"
    assert response.json()["fact_changes"] == []
    assert service.calls[0][0] == WorkspaceScope(WORKSPACE_ID, USER_ID, "member")
    assert service.calls[0][1].accession == accession
    assert service.calls[0][2:] == (comparison_accession, "risk factors")


def test_monitor_and_case_lists_are_rebuilt_only_in_the_authenticated_workspace(
    test_settings: Settings,
) -> None:
    service = TrackingMonitorSubscriptionService()
    application = create_app(settings=test_settings)
    application.dependency_overrides[get_principal_resolver] = lambda: StubPrincipalResolver(
        principal()
    )
    application.dependency_overrides[get_disclosure_resources] = lambda: cast(
        DisclosureResources,
        StubResources(monitor_subscription_service=service),
    )

    with TestClient(application, base_url="https://localhost") as client:
        monitors = client.get(
            f"/api/v1/workspaces/{WORKSPACE_ID}/disclosures/monitors",
            headers=headers(),
        )
        cases = client.get(
            f"/api/v1/workspaces/{WORKSPACE_ID}/disclosures/cases",
            headers=headers(),
        )
        denied = client.get(
            f"/api/v1/workspaces/{OTHER_WORKSPACE_ID}/disclosures/monitors",
            headers=headers(),
        )

    assert monitors.status_code == 200
    assert monitors.json() == {"monitors": []}
    assert cases.status_code == 200
    assert cases.json() == {"cases": []}
    assert service.scopes == [
        WorkspaceScope(WORKSPACE_ID, USER_ID, "member"),
        WorkspaceScope(WORKSPACE_ID, USER_ID, "member"),
    ]
    assert denied.status_code == 403
