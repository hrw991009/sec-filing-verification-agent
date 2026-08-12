"""Exercise workspace RBAC HTTP contracts against real PostgreSQL state."""

from ipaddress import IPv6Address
from typing import TypedDict, cast
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from industry_platform.main import create_app
from industry_platform.modules.identity.models import AuditLog, AuditOutcome
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe

RAW_VALUE = "correct-horse-battery-staple"


class RegisteredUser(TypedDict):
    id: str
    email: str


class RegisteredWorkspace(TypedDict):
    id: str
    name: str
    role: str


class RegistrationPayload(TypedDict):
    user: RegisteredUser
    workspace: RegisteredWorkspace


def bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


def register_and_login(
    client: TestClient,
    *,
    label: str,
) -> tuple[RegistrationPayload, str]:
    email = f"workspace-{label}-{uuid4().hex}@example.com"
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
    access_token = logged_in.json()["access_token"]
    if not isinstance(access_token, str):
        raise AssertionError("Login did not return an access token")
    return cast(RegistrationPayload, registered.json()), access_token


def test_workspace_http_enforces_live_roles_and_tenant_boundaries(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    settings = migrated_postgres_probe.settings
    trusted_origin = settings.browser_trusted_origins[0]
    application = create_app(settings=settings)

    with TestClient(
        application,
        base_url=trusted_origin,
        client=(str(IPv6Address(uuid4().int)), 50_010),
        backend_options={"loop_factory": create_selector_event_loop},
    ) as client:
        owner, owner_access = register_and_login(client, label="owner")
        admin, admin_access = register_and_login(client, label="admin")
        member, member_access = register_and_login(client, label="member")
        _outsider, outsider_access = register_and_login(client, label="outsider")

        workspace_id = UUID(owner["workspace"]["id"])
        owner_id = UUID(owner["user"]["id"])
        admin_id = UUID(admin["user"]["id"])
        member_id = UUID(member["user"]["id"])

        added_admin = client.post(
            f"/api/v1/workspaces/{workspace_id}/members",
            headers=bearer(owner_access),
            json={"user_id": str(admin_id), "role": "admin"},
        )
        added_member = client.post(
            f"/api/v1/workspaces/{workspace_id}/members",
            headers=bearer(owner_access),
            json={"user_id": str(member_id), "role": "member"},
        )
        assert added_admin.status_code == 201
        assert added_member.status_code == 201

        owner_members = client.get(
            f"/api/v1/workspaces/{workspace_id}/members",
            headers=bearer(owner_access),
        )
        admin_members = client.get(
            f"/api/v1/workspaces/{workspace_id}/members",
            headers=bearer(admin_access),
        )
        member_members = client.get(
            f"/api/v1/workspaces/{workspace_id}/members",
            headers=bearer(member_access),
        )
        cross_tenant_members = client.get(
            f"/api/v1/workspaces/{workspace_id}/members",
            headers=bearer(outsider_access),
        )

        assert owner_members.status_code == 200
        assert admin_members.status_code == 200
        assert len(owner_members.json()["members"]) == 3
        assert member_members.status_code == 403
        assert member_members.json()["code"] == "WORKSPACE_ACCESS_DENIED"
        assert cross_tenant_members.status_code == 403
        assert cross_tenant_members.json()["code"] == "WORKSPACE_ACCESS_DENIED"

        admin_cannot_change_owner = client.patch(
            f"/api/v1/workspaces/{workspace_id}/members/{owner_id}",
            headers=bearer(admin_access),
            json={"role": "viewer"},
        )
        admin_cannot_grant_owner = client.post(
            f"/api/v1/workspaces/{workspace_id}/members",
            headers=bearer(admin_access),
            json={
                "user_id": _outsider["user"]["id"],
                "role": "owner",
            },
        )
        admin_cannot_promote_self = client.patch(
            f"/api/v1/workspaces/{workspace_id}/members/{admin_id}",
            headers=bearer(admin_access),
            json={"role": "owner"},
        )
        unauthorized_unknown_target = client.delete(
            f"/api/v1/workspaces/{workspace_id}/members/{uuid4()}",
            headers=bearer(member_access),
        )
        authorized_unknown_target = client.delete(
            f"/api/v1/workspaces/{workspace_id}/members/{uuid4()}",
            headers=bearer(owner_access),
        )
        last_owner = client.delete(
            f"/api/v1/workspaces/{workspace_id}/members/{owner_id}",
            headers=bearer(owner_access),
        )

        assert admin_cannot_change_owner.status_code == 403
        assert admin_cannot_grant_owner.status_code == 403
        assert admin_cannot_promote_self.status_code == 403
        assert unauthorized_unknown_target.status_code == 403
        assert authorized_unknown_target.status_code == 404
        assert last_owner.status_code == 409
        assert last_owner.json()["code"] == "LAST_WORKSPACE_OWNER"

        admin_workspaces = client.get(
            "/api/v1/workspaces",
            headers=bearer(admin_access),
        )
        assert admin_workspaces.status_code == 200
        roles = {
            UUID(workspace["id"]): workspace["role"]
            for workspace in admin_workspaces.json()["workspaces"]
        }
        assert roles[workspace_id] == "admin"

    with Session(migrated_postgres_probe.engine) as session:
        last_owner_audit = session.scalar(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.actor_user_id == owner_id,
                AuditLog.resource_id == owner_id,
                AuditLog.action == "workspace.members.remove",
            )
        )
        successful_add_audit = session.scalar(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.actor_user_id == owner_id,
                AuditLog.resource_id == admin_id,
                AuditLog.action == "workspace.members.add",
            )
        )
        assert last_owner_audit is not None
        assert last_owner_audit.outcome is AuditOutcome.DENIED
        assert last_owner_audit.sanitized_metadata == {"reason": "last_owner"}
        assert successful_add_audit is not None
        assert successful_add_audit.outcome is AuditOutcome.SUCCEEDED
