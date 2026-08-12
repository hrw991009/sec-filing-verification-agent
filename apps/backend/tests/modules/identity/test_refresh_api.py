"""HTTP contract and security tests for refresh-session delivery."""

import logging
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
from industry_platform.modules.identity.domain import (
    AccessToken,
    AccessTokenGenerationError,
    CsrfToken,
    InvalidRefreshSessionError,
    LoginSessionRecord,
    RefreshedSession,
    RefreshRecoveryError,
    RefreshSessionCommand,
    RefreshSessionPersistenceError,
    RefreshSessionUnavailableError,
    RefreshToken,
    SessionTokenGenerationError,
)
from industry_platform.modules.identity.http_cookies import (
    CSRF_COOKIE_NAME,
    DEVICE_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
)
from industry_platform.modules.identity.ports import RefreshSessionUseCase
from industry_platform.modules.identity.router import get_refresh_service
from industry_platform.modules.identity.schemas import BEARER_SCHEME, RefreshResponse

USER_ID = UUID("11111111-1111-4111-8111-111111111111")
FAMILY_ID = UUID("22222222-2222-4222-8222-222222222222")
SESSION_ID = UUID("33333333-3333-4333-8333-333333333333")
ISSUED_AT = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)
ACCESS_EXPIRES_AT = ISSUED_AT + timedelta(minutes=10)
IDLE_EXPIRES_AT = ISSUED_AT + timedelta(days=7)
ABSOLUTE_EXPIRES_AT = ISSUED_AT + timedelta(days=30)
TRUSTED_ORIGIN = "https://localhost:5173"
ACCESS_VALUE = "signed.refresh.access.value"
PRESENTED_REFRESH_VALUE = "o" * 43
PRESENTED_CSRF_VALUE = "p" * 43
PRESENTED_DEVICE_VALUE = "d" * 43
SUCCESSOR_REFRESH_VALUE = "r" * 43
SUCCESSOR_CSRF_VALUE = "c" * 43
CSRF_HEADER_NAME = "X-CSRF-Token"
TEST_COOKIE_DOMAIN = "localhost.local"


@dataclass(slots=True)
class StubRefreshService:
    """Record the HTTP command and return or raise one controlled outcome."""

    result: RefreshedSession | None = None
    failure: Exception | None = None
    commands: list[RefreshSessionCommand] = field(default_factory=list)

    async def refresh(self, command: RefreshSessionCommand) -> RefreshedSession:
        self.commands.append(command)

        if self.failure is not None:
            raise self.failure
        if self.result is None:
            raise RuntimeError("Refresh test stub has no result")

        return self.result


def successful_refresh(*, recovered: bool = False) -> RefreshedSession:
    """Return one committed successor suitable for HTTP serialization."""

    return RefreshedSession(
        session=LoginSessionRecord(
            user_id=USER_ID,
            rotation_family_id=FAMILY_ID,
            session_id=SESSION_ID,
            issued_at=ISSUED_AT,
            idle_expires_at=IDLE_EXPIRES_AT,
            absolute_expires_at=ABSOLUTE_EXPIRES_AT,
        ),
        access_token=AccessToken.from_transport(ACCESS_VALUE),
        access_token_expires_at=ACCESS_EXPIRES_AT,
        refresh_token=RefreshToken.from_transport(SUCCESSOR_REFRESH_VALUE),
        csrf_token=CsrfToken.from_transport(SUCCESSOR_CSRF_VALUE),
        recovered=recovered,
    )


@contextmanager
def refresh_client(
    settings: Settings,
    service: RefreshSessionUseCase,
) -> Iterator[TestClient]:
    """Run the real FastAPI delivery layer with only the use case replaced."""

    application = create_app(settings=settings)
    application.dependency_overrides[get_refresh_service] = lambda: service

    with TestClient(application, base_url="https://localhost") as client:
        yield client


def refresh_headers(*, csrf_value: str = PRESENTED_CSRF_VALUE) -> dict[str, str]:
    return {
        "Origin": TRUSTED_ORIGIN,
        CSRF_HEADER_NAME: csrf_value,
    }


def install_presented_cookies(client: TestClient) -> None:
    client.cookies.set(
        REFRESH_COOKIE_NAME,
        PRESENTED_REFRESH_VALUE,
        domain=TEST_COOKIE_DOMAIN,
        path="/",
    )
    client.cookies.set(
        CSRF_COOKIE_NAME,
        PRESENTED_CSRF_VALUE,
        domain=TEST_COOKIE_DOMAIN,
        path="/",
    )
    client.cookies.set(
        DEVICE_COOKIE_NAME,
        PRESENTED_DEVICE_VALUE,
        domain=TEST_COOKIE_DOMAIN,
        path="/",
    )


def cookie_headers(response: HttpxResponse) -> dict[str, str]:
    """Index every Set-Cookie field without parsing away security attributes."""

    return {value.split("=", 1)[0]: value for value in response.headers.get_list("set-cookie")}


def assert_no_store(response: HttpxResponse) -> None:
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"


def assert_problem(
    response: HttpxResponse,
    *,
    status_code: int,
    code: str,
) -> None:
    body = response.json()
    assert response.status_code == status_code
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert_no_store(response)
    assert body["status"] == status_code
    assert body["code"] == code
    assert body["trace_id"] == response.headers["X-Trace-ID"]
    assert set(body) == {"type", "title", "status", "detail", "code", "trace_id"}
    for sensitive_value in (
        PRESENTED_REFRESH_VALUE,
        PRESENTED_CSRF_VALUE,
        PRESENTED_DEVICE_VALUE,
        SUCCESSOR_REFRESH_VALUE,
        SUCCESSOR_CSRF_VALUE,
    ):
        assert sensitive_value not in response.text


def assert_secure_cookie(
    header: str,
    *,
    httponly: bool,
    deleted: bool = False,
) -> None:
    lowered = header.lower()
    assert "secure" in lowered
    assert "samesite=strict" in lowered
    assert "path=/" in lowered
    assert "domain=" not in lowered
    assert ("httponly" in lowered) is httponly
    if deleted:
        assert "max-age=0" in lowered


def test_refresh_reads_browser_proof_and_returns_only_access_credentials(
    test_settings: Settings,
) -> None:
    service = StubRefreshService(result=successful_refresh())

    with refresh_client(test_settings, service) as client:
        install_presented_cookies(client)
        response = client.post(
            "/api/v1/auth/refresh",
            headers={
                **refresh_headers(),
                "X-Trace-ID": "untrusted-client-trace",
            },
        )

        assert client.cookies.get(DEVICE_COOKIE_NAME) == PRESENTED_DEVICE_VALUE

    assert response.status_code == 200
    assert response.json() == {
        "access_token": ACCESS_VALUE,
        "token_type": BEARER_SCHEME,
        "expires_at": "2026-08-11T08:10:00Z",
    }
    assert_no_store(response)
    assert response.headers["access-control-allow-origin"] == TRUSTED_ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"
    assert response.headers["access-control-expose-headers"] == "X-Trace-ID"
    assert len(service.commands) == 1
    command = service.commands[0]
    assert command.origin == TRUSTED_ORIGIN
    assert command.refresh_token.reveal_for_transport() == PRESENTED_REFRESH_VALUE
    assert command.csrf_cookie_value == PRESENTED_CSRF_VALUE
    assert command.csrf_header_value == PRESENTED_CSRF_VALUE
    assert command.device_token.reveal_for_transport() == PRESENTED_DEVICE_VALUE
    assert command.trace_id == response.headers["X-Trace-ID"]
    assert command.trace_id != "untrusted-client-trace"

    headers = cookie_headers(response)
    assert set(headers) == {REFRESH_COOKIE_NAME, CSRF_COOKIE_NAME}
    assert response.cookies[REFRESH_COOKIE_NAME] == SUCCESSOR_REFRESH_VALUE
    assert response.cookies[CSRF_COOKIE_NAME] == SUCCESSOR_CSRF_VALUE
    assert "max-age=604800" in headers[REFRESH_COOKIE_NAME].lower()
    assert "max-age=604800" in headers[CSRF_COOKIE_NAME].lower()
    assert_secure_cookie(headers[REFRESH_COOKIE_NAME], httponly=True)
    assert_secure_cookie(headers[CSRF_COOKIE_NAME], httponly=False)
    assert ACCESS_VALUE not in " ".join(headers.values())
    assert SUCCESSOR_REFRESH_VALUE not in response.text
    assert SUCCESSOR_CSRF_VALUE not in response.text
    assert ACCESS_VALUE not in repr(RefreshResponse.model_validate(response.json()))


def test_refresh_cors_preflight_allows_only_the_configured_browser_origin(
    test_settings: Settings,
) -> None:
    service = StubRefreshService(result=successful_refresh())
    preflight_headers = {
        "Origin": TRUSTED_ORIGIN,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": (f"{CSRF_HEADER_NAME}, Authorization, Content-Type"),
    }

    with refresh_client(test_settings, service) as client:
        allowed = client.options(
            "/api/v1/auth/refresh",
            headers=preflight_headers,
        )
        rejected = client.options(
            "/api/v1/auth/refresh",
            headers={
                **preflight_headers,
                "Origin": "https://attacker.invalid",
            },
        )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == TRUSTED_ORIGIN
    assert allowed.headers["access-control-allow-credentials"] == "true"
    assert "POST" in allowed.headers["access-control-allow-methods"]
    allowed_headers = allowed.headers["access-control-allow-headers"].lower()
    for header_name in (CSRF_HEADER_NAME, "Authorization", "Content-Type"):
        assert header_name.lower() in allowed_headers
    assert "Origin" in allowed.headers["vary"]
    assert rejected.status_code == 400
    assert "access-control-allow-origin" not in rejected.headers
    assert service.commands == []


def test_recovery_reissues_the_same_successor_without_exposing_recovery_state(
    test_settings: Settings,
) -> None:
    service = StubRefreshService(result=successful_refresh(recovered=True))

    with refresh_client(test_settings, service) as client:
        install_presented_cookies(client)
        response = client.post(
            "/api/v1/auth/refresh",
            headers=refresh_headers(),
        )

    assert response.status_code == 200
    assert set(response.json()) == {"access_token", "token_type", "expires_at"}
    assert "recovered" not in response.json()
    assert response.cookies[REFRESH_COOKIE_NAME] == SUCCESSOR_REFRESH_VALUE
    assert response.cookies[CSRF_COOKIE_NAME] == SUCCESSOR_CSRF_VALUE
    assert set(cookie_headers(response)) == {REFRESH_COOKIE_NAME, CSRF_COOKIE_NAME}


def test_invalid_refresh_clears_all_browser_session_cookies(
    test_settings: Settings,
) -> None:
    service = StubRefreshService(failure=InvalidRefreshSessionError())

    with refresh_client(test_settings, service) as client:
        install_presented_cookies(client)
        response = client.post(
            "/api/v1/auth/refresh",
            headers=refresh_headers(csrf_value="wrong-csrf-proof"),
        )
        assert client.cookies.get(REFRESH_COOKIE_NAME) is None
        assert client.cookies.get(CSRF_COOKIE_NAME) is None
        assert client.cookies.get(DEVICE_COOKIE_NAME) is None

    assert_problem(response, status_code=401, code="INVALID_REFRESH_SESSION")
    headers = cookie_headers(response)
    assert set(headers) == {
        REFRESH_COOKIE_NAME,
        CSRF_COOKIE_NAME,
        DEVICE_COOKIE_NAME,
    }
    assert_secure_cookie(headers[REFRESH_COOKIE_NAME], httponly=True, deleted=True)
    assert_secure_cookie(headers[CSRF_COOKIE_NAME], httponly=False, deleted=True)
    assert_secure_cookie(headers[DEVICE_COOKIE_NAME], httponly=True, deleted=True)


@pytest.mark.parametrize(
    ("headers", "expected_origin", "expected_csrf_header"),
    [
        (
            {CSRF_HEADER_NAME: PRESENTED_CSRF_VALUE},
            "",
            PRESENTED_CSRF_VALUE,
        ),
        (
            {"Origin": TRUSTED_ORIGIN},
            TRUSTED_ORIGIN,
            "",
        ),
        (
            [
                ("Origin", TRUSTED_ORIGIN),
                ("Origin", "https://attacker.invalid"),
                (CSRF_HEADER_NAME, PRESENTED_CSRF_VALUE),
            ],
            "",
            PRESENTED_CSRF_VALUE,
        ),
        (
            [
                ("Origin", TRUSTED_ORIGIN),
                (CSRF_HEADER_NAME, PRESENTED_CSRF_VALUE),
                (CSRF_HEADER_NAME, "different-proof"),
            ],
            TRUSTED_ORIGIN,
            "",
        ),
    ],
)
def test_missing_or_duplicate_proof_headers_fail_closed(
    test_settings: Settings,
    headers: dict[str, str] | list[tuple[str, str]],
    expected_origin: str,
    expected_csrf_header: str,
) -> None:
    service = StubRefreshService(failure=InvalidRefreshSessionError())

    with refresh_client(test_settings, service) as client:
        install_presented_cookies(client)
        response = client.post("/api/v1/auth/refresh", headers=headers)

    assert_problem(response, status_code=401, code="INVALID_REFRESH_SESSION")
    assert len(service.commands) == 1
    assert service.commands[0].origin == expected_origin
    assert service.commands[0].csrf_header_value == expected_csrf_header


def test_missing_cookies_reach_the_uniform_refresh_rejection(
    test_settings: Settings,
) -> None:
    service = StubRefreshService(failure=InvalidRefreshSessionError())

    with refresh_client(test_settings, service) as client:
        response = client.post(
            "/api/v1/auth/refresh",
            headers=refresh_headers(),
        )
        assert client.cookies.get(REFRESH_COOKIE_NAME) is None
        assert client.cookies.get(CSRF_COOKIE_NAME) is None
        assert client.cookies.get(DEVICE_COOKIE_NAME) is None

    assert_problem(response, status_code=401, code="INVALID_REFRESH_SESSION")
    assert len(service.commands) == 1
    command = service.commands[0]
    assert command.refresh_token.reveal_for_transport() == ""
    assert command.csrf_cookie_value == ""
    assert command.device_token.reveal_for_transport() == ""


@pytest.mark.parametrize(
    "failure",
    [
        RefreshSessionUnavailableError(),
        RefreshSessionPersistenceError(sqlstate="40001"),
        AccessTokenGenerationError(),
        SessionTokenGenerationError(),
        RefreshRecoveryError(),
    ],
)
def test_transient_refresh_failures_preserve_cookies_for_safe_retry(
    test_settings: Settings,
    failure: Exception,
) -> None:
    service = StubRefreshService(failure=failure)

    with refresh_client(test_settings, service) as client:
        install_presented_cookies(client)
        response = client.post(
            "/api/v1/auth/refresh",
            headers=refresh_headers(),
        )
        assert client.cookies.get(REFRESH_COOKIE_NAME) == PRESENTED_REFRESH_VALUE
        assert client.cookies.get(CSRF_COOKIE_NAME) == PRESENTED_CSRF_VALUE
        assert client.cookies.get(DEVICE_COOKIE_NAME) == PRESENTED_DEVICE_VALUE

    assert_problem(response, status_code=503, code="REFRESH_UNAVAILABLE")
    assert response.headers.get_list("set-cookie") == []
    assert "40001" not in response.text


def test_unexpected_refresh_failure_is_sanitized_without_changing_cookies(
    test_settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sensitive_detail = f"database cookie={PRESENTED_REFRESH_VALUE}"
    service = StubRefreshService(failure=RuntimeError(sensitive_detail))
    caplog.set_level(logging.ERROR, logger="industry_platform.core.http")

    with refresh_client(test_settings, service) as client:
        install_presented_cookies(client)
        response = client.post(
            "/api/v1/auth/refresh",
            headers=refresh_headers(),
        )
        assert client.cookies.get(REFRESH_COOKIE_NAME) == PRESENTED_REFRESH_VALUE
        assert client.cookies.get(CSRF_COOKIE_NAME) == PRESENTED_CSRF_VALUE
        assert client.cookies.get(DEVICE_COOKIE_NAME) == PRESENTED_DEVICE_VALUE

    assert_problem(response, status_code=500, code="INTERNAL_SERVER_ERROR")
    assert response.headers.get_list("set-cookie") == []
    assert sensitive_detail not in response.text
    assert sensitive_detail not in caplog.text
    assert "RuntimeError" in caplog.text


def test_openapi_documents_refresh_success_and_problem_contracts(
    test_settings: Settings,
) -> None:
    service = StubRefreshService(result=successful_refresh())

    with refresh_client(test_settings, service) as client:
        document = client.get("/openapi.json").json()

    responses = document["paths"]["/api/v1/auth/refresh"]["post"]["responses"]
    assert "200" in responses
    for status_code in ("401", "500", "503"):
        assert set(responses[status_code]["content"]) == {PROBLEM_MEDIA_TYPE}
