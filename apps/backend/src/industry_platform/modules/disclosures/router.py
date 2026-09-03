"""Authenticated HTTP delivery for public SEC filer discovery."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status

from industry_platform.core.http import (
    get_trace_id,
    problem_openapi_response,
    set_no_store_headers,
)
from industry_platform.modules.disclosures.monitor import SecMonitorStatus
from industry_platform.modules.disclosures.resources import (
    DisclosureResources,
    get_disclosure_resources,
)
from industry_platform.modules.disclosures.schemas import (
    ChangeSecMonitorStatusRequest,
    DecideSecMonitorSubscriptionRequest,
    FilingSelectionQuery,
    SecDisclosureCaseCollectionResponse,
    SecDisclosureCaseResponse,
    SecFilerResolutionResponse,
    SecFilingDiffQueryParameters,
    SecFilingDiffResponse,
    SecFilingImportCollectionResponse,
    SecFilingImportRequest,
    SecFilingSearchQuery,
    SecFilingSearchResponse,
    SecFilingSectionQuery,
    SecFilingSectionResponse,
    SecFilingSelectionResponse,
    SecMonitorCollectionResponse,
    SecMonitorResponse,
    SecMonitorSubscriptionDecisionResponse,
    SecWorkspaceFilingImportResponse,
    SecXbrlFactCollectionResponse,
    SecXbrlFactQueryParameters,
    SecXbrlSyncRequest,
    SecXbrlSyncResponse,
    TriggerSecMonitorRunRequest,
    TriggerSecMonitorRunResponse,
)
from industry_platform.modules.disclosures.subscription import (
    ChangeSecMonitorStatus,
    DecideSecMonitorSubscription,
    TriggerSecMonitorRun,
)
from industry_platform.modules.identity.domain import AuthenticatedPrincipal, TraceId
from industry_platform.modules.identity.http_auth import require_authenticated_principal
from industry_platform.modules.research.schemas import ResearchApprovalResponse
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
_MONITOR_RESPONSES: OpenApiResponses = {
    **_RESPONSES,
    status.HTTP_404_NOT_FOUND: problem_openapi_response("Monitor resource not found"),
    status.HTTP_409_CONFLICT: problem_openapi_response("Monitor state conflict"),
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


@router.get(
    "/workspaces/{workspace_id}/disclosures/filings",
    response_model=SecFilingSelectionResponse,
    responses=_RESPONSES,
)
async def list_filings(
    workspace_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[DisclosureResources, Depends(get_disclosure_resources)],
    query: Annotated[FilingSelectionQuery, Query()],
) -> SecFilingSelectionResponse:
    result = await resources.filing_selection_service.select(
        _workspace_scope(principal, workspace_id),
        selection_scope=query.to_domain(),
    )
    set_no_store_headers(response)
    return SecFilingSelectionResponse.from_domain(result)


@router.post(
    "/workspaces/{workspace_id}/disclosures/filings/{accession}/imports",
    response_model=SecWorkspaceFilingImportResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_RESPONSES,
)
async def import_filing(
    workspace_id: UUID,
    payload: SecFilingImportRequest,
    request: Request,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[DisclosureResources, Depends(get_disclosure_resources)],
    accession: Annotated[str, Path(pattern=r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")],
) -> SecWorkspaceFilingImportResponse:
    imported = await resources.filing_import_service.import_filing(
        _workspace_scope(principal, workspace_id),
        accession=accession,
        knowledge_base_id=payload.knowledge_base_id,
        as_of=payload.as_of,
        trace_id=TraceId(get_trace_id(request)),
    )
    set_no_store_headers(response)
    return SecWorkspaceFilingImportResponse.from_domain(imported)


@router.get(
    "/workspaces/{workspace_id}/disclosures/filing-imports",
    response_model=SecFilingImportCollectionResponse,
    responses=_RESPONSES,
)
async def list_filing_imports(
    workspace_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[DisclosureResources, Depends(get_disclosure_resources)],
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> SecFilingImportCollectionResponse:
    imports = await resources.filing_import_service.list_imports(
        _workspace_scope(principal, workspace_id),
        limit=limit,
    )
    set_no_store_headers(response)
    return SecFilingImportCollectionResponse(
        imports=[SecWorkspaceFilingImportResponse.from_domain(item) for item in imports]
    )


@router.get(
    "/workspaces/{workspace_id}/disclosures/filing-imports/{import_id}",
    response_model=SecWorkspaceFilingImportResponse,
    responses=_RESPONSES,
)
async def get_filing_import(
    workspace_id: UUID,
    import_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[DisclosureResources, Depends(get_disclosure_resources)],
) -> SecWorkspaceFilingImportResponse:
    imported = await resources.filing_import_service.get_import(
        _workspace_scope(principal, workspace_id),
        import_id,
    )
    set_no_store_headers(response)
    return SecWorkspaceFilingImportResponse.from_domain(imported)


@router.get(
    "/workspaces/{workspace_id}/disclosures/filings/{accession}/search",
    response_model=SecFilingSearchResponse,
    responses=_RESPONSES,
)
async def search_filing(
    workspace_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[DisclosureResources, Depends(get_disclosure_resources)],
    accession: Annotated[str, Path(pattern=r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")],
    query: Annotated[SecFilingSearchQuery, Query()],
) -> SecFilingSearchResponse:
    result = await resources.filing_content_service.search_imported(
        _workspace_scope(principal, workspace_id),
        knowledge_base_ids=(query.knowledge_base_id,),
        accession=accession,
        as_of=query.as_of,
        query=query.query,
    )
    set_no_store_headers(response)
    return SecFilingSearchResponse.from_domain(result)


@router.get(
    "/workspaces/{workspace_id}/disclosures/filings/{accession}/sections/{chunk_id}",
    response_model=SecFilingSectionResponse,
    responses=_RESPONSES,
)
async def read_filing_section(
    workspace_id: UUID,
    chunk_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[DisclosureResources, Depends(get_disclosure_resources)],
    accession: Annotated[str, Path(pattern=r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")],
    query: Annotated[SecFilingSectionQuery, Query()],
) -> SecFilingSectionResponse:
    section = await resources.filing_content_service.read_imported_section(
        _workspace_scope(principal, workspace_id),
        knowledge_base_ids=(query.knowledge_base_id,),
        accession=accession,
        as_of=query.as_of,
        document_version_id=query.document_version_id,
        chunk_id=chunk_id,
    )
    set_no_store_headers(response)
    return SecFilingSectionResponse.from_domain(section)


@router.post(
    "/workspaces/{workspace_id}/disclosures/filings/{accession}/xbrl/sync",
    response_model=SecXbrlSyncResponse,
    responses=_RESPONSES,
)
async def sync_xbrl_facts(
    workspace_id: UUID,
    payload: SecXbrlSyncRequest,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[DisclosureResources, Depends(get_disclosure_resources)],
    accession: Annotated[str, Path(pattern=r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")],
) -> SecXbrlSyncResponse:
    result = await resources.xbrl_service.sync(
        _workspace_scope(principal, workspace_id),
        accession=accession,
        knowledge_base_id=payload.knowledge_base_id,
    )
    set_no_store_headers(response)
    return SecXbrlSyncResponse.from_domain(result)


@router.get(
    "/workspaces/{workspace_id}/disclosures/filings/{accession}/xbrl/facts",
    response_model=SecXbrlFactCollectionResponse,
    responses=_RESPONSES,
)
async def get_xbrl_facts(
    workspace_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[DisclosureResources, Depends(get_disclosure_resources)],
    accession: Annotated[str, Path(pattern=r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")],
    query: Annotated[SecXbrlFactQueryParameters, Query()],
) -> SecXbrlFactCollectionResponse:
    result = await resources.xbrl_service.get_imported_facts(
        _workspace_scope(principal, workspace_id),
        knowledge_base_ids=(query.knowledge_base_id,),
        accession=accession,
        as_of=query.as_of,
        query=query.to_domain(),
    )
    set_no_store_headers(response)
    return SecXbrlFactCollectionResponse.from_domain(result)


@router.get(
    "/workspaces/{workspace_id}/disclosures/filings/{accession}/diff",
    response_model=SecFilingDiffResponse,
    responses=_RESPONSES,
)
async def diff_filings(
    workspace_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[DisclosureResources, Depends(get_disclosure_resources)],
    accession: Annotated[str, Path(pattern=r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")],
    query: Annotated[SecFilingDiffQueryParameters, Query()],
) -> SecFilingDiffResponse:
    result = await resources.filing_diff_service.compare(
        _workspace_scope(principal, workspace_id),
        knowledge_base_ids=(query.knowledge_base_id,),
        financial_scope=query.to_financial_scope(accession),
        comparison_accession=query.comparison_accession,
        section_query=query.section_query,
        taxonomy=query.taxonomy,
        concept=query.concept,
        fact_limit=query.fact_limit,
    )
    set_no_store_headers(response)
    return SecFilingDiffResponse.from_domain(result)


@router.post(
    "/workspaces/{workspace_id}/research-runs/{research_run_id}/monitor-subscription-decisions",
    response_model=SecMonitorSubscriptionDecisionResponse,
    responses=_MONITOR_RESPONSES,
)
async def decide_monitor_subscription(
    workspace_id: UUID,
    research_run_id: UUID,
    payload: DecideSecMonitorSubscriptionRequest,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[DisclosureResources, Depends(get_disclosure_resources)],
) -> SecMonitorSubscriptionDecisionResponse:
    result = await resources.monitor_subscription_service.decide(
        _workspace_scope(principal, workspace_id),
        DecideSecMonitorSubscription(
            research_run_id=research_run_id,
            approval_request_id=payload.approval_request_id,
            checkpoint_revision=payload.checkpoint_revision,
            outcome=payload.outcome,
        ),
    )
    approval = result.approval
    tool_request = approval.tool_request
    set_no_store_headers(response)
    return SecMonitorSubscriptionDecisionResponse(
        approval=ResearchApprovalResponse(
            approval_request_id=approval.approval_request_id,
            run_id=approval.run_id,
            checkpoint_id=approval.checkpoint_id,
            checkpoint_revision=approval.checkpoint_revision,
            reason=approval.reason,
            status=approval.status,
            requested_by_user_id=approval.requested_by_user_id,
            created_at=approval.created_at,
            expires_at=approval.expires_at,
            decided_by_user_id=approval.decided_by_user_id,
            decided_at=approval.decided_at,
            resume_claimed=approval.resume_claimed,
            resume_job_id=approval.resume_job_id,
            resumed_at=approval.resumed_at,
            tool_call_id=None if tool_request is None else tool_request.call_id,
            tool_name=None if tool_request is None else tool_request.tool.name,
            tool_version=None if tool_request is None else tool_request.tool.version,
            tool_arguments=None if tool_request is None else dict(tool_request.arguments),
            tool_arguments_sha256=(None if tool_request is None else tool_request.arguments_sha256),
            resume_token=None,
        ),
        monitor=(
            None if result.monitor is None else SecMonitorResponse.from_domain(result.monitor)
        ),
        resume_job_id=result.resume_job_id,
        created=result.created,
    )


@router.get(
    "/workspaces/{workspace_id}/disclosures/monitors",
    response_model=SecMonitorCollectionResponse,
    responses=_MONITOR_RESPONSES,
)
async def list_monitors(
    workspace_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[DisclosureResources, Depends(get_disclosure_resources)],
) -> SecMonitorCollectionResponse:
    values = await resources.monitor_subscription_service.list_monitors(
        _workspace_scope(principal, workspace_id)
    )
    set_no_store_headers(response)
    return SecMonitorCollectionResponse(
        monitors=[SecMonitorResponse.from_domain(value) for value in values]
    )


@router.get(
    "/workspaces/{workspace_id}/disclosures/monitors/{monitor_id}",
    response_model=SecMonitorResponse,
    responses=_MONITOR_RESPONSES,
)
async def get_monitor(
    workspace_id: UUID,
    monitor_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[DisclosureResources, Depends(get_disclosure_resources)],
) -> SecMonitorResponse:
    value = await resources.monitor_subscription_service.get_monitor(
        _workspace_scope(principal, workspace_id), monitor_id
    )
    set_no_store_headers(response)
    return SecMonitorResponse.from_domain(value)


async def _change_monitor_status(
    *,
    workspace_id: UUID,
    monitor_id: UUID,
    payload: ChangeSecMonitorStatusRequest,
    response: Response,
    principal: AuthenticatedPrincipal,
    resources: DisclosureResources,
    target: SecMonitorStatus,
) -> SecMonitorResponse:
    value = await resources.monitor_subscription_service.change_status(
        _workspace_scope(principal, workspace_id),
        ChangeSecMonitorStatus(
            monitor_id=monitor_id,
            expected_revision=payload.expected_revision,
            status=target,
        ),
    )
    set_no_store_headers(response)
    return SecMonitorResponse.from_domain(value)


@router.post(
    "/workspaces/{workspace_id}/disclosures/monitors/{monitor_id}/pause",
    response_model=SecMonitorResponse,
    responses=_MONITOR_RESPONSES,
)
async def pause_monitor(
    workspace_id: UUID,
    monitor_id: UUID,
    payload: ChangeSecMonitorStatusRequest,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[DisclosureResources, Depends(get_disclosure_resources)],
) -> SecMonitorResponse:
    return await _change_monitor_status(
        workspace_id=workspace_id,
        monitor_id=monitor_id,
        payload=payload,
        response=response,
        principal=principal,
        resources=resources,
        target=SecMonitorStatus.PAUSED,
    )


@router.post(
    "/workspaces/{workspace_id}/disclosures/monitors/{monitor_id}/resume",
    response_model=SecMonitorResponse,
    responses=_MONITOR_RESPONSES,
)
async def resume_monitor(
    workspace_id: UUID,
    monitor_id: UUID,
    payload: ChangeSecMonitorStatusRequest,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[DisclosureResources, Depends(get_disclosure_resources)],
) -> SecMonitorResponse:
    return await _change_monitor_status(
        workspace_id=workspace_id,
        monitor_id=monitor_id,
        payload=payload,
        response=response,
        principal=principal,
        resources=resources,
        target=SecMonitorStatus.ACTIVE,
    )


@router.post(
    "/workspaces/{workspace_id}/disclosures/monitors/{monitor_id}/runs",
    response_model=TriggerSecMonitorRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_MONITOR_RESPONSES,
)
async def trigger_monitor_run(
    workspace_id: UUID,
    monitor_id: UUID,
    payload: TriggerSecMonitorRunRequest,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[DisclosureResources, Depends(get_disclosure_resources)],
) -> TriggerSecMonitorRunResponse:
    result = await resources.monitor_subscription_service.trigger_run(
        _workspace_scope(principal, workspace_id),
        TriggerSecMonitorRun(
            monitor_id=monitor_id,
            expected_revision=payload.expected_revision,
            trigger_id=payload.trigger_id,
        ),
    )
    set_no_store_headers(response)
    return TriggerSecMonitorRunResponse(
        occurrence_id=result.occurrence_id,
        job_id=result.job_id,
        created=result.created,
    )


@router.delete(
    "/workspaces/{workspace_id}/disclosures/monitors/{monitor_id}",
    response_model=SecMonitorResponse,
    responses=_MONITOR_RESPONSES,
)
async def delete_monitor(
    workspace_id: UUID,
    monitor_id: UUID,
    payload: ChangeSecMonitorStatusRequest,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[DisclosureResources, Depends(get_disclosure_resources)],
) -> SecMonitorResponse:
    return await _change_monitor_status(
        workspace_id=workspace_id,
        monitor_id=monitor_id,
        payload=payload,
        response=response,
        principal=principal,
        resources=resources,
        target=SecMonitorStatus.DELETED,
    )


@router.get(
    "/workspaces/{workspace_id}/disclosures/cases",
    response_model=SecDisclosureCaseCollectionResponse,
    responses=_MONITOR_RESPONSES,
)
async def list_cases(
    workspace_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[DisclosureResources, Depends(get_disclosure_resources)],
    monitor_id: Annotated[UUID | None, Query()] = None,
) -> SecDisclosureCaseCollectionResponse:
    values = await resources.monitor_subscription_service.list_cases(
        _workspace_scope(principal, workspace_id), monitor_id=monitor_id
    )
    set_no_store_headers(response)
    return SecDisclosureCaseCollectionResponse(
        cases=[SecDisclosureCaseResponse.from_domain(value) for value in values]
    )


@router.get(
    "/workspaces/{workspace_id}/disclosures/cases/{case_id}",
    response_model=SecDisclosureCaseResponse,
    responses=_MONITOR_RESPONSES,
)
async def get_case(
    workspace_id: UUID,
    case_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[DisclosureResources, Depends(get_disclosure_resources)],
) -> SecDisclosureCaseResponse:
    value = await resources.monitor_subscription_service.get_case(
        _workspace_scope(principal, workspace_id), case_id
    )
    set_no_store_headers(response)
    return SecDisclosureCaseResponse.from_domain(value)


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
