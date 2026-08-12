"""Domain values and errors for workspace authorization."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal
from uuid import UUID

from industry_platform.modules.identity.domain import (
    AccountStatus,
    NormalizedEmail,
    TraceId,
    WorkspaceRoleName,
)


class WorkspaceAction(StrEnum):
    """Actions governed by the workspace role policy."""

    VIEW = "workspace.view"
    CREATE_RESOURCE = "workspace.resources.create"
    UPDATE_RESOURCE = "workspace.resources.update"
    DELETE_RESOURCE = "workspace.resources.delete"
    RUN_TOOL = "workspace.tools.run"
    RUN_RESEARCH = "workspace.research.run"
    LIST_MEMBERS = "workspace.members.list"
    ADD_MEMBER = "workspace.members.add"
    CHANGE_MEMBER_ROLE = "workspace.members.change_role"
    REMOVE_MEMBER = "workspace.members.remove"
    UPDATE_SETTINGS = "workspace.settings.update"
    DELETE = "workspace.delete"


@dataclass(frozen=True, slots=True)
class WorkspaceScope:
    """Server-resolved authorization scope for one active membership."""

    workspace_id: UUID
    user_id: UUID
    role: WorkspaceRoleName


@dataclass(frozen=True, slots=True)
class WorkspaceMembershipRecord:
    """Current membership data used by policy decisions."""

    membership_id: UUID
    workspace_id: UUID
    user_id: UUID
    role: WorkspaceRoleName


@dataclass(frozen=True, slots=True)
class WorkspaceSummary:
    """One current workspace exposed by the authenticated principal."""

    workspace_id: UUID
    name: str
    role: WorkspaceRoleName


@dataclass(frozen=True, slots=True)
class WorkspaceMemberSummary:
    """Sanitized member data returned to workspace administrators."""

    membership_id: UUID
    user_id: UUID
    email: NormalizedEmail
    role: WorkspaceRoleName
    account_status: AccountStatus


@dataclass(frozen=True, slots=True)
class LockedWorkspaceMemberships:
    """Workspace membership snapshot protected by database row locks."""

    workspace_id: UUID
    actor_scope: WorkspaceScope | None
    target_user_is_active: bool
    active_owner_user_ids: frozenset[UUID]
    memberships: tuple[WorkspaceMembershipRecord, ...]


@dataclass(frozen=True, slots=True)
class AddWorkspaceMemberCommand:
    """Request to add a user to a workspace."""

    workspace_id: UUID
    actor_user_id: UUID
    target_user_id: UUID
    role: WorkspaceRoleName
    trace_id: TraceId


@dataclass(frozen=True, slots=True)
class ChangeWorkspaceMemberRoleCommand:
    """Request to replace a member's workspace role."""

    workspace_id: UUID
    actor_user_id: UUID
    target_user_id: UUID
    role: WorkspaceRoleName
    trace_id: TraceId


@dataclass(frozen=True, slots=True)
class RemoveWorkspaceMemberCommand:
    """Request to remove a member from a workspace."""

    workspace_id: UUID
    actor_user_id: UUID
    target_user_id: UUID
    trace_id: TraceId


type WorkspaceMembershipCommand = (
    AddWorkspaceMemberCommand | ChangeWorkspaceMemberRoleCommand | RemoveWorkspaceMemberCommand
)


type WorkspaceDenialReason = Literal[
    "workspace_not_found",
    "actor_not_member",
    "action_not_allowed",
    "self_promotion",
    "protected_role",
    "target_not_found",
    "target_already_member",
    "last_owner",
]


@dataclass(frozen=True, slots=True)
class WorkspaceMutationDecision:
    """Result returned after a membership transaction has committed."""

    membership: WorkspaceMembershipRecord | None = None
    denial_reason: WorkspaceDenialReason | None = None

    def __post_init__(self) -> None:
        if (self.membership is None) == (self.denial_reason is None):
            message = "A workspace mutation decision must contain one result"
            raise ValueError(message)


class WorkspaceAccessDeniedError(RuntimeError):
    """Raised when the current server-side role cannot perform an action."""


class WorkspaceMembershipNotFoundError(RuntimeError):
    """Raised when the target is not a member of the selected workspace."""


class WorkspaceMembershipConflictError(RuntimeError):
    """Raised when a requested membership already exists."""


class LastWorkspaceOwnerError(RuntimeError):
    """Raised after committing an audit for a rejected last-owner mutation."""


class WorkspacePersistenceError(RuntimeError):
    """Raised when workspace state could not be read or persisted."""

    def __init__(self, *, sqlstate: str | None) -> None:
        super().__init__("Workspace membership persistence failed")
        self.sqlstate = sqlstate
