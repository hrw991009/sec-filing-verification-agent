"""Ports for workspace membership persistence and application services."""

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from industry_platform.modules.identity.domain import (
    AuthenticatedPrincipal,
    TraceId,
    WorkspaceRoleName,
)
from industry_platform.modules.workspaces.domain import (
    AddWorkspaceMemberCommand,
    ChangeWorkspaceMemberRoleCommand,
    RemoveWorkspaceMemberCommand,
    WorkspaceMembershipRecord,
    WorkspaceMemberSummary,
    WorkspaceScope,
    WorkspaceSummary,
)


@dataclass(frozen=True, slots=True)
class LockedWorkspaceAuthorization:
    """Workspace and actor state locked before any target User is inspected."""

    workspace_id: UUID
    actor_scope: WorkspaceScope | None


@dataclass(frozen=True, slots=True)
class LockedWorkspaceTargetUsers:
    """Target and owner account state locked after authorization succeeds."""

    target_user_is_active: bool
    active_owner_user_ids: frozenset[UUID]


class WorkspaceMembershipWriter(Protocol):
    """Atomic operations available while workspace rows remain locked."""

    async def lock_workspace_authorization(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
    ) -> LockedWorkspaceAuthorization | None: ...

    async def lock_authorized_workspace_memberships(
        self,
        *,
        workspace_id: UUID,
    ) -> tuple[WorkspaceMembershipRecord, ...]:
        """Lock the target-bearing membership snapshot after authorization."""
        ...

    async def lock_authorized_target_users(
        self,
        *,
        target_user_id: UUID,
        owner_user_ids: frozenset[UUID],
    ) -> LockedWorkspaceTargetUsers:
        """Lock target-related users only after the application authorizes access."""
        ...

    async def add_membership(
        self, *, workspace_id: UUID, user_id: UUID, role: WorkspaceRoleName
    ) -> WorkspaceMembershipRecord: ...

    async def change_membership_role(
        self, *, user_id: UUID, role: WorkspaceRoleName
    ) -> WorkspaceMembershipRecord: ...

    async def remove_membership(self, *, user_id: UUID) -> WorkspaceMembershipRecord: ...

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
    ) -> None: ...


class WorkspaceMembershipTransactionFactory(Protocol):
    """Create one all-or-nothing workspace membership transaction."""

    def __call__(self) -> AbstractAsyncContextManager[WorkspaceMembershipWriter]: ...


class WorkspaceMembershipUseCase(Protocol):
    """Application boundary consumed later by HTTP routes."""

    async def add_member(self, command: AddWorkspaceMemberCommand) -> WorkspaceMembershipRecord: ...

    async def change_member_role(
        self, command: ChangeWorkspaceMemberRoleCommand
    ) -> WorkspaceMembershipRecord: ...

    async def remove_member(
        self, command: RemoveWorkspaceMemberCommand
    ) -> WorkspaceMembershipRecord: ...


class WorkspaceQueryRepository(Protocol):
    """Read workspace data using an explicit server-derived scope."""

    async def list_members(self, scope: WorkspaceScope) -> tuple[WorkspaceMemberSummary, ...]: ...


class WorkspaceQueryUseCase(Protocol):
    """Read boundary consumed by protected workspace routes."""

    def list_workspaces(
        self, principal: AuthenticatedPrincipal
    ) -> tuple[WorkspaceSummary, ...]: ...

    async def list_members(
        self, principal: AuthenticatedPrincipal, workspace_id: UUID
    ) -> tuple[WorkspaceMemberSummary, ...]: ...
