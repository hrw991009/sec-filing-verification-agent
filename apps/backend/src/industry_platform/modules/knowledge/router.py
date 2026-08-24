"""Authenticated HTTP delivery for Workspace-owned Knowledge."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from industry_platform.core.http import get_trace_id, problem_openapi_response, set_no_store_headers
from industry_platform.modules.identity.domain import AuthenticatedPrincipal, TraceId
from industry_platform.modules.identity.http_auth import require_authenticated_principal
from industry_platform.modules.knowledge.domain import (
    CompleteKnowledgeUpload,
    CreateKnowledgeBase,
    CreateKnowledgeUpload,
    DeleteKnowledgeBase,
    KnowledgeConflictError,
    UpdateKnowledgeBase,
)
from industry_platform.modules.knowledge.resources import (
    KnowledgeResources,
    get_knowledge_resources,
)
from industry_platform.modules.knowledge.schemas import (
    CompleteKnowledgeUploadRequest,
    CreateKnowledgeBaseRequest,
    CreateKnowledgeUploadRequest,
    DocumentCollectionResponse,
    DocumentDetailResponse,
    IdempotencyKey,
    KnowledgeAcceptanceResponse,
    KnowledgeBaseCollectionResponse,
    KnowledgeBaseResponse,
    KnowledgeIngestionEventCollectionResponse,
    KnowledgeUploadResponse,
    UpdateKnowledgeBaseRequest,
    acceptance_response,
    document_detail_response,
    document_response,
    ingestion_event_response,
    knowledge_base_response,
    upload_response,
)
from industry_platform.modules.knowledge.service import KnowledgeApplicationService
from industry_platform.modules.workspaces.domain import WorkspaceAccessDeniedError, WorkspaceScope

router = APIRouter(prefix="/workspaces/{workspace_id}/knowledge-bases", tags=["knowledge"])
type OpenApiResponses = dict[int | str, dict[str, Any]]

_RESPONSES: OpenApiResponses = {
    status.HTTP_401_UNAUTHORIZED: problem_openapi_response("Invalid authenticated session"),
    status.HTTP_403_FORBIDDEN: problem_openapi_response("Workspace access denied"),
    status.HTTP_404_NOT_FOUND: problem_openapi_response("Knowledge resource not found"),
    status.HTTP_409_CONFLICT: problem_openapi_response("Knowledge state conflict"),
    status.HTTP_422_UNPROCESSABLE_CONTENT: problem_openapi_response("Knowledge upload rejected"),
    status.HTTP_503_SERVICE_UNAVAILABLE: problem_openapi_response(
        "Knowledge service temporarily unavailable"
    ),
}


def get_knowledge_service(
    resources: Annotated[KnowledgeResources, Depends(get_knowledge_resources)],
) -> KnowledgeApplicationService:
    return resources.service


@router.post(
    "",
    response_model=KnowledgeBaseResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_RESPONSES,
)
async def create_knowledge_base(
    workspace_id: UUID,
    payload: CreateKnowledgeBaseRequest,
    request: Request,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[KnowledgeApplicationService, Depends(get_knowledge_service)],
) -> KnowledgeBaseResponse:
    value = await service.create_knowledge_base(
        _scope(principal, workspace_id),
        CreateKnowledgeBase(
            name=payload.name,
            description=payload.description,
            trace_id=TraceId(get_trace_id(request)),
        ),
    )
    set_no_store_headers(response)
    _etag(response, value.revision)
    return knowledge_base_response(value)


@router.get("", response_model=KnowledgeBaseCollectionResponse, responses=_RESPONSES)
async def list_knowledge_bases(
    workspace_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[KnowledgeApplicationService, Depends(get_knowledge_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> KnowledgeBaseCollectionResponse:
    values = await service.list_knowledge_bases(_scope(principal, workspace_id), limit=limit)
    set_no_store_headers(response)
    return KnowledgeBaseCollectionResponse(
        knowledge_bases=[knowledge_base_response(value) for value in values]
    )


@router.get("/{knowledge_base_id}", response_model=KnowledgeBaseResponse, responses=_RESPONSES)
async def get_knowledge_base(
    workspace_id: UUID,
    knowledge_base_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[KnowledgeApplicationService, Depends(get_knowledge_service)],
) -> KnowledgeBaseResponse:
    value = await service.get_knowledge_base(_scope(principal, workspace_id), knowledge_base_id)
    set_no_store_headers(response)
    _etag(response, value.revision)
    return knowledge_base_response(value)


@router.patch("/{knowledge_base_id}", response_model=KnowledgeBaseResponse, responses=_RESPONSES)
async def update_knowledge_base(
    workspace_id: UUID,
    knowledge_base_id: UUID,
    payload: UpdateKnowledgeBaseRequest,
    request: Request,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[KnowledgeApplicationService, Depends(get_knowledge_service)],
    if_match: Annotated[str, Header(alias="If-Match", min_length=1, max_length=32)],
) -> KnowledgeBaseResponse:
    value = await service.update_knowledge_base(
        _scope(principal, workspace_id),
        UpdateKnowledgeBase(
            knowledge_base_id=knowledge_base_id,
            expected_revision=_parse_revision(if_match),
            name=payload.name,
            description=payload.description,
            trace_id=TraceId(get_trace_id(request)),
        ),
    )
    set_no_store_headers(response)
    _etag(response, value.revision)
    return knowledge_base_response(value)


@router.delete("/{knowledge_base_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_RESPONSES)
async def delete_knowledge_base(
    workspace_id: UUID,
    knowledge_base_id: UUID,
    request: Request,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[KnowledgeApplicationService, Depends(get_knowledge_service)],
    if_match: Annotated[str, Header(alias="If-Match", min_length=1, max_length=32)],
) -> None:
    await service.delete_knowledge_base(
        _scope(principal, workspace_id),
        DeleteKnowledgeBase(
            knowledge_base_id=knowledge_base_id,
            expected_revision=_parse_revision(if_match),
            trace_id=TraceId(get_trace_id(request)),
        ),
    )
    set_no_store_headers(response)


@router.post(
    "/{knowledge_base_id}/uploads/presign",
    response_model=KnowledgeUploadResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_RESPONSES,
)
async def create_knowledge_upload(
    workspace_id: UUID,
    knowledge_base_id: UUID,
    payload: CreateKnowledgeUploadRequest,
    request: Request,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[KnowledgeApplicationService, Depends(get_knowledge_service)],
) -> KnowledgeUploadResponse:
    ticket = await service.create_upload(
        _scope(principal, workspace_id),
        CreateKnowledgeUpload(
            knowledge_base_id=knowledge_base_id,
            original_name=payload.original_name,
            declared_media_type=payload.declared_media_type,
            expected_size=payload.expected_size,
            expected_sha256=payload.expected_sha256,
            trace_id=TraceId(get_trace_id(request)),
        ),
    )
    set_no_store_headers(response)
    return upload_response(ticket)


@router.post(
    "/{knowledge_base_id}/uploads/{file_id}/complete",
    response_model=KnowledgeAcceptanceResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_RESPONSES,
)
async def complete_knowledge_upload(
    workspace_id: UUID,
    knowledge_base_id: UUID,
    file_id: UUID,
    payload: CompleteKnowledgeUploadRequest,
    request: Request,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[KnowledgeApplicationService, Depends(get_knowledge_service)],
    idempotency_key: Annotated[IdempotencyKey, Header(alias="Idempotency-Key")],
) -> KnowledgeAcceptanceResponse:
    receipt = await service.complete_upload(
        _scope(principal, workspace_id),
        CompleteKnowledgeUpload(
            knowledge_base_id=knowledge_base_id,
            file_id=file_id,
            title=payload.title,
            idempotency_key=idempotency_key,
            trace_id=TraceId(get_trace_id(request)),
        ),
    )
    set_no_store_headers(response)
    events_url = (
        f"/api/v1/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/documents/"
        f"{receipt.document.id}/versions/{receipt.version.id}/events"
    )
    return acceptance_response(receipt, events_url=events_url)


@router.get(
    "/{knowledge_base_id}/documents",
    response_model=DocumentCollectionResponse,
    responses=_RESPONSES,
)
async def list_documents(
    workspace_id: UUID,
    knowledge_base_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[KnowledgeApplicationService, Depends(get_knowledge_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> DocumentCollectionResponse:
    values = await service.list_documents(
        _scope(principal, workspace_id), knowledge_base_id=knowledge_base_id, limit=limit
    )
    set_no_store_headers(response)
    return DocumentCollectionResponse(documents=[document_response(value) for value in values])


@router.get(
    "/{knowledge_base_id}/documents/{document_id}",
    response_model=DocumentDetailResponse,
    responses=_RESPONSES,
)
async def get_document(
    workspace_id: UUID,
    knowledge_base_id: UUID,
    document_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[KnowledgeApplicationService, Depends(get_knowledge_service)],
) -> DocumentDetailResponse:
    value = await service.get_document(
        _scope(principal, workspace_id),
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
    )
    set_no_store_headers(response)
    return document_detail_response(value)


@router.get(
    "/{knowledge_base_id}/documents/{document_id}/versions/{version_id}/events",
    response_model=KnowledgeIngestionEventCollectionResponse,
    responses=_RESPONSES,
)
async def list_ingestion_events(
    workspace_id: UUID,
    knowledge_base_id: UUID,
    document_id: UUID,
    version_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[KnowledgeApplicationService, Depends(get_knowledge_service)],
) -> KnowledgeIngestionEventCollectionResponse:
    values = await service.list_ingestion_events(
        _scope(principal, workspace_id),
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
        version_id=version_id,
    )
    set_no_store_headers(response)
    return KnowledgeIngestionEventCollectionResponse(
        events=[ingestion_event_response(value) for value in values]
    )


def _scope(principal: AuthenticatedPrincipal, workspace_id: UUID) -> WorkspaceScope:
    workspace = next(
        (candidate for candidate in principal.workspaces if candidate.workspace_id == workspace_id),
        None,
    )
    if workspace is None:
        raise WorkspaceAccessDeniedError
    return WorkspaceScope(workspace_id=workspace_id, user_id=principal.user_id, role=workspace.role)


def _parse_revision(value: str) -> int:
    normalized = value.strip('"')
    if not normalized.isdigit() or int(normalized) < 1:
        raise KnowledgeConflictError
    return int(normalized)


def _etag(response: Response, revision: int) -> None:
    response.headers["ETag"] = f'"{revision}"'
