"""Authenticated HTTP delivery for the Evidence and Claim ledger."""

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from industry_platform.core.http import get_trace_id, problem_openapi_response, set_no_store_headers
from industry_platform.modules.evidence.domain import (
    ClaimEvidenceInput,
    ClaimGraph,
    ClaimNotFoundError,
    CreateClaim,
    Evidence,
    EvidenceKind,
    EvidenceNormalizationResult,
    EvidenceRequestRejectedError,
    EvidenceStatus,
    FinancialCalculationLocatorV1,
    IndustrySourceLocatorV1,
    InvalidateEvidence,
    NormalizeObservation,
    ResearchClaim,
    SecFilingChunkLocatorV1,
    SecFilingTextLocatorV1,
    SecXbrlFactLocatorV1,
    SqlResultLocatorV1,
)
from industry_platform.modules.evidence.ports import EvidenceUseCase
from industry_platform.modules.evidence.resources import EvidenceResources, get_evidence_resources
from industry_platform.modules.evidence.schemas import (
    AuthorizationSnapshotResponse,
    CitationResolutionResponse,
    ClaimEvidenceResponse,
    ClaimGraphResponse,
    CreateClaimRequest,
    EvidenceCollectionResponse,
    EvidenceNormalizationItemResponse,
    EvidenceNormalizationResponse,
    EvidenceResponse,
    FinancialCalculationLocatorResponse,
    GraphEdgeResponse,
    GraphNodeResponse,
    IndustrySourceLocatorResponse,
    InvalidateEvidenceRequest,
    NormalizeObservationRequest,
    ResearchClaimCollectionResponse,
    ResearchClaimResponse,
    SecFilingChunkLocatorResponse,
    SecFilingTableCellCoordinateResponse,
    SecFilingTextLocatorResponse,
    SecXbrlFactLocatorResponse,
    SqlResultLocatorResponse,
)
from industry_platform.modules.identity.domain import AuthenticatedPrincipal, TraceId
from industry_platform.modules.identity.http_auth import require_authenticated_principal
from industry_platform.modules.workspaces.domain import WorkspaceAccessDeniedError, WorkspaceScope

router = APIRouter(tags=["evidence"])
type OpenApiResponses = dict[int | str, dict[str, Any]]

_RESPONSES: OpenApiResponses = {
    status.HTTP_401_UNAUTHORIZED: problem_openapi_response("Invalid authenticated session"),
    status.HTTP_403_FORBIDDEN: problem_openapi_response("Workspace access denied"),
    status.HTTP_404_NOT_FOUND: problem_openapi_response("Evidence resource not found"),
    status.HTTP_409_CONFLICT: problem_openapi_response("Evidence revision conflict"),
    status.HTTP_422_UNPROCESSABLE_CONTENT: problem_openapi_response("Evidence request rejected"),
    status.HTTP_500_INTERNAL_SERVER_ERROR: problem_openapi_response("Internal server error"),
    status.HTTP_503_SERVICE_UNAVAILABLE: problem_openapi_response(
        "Evidence service temporarily unavailable"
    ),
}


def get_evidence_service(
    resources: Annotated[EvidenceResources, Depends(get_evidence_resources)],
) -> EvidenceUseCase:
    return resources.service


@router.post(
    "/workspaces/{workspace_id}/evidence/normalizations",
    response_model=EvidenceNormalizationResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_RESPONSES,
)
async def normalize_observation(
    workspace_id: UUID,
    payload: NormalizeObservationRequest,
    request: Request,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[EvidenceUseCase, Depends(get_evidence_service)],
) -> EvidenceNormalizationResponse:
    result = await service.normalize_observation(
        _workspace_scope(principal, workspace_id),
        NormalizeObservation(
            tool_call_id=payload.tool_call_id,
            observation_id=payload.observation_id,
            trace_id=TraceId(get_trace_id(request)),
        ),
    )
    set_no_store_headers(response)
    return _normalization_response(result)


@router.get(
    "/workspaces/{workspace_id}/evidence",
    response_model=EvidenceCollectionResponse,
    responses=_RESPONSES,
)
async def list_evidence(
    workspace_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[EvidenceUseCase, Depends(get_evidence_service)],
    evidence_status: Annotated[EvidenceStatus | None, Query(alias="status")] = None,
    kind: Annotated[EvidenceKind | None, Query()] = None,
    origin_run_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> EvidenceCollectionResponse:
    items = await service.list_evidence(
        _workspace_scope(principal, workspace_id),
        status=evidence_status,
        kind=kind,
        origin_run_id=origin_run_id,
        limit=limit,
    )
    set_no_store_headers(response)
    return EvidenceCollectionResponse(evidence=[_evidence_response(item) for item in items])


@router.get(
    "/workspaces/{workspace_id}/evidence/{evidence_id}",
    response_model=EvidenceResponse,
    responses=_RESPONSES,
)
async def get_evidence(
    workspace_id: UUID,
    evidence_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[EvidenceUseCase, Depends(get_evidence_service)],
) -> EvidenceResponse:
    evidence = await service.get_evidence(_workspace_scope(principal, workspace_id), evidence_id)
    set_no_store_headers(response)
    _set_revision_header(response, evidence.revision)
    return _evidence_response(evidence)


@router.get(
    "/workspaces/{workspace_id}/evidence/{evidence_id}/resolve",
    response_model=CitationResolutionResponse,
    responses=_RESPONSES,
)
async def resolve_citation(
    workspace_id: UUID,
    evidence_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[EvidenceUseCase, Depends(get_evidence_service)],
    table_index: Annotated[int | None, Query(ge=1)] = None,
    row_index: Annotated[int | None, Query(ge=1)] = None,
    column_index: Annotated[int | None, Query(ge=1)] = None,
) -> CitationResolutionResponse:
    requested_cell = (table_index, row_index, column_index)
    if any(value is not None for value in requested_cell) and any(
        value is None for value in requested_cell
    ):
        raise EvidenceRequestRejectedError
    scope = _workspace_scope(principal, workspace_id)
    evidence, available = await service.resolve_evidence(scope, evidence_id)
    resolved_cell = None
    failure_reason: Literal["evidence_unavailable", "table_cell_not_found"] | None = None
    if not available:
        failure_reason = "evidence_unavailable"
    elif table_index is not None and row_index is not None and column_index is not None:
        locator = evidence.locator
        if isinstance(locator, SecFilingTextLocatorV1):
            resolved_cell = next(
                (
                    cell
                    for cell in locator.table_cells
                    if (
                        cell.table_index,
                        cell.row_index,
                        cell.column_index,
                    )
                    == (table_index, row_index, column_index)
                ),
                None,
            )
        if resolved_cell is None:
            available = False
            failure_reason = "table_cell_not_found"
    evidence_response = _evidence_response(evidence)
    set_no_store_headers(response)
    return CitationResolutionResponse(
        evidence_id=evidence_id,
        resolvable=available,
        canonical_url=evidence.canonical_url,
        content_sha256=evidence.content_sha256,
        locator=evidence_response.locator,
        resolved_table_cell=(
            None
            if resolved_cell is None
            else SecFilingTableCellCoordinateResponse.model_validate(
                dict(resolved_cell.to_mapping())
            )
        ),
        failure_reason=failure_reason,
    )


@router.post(
    "/workspaces/{workspace_id}/evidence/{evidence_id}/invalidate",
    response_model=EvidenceResponse,
    responses=_RESPONSES,
)
async def invalidate_evidence(
    workspace_id: UUID,
    evidence_id: UUID,
    payload: InvalidateEvidenceRequest,
    request: Request,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[EvidenceUseCase, Depends(get_evidence_service)],
    if_match: Annotated[str, Header(alias="If-Match", min_length=1, max_length=32)],
) -> EvidenceResponse:
    evidence = await service.invalidate_evidence(
        _workspace_scope(principal, workspace_id),
        InvalidateEvidence(
            evidence_id=evidence_id,
            expected_revision=_parse_if_match(if_match),
            status=payload.status,
            reason=payload.reason,
            trace_id=TraceId(get_trace_id(request)),
        ),
    )
    set_no_store_headers(response)
    _set_revision_header(response, evidence.revision)
    return _evidence_response(evidence)


@router.post(
    "/workspaces/{workspace_id}/research-runs/{research_run_id}/claims",
    response_model=ResearchClaimResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_RESPONSES,
)
async def create_claim(
    workspace_id: UUID,
    research_run_id: UUID,
    payload: CreateClaimRequest,
    request: Request,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[EvidenceUseCase, Depends(get_evidence_service)],
) -> ResearchClaimResponse:
    claim = await service.create_claim(
        _workspace_scope(principal, workspace_id),
        CreateClaim(
            research_run_id=research_run_id,
            statement=payload.statement,
            confidence=payload.confidence,
            relations=tuple(
                ClaimEvidenceInput(
                    evidence_id=item.evidence_id,
                    relation=item.relation,
                )
                for item in payload.relations
            ),
            origin_run_id=payload.origin_run_id,
            origin_step_id=payload.origin_step_id,
            trace_id=TraceId(get_trace_id(request)),
        ),
    )
    set_no_store_headers(response)
    return _claim_response(claim)


@router.get(
    "/workspaces/{workspace_id}/research-runs/{research_run_id}/claims",
    response_model=ResearchClaimCollectionResponse,
    responses=_RESPONSES,
)
async def list_claims(
    workspace_id: UUID,
    research_run_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[EvidenceUseCase, Depends(get_evidence_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ResearchClaimCollectionResponse:
    claims = await service.list_claims(
        _workspace_scope(principal, workspace_id),
        research_run_id,
        limit=limit,
    )
    set_no_store_headers(response)
    return ResearchClaimCollectionResponse(claims=[_claim_response(item) for item in claims])


@router.get(
    "/workspaces/{workspace_id}/research-runs/{research_run_id}/claims/{claim_id}",
    response_model=ResearchClaimResponse,
    responses=_RESPONSES,
)
async def get_claim(
    workspace_id: UUID,
    research_run_id: UUID,
    claim_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[EvidenceUseCase, Depends(get_evidence_service)],
) -> ResearchClaimResponse:
    claim = await service.get_claim(_workspace_scope(principal, workspace_id), claim_id)
    if claim.research_run_id != research_run_id:
        raise ClaimNotFoundError
    set_no_store_headers(response)
    return _claim_response(claim)


@router.get(
    "/workspaces/{workspace_id}/research-runs/{research_run_id}/graph",
    response_model=ClaimGraphResponse,
    responses=_RESPONSES,
)
async def get_claim_graph(
    workspace_id: UUID,
    research_run_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[EvidenceUseCase, Depends(get_evidence_service)],
) -> ClaimGraphResponse:
    graph = await service.get_claim_graph(
        _workspace_scope(principal, workspace_id), research_run_id
    )
    set_no_store_headers(response)
    return _graph_response(graph)


def _parse_if_match(value: str) -> int:
    normalized = value.strip()
    if normalized.startswith('"') and normalized.endswith('"'):
        normalized = normalized[1:-1]
    if not normalized.isascii() or not normalized.isdecimal():
        raise EvidenceRequestRejectedError
    revision = int(normalized)
    if revision < 1:
        raise EvidenceRequestRejectedError
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
    return WorkspaceScope(
        workspace_id=workspace_id,
        user_id=principal.user_id,
        role=workspace.role,
    )


def _evidence_response(evidence: Evidence) -> EvidenceResponse:
    locator = evidence.locator
    locator_response: (
        IndustrySourceLocatorResponse
        | SqlResultLocatorResponse
        | SecFilingChunkLocatorResponse
        | SecFilingTextLocatorResponse
        | SecXbrlFactLocatorResponse
        | FinancialCalculationLocatorResponse
    )
    if isinstance(locator, IndustrySourceLocatorV1):
        locator_response = IndustrySourceLocatorResponse.model_validate(dict(locator.to_mapping()))
    elif isinstance(locator, SqlResultLocatorV1):
        locator_response = SqlResultLocatorResponse.model_validate(dict(locator.to_mapping()))
    elif isinstance(locator, SecFilingChunkLocatorV1):
        locator_response = SecFilingChunkLocatorResponse.model_validate(dict(locator.to_mapping()))
    elif isinstance(locator, SecFilingTextLocatorV1):
        locator_response = SecFilingTextLocatorResponse.model_validate(dict(locator.to_mapping()))
    elif isinstance(locator, SecXbrlFactLocatorV1):
        locator_response = SecXbrlFactLocatorResponse.model_validate(dict(locator.to_mapping()))
    elif isinstance(locator, FinancialCalculationLocatorV1):
        locator_response = FinancialCalculationLocatorResponse.model_validate(
            dict(locator.to_mapping())
        )
    else:
        raise EvidenceRequestRejectedError
    authorization = evidence.authorization_snapshot
    return EvidenceResponse(
        id=evidence.evidence_id,
        workspace_id=evidence.workspace_id,
        kind=evidence.kind,
        title=evidence.title,
        canonical_url=evidence.canonical_url,
        locator=locator_response,
        excerpt=evidence.excerpt,
        content_sha256=evidence.content_sha256,
        source_published_at=evidence.source_published_at,
        retrieved_at=evidence.retrieved_at,
        license_or_terms=evidence.license_or_terms,
        status=evidence.status,
        revision=evidence.revision,
        invalidated_at=evidence.invalidated_at,
        invalidation_reason=evidence.invalidation_reason,
        origin_run_id=evidence.origin_run_id,
        origin_step_id=evidence.origin_step_id,
        origin_tool_call_id=evidence.origin_tool_call_id,
        origin_case_id=evidence.origin_case_id,
        origin_observation_id=evidence.origin_observation_id,
        origin_source_ordinal=evidence.origin_source_ordinal,
        normalizer_version=evidence.normalizer_version,
        authorization_snapshot=AuthorizationSnapshotResponse(
            workspace_id=authorization.workspace_id,
            actor_user_id=authorization.actor_user_id,
            role=authorization.role,
            action=authorization.action,
            captured_at=authorization.captured_at,
        ),
        source_resource_version=evidence.source_resource_version,
        created_at=evidence.created_at,
        updated_at=evidence.updated_at,
    )


def _normalization_response(result: EvidenceNormalizationResult) -> EvidenceNormalizationResponse:
    return EvidenceNormalizationResponse(
        observation_id=result.observation_id,
        tool_call_id=result.tool_call_id,
        normalizer_version=result.normalizer_version,
        items=[
            EvidenceNormalizationItemResponse(
                source_ordinal=item.source_ordinal,
                decision=item.decision,
                reason=item.reason,
                evidence=(None if item.evidence is None else _evidence_response(item.evidence)),
            )
            for item in result.items
        ],
    )


def _claim_response(claim: ResearchClaim) -> ResearchClaimResponse:
    return ResearchClaimResponse(
        id=claim.claim_id,
        workspace_id=claim.workspace_id,
        research_run_id=claim.research_run_id,
        statement=claim.statement,
        confidence=claim.confidence,
        verification_status=claim.verification_status,
        coverage=claim.coverage,
        conflict=claim.conflict,
        revision=claim.revision,
        relations=[
            ClaimEvidenceResponse(
                evidence=_evidence_response(relation.evidence),
                relation=relation.relation,
                relation_version=relation.relation_version,
                status=relation.status,
                ordinal=relation.ordinal,
                origin_run_id=relation.origin_run_id,
                origin_step_id=relation.origin_step_id,
            )
            for relation in claim.relations
        ],
        created_at=claim.created_at,
        updated_at=claim.updated_at,
    )


def _graph_response(graph: ClaimGraph) -> ClaimGraphResponse:
    return ClaimGraphResponse(
        research_run_id=graph.research_run_id,
        nodes=[
            GraphNodeResponse(
                id=node.node_id,
                node_type=node.node_type,
                resource_id=node.resource_id,
                label=node.label,
                status=node.status,
            )
            for node in graph.nodes
        ],
        edges=[
            GraphEdgeResponse(
                id=edge.edge_id,
                source_node_id=edge.source_node_id,
                target_node_id=edge.target_node_id,
                relation=edge.relation,
                status=edge.status,
            )
            for edge in graph.edges
        ],
    )
