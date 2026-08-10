"""Exercise the public registration endpoint against real PostgreSQL."""

import logging

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from industry_platform.core.http import PROBLEM_MEDIA_TYPE
from industry_platform.main import create_app
from industry_platform.modules.identity.models import (
    AuditLog,
    User,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
)
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe

VALID_RAW_VALUE = "correct-horse-battery-staple"


def test_registration_http_contract_commits_one_complete_registration(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    """Cross HTTP, application, hashing, transaction, and database boundaries."""

    http_logger = logging.getLogger("industry_platform.core.http")
    assert not http_logger.disabled

    application = create_app(settings=migrated_postgres_probe.settings)

    with TestClient(
        application,
        backend_options={"loop_factory": create_selector_event_loop},
    ) as client:
        created = client.post(
            "/api/v1/auth/register",
            json={
                "email": "Http.User@EXAMPLE.com",
                "password": VALID_RAW_VALUE,
            },
        )
        duplicate = client.post(
            "/api/v1/auth/register",
            json={
                "email": "http.user@example.COM",
                "password": VALID_RAW_VALUE,
            },
        )

    assert created.status_code == 201
    assert created.json()["user"]["email"] == "http.user@example.com"
    assert created.json()["workspace"]["name"] == "My Workspace"
    assert created.json()["workspace"]["role"] == "owner"
    assert VALID_RAW_VALUE not in created.text

    assert duplicate.status_code == 409
    assert duplicate.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert duplicate.json()["code"] == "EMAIL_ALREADY_REGISTERED"
    assert duplicate.json()["trace_id"] == duplicate.headers["X-Trace-ID"]
    assert VALID_RAW_VALUE not in duplicate.text
    assert "uq_users_email" not in duplicate.text
    assert "IntegrityError" not in duplicate.text

    with Session(migrated_postgres_probe.engine) as session:
        user = session.scalars(select(User)).one()
        workspace = session.scalars(select(Workspace)).one()
        membership = session.scalars(select(WorkspaceMembership)).one()
        audit_log = session.scalars(select(AuditLog)).one()

    assert str(user.id) == created.json()["user"]["id"]
    assert user.email == "http.user@example.com"
    assert user.password_hash.startswith("$argon2id$")
    assert user.password_hash != VALID_RAW_VALUE
    assert str(workspace.id) == created.json()["workspace"]["id"]
    assert membership.user_id == user.id
    assert membership.workspace_id == workspace.id
    assert membership.role is WorkspaceRole.OWNER
    assert audit_log.actor_user_id == user.id
    assert audit_log.workspace_id == workspace.id
    assert audit_log.trace_id == created.headers["X-Trace-ID"]
    assert audit_log.sanitized_metadata == {
        "source": "self_service",
        "role": "owner",
    }
