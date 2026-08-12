"""PostgreSQL cleanup for expired refresh recovery envelopes."""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from industry_platform.core.database import AsyncSessionFactory, safe_sqlstate
from industry_platform.modules.identity.domain import (
    RefreshRecoveryCleanupCommand,
    RefreshRecoveryCleanupPersistenceError,
    RefreshRecoveryCleanupResult,
)
from industry_platform.modules.identity.models import RefreshSession
from industry_platform.modules.identity.ports import RefreshRecoveryCleanupWriter


class SqlAlchemyRefreshRecoveryCleanupRepository:
    """Claim and clear one stable UUID-ordered batch without reading ciphertext."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def clear_expired(
        self,
        command: RefreshRecoveryCleanupCommand,
    ) -> RefreshRecoveryCleanupResult:
        """Use PostgreSQL's clock and skip rows already claimed by another worker."""

        session_ids = tuple(
            await self._session.scalars(
                select(RefreshSession.id)
                .where(
                    RefreshSession.recovery_envelope.is_not(None),
                    RefreshSession.recovery_expires_at.is_not(None),
                    RefreshSession.recovery_expires_at <= func.clock_timestamp(),
                )
                .order_by(RefreshSession.id.asc())
                .limit(command.batch_size)
                .with_for_update(skip_locked=True)
            )
        )

        if session_ids:
            await self._session.execute(
                update(RefreshSession)
                .where(RefreshSession.id.in_(session_ids))
                .values(
                    recovery_envelope=None,
                    recovery_expires_at=None,
                )
            )

        cleared_count = len(session_ids)
        return RefreshRecoveryCleanupResult(
            scanned_count=cleared_count,
            cleared_count=cleared_count,
        )


class SqlAlchemyRefreshRecoveryCleanupTransactionFactory:
    """Create one fresh SQLAlchemy transaction per cleanup batch."""

    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        self._session_factory = session_factory

    def __call__(self) -> AbstractAsyncContextManager[RefreshRecoveryCleanupWriter]:
        return self._transaction()

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[RefreshRecoveryCleanupWriter]:
        try:
            async with self._session_factory.begin() as session:
                yield SqlAlchemyRefreshRecoveryCleanupRepository(session)
        except SQLAlchemyError as error:
            raise RefreshRecoveryCleanupPersistenceError(
                sqlstate=safe_sqlstate(error),
            ) from None
