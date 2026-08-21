"""Authenticated HTTP delivery for user-controlled Memory writes."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from industry_platform.core.http import get_trace_id, problem_openapi_response, set_no_store_headers
from industry_platform.modules.identity.domain import AuthenticatedPrincipal, TraceId
from industry_platform.modules.identity.http_auth import require_authenticated_principal
from industry_platform.modules.memory.domain import (
    CreateMemoryCandidate,
    Memory,
    MemoryCandidate,
    MemoryDetail,
    MemoryRequestRejectedError,
    MemoryRevision,
    RejectMemoryCandidate,
    ResolveMemoryCandidate,
)
from industry_platform.modules.memory.ports import MemoryUseCase
from industry_platform.modules.memory.resources import MemoryResources, get_memory_resources
from industry_platform.modules.memory.schemas import (
    CreateMemoryCandidateRequest,
    MemoryCandidateCollectionResponse,
    MemoryCandidateCreatedResponse,
    MemoryCandidateResponse,
    MemoryCollectionResponse,
    MemoryDetailResponse,
    MemoryResolutionResponse,
    MemoryResponse,
    MemoryRevisionResponse,
    ResolveMemoryCandidateRequest,
)
from industry_platform.modules.workspaces.domain import WorkspaceAccessDeniedError, WorkspaceScope

router = APIRouter(prefix="/workspaces/{workspace_id}/memories", tags=["memories"])
type OpenApiResponses = dict[int | str, dict[str, Any]]

_RESPONSES: OpenApiResponses = {
    status.HTTP_401_UNAUTHORIZED: problem_openapi_response("Invalid authenticated session"),
    status.HTTP_403_FORBIDDEN: problem_openapi_response("Workspace access denied"),
    status.HTTP_404_NOT_FOUND: problem_openapi_response("Memory resource not found"),
    status.HTTP_409_CONFLICT: problem_openapi_response("Memory revision conflict"),
    status.HTTP_422_UNPROCESSABLE_CONTENT: problem_openapi_response("Memory request rejected"),
    status.HTTP_500_INTERNAL_SERVER_ERROR: problem_openapi_response("Internal server error"),
    status.HTTP_503_SERVICE_UNAVAILABLE: problem_openapi_response(
        "Memory service temporarily unavailable"
    ),
}


def get_memory_service(
    resources: Annotated[MemoryResources, Depends(get_memory_resources)],
) -> MemoryUseCase:
    return resources.service


@router.post(
    "/candidates",
    response_model=MemoryCandidateCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_RESPONSES,
)
async def create_memory_candidate(
    workspace_id: UUID,
    payload: CreateMemoryCandidateRequest,
    request: Request,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[MemoryUseCase, Depends(get_memory_service)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
) -> MemoryCandidateCreatedResponse:
    result = await service.create_candidate(
        _workspace_scope(principal, workspace_id),
        CreateMemoryCandidate(
            conversation_id=payload.conversation_id,
            message_ids=tuple(payload.message_ids),
            scope=payload.scope,
            idempotency_key=idempotency_key,
            trace_id=TraceId(get_trace_id(request)),
        ),
    )
    set_no_store_headers(response)
    _set_revision_header(response, result.candidate.revision)
    return MemoryCandidateCreatedResponse(
        **_candidate_response(result.candidate).model_dump(),
        created=result.created,
    )


@router.get(
    "/candidates",
    response_model=MemoryCandidateCollectionResponse,
    responses=_RESPONSES,
)
async def list_memory_candidates(
    workspace_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[MemoryUseCase, Depends(get_memory_service)],
    conversation_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> MemoryCandidateCollectionResponse:
    candidates = await service.list_candidates(
        _workspace_scope(principal, workspace_id),
        conversation_id=conversation_id,
        limit=limit,
    )
    set_no_store_headers(response)
    return MemoryCandidateCollectionResponse(
        candidates=[_candidate_response(candidate) for candidate in candidates]
    )


@router.get(
    "/candidates/{candidate_id}",
    response_model=MemoryCandidateResponse,
    responses=_RESPONSES,
)
async def get_memory_candidate(
    workspace_id: UUID,
    candidate_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[MemoryUseCase, Depends(get_memory_service)],
) -> MemoryCandidateResponse:
    candidate = await service.get_candidate(_workspace_scope(principal, workspace_id), candidate_id)
    set_no_store_headers(response)
    _set_revision_header(response, candidate.revision)
    return _candidate_response(candidate)


@router.post(
    "/candidates/{candidate_id}/confirm",
    response_model=MemoryResolutionResponse,
    responses=_RESPONSES,
)
async def confirm_memory_candidate(
    workspace_id: UUID,
    candidate_id: UUID,
    payload: ResolveMemoryCandidateRequest,
    request: Request,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[MemoryUseCase, Depends(get_memory_service)],
    if_match: Annotated[str, Header(alias="If-Match", min_length=1, max_length=32)],
) -> MemoryResolutionResponse:
    result = await service.resolve_candidate(
        _workspace_scope(principal, workspace_id),
        ResolveMemoryCandidate(
            candidate_id=candidate_id,
            expected_candidate_revision=_parse_if_match(if_match),
            action=payload.action,
            content=payload.content,
            scope=payload.scope,
            kind=payload.kind,
            expires_at=payload.expires_at,
            target_memory_id=payload.target_memory_id,
            expected_target_revision=payload.target_revision,
            trace_id=TraceId(get_trace_id(request)),
        ),
    )
    set_no_store_headers(response)
    _set_revision_header(response, result.detail.memory.current_version)
    return MemoryResolutionResponse(
        memory=_detail_response(result.detail),
        action=result.action,
        created=result.created,
    )


@router.post(
    "/candidates/{candidate_id}/reject",
    response_model=MemoryCandidateResponse,
    responses=_RESPONSES,
)
async def reject_memory_candidate(
    workspace_id: UUID,
    candidate_id: UUID,
    request: Request,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[MemoryUseCase, Depends(get_memory_service)],
    if_match: Annotated[str, Header(alias="If-Match", min_length=1, max_length=32)],
) -> MemoryCandidateResponse:
    candidate = await service.reject_candidate(
        _workspace_scope(principal, workspace_id),
        RejectMemoryCandidate(
            candidate_id=candidate_id,
            expected_candidate_revision=_parse_if_match(if_match),
            trace_id=TraceId(get_trace_id(request)),
        ),
    )
    set_no_store_headers(response)
    _set_revision_header(response, candidate.revision)
    return _candidate_response(candidate)


@router.get("", response_model=MemoryCollectionResponse, responses=_RESPONSES)
async def list_memories(
    workspace_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[MemoryUseCase, Depends(get_memory_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> MemoryCollectionResponse:
    memories = await service.list_memories(_workspace_scope(principal, workspace_id), limit=limit)
    set_no_store_headers(response)
    return MemoryCollectionResponse(memories=[_memory_response(memory) for memory in memories])


@router.get("/{memory_id}", response_model=MemoryDetailResponse, responses=_RESPONSES)
async def get_memory(
    workspace_id: UUID,
    memory_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[MemoryUseCase, Depends(get_memory_service)],
) -> MemoryDetailResponse:
    detail = await service.get_memory(_workspace_scope(principal, workspace_id), memory_id)
    set_no_store_headers(response)
    _set_revision_header(response, detail.memory.current_version)
    return _detail_response(detail)


def _parse_if_match(value: str) -> int:
    normalized = value.strip()
    if normalized.startswith('"') and normalized.endswith('"'):
        normalized = normalized[1:-1]
    if not normalized.isascii() or not normalized.isdecimal():
        raise MemoryRequestRejectedError
    revision = int(normalized)
    if revision < 1:
        raise MemoryRequestRejectedError
    return revision


def _set_revision_header(response: Response, revision: int) -> None:
    response.headers["ETag"] = f'"{revision}"'


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
    return WorkspaceScope(workspace_id=workspace_id, user_id=principal.user_id, role=workspace.role)


def _candidate_response(candidate: MemoryCandidate) -> MemoryCandidateResponse:
    return MemoryCandidateResponse(
        id=candidate.candidate_id,
        conversation_id=candidate.conversation_id,
        source_message_ids=list(candidate.source_message_ids),
        suggested_content=candidate.suggested_content,
        suggested_scope=candidate.suggested_scope,
        suggested_expires_at=candidate.suggested_expires_at,
        confidence=candidate.confidence,
        write_reason=candidate.write_reason,
        policy_decision=candidate.policy_decision,
        policy_reason=candidate.policy_reason,
        status=candidate.status,
        revision=candidate.revision,
        resolved_memory_id=candidate.resolved_memory_id,
        created_at=candidate.created_at,
        updated_at=candidate.updated_at,
    )


def _memory_response(memory: Memory) -> MemoryResponse:
    return MemoryResponse(
        id=memory.memory_id,
        owner_user_id=memory.owner_user_id,
        source_conversation_id=memory.source_conversation_id,
        scope=memory.scope,
        kind=memory.kind,
        confidence=memory.confidence,
        status=memory.status,
        current_revision_id=memory.current_revision_id,
        current_version=memory.current_version,
        expires_at=memory.expires_at,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
    )


def _revision_response(revision: MemoryRevision) -> MemoryRevisionResponse:
    return MemoryRevisionResponse(
        id=revision.revision_id,
        version=revision.version,
        content=revision.content,
        scope=revision.scope,
        kind=revision.kind,
        write_action=revision.write_action,
        write_reason=revision.write_reason,
        policy_decision=revision.policy_decision,
        editor_user_id=revision.editor_user_id,
        source_message_ids=list(revision.source_message_ids),
        validity=revision.validity,
        created_at=revision.created_at,
    )


def _detail_response(detail: MemoryDetail) -> MemoryDetailResponse:
    return MemoryDetailResponse(
        memory=_memory_response(detail.memory),
        current_revision=_revision_response(detail.current_revision),
        revisions=[_revision_response(revision) for revision in detail.revisions],
    )
