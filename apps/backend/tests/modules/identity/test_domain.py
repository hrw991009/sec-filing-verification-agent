"""Tests for identity application values and safe failures."""

from uuid import UUID

from pydantic import SecretStr

from industry_platform.modules.identity.domain import (
    NormalizedEmail,
    RegisterUserCommand,
    RegistrationPersistenceError,
    RegistrationRecord,
    TraceId,
)

USER_ID = UUID("11111111-1111-4111-8111-111111111111")
WORKSPACE_ID = UUID("22222222-2222-4222-8222-222222222222")


def test_registration_command_does_not_expose_the_password_in_its_representation() -> None:
    plaintext = "correct horse battery staple"
    command = RegisterUserCommand(
        email="member@example.com",
        password=SecretStr(plaintext),
        trace_id=TraceId("registration-trace"),
    )

    assert plaintext not in repr(command)
    assert "password=" not in repr(command)


def test_registration_record_contains_only_the_safe_success_result() -> None:
    record = RegistrationRecord(
        user_id=USER_ID,
        email=NormalizedEmail("member@example.com"),
        workspace_id=WORKSPACE_ID,
        workspace_name="My Workspace",
    )

    assert record.user_id == USER_ID
    assert record.email == "member@example.com"
    assert record.workspace_id == WORKSPACE_ID
    assert record.workspace_name == "My Workspace"
    assert record.workspace_role == "owner"
    assert "password" not in repr(record)


def test_persistence_failure_exposes_only_a_safe_classification() -> None:
    error = RegistrationPersistenceError(sqlstate="23505")

    assert str(error) == "Registration persistence failed"
    assert error.sqlstate == "23505"
    assert "SELECT" not in str(error)
