"""Focused tests for the SQLAlchemy credential reader."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.identity.adapters.sqlalchemy import (
    SqlAlchemyCredentialReader,
)
from industry_platform.modules.identity.domain import (
    AuthenticationPersistenceError,
    NormalizedEmail,
    PasswordHash,
)
from industry_platform.modules.identity.models import UserStatus

USER_ID = UUID("44444444-4444-4444-8444-444444444444")
STORED_HASH = PasswordHash("$argon2id$repository-test-value")


def session_factory_returning(
    session: AsyncSession,
) -> tuple[AsyncSessionFactory, MagicMock]:
    """Build one observable async-session context for adapter tests."""

    session_context = MagicMock()
    session_context.__aenter__ = AsyncMock(return_value=session)
    session_context.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=session_context)

    return cast(AsyncSessionFactory, factory), session_context


@pytest.mark.asyncio
async def test_credential_reader_returns_a_detached_minimal_snapshot() -> None:
    session_mock = AsyncMock(spec=AsyncSession)
    result = MagicMock()
    result.one_or_none.return_value = (
        USER_ID,
        "learner@example.com",
        str(STORED_HASH),
        UserStatus.ACTIVE,
    )
    session_mock.execute.return_value = result
    session_factory, session_context = session_factory_returning(cast(AsyncSession, session_mock))
    reader = SqlAlchemyCredentialReader(session_factory)

    record = await reader.find_by_email(NormalizedEmail("learner@example.com"))

    assert record is not None
    assert record.user_id == USER_ID
    assert record.email == NormalizedEmail("learner@example.com")
    assert record.password_hash == STORED_HASH
    assert record.status == "active"
    assert str(STORED_HASH) not in repr(record)
    session_mock.execute.assert_awaited_once()
    session_context.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_credential_reader_returns_none_and_closes_the_session() -> None:
    session_mock = AsyncMock(spec=AsyncSession)
    result = MagicMock()
    result.one_or_none.return_value = None
    session_mock.execute.return_value = result
    session_factory, session_context = session_factory_returning(cast(AsyncSession, session_mock))
    reader = SqlAlchemyCredentialReader(session_factory)

    record = await reader.find_by_email(NormalizedEmail("missing@example.com"))

    assert record is None
    session_context.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_credential_reader_sanitizes_database_failures() -> None:
    session_mock = AsyncMock(spec=AsyncSession)
    sensitive_detail = "SELECT password_hash FROM users WHERE email='private@example.com'"
    session_mock.execute.side_effect = SQLAlchemyError(sensitive_detail)
    session_factory, session_context = session_factory_returning(cast(AsyncSession, session_mock))
    reader = SqlAlchemyCredentialReader(session_factory)

    with pytest.raises(AuthenticationPersistenceError) as exc_info:
        await reader.find_by_email(NormalizedEmail("private@example.com"))

    assert str(exc_info.value) == "Authentication persistence failed"
    assert exc_info.value.sqlstate is None
    assert sensitive_detail not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True
    session_context.__aexit__.assert_awaited_once()
