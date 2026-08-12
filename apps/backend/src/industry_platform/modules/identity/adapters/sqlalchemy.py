"""SQLAlchemy persistence adapters for identity workflows."""

import hmac
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import datetime

from psycopg.errors import UniqueViolation
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from industry_platform.core.database import AsyncSessionFactory, safe_sqlstate
from industry_platform.modules.identity.domain import (
    AccountStatus,
    AuthenticationPersistenceError,
    CreateLoginSessionCommand,
    CsrfTokenHash,
    DeviceTokenHash,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidRefreshSessionError,
    LockedRefreshRotation,
    LockedRefreshSessionState,
    LoginSessionPersistenceError,
    LoginSessionRecord,
    NormalizedEmail,
    PasswordHash,
    PersistRefreshSuccessorCommand,
    RecordRefreshRecoveryCommand,
    RefreshRecoveryEnvelope,
    RefreshSessionPersistenceError,
    RefreshTokenHash,
    RegistrationPersistenceError,
    RegistrationRecord,
    RevokeRefreshFamilyCommand,
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
from industry_platform.modules.identity.ports import (
    LoginSessionWriter,
    RefreshSessionWriter,
    RegistrationWriter,
)

USER_EMAIL_UNIQUE_CONSTRAINT = "uq_users_email"
REGISTRATION_AUDIT_ACTION = "identity.user.registered"
LOGIN_AUDIT_ACTION = "identity.session.created"
REFRESH_AUDIT_ACTION = "identity.session.refreshed"
REFRESH_FAMILY_REVOCATION_AUDIT_ACTION = "identity.session.family_revoked"
REFRESH_RECOVERY_AUDIT_ACTION = "identity.session.refresh_recovered"
REFRESH_REPLAY_AUDIT_ACTION = "identity.session.refresh_replay_detected"
LOGOUT_AUDIT_ACTION = "identity.session.logged_out"

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
                sqlstate=safe_sqlstate(error),
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
                sqlstate=safe_sqlstate(error),
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
                sqlstate=safe_sqlstate(error),
            ) from None


class SqlAlchemyRefreshSessionRepository:
    """Lock and mutate one refresh rotation family without handling plaintext."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_rotation(
        self,
        refresh_token_hash: RefreshTokenHash,
    ) -> LockedRefreshRotation:
        """Lock User, family, then relevant session rows and recheck the hint."""

        candidate_hint = (
            await self._session.scalars(
                select(RefreshSession).where(RefreshSession.token_hash == bytes(refresh_token_hash))
            )
        ).one_or_none()
        if candidate_hint is None:
            raise InvalidRefreshSessionError

        candidate_id = candidate_hint.id
        user_id = candidate_hint.user_id
        family_id = candidate_hint.rotation_family_id

        user = (
            await self._session.scalars(
                select(User)
                .where(User.id == user_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).one_or_none()
        if user is None:
            raise InvalidRefreshSessionError

        family = (
            await self._session.scalars(
                select(RefreshSessionFamily)
                .where(
                    RefreshSessionFamily.id == family_id,
                    RefreshSessionFamily.user_id == user_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).one_or_none()
        if family is None or family.current_session_id is None:
            raise InvalidRefreshSessionError

        locked_ids = sorted(
            {candidate_id, family.current_session_id},
            key=lambda value: value.int,
        )
        locked_sessions = list(
            await self._session.scalars(
                select(RefreshSession)
                .where(
                    RefreshSession.id.in_(locked_ids),
                    RefreshSession.rotation_family_id == family.id,
                    RefreshSession.user_id == user.id,
                )
                .order_by(RefreshSession.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        sessions_by_id = {
            refresh_session.id: refresh_session for refresh_session in locked_sessions
        }
        candidate = sessions_by_id.get(candidate_id)
        current = sessions_by_id.get(family.current_session_id)

        if (
            candidate is None
            or current is None
            or not hmac.compare_digest(
                candidate.token_hash,
                bytes(refresh_token_hash),
            )
        ):
            raise InvalidRefreshSessionError

        checked_at = await self._session.scalar(select(func.clock_timestamp()))
        if not isinstance(checked_at, datetime):
            raise RefreshSessionPersistenceError

        return LockedRefreshRotation(
            user_status=_ACCOUNT_STATUS_BY_USER_STATUS[user.status],
            family_id=family.id,
            family_current_session_id=family.current_session_id,
            family_absolute_expires_at=family.absolute_expires_at,
            family_revoked_at=family.revoked_at,
            checked_at=checked_at,
            presented=self._snapshot(candidate),
            current=self._snapshot(current),
        )

    async def persist_successor(
        self,
        command: PersistRefreshSuccessorCommand,
    ) -> LoginSessionRecord:
        """Insert the successor, consume predecessor, move pointer, and audit."""

        predecessor = await self._session.get(
            RefreshSession,
            command.predecessor_session_id,
        )
        family = await self._session.get(
            RefreshSessionFamily,
            command.rotation_family_id,
        )
        if (
            predecessor is None
            or family is None
            or predecessor.user_id != command.user_id
            or predecessor.rotation_family_id != command.rotation_family_id
            or family.user_id != command.user_id
            or family.current_session_id != predecessor.id
            or predecessor.used_at is not None
            or predecessor.replaced_by_session_id is not None
        ):
            raise InvalidRefreshSessionError

        successor = RefreshSession(
            id=command.successor_session_id,
            user_id=command.user_id,
            rotation_family_id=command.rotation_family_id,
            token_hash=bytes(command.refresh_token_hash),
            csrf_token_hash=bytes(command.csrf_token_hash),
            device_hash=bytes(command.device_token_hash),
            previous_session_id=command.predecessor_session_id,
            idle_expires_at=command.idle_expires_at,
            absolute_expires_at=command.absolute_expires_at,
        )
        self._session.add(successor)
        await self._session.flush()

        predecessor.used_at = command.issued_at
        predecessor.replaced_by_session_id = successor.id
        predecessor.recovery_envelope = bytes(command.recovery_envelope)
        predecessor.recovery_expires_at = command.recovery_expires_at
        family.current_session_id = successor.id
        self._session.add(
            AuditLog(
                actor_user_id=command.user_id,
                action=REFRESH_AUDIT_ACTION,
                resource_type="refresh_session",
                resource_id=successor.id,
                outcome=AuditOutcome.SUCCEEDED,
                trace_id=str(command.trace_id),
                sanitized_metadata={"mode": "rotated"},
            )
        )
        await self._session.flush()

        return LoginSessionRecord(
            user_id=command.user_id,
            rotation_family_id=command.rotation_family_id,
            session_id=successor.id,
            issued_at=command.issued_at,
            idle_expires_at=successor.idle_expires_at,
            absolute_expires_at=successor.absolute_expires_at,
        )

    async def record_recovery(
        self,
        command: RecordRefreshRecoveryCommand,
    ) -> None:
        """Audit a successful recovery without changing or extending its envelope."""

        self._session.add(
            AuditLog(
                actor_user_id=command.user_id,
                action=REFRESH_RECOVERY_AUDIT_ACTION,
                resource_type="refresh_session",
                resource_id=command.session_id,
                outcome=AuditOutcome.SUCCEEDED,
                trace_id=str(command.trace_id),
                sanitized_metadata={"mode": "lost_response_recovery"},
            )
        )
        await self._session.flush()

    async def revoke_family(
        self,
        command: RevokeRefreshFamilyCommand,
    ) -> None:
        """Revoke every generation and clear recoverable ciphertext atomically."""

        family = await self._session.get(
            RefreshSessionFamily,
            command.rotation_family_id,
        )
        if family is None or family.user_id != command.user_id:
            raise InvalidRefreshSessionError

        family_sessions = list(
            await self._session.scalars(
                select(RefreshSession)
                .where(RefreshSession.rotation_family_id == command.rotation_family_id)
                .order_by(RefreshSession.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        if family.revoked_at is None:
            family.revoked_at = command.revoked_at
            family.revocation_reason = command.reason

        for refresh_session in family_sessions:
            if refresh_session.revoked_at is None:
                refresh_session.revoked_at = command.revoked_at
                refresh_session.revocation_reason = command.reason
            refresh_session.recovery_envelope = None
            refresh_session.recovery_expires_at = None

        if command.reason == "refresh_replay_detected":
            audit_action = REFRESH_REPLAY_AUDIT_ACTION
            audit_outcome = AuditOutcome.DENIED
        elif command.reason == "logout":
            audit_action = LOGOUT_AUDIT_ACTION
            audit_outcome = AuditOutcome.SUCCEEDED
        else:
            audit_action = REFRESH_FAMILY_REVOCATION_AUDIT_ACTION
            audit_outcome = AuditOutcome.SUCCEEDED

        self._session.add(
            AuditLog(
                actor_user_id=command.user_id,
                action=audit_action,
                resource_type="refresh_session_family",
                resource_id=command.rotation_family_id,
                outcome=audit_outcome,
                trace_id=str(command.trace_id),
                sanitized_metadata={"reason": command.reason},
            )
        )
        await self._session.flush()

    @staticmethod
    def _snapshot(refresh_session: RefreshSession) -> LockedRefreshSessionState:
        recovery_envelope = (
            None
            if refresh_session.recovery_envelope is None
            else RefreshRecoveryEnvelope(refresh_session.recovery_envelope)
        )
        return LockedRefreshSessionState(
            user_id=refresh_session.user_id,
            rotation_family_id=refresh_session.rotation_family_id,
            session_id=refresh_session.id,
            previous_session_id=refresh_session.previous_session_id,
            replaced_by_session_id=refresh_session.replaced_by_session_id,
            refresh_token_hash=RefreshTokenHash(refresh_session.token_hash),
            csrf_token_hash=CsrfTokenHash(refresh_session.csrf_token_hash),
            device_token_hash=DeviceTokenHash(refresh_session.device_hash),
            idle_expires_at=refresh_session.idle_expires_at,
            absolute_expires_at=refresh_session.absolute_expires_at,
            used_at=refresh_session.used_at,
            revoked_at=refresh_session.revoked_at,
            recovery_envelope=recovery_envelope,
            recovery_expires_at=refresh_session.recovery_expires_at,
        )


class SqlAlchemyRefreshSessionTransactionFactory:
    """Create one fresh SQLAlchemy transaction for each refresh attempt."""

    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        self._session_factory = session_factory

    def __call__(self) -> AbstractAsyncContextManager[RefreshSessionWriter]:
        return self._transaction()

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[RefreshSessionWriter]:
        try:
            async with self._session_factory.begin() as session:
                yield SqlAlchemyRefreshSessionRepository(session)
        except SQLAlchemyError as error:
            raise RefreshSessionPersistenceError(
                sqlstate=safe_sqlstate(error),
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
                sqlstate=safe_sqlstate(error),
            ) from None
        except SQLAlchemyError as error:
            raise RegistrationPersistenceError(
                sqlstate=safe_sqlstate(error),
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
                sqlstate=safe_sqlstate(error),
            ) from None
