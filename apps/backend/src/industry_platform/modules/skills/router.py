"""Authenticated discovery of built-in executable Skills."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict

from industry_platform.core.http import set_no_store_headers
from industry_platform.modules.identity.domain import AuthenticatedPrincipal
from industry_platform.modules.identity.http_auth import require_authenticated_principal
from industry_platform.modules.skills.registry import SKILL_REGISTRY

router = APIRouter(prefix="/skills", tags=["skills"])


class SkillRoleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    nodes: list[str]
    responsibility: str


class SkillResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    version: str
    description: str
    graph_version: str
    roles: list[SkillRoleResponse]


@router.get("", response_model=list[SkillResponse])
async def list_skills(
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
) -> list[SkillResponse]:
    set_no_store_headers(response)
    return [
        SkillResponse(
            name=definition.name,
            version=definition.version,
            description=definition.description,
            graph_version=definition.graph_version,
            roles=[
                SkillRoleResponse(
                    name=role.name,
                    nodes=[node.value for node in role.nodes],
                    responsibility=role.responsibility,
                )
                for role in definition.roles
            ],
        )
        for definition in SKILL_REGISTRY.definitions()
    ]
