"""Authenticated delivery for industry context, sources, and collection controls."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from industry_platform.core.http import problem_openapi_response, set_no_store_headers
from industry_platform.modules.identity.domain import AuthenticatedPrincipal
from industry_platform.modules.identity.http_auth import require_authenticated_principal
from industry_platform.modules.industry.domain import (
    CollectionScheduleRequest,
    IndustryPreference,
    IndustryPreset,
    SourceKind,
    search_industries,
)
from industry_platform.modules.industry.resources import (
    IndustryResources,
    get_industry_resources,
)
from industry_platform.modules.industry.schemas import (
    CollectionRunCollectionResponse,
    CollectionRunResponse,
    CollectionScheduleCollectionResponse,
    CollectionScheduleCreatedResponse,
    CollectionScheduleResponse,
    CreateCollectionScheduleRequest,
    IndustryCollectionResponse,
    IndustryPreferenceResponse,
    IndustryResponse,
    ProviderStatusCollectionResponse,
    ProviderStatusResponse,
    SetIndustryPreferenceRequest,
    SourceItemCollectionResponse,
    SourceItemResponse,
    TriggerCollectionRequest,
    TriggerCollectionResponse,
)
from industry_platform.modules.workspaces.domain import (
    WorkspaceAccessDeniedError,
    WorkspaceScope,
)

router = APIRouter(tags=["industry"])
type OpenApiResponses = dict[int | str, dict[str, Any]]

_RESPONSES: OpenApiResponses = {
    status.HTTP_401_UNAUTHORIZED: problem_openapi_response("Invalid authenticated session"),
    status.HTTP_403_FORBIDDEN: problem_openapi_response("Workspace access denied"),
    status.HTTP_404_NOT_FOUND: problem_openapi_response("Industry resource not found"),
    status.HTTP_409_CONFLICT: problem_openapi_response("Industry schedule conflict"),
    status.HTTP_422_UNPROCESSABLE_CONTENT: problem_openapi_response("Request validation failed"),
    status.HTTP_503_SERVICE_UNAVAILABLE: problem_openapi_response(
        "Industry service temporarily unavailable"
    ),
}


@router.get(
    "/industries",
    response_model=IndustryCollectionResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: _RESPONSES[status.HTTP_401_UNAUTHORIZED],
        status.HTTP_422_UNPROCESSABLE_CONTENT: _RESPONSES[status.HTTP_422_UNPROCESSABLE_CONTENT],
    },
)
async def list_industries(
    response: Response,
    _principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_authenticated_principal),
    ],
    query: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
) -> IndustryCollectionResponse:
    set_no_store_headers(response)
    return IndustryCollectionResponse(
        industries=[_industry_response(industry) for industry in search_industries(query)]
    )


@router.get(
    "/workspaces/{workspace_id}/industry-preference",
    response_model=IndustryPreferenceResponse | None,
    responses=_RESPONSES,
)
async def get_industry_preference(
    workspace_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[IndustryResources, Depends(get_industry_resources)],
) -> IndustryPreferenceResponse | None:
    preference = await resources.catalog_service.get_preference(
        _workspace_scope(principal, workspace_id)
    )
    set_no_store_headers(response)
    return None if preference is None else _preference_response(preference)


@router.patch(
    "/workspaces/{workspace_id}/industry-preference",
    response_model=IndustryPreferenceResponse,
    responses=_RESPONSES,
)
async def set_industry_preference(
    workspace_id: UUID,
    payload: SetIndustryPreferenceRequest,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[IndustryResources, Depends(get_industry_resources)],
) -> IndustryPreferenceResponse:
    preference = await resources.catalog_service.set_preference(
        _workspace_scope(principal, workspace_id), payload.industry_id
    )
    set_no_store_headers(response)
    return _preference_response(preference)


@router.get(
    "/workspaces/{workspace_id}/industry-sources/readiness",
    response_model=ProviderStatusCollectionResponse,
    responses=_RESPONSES,
)
async def provider_readiness(
    workspace_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[IndustryResources, Depends(get_industry_resources)],
) -> ProviderStatusCollectionResponse:
    await resources.catalog_service.list_runs(_workspace_scope(principal, workspace_id), limit=1)
    set_no_store_headers(response)
    return ProviderStatusCollectionResponse(
        providers=[
            ProviderStatusResponse(
                provider=item.provider,
                kind=item.kind,
                readiness=item.readiness,
                reason_code=item.reason_code,
            )
            for item in resources.collection_service.provider_statuses()
        ]
    )


@router.get(
    "/workspaces/{workspace_id}/industry-sources/items",
    response_model=SourceItemCollectionResponse,
    responses=_RESPONSES,
)
async def list_source_items(
    workspace_id: UUID,
    industry_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[IndustryResources, Depends(get_industry_resources)],
    kind: SourceKind | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> SourceItemCollectionResponse:
    items = await resources.catalog_service.list_items(
        _workspace_scope(principal, workspace_id),
        industry_id=industry_id,
        kind=kind,
        limit=limit,
        offset=offset,
    )
    set_no_store_headers(response)
    return SourceItemCollectionResponse(
        items=[
            SourceItemResponse(
                id=item.source_item_id,
                industry_id=item.industry_id,
                kind=item.kind,
                provider=item.provider,
                external_id=item.external_id,
                title=item.title,
                summary=item.summary,
                locator=item.locator,
                published_at=item.published_at,
                collected_at=item.collected_at,
                content_sha256=item.content_sha256,
                metadata=dict(item.metadata),
            )
            for item in items
        ],
        limit=limit,
        offset=offset,
    )


@router.get(
    "/workspaces/{workspace_id}/industry-collections/runs",
    response_model=CollectionRunCollectionResponse,
    responses=_RESPONSES,
)
async def list_collection_runs(
    workspace_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[IndustryResources, Depends(get_industry_resources)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CollectionRunCollectionResponse:
    runs = await resources.catalog_service.list_runs(
        _workspace_scope(principal, workspace_id), limit=limit
    )
    set_no_store_headers(response)
    return CollectionRunCollectionResponse(
        runs=[
            CollectionRunResponse(
                id=run.collection_run_id,
                industry_id=run.industry_id,
                kind=run.kind,
                provider=run.provider,
                status=run.status,
                scheduled_for=run.scheduled_for,
                started_at=run.started_at,
                terminal_at=run.terminal_at,
                last_error_code=run.last_error_code,
                fetched_count=run.fetched_count,
                inserted_count=run.inserted_count,
                duplicate_count=run.duplicate_count,
            )
            for run in runs
        ]
    )


@router.get(
    "/workspaces/{workspace_id}/industry-collections/schedules",
    response_model=CollectionScheduleCollectionResponse,
    responses=_RESPONSES,
)
async def list_collection_schedules(
    workspace_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[IndustryResources, Depends(get_industry_resources)],
) -> CollectionScheduleCollectionResponse:
    schedules = await resources.catalog_service.list_schedules(
        _workspace_scope(principal, workspace_id)
    )
    set_no_store_headers(response)
    return CollectionScheduleCollectionResponse(
        schedules=[
            CollectionScheduleResponse(
                id=item.schedule_id,
                industry_id=item.industry_id,
                kind=item.kind,
                cron_expression=item.cron_expression,
                timezone_name=item.timezone_name,
                next_due_at=item.next_due_at,
                last_fired_at=item.last_fired_at,
                enabled=item.enabled,
                misfire_policy=item.misfire_policy,
                misfire_error_code=item.misfire_error_code,
            )
            for item in schedules
        ]
    )


@router.post(
    "/workspaces/{workspace_id}/industry-collections/schedules",
    response_model=CollectionScheduleCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_RESPONSES,
)
async def create_collection_schedule(
    workspace_id: UUID,
    payload: CreateCollectionScheduleRequest,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[IndustryResources, Depends(get_industry_resources)],
) -> CollectionScheduleCreatedResponse:
    ensured = await resources.schedule_service.ensure_schedule(
        CollectionScheduleRequest(
            scope=_workspace_scope(principal, workspace_id),
            industry_id=payload.industry_id,
            kind=payload.kind,
            cron_expression=payload.cron_expression,
            timezone_name=payload.timezone_name,
            misfire_policy=payload.misfire_policy,
            catch_up_window_seconds=payload.catch_up_window_seconds,
            max_catch_up=payload.max_catch_up,
        )
    )
    set_no_store_headers(response)
    return CollectionScheduleCreatedResponse(id=ensured.schedule_id, created=ensured.created)


@router.post(
    "/workspaces/{workspace_id}/industry-collections/schedules/{schedule_id}/runs",
    response_model=TriggerCollectionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_RESPONSES,
)
async def trigger_collection(
    workspace_id: UUID,
    schedule_id: UUID,
    payload: TriggerCollectionRequest,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[IndustryResources, Depends(get_industry_resources)],
) -> TriggerCollectionResponse:
    result = await resources.schedule_service.trigger_manual(
        _workspace_scope(principal, workspace_id),
        schedule_id=schedule_id,
        trigger_id=payload.trigger_id,
    )
    set_no_store_headers(response)
    return TriggerCollectionResponse(
        occurrence_id=result.occurrence_id,
        job_id=result.job_id,
        created=result.created,
    )


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


def _industry_response(industry: IndustryPreset) -> IndustryResponse:
    return IndustryResponse(
        id=industry.industry_id,
        code=industry.code,
        name=industry.name,
        default_query=industry.default_query,
        default_symbol=industry.default_symbol,
    )


def _preference_response(preference: IndustryPreference) -> IndustryPreferenceResponse:
    return IndustryPreferenceResponse(
        workspace_id=preference.workspace_id,
        user_id=preference.user_id,
        industry=_industry_response(preference.industry),
        updated_at=preference.updated_at,
    )
