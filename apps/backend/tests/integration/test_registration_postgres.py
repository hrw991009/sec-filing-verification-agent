"""Prove registration invariants against real PostgreSQL transactions."""

import asyncio

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from industry_platform.core.database import (
    AsyncSessionFactory,
    create_database_engine,
    create_database_session_factory,
)
from industry_platform.modules.identity.adapters.sqlalchemy import (
    SqlAlchemyRegistrationTransactionFactory,
)
from industry_platform.modules.identity.domain import (
    EmailAlreadyRegisteredError,
    NormalizedEmail,
    PasswordHash,
    RegisterUserCommand,
    RegistrationPersistenceError,
    RegistrationRecord,
    TraceId,
)
from industry_platform.modules.identity.models import (
    AuditLog,
    AuditOutcome,
    User,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
)
from industry_platform.modules.identity.passwords import ValidatedPassword
from industry_platform.modules.identity.service import RegistrationService
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe

FIXED_PASSWORD_HASH = PasswordHash("$argon2id$integration-test-hash")


class FixedPasswordHasher:
    """Fast adapter here because Argon2 itself already has focused tests."""

    async def hash(self, password: ValidatedPassword) -> PasswordHash:
        del password
        return FIXED_PASSWORD_HASH

    async def verify(
        self,
        password_hash: PasswordHash,
        password: SecretStr,
    ) -> bool:
        return password_hash == FIXED_PASSWORD_HASH and bool(password.get_secret_value())

    async def needs_rehash(self, password_hash: PasswordHash) -> bool:
        return password_hash != FIXED_PASSWORD_HASH


async def count_registration_rows(
    session_factory: AsyncSessionFactory,
) -> dict[str, int]:
    """Count every table that one registration is required to create."""

    async with session_factory() as session:
        user_count = (await session.execute(select(func.count()).select_from(User))).scalar_one()
        workspace_count = (
            await session.execute(select(func.count()).select_from(Workspace))
        ).scalar_one()
        membership_count = (
            await session.execute(select(func.count()).select_from(WorkspaceMembership))
        ).scalar_one()
        audit_log_count = (
            await session.execute(select(func.count()).select_from(AuditLog))
        ).scalar_one()

    return {
        "users": user_count,
        "workspaces": workspace_count,
        "memberships": membership_count,
        "audit_logs": audit_log_count,
    }


def test_registration_persists_complete_sanitized_owner_workspace(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    """Commit one complete registration and inspect every stored record."""

    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        service = RegistrationService(
            password_hasher=FixedPasswordHasher(),
            transaction_factory=SqlAlchemyRegistrationTransactionFactory(session_factory),
        )
        plaintext = "correct horse 电池订书钉"
        trace_id = TraceId("registration-success-trace")

        try:
            record = await service.register(
                RegisterUserCommand(
                    email="  New.User@EXAMPLE.com ",
                    password=SecretStr(plaintext),
                    trace_id=trace_id,
                )
            )

            async with session_factory() as session:
                user = (await session.scalars(select(User))).one()
                workspace = (await session.scalars(select(Workspace))).one()
                membership = (await session.scalars(select(WorkspaceMembership))).one()
                audit_log = (await session.scalars(select(AuditLog))).one()

            assert record.email == NormalizedEmail("new.user@example.com")
            assert record.user_id == user.id
            assert record.workspace_id == workspace.id
            assert user.password_hash == FIXED_PASSWORD_HASH
            assert user.password_hash != plaintext
            assert workspace.created_by_user_id == user.id
            assert membership.workspace_id == workspace.id
            assert membership.user_id == user.id
            assert membership.role is WorkspaceRole.OWNER
            assert audit_log.actor_user_id == user.id
            assert audit_log.workspace_id == workspace.id
            assert audit_log.action == "identity.user.registered"
            assert audit_log.outcome is AuditOutcome.SUCCEEDED
            assert audit_log.trace_id == trace_id
            assert audit_log.sanitized_metadata == {
                "source": "self_service",
                "role": "owner",
            }
            assert await count_registration_rows(session_factory) == {
                "users": 1,
                "workspaces": 1,
                "memberships": 1,
                "audit_logs": 1,
            }
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())


def test_registration_rolls_back_user_when_workspace_insert_fails(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    """Prove a later constraint failure removes the earlier user insert."""

    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        transaction_factory = SqlAlchemyRegistrationTransactionFactory(session_factory)

        try:
            with pytest.raises(RegistrationPersistenceError) as exc_info:
                async with transaction_factory() as writer:
                    await writer.create_registration(
                        email=NormalizedEmail("rollback@example.com"),
                        password_hash=FIXED_PASSWORD_HASH,
                        workspace_name="   ",
                        trace_id=TraceId("registration-rollback-trace"),
                    )

            assert exc_info.value.sqlstate == "23514"
            assert await count_registration_rows(session_factory) == {
                "users": 0,
                "workspaces": 0,
                "memberships": 0,
                "audit_logs": 0,
            }
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())


def test_concurrent_canonical_duplicate_has_exactly_one_winner(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    """Let PostgreSQL arbitrate two simultaneous canonical duplicate emails."""

    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        service = RegistrationService(
            password_hasher=FixedPasswordHasher(),
            transaction_factory=SqlAlchemyRegistrationTransactionFactory(session_factory),
        )

        try:
            results = await asyncio.gather(
                service.register(
                    RegisterUserCommand(
                        email="Race.User@example.com",
                        password=SecretStr("first valid password"),
                        trace_id=TraceId("registration-race-one"),
                    )
                ),
                service.register(
                    RegisterUserCommand(
                        email=" race.user@EXAMPLE.COM ",
                        password=SecretStr("second valid password"),
                        trace_id=TraceId("registration-race-two"),
                    )
                ),
                return_exceptions=True,
            )

            assert sum(isinstance(result, RegistrationRecord) for result in results) == 1
            assert sum(isinstance(result, EmailAlreadyRegisteredError) for result in results) == 1
            assert await count_registration_rows(session_factory) == {
                "users": 1,
                "workspaces": 1,
                "memberships": 1,
                "audit_logs": 1,
            }
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())
