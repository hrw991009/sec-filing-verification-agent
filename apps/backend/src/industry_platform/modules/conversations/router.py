"""Authenticated HTTP delivery for Workspace-owned conversations."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from industry_platform.core.http import problem_openapi_response, set_no_store_headers
from industry_platform.modules.conversations.management import (
    ConversationDetail,
    ConversationManagementUseCase,
    ConversationMessage,
    ConversationSummary,
    RenameConversation,
)
from industry_platform.modules.conversations.resources import (
    ConversationResources,
    get_conversation_resources,
)
from industry_platform.modules.conversations.schemas import (
    MAX_CURSOR_LENGTH,
    ConversationCollectionResponse,
    ConversationDetailResponse,
    ConversationMessageCollectionResponse,
    ConversationMessageResponse,
    ConversationSummaryResponse,
    NonNilUuid,
    RenameConversationRequest,
    decode_conversation_cursor,
    decode_message_cursor,
    encode_conversation_cursor,
    encode_message_cursor,
)
from industry_platform.modules.identity.domain import AuthenticatedPrincipal
from industry_platform.modules.identity.http_auth import require_authenticated_principal
from industry_platform.modules.workspaces.domain import (
    WorkspaceAccessDeniedError,
    WorkspaceScope,
)

router = APIRouter(
    prefix="/workspaces/{workspace_id}/conversations",
    tags=["conversations"],
)
type OpenApiResponses = dict[int | str, dict[str, Any]]

_AUTHENTICATED_RESPONSES: OpenApiResponses = {
    status.HTTP_401_UNAUTHORIZED: problem_openapi_response("Invalid authenticated session"),
    status.HTTP_403_FORBIDDEN: problem_openapi_response("Workspace access denied"),
    status.HTTP_422_UNPROCESSABLE_CONTENT: problem_openapi_response("Request validation failed"),
    status.HTTP_500_INTERNAL_SERVER_ERROR: problem_openapi_response("Internal server error"),
    status.HTTP_503_SERVICE_UNAVAILABLE: problem_openapi_response(
        "Conversation service temporarily unavailable"
    ),
}
_PAGED_RESPONSES: OpenApiResponses = {
    **_AUTHENTICATED_RESPONSES,
    status.HTTP_400_BAD_REQUEST: problem_openapi_response("Invalid conversation cursor"),
}
_ITEM_RESPONSES: OpenApiResponses = {
    **_AUTHENTICATED_RESPONSES,
    status.HTTP_404_NOT_FOUND: problem_openapi_response("Conversation not found"),
}
_PAGED_ITEM_RESPONSES: OpenApiResponses = {
    **_ITEM_RESPONSES,
    status.HTTP_400_BAD_REQUEST: problem_openapi_response("Invalid conversation cursor"),
}


def get_conversation_management_service(
    resources: Annotated[ConversationResources, Depends(get_conversation_resources)],
) -> ConversationManagementUseCase:
    return resources.management_service


@router.get(
    "",
    response_model=ConversationCollectionResponse,
    responses=_PAGED_RESPONSES,
)
async def list_conversations(
    workspace_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[
        ConversationManagementUseCase,
        Depends(get_conversation_management_service),
    ],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=MAX_CURSOR_LENGTH)] = None,
) -> ConversationCollectionResponse:
    scope = _workspace_scope(principal, workspace_id)
    page = await service.list_conversations(
        scope,
        page_size=limit,
        cursor=decode_conversation_cursor(cursor) if cursor is not None else None,
    )
    set_no_store_headers(response)
    return ConversationCollectionResponse(
        conversations=[_summary_response(item) for item in page.items],
        next_cursor=(
            encode_conversation_cursor(page.next_cursor) if page.next_cursor is not None else None
        ),
    )


@router.get(
    "/{conversation_id}",
    response_model=ConversationDetailResponse,
    responses=_ITEM_RESPONSES,
)
async def get_conversation(
    workspace_id: UUID,
    conversation_id: NonNilUuid,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[
        ConversationManagementUseCase,
        Depends(get_conversation_management_service),
    ],
) -> ConversationDetailResponse:
    detail = await service.get_conversation(
        _workspace_scope(principal, workspace_id), conversation_id
    )
    set_no_store_headers(response)
    return _detail_response(detail)


@router.get(
    "/{conversation_id}/messages",
    response_model=ConversationMessageCollectionResponse,
    responses=_PAGED_ITEM_RESPONSES,
)
async def list_conversation_messages(
    workspace_id: UUID,
    conversation_id: NonNilUuid,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[
        ConversationManagementUseCase,
        Depends(get_conversation_management_service),
    ],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=MAX_CURSOR_LENGTH)] = None,
) -> ConversationMessageCollectionResponse:
    page = await service.list_messages(
        _workspace_scope(principal, workspace_id),
        conversation_id,
        page_size=limit,
        cursor=decode_message_cursor(cursor) if cursor is not None else None,
    )
    set_no_store_headers(response)
    return ConversationMessageCollectionResponse(
        messages=[_message_response(item) for item in page.items],
        next_cursor=(
            encode_message_cursor(page.next_cursor) if page.next_cursor is not None else None
        ),
    )


@router.patch(
    "/{conversation_id}",
    response_model=ConversationSummaryResponse,
    responses=_ITEM_RESPONSES,
)
async def rename_conversation(
    workspace_id: UUID,
    conversation_id: NonNilUuid,
    payload: RenameConversationRequest,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[
        ConversationManagementUseCase,
        Depends(get_conversation_management_service),
    ],
) -> ConversationSummaryResponse:
    summary = await service.rename(
        _workspace_scope(principal, workspace_id),
        RenameConversation(conversation_id=conversation_id, title=payload.title),
    )
    set_no_store_headers(response)
    return _summary_response(summary)


@router.delete(
    "/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_ITEM_RESPONSES,
)
async def delete_conversation(
    workspace_id: UUID,
    conversation_id: NonNilUuid,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[
        ConversationManagementUseCase,
        Depends(get_conversation_management_service),
    ],
) -> None:
    await service.delete(_workspace_scope(principal, workspace_id), conversation_id)
    set_no_store_headers(response)


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


def _summary_response(summary: ConversationSummary) -> ConversationSummaryResponse:
    return ConversationSummaryResponse(
        id=summary.conversation_id,
        title=summary.title,
        created_at=summary.created_at,
        updated_at=summary.updated_at,
    )


def _detail_response(detail: ConversationDetail) -> ConversationDetailResponse:
    return ConversationDetailResponse(
        **_summary_response(detail.summary).model_dump(),
        turn_count=detail.turn_count,
    )


def _message_response(message: ConversationMessage) -> ConversationMessageResponse:
    return ConversationMessageResponse(
        id=message.message_id,
        turn_id=message.turn_id,
        agent_run_id=message.agent_run_id,
        role=message.role,
        status=message.status,
        content_markdown=message.content_markdown,
        created_at=message.created_at,
    )
