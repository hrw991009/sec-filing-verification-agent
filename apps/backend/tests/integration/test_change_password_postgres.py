"""Prove PostgreSQL serializes concurrent password replacements with one CAS winner."""

import asyncio
from datetime import UTC, datetime
from ipaddress import IPv6Address
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from industry_platform.core.config import Settings
from industry_platform.core.database import (
    create_database_engine,
    create_database_session_factory,
)
from industry_platform.main import create_app
from industry_platform.modules.identity.adapters.password_changes import (
    PASSWORD_CHANGE_AUDIT_ACTION,
    SqlAlchemyPasswordChangeTransactionFactory,
)
from industry_platform.modules.identity.domain import (
    PasswordChangeConflictError,
    PasswordHash,
    PersistPasswordChangeCommand,
    TraceId,
)
from industry_platform.modules.identity.http_cookies import REFRESH_COOKIE_NAME
from industry_platform.modules.identity.models import (
    AuditLog,
    RefreshSession,
    RefreshSessionFamily,
    User,
)
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe

RAW_VALUE = "current-horse-battery-staple"
RATE_LIMIT_KEY_PATTERN = "iip:login-rate-limit:v1:*"
CHANGED_AT = datetime(2026, 8, 11, 13, 0, tzinfo=UTC)


def _redis_client(settings: Settings) -> Redis:
    return Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password.get_secret_value(),
        decode_responses=True,
    )


def _rate_limit_keys(client: Redis) -> set[str]:
    return {key for key in client.scan_iter(match=RATE_LIMIT_KEY_PATTERN) if isinstance(key, str)}


def test_concurrent_password_change_has_one_cas_winner(
    migrated_postgres_probe: PostgresProbe,
    request: pytest.FixtureRequest,
) -> None:
    settings = migrated_postgres_probe.settings
    redis_client = _redis_client(settings)
    keys_before = _rate_limit_keys(redis_client)

    def cleanup_rate_limit_keys() -> None:
        created_keys = _rate_limit_keys(redis_client) - keys_before
        if created_keys:
            redis_client.delete(*created_keys)
        redis_client.close()

    request.addfinalizer(cleanup_rate_limit_keys)
    email = f"change-password-race-{uuid4().hex}@example.com"
    application = create_app(settings=settings)
    with TestClient(
        application,
        base_url=settings.browser_trusted_origins[0],
        client=(str(IPv6Address(uuid4().int)), 50_000),
        backend_options={"loop_factory": create_selector_event_loop},
    ) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": RAW_VALUE},
        )
        logged_in = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": RAW_VALUE},
        )
        assert registered.status_code == 201
        assert logged_in.status_code == 200
        assert client.cookies.get(REFRESH_COOKIE_NAME) is not None

    with Session(migrated_postgres_probe.engine) as session:
        user = session.scalars(select(User)).one()
        login_session = session.scalars(select(RefreshSession)).one()
        user_id = user.id
        session_id = login_session.id
        expected_digest = PasswordHash(user.password_hash)

    async def exercise() -> tuple[str, str]:
        engine = create_database_engine(settings)
        factory = SqlAlchemyPasswordChangeTransactionFactory(
            create_database_session_factory(engine)
        )

        async def apply(replacement: PasswordHash, trace: str) -> str:
            try:
                async with factory() as writer:
                    await writer.persist_password_change(
                        PersistPasswordChangeCommand(
                            user_id=user_id,
                            authenticated_session_id=session_id,
                            expected_password_hash=expected_digest,
                            replacement_password_hash=replacement,
                            changed_at=CHANGED_AT,
                            trace_id=TraceId(trace),
                        )
                    )
            except PasswordChangeConflictError:
                return "conflict"
            return "applied"

        try:
            async with asyncio.timeout(30):
                return await asyncio.gather(
                    apply(PasswordHash("$argon2id$replacement-a"), "race-a"),
                    apply(PasswordHash("$argon2id$replacement-b"), "race-b"),
                )
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        outcomes = runner.run(exercise())

    with Session(migrated_postgres_probe.engine) as session:
        stored_user = session.get(User, user_id)
        family = session.scalars(select(RefreshSessionFamily)).one()
        stored_session = session.scalars(select(RefreshSession)).one()
        audit_count = session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == PASSWORD_CHANGE_AUDIT_ACTION)
        )

    assert sorted(outcomes) == ["applied", "conflict"]
    assert stored_user is not None
    assert stored_user.password_hash in {
        "$argon2id$replacement-a",
        "$argon2id$replacement-b",
    }
    assert family.revocation_reason == "password_changed"
    assert stored_session.revocation_reason == "password_changed"
    assert audit_count == 1
