"""Tests for bounded expired refresh-recovery maintenance."""

from collections.abc import Callable
from dataclasses import FrozenInstanceError
from types import TracebackType
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy import Select, Update
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Dialect

from industry_platform.modules.identity.adapters.refresh_cleanup import (
    SqlAlchemyRefreshRecoveryCleanupRepository,
)
from industry_platform.modules.identity.domain import (
    RefreshRecoveryCleanupCommand,
    RefreshRecoveryCleanupPersistenceError,
    RefreshRecoveryCleanupResult,
    RefreshRecoveryCleanupUnavailableError,
)
from industry_platform.modules.identity.service import RefreshRecoveryCleanupService

FIRST_SESSION_ID = UUID("11111111-1111-4111-8111-111111111111")
SECOND_SESSION_ID = UUID("22222222-2222-4222-8222-222222222222")


def postgresql_dialect() -> Dialect:
    """Cross SQLAlchemy's dynamically typed dialect factory at one test boundary."""

    factory = cast(Callable[[], Dialect], postgresql.dialect)
    return factory()


class StatementRecordingSession:
    """Capture SQLAlchemy statements without requiring a database."""

    def __init__(self, *session_ids: UUID) -> None:
        self.session_ids = session_ids
        self.statements: list[object] = []

    async def scalars(self, statement: object) -> tuple[UUID, ...]:
        self.statements.append(statement)
        return self.session_ids

    async def execute(self, statement: object) -> None:
        self.statements.append(statement)


class RecordingWriter:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.commands: list[RefreshRecoveryCleanupCommand] = []

    async def clear_expired(
        self,
        command: RefreshRecoveryCleanupCommand,
    ) -> RefreshRecoveryCleanupResult:
        self.events.append("writer.clear")
        self.commands.append(command)
        return RefreshRecoveryCleanupResult(scanned_count=2, cleared_count=2)


class RecordingTransaction:
    def __init__(
        self,
        events: list[str],
        writer: RecordingWriter,
        *,
        commit_failure: Exception | None = None,
    ) -> None:
        self.events = events
        self.writer = writer
        self.commit_failure = commit_failure

    async def __aenter__(self) -> RecordingWriter:
        self.events.append("transaction.enter")
        return self.writer

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_value, traceback
        if exc_type is not None:
            self.events.append("transaction.rollback")
            return
        if self.commit_failure is not None:
            self.events.append("transaction.commit_failed")
            raise self.commit_failure
        self.events.append("transaction.commit")


class RecordingTransactionFactory:
    def __init__(
        self,
        events: list[str],
        writer: RecordingWriter,
        *,
        commit_failure: Exception | None = None,
    ) -> None:
        self.events = events
        self.writer = writer
        self.commit_failure = commit_failure

    def __call__(self) -> RecordingTransaction:
        self.events.append("transaction.create")
        return RecordingTransaction(
            self.events,
            self.writer,
            commit_failure=self.commit_failure,
        )


def test_cleanup_command_enforces_the_closed_batch_range() -> None:
    assert RefreshRecoveryCleanupCommand(batch_size=1).batch_size == 1
    assert RefreshRecoveryCleanupCommand(batch_size=1000).batch_size == 1000

    for batch_size in (0, 1001, -1, True):
        with pytest.raises(ValueError, match="batch size must be between 1 and 1000"):
            RefreshRecoveryCleanupCommand(batch_size=batch_size)


def test_cleanup_result_is_immutable_and_rejects_impossible_counts() -> None:
    result = RefreshRecoveryCleanupResult(scanned_count=3, cleared_count=2)

    with pytest.raises(FrozenInstanceError):
        result.__setattr__("cleared_count", 1)
    with pytest.raises(ValueError, match="counts are inconsistent"):
        RefreshRecoveryCleanupResult(scanned_count=1, cleared_count=2)


@pytest.mark.asyncio
async def test_repository_builds_a_bounded_database_clock_skip_locked_cleanup() -> None:
    session = StatementRecordingSession(FIRST_SESSION_ID, SECOND_SESSION_ID)
    repository = SqlAlchemyRefreshRecoveryCleanupRepository(session)  # type: ignore[arg-type]

    result = await repository.clear_expired(RefreshRecoveryCleanupCommand(batch_size=2))

    assert result == RefreshRecoveryCleanupResult(scanned_count=2, cleared_count=2)
    assert len(session.statements) == 2
    select_statement = cast(Select[tuple[UUID]], session.statements[0])
    update_statement = cast(Update, session.statements[1])
    select_sql = str(
        select_statement.compile(
            dialect=postgresql_dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    update_sql = str(
        update_statement.compile(
            dialect=postgresql_dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert list(select_statement.selected_columns.keys()) == ["id"]
    assert "clock_timestamp()" in select_sql
    assert "ORDER BY refresh_sessions.id ASC" in select_sql
    assert "LIMIT 2" in select_sql
    assert "FOR UPDATE SKIP LOCKED" in select_sql
    assert "recovery_envelope=NULL" in update_sql
    assert "recovery_expires_at=NULL" in update_sql


@pytest.mark.asyncio
async def test_cleanup_returns_only_after_commit() -> None:
    events: list[str] = []
    writer = RecordingWriter(events)
    service = RefreshRecoveryCleanupService(
        transaction_factory=RecordingTransactionFactory(events, writer),
    )
    command = RefreshRecoveryCleanupCommand(batch_size=17)

    result = await service.cleanup_expired(command)
    events.append("caller.returned")

    assert result == RefreshRecoveryCleanupResult(scanned_count=2, cleared_count=2)
    assert writer.commands == [command]
    assert events == [
        "transaction.create",
        "transaction.enter",
        "writer.clear",
        "transaction.commit",
        "caller.returned",
    ]


@pytest.mark.asyncio
async def test_commit_failure_exposes_only_the_safe_sqlstate() -> None:
    events: list[str] = []
    writer = RecordingWriter(events)
    service = RefreshRecoveryCleanupService(
        transaction_factory=RecordingTransactionFactory(
            events,
            writer,
            commit_failure=RefreshRecoveryCleanupPersistenceError(sqlstate="40001"),
        ),
    )

    with pytest.raises(RefreshRecoveryCleanupUnavailableError) as exc_info:
        await service.cleanup_expired(RefreshRecoveryCleanupCommand(batch_size=25))

    assert exc_info.value.sqlstate == "40001"
    assert str(exc_info.value) == "Refresh recovery cleanup unavailable"
    assert exc_info.value.__cause__ is None
    assert events[-1] == "transaction.commit_failed"
