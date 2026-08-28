"""Authenticated HTTP delivery for public SEC filer discovery."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status

from industry_platform.core.http import (
    get_trace_id,
    problem_openapi_response,
    set_no_store_headers,
)
from industry_platform.modules.disclosures.resources import (
    DisclosureResources,
    get_disclosure_resources,
)
from industry_platform.modules.disclosures.schemas import (
    FilingSelectionQuery,
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
    SecWorkspaceFilingImportResponse,
    SecXbrlFactCollectionResponse,
    SecXbrlFactQueryParameters,
    SecXbrlSyncRequest,
    SecXbrlSyncResponse,
)
from industry_platform.modules.identity.domain import AuthenticatedPrincipal, TraceId
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
