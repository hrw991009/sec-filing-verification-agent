"""HTTP authorization and error contracts for SEC filer discovery."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import cast
from uuid import UUID

from fastapi.testclient import TestClient

from industry_platform.core.config import Settings
from industry_platform.main import create_app
from industry_platform.modules.disclosures.adapters.sec_edgar import (
    FrozenSecEdgarAdapter,
    UnavailableSecEdgarAdapter,
)
from industry_platform.modules.disclosures.domain import SecFilerResolution
from industry_platform.modules.disclosures.resources import (
    DisclosureResources,
    get_disclosure_resources,
)
from industry_platform.modules.disclosures.service import SecFilerResolutionService
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
    resolution_service: object


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
