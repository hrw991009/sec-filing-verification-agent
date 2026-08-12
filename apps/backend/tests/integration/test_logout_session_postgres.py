"""Prove logout revocation and idempotency against migrated PostgreSQL."""

import asyncio
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from industry_platform.core.database import (
    create_database_engine,
    create_database_session_factory,
)
from industry_platform.core.redis_client import create_redis_client
from industry_platform.modules.identity.adapters.sqlalchemy import LOGOUT_AUDIT_ACTION
from industry_platform.modules.identity.domain import (
    AuthenticateCredentialsCommand,
    InvalidAuthenticatedSessionError,
    LogoutSessionCommand,
    RegisterUserCommand,
    TraceId,
)
from industry_platform.modules.identity.models import (
    AuditLog,
    RefreshSession,
    RefreshSessionFamily,
)
from industry_platform.modules.identity.resources import create_identity_resources
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe

RAW_VALUE = "correct-horse-battery-staple"
TRUSTED_ORIGIN = "https://localhost:5173"


def test_logout_revokes_every_generation_and_invalidates_access(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        settings = migrated_postgres_probe.settings
        engine = create_database_engine(settings)
        session_factory = create_database_session_factory(engine)
        redis_client = create_redis_client(settings)
        resources = await create_identity_resources(
            settings,
            session_factory,
            redis_client,
        )
        email = f"logout-{uuid4().hex}@example.com"

        try:
            await resources.registration_service.register(
                RegisterUserCommand(
                    email=email,
                    password=SecretStr(RAW_VALUE),
                    trace_id=TraceId("logout-registration-trace"),
                )
            )
            login = await resources.login_service.login(
                AuthenticateCredentialsCommand(
                    email=email,
                    password=SecretStr(RAW_VALUE),
                    trace_id=TraceId("logout-login-trace"),
                )
            )
            command = LogoutSessionCommand(
                origin=TRUSTED_ORIGIN,
                refresh_token=login.refresh_token,
                csrf_cookie_value=login.csrf_token.reveal_for_transport(),
                csrf_header_value=login.csrf_token.reveal_for_transport(),
                device_token=login.device_token,
                trace_id=TraceId("logout-success-trace"),
            )

            await resources.logout_service.logout(command)

            with pytest.raises(InvalidAuthenticatedSessionError):
                await resources.principal_resolver.resolve(login.access_token)

            await resources.logout_service.logout(command)

            async with session_factory() as session:
                family = (await session.scalars(select(RefreshSessionFamily))).one()
                sessions = list(await session.scalars(select(RefreshSession)))
                audit_count = await session.scalar(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(AuditLog.action == LOGOUT_AUDIT_ACTION)
                )
                audit = (
                    await session.scalars(
                        select(AuditLog).where(AuditLog.action == LOGOUT_AUDIT_ACTION)
                    )
                ).one()

            assert family.revoked_at is not None
            assert family.revocation_reason == "logout"
            assert sessions
            assert all(item.revoked_at is not None for item in sessions)
            assert all(item.revocation_reason == "logout" for item in sessions)
            assert all(item.recovery_envelope is None for item in sessions)
            assert audit_count == 1
            assert audit.trace_id == "logout-success-trace"
        finally:
            await redis_client.aclose()
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())
