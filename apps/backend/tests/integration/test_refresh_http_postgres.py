"""Exercise Refresh HTTP delivery against real PostgreSQL and Redis."""

from datetime import UTC, datetime
from ipaddress import IPv6Address
from uuid import uuid4

from fastapi.testclient import TestClient
from httpx2 import Response as HttpxResponse
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from industry_platform.core.config import Settings
from industry_platform.main import create_app
from industry_platform.modules.identity.adapters.access_tokens import (
    Ed25519AccessTokenCodec,
)
from industry_platform.modules.identity.adapters.session_tokens import (
    HmacSessionTokenService,
)
from industry_platform.modules.identity.adapters.sqlalchemy import (
    REFRESH_AUDIT_ACTION,
    REFRESH_RECOVERY_AUDIT_ACTION,
)
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


def _required_cookie(response: HttpxResponse, name: str) -> str:
    value = response.cookies.get(name)
    if not isinstance(value, str):
        raise AssertionError(f"Missing refresh response Cookie: {name}")

    return value


def _cookie_headers(response: HttpxResponse) -> dict[str, str]:
    return {value.split("=", 1)[0]: value for value in response.headers.get_list("set-cookie")}


def test_refresh_http_rotates_and_recovers_one_committed_successor(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    """Cross HTTP, Cookie, crypto, transaction, recovery, and JWT boundaries."""

    settings = migrated_postgres_probe.settings
    redis_client = _redis_client(settings)
    keys_before = _rate_limit_keys(redis_client)
    source_ip = str(IPv6Address(uuid4().int))
    email = f"http-refresh-{uuid4().hex}@example.com"
    trusted_origin = settings.browser_trusted_origins[0]
    application = create_app(settings=settings)

    try:
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

            predecessor_refresh = _required_cookie(logged_in, REFRESH_COOKIE_NAME)
            predecessor_csrf = _required_cookie(logged_in, CSRF_COOKIE_NAME)
            stable_device = _required_cookie(logged_in, DEVICE_COOKIE_NAME)

            refreshed = client.post(
                "/api/v1/auth/refresh",
                headers={
                    "Origin": trusted_origin,
                    "X-CSRF-Token": predecessor_csrf,
                },
            )
            successor_refresh = _required_cookie(refreshed, REFRESH_COOKIE_NAME)
            successor_csrf = _required_cookie(refreshed, CSRF_COOKIE_NAME)

            # Simulate a lost first response: the browser still holds the predecessor pair.
            client.cookies.clear()
            client.cookies.set(
                REFRESH_COOKIE_NAME,
                predecessor_refresh,
                domain=BROWSER_COOKIE_DOMAIN,
                path="/",
            )
            client.cookies.set(
                CSRF_COOKIE_NAME,
                predecessor_csrf,
                domain=BROWSER_COOKIE_DOMAIN,
                path="/",
            )
            client.cookies.set(
                DEVICE_COOKIE_NAME,
                stable_device,
                domain=BROWSER_COOKIE_DOMAIN,
                path="/",
            )
            recovered = client.post(
                "/api/v1/auth/refresh",
                headers={
                    "Origin": trusted_origin,
                    "X-CSRF-Token": predecessor_csrf,
                },
            )
            assert client.cookies.get(DEVICE_COOKIE_NAME) == stable_device

        assert registered.status_code == 201
        assert logged_in.status_code == 200
        assert refreshed.status_code == 200
        assert recovered.status_code == 200

        assert set(refreshed.json()) == {
            "access_token",
            "token_type",
            "expires_at",
        }
        assert refreshed.json()["token_type"] == BEARER_SCHEME
        assert refreshed.headers["cache-control"] == "no-store"
        assert refreshed.headers["pragma"] == "no-cache"
        assert refreshed.headers["X-Trace-ID"]

        assert successor_refresh != predecessor_refresh
        assert successor_csrf != predecessor_csrf
        assert _required_cookie(recovered, REFRESH_COOKIE_NAME) == successor_refresh
        assert _required_cookie(recovered, CSRF_COOKIE_NAME) == successor_csrf
        assert recovered.headers["cache-control"] == "no-store"
        assert recovered.headers["pragma"] == "no-cache"

        for response in (refreshed, recovered):
            cookie_headers = _cookie_headers(response)
            assert set(cookie_headers) == {
                REFRESH_COOKIE_NAME,
                CSRF_COOKIE_NAME,
            }
            assert DEVICE_COOKIE_NAME not in cookie_headers

            for cookie_name, cookie_header in cookie_headers.items():
                lowered = cookie_header.lower()
                assert "secure" in lowered
                assert "samesite=strict" in lowered
                assert "path=/" in lowered
                assert "domain=" not in lowered
                assert "expires=" in lowered
                if cookie_name == CSRF_COOKIE_NAME:
                    assert "httponly" not in lowered
                else:
                    assert "httponly" in lowered

        with Session(migrated_postgres_probe.engine) as session:
            user = session.scalars(select(User).where(User.email == email)).one()
            refresh_sessions = session.scalars(
                select(RefreshSession).where(RefreshSession.user_id == user.id)
            ).all()

            assert len(refresh_sessions) == 2
            predecessor = next(
                candidate for candidate in refresh_sessions if candidate.previous_session_id is None
            )
            successor = next(
                candidate
                for candidate in refresh_sessions
                if candidate.previous_session_id == predecessor.id
            )
            family = session.get(RefreshSessionFamily, predecessor.rotation_family_id)
            refresh_audits = session.scalars(
                select(AuditLog).where(
                    AuditLog.actor_user_id == user.id,
                    AuditLog.action.in_((REFRESH_AUDIT_ACTION, REFRESH_RECOVERY_AUDIT_ACTION)),
                )
            ).all()

            assert family is not None
            assert family.current_session_id == successor.id
            assert predecessor.rotation_family_id == successor.rotation_family_id
            assert predecessor.used_at is not None
            assert predecessor.replaced_by_session_id == successor.id
            assert successor.previous_session_id == predecessor.id
            assert successor.used_at is None
            assert successor.replaced_by_session_id is None
            assert successor.revoked_at is None
            assert {audit.action: audit.trace_id for audit in refresh_audits} == {
                REFRESH_AUDIT_ACTION: refreshed.headers["X-Trace-ID"],
                REFRESH_RECOVERY_AUDIT_ACTION: recovered.headers["X-Trace-ID"],
            }

            session_tokens = HmacSessionTokenService(
                refresh_hmac_key=settings.refresh_token_hmac_key,
                csrf_hmac_key=settings.csrf_token_hmac_key,
                device_hmac_key=settings.device_token_hmac_key,
            )
            assert successor.token_hash == bytes(
                session_tokens.digest_refresh(RefreshToken.from_transport(successor_refresh))
            )
            assert successor.csrf_token_hash == bytes(
                session_tokens.digest_csrf(CsrfToken.from_transport(successor_csrf))
            )
            assert (
                predecessor.device_hash
                == successor.device_hash
                == bytes(session_tokens.digest_device(DeviceToken.from_transport(stable_device)))
            )

            assert predecessor_refresh.encode() not in predecessor.token_hash
            assert predecessor_csrf.encode() not in predecessor.csrf_token_hash
            assert successor_refresh.encode() not in successor.token_hash
            assert successor_csrf.encode() not in successor.csrf_token_hash
            assert stable_device.encode() not in successor.device_hash
            assert predecessor.recovery_envelope is not None
            assert successor_refresh.encode() not in predecessor.recovery_envelope
            assert successor_csrf.encode() not in predecessor.recovery_envelope

            access_codec = Ed25519AccessTokenCodec(
                current_kid=settings.access_token_current_kid,
                private_key=settings.access_token_private_key,
                public_keys=dict(settings.access_token_public_keys),
            )
            access_value = refreshed.json()["access_token"]
            recovered_access_value = recovered.json()["access_token"]
            assert isinstance(access_value, str)
            assert isinstance(recovered_access_value, str)

            claims = access_codec.verify(
                AccessToken.from_transport(access_value),
                now=datetime.now(UTC),
            )
            recovered_claims = access_codec.verify(
                AccessToken.from_transport(recovered_access_value),
                now=datetime.now(UTC),
            )
            assert claims.user_id == user.id
            assert claims.session_id == successor.id
            assert recovered_claims.user_id == user.id
            assert recovered_claims.session_id == successor.id
            assert claims.expires_at == datetime.fromisoformat(
                refreshed.json()["expires_at"].replace("Z", "+00:00")
            )
            assert recovered_claims.expires_at == datetime.fromisoformat(
                recovered.json()["expires_at"].replace("Z", "+00:00")
            )
    finally:
        keys_after = _rate_limit_keys(redis_client)
        created_keys = keys_after - keys_before
        if created_keys:
            redis_client.delete(*created_keys)
        redis_client.close()
