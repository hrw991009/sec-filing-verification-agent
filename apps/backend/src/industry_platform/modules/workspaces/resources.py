"""Composition helpers for workspace application resources."""

from dataclasses import dataclass

from fastapi import Request

from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.workspaces.adapters.sqlalchemy import (
    SqlAlchemyWorkspaceMembershipTransactionFactory,
    SqlAlchemyWorkspaceQueryRepository,
)
from industry_platform.modules.workspaces.ports import (
    WorkspaceMembershipUseCase,
    WorkspaceQueryUseCase,
)
from industry_platform.modules.workspaces.query_service import WorkspaceQueryService
from industry_platform.modules.workspaces.service import WorkspaceMembershipService


@dataclass(frozen=True, slots=True)
class WorkspaceResources:
    """Long-lived stateless workspace application services."""

    membership_service: WorkspaceMembershipUseCase
    query_service: WorkspaceQueryUseCase


def create_workspace_membership_service(
    session_factory: AsyncSessionFactory,
) -> WorkspaceMembershipService:
    """Compose the workspace service with its SQLAlchemy transaction adapter."""

    return WorkspaceMembershipService(
        transaction_factory=SqlAlchemyWorkspaceMembershipTransactionFactory(session_factory)
    )


def create_workspace_resources(
    session_factory: AsyncSessionFactory,
) -> WorkspaceResources:
    """Compose protected workspace command and query services."""

    return WorkspaceResources(
        membership_service=create_workspace_membership_service(session_factory),
        query_service=WorkspaceQueryService(
            repository=SqlAlchemyWorkspaceQueryRepository(session_factory)
        ),
    )


def get_workspace_resources(request: Request) -> WorkspaceResources:
    """Return workspace resources initialized by application lifespan."""

    resources = getattr(request.app.state, "workspace_resources", None)
    if not isinstance(resources, WorkspaceResources):
        raise RuntimeError("Application lifespan has not initialized workspace resources")
    return resources
