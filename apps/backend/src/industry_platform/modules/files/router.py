"""Authenticated HTTP delivery for workspace-owned private attachments."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from industry_platform.core.http import problem_openapi_response, set_no_store_headers
from industry_platform.modules.files.resources import FileResources, get_file_resources
from industry_platform.modules.files.schemas import (
    CreateFileUploadRequest,
    FileDownloadResponse,
    FileResponse,
    FileUploadResponse,
    file_response,
)
from industry_platform.modules.files.service import CreateFileUpload, FileApplicationService
from industry_platform.modules.identity.domain import AuthenticatedPrincipal
from industry_platform.modules.identity.http_auth import require_authenticated_principal
from industry_platform.modules.workspaces.domain import WorkspaceAccessDeniedError, WorkspaceScope

router = APIRouter(prefix="/workspaces/{workspace_id}/files", tags=["files"])
type OpenApiResponses = dict[int | str, dict[str, Any]]

_RESPONSES: OpenApiResponses = {
    status.HTTP_401_UNAUTHORIZED: problem_openapi_response("Invalid authenticated session"),
    status.HTTP_403_FORBIDDEN: problem_openapi_response("Workspace access denied"),
    status.HTTP_404_NOT_FOUND: problem_openapi_response("File not found"),
    status.HTTP_409_CONFLICT: problem_openapi_response("File state conflict"),
    status.HTTP_422_UNPROCESSABLE_CONTENT: problem_openapi_response("File rejected"),
    status.HTTP_503_SERVICE_UNAVAILABLE: problem_openapi_response(
        "File service temporarily unavailable"
    ),
}


def get_file_service(
    resources: Annotated[FileResources, Depends(get_file_resources)],
) -> FileApplicationService:
    return resources.service


@router.post(
    "/presign",
    response_model=FileUploadResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_RESPONSES,
)
async def create_file_upload(
    workspace_id: UUID,
    payload: CreateFileUploadRequest,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[FileApplicationService, Depends(get_file_service)],
) -> FileUploadResponse:
    ticket = await service.create_upload(
        _workspace_scope(principal, workspace_id),
        CreateFileUpload(
            original_name=payload.original_name,
            declared_media_type=payload.declared_media_type,
            expected_size=payload.expected_size,
            expected_sha256=payload.expected_sha256,
        ),
    )
    set_no_store_headers(response)
    return FileUploadResponse(
        file=file_response(ticket.file),
        method=ticket.method,
        url=ticket.url,
        fields=dict(ticket.fields),
        expires_at=ticket.expires_at,
    )


@router.post(
    "/{file_id}/complete",
    response_model=FileResponse,
    responses=_RESPONSES,
)
async def complete_file_upload(
    workspace_id: UUID,
    file_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[FileApplicationService, Depends(get_file_service)],
) -> FileResponse:
    file = await service.complete_upload(_workspace_scope(principal, workspace_id), file_id)
    set_no_store_headers(response)
    return file_response(file)


@router.get("/{file_id}", response_model=FileResponse, responses=_RESPONSES)
async def get_file(
    workspace_id: UUID,
    file_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[FileApplicationService, Depends(get_file_service)],
) -> FileResponse:
    file = await service.get_file(_workspace_scope(principal, workspace_id), file_id)
    set_no_store_headers(response)
    return file_response(file)


@router.post(
    "/{file_id}/download-url",
    response_model=FileDownloadResponse,
    responses=_RESPONSES,
)
async def create_file_download(
    workspace_id: UUID,
    file_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[FileApplicationService, Depends(get_file_service)],
) -> FileDownloadResponse:
    ticket = await service.create_download(_workspace_scope(principal, workspace_id), file_id)
    set_no_store_headers(response)
    return FileDownloadResponse(url=ticket.url, expires_at=ticket.expires_at)


@router.delete("/{file_id}", response_model=FileResponse, responses=_RESPONSES)
async def delete_file(
    workspace_id: UUID,
    file_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[FileApplicationService, Depends(get_file_service)],
) -> FileResponse:
    file = await service.delete_file(_workspace_scope(principal, workspace_id), file_id)
    set_no_store_headers(response)
    return file_response(file)


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
