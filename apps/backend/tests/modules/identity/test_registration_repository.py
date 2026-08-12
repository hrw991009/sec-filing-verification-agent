"""Focused tests for the SQLAlchemy registration adapter."""

from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from industry_platform.modules.identity.adapters.sqlalchemy import (
    REGISTRATION_AUDIT_ACTION,
    SqlAlchemyRegistrationRepository,
)
from industry_platform.modules.identity.domain import (
    NormalizedEmail,
    PasswordHash,
    RegistrationPersistenceError,
    TraceId,
)
from industry_platform.modules.identity.models import (
    AuditLog,
    AuditOutcome,
    User,
    UserStatus,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
    WorkspaceStatus,
)

USER_ID = UUID("11111111-1111-4111-8111-111111111111")
WORKSPACE_ID = UUID("22222222-2222-4222-8222-222222222222")
PASSWORD_HASH = PasswordHash("$argon2id$unit-test-encoded-value")


@pytest.mark.asyncio
async def test_repository_builds_the_complete_registration_without_committing() -> None:
    session_mock = AsyncMock(spec=AsyncSession)
    flush_number = 0

    async def assign_database_generated_ids() -> None:
        nonlocal flush_number
        flush_number += 1

        if flush_number == 1:
            user = session_mock.add.call_args_list[0].args[0]
            user.id = USER_ID
        elif flush_number == 2:
            workspace = session_mock.add.call_args_list[1].args[0]
            workspace.id = WORKSPACE_ID

    session_mock.flush.side_effect = assign_database_generated_ids
    repository = SqlAlchemyRegistrationRepository(
        cast(AsyncSession, session_mock),
    )

    record = await repository.create_registration(
        email=NormalizedEmail("member@example.com"),
        password_hash=PASSWORD_HASH,
        workspace_name="My Workspace",
        trace_id=TraceId("registration-repository-trace"),
    )

    user = session_mock.add.call_args_list[0].args[0]
    workspace = session_mock.add.call_args_list[1].args[0]
    membership, audit_log = session_mock.add_all.call_args.args[0]

    assert isinstance(user, User)
    assert user.id == USER_ID
    assert user.email == "member@example.com"
    assert user.password_hash == PASSWORD_HASH
    assert user.status is UserStatus.ACTIVE

    assert isinstance(workspace, Workspace)
    assert workspace.id == WORKSPACE_ID
    assert workspace.created_by_user_id == USER_ID
    assert workspace.status is WorkspaceStatus.ACTIVE

    assert isinstance(membership, WorkspaceMembership)
    assert membership.workspace_id == WORKSPACE_ID
    assert membership.user_id == USER_ID
    assert membership.role is WorkspaceRole.OWNER

    assert isinstance(audit_log, AuditLog)
    assert audit_log.workspace_id == WORKSPACE_ID
    assert audit_log.actor_user_id == USER_ID
    assert audit_log.action == REGISTRATION_AUDIT_ACTION
    assert audit_log.resource_type == "user"
    assert audit_log.resource_id == USER_ID
    assert audit_log.outcome is AuditOutcome.SUCCEEDED
    assert audit_log.trace_id == "registration-repository-trace"
    assert audit_log.sanitized_metadata == {
        "source": "self_service",
        "role": "owner",
    }

    assert session_mock.flush.await_count == 3
    session_mock.commit.assert_not_called()
    assert record.user_id == USER_ID
    assert record.workspace_id == WORKSPACE_ID
    assert record.workspace_role == "owner"


@pytest.mark.asyncio
async def test_repository_replaces_database_details_with_a_safe_failure() -> None:
    session_mock = AsyncMock(spec=AsyncSession)
    sensitive_detail = "INSERT INTO users VALUES ('private@example.com', 'secret')"
    session_mock.flush.side_effect = SQLAlchemyError(sensitive_detail)
    repository = SqlAlchemyRegistrationRepository(
        cast(AsyncSession, session_mock),
    )

    with pytest.raises(RegistrationPersistenceError) as exc_info:
        await repository.create_registration(
            email=NormalizedEmail("private@example.com"),
            password_hash=PASSWORD_HASH,
            workspace_name="My Workspace",
            trace_id=TraceId("registration-failure-trace"),
        )

    assert str(exc_info.value) == "Registration persistence failed"
    assert exc_info.value.sqlstate is None
    assert sensitive_detail not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True
