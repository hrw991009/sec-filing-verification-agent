"""Application ports for starting and reading Research L3 runs."""

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol
from uuid import UUID

from industry_platform.modules.research.domain import (
    ResearchDraft,
    ResearchNode,
    ResearchPlan,
    ResearchRunView,
)
from industry_platform.modules.workspaces.domain import WorkspaceScope


class ResearchQueryRepository(Protocol):
    async def get(self, scope: WorkspaceScope, research_run_id: UUID) -> ResearchRunView: ...

    async def list(self, scope: WorkspaceScope, *, limit: int) -> tuple[ResearchRunView, ...]: ...


class ResearchQueryUseCase(Protocol):
    async def get(self, scope: WorkspaceScope, research_run_id: UUID) -> ResearchRunView: ...

    async def list(self, scope: WorkspaceScope, *, limit: int) -> tuple[ResearchRunView, ...]: ...


class ResearchWorkflowStore(Protocol):
    async def save_state(
        self,
        scope: WorkspaceScope,
        research_run_id: UUID,
        *,
        node: ResearchNode,
        state: Mapping[str, object],
        updated_at: datetime,
    ) -> None: ...

    async def save_plan(self, scope: WorkspaceScope, plan: ResearchPlan) -> None: ...

    async def save_draft(self, scope: WorkspaceScope, draft: ResearchDraft) -> None: ...
