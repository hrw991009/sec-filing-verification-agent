"""Prove credential authentication against a migrated PostgreSQL database."""

import asyncio

import pytest
from anyio import CapacityLimiter
from pydantic import SecretStr
from sqlalchemy import func, select

from industry_platform.core.database import (
    create_database_engine,
    create_database_session_factory,
)
from industry_platform.modules.identity.adapters.argon2 import Argon2idPasswordHasher
from industry_platform.modules.identity.adapters.sqlalchemy import (
    SqlAlchemyCredentialReader,
    SqlAlchemyRegistrationTransactionFactory,
)
from industry_platform.modules.identity.domain import (
    AuthenticateCredentialsCommand,
    InvalidCredentialsError,
    RegisterUserCommand,
    TraceId,
)
from industry_platform.modules.identity.models import RefreshSession, User, UserStatus
from industry_platform.modules.identity.passwords import ValidatedPassword
from industry_platform.modules.identity.service import (
    CredentialAuthenticationService,
    RegistrationService,
)
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe

RAW_VALUE = "correct horse battery staple"
DUMMY_RAW_VALUE = "unrelated dummy verification value"


def test_registered_account_authenticates_through_the_real_database_and_argon2(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    """Exercise registration, lookup, password verification, and account status."""

    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        password_hasher = Argon2idPasswordHasher(
            migrated_postgres_probe.settings,
            limiter=CapacityLimiter(1),
        )
        registration_service = RegistrationService(
            password_hasher=password_hasher,
            transaction_factory=SqlAlchemyRegistrationTransactionFactory(session_factory),
        )
        dummy_password_hash = await password_hasher.hash(
            ValidatedPassword.from_secret(SecretStr(DUMMY_RAW_VALUE))
        )
        authentication_service = CredentialAuthenticationService(
            password_hasher=password_hasher,
            credential_reader=SqlAlchemyCredentialReader(session_factory),
            dummy_password_hash=dummy_password_hash,
        )

        try:
            registration = await registration_service.register(
                RegisterUserCommand(
                    email="learner@example.com",
                    password=SecretStr(RAW_VALUE),
                    trace_id=TraceId("authentication-registration-trace"),
                )
            )
            verified = await authentication_service.authenticate(
                AuthenticateCredentialsCommand(
                    email="  Learner@EXAMPLE.COM ",
                    password=SecretStr(RAW_VALUE),
                    trace_id=TraceId("authentication-success-trace"),
                )
            )

            assert verified.user_id == registration.user_id
            assert verified.email == registration.email
            assert verified.password_rehash_required is False

            for rejected_command in (
                AuthenticateCredentialsCommand(
                    email="learner@example.com",
                    password=SecretStr("different but valid raw value"),
                    trace_id=TraceId("authentication-wrong-value-trace"),
                ),
                AuthenticateCredentialsCommand(
                    email="missing@example.com",
                    password=SecretStr(RAW_VALUE),
                    trace_id=TraceId("authentication-missing-user-trace"),
                ),
            ):
                with pytest.raises(InvalidCredentialsError) as exc_info:
                    await authentication_service.authenticate(rejected_command)

                assert str(exc_info.value) == "Invalid email or password"
                assert rejected_command.email not in str(exc_info.value)

            async with session_factory.begin() as session:
                user = (
                    await session.scalars(select(User).where(User.id == registration.user_id))
                ).one()
                user.status = UserStatus.DISABLED

            with pytest.raises(InvalidCredentialsError):
                await authentication_service.authenticate(
                    AuthenticateCredentialsCommand(
                        email="learner@example.com",
                        password=SecretStr(RAW_VALUE),
                        trace_id=TraceId("authentication-disabled-trace"),
                    )
                )

            async with session_factory() as session:
                stored_user = (
                    await session.scalars(select(User).where(User.id == registration.user_id))
                ).one()
                refresh_session_count = (
                    await session.execute(select(func.count()).select_from(RefreshSession))
                ).scalar_one()

            assert stored_user.last_login_at is None
            assert refresh_session_count == 0
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())
