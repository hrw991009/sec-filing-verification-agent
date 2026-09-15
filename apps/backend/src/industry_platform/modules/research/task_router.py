"""Authenticated discovery of built-in executable Research tasks."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict

from industry_platform.core.http import set_no_store_headers
from industry_platform.modules.identity.domain import AuthenticatedPrincipal
from industry_platform.modules.identity.http_auth import require_authenticated_principal
from industry_platform.modules.research.tasks import RESEARCH_TASKS

router = APIRouter(prefix="/research/tasks", tags=["research-tasks"])


class ResearchRoleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    nodes: list[str]
    responsibility: str


class ResearchTaskResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    version: str
    description: str
    graph_version: str
    roles: list[ResearchRoleResponse]


@router.get("", response_model=list[ResearchTaskResponse])
async def list_research_tasks(
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
) -> list[ResearchTaskResponse]:
    set_no_store_headers(response)
    return [
        ResearchTaskResponse(
            name=definition.name,
            version=definition.version,
            description=definition.description,
            graph_version=definition.graph_version,
            roles=[
                ResearchRoleResponse(
                    name=role.name,
                    nodes=[node.value for node in role.nodes],
                    responsibility=role.responsibility,
                )
                for role in definition.roles
            ],
        )
        for definition in RESEARCH_TASKS.definitions()
    ]
