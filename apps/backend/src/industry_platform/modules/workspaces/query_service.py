"""Protected workspace query orchestration."""

from dataclasses import dataclass
from uuid import UUID

from industry_platform.modules.identity.domain import AuthenticatedPrincipal
from industry_platform.modules.workspaces.domain import (
    WorkspaceAccessDeniedError,
    WorkspaceMemberSummary,
    WorkspaceScope,
    WorkspaceSummary,
)
from industry_platform.modules.workspaces.ports import WorkspaceQueryRepository


@dataclass(frozen=True, slots=True)
class WorkspaceQueryService:
    """Derive scopes from authenticated state and delegate consistent reads."""

    repository: WorkspaceQueryRepository

    def list_workspaces(self, principal: AuthenticatedPrincipal) -> tuple[WorkspaceSummary, ...]:
        """Return the active memberships already revalidated for this request."""

        return tuple(
            WorkspaceSummary(
                workspace_id=workspace.workspace_id,
                name=workspace.name,
                role=workspace.role,
            )
            for workspace in principal.workspaces
        )

    async def list_members(
        self,
        principal: AuthenticatedPrincipal,
        workspace_id: UUID,
    ) -> tuple[WorkspaceMemberSummary, ...]:
        """Reject unknown tenant scope before asking persistence for any members."""

        workspace = next(
            (
                candidate
                for candidate in principal.workspaces
                if candidate.workspace_id == workspace_id
            ),
            None,
        )
        if workspace is None:
            raise WorkspaceAccessDeniedError

        return await self.repository.list_members(
            WorkspaceScope(
                workspace_id=workspace_id,
                user_id=principal.user_id,
                role=workspace.role,
            )
        )
