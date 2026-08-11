"""SQLAlchemy persistence adapters for identity workflows."""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from psycopg.errors import UniqueViolation
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.identity.domain import (
    AccountStatus,
    AuthenticationPersistenceError,
    CreateLoginSessionCommand,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    LoginSessionPersistenceError,
    LoginSessionRecord,
    NormalizedEmail,
    PasswordHash,
    RegistrationPersistenceError,
    RegistrationRecord,
    StoredCredentials,
    TraceId,
)
from industry_platform.modules.identity.models import (
    AuditLog,
    AuditOutcome,
    RefreshSession,
    RefreshSessionFamily,
    User,
    UserStatus,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
    WorkspaceStatus,
)
from industry_platform.modules.identity.ports import LoginSessionWriter, RegistrationWriter

USER_EMAIL_UNIQUE_CONSTRAINT = "uq_users_email"
REGISTRATION_AUDIT_ACTION = "identity.user.registered"
LOGIN_AUDIT_ACTION = "identity.session.created"

_ACCOUNT_STATUS_BY_USER_STATUS: dict[UserStatus, AccountStatus] = {
    UserStatus.ACTIVE: "active",
    UserStatus.DISABLED: "disabled",
    UserStatus.DELETING: "deleting",
    UserStatus.DELETED: "deleted",
}


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


class SqlAlchemyCredentialReader:
    """Read one minimal credential snapshot in a short-lived session."""

    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        self._session_factory = session_factory

    async def find_by_email(
        self,
        email: NormalizedEmail,
    ) -> StoredCredentials | None:
        """Find a canonical email without allowing ORM objects to escape."""

        try:
            async with self._session_factory() as session:
                row = (
                    await session.execute(
                        select(
                            User.id,
                            User.email,
                            User.password_hash,
                            User.status,
                        ).where(User.email == str(email))
                    )
                ).one_or_none()
        except SQLAlchemyError as error:
            raise AuthenticationPersistenceError(
                sqlstate=_safe_sqlstate(error),
            ) from None

        if row is None:
            return None

        user_id, stored_email, password_hash, user_status = row

        return StoredCredentials(
            user_id=user_id,
            email=NormalizedEmail(stored_email),
            password_hash=PasswordHash(password_hash),
            status=_ACCOUNT_STATUS_BY_USER_STATUS[user_status],
        )


class SqlAlchemyLoginSessionRepository:
    """Create a refresh-session family after rechecking locked user state."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_login_session(
        self,
        command: CreateLoginSessionCommand,
    ) -> LoginSessionRecord:
        """Persist the complete login state without exposing plaintext tokens."""

        try:
            user = (
                await self._session.scalars(
                    select(User).where(User.id == command.user_id).with_for_update()
                )
            ).one_or_none()

            if (
                user is None
                or user.status is not UserStatus.ACTIVE
                or PasswordHash(user.password_hash) != command.expected_password_hash
            ):
                raise InvalidCredentialsError

            if command.replacement_password_hash is not None:
                user.password_hash = str(command.replacement_password_hash)

            user.last_login_at = command.issued_at
            rotation_family = RefreshSessionFamily(
                user_id=user.id,
                absolute_expires_at=command.absolute_expires_at,
            )
            self._session.add(rotation_family)
            await self._session.flush()

            refresh_session = RefreshSession(
                user_id=user.id,
                rotation_family_id=rotation_family.id,
                token_hash=bytes(command.refresh_token_hash),
                csrf_token_hash=bytes(command.csrf_token_hash),
                device_hash=bytes(command.device_token_hash),
                idle_expires_at=command.idle_expires_at,
                absolute_expires_at=command.absolute_expires_at,
            )
            self._session.add(refresh_session)
            await self._session.flush()

            rotation_family.current_session_id = refresh_session.id
            audit_log = AuditLog(
                actor_user_id=user.id,
                action=LOGIN_AUDIT_ACTION,
                resource_type="refresh_session",
                resource_id=refresh_session.id,
                outcome=AuditOutcome.SUCCEEDED,
                trace_id=str(command.trace_id),
                sanitized_metadata={"authentication_method": "password"},
            )
            self._session.add(audit_log)
            await self._session.flush()
        except SQLAlchemyError as error:
            raise LoginSessionPersistenceError(
                sqlstate=_safe_sqlstate(error),
            ) from None

        return LoginSessionRecord(
            user_id=user.id,
            rotation_family_id=rotation_family.id,
            session_id=refresh_session.id,
            issued_at=command.issued_at,
            idle_expires_at=refresh_session.idle_expires_at,
            absolute_expires_at=rotation_family.absolute_expires_at,
        )


class SqlAlchemyLoginSessionTransactionFactory:
    """Create one fresh SQLAlchemy transaction per successful authentication."""

    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        self._session_factory = session_factory

    def __call__(self) -> AbstractAsyncContextManager[LoginSessionWriter]:
        return self._transaction()

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[LoginSessionWriter]:
        try:
            async with self._session_factory.begin() as session:
                yield SqlAlchemyLoginSessionRepository(session)
        except SQLAlchemyError as error:
            raise LoginSessionPersistenceError(
                sqlstate=_safe_sqlstate(error),
            ) from None


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
