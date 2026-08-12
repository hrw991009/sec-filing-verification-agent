"""Exercise the public login contract against real PostgreSQL and Redis."""

from datetime import datetime
from ipaddress import IPv6Address
from uuid import uuid4

from fastapi.testclient import TestClient
from httpx2 import Response as HttpxResponse
from redis import Redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from industry_platform.core.config import Settings
from industry_platform.core.http import PROBLEM_MEDIA_TYPE
from industry_platform.main import create_app
from industry_platform.modules.identity.adapters.access_tokens import (
    Ed25519AccessTokenCodec,
)
from industry_platform.modules.identity.adapters.session_tokens import (
    HmacSessionTokenService,
)
from industry_platform.modules.identity.adapters.sqlalchemy import LOGIN_AUDIT_ACTION
from industry_platform.modules.identity.domain import (
    AccessToken,
    CsrfToken,
    DeviceToken,
    RefreshToken,
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

RAW_VALUE = "correct-horse-battery-staple"
DIFFERENT_RAW_VALUE = "different-but-still-valid-value"
RATE_LIMIT_KEY_PATTERN = "iip:login-rate-limit:v1:*"


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


def _required_cookie(response: HttpxResponse, name: str) -> str:
    value = response.cookies.get(name)
    if not isinstance(value, str):
        raise AssertionError(f"Missing login cookie: {name}")

    return value


def test_login_http_contract_commits_only_token_digests(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    """Cross HTTP, limiter, crypto, transaction, Cookie, and database boundaries."""

    settings = migrated_postgres_probe.settings
    redis_client = _redis_client(settings)
    keys_before = _rate_limit_keys(redis_client)
    source_ip = str(IPv6Address(uuid4().int))
    email = f"http-login-{uuid4().hex}@example.com"
    application = create_app(settings=settings)

    try:
        with TestClient(
            application,
            base_url="https://localhost",
            client=(source_ip, 50_000),
            backend_options={"loop_factory": create_selector_event_loop},
        ) as client:
            registered = client.post(
                "/api/v1/auth/register",
                json={"email": email, "password": RAW_VALUE},
            )
            logged_in = client.post(
                "/api/v1/auth/login",
                json={"email": email.upper(), "password": RAW_VALUE},
            )
            rejected = client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": DIFFERENT_RAW_VALUE},
            )

        assert registered.status_code == 201
        assert logged_in.status_code == 200
        assert logged_in.headers["cache-control"] == "no-store"
        assert logged_in.headers["pragma"] == "no-cache"
        assert set(logged_in.json()) == {
            "user",
            "access_token",
            "token_type",
            "expires_at",
        }
        assert logged_in.json()["user"] == {
            "id": registered.json()["user"]["id"],
            "email": email,
        }
        assert logged_in.json()["token_type"] == BEARER_SCHEME
        assert RAW_VALUE not in logged_in.text

        refresh_value = _required_cookie(logged_in, REFRESH_COOKIE_NAME)
        csrf_value = _required_cookie(logged_in, CSRF_COOKIE_NAME)
        device_value = _required_cookie(logged_in, DEVICE_COOKIE_NAME)

        with Session(migrated_postgres_probe.engine) as session:
            user = session.scalars(select(User).where(User.email == email)).one()
            refresh_session = session.scalars(
                select(RefreshSession).where(RefreshSession.user_id == user.id)
            ).one()
            family = session.get(
                RefreshSessionFamily,
                refresh_session.rotation_family_id,
            )
            login_audit = session.scalars(
                select(AuditLog).where(AuditLog.action == LOGIN_AUDIT_ACTION)
            ).one()
            session_count = session.scalar(select(func.count()).select_from(RefreshSession))
            family_count = session.scalar(select(func.count()).select_from(RefreshSessionFamily))
            login_audit_count = session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == LOGIN_AUDIT_ACTION)
            )

        assert family is not None
        assert family.current_session_id == refresh_session.id
        assert user.last_login_at is not None
        assert login_audit.trace_id == logged_in.headers["X-Trace-ID"]
        assert session_count == 1
        assert family_count == 1
        assert login_audit_count == 1

        session_tokens = HmacSessionTokenService(
            refresh_hmac_key=settings.refresh_token_hmac_key,
            csrf_hmac_key=settings.csrf_token_hmac_key,
            device_hmac_key=settings.device_token_hmac_key,
        )
        assert refresh_session.token_hash == bytes(
            session_tokens.digest_refresh(RefreshToken.from_transport(refresh_value))
        )
        assert refresh_session.csrf_token_hash == bytes(
            session_tokens.digest_csrf(CsrfToken.from_transport(csrf_value))
        )
        assert refresh_session.device_hash == bytes(
            session_tokens.digest_device(DeviceToken.from_transport(device_value))
        )
        assert refresh_value.encode() not in refresh_session.token_hash
        assert csrf_value.encode() not in refresh_session.csrf_token_hash
        assert device_value.encode() not in refresh_session.device_hash

        access_codec = Ed25519AccessTokenCodec(
            current_kid=settings.access_token_current_kid,
            private_key=settings.access_token_private_key,
            public_keys=dict(settings.access_token_public_keys),
        )
        access_value = logged_in.json()["access_token"]
        assert isinstance(access_value, str)
        claims = access_codec.verify(
            AccessToken.from_transport(access_value),
            now=user.last_login_at,
        )
        assert claims.user_id == user.id
        assert claims.session_id == refresh_session.id
        assert claims.issued_at == user.last_login_at
        assert claims.expires_at == datetime.fromisoformat(
            logged_in.json()["expires_at"].replace("Z", "+00:00")
        )

        assert rejected.status_code == 401
        assert rejected.headers["content-type"] == PROBLEM_MEDIA_TYPE
        assert rejected.headers["cache-control"] == "no-store"
        assert rejected.json()["code"] == "INVALID_CREDENTIALS"
        assert rejected.json()["trace_id"] == rejected.headers["X-Trace-ID"]
        assert rejected.headers.get_list("set-cookie") == []
        assert RAW_VALUE not in rejected.text
        assert DIFFERENT_RAW_VALUE not in rejected.text
    finally:
        keys_after = _rate_limit_keys(redis_client)
        created_keys = keys_after - keys_before
        if created_keys:
            redis_client.delete(*created_keys)
        redis_client.close()
