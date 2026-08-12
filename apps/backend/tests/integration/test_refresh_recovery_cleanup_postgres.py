"""Exercise bounded refresh recovery-envelope cleanup in PostgreSQL."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from industry_platform.core.database import (
    AsyncSessionFactory,
    create_database_engine,
    create_database_session_factory,
)
from industry_platform.modules.identity.adapters.refresh_cleanup import (
    SqlAlchemyRefreshRecoveryCleanupTransactionFactory,
)
from industry_platform.modules.identity.domain import (
    RefreshRecoveryCleanupCommand,
    RefreshRecoveryCleanupResult,
    RefreshRecoveryCleanupUnavailableError,
)
from industry_platform.modules.identity.models import (
    RefreshSession,
    RefreshSessionFamily,
    User,
    UserStatus,
)
from industry_platform.modules.identity.ports import (
    RefreshRecoveryCleanupTransactionFactory,
    RefreshRecoveryCleanupWriter,
)
from industry_platform.modules.identity.service import RefreshRecoveryCleanupService
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe

SENSITIVE_ENVELOPE = b"recovery-ciphertext-must-not-escape"


class PausingCleanupWriter:
    """Hold the first worker's locks until a concurrent worker has committed."""

    def __init__(
        self,
        writer: RefreshRecoveryCleanupWriter,
        claimed: asyncio.Event,
        release: asyncio.Event,
    ) -> None:
        self._writer = writer
        self._claimed = claimed
        self._release = release

    async def clear_expired(
        self,
        command: RefreshRecoveryCleanupCommand,
    ) -> RefreshRecoveryCleanupResult:
        result = await self._writer.clear_expired(command)
        self._claimed.set()
        await self._release.wait()
        return result


class PausingCleanupTransactionFactory:
    def __init__(
        self,
        transaction_factory: RefreshRecoveryCleanupTransactionFactory,
        claimed: asyncio.Event,
        release: asyncio.Event,
    ) -> None:
        self._transaction_factory = transaction_factory
        self._claimed = claimed
        self._release = release

    def __call__(self) -> AbstractAsyncContextManager[RefreshRecoveryCleanupWriter]:
        return self._transaction()

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[RefreshRecoveryCleanupWriter]:
        async with self._transaction_factory() as writer:
            yield PausingCleanupWriter(writer, self._claimed, self._release)


class SensitiveDriverError(Exception):
    sqlstate = "40001"


class FailingCleanupWriter:
    """Raise a database failure after UPDATE so the real transaction rolls back."""

    def __init__(self, writer: RefreshRecoveryCleanupWriter) -> None:
        self._writer = writer

    async def clear_expired(
        self,
        command: RefreshRecoveryCleanupCommand,
    ) -> RefreshRecoveryCleanupResult:
        await self._writer.clear_expired(command)
        raise DBAPIError(
            "UPDATE refresh_sessions",
            {"recovery_envelope": SENSITIVE_ENVELOPE},
            SensitiveDriverError(SENSITIVE_ENVELOPE.decode("ascii")),
            False,
        )


class FailingCleanupTransactionFactory:
    def __init__(
        self,
        transaction_factory: RefreshRecoveryCleanupTransactionFactory,
    ) -> None:
        self._transaction_factory = transaction_factory

    def __call__(self) -> AbstractAsyncContextManager[RefreshRecoveryCleanupWriter]:
        return self._transaction()

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[RefreshRecoveryCleanupWriter]:
        async with self._transaction_factory() as writer:
            yield FailingCleanupWriter(writer)


async def seed_recovery_chain(
    session_factory: AsyncSessionFactory,
    *,
    user_id: UUID,
    family_id: UUID,
    session_ids: tuple[UUID, ...],
    recovery_expiry_offsets: tuple[timedelta, ...],
    recovery_envelopes: tuple[bytes, ...] | None = None,
) -> datetime:
    """Insert one valid rotation chain using PostgreSQL's current wall clock."""

    if len(session_ids) != len(recovery_expiry_offsets) + 1:
        raise ValueError("A recovery expiry is required for every predecessor")
    if recovery_envelopes is None:
        recovery_envelopes = tuple(
            f"encrypted-successor-{index}".encode("ascii")
            for index in range(len(recovery_expiry_offsets))
        )
    if len(recovery_envelopes) != len(recovery_expiry_offsets):
        raise ValueError("A recovery envelope is required for every expiry")

    async with session_factory.begin() as session:
        database_now = await session.scalar(select(func.clock_timestamp()))
        if not isinstance(database_now, datetime):
            raise RuntimeError("PostgreSQL did not return its current timestamp")

        user = User(
            id=user_id,
            email=f"cleanup-{user_id}@example.com",
            password_hash=str(user_id),
            status=UserStatus.ACTIVE,
        )
        session.add(user)
        await session.flush()

        absolute_expires_at = database_now + timedelta(days=30)
        family = RefreshSessionFamily(
            id=family_id,
            user_id=user_id,
            absolute_expires_at=absolute_expires_at,
        )
        session.add(family)
        await session.flush()

        refresh_sessions: list[RefreshSession] = []
        for index, session_id in enumerate(session_ids):
            refresh_session = RefreshSession(
                id=session_id,
                user_id=user_id,
                rotation_family_id=family_id,
                token_hash=bytes([index + 1]) * 32,
                csrf_token_hash=bytes([index + 65]) * 32,
                device_hash=b"d" * 32,
                previous_session_id=None if index == 0 else session_ids[index - 1],
                idle_expires_at=database_now + timedelta(days=7),
                absolute_expires_at=absolute_expires_at,
            )
            session.add(refresh_session)
            await session.flush()
            refresh_sessions.append(refresh_session)

        for index, refresh_session in enumerate(refresh_sessions[:-1]):
            refresh_session.used_at = database_now - timedelta(minutes=1)
            refresh_session.replaced_by_session_id = session_ids[index + 1]
            refresh_session.recovery_envelope = recovery_envelopes[index]
            refresh_session.recovery_expires_at = database_now + recovery_expiry_offsets[index]

        family.current_session_id = session_ids[-1]

    return database_now


def cleanup_service(
    session_factory: AsyncSessionFactory,
) -> RefreshRecoveryCleanupService:
    return RefreshRecoveryCleanupService(
        transaction_factory=SqlAlchemyRefreshRecoveryCleanupTransactionFactory(session_factory),
    )


def test_cleanup_uses_database_expiry_order_and_batch_limit(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        user_id = UUID("00000000-0000-4000-8000-000000000101")
        family_id = UUID("00000000-0000-4000-8000-000000000201")
        session_ids = tuple(
            UUID(f"00000000-0000-4000-8000-{value:012d}") for value in range(301, 306)
        )

        try:
            await seed_recovery_chain(
                session_factory,
                user_id=user_id,
                family_id=family_id,
                session_ids=session_ids,
                recovery_expiry_offsets=(
                    -timedelta(minutes=3),
                    -timedelta(minutes=2),
                    timedelta(0),
                    timedelta(minutes=10),
                ),
            )
            service = cleanup_service(session_factory)

            first_result = await service.cleanup_expired(
                RefreshRecoveryCleanupCommand(batch_size=2)
            )

            assert first_result == RefreshRecoveryCleanupResult(
                scanned_count=2,
                cleared_count=2,
            )
            async with session_factory() as session:
                rows_after_first = list(
                    await session.scalars(
                        select(RefreshSession)
                        .where(RefreshSession.user_id == user_id)
                        .order_by(RefreshSession.id)
                    )
                )

            assert [row.id for row in rows_after_first[:2]] == list(session_ids[:2])
            assert all(row.recovery_envelope is None for row in rows_after_first[:2])
            assert rows_after_first[2].recovery_envelope is not None
            assert rows_after_first[3].recovery_envelope is not None

            second_result = await service.cleanup_expired(
                RefreshRecoveryCleanupCommand(batch_size=1000)
            )

            assert second_result == RefreshRecoveryCleanupResult(
                scanned_count=1,
                cleared_count=1,
            )
            async with session_factory() as session:
                rows_after_second = list(
                    await session.scalars(
                        select(RefreshSession)
                        .where(RefreshSession.user_id == user_id)
                        .order_by(RefreshSession.id)
                    )
                )

            assert rows_after_second[2].recovery_envelope is None
            assert rows_after_second[3].recovery_envelope is not None
            assert rows_after_second[3].recovery_expires_at is not None
            assert all(
                (row.recovery_envelope is None) == (row.recovery_expires_at is None)
                for row in rows_after_second
            )
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())


def test_two_cleanup_workers_skip_locked_batches_and_converge(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        user_id = UUID("00000000-0000-4000-8000-000000000102")
        family_id = UUID("00000000-0000-4000-8000-000000000202")
        session_ids = tuple(
            UUID(f"00000000-0000-4000-8000-{value:012d}") for value in range(401, 408)
        )

        try:
            await seed_recovery_chain(
                session_factory,
                user_id=user_id,
                family_id=family_id,
                session_ids=session_ids,
                recovery_expiry_offsets=(-timedelta(minutes=1),) * 6,
            )
            claimed = asyncio.Event()
            release = asyncio.Event()
            base_factory = SqlAlchemyRefreshRecoveryCleanupTransactionFactory(session_factory)
            first_service = RefreshRecoveryCleanupService(
                transaction_factory=PausingCleanupTransactionFactory(
                    base_factory,
                    claimed,
                    release,
                )
            )
            second_service = RefreshRecoveryCleanupService(
                transaction_factory=base_factory,
            )
            command = RefreshRecoveryCleanupCommand(batch_size=3)

            async def run_second_worker() -> RefreshRecoveryCleanupResult:
                await claimed.wait()
                try:
                    return await second_service.cleanup_expired(command)
                finally:
                    release.set()

            async with asyncio.timeout(30):
                first_result, second_result = await asyncio.gather(
                    first_service.cleanup_expired(command),
                    run_second_worker(),
                )

            assert first_result.cleared_count == 3
            assert second_result.cleared_count == 3
            async with session_factory() as session:
                remaining_count = await session.scalar(
                    select(func.count())
                    .select_from(RefreshSession)
                    .where(
                        RefreshSession.user_id == user_id,
                        RefreshSession.recovery_envelope.is_not(None),
                    )
                )

            assert remaining_count == 0
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())


def test_cleanup_failure_rolls_back_and_does_not_expose_ciphertext(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        user_id = UUID("00000000-0000-4000-8000-000000000103")
        family_id = UUID("00000000-0000-4000-8000-000000000203")
        session_ids = (
            UUID("00000000-0000-4000-8000-000000000501"),
            UUID("00000000-0000-4000-8000-000000000502"),
        )

        try:
            await seed_recovery_chain(
                session_factory,
                user_id=user_id,
                family_id=family_id,
                session_ids=session_ids,
                recovery_expiry_offsets=(-timedelta(minutes=1),),
                recovery_envelopes=(SENSITIVE_ENVELOPE,),
            )
            base_factory = SqlAlchemyRefreshRecoveryCleanupTransactionFactory(session_factory)
            service = RefreshRecoveryCleanupService(
                transaction_factory=FailingCleanupTransactionFactory(base_factory),
            )

            with pytest.raises(RefreshRecoveryCleanupUnavailableError) as exc_info:
                await service.cleanup_expired(RefreshRecoveryCleanupCommand(batch_size=1))

            sensitive_text = SENSITIVE_ENVELOPE.decode("ascii")
            assert exc_info.value.sqlstate == "40001"
            assert sensitive_text not in str(exc_info.value)
            assert sensitive_text not in repr(exc_info.value)
            assert exc_info.value.__cause__ is None

            async with session_factory() as session:
                rolled_back = await session.get(RefreshSession, session_ids[0])

            assert rolled_back is not None
            assert rolled_back.recovery_envelope == SENSITIVE_ENVELOPE
            assert rolled_back.recovery_expires_at is not None
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())
