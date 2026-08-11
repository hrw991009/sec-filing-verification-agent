"""Exercise complete login-session orchestration against PostgreSQL."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest
from anyio import CapacityLimiter
from argon2 import PasswordHasher as Argon2Engine
from argon2.low_level import Type
from pydantic import SecretStr
from sqlalchemy import func, select

from industry_platform.core.database import (
    create_database_engine,
    create_database_session_factory,
)
from industry_platform.modules.identity.adapters.access_tokens import (
    Ed25519AccessTokenCodec,
)
from industry_platform.modules.identity.adapters.argon2 import Argon2idPasswordHasher
from industry_platform.modules.identity.adapters.session_tokens import (
    HmacSessionTokenService,
)
from industry_platform.modules.identity.adapters.sqlalchemy import (
    LOGIN_AUDIT_ACTION,
    SqlAlchemyCredentialReader,
    SqlAlchemyLoginSessionTransactionFactory,
    SqlAlchemyRegistrationTransactionFactory,
)
from industry_platform.modules.identity.domain import (
    AccessTokenGenerationError,
    AuthenticateCredentialsCommand,
    InvalidCredentialsError,
    PasswordHash,
    RegisterUserCommand,
    TraceId,
)
from industry_platform.modules.identity.models import (
    AuditLog,
    RefreshSession,
    RefreshSessionFamily,
    User,
)
from industry_platform.modules.identity.passwords import ValidatedPassword
from industry_platform.modules.identity.service import (
    CredentialAuthenticationService,
    LoginSessionService,
    RegistrationService,
)
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe

RAW_VALUE = "correct horse battery staple"
LOGIN_AT = datetime(2026, 8, 11, 4, 0, tzinfo=UTC)


class LegacyRegistrationHasher:
    """Persist one real legacy Argon2 hash before the login upgrade path."""

    def __init__(self, encoded_hash: PasswordHash) -> None:
        self.encoded_hash = encoded_hash

    async def hash(self, password: ValidatedPassword) -> PasswordHash:
        assert password.reveal() == RAW_VALUE
        return self.encoded_hash

    async def verify(
        self,
        password_hash: PasswordHash,
        password: SecretStr,
    ) -> bool:
        del password_hash, password
        raise AssertionError("Registration must not verify a password")

    async def needs_rehash(self, password_hash: PasswordHash) -> bool:
        del password_hash
        raise AssertionError("Registration must not inspect hash parameters")


def test_login_commits_credentials_tokens_and_audit_atomically(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    legacy_engine = Argon2Engine(
        time_cost=2,
        memory_cost=32_768,
        parallelism=1,
        hash_len=16,
        salt_len=16,
        type=Type.ID,
    )
    legacy_hash = PasswordHash(legacy_engine.hash(RAW_VALUE))

    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        current_hasher = Argon2idPasswordHasher(
            migrated_postgres_probe.settings,
            limiter=CapacityLimiter(1),
        )
        registration_service = RegistrationService(
            password_hasher=LegacyRegistrationHasher(legacy_hash),
            transaction_factory=SqlAlchemyRegistrationTransactionFactory(session_factory),
        )
        authentication_service = CredentialAuthenticationService(
            password_hasher=current_hasher,
            credential_reader=SqlAlchemyCredentialReader(session_factory),
            dummy_password_hash=legacy_hash,
        )
        token_service = HmacSessionTokenService(
            refresh_hmac_key=migrated_postgres_probe.settings.refresh_token_hmac_key,
            csrf_hmac_key=migrated_postgres_probe.settings.csrf_token_hmac_key,
            device_hmac_key=migrated_postgres_probe.settings.device_token_hmac_key,
        )
        access_token_codec = Ed25519AccessTokenCodec(
            current_kid=migrated_postgres_probe.settings.access_token_current_kid,
            private_key=migrated_postgres_probe.settings.access_token_private_key,
            public_keys=dict(migrated_postgres_probe.settings.access_token_public_keys),
        )
        failing_access_token_codec = Ed25519AccessTokenCodec(
            current_kid=migrated_postgres_probe.settings.access_token_current_kid,
            private_key=migrated_postgres_probe.settings.access_token_private_key,
            public_keys=dict(migrated_postgres_probe.settings.access_token_public_keys),
            jti_source=lambda: UUID(int=0),
        )
        login_service = LoginSessionService(
            authentication_service=authentication_service,
            password_rehasher=current_hasher,
            session_token_service=token_service,
            access_token_codec=access_token_codec,
            transaction_factory=SqlAlchemyLoginSessionTransactionFactory(session_factory),
            clock=lambda: LOGIN_AT,
        )
        rollback_probe_service = LoginSessionService(
            authentication_service=authentication_service,
            password_rehasher=current_hasher,
            session_token_service=token_service,
            access_token_codec=failing_access_token_codec,
            transaction_factory=SqlAlchemyLoginSessionTransactionFactory(session_factory),
            clock=lambda: LOGIN_AT,
        )

        try:
            registration = await registration_service.register(
                RegisterUserCommand(
                    email="login-owner@example.com",
                    password=SecretStr(RAW_VALUE),
                    trace_id=TraceId("login-service-registration-trace"),
                )
            )

            async with session_factory() as session:
                user_before_login = await session.get(User, registration.user_id)

            assert user_before_login is not None
            password_changed_at = user_before_login.password_changed_at

            with pytest.raises(AccessTokenGenerationError):
                await rollback_probe_service.login(
                    AuthenticateCredentialsCommand(
                        email="login-owner@example.com",
                        password=SecretStr(RAW_VALUE),
                        trace_id=TraceId("login-service-signing-rollback-trace"),
                    )
                )

            async with session_factory() as session:
                user_after_rollback = await session.get(User, registration.user_id)
                rolled_back_session_count = (
                    await session.execute(select(func.count()).select_from(RefreshSession))
                ).scalar_one()
                rolled_back_family_count = (
                    await session.execute(select(func.count()).select_from(RefreshSessionFamily))
                ).scalar_one()
                rolled_back_audit_count = (
                    await session.execute(
                        select(func.count())
                        .select_from(AuditLog)
                        .where(AuditLog.action == LOGIN_AUDIT_ACTION)
                    )
                ).scalar_one()

            assert user_after_rollback is not None
            assert PasswordHash(user_after_rollback.password_hash) == legacy_hash
            assert user_after_rollback.password_changed_at == password_changed_at
            assert user_after_rollback.last_login_at is None
            assert rolled_back_session_count == 0
            assert rolled_back_family_count == 0
            assert rolled_back_audit_count == 0

            result = await login_service.login(
                AuthenticateCredentialsCommand(
                    email="  LOGIN-OWNER@EXAMPLE.COM ",
                    password=SecretStr(RAW_VALUE),
                    trace_id=TraceId("login-service-success-trace"),
                )
            )

            async with session_factory() as session:
                user = await session.get(User, registration.user_id)
                family = await session.get(
                    RefreshSessionFamily,
                    result.session.rotation_family_id,
                )
                refresh_session = await session.get(
                    RefreshSession,
                    result.session.session_id,
                )
                login_audit = (
                    await session.scalars(
                        select(AuditLog).where(AuditLog.action == LOGIN_AUDIT_ACTION)
                    )
                ).one()

            assert user is not None
            assert family is not None
            assert refresh_session is not None
            assert PasswordHash(user.password_hash) != legacy_hash
            assert await current_hasher.verify(
                PasswordHash(user.password_hash),
                SecretStr(RAW_VALUE),
            )
            assert await current_hasher.needs_rehash(PasswordHash(user.password_hash)) is False
            assert user.password_changed_at == password_changed_at
            assert user.last_login_at == LOGIN_AT
            assert family.current_session_id == result.session.session_id
            assert refresh_session.token_hash == bytes(
                token_service.digest_refresh(result.refresh_token)
            )
            assert refresh_session.csrf_token_hash == bytes(
                token_service.digest_csrf(result.csrf_token)
            )
            assert refresh_session.device_hash == bytes(
                token_service.digest_device(result.device_token)
            )
            assert login_audit.trace_id == "login-service-success-trace"

            access_claims = access_token_codec.verify(
                result.access_token,
                now=LOGIN_AT,
            )
            assert access_claims.user_id == registration.user_id
            assert access_claims.session_id == result.session.session_id
            assert access_claims.issued_at == result.session.issued_at
            assert access_claims.expires_at == result.access_token_expires_at

            with pytest.raises(InvalidCredentialsError):
                await login_service.login(
                    AuthenticateCredentialsCommand(
                        email="login-owner@example.com",
                        password=SecretStr("different but valid raw value"),
                        trace_id=TraceId("login-service-rejected-trace"),
                    )
                )

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
