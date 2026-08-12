"""HTTP contracts for authenticated password replacement."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from industry_platform.core.config import Settings
from industry_platform.core.http import PROBLEM_MEDIA_TYPE
from industry_platform.main import create_app
from industry_platform.modules.identity.domain import (
    AccessToken,
    AuthenticatedPrincipal,
    ChangePasswordCommand,
    InvalidCurrentPasswordError,
    LoginRateLimitExceededError,
    LoginRateLimitUnavailableError,
    NewPasswordMatchesCurrentError,
    NormalizedEmail,
    PasswordChangeConflictError,
    PasswordChangePersistenceError,
)
from industry_platform.modules.identity.http_auth import get_principal_resolver
from industry_platform.modules.identity.http_cookies import (
    CSRF_COOKIE_NAME,
    DEVICE_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
)
from industry_platform.modules.identity.ports import (
    AuthenticatedPrincipalResolver,
    LoginAttemptRateLimiter,
    PasswordChangeUseCase,
)
from industry_platform.modules.identity.router import (
    get_password_change_rate_limiter,
    get_password_change_service,
)

USER_ID = UUID("11111111-1111-4111-8111-111111111111")
SESSION_ID = UUID("22222222-2222-4222-8222-222222222222")
ACCESS_VALUE = ".".join(("header", "payload", "signature"))
CURRENT_RAW_VALUE = "current-horse-battery-staple"
NEW_RAW_VALUE = "replacement-horse-battery-staple"
REFRESH_VALUE = "r" * 43
CSRF_VALUE = "c" * 43
DEVICE_VALUE = "d" * 43
TRUSTED_ORIGIN = "https://localhost:5173"


@dataclass(slots=True)
class StubPrincipalResolver:
    async def resolve(self, token: AccessToken) -> AuthenticatedPrincipal:
        assert token.reveal_for_transport() == ACCESS_VALUE
        return AuthenticatedPrincipal(
            user_id=USER_ID,
            session_id=SESSION_ID,
            email=NormalizedEmail("learner@example.com"),
            workspaces=(),
        )


@dataclass(slots=True)
class StubPasswordChangeService:
    failure: Exception | None = None
    commands: list[ChangePasswordCommand] = field(default_factory=list)

    async def change_password(self, command: ChangePasswordCommand) -> None:
        self.commands.append(command)
        if self.failure is not None:
            raise self.failure


@dataclass(slots=True)
class StubRateLimiter:
    failure: Exception | None = None
    attempts: list[tuple[str, str]] = field(default_factory=list)

    async def acquire(self, *, source_ip: str, raw_email: str) -> None:
        self.attempts.append((source_ip, raw_email))
        if self.failure is not None:
            raise self.failure


@contextmanager
def password_change_client(
    settings: Settings,
    service: PasswordChangeUseCase,
    limiter: LoginAttemptRateLimiter,
) -> Iterator[TestClient]:
    application = create_app(settings=settings)
    resolver: AuthenticatedPrincipalResolver = StubPrincipalResolver()
    application.dependency_overrides[get_principal_resolver] = lambda: resolver
    application.dependency_overrides[get_password_change_service] = lambda: service
    application.dependency_overrides[get_password_change_rate_limiter] = lambda: limiter
    with TestClient(
        application,
        base_url="https://localhost",
        client=("203.0.113.80", 50_000),
    ) as client:
        yield client


def install_cookies(client: TestClient) -> None:
    client.cookies.set(REFRESH_COOKIE_NAME, REFRESH_VALUE, domain="localhost.local", path="/")
    client.cookies.set(CSRF_COOKIE_NAME, CSRF_VALUE, domain="localhost.local", path="/")
    client.cookies.set(DEVICE_COOKIE_NAME, DEVICE_VALUE, domain="localhost.local", path="/")


def request_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {ACCESS_VALUE}",
        "Origin": TRUSTED_ORIGIN,
    }


def request_body(*, new_raw_value: str = NEW_RAW_VALUE) -> dict[str, str]:
    return {
        "current_password": CURRENT_RAW_VALUE,
        "new_password": new_raw_value,
    }


def assert_cookies_preserved(client: TestClient) -> None:
    assert client.cookies.get(REFRESH_COOKIE_NAME) == REFRESH_VALUE
    assert client.cookies.get(CSRF_COOKIE_NAME) == CSRF_VALUE
    assert client.cookies.get(DEVICE_COOKIE_NAME) == DEVICE_VALUE


def test_success_clears_cookies_only_after_service_commit(
    test_settings: Settings,
) -> None:
    service = StubPasswordChangeService()
    limiter = StubRateLimiter()

    with password_change_client(test_settings, service, limiter) as client:
        install_cookies(client)
        response = client.post(
            "/api/v1/auth/change-password",
            headers=request_headers(),
            json=request_body(),
        )
        assert client.cookies.get(REFRESH_COOKIE_NAME) is None
        assert client.cookies.get(CSRF_COOKIE_NAME) is None
        assert client.cookies.get(DEVICE_COOKIE_NAME) is None

    assert response.status_code == 204
    assert response.content == b""
    assert response.headers["cache-control"] == "no-store"
    assert len(response.headers.get_list("set-cookie")) == 3
    assert limiter.attempts == [("203.0.113.80", "learner@example.com")]
    command = service.commands[0]
    assert command.user_id == USER_ID
    assert command.session_id == SESSION_ID
    assert command.origin == TRUSTED_ORIGIN
    assert command.current_password.get_secret_value() == CURRENT_RAW_VALUE
    assert command.new_password.get_secret_value() == NEW_RAW_VALUE
    assert str(command.trace_id) == response.headers["X-Trace-ID"]


@pytest.mark.parametrize(
    ("failure", "status_code", "code"),
    [
        (InvalidCurrentPasswordError(), 400, "INVALID_CURRENT_PASSWORD"),
        (NewPasswordMatchesCurrentError(), 422, "REQUEST_VALIDATION_FAILED"),
        (PasswordChangePersistenceError(sqlstate="40001"), 503, "PASSWORD_CHANGE_UNAVAILABLE"),
    ],
)
def test_rejected_or_retryable_change_preserves_browser_credentials(
    test_settings: Settings,
    failure: Exception,
    status_code: int,
    code: str,
) -> None:
    service = StubPasswordChangeService(failure=failure)

    with password_change_client(test_settings, service, StubRateLimiter()) as client:
        install_cookies(client)
        response = client.post(
            "/api/v1/auth/change-password",
            headers=request_headers(),
            json=request_body(),
        )
        assert_cookies_preserved(client)

    assert response.status_code == status_code
    assert response.json()["code"] == code
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert response.headers.get_list("set-cookie") == []
    assert CURRENT_RAW_VALUE not in response.text
    assert NEW_RAW_VALUE not in response.text


def test_concurrent_change_conflict_requires_sign_in_again(
    test_settings: Settings,
) -> None:
    service = StubPasswordChangeService(failure=PasswordChangeConflictError())

    with password_change_client(test_settings, service, StubRateLimiter()) as client:
        install_cookies(client)
        response = client.post(
            "/api/v1/auth/change-password",
            headers=request_headers(),
            json=request_body(),
        )

    assert response.status_code == 409
    assert response.json()["code"] == "PASSWORD_CHANGE_CONFLICT"
    assert len(response.headers.get_list("set-cookie")) == 3


@pytest.mark.parametrize(
    ("failure", "status_code", "code"),
    [
        (
            LoginRateLimitExceededError(retry_after_seconds=37),
            429,
            "PASSWORD_CHANGE_RATE_LIMITED",
        ),
        (
            LoginRateLimitUnavailableError(),
            503,
            "PASSWORD_CHANGE_UNAVAILABLE",
        ),
    ],
)
def test_rate_limit_failure_is_closed_before_password_verification(
    test_settings: Settings,
    failure: Exception,
    status_code: int,
    code: str,
) -> None:
    service = StubPasswordChangeService()
    limiter = StubRateLimiter(failure=failure)

    with password_change_client(test_settings, service, limiter) as client:
        install_cookies(client)
        response = client.post(
            "/api/v1/auth/change-password",
            headers=request_headers(),
            json=request_body(),
        )
        assert_cookies_preserved(client)

    assert response.status_code == status_code
    assert response.json()["code"] == code
    assert response.headers.get_list("set-cookie") == []
    assert service.commands == []
    if status_code == 429:
        assert response.headers["retry-after"] == "37"


def test_weak_replacement_is_422_before_the_service(
    test_settings: Settings,
) -> None:
    service = StubPasswordChangeService()

    with password_change_client(test_settings, service, StubRateLimiter()) as client:
        response = client.post(
            "/api/v1/auth/change-password",
            headers=request_headers(),
            json=request_body(new_raw_value="short"),
        )

    assert response.status_code == 422
    assert response.json()["code"] == "REQUEST_VALIDATION_FAILED"
    assert service.commands == []
    assert "short" not in response.text


def test_openapi_documents_security_and_all_password_change_errors(
    test_settings: Settings,
) -> None:
    with password_change_client(
        test_settings,
        StubPasswordChangeService(),
        StubRateLimiter(),
    ) as client:
        document = client.get("/openapi.json").json()

    operation = document["paths"]["/api/v1/auth/change-password"]["post"]
    assert operation["security"] == [{"AccessToken": []}]
    for status_code in ("400", "401", "403", "409", "422", "429", "500", "503"):
        assert set(operation["responses"][status_code]["content"]) == {PROBLEM_MEDIA_TYPE}
