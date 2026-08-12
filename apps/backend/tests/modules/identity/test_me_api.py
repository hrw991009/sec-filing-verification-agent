"""HTTP contract tests for the current authenticated user endpoint."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response as HttpxResponse

from industry_platform.core.config import Settings
from industry_platform.core.http import PROBLEM_MEDIA_TYPE
from industry_platform.main import create_app
from industry_platform.modules.identity.domain import (
    AccessToken,
    AuthenticatedPrincipal,
    AuthenticatedSessionPersistenceError,
    AuthenticatedWorkspace,
    InvalidAuthenticatedSessionError,
    NormalizedEmail,
)
from industry_platform.modules.identity.http_auth import get_principal_resolver
from industry_platform.modules.identity.http_cookies import REFRESH_COOKIE_NAME
from industry_platform.modules.identity.ports import AuthenticatedPrincipalResolver

USER_ID = UUID("11111111-1111-4111-8111-111111111111")
SESSION_ID = UUID("22222222-2222-4222-8222-222222222222")
WORKSPACE_ID = UUID("33333333-3333-4333-8333-333333333333")
RAW_ACCESS_VALUE = ".".join(("header", "payload", "signature"))
PRESENTED_REFRESH_VALUE = "r" * 43


@dataclass(slots=True)
class StubPrincipalResolver:
    result: AuthenticatedPrincipal | None = None
    failure: Exception | None = None
    presented: list[AccessToken] = field(default_factory=list)

    async def resolve(self, token: AccessToken) -> AuthenticatedPrincipal:
        self.presented.append(token)
        if self.failure is not None:
            raise self.failure
        if self.result is None:
            raise RuntimeError("Principal test stub has no result")
        return self.result


def current_principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        user_id=USER_ID,
        session_id=SESSION_ID,
        email=NormalizedEmail("learner@example.com"),
        workspaces=(
            AuthenticatedWorkspace(
                workspace_id=WORKSPACE_ID,
                name="My Workspace",
                role="owner",
            ),
        ),
    )


@contextmanager
def me_client(
    settings: Settings,
    resolver: AuthenticatedPrincipalResolver,
) -> Iterator[TestClient]:
    application = create_app(settings=settings)
    application.dependency_overrides[get_principal_resolver] = lambda: resolver
    with TestClient(application, base_url="https://localhost") as client:
        yield client


def bearer_header(value: str = RAW_ACCESS_VALUE) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


def assert_problem(response: HttpxResponse, *, status_code: int, code: str) -> None:
    body = response.json()
    assert response.status_code == status_code
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert body["code"] == code
    assert body["trace_id"] == response.headers["X-Trace-ID"]


def test_me_returns_only_live_database_identity(
    test_settings: Settings,
) -> None:
    resolver = StubPrincipalResolver(result=current_principal())

    with me_client(test_settings, resolver) as client:
        response = client.get("/api/v1/auth/me", headers=bearer_header())

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.json() == {
        "user": {
            "id": str(USER_ID),
            "email": "learner@example.com",
        },
        "workspaces": [
            {
                "id": str(WORKSPACE_ID),
                "name": "My Workspace",
                "role": "owner",
            }
        ],
    }
    assert len(resolver.presented) == 1
    assert resolver.presented[0].reveal_for_transport() == RAW_ACCESS_VALUE
    assert RAW_ACCESS_VALUE not in response.text


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Basic credentials"},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer value with spaces"},
        {"Authorization": "Bearer first,second"},
        [("Authorization", "Bearer first"), ("Authorization", "Bearer second")],
    ],
)
def test_missing_ambiguous_or_malformed_bearer_is_uniform_401(
    test_settings: Settings,
    headers: dict[str, str] | list[tuple[str, str]],
) -> None:
    resolver = StubPrincipalResolver(result=current_principal())

    with me_client(test_settings, resolver) as client:
        response = client.get("/api/v1/auth/me", headers=headers)

    assert_problem(
        response,
        status_code=401,
        code="INVALID_AUTHENTICATED_SESSION",
    )
    assert response.headers["www-authenticate"] == "Bearer"
    assert resolver.presented == []


def test_revoked_session_401_does_not_destroy_refresh_recovery(
    test_settings: Settings,
) -> None:
    resolver = StubPrincipalResolver(failure=InvalidAuthenticatedSessionError())

    with me_client(test_settings, resolver) as client:
        client.cookies.set(REFRESH_COOKIE_NAME, PRESENTED_REFRESH_VALUE)
        response = client.get("/api/v1/auth/me", headers=bearer_header())
        assert client.cookies.get(REFRESH_COOKIE_NAME) == PRESENTED_REFRESH_VALUE

    assert_problem(
        response,
        status_code=401,
        code="INVALID_AUTHENTICATED_SESSION",
    )
    assert response.headers.get_list("set-cookie") == []


def test_identity_database_failure_is_safe_retryable_503(
    test_settings: Settings,
) -> None:
    resolver = StubPrincipalResolver(failure=AuthenticatedSessionPersistenceError(sqlstate="40001"))

    with me_client(test_settings, resolver) as client:
        response = client.get("/api/v1/auth/me", headers=bearer_header())

    assert_problem(response, status_code=503, code="IDENTITY_UNAVAILABLE")
    assert "40001" not in response.text
    assert RAW_ACCESS_VALUE not in response.text


def test_unexpected_identity_failure_is_sanitized(
    test_settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sensitive_detail = "access credential must not reach logs"
    resolver = StubPrincipalResolver(failure=RuntimeError(sensitive_detail))
    caplog.set_level(logging.ERROR, logger="industry_platform.core.http")

    with me_client(test_settings, resolver) as client:
        response = client.get("/api/v1/auth/me", headers=bearer_header())

    assert_problem(response, status_code=500, code="INTERNAL_SERVER_ERROR")
    assert sensitive_detail not in response.text
    assert sensitive_detail not in caplog.text
    assert RAW_ACCESS_VALUE not in response.text
    assert RAW_ACCESS_VALUE not in caplog.text


def test_openapi_documents_me_problem_contracts(test_settings: Settings) -> None:
    resolver = StubPrincipalResolver(result=current_principal())

    with me_client(test_settings, resolver) as client:
        document = client.get("/openapi.json").json()

    operation = document["paths"]["/api/v1/auth/me"]["get"]
    responses = operation["responses"]
    assert "200" in responses
    for status_code in ("401", "500", "503"):
        assert set(responses[status_code]["content"]) == {PROBLEM_MEDIA_TYPE}
    assert operation["security"] == [{"AccessToken": []}]
    assert document["components"]["securitySchemes"]["AccessToken"] == {
        "type": "http",
        "scheme": "bearer",
    }
