"""PostgreSQL transaction for password replacement and global session revocation."""

import hmac
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from industry_platform.core.database import AsyncSessionFactory, safe_sqlstate
from industry_platform.modules.identity.domain import (
    InvalidAuthenticatedSessionError,
    PasswordChangeConflictError,
    PasswordChangePersistenceError,
    PersistPasswordChangeCommand,
)
from industry_platform.modules.identity.models import (
    AuditLog,
    AuditOutcome,
    RefreshSession,
    RefreshSessionFamily,
    User,
    UserStatus,
)
from industry_platform.modules.identity.ports import PasswordChangeWriter

PASSWORD_CHANGE_AUDIT_ACTION = "identity.password.changed"  # noqa: S105 -- audit event name


class SqlAlchemyPasswordChangeRepository:
    """Apply one password change under the global user→family→session lock order."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def persist_password_change(
        self,
        command: PersistPasswordChangeCommand,
    ) -> None:
        """Recheck proof, replace the hash, revoke all sessions, and audit."""

        user = (
            await self._session.scalars(
                select(User)
                .where(User.id == command.user_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).one_or_none()
        if user is None or user.status is not UserStatus.ACTIVE:
            raise InvalidAuthenticatedSessionError
        if not hmac.compare_digest(
            user.password_hash,
            str(command.expected_password_hash),
        ):
            raise PasswordChangeConflictError

        families = list(
            await self._session.scalars(
                select(RefreshSessionFamily)
                .where(RefreshSessionFamily.user_id == command.user_id)
                .order_by(RefreshSessionFamily.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        sessions = list(
            await self._session.scalars(
                select(RefreshSession)
                .where(RefreshSession.user_id == command.user_id)
                .order_by(RefreshSession.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        authenticated_session = next(
            (item for item in sessions if item.id == command.authenticated_session_id),
            None,
        )
        family_by_id = {family.id: family for family in families}
        authenticated_family = (
            None
            if authenticated_session is None
            else family_by_id.get(authenticated_session.rotation_family_id)
        )
        if (
            authenticated_session is None
            or authenticated_family is None
            or authenticated_session.revoked_at is not None
            or authenticated_session.idle_expires_at <= command.changed_at
            or authenticated_session.absolute_expires_at <= command.changed_at
            or authenticated_family.revoked_at is not None
            or authenticated_family.current_session_id is None
            or authenticated_family.absolute_expires_at <= command.changed_at
        ):
            raise InvalidAuthenticatedSessionError

        user.password_hash = str(command.replacement_password_hash)
        user.password_changed_at = command.changed_at
        for family in families:
            if family.revoked_at is None:
                family.revoked_at = command.changed_at
                family.revocation_reason = "password_changed"
        for refresh_session in sessions:
            if refresh_session.revoked_at is None:
                refresh_session.revoked_at = command.changed_at
                refresh_session.revocation_reason = "password_changed"
            refresh_session.recovery_envelope = None
            refresh_session.recovery_expires_at = None

        self._session.add(
            AuditLog(
                actor_user_id=command.user_id,
                action=PASSWORD_CHANGE_AUDIT_ACTION,
                resource_type="user",
                resource_id=command.user_id,
                outcome=AuditOutcome.SUCCEEDED,
                trace_id=str(command.trace_id),
                sanitized_metadata={
                    "revoked_family_count": len(families),
                    "revoked_session_count": len(sessions),
                },
            )
        )
        await self._session.flush()


class SqlAlchemyPasswordChangeTransactionFactory:
    """Create one fresh SQLAlchemy transaction per password change."""

    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        self._session_factory = session_factory

    def __call__(self) -> AbstractAsyncContextManager[PasswordChangeWriter]:
        return self._transaction()

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[PasswordChangeWriter]:
        try:
            async with self._session_factory.begin() as session:
                yield SqlAlchemyPasswordChangeRepository(session)
        except SQLAlchemyError as error:
            raise PasswordChangePersistenceError(
                sqlstate=safe_sqlstate(error),
            ) from None
