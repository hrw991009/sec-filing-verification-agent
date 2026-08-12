"""HTTP contract tests for protected workspace endpoints."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response as HttpxResponse

from industry_platform.core.config import Settings
from industry_platform.core.http import PROBLEM_MEDIA_TYPE
from industry_platform.main import create_app
from industry_platform.modules.identity.domain import (
    AccessToken,
    AuthenticatedPrincipal,
    AuthenticatedWorkspace,
    NormalizedEmail,
)
from industry_platform.modules.identity.http_auth import get_principal_resolver
from industry_platform.modules.workspaces.domain import (
    AddWorkspaceMemberCommand,
    ChangeWorkspaceMemberRoleCommand,
    LastWorkspaceOwnerError,
    RemoveWorkspaceMemberCommand,
    WorkspaceAccessDeniedError,
    WorkspaceMembershipCommand,
    WorkspaceMembershipConflictError,
    WorkspaceMembershipNotFoundError,
    WorkspaceMembershipRecord,
    WorkspaceMemberSummary,
    WorkspacePersistenceError,
    WorkspaceSummary,
)
from industry_platform.modules.workspaces.ports import (
    WorkspaceMembershipUseCase,
    WorkspaceQueryUseCase,
)
from industry_platform.modules.workspaces.router import (
    get_workspace_membership_service,
    get_workspace_query_service,
)

USER_ID = UUID("11111111-1111-4111-8111-111111111111")
TARGET_ID = UUID("22222222-2222-4222-8222-222222222222")
SESSION_ID = UUID("33333333-3333-4333-8333-333333333333")
WORKSPACE_ID = UUID("44444444-4444-4444-8444-444444444444")
MEMBERSHIP_ID = UUID("55555555-5555-4555-8555-555555555555")
RAW_ACCESS_VALUE = ".".join(("header", "payload", "signature"))


@dataclass(slots=True)
class StubPrincipalResolver:
    principal: AuthenticatedPrincipal

    async def resolve(self, _token: AccessToken) -> AuthenticatedPrincipal:
        return self.principal


@dataclass(slots=True)
class StubQueryService:
    workspaces: tuple[WorkspaceSummary, ...]
    members: tuple[WorkspaceMemberSummary, ...]
    requested_members: list[tuple[UUID, UUID]] = field(default_factory=list)

    def list_workspaces(self, _principal: AuthenticatedPrincipal) -> tuple[WorkspaceSummary, ...]:
        return self.workspaces

    async def list_members(
        self, principal: AuthenticatedPrincipal, workspace_id: UUID
    ) -> tuple[WorkspaceMemberSummary, ...]:
        self.requested_members.append((principal.user_id, workspace_id))
        return self.members


@dataclass(slots=True)
class StubMembershipService:
    failure: Exception | None = None
    commands: list[WorkspaceMembershipCommand] = field(default_factory=list)

    async def add_member(self, command: AddWorkspaceMemberCommand) -> WorkspaceMembershipRecord:
        return self._complete(command)

    async def change_member_role(
        self, command: ChangeWorkspaceMemberRoleCommand
    ) -> WorkspaceMembershipRecord:
        return self._complete(command)

    async def remove_member(
        self, command: RemoveWorkspaceMemberCommand
    ) -> WorkspaceMembershipRecord:
        return self._complete(command)

    def _complete(self, command: WorkspaceMembershipCommand) -> WorkspaceMembershipRecord:
        self.commands.append(command)
        if self.failure is not None:
            raise self.failure
        role = (
            command.role
            if isinstance(
                command,
                AddWorkspaceMemberCommand | ChangeWorkspaceMemberRoleCommand,
            )
            else "viewer"
        )
        return WorkspaceMembershipRecord(
            membership_id=MEMBERSHIP_ID,
            workspace_id=command.workspace_id,
            user_id=command.target_user_id,
            role=role,
        )


def principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        user_id=USER_ID,
        session_id=SESSION_ID,
        email=NormalizedEmail("owner@example.com"),
        workspaces=(AuthenticatedWorkspace(WORKSPACE_ID, "Workspace", "owner"),),
    )


def query_service() -> StubQueryService:
    return StubQueryService(
        workspaces=(WorkspaceSummary(WORKSPACE_ID, "Workspace", "owner"),),
        members=(
            WorkspaceMemberSummary(
                membership_id=MEMBERSHIP_ID,
                user_id=USER_ID,
                email=NormalizedEmail("owner@example.com"),
                role="owner",
                account_status="active",
            ),
        ),
    )


@contextmanager
def workspace_client(
    settings: Settings,
    *,
    query: WorkspaceQueryUseCase | None = None,
    membership: WorkspaceMembershipUseCase | None = None,
) -> Iterator[TestClient]:
    application = create_app(settings=settings)
    application.dependency_overrides[get_principal_resolver] = lambda: StubPrincipalResolver(
        principal()
    )
    application.dependency_overrides[get_workspace_query_service] = lambda: (
        query if query is not None else query_service()
    )
    application.dependency_overrides[get_workspace_membership_service] = lambda: (
        membership if membership is not None else StubMembershipService()
    )
    with TestClient(application, base_url="https://localhost") as client:
        yield client


def bearer_header() -> dict[str, str]:
    return {"Authorization": f"Bearer {RAW_ACCESS_VALUE}"}


def assert_problem(response: HttpxResponse, status_code: int, code: str) -> None:
    assert response.status_code == status_code
    assert response.headers["content-type"].startswith(PROBLEM_MEDIA_TYPE)
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["code"] == code
    assert response.json()["trace_id"] == response.headers["X-Trace-ID"]


def test_lists_principal_workspaces_and_authorized_members(
    test_settings: Settings,
) -> None:
    query = query_service()
    with workspace_client(test_settings, query=query) as client:
        workspaces = client.get("/api/v1/workspaces", headers=bearer_header())
        members = client.get(
            f"/api/v1/workspaces/{WORKSPACE_ID}/members",
            headers=bearer_header(),
        )

    assert workspaces.status_code == 200
    assert workspaces.json()["workspaces"] == [
        {"id": str(WORKSPACE_ID), "name": "Workspace", "role": "owner"}
    ]
    assert members.status_code == 200
    assert members.json()["members"][0] == {
        "membership_id": str(MEMBERSHIP_ID),
        "user_id": str(USER_ID),
        "email": "owner@example.com",
        "role": "owner",
        "account_status": "active",
    }
    assert query.requested_members == [(USER_ID, WORKSPACE_ID)]
    assert workspaces.headers["cache-control"] == "no-store"
    assert members.headers["cache-control"] == "no-store"


def test_mutation_routes_build_commands_from_the_authenticated_actor(
    test_settings: Settings,
) -> None:
    service = StubMembershipService()
    with workspace_client(test_settings, membership=service) as client:
        added = client.post(
            f"/api/v1/workspaces/{WORKSPACE_ID}/members",
            headers=bearer_header(),
            json={"user_id": str(TARGET_ID), "role": "member"},
        )
        changed = client.patch(
            f"/api/v1/workspaces/{WORKSPACE_ID}/members/{TARGET_ID}",
            headers=bearer_header(),
            json={"role": "viewer"},
        )
        removed = client.delete(
            f"/api/v1/workspaces/{WORKSPACE_ID}/members/{TARGET_ID}",
            headers=bearer_header(),
        )

    assert added.status_code == 201
    assert changed.status_code == 200
    assert removed.status_code == 204
    assert added.headers["cache-control"] == "no-store"
    assert changed.headers["cache-control"] == "no-store"
    assert removed.headers["cache-control"] == "no-store"
    add_command, change_command, remove_command = service.commands
    assert isinstance(add_command, AddWorkspaceMemberCommand)
    assert isinstance(change_command, ChangeWorkspaceMemberRoleCommand)
    assert isinstance(remove_command, RemoveWorkspaceMemberCommand)
    assert add_command.role == "member"
    assert change_command.role == "viewer"
    for command, response in zip(
        service.commands,
        (added, changed, removed),
        strict=True,
    ):
        assert command.workspace_id == WORKSPACE_ID
        assert command.actor_user_id == USER_ID
        assert command.target_user_id == TARGET_ID
        assert command.trace_id == response.headers["X-Trace-ID"]


@pytest.mark.parametrize(
    ("failure", "status_code", "code"),
    [
        (WorkspaceAccessDeniedError(), 403, "WORKSPACE_ACCESS_DENIED"),
        (WorkspaceMembershipNotFoundError(), 404, "WORKSPACE_MEMBER_NOT_FOUND"),
        (WorkspaceMembershipConflictError(), 409, "WORKSPACE_MEMBERSHIP_CONFLICT"),
        (LastWorkspaceOwnerError(), 409, "LAST_WORKSPACE_OWNER"),
        (WorkspacePersistenceError(sqlstate="40001"), 503, "WORKSPACE_UNAVAILABLE"),
    ],
)
def test_workspace_failures_use_safe_problem_contracts(
    test_settings: Settings,
    failure: Exception,
    status_code: int,
    code: str,
) -> None:
    with workspace_client(
        test_settings,
        membership=StubMembershipService(failure=failure),
    ) as client:
        response = client.delete(
            f"/api/v1/workspaces/{WORKSPACE_ID}/members/{TARGET_ID}",
            headers=bearer_header(),
        )

    assert_problem(response, status_code, code)
    assert "40001" not in response.text


def test_protected_workspace_routes_require_bearer_authentication(
    test_settings: Settings,
) -> None:
    with workspace_client(test_settings) as client:
        responses = (
            client.get("/api/v1/workspaces"),
            client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/members"),
            client.post(
                f"/api/v1/workspaces/{WORKSPACE_ID}/members",
                json={"user_id": str(TARGET_ID), "role": "member"},
            ),
            client.patch(
                f"/api/v1/workspaces/{WORKSPACE_ID}/members/{TARGET_ID}",
                json={"role": "viewer"},
            ),
            client.delete(f"/api/v1/workspaces/{WORKSPACE_ID}/members/{TARGET_ID}"),
        )

    for response in responses:
        assert_problem(response, 401, "INVALID_AUTHENTICATED_SESSION")


def test_openapi_documents_bearer_and_problem_responses(
    test_settings: Settings,
) -> None:
    with workspace_client(test_settings) as client:
        document = client.get("/openapi.json").json()

    paths = document["paths"]
    operations = (
        paths["/api/v1/workspaces"]["get"],
        paths["/api/v1/workspaces/{workspace_id}/members"]["get"],
        paths["/api/v1/workspaces/{workspace_id}/members"]["post"],
        paths["/api/v1/workspaces/{workspace_id}/members/{user_id}"]["patch"],
        paths["/api/v1/workspaces/{workspace_id}/members/{user_id}"]["delete"],
    )
    for operation in operations:
        assert operation["security"] == [{"AccessToken": []}]
        assert "401" in operation["responses"]
        assert "500" in operation["responses"]
        assert "503" in operation["responses"]

    for operation in operations[1:]:
        assert set(operation["responses"]["422"]["content"]) == {PROBLEM_MEDIA_TYPE}
