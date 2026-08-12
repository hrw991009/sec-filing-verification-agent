"""Validated HTTP schemas for workspace APIs."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr

from industry_platform.modules.identity.domain import AccountStatus, WorkspaceRoleName


class StrictWorkspaceModel(BaseModel):
    """Reject unknown request fields at workspace HTTP boundaries."""

    model_config = ConfigDict(extra="forbid")


class WorkspaceResponse(StrictWorkspaceModel):
    id: UUID
    name: str
    role: WorkspaceRoleName


class WorkspaceCollectionResponse(StrictWorkspaceModel):
    workspaces: list[WorkspaceResponse]


class WorkspaceMemberResponse(StrictWorkspaceModel):
    membership_id: UUID
    user_id: UUID
    email: EmailStr
    role: WorkspaceRoleName
    account_status: AccountStatus


class WorkspaceMembershipResponse(StrictWorkspaceModel):
    membership_id: UUID
    user_id: UUID
    role: WorkspaceRoleName


class WorkspaceMemberCollectionResponse(StrictWorkspaceModel):
    workspace_id: UUID
    members: list[WorkspaceMemberResponse]


class AddWorkspaceMemberRequest(StrictWorkspaceModel):
    user_id: UUID
    role: WorkspaceRoleName


class ChangeWorkspaceMemberRoleRequest(StrictWorkspaceModel):
    role: WorkspaceRoleName
