"""Exercise public logout across HTTP, browser cookies, and PostgreSQL."""

from ipaddress import IPv6Address
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from industry_platform.core.config import Settings
from industry_platform.main import create_app
from industry_platform.modules.identity.adapters.sqlalchemy import LOGOUT_AUDIT_ACTION
from industry_platform.modules.identity.http_cookies import (
    CSRF_COOKIE_NAME,
    DEVICE_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
)
from industry_platform.modules.identity.models import (
    AuditLog,
    RefreshSession,
    RefreshSessionFamily,
)
from industry_platform.modules.identity.schemas import BEARER_SCHEME
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe

RAW_VALUE = "correct-horse-battery-staple"
CSRF_HEADER_NAME = "X-CSRF-Token"
RATE_LIMIT_KEY_PATTERN = "iip:login-rate-limit:v1:*"
BROWSER_COOKIE_DOMAIN = "localhost.local"


def _redis_client(settings: Settings) -> Redis:
    return Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password.get_secret_value(),
        decode_responses=True,
    )


def _rate_limit_keys(client: Redis) -> set[str]:
    keys: set[str] = set()
    for key in client.scan_iter(match=RATE_LIMIT_KEY_PATTERN):
        if not isinstance(key, str):
            raise AssertionError("Redis integration client must decode keys as text")
        keys.add(key)
    return keys


def test_logout_http_is_idempotent_and_invalidates_access_immediately(
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
    email = f"http-logout-{uuid4().hex}@example.com"
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
            json={"email": email, "password": RAW_VALUE},
        )
        logged_in = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": RAW_VALUE},
        )
        assert registered.status_code == 201
        assert logged_in.status_code == 200

        access_value = logged_in.json()["access_token"]
        refresh_value = client.cookies.get(REFRESH_COOKIE_NAME)
        csrf_value = client.cookies.get(CSRF_COOKIE_NAME)
        device_value = client.cookies.get(DEVICE_COOKIE_NAME)
        if not all(isinstance(value, str) for value in (refresh_value, csrf_value, device_value)):
            raise AssertionError("Login did not establish all browser cookies")
        assert isinstance(refresh_value, str)
        assert isinstance(csrf_value, str)
        assert isinstance(device_value, str)

        logged_out = client.post(
            "/api/v1/auth/logout",
            headers={"Origin": trusted_origin, CSRF_HEADER_NAME: csrf_value},
        )
        assert logged_out.status_code == 204
        assert client.cookies.get(REFRESH_COOKIE_NAME) is None
        assert client.cookies.get(CSRF_COOKIE_NAME) is None
        assert client.cookies.get(DEVICE_COOKIE_NAME) is None

        rejected = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"{BEARER_SCHEME} {access_value}"},
        )
        assert rejected.status_code == 401
        assert rejected.json()["code"] == "INVALID_AUTHENTICATED_SESSION"

        client.cookies.set(
            REFRESH_COOKIE_NAME,
            refresh_value,
            domain=BROWSER_COOKIE_DOMAIN,
            path="/",
        )
        client.cookies.set(
            CSRF_COOKIE_NAME,
            csrf_value,
            domain=BROWSER_COOKIE_DOMAIN,
            path="/",
        )
        client.cookies.set(
            DEVICE_COOKIE_NAME,
            device_value,
            domain=BROWSER_COOKIE_DOMAIN,
            path="/",
        )
        retried = client.post(
            "/api/v1/auth/logout",
            headers={"Origin": trusted_origin, CSRF_HEADER_NAME: csrf_value},
        )
        assert retried.status_code == 204
        assert client.cookies.get(REFRESH_COOKIE_NAME) is None
        assert client.cookies.get(CSRF_COOKIE_NAME) is None
        assert client.cookies.get(DEVICE_COOKIE_NAME) is None

    with Session(migrated_postgres_probe.engine) as session:
        family = session.scalars(select(RefreshSessionFamily)).one()
        stored_sessions = list(session.scalars(select(RefreshSession)))
        audit_count = session.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.action == LOGOUT_AUDIT_ACTION)
        )
        audit = session.scalars(
            select(AuditLog).where(AuditLog.action == LOGOUT_AUDIT_ACTION)
        ).one()

    assert family.revoked_at is not None
    assert family.revocation_reason == "logout"
    assert stored_sessions
    assert all(item.revoked_at is not None for item in stored_sessions)
    assert audit_count == 1
    assert audit.trace_id == logged_out.headers["X-Trace-ID"]
