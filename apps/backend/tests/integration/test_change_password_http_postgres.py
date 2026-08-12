"""Exercise password replacement across HTTP, Redis, Argon2, and PostgreSQL."""

from ipaddress import IPv6Address
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from industry_platform.core.config import Settings
from industry_platform.main import create_app
from industry_platform.modules.identity.adapters.password_changes import (
    PASSWORD_CHANGE_AUDIT_ACTION,
)
from industry_platform.modules.identity.http_cookies import (
    CSRF_COOKIE_NAME,
    DEVICE_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
)
from industry_platform.modules.identity.models import (
    AuditLog,
    RefreshSession,
    RefreshSessionFamily,
    User,
)
from industry_platform.modules.identity.schemas import BEARER_SCHEME
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe

CURRENT_RAW_VALUE = "current-horse-battery-staple"
NEW_RAW_VALUE = "replacement-horse-battery-staple"
RATE_LIMIT_KEY_PATTERN = "iip:*rate-limit:v1:*"
BROWSER_COOKIE_DOMAIN = "localhost.local"


def _redis_client(settings: Settings) -> Redis:
    return Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password.get_secret_value(),
        decode_responses=True,
    )


def _rate_limit_keys(client: Redis) -> set[str]:
    return {key for key in client.scan_iter(match=RATE_LIMIT_KEY_PATTERN) if isinstance(key, str)}


def test_change_password_revokes_every_session_and_requires_new_credentials(
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
    trusted_origin = settings.browser_trusted_origins[0]
    email = f"change-password-{uuid4().hex}@example.com"
    source_ip = str(IPv6Address(uuid4().int))
    application = create_app(settings=settings)

    with TestClient(
        application,
        base_url=trusted_origin,
        client=(source_ip, 50_000),
        backend_options={"loop_factory": create_selector_event_loop},
    ) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": CURRENT_RAW_VALUE},
        )
        first_login = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": CURRENT_RAW_VALUE},
        )
        first_access = first_login.json()["access_token"]
        first_refresh = client.cookies.get(REFRESH_COOKIE_NAME)
        first_csrf = client.cookies.get(CSRF_COOKIE_NAME)
        first_device = client.cookies.get(DEVICE_COOKIE_NAME)
        second_login = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": CURRENT_RAW_VALUE},
        )
        second_access = second_login.json()["access_token"]

        assert registered.status_code == 201
        assert first_login.status_code == 200
        assert second_login.status_code == 200
        assert isinstance(first_refresh, str)
        assert isinstance(first_csrf, str)
        assert isinstance(first_device, str)

        changed = client.post(
            "/api/v1/auth/change-password",
            headers={
                "Authorization": f"{BEARER_SCHEME} {second_access}",
                "Origin": trusted_origin,
            },
            json={
                "current_password": CURRENT_RAW_VALUE,
                "new_password": NEW_RAW_VALUE,
            },
        )
        assert changed.status_code == 204
        assert client.cookies.get(REFRESH_COOKIE_NAME) is None
        assert client.cookies.get(CSRF_COOKIE_NAME) is None
        assert client.cookies.get(DEVICE_COOKIE_NAME) is None

        for access_value in (first_access, second_access):
            rejected_me = client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"{BEARER_SCHEME} {access_value}"},
            )
            assert rejected_me.status_code == 401

        client.cookies.set(
            REFRESH_COOKIE_NAME,
            first_refresh,
            domain=BROWSER_COOKIE_DOMAIN,
            path="/",
        )
        client.cookies.set(
            CSRF_COOKIE_NAME,
            first_csrf,
            domain=BROWSER_COOKIE_DOMAIN,
            path="/",
        )
        client.cookies.set(
            DEVICE_COOKIE_NAME,
            first_device,
            domain=BROWSER_COOKIE_DOMAIN,
            path="/",
        )
        rejected_refresh = client.post(
            "/api/v1/auth/refresh",
            headers={"Origin": trusted_origin, "X-CSRF-Token": first_csrf},
        )
        assert rejected_refresh.status_code == 401

        old_login = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": CURRENT_RAW_VALUE},
        )
        new_login = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": NEW_RAW_VALUE},
        )
        assert old_login.status_code == 401
        assert new_login.status_code == 200

    with Session(migrated_postgres_probe.engine) as session:
        user = session.scalars(select(User)).one()
        families = list(session.scalars(select(RefreshSessionFamily)))
        stored_sessions = list(session.scalars(select(RefreshSession)))
        audit_count = session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == PASSWORD_CHANGE_AUDIT_ACTION)
        )

    assert user.password_changed_at is not None
    assert families
    assert stored_sessions
    assert len(families) == 3
    assert len(stored_sessions) == 3
    assert sum(family.revocation_reason == "password_changed" for family in families) == 2
    assert sum(item.revocation_reason == "password_changed" for item in stored_sessions) == 2
    assert sum(family.revoked_at is None for family in families) == 1
    assert sum(item.revoked_at is None for item in stored_sessions) == 1
    assert audit_count == 1
