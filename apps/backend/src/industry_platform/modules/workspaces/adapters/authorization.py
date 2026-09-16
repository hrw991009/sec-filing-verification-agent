"""Transaction-scoped recheck for owner-only recovery mutations."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from industry_platform.modules.identity.models import (
    User,
    UserStatus,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
    WorkspaceStatus,
)
from industry_platform.modules.workspaces.domain import WorkspaceAccessDeniedError, WorkspaceScope


async def require_current_owner(session: AsyncSession, scope: WorkspaceScope) -> None:
    if scope.role != "owner":
        raise WorkspaceAccessDeniedError
    membership = await session.scalar(
        select(WorkspaceMembership)
        .join(User, User.id == WorkspaceMembership.user_id)
        .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
        .where(
            WorkspaceMembership.workspace_id == scope.workspace_id,
            WorkspaceMembership.user_id == scope.user_id,
            WorkspaceMembership.role == WorkspaceRole.OWNER,
            User.status == UserStatus.ACTIVE,
            Workspace.status == WorkspaceStatus.ACTIVE,
        )
        .with_for_update(read=True, of=(WorkspaceMembership, User, Workspace))
    )
    if membership is None:
        raise WorkspaceAccessDeniedError
