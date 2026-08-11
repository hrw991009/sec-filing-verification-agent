"""HTTP contract and security tests for login and Cookie delivery."""

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
    AuthenticateCredentialsCommand,
    AuthenticationPersistenceError,
    CsrfToken,
    DeviceToken,
    EstablishedLoginSession,
    InvalidCredentialsError,
    LoginRateLimitExceededError,
    LoginRateLimitUnavailableError,
    LoginSessionPersistenceError,
    LoginSessionRecord,
    NormalizedEmail,
    RefreshToken,
    SessionTokenGenerationError,
)
from industry_platform.modules.identity.http_cookies import (
    CSRF_COOKIE_NAME,
    DEVICE_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
)
from industry_platform.modules.identity.ports import (
    LoginAttemptRateLimiter,
    LoginSessionUseCase,
)
from industry_platform.modules.identity.router import (
    get_login_rate_limiter,
    get_login_service,
)
from industry_platform.modules.identity.schemas import LoginResponse

USER_ID = UUID("11111111-1111-4111-8111-111111111111")
FAMILY_ID = UUID("22222222-2222-4222-8222-222222222222")
SESSION_ID = UUID("33333333-3333-4333-8333-333333333333")
ISSUED_AT = datetime(2026, 8, 11, 4, 0, tzinfo=UTC)
ACCESS_EXPIRES_AT = ISSUED_AT + timedelta(minutes=10)
IDLE_EXPIRES_AT = ISSUED_AT + timedelta(days=7)
ABSOLUTE_EXPIRES_AT = ISSUED_AT + timedelta(days=30)
VALID_RAW_VALUE = "legacy"
ACCESS_VALUE = "signed.access.value"
REFRESH_VALUE = "r" * 43
CSRF_VALUE = "c" * 43
DEVICE_VALUE = "d" * 43
SOURCE_IP = "203.0.113.50"


@dataclass(slots=True)
class StubRateLimiter:
    failure: Exception | None = None
    events: list[str] = field(default_factory=list)
    attempts: list[tuple[str, str]] = field(default_factory=list)

    async def acquire(self, *, source_ip: str, raw_email: str) -> None:
        self.events.append("rate-limit")
        self.attempts.append((source_ip, raw_email))

        if self.failure is not None:
            raise self.failure


@dataclass(slots=True)
class StubLoginService:
    result: EstablishedLoginSession | None = None
    failure: Exception | None = None
    events: list[str] = field(default_factory=list)
    commands: list[AuthenticateCredentialsCommand] = field(default_factory=list)

    async def login(
        self,
        command: AuthenticateCredentialsCommand,
    ) -> EstablishedLoginSession:
        self.events.append("login")
        self.commands.append(command)

        if self.failure is not None:
            raise self.failure
        if self.result is None:
            raise RuntimeError("Login test stub has no result")

        return self.result


def successful_login() -> EstablishedLoginSession:
    return EstablishedLoginSession(
        email=NormalizedEmail("learner@example.com"),
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
        refresh_token=RefreshToken.from_transport(REFRESH_VALUE),
        csrf_token=CsrfToken.from_transport(CSRF_VALUE),
        device_token=DeviceToken.from_transport(DEVICE_VALUE),
    )


@contextmanager
def login_client(
    settings: Settings,
    service: LoginSessionUseCase,
    rate_limiter: LoginAttemptRateLimiter,
) -> Iterator[TestClient]:
    application = create_app(settings=settings)
    application.dependency_overrides[get_login_service] = lambda: service
    application.dependency_overrides[get_login_rate_limiter] = lambda: rate_limiter

    with TestClient(
        application,
        base_url="https://localhost",
        client=(SOURCE_IP, 50_000),
    ) as client:
        yield client


def assert_safe_failure(
    response: HttpxResponse,
    *,
    status_code: int,
    code: str,
) -> None:
    body = response.json()
    assert response.status_code == status_code
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers.get_list("set-cookie") == []
    assert body["status"] == status_code
    assert body["code"] == code
    assert body["trace_id"] == response.headers["X-Trace-ID"]
    assert set(body) == {"type", "title", "status", "detail", "code", "trace_id"}
    assert VALID_RAW_VALUE not in response.text


def test_login_rate_limits_then_returns_only_access_and_secure_cookies(
    test_settings: Settings,
) -> None:
    events: list[str] = []
    rate_limiter = StubRateLimiter(events=events)
    service = StubLoginService(result=successful_login(), events=events)

    with login_client(test_settings, service, rate_limiter) as client:
        response = client.post(
            "/api/v1/auth/login",
            headers={"X-Trace-ID": "untrusted-client-trace"},
            json={"email": "Learner@Example.COM", "password": VALID_RAW_VALUE},
        )

    assert response.status_code == 200
    assert response.json() == {
        "user": {"id": str(USER_ID), "email": "learner@example.com"},
        "access_token": ACCESS_VALUE,
        "token_type": "Bearer",
        "expires_at": "2026-08-11T04:10:00Z",
    }
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert events == ["rate-limit", "login"]
    assert rate_limiter.attempts == [(SOURCE_IP, "Learner@Example.COM")]
    assert len(service.commands) == 1
    assert service.commands[0].password.get_secret_value() == VALID_RAW_VALUE
    assert service.commands[0].trace_id == response.headers["X-Trace-ID"]
    assert response.headers["X-Trace-ID"] != "untrusted-client-trace"

    cookie_headers = {
        value.split("=", 1)[0]: value for value in response.headers.get_list("set-cookie")
    }
    assert set(cookie_headers) == {
        REFRESH_COOKIE_NAME,
        CSRF_COOKIE_NAME,
        DEVICE_COOKIE_NAME,
    }
    assert response.cookies[REFRESH_COOKIE_NAME] == REFRESH_VALUE
    assert response.cookies[CSRF_COOKIE_NAME] == CSRF_VALUE
    assert response.cookies[DEVICE_COOKIE_NAME] == DEVICE_VALUE

    for cookie_name, cookie_header in cookie_headers.items():
        lowered = cookie_header.lower()
        assert "secure" in lowered
        assert "samesite=strict" in lowered
        assert "path=/" in lowered
        assert "domain=" not in lowered
        assert "expires=" in lowered
        if cookie_name == CSRF_COOKIE_NAME:
            assert "httponly" not in lowered
        else:
            assert "httponly" in lowered

    assert "max-age=604800" in cookie_headers[REFRESH_COOKIE_NAME].lower()
    assert "max-age=604800" in cookie_headers[CSRF_COOKIE_NAME].lower()
    assert "max-age=2592000" in cookie_headers[DEVICE_COOKIE_NAME].lower()
    assert ACCESS_VALUE not in " ".join(cookie_headers.values())
    assert ACCESS_VALUE not in repr(LoginResponse.model_validate(response.json()))


@pytest.mark.parametrize(
    ("failure", "status_code", "code"),
    [
        (InvalidCredentialsError(), 401, "INVALID_CREDENTIALS"),
        (AuthenticationPersistenceError(sqlstate="08006"), 503, "LOGIN_UNAVAILABLE"),
        (LoginSessionPersistenceError(sqlstate="40001"), 503, "LOGIN_UNAVAILABLE"),
        (SessionTokenGenerationError(), 503, "LOGIN_UNAVAILABLE"),
        (AccessTokenGenerationError(), 503, "LOGIN_UNAVAILABLE"),
    ],
)
def test_login_failures_are_sanitized_without_setting_cookies(
    test_settings: Settings,
    failure: Exception,
    status_code: int,
    code: str,
) -> None:
    service = StubLoginService(failure=failure)

    with login_client(test_settings, service, StubRateLimiter()) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "learner@example.com", "password": VALID_RAW_VALUE},
        )

    assert_safe_failure(response, status_code=status_code, code=code)
    assert "08006" not in response.text
    assert "40001" not in response.text
    if status_code == 401:
        assert response.headers["www-authenticate"] == "Bearer"


def test_malformed_email_still_uses_limiter_and_generic_credentials_failure(
    test_settings: Settings,
) -> None:
    rate_limiter = StubRateLimiter()
    service = StubLoginService(failure=InvalidCredentialsError())

    with login_client(test_settings, service, rate_limiter) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "not-an-email", "password": VALID_RAW_VALUE},
        )

    assert_safe_failure(response, status_code=401, code="INVALID_CREDENTIALS")
    assert rate_limiter.attempts == [(SOURCE_IP, "not-an-email")]
    assert len(service.commands) == 1


@pytest.mark.parametrize(
    ("failure", "status_code", "code"),
    [
        (LoginRateLimitExceededError(retry_after_seconds=19), 429, "LOGIN_RATE_LIMITED"),
        (LoginRateLimitUnavailableError(), 503, "LOGIN_UNAVAILABLE"),
    ],
)
def test_rate_limit_rejections_happen_before_credential_work(
    test_settings: Settings,
    failure: Exception,
    status_code: int,
    code: str,
) -> None:
    service = StubLoginService(result=successful_login())

    with login_client(test_settings, service, StubRateLimiter(failure=failure)) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "learner@example.com", "password": VALID_RAW_VALUE},
        )

    assert_safe_failure(response, status_code=status_code, code=code)
    assert service.commands == []
    if status_code == 429:
        assert response.headers["retry-after"] == "19"


def test_invalid_login_payload_never_reaches_limiter_or_service(
    test_settings: Settings,
) -> None:
    rate_limiter = StubRateLimiter()
    service = StubLoginService(result=successful_login())

    with login_client(test_settings, service, rate_limiter) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "learner@example.com", "password": "", "extra": True},
        )

    assert_safe_failure(response, status_code=422, code="REQUEST_VALIDATION_FAILED")
    assert rate_limiter.attempts == []
    assert service.commands == []


def test_unexpected_login_failure_hides_sensitive_details(
    test_settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sensitive_detail = "database password and Cookie must not escape"
    service = StubLoginService(failure=RuntimeError(sensitive_detail))
    caplog.set_level(logging.ERROR, logger="industry_platform.core.http")

    with login_client(test_settings, service, StubRateLimiter()) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "learner@example.com", "password": VALID_RAW_VALUE},
        )

    assert_safe_failure(response, status_code=500, code="INTERNAL_SERVER_ERROR")
    assert sensitive_detail not in response.text
    assert sensitive_detail not in caplog.text
    assert "RuntimeError" in caplog.text


def test_openapi_documents_only_problem_json_for_login_errors(
    test_settings: Settings,
) -> None:
    with login_client(
        test_settings,
        StubLoginService(result=successful_login()),
        StubRateLimiter(),
    ) as client:
        document = client.get("/openapi.json").json()

    responses = document["paths"]["/api/v1/auth/login"]["post"]["responses"]
    for status_code in ("401", "422", "429", "500", "503"):
        assert set(responses[status_code]["content"]) == {PROBLEM_MEDIA_TYPE}
