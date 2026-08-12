"""Exercise /auth/me against real login, refresh, and PostgreSQL state."""

from datetime import UTC, datetime
from ipaddress import IPv6Address
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from industry_platform.main import create_app
from industry_platform.modules.identity.http_cookies import CSRF_COOKIE_NAME
from industry_platform.modules.identity.models import RefreshSessionFamily
from industry_platform.modules.identity.schemas import BEARER_SCHEME
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe

RAW_VALUE = "correct-horse-battery-staple"
CSRF_HEADER_NAME = "X-CSRF-Token"


def authorization_header(value: str) -> dict[str, str]:
    return {"Authorization": f"{BEARER_SCHEME} {value}"}


def test_me_rechecks_live_session_after_login_and_refresh(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    settings = migrated_postgres_probe.settings
    trusted_origin = settings.browser_trusted_origins[0]
    email = f"http-me-{uuid4().hex}@example.com"
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
        first_access = logged_in.json()["access_token"]
        current = client.get(
            "/api/v1/auth/me",
            headers=authorization_header(first_access),
        )

        assert current.status_code == 200
        assert current.json() == {
            "user": registered.json()["user"],
            "workspaces": [registered.json()["workspace"]],
        }

        csrf_value = client.cookies.get(CSRF_COOKIE_NAME)
        if not isinstance(csrf_value, str):
            raise AssertionError("Login did not provide the CSRF cookie")

        refreshed = client.post(
            "/api/v1/auth/refresh",
            headers={
                "Origin": trusted_origin,
                CSRF_HEADER_NAME: csrf_value,
            },
        )

        assert refreshed.status_code == 200
        second_access = refreshed.json()["access_token"]
        predecessor_access = client.get(
            "/api/v1/auth/me",
            headers=authorization_header(first_access),
        )
        successor_access = client.get(
            "/api/v1/auth/me",
            headers=authorization_header(second_access),
        )
        assert predecessor_access.status_code == 200
        assert successor_access.status_code == 200

        with Session(migrated_postgres_probe.engine) as session:
            family = session.scalars(select(RefreshSessionFamily)).one()
            family.revoked_at = datetime.now(UTC)
            family.revocation_reason = "integration_probe"
            session.commit()

        for encoded_value in (first_access, second_access):
            rejected = client.get(
                "/api/v1/auth/me",
                headers=authorization_header(encoded_value),
            )
            assert rejected.status_code == 401
            assert rejected.json()["code"] == "INVALID_AUTHENTICATED_SESSION"
            assert rejected.headers["www-authenticate"] == BEARER_SCHEME
            assert encoded_value not in rejected.text

        assert UUID(current.json()["user"]["id"]) == UUID(registered.json()["user"]["id"])
