"""SQLAlchemy persistence adapter for atomic identity registration."""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from psycopg.errors import UniqueViolation
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.identity.domain import (
    EmailAlreadyRegisteredError,
    NormalizedEmail,
    PasswordHash,
    RegistrationPersistenceError,
    RegistrationRecord,
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
from industry_platform.modules.identity.ports import RegistrationWriter

USER_EMAIL_UNIQUE_CONSTRAINT = "uq_users_email"
REGISTRATION_AUDIT_ACTION = "identity.user.registered"


def _is_duplicate_email(error: IntegrityError) -> bool:
    """Recognize only the unique constraint owned by normalized user email."""

    original_error = error.orig

    return (
        isinstance(original_error, UniqueViolation)
        and original_error.diag.constraint_name == USER_EMAIL_UNIQUE_CONSTRAINT
    )


def _safe_sqlstate(error: SQLAlchemyError) -> str | None:
    """Extract a non-sensitive PostgreSQL classification without error text."""

    if not isinstance(error, DBAPIError):
        return None

    sqlstate = getattr(error.orig, "sqlstate", None)
    return sqlstate if isinstance(sqlstate, str) else None


class SqlAlchemyRegistrationRepository:
    """Write one complete registration using the caller-owned transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_registration(
        self,
        *,
        email: NormalizedEmail,
        password_hash: PasswordHash,
        workspace_name: str,
        trace_id: TraceId,
    ) -> RegistrationRecord:
        """Insert user, workspace, owner membership, and sanitized audit."""

        try:
            user = User(
                email=str(email),
                password_hash=str(password_hash),
                status=UserStatus.ACTIVE,
            )
            self._session.add(user)
            await self._session.flush()

            workspace = Workspace(
                name=workspace_name,
                created_by_user_id=user.id,
                status=WorkspaceStatus.ACTIVE,
            )
            self._session.add(workspace)
            await self._session.flush()

            membership = WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=user.id,
                role=WorkspaceRole.OWNER,
            )
            audit_log = AuditLog(
                workspace_id=workspace.id,
                actor_user_id=user.id,
                action=REGISTRATION_AUDIT_ACTION,
                resource_type="user",
                resource_id=user.id,
                outcome=AuditOutcome.SUCCEEDED,
                trace_id=str(trace_id),
                sanitized_metadata={
                    "source": "self_service",
                    "role": WorkspaceRole.OWNER.value,
                },
            )
            self._session.add_all([membership, audit_log])
            await self._session.flush()
        except IntegrityError as error:
            if _is_duplicate_email(error):
                raise EmailAlreadyRegisteredError from None

            raise RegistrationPersistenceError(
                sqlstate=_safe_sqlstate(error),
            ) from None
        except SQLAlchemyError as error:
            raise RegistrationPersistenceError(
                sqlstate=_safe_sqlstate(error),
            ) from None

        return RegistrationRecord(
            user_id=user.id,
            email=NormalizedEmail(user.email),
            workspace_id=workspace.id,
            workspace_name=workspace.name,
        )


class SqlAlchemyRegistrationTransactionFactory:
    """Create one fresh SQLAlchemy session and transaction per registration."""

    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        self._session_factory = session_factory

    def __call__(self) -> AbstractAsyncContextManager[RegistrationWriter]:
        return self._transaction()

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[RegistrationWriter]:
        try:
            async with self._session_factory.begin() as session:
                yield SqlAlchemyRegistrationRepository(session)
        except SQLAlchemyError as error:
            raise RegistrationPersistenceError(
                sqlstate=_safe_sqlstate(error),
            ) from None
