"""Prove login-session creation and stale-proof rejection in PostgreSQL."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from industry_platform.core.database import (
    create_database_engine,
    create_database_session_factory,
)
from industry_platform.modules.identity.adapters.sqlalchemy import (
    LOGIN_AUDIT_ACTION,
    SqlAlchemyLoginSessionTransactionFactory,
    SqlAlchemyRegistrationTransactionFactory,
)
from industry_platform.modules.identity.domain import (
    CreateLoginSessionCommand,
    CsrfTokenHash,
    DeviceTokenHash,
    InvalidCredentialsError,
    PasswordHash,
    RefreshTokenHash,
    RegisterUserCommand,
    TraceId,
)
from industry_platform.modules.identity.models import (
    AuditLog,
    AuditOutcome,
    RefreshSession,
    RefreshSessionFamily,
    User,
)
from industry_platform.modules.identity.passwords import ValidatedPassword
from industry_platform.modules.identity.service import RegistrationService
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe

ORIGINAL_HASH = PasswordHash("$argon2id$login-session-original")
REPLACEMENT_HASH = PasswordHash("$argon2id$login-session-replacement")


class FixedPasswordHasher:
    """Keep this database transaction test independent from Argon2 cost."""

    async def hash(self, password: ValidatedPassword) -> PasswordHash:
        del password
        return ORIGINAL_HASH

    async def verify(
        self,
        password_hash: PasswordHash,
        password: SecretStr,
    ) -> bool:
        del password_hash, password
        raise AssertionError("This test starts after credential verification")

    async def needs_rehash(self, password_hash: PasswordHash) -> bool:
        del password_hash
        raise AssertionError("This test starts after credential verification")


def login_session_command(
    *,
    user_id: UUID,
    expected_password_hash: PasswordHash = ORIGINAL_HASH,
) -> CreateLoginSessionCommand:
    """Build deterministic token hashes for inspecting persisted rows."""

    issued_at = datetime.now(UTC)
    return CreateLoginSessionCommand(
        user_id=user_id,
        expected_password_hash=expected_password_hash,
        replacement_password_hash=REPLACEMENT_HASH,
        refresh_token_hash=RefreshTokenHash(b"r" * 32),
        csrf_token_hash=CsrfTokenHash(b"c" * 32),
        device_token_hash=DeviceTokenHash(b"d" * 32),
        issued_at=issued_at,
        idle_expires_at=issued_at + timedelta(days=7),
        absolute_expires_at=issued_at + timedelta(days=30),
        trace_id=TraceId("login-session-postgres-trace"),
    )


def test_login_session_is_atomic_and_rejects_a_stale_password_proof(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    """Commit one session, then prove an outdated hash cannot create another."""

    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        registration_service = RegistrationService(
            password_hasher=FixedPasswordHasher(),
            transaction_factory=SqlAlchemyRegistrationTransactionFactory(session_factory),
        )
        login_transaction_factory = SqlAlchemyLoginSessionTransactionFactory(session_factory)

        try:
            registration = await registration_service.register(
                RegisterUserCommand(
                    email="session-owner@example.com",
                    password=SecretStr("registration-only-raw-value"),
                    trace_id=TraceId("login-session-registration-trace"),
                )
            )

            async with session_factory() as session:
                registered_user = await session.get(User, registration.user_id)

            assert registered_user is not None
            original_password_changed_at = registered_user.password_changed_at
            first_command = login_session_command(user_id=registration.user_id)

            async with login_transaction_factory() as writer:
                record = await writer.create_login_session(first_command)

            async with session_factory() as session:
                user = await session.get(User, registration.user_id)
                family = await session.get(
                    RefreshSessionFamily,
                    record.rotation_family_id,
                )
                refresh_session = await session.get(RefreshSession, record.session_id)
                login_audit = (
                    await session.scalars(
                        select(AuditLog).where(AuditLog.action == LOGIN_AUDIT_ACTION)
                    )
                ).one()

            assert user is not None
            assert family is not None
            assert refresh_session is not None
            assert user.password_hash == REPLACEMENT_HASH
            assert user.password_changed_at == original_password_changed_at
            assert user.last_login_at == first_command.issued_at
            assert family.user_id == registration.user_id
            assert family.current_session_id == refresh_session.id
            assert family.absolute_expires_at == first_command.absolute_expires_at
            assert refresh_session.rotation_family_id == family.id
            assert refresh_session.user_id == registration.user_id
            assert refresh_session.token_hash == b"r" * 32
            assert refresh_session.csrf_token_hash == b"c" * 32
            assert refresh_session.device_hash == b"d" * 32
            assert refresh_session.idle_expires_at == first_command.idle_expires_at
            assert login_audit.actor_user_id == registration.user_id
            assert login_audit.resource_id == refresh_session.id
            assert login_audit.outcome is AuditOutcome.SUCCEEDED
            assert login_audit.trace_id == first_command.trace_id
            assert login_audit.sanitized_metadata == {"authentication_method": "password"}

            stale_command = login_session_command(
                user_id=registration.user_id,
                expected_password_hash=ORIGINAL_HASH,
            )

            with pytest.raises(InvalidCredentialsError):
                async with login_transaction_factory() as writer:
                    await writer.create_login_session(stale_command)

            async with session_factory() as session:
                session_count = (
                    await session.execute(select(func.count()).select_from(RefreshSession))
                ).scalar_one()
                family_count = (
                    await session.execute(select(func.count()).select_from(RefreshSessionFamily))
                ).scalar_one()

            assert session_count == 1
            assert family_count == 1
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())
