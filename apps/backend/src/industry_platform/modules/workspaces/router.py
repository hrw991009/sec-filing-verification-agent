"""Protected HTTP delivery adapter for workspace use cases."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status

from industry_platform.core.http import (
    get_trace_id,
    problem_openapi_response,
    set_no_store_headers,
)
from industry_platform.modules.identity.domain import (
    AuthenticatedPrincipal,
    TraceId,
)
from industry_platform.modules.identity.http_auth import require_authenticated_principal
from industry_platform.modules.workspaces.domain import (
    AddWorkspaceMemberCommand,
    ChangeWorkspaceMemberRoleCommand,
    RemoveWorkspaceMemberCommand,
    WorkspaceMembershipRecord,
)
from industry_platform.modules.workspaces.ports import (
    WorkspaceMembershipUseCase,
    WorkspaceQueryUseCase,
)
from industry_platform.modules.workspaces.resources import (
    WorkspaceResources,
    get_workspace_resources,
)
from industry_platform.modules.workspaces.schemas import (
    AddWorkspaceMemberRequest,
    ChangeWorkspaceMemberRoleRequest,
    WorkspaceCollectionResponse,
    WorkspaceMemberCollectionResponse,
    WorkspaceMembershipResponse,
)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])
type OpenApiResponses = dict[int | str, dict[str, Any]]


def get_workspace_query_service(
    resources: Annotated[WorkspaceResources, Depends(get_workspace_resources)],
) -> WorkspaceQueryUseCase:
    return resources.query_service


def get_workspace_membership_service(
    resources: Annotated[WorkspaceResources, Depends(get_workspace_resources)],
) -> WorkspaceMembershipUseCase:
    return resources.membership_service


def _membership_response(
    record: WorkspaceMembershipRecord,
) -> WorkspaceMembershipResponse:
    return WorkspaceMembershipResponse.model_validate(
        {
            "membership_id": record.membership_id,
            "user_id": record.user_id,
            "role": record.role,
        }
    )


_AUTHENTICATED_RESPONSES: OpenApiResponses = {
    status.HTTP_401_UNAUTHORIZED: problem_openapi_response("Invalid authenticated session"),
    status.HTTP_500_INTERNAL_SERVER_ERROR: problem_openapi_response("Internal server error"),
    status.HTTP_503_SERVICE_UNAVAILABLE: problem_openapi_response(
        "Workspace service temporarily unavailable"
    ),
}
_AUTHORIZED_RESPONSES: OpenApiResponses = {
    **_AUTHENTICATED_RESPONSES,
    status.HTTP_403_FORBIDDEN: problem_openapi_response("Workspace access denied"),
}
_PATH_RESPONSES: OpenApiResponses = {
    **_AUTHORIZED_RESPONSES,
    status.HTTP_422_UNPROCESSABLE_CONTENT: problem_openapi_response("Request validation failed"),
}
_MUTATION_RESPONSES: OpenApiResponses = {
    **_PATH_RESPONSES,
    status.HTTP_404_NOT_FOUND: problem_openapi_response("Workspace member not found"),
    status.HTTP_409_CONFLICT: problem_openapi_response("Workspace membership conflict"),
}


@router.get(
    "",
    response_model=WorkspaceCollectionResponse,
    responses=_AUTHENTICATED_RESPONSES,
)
async def list_workspaces(
    response: Response,
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_authenticated_principal),
    ],
    query_service: Annotated[
        WorkspaceQueryUseCase,
        Depends(get_workspace_query_service),
    ],
) -> WorkspaceCollectionResponse:
    set_no_store_headers(response)
    workspaces = query_service.list_workspaces(principal)
    return WorkspaceCollectionResponse.model_validate(
        {
            "workspaces": [
                {
                    "id": workspace.workspace_id,
                    "name": workspace.name,
                    "role": workspace.role,
                }
                for workspace in workspaces
            ]
        }
    )


@router.get(
    "/{workspace_id}/members",
    response_model=WorkspaceMemberCollectionResponse,
    responses=_PATH_RESPONSES,
)
async def list_workspace_members(
    workspace_id: UUID,
    response: Response,
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_authenticated_principal),
    ],
    query_service: Annotated[
        WorkspaceQueryUseCase,
        Depends(get_workspace_query_service),
    ],
) -> WorkspaceMemberCollectionResponse:
    set_no_store_headers(response)
    members = await query_service.list_members(principal, workspace_id)
    return WorkspaceMemberCollectionResponse.model_validate(
        {
            "workspace_id": workspace_id,
            "members": [
                {
                    "membership_id": member.membership_id,
                    "user_id": member.user_id,
                    "email": member.email,
                    "role": member.role,
                    "account_status": member.account_status,
                }
                for member in members
            ],
        }
    )


@router.post(
    "/{workspace_id}/members",
    response_model=WorkspaceMembershipResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_MUTATION_RESPONSES,
)
async def add_workspace_member(
    workspace_id: UUID,
    payload: AddWorkspaceMemberRequest,
    request: Request,
    response: Response,
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_authenticated_principal),
    ],
    service: Annotated[
        WorkspaceMembershipUseCase,
        Depends(get_workspace_membership_service),
    ],
) -> WorkspaceMembershipResponse:
    record = await service.add_member(
        AddWorkspaceMemberCommand(
            workspace_id=workspace_id,
            actor_user_id=principal.user_id,
            target_user_id=payload.user_id,
            role=payload.role,
            trace_id=TraceId(get_trace_id(request)),
        )
    )
    set_no_store_headers(response)
    return _membership_response(record)


@router.patch(
    "/{workspace_id}/members/{user_id}",
    response_model=WorkspaceMembershipResponse,
    responses=_MUTATION_RESPONSES,
)
async def change_workspace_member_role(
    workspace_id: UUID,
    user_id: UUID,
    payload: ChangeWorkspaceMemberRoleRequest,
    request: Request,
    response: Response,
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_authenticated_principal),
    ],
    service: Annotated[
        WorkspaceMembershipUseCase,
        Depends(get_workspace_membership_service),
    ],
) -> WorkspaceMembershipResponse:
    record = await service.change_member_role(
        ChangeWorkspaceMemberRoleCommand(
            workspace_id=workspace_id,
            actor_user_id=principal.user_id,
            target_user_id=user_id,
            role=payload.role,
            trace_id=TraceId(get_trace_id(request)),
        )
    )
    set_no_store_headers(response)
    return _membership_response(record)


@router.delete(
    "/{workspace_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_MUTATION_RESPONSES,
)
async def remove_workspace_member(
    workspace_id: UUID,
    user_id: UUID,
    request: Request,
    response: Response,
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_authenticated_principal),
    ],
    service: Annotated[
        WorkspaceMembershipUseCase,
        Depends(get_workspace_membership_service),
    ],
) -> None:
    await service.remove_member(
        RemoveWorkspaceMemberCommand(
            workspace_id=workspace_id,
            actor_user_id=principal.user_id,
            target_user_id=user_id,
            trace_id=TraceId(get_trace_id(request)),
        )
    )
    set_no_store_headers(response)
