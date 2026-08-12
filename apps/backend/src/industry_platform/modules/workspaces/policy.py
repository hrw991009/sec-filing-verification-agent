"""Fixed workspace role-to-action authorization policy."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from industry_platform.modules.identity.domain import WorkspaceRoleName
from industry_platform.modules.workspaces.domain import (
    WorkspaceAction,
    WorkspaceMembershipRecord,
    WorkspaceScope,
)

_MEMBER_RESOURCE_ACTIONS: Final = frozenset(
    {
        WorkspaceAction.VIEW,
        WorkspaceAction.CREATE_RESOURCE,
        WorkspaceAction.UPDATE_RESOURCE,
        WorkspaceAction.DELETE_RESOURCE,
        WorkspaceAction.RUN_TOOL,
        WorkspaceAction.RUN_RESEARCH,
    }
)
WORKSPACE_ROLE_ACTIONS: Final[Mapping[WorkspaceRoleName, frozenset[WorkspaceAction]]] = (
    MappingProxyType(
        {
            "owner": frozenset(WorkspaceAction),
            "admin": _MEMBER_RESOURCE_ACTIONS
            | frozenset(
                {
                    WorkspaceAction.LIST_MEMBERS,
                    WorkspaceAction.ADD_MEMBER,
                    WorkspaceAction.CHANGE_MEMBER_ROLE,
                    WorkspaceAction.REMOVE_MEMBER,
                }
            ),
            "member": _MEMBER_RESOURCE_ACTIONS,
            "viewer": frozenset({WorkspaceAction.VIEW}),
        }
    )
)

_ROLE_RANK: Final[Mapping[WorkspaceRoleName, int]] = MappingProxyType(
    {"viewer": 0, "member": 1, "admin": 2, "owner": 3}
)
_ADMIN_MANAGED_ROLES: Final = frozenset[WorkspaceRoleName]({"member", "viewer"})


def scope_allows(scope: WorkspaceScope, action: WorkspaceAction) -> bool:
    """Return whether the scope's current role contains an action."""

    return action in WORKSPACE_ROLE_ACTIONS[scope.role]


def can_add_role(scope: WorkspaceScope, role: WorkspaceRoleName) -> bool:
    """Apply both the action matrix and administrator role boundary."""

    if not scope_allows(scope, WorkspaceAction.ADD_MEMBER):
        return False
    return scope.role == "owner" or role in _ADMIN_MANAGED_ROLES


def can_manage_membership(
    scope: WorkspaceScope,
    target: WorkspaceMembershipRecord,
    *,
    action: WorkspaceAction,
    requested_role: WorkspaceRoleName | None = None,
) -> bool:
    """Protect high-privilege roles and reject self-promotion."""

    if not scope_allows(scope, action):
        return False

    if scope.role == "admin":
        if target.role not in _ADMIN_MANAGED_ROLES:
            return False
        if requested_role is not None and requested_role not in _ADMIN_MANAGED_ROLES:
            return False

    return not (
        requested_role is not None
        and scope.user_id == target.user_id
        and _ROLE_RANK[requested_role] > _ROLE_RANK[scope.role]
    )


def is_self_promotion(
    scope: WorkspaceScope,
    target: WorkspaceMembershipRecord,
    requested_role: WorkspaceRoleName,
) -> bool:
    """Return whether an actor is trying to raise their own authority."""

    return scope.user_id == target.user_id and _ROLE_RANK[requested_role] > _ROLE_RANK[scope.role]
