"""Unit tests for workspace authorization and locked membership mutations."""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from uuid import UUID

import pytest

from industry_platform.modules.identity.domain import TraceId, WorkspaceRoleName
from industry_platform.modules.workspaces.domain import (
    AddWorkspaceMemberCommand,
    ChangeWorkspaceMemberRoleCommand,
    LastWorkspaceOwnerError,
    RemoveWorkspaceMemberCommand,
    WorkspaceAccessDeniedError,
    WorkspaceAction,
    WorkspaceMembershipNotFoundError,
    WorkspaceMembershipRecord,
    WorkspaceScope,
)
from industry_platform.modules.workspaces.policy import (
    WORKSPACE_ROLE_ACTIONS,
    can_add_role,
    can_manage_membership,
)
from industry_platform.modules.workspaces.ports import (
    LockedWorkspaceAuthorization,
    LockedWorkspaceTargetUsers,
    WorkspaceMembershipWriter,
)
from industry_platform.modules.workspaces.service import WorkspaceMembershipService

WORKSPACE_ID = UUID(int=1)
OTHER_WORKSPACE_ID = UUID(int=2)
OWNER_ID = UUID(int=11)
ADMIN_ID = UUID(int=12)
MEMBER_ID = UUID(int=13)
VIEWER_ID = UUID(int=14)
OUTSIDER_ID = UUID(int=15)
TRACE_ID = TraceId("workspace-test-trace")


def membership(user_id: UUID, role: WorkspaceRoleName) -> WorkspaceMembershipRecord:
    return WorkspaceMembershipRecord(
        membership_id=UUID(int=user_id.int + 100),
        workspace_id=WORKSPACE_ID,
        user_id=user_id,
        role=role,
    )


def locked_state(
    actor_id: UUID,
    *memberships: WorkspaceMembershipRecord,
) -> tuple[
    LockedWorkspaceAuthorization,
    tuple[WorkspaceMembershipRecord, ...],
]:
    actor = next(item for item in memberships if item.user_id == actor_id)
    return (
        LockedWorkspaceAuthorization(
            workspace_id=WORKSPACE_ID,
            actor_scope=WorkspaceScope(
                workspace_id=WORKSPACE_ID,
                user_id=actor_id,
                role=actor.role,
            ),
        ),
        tuple(memberships),
    )


@dataclass(slots=True)
class FakeWriter:
    state: LockedWorkspaceAuthorization | None
    memberships: tuple[WorkspaceMembershipRecord, ...]
    target_user_is_active: bool = True
    active_owner_user_ids: frozenset[UUID] | None = None
    audits: list[tuple[str, str | None, str]] = field(default_factory=list)
    changed: list[tuple[str, UUID, WorkspaceRoleName | None]] = field(default_factory=list)
    target_user_reads: list[UUID] = field(default_factory=list)
    membership_reads: int = 0

    async def lock_workspace_authorization(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
    ) -> LockedWorkspaceAuthorization | None:
        del workspace_id, actor_user_id
        return self.state

    async def lock_authorized_workspace_memberships(
        self,
        *,
        workspace_id: UUID,
    ) -> tuple[WorkspaceMembershipRecord, ...]:
        del workspace_id
        self.membership_reads += 1
        return self.memberships

    async def lock_authorized_target_users(
        self,
        *,
        target_user_id: UUID,
        owner_user_ids: frozenset[UUID],
    ) -> LockedWorkspaceTargetUsers:
        self.target_user_reads.append(target_user_id)
        assert self.state is not None
        active_owner_user_ids = (
            self.active_owner_user_ids
            if self.active_owner_user_ids is not None
            else frozenset(
                membership.user_id for membership in self.memberships if membership.role == "owner"
            )
        )
        return LockedWorkspaceTargetUsers(
            target_user_is_active=self.target_user_is_active,
            active_owner_user_ids=owner_user_ids & active_owner_user_ids,
        )

    async def add_membership(
        self, *, workspace_id: UUID, user_id: UUID, role: WorkspaceRoleName
    ) -> WorkspaceMembershipRecord:
        self.changed.append(("add", user_id, role))
        return WorkspaceMembershipRecord(UUID(int=999), workspace_id, user_id, role)

    async def change_membership_role(
        self, *, user_id: UUID, role: WorkspaceRoleName
    ) -> WorkspaceMembershipRecord:
        self.changed.append(("change", user_id, role))
        return WorkspaceMembershipRecord(UUID(int=998), WORKSPACE_ID, user_id, role)

    async def remove_membership(self, *, user_id: UUID) -> WorkspaceMembershipRecord:
        self.changed.append(("remove", user_id, None))
        target = next(item for item in self.memberships if item.user_id == user_id)
        return target

    async def record_audit(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        action: str,
        resource_id: UUID,
        outcome: str,
        reason: str | None,
        trace_id: TraceId,
    ) -> None:
        del workspace_id, actor_user_id, resource_id, trace_id
        self.audits.append((action, reason, outcome))


@dataclass(slots=True)
class FakeTransactionFactory:
    writer: FakeWriter
    committed: bool = False

    def __call__(self) -> AbstractAsyncContextManager[WorkspaceMembershipWriter]:
        return self.transaction()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[WorkspaceMembershipWriter]:
        yield self.writer
        self.committed = True


def test_role_action_matrix_is_explicit_and_closed() -> None:
    assert {
        "owner": frozenset(WorkspaceAction),
        "admin": frozenset(
            {
                WorkspaceAction.VIEW,
                WorkspaceAction.CREATE_RESOURCE,
                WorkspaceAction.UPDATE_RESOURCE,
                WorkspaceAction.DELETE_RESOURCE,
                WorkspaceAction.RUN_TOOL,
                WorkspaceAction.RUN_RESEARCH,
                WorkspaceAction.LIST_MEMBERS,
                WorkspaceAction.ADD_MEMBER,
                WorkspaceAction.CHANGE_MEMBER_ROLE,
                WorkspaceAction.REMOVE_MEMBER,
            }
        ),
        "member": frozenset(
            {
                WorkspaceAction.VIEW,
                WorkspaceAction.CREATE_RESOURCE,
                WorkspaceAction.UPDATE_RESOURCE,
                WorkspaceAction.DELETE_RESOURCE,
                WorkspaceAction.RUN_TOOL,
                WorkspaceAction.RUN_RESEARCH,
            }
        ),
        "viewer": frozenset({WorkspaceAction.VIEW}),
    } == WORKSPACE_ROLE_ACTIONS


def test_admin_cannot_assign_or_operate_on_high_privilege_roles() -> None:
    admin_scope = WorkspaceScope(WORKSPACE_ID, ADMIN_ID, "admin")

    assert not can_add_role(admin_scope, "admin")
    assert not can_add_role(admin_scope, "owner")
    assert not can_manage_membership(
        admin_scope,
        membership(OWNER_ID, "owner"),
        action=WorkspaceAction.REMOVE_MEMBER,
    )
    assert not can_manage_membership(
        admin_scope,
        membership(ADMIN_ID, "admin"),
        action=WorkspaceAction.CHANGE_MEMBER_ROLE,
        requested_role="owner",
    )


@pytest.mark.asyncio
async def test_service_uses_current_server_membership_instead_of_client_role() -> None:
    writer = FakeWriter(*locked_state(MEMBER_ID, membership(MEMBER_ID, "member")))
    service = WorkspaceMembershipService(FakeTransactionFactory(writer))

    with pytest.raises(WorkspaceAccessDeniedError):
        await service.add_member(
            AddWorkspaceMemberCommand(WORKSPACE_ID, MEMBER_ID, OUTSIDER_ID, "viewer", TRACE_ID)
        )

    assert writer.changed == []
    assert writer.audits == [(WorkspaceAction.ADD_MEMBER.value, "action_not_allowed", "denied")]


@pytest.mark.asyncio
async def test_unauthorized_actor_cannot_probe_target_membership() -> None:
    writer = FakeWriter(
        *locked_state(
            VIEWER_ID,
            membership(VIEWER_ID, "viewer"),
            membership(MEMBER_ID, "member"),
        )
    )
    service = WorkspaceMembershipService(FakeTransactionFactory(writer))

    for target_user_id in (MEMBER_ID, OUTSIDER_ID):
        with pytest.raises(WorkspaceAccessDeniedError):
            await service.remove_member(
                RemoveWorkspaceMemberCommand(
                    WORKSPACE_ID,
                    VIEWER_ID,
                    target_user_id,
                    TRACE_ID,
                )
            )

    assert writer.changed == []
    assert writer.target_user_reads == []
    assert writer.membership_reads == 0
    assert writer.audits == [
        (WorkspaceAction.REMOVE_MEMBER.value, "action_not_allowed", "denied"),
        (WorkspaceAction.REMOVE_MEMBER.value, "action_not_allowed", "denied"),
    ]


@pytest.mark.asyncio
async def test_nonmember_is_rejected_before_target_user_is_read() -> None:
    writer = FakeWriter(
        LockedWorkspaceAuthorization(
            workspace_id=WORKSPACE_ID,
            actor_scope=None,
        ),
        (membership(OWNER_ID, "owner"),),
    )
    service = WorkspaceMembershipService(FakeTransactionFactory(writer))

    with pytest.raises(WorkspaceAccessDeniedError):
        await service.remove_member(
            RemoveWorkspaceMemberCommand(
                WORKSPACE_ID,
                OUTSIDER_ID,
                OWNER_ID,
                TRACE_ID,
            )
        )

    assert writer.target_user_reads == []
    assert writer.membership_reads == 0


@pytest.mark.asyncio
async def test_inactive_target_cannot_be_added_to_workspace() -> None:
    writer = FakeWriter(
        *locked_state(OWNER_ID, membership(OWNER_ID, "owner")),
        target_user_is_active=False,
    )
    service = WorkspaceMembershipService(FakeTransactionFactory(writer))

    with pytest.raises(WorkspaceMembershipNotFoundError):
        await service.add_member(
            AddWorkspaceMemberCommand(
                WORKSPACE_ID,
                OWNER_ID,
                OUTSIDER_ID,
                "viewer",
                TRACE_ID,
            )
        )

    assert writer.changed == []


@pytest.mark.asyncio
async def test_last_owner_denial_is_committed_before_error_is_raised() -> None:
    writer = FakeWriter(*locked_state(OWNER_ID, membership(OWNER_ID, "owner")))
    factory = FakeTransactionFactory(writer)
    service = WorkspaceMembershipService(factory)

    with pytest.raises(LastWorkspaceOwnerError):
        await service.remove_member(
            RemoveWorkspaceMemberCommand(WORKSPACE_ID, OWNER_ID, OWNER_ID, TRACE_ID)
        )

    assert factory.committed
    assert writer.changed == []
    assert writer.audits == [(WorkspaceAction.REMOVE_MEMBER.value, "last_owner", "denied")]


@pytest.mark.asyncio
async def test_owner_can_demote_self_when_another_owner_remains() -> None:
    second_owner = membership(OUTSIDER_ID, "owner")
    writer = FakeWriter(
        *locked_state(
            OWNER_ID,
            membership(OWNER_ID, "owner"),
            second_owner,
        )
    )
    service = WorkspaceMembershipService(FakeTransactionFactory(writer))

    changed = await service.change_member_role(
        ChangeWorkspaceMemberRoleCommand(WORKSPACE_ID, OWNER_ID, OWNER_ID, "member", TRACE_ID)
    )

    assert changed.role == "member"
    assert writer.changed == [("change", OWNER_ID, "member")]


@pytest.mark.asyncio
async def test_inactive_owner_does_not_satisfy_last_owner_invariant() -> None:
    inactive_owner = membership(OUTSIDER_ID, "owner")
    writer = FakeWriter(
        *locked_state(
            OWNER_ID,
            membership(OWNER_ID, "owner"),
            inactive_owner,
        ),
        active_owner_user_ids=frozenset({OWNER_ID}),
    )
    service = WorkspaceMembershipService(FakeTransactionFactory(writer))

    with pytest.raises(LastWorkspaceOwnerError):
        await service.change_member_role(
            ChangeWorkspaceMemberRoleCommand(
                WORKSPACE_ID,
                OWNER_ID,
                OWNER_ID,
                "member",
                TRACE_ID,
            )
        )

    assert writer.changed == []


@pytest.mark.asyncio
async def test_target_from_another_workspace_is_not_mutated() -> None:
    writer = FakeWriter(*locked_state(OWNER_ID, membership(OWNER_ID, "owner")))
    service = WorkspaceMembershipService(FakeTransactionFactory(writer))

    with pytest.raises(WorkspaceMembershipNotFoundError):
        await service.remove_member(
            RemoveWorkspaceMemberCommand(WORKSPACE_ID, OWNER_ID, OUTSIDER_ID, TRACE_ID)
        )

    assert writer.changed == []
