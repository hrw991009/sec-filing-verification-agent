"""HTTP contract tests for browser-bound logout."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from industry_platform.core.config import Settings
from industry_platform.core.http import PROBLEM_MEDIA_TYPE
from industry_platform.main import create_app
from industry_platform.modules.identity.domain import (
    InvalidLogoutSessionError,
    LogoutSessionCommand,
    LogoutSessionUnavailableError,
)
from industry_platform.modules.identity.http_cookies import (
    CSRF_COOKIE_NAME,
    DEVICE_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
)
from industry_platform.modules.identity.ports import LogoutSessionUseCase
from industry_platform.modules.identity.router import get_logout_service

TRUSTED_ORIGIN = "https://localhost:5173"
REFRESH_VALUE = "r" * 43
CSRF_VALUE = "c" * 43
DEVICE_VALUE = "d" * 43
CSRF_HEADER_NAME = "X-CSRF-Token"


@dataclass(slots=True)
class StubLogoutService:
    failure: Exception | None = None
    commands: list[LogoutSessionCommand] = field(default_factory=list)

    async def logout(self, command: LogoutSessionCommand) -> None:
        self.commands.append(command)
        if self.failure is not None:
            raise self.failure


@contextmanager
def logout_client(
    settings: Settings,
    service: LogoutSessionUseCase,
) -> Iterator[TestClient]:
    application = create_app(settings=settings)
    application.dependency_overrides[get_logout_service] = lambda: service
    with TestClient(application, base_url="https://localhost") as client:
        yield client


def install_cookies(client: TestClient) -> None:
    client.cookies.set(REFRESH_COOKIE_NAME, REFRESH_VALUE, domain="localhost.local", path="/")
    client.cookies.set(CSRF_COOKIE_NAME, CSRF_VALUE, domain="localhost.local", path="/")
    client.cookies.set(DEVICE_COOKIE_NAME, DEVICE_VALUE, domain="localhost.local", path="/")


def request_headers() -> dict[str, str]:
    return {
        "Origin": TRUSTED_ORIGIN,
        CSRF_HEADER_NAME: CSRF_VALUE,
    }


def test_successful_logout_clears_every_browser_cookie_after_commit(
    test_settings: Settings,
) -> None:
    service = StubLogoutService()

    with logout_client(test_settings, service) as client:
        install_cookies(client)
        response = client.post("/api/v1/auth/logout", headers=request_headers())
        assert client.cookies.get(REFRESH_COOKIE_NAME) is None
        assert client.cookies.get(CSRF_COOKIE_NAME) is None
        assert client.cookies.get(DEVICE_COOKIE_NAME) is None

    assert response.status_code == 204
    assert response.content == b""
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["access-control-allow-origin"] == TRUSTED_ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"
    cookie_headers = response.headers.get_list("set-cookie")
    assert len(cookie_headers) == 3
    assert all("Max-Age=0" in header for header in cookie_headers)
    assert len(service.commands) == 1
    command = service.commands[0]
    assert command.origin == TRUSTED_ORIGIN
    assert command.refresh_token.reveal_for_transport() == REFRESH_VALUE
    assert command.csrf_cookie_value == CSRF_VALUE
    assert command.csrf_header_value == CSRF_VALUE
    assert command.device_token.reveal_for_transport() == DEVICE_VALUE
    assert str(command.trace_id) == response.headers["X-Trace-ID"]


def test_invalid_logout_proof_preserves_existing_cookies(
    test_settings: Settings,
) -> None:
    service = StubLogoutService(failure=InvalidLogoutSessionError())

    with logout_client(test_settings, service) as client:
        install_cookies(client)
        response = client.post("/api/v1/auth/logout", headers=request_headers())
        assert client.cookies.get(REFRESH_COOKIE_NAME) == REFRESH_VALUE
        assert client.cookies.get(CSRF_COOKIE_NAME) == CSRF_VALUE
        assert client.cookies.get(DEVICE_COOKIE_NAME) == DEVICE_VALUE
        assert client.cookies.get(CSRF_COOKIE_NAME) == CSRF_VALUE
        assert client.cookies.get(DEVICE_COOKIE_NAME) == DEVICE_VALUE

    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_LOGOUT_SESSION"
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert response.headers.get_list("set-cookie") == []
    assert REFRESH_VALUE not in response.text


def test_logout_infrastructure_failure_preserves_retry_credentials(
    test_settings: Settings,
) -> None:
    service = StubLogoutService(failure=LogoutSessionUnavailableError(sqlstate="40001"))

    with logout_client(test_settings, service) as client:
        install_cookies(client)
        response = client.post("/api/v1/auth/logout", headers=request_headers())
        assert client.cookies.get(REFRESH_COOKIE_NAME) == REFRESH_VALUE

    assert response.status_code == 503
    assert response.json()["code"] == "LOGOUT_UNAVAILABLE"
    assert response.headers.get_list("set-cookie") == []
    assert "40001" not in response.text


def test_unexpected_logout_failure_is_sanitized_and_preserves_cookies(
    test_settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sensitive_detail = "logout credential must not reach logs"
    service = StubLogoutService(failure=RuntimeError(sensitive_detail))
    caplog.set_level(logging.ERROR, logger="industry_platform.core.http")

    with logout_client(test_settings, service) as client:
        install_cookies(client)
        response = client.post("/api/v1/auth/logout", headers=request_headers())
        assert client.cookies.get(REFRESH_COOKIE_NAME) == REFRESH_VALUE
        assert client.cookies.get(CSRF_COOKIE_NAME) == CSRF_VALUE
        assert client.cookies.get(DEVICE_COOKIE_NAME) == DEVICE_VALUE

    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_SERVER_ERROR"
    assert response.headers.get_list("set-cookie") == []
    assert sensitive_detail not in response.text
    assert sensitive_detail not in caplog.text
    assert REFRESH_VALUE not in response.text
    assert REFRESH_VALUE not in caplog.text


def test_missing_browser_proofs_use_the_same_logout_rejection(
    test_settings: Settings,
) -> None:
    service = StubLogoutService(failure=InvalidLogoutSessionError())

    with logout_client(test_settings, service) as client:
        response = client.post("/api/v1/auth/logout")

    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_LOGOUT_SESSION"
    assert len(service.commands) == 1
    command = service.commands[0]
    assert command.origin == ""
    assert command.refresh_token.reveal_for_transport() == ""
    assert command.csrf_cookie_value == ""
    assert command.csrf_header_value == ""
    assert command.device_token.reveal_for_transport() == ""


def test_openapi_documents_logout_problem_contracts(test_settings: Settings) -> None:
    service = StubLogoutService()

    with logout_client(test_settings, service) as client:
        document = client.get("/openapi.json").json()

    responses = document["paths"]["/api/v1/auth/logout"]["post"]["responses"]
    assert "204" in responses
    for status_code in ("401", "500", "503"):
        assert set(responses[status_code]["content"]) == {PROBLEM_MEDIA_TYPE}
