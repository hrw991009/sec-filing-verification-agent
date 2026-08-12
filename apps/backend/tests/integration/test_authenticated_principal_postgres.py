"""Prove authenticated principals against migrated PostgreSQL state."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from industry_platform.core.database import (
    create_database_engine,
    create_database_session_factory,
)
from industry_platform.core.redis_client import create_redis_client
from industry_platform.modules.identity.domain import (
    AuthenticateCredentialsCommand,
    InvalidAuthenticatedSessionError,
    RegisterUserCommand,
    TraceId,
)
from industry_platform.modules.identity.models import RefreshSession
from industry_platform.modules.identity.resources import create_identity_resources
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe

RAW_VALUE = "correct-horse-battery-staple"


def test_principal_rechecks_session_and_current_workspace_membership(
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
        email = f"principal-{uuid4().hex}@example.com"

        try:
            registration = await resources.registration_service.register(
                RegisterUserCommand(
                    email=email,
                    password=SecretStr(RAW_VALUE),
                    trace_id=TraceId("principal-registration-trace"),
                )
            )
            login = await resources.login_service.login(
                AuthenticateCredentialsCommand(
                    email=email,
                    password=SecretStr(RAW_VALUE),
                    trace_id=TraceId("principal-login-trace"),
                )
            )

            principal = await resources.principal_resolver.resolve(login.access_token)

            assert principal.user_id == registration.user_id
            assert principal.session_id == login.session.session_id
            assert principal.email == registration.email
            assert len(principal.workspaces) == 1
            assert principal.workspaces[0].workspace_id == registration.workspace_id
            assert principal.workspaces[0].name == registration.workspace_name
            assert principal.workspaces[0].role == "owner"

            async with session_factory.begin() as session:
                stored_session = (
                    await session.scalars(
                        select(RefreshSession).where(RefreshSession.id == login.session.session_id)
                    )
                ).one()
                stored_session.revoked_at = datetime.now(UTC)
                stored_session.revocation_reason = "integration_probe"

            with pytest.raises(InvalidAuthenticatedSessionError):
                await resources.principal_resolver.resolve(login.access_token)
        finally:
            await redis_client.aclose()
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())
