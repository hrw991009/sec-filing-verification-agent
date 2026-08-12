"""HTTP contract and security tests for account registration."""

import logging
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
    EmailAlreadyRegisteredError,
    NormalizedEmail,
    RegisterUserCommand,
    RegistrationPersistenceError,
    RegistrationRecord,
)
from industry_platform.modules.identity.ports import RegistrationUseCase
from industry_platform.modules.identity.router import get_registration_service

USER_ID = UUID("11111111-1111-4111-8111-111111111111")
WORKSPACE_ID = UUID("22222222-2222-4222-8222-222222222222")
VALID_RAW_VALUE = "correct-horse-battery-staple"


@dataclass(slots=True)
class StubRegistrationService:
    """Controllable registration use case used only at the HTTP boundary."""

    result: RegistrationRecord | None = None
    failure: Exception | None = None
    commands: list[RegisterUserCommand] = field(default_factory=list)

    async def register(self, command: RegisterUserCommand) -> RegistrationRecord:
        self.commands.append(command)

        if self.failure is not None:
            raise self.failure
        if self.result is None:
            raise RuntimeError("Registration test stub has no result")

        return self.result


def successful_record() -> RegistrationRecord:
    return RegistrationRecord(
        user_id=USER_ID,
        email=NormalizedEmail("learner@example.com"),
        workspace_id=WORKSPACE_ID,
        workspace_name="My Workspace",
    )


@contextmanager
def registration_client(
    settings: Settings,
    service: RegistrationUseCase,
) -> Iterator[TestClient]:
    application = create_app(settings=settings)

    def override_registration_service() -> RegistrationUseCase:
        return service

    application.dependency_overrides[get_registration_service] = override_registration_service

    with TestClient(application) as client:
        yield client


def assert_problem_response(
    response_status: int,
    response_content_type: str,
    response_trace_id: str,
    response_body: dict[str, object],
    *,
    expected_code: str,
) -> None:
    assert response_status == response_body["status"]
    assert response_content_type == PROBLEM_MEDIA_TYPE
    assert response_body["code"] == expected_code
    assert response_body["trace_id"] == response_trace_id
    assert_valid_trace_id(response_trace_id)
    assert set(response_body) == {
        "type",
        "title",
        "status",
        "detail",
        "code",
        "trace_id",
    }


def assert_valid_trace_id(trace_id: str) -> None:
    assert len(trace_id) == 32
    assert UUID(hex=trace_id).hex == trace_id


def test_registration_returns_only_safe_created_resources(
    test_settings: Settings,
) -> None:
    service = StubRegistrationService(result=successful_record())

    with registration_client(test_settings, service) as client:
        response = client.post(
            "/api/v1/auth/register",
            headers={"X-Trace-ID": "untrusted-client-value"},
            json={
                "email": "Learner@Example.COM",
                "password": VALID_RAW_VALUE,
            },
        )

    assert response.status_code == 201
    assert response.json() == {
        "user": {
            "id": str(USER_ID),
            "email": "learner@example.com",
        },
        "workspace": {
            "id": str(WORKSPACE_ID),
            "name": "My Workspace",
            "role": "owner",
        },
    }
    assert VALID_RAW_VALUE not in response.text
    assert response.headers["X-Trace-ID"] != "untrusted-client-value"
    assert_valid_trace_id(response.headers["X-Trace-ID"])
    assert len(service.commands) == 1
    assert service.commands[0].trace_id == response.headers["X-Trace-ID"]


def test_duplicate_email_returns_a_sanitized_conflict(
    test_settings: Settings,
) -> None:
    service = StubRegistrationService(failure=EmailAlreadyRegisteredError())

    with registration_client(test_settings, service) as client:
        response = client.post(
            "/api/v1/auth/register",
            json={"email": "learner@example.com", "password": VALID_RAW_VALUE},
        )

    body = response.json()
    assert_problem_response(
        response.status_code,
        response.headers["content-type"],
        response.headers["X-Trace-ID"],
        body,
        expected_code="EMAIL_ALREADY_REGISTERED",
    )
    assert response.status_code == 409
    assert VALID_RAW_VALUE not in response.text
    assert "uq_users_email" not in response.text
    assert "IntegrityError" not in response.text


def test_invalid_password_is_rejected_before_the_service(
    test_settings: Settings,
) -> None:
    service = StubRegistrationService(result=successful_record())
    invalid_raw_value = "too-short"

    with registration_client(test_settings, service) as client:
        response = client.post(
            "/api/v1/auth/register",
            json={"email": "learner@example.com", "password": invalid_raw_value},
        )

    assert_problem_response(
        response.status_code,
        response.headers["content-type"],
        response.headers["X-Trace-ID"],
        response.json(),
        expected_code="REQUEST_VALIDATION_FAILED",
    )
    assert response.status_code == 422
    assert invalid_raw_value not in response.text
    assert service.commands == []


def test_persistence_failure_hides_database_details(
    test_settings: Settings,
) -> None:
    service = StubRegistrationService(failure=RegistrationPersistenceError(sqlstate="23514"))

    with registration_client(test_settings, service) as client:
        response = client.post(
            "/api/v1/auth/register",
            json={"email": "learner@example.com", "password": VALID_RAW_VALUE},
        )

    assert_problem_response(
        response.status_code,
        response.headers["content-type"],
        response.headers["X-Trace-ID"],
        response.json(),
        expected_code="REGISTRATION_UNAVAILABLE",
    )
    assert response.status_code == 503
    assert "23514" not in response.text
    assert VALID_RAW_VALUE not in response.text


def test_unexpected_failure_is_sanitized_in_response_and_log(
    test_settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sensitive_detail = "database password=do-not-expose"
    service = StubRegistrationService(failure=RuntimeError(sensitive_detail))
    caplog.set_level(logging.ERROR, logger="industry_platform.core.http")

    with registration_client(test_settings, service) as client:
        response = client.post(
            "/api/v1/auth/register",
            json={"email": "learner@example.com", "password": VALID_RAW_VALUE},
        )

    assert_problem_response(
        response.status_code,
        response.headers["content-type"],
        response.headers["X-Trace-ID"],
        response.json(),
        expected_code="INTERNAL_SERVER_ERROR",
    )
    assert response.status_code == 500
    assert sensitive_detail not in response.text
    assert sensitive_detail not in caplog.text
    assert "RuntimeError" in caplog.text


def test_framework_404_and_405_use_the_problem_contract(
    test_settings: Settings,
) -> None:
    service = StubRegistrationService(result=successful_record())

    with registration_client(test_settings, service) as client:
        missing_response = client.get("/does-not-exist")
        method_response = client.get("/api/v1/auth/register")

    assert_problem_response(
        missing_response.status_code,
        missing_response.headers["content-type"],
        missing_response.headers["X-Trace-ID"],
        missing_response.json(),
        expected_code="RESOURCE_NOT_FOUND",
    )
    assert missing_response.status_code == 404
    assert_problem_response(
        method_response.status_code,
        method_response.headers["content-type"],
        method_response.headers["X-Trace-ID"],
        method_response.json(),
        expected_code="METHOD_NOT_ALLOWED",
    )
    assert method_response.status_code == 405
    assert "POST" in method_response.headers["allow"]


def test_openapi_documents_only_problem_json_for_registration_errors(
    test_settings: Settings,
) -> None:
    service = StubRegistrationService(result=successful_record())

    with registration_client(test_settings, service) as client:
        document = client.get("/openapi.json").json()

    responses = document["paths"]["/api/v1/auth/register"]["post"]["responses"]

    for status_code in ("409", "422", "500", "503"):
        assert set(responses[status_code]["content"]) == {PROBLEM_MEDIA_TYPE}
