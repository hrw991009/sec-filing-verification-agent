"""Authenticated fetch-SSE and cancellation delivery for Agent Runs."""

import asyncio
import logging
from collections.abc import AsyncIterator
from time import monotonic
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response, status
from starlette.responses import StreamingResponse

from industry_platform.core.http import problem_openapi_response, set_no_store_headers
from industry_platform.modules.agent_runtime.delivery import (
    AgentRunDeliveryStateError,
    AgentRunDeliveryUnavailableError,
    AgentRunDeliveryUseCase,
    PreparedAgentEventStream,
)
from industry_platform.modules.agent_runtime.events import TERMINAL_AGENT_EVENT_TYPES
from industry_platform.modules.agent_runtime.resources import (
    AgentRunDeliveryResources,
    AgentTraceQuery,
    AgentTraceResources,
    get_agent_run_delivery_resources,
    get_agent_trace_resources,
)
from industry_platform.modules.agent_runtime.schemas import (
    AgentTraceResponse,
    agent_trace_response,
)
from industry_platform.modules.agent_runtime.streaming import (
    DEFAULT_HEARTBEAT_SECONDS,
    StreamContractError,
    encode_agent_event_sse,
    encode_heartbeat_sse,
    encode_snapshot_sse,
)
from industry_platform.modules.identity.domain import AuthenticatedPrincipal
from industry_platform.modules.identity.http_auth import require_authenticated_principal
from industry_platform.modules.workspaces.domain import (
    WorkspaceAccessDeniedError,
    WorkspaceScope,
)

POLL_INTERVAL_SECONDS = 0.25
SSE_MEDIA_TYPE = "text/event-stream"

router = APIRouter(
    prefix="/workspaces/{workspace_id}/agent-runs",
    tags=["agent-runs"],
)
type OpenApiResponses = dict[int | str, dict[str, Any]]

_COMMON_RESPONSES: OpenApiResponses = {
    status.HTTP_401_UNAUTHORIZED: problem_openapi_response("Invalid authenticated session"),
    status.HTTP_403_FORBIDDEN: problem_openapi_response("Workspace access denied"),
    status.HTTP_404_NOT_FOUND: problem_openapi_response("Agent Run not found"),
    status.HTTP_422_UNPROCESSABLE_CONTENT: problem_openapi_response("Request validation failed"),
    status.HTTP_503_SERVICE_UNAVAILABLE: problem_openapi_response(
        "Agent event delivery temporarily unavailable"
    ),
}
_STREAM_RESPONSES: OpenApiResponses = {
    status.HTTP_200_OK: {
        "description": "Committed Agent Event stream",
        "content": {SSE_MEDIA_TYPE: {"schema": {"type": "string"}}},
    },
    **_COMMON_RESPONSES,
    status.HTTP_400_BAD_REQUEST: problem_openapi_response("Invalid Agent stream cursor"),
    status.HTTP_409_CONFLICT: problem_openapi_response("Agent stream recovery required"),
}
_TRACE_RESPONSES: OpenApiResponses = {
    status.HTTP_401_UNAUTHORIZED: problem_openapi_response("Invalid authenticated session"),
    status.HTTP_403_FORBIDDEN: problem_openapi_response("Workspace access denied"),
    status.HTTP_404_NOT_FOUND: problem_openapi_response("Agent Run not found"),
    status.HTTP_422_UNPROCESSABLE_CONTENT: problem_openapi_response("Request validation failed"),
    status.HTTP_500_INTERNAL_SERVER_ERROR: problem_openapi_response(
        "Persisted Agent Trace is inconsistent"
    ),
    status.HTTP_503_SERVICE_UNAVAILABLE: problem_openapi_response(
        "Agent Trace temporarily unavailable"
    ),
}

logger = logging.getLogger(__name__)


def get_agent_run_delivery_service(
    resources: Annotated[
        AgentRunDeliveryResources,
        Depends(get_agent_run_delivery_resources),
    ],
) -> AgentRunDeliveryUseCase:
    return resources.service


def get_agent_trace_query(
    resources: Annotated[AgentTraceResources, Depends(get_agent_trace_resources)],
) -> AgentTraceQuery:
    return resources.query


@router.get(
    "/{run_id}/trace",
    response_model=AgentTraceResponse,
    responses=_TRACE_RESPONSES,
)
async def get_agent_run_trace(
    workspace_id: UUID,
    run_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    query: Annotated[AgentTraceQuery, Depends(get_agent_trace_query)],
) -> AgentTraceResponse:
    """Return the sanitized PostgreSQL Trace projection for one authorized Run."""

    trace = await query.get(scope=_workspace_scope(principal, workspace_id), run_id=run_id)
    set_no_store_headers(response)
    return agent_trace_response(trace)


@router.get(
    "/{run_id}/events",
    response_class=StreamingResponse,
    responses=_STREAM_RESPONSES,
)
async def stream_agent_run_events(
    workspace_id: UUID,
    run_id: UUID,
    request: Request,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[AgentRunDeliveryUseCase, Depends(get_agent_run_delivery_service)],
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """Replay and follow only committed Events for one authorized Agent Run."""

    scope = _workspace_scope(principal, workspace_id)
    prepared = await service.prepare_stream(
        scope,
        run_id=run_id,
        last_event_id=last_event_id,
    )
    response = StreamingResponse(
        _committed_event_frames(request, service, scope, prepared),
        media_type=SSE_MEDIA_TYPE,
        headers={"X-Accel-Buffering": "no"},
    )
    set_no_store_headers(response)
    return response


@router.post(
    "/{run_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
    response_class=Response,
    responses=_COMMON_RESPONSES,
)
async def cancel_agent_run(
    workspace_id: UUID,
    run_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[AgentRunDeliveryUseCase, Depends(get_agent_run_delivery_service)],
) -> None:
    """Persist an idempotent cancellation request without claiming terminal success."""

    await service.request_cancel(_workspace_scope(principal, workspace_id), run_id=run_id)
    set_no_store_headers(response)


async def _committed_event_frames(
    request: Request,
    service: AgentRunDeliveryUseCase,
    scope: WorkspaceScope,
    prepared: PreparedAgentEventStream,
) -> AsyncIterator[bytes]:
    last_sequence = prepared.replay.cursor
    if prepared.replay.snapshot is not None:
        last_sequence = prepared.replay.snapshot.last_sequence
        yield encode_snapshot_sse(prepared.replay.snapshot)
    for event in prepared.replay.events:
        last_sequence = event.sequence
        yield encode_agent_event_sse(event)
        if event.event_type in TERMINAL_AGENT_EVENT_TYPES:
            return
    if prepared.descriptor.is_terminal:
        return

    next_heartbeat = monotonic() + DEFAULT_HEARTBEAT_SECONDS
    while not await request.is_disconnected():
        try:
            events = await service.load_events_after(
                scope,
                descriptor=prepared.descriptor,
                after_sequence=last_sequence,
            )
        except AgentRunDeliveryUnavailableError as error:
            logger.warning(
                "Agent SSE follow read failed trace_id=%s sqlstate=%s",
                prepared.descriptor.trace_id,
                error.sqlstate or "unknown",
            )
            return
        except (AgentRunDeliveryStateError, StreamContractError) as error:
            logger.warning(
                "Agent SSE follow contract failed trace_id=%s error=%s",
                prepared.descriptor.trace_id,
                type(error).__name__,
            )
            return

        if events:
            for event in events:
                last_sequence = event.sequence
                yield encode_agent_event_sse(event)
                if event.event_type in TERMINAL_AGENT_EVENT_TYPES:
                    return
            continue

        current_time = monotonic()
        if current_time >= next_heartbeat:
            yield encode_heartbeat_sse(last_sequence=last_sequence)
            next_heartbeat = current_time + DEFAULT_HEARTBEAT_SECONDS
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


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
