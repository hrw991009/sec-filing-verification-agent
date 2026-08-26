"""Authenticated HTTP delivery for public SEC filer discovery."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from industry_platform.core.http import problem_openapi_response, set_no_store_headers
from industry_platform.modules.disclosures.resources import (
    DisclosureResources,
    get_disclosure_resources,
)
from industry_platform.modules.disclosures.schemas import SecFilerResolutionResponse
from industry_platform.modules.identity.domain import AuthenticatedPrincipal
from industry_platform.modules.identity.http_auth import require_authenticated_principal
from industry_platform.modules.workspaces.domain import WorkspaceAccessDeniedError, WorkspaceScope

router = APIRouter(tags=["disclosures"])
type OpenApiResponses = dict[int | str, dict[str, Any]]

_RESPONSES: OpenApiResponses = {
    status.HTTP_401_UNAUTHORIZED: problem_openapi_response("Invalid authenticated session"),
    status.HTTP_403_FORBIDDEN: problem_openapi_response("Workspace access denied"),
    status.HTTP_422_UNPROCESSABLE_CONTENT: problem_openapi_response("Request validation failed"),
    status.HTTP_502_BAD_GATEWAY: problem_openapi_response("SEC source response rejected"),
    status.HTTP_503_SERVICE_UNAVAILABLE: problem_openapi_response(
        "SEC filer discovery temporarily unavailable"
    ),
}


@router.get(
    "/workspaces/{workspace_id}/disclosures/filers/resolve",
    response_model=SecFilerResolutionResponse,
    responses=_RESPONSES,
)
async def resolve_filer(
    workspace_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[DisclosureResources, Depends(get_disclosure_resources)],
    query: Annotated[str, Query(min_length=1, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=10)] = 5,
) -> SecFilerResolutionResponse:
    result = await resources.resolution_service.resolve(
        _workspace_scope(principal, workspace_id),
        query=query,
        limit=limit,
    )
    set_no_store_headers(response)
    return SecFilerResolutionResponse.from_domain(result)


def _workspace_scope(
    principal: AuthenticatedPrincipal,
    workspace_id: UUID,
) -> WorkspaceScope:
    workspace = next(
        (candidate for candidate in principal.workspaces if candidate.workspace_id == workspace_id),
        None,
    )
    if workspace is None:
        raise WorkspaceAccessDeniedError
    return WorkspaceScope(
        workspace_id=workspace_id,
        user_id=principal.user_id,
        role=workspace.role,
    )
