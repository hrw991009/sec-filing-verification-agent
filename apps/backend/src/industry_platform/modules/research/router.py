"""Authenticated HTTP delivery for Research creation, inspection, and L4 recovery."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from industry_platform.core.http import get_trace_id, problem_openapi_response, set_no_store_headers
from industry_platform.modules.conversations.domain import TurnSearchMode
from industry_platform.modules.conversations.schemas import IdempotencyKey
from industry_platform.modules.financial_verification.schemas import FinancialScopePayload
from industry_platform.modules.identity.domain import AuthenticatedPrincipal, TraceId
from industry_platform.modules.identity.http_auth import require_authenticated_principal
from industry_platform.modules.research.domain import (
    ResearchApprovalStatus,
    ResearchBriefInput,
    ResearchRunView,
)
from industry_platform.modules.research.durability import (
    DecideResearchApproval,
    ResearchApprovalRequest,
    ResearchDurabilityService,
    ResumeResearch,
)
from industry_platform.modules.research.resources import ResearchResources, get_research_resources
from industry_platform.modules.research.schemas import (
    DecideResearchApprovalRequest,
    ResearchApprovalResponse,
    ResearchBriefResponse,
    ResearchBudgetResponse,
    ResearchCheckpointResponse,
    ResearchDraftResponse,
    ResearchDurabilityTimelineResponse,
    ResearchPlanActionResponse,
    ResearchPlanResponse,
    ResearchRunCollectionResponse,
    ResearchRunDetailResponse,
    ResumeResearchRequest,
    ResumeResearchResponse,
    StartResearchRequest,
    StartResearchResponse,
)
from industry_platform.modules.research.service import (
    ResearchQueryService,
    ResearchSubmissionService,
    StartResearch,
)
from industry_platform.modules.workspaces.domain import WorkspaceAccessDeniedError, WorkspaceScope

router = APIRouter(prefix="/workspaces/{workspace_id}/research-runs", tags=["research"])
type OpenApiResponses = dict[int | str, dict[str, Any]]

_RESPONSES: OpenApiResponses = {
    status.HTTP_401_UNAUTHORIZED: problem_openapi_response("Invalid authenticated session"),
    status.HTTP_403_FORBIDDEN: problem_openapi_response("Workspace access denied"),
    status.HTTP_404_NOT_FOUND: problem_openapi_response("Research Run not found"),
    status.HTTP_409_CONFLICT: problem_openapi_response("Research request conflict"),
    status.HTTP_422_UNPROCESSABLE_CONTENT: problem_openapi_response("Research request rejected"),
    status.HTTP_500_INTERNAL_SERVER_ERROR: problem_openapi_response("Internal server error"),
    status.HTTP_503_SERVICE_UNAVAILABLE: problem_openapi_response(
        "Research service temporarily unavailable"
    ),
}


def get_submission_service(
    resources: Annotated[ResearchResources, Depends(get_research_resources)],
) -> ResearchSubmissionService:
    return resources.submission_service


def get_query_service(
    resources: Annotated[ResearchResources, Depends(get_research_resources)],
) -> ResearchQueryService:
    return resources.query_service


def get_durability_service(
    resources: Annotated[ResearchResources, Depends(get_research_resources)],
) -> ResearchDurabilityService:
    return resources.durability_service


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=StartResearchResponse,
    responses=_RESPONSES,
)
async def start_research(
    workspace_id: UUID,
    payload: StartResearchRequest,
    request: Request,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[ResearchSubmissionService, Depends(get_submission_service)],
    idempotency_key: Annotated[IdempotencyKey, Header(alias="Idempotency-Key")],
) -> StartResearchResponse:
    receipt = await service.start(
        _workspace_scope(principal, workspace_id),
        StartResearch(
            trace_id=TraceId(get_trace_id(request)),
            industry_id=payload.industry_id,
            search_mode=TurnSearchMode(payload.mode),
            knowledge_base_ids=tuple(payload.knowledge_base_ids),
            brief=ResearchBriefInput(
                original_question=payload.original_question,
                confirmed_scope=tuple(payload.confirmed_scope),
                exclusions=tuple(payload.exclusions),
                completion_criteria=tuple(payload.completion_criteria),
                financial_scope=(
                    None if payload.financial_scope is None else payload.financial_scope.to_domain()
                ),
                approval_reason=payload.approval_reason,
            ),
            idempotency_key=idempotency_key,
            max_steps=payload.max_steps,
            max_total_tokens=payload.max_total_tokens,
            max_cost_micro_usd=payload.max_cost_micro_usd,
            timeout_seconds=payload.timeout_seconds,
        ),
    )
    set_no_store_headers(response)
    return StartResearchResponse(
        research_run_id=receipt.research_run_id,
        agent_run_id=receipt.agent_run_id,
        conversation_id=receipt.conversation_id,
        turn_id=receipt.turn_id,
        job_id=receipt.job_id,
        created=receipt.created,
    )


@router.get("", response_model=ResearchRunCollectionResponse, responses=_RESPONSES)
async def list_research(
    workspace_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[ResearchQueryService, Depends(get_query_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ResearchRunCollectionResponse:
    views = await service.list(_workspace_scope(principal, workspace_id), limit=limit)
    set_no_store_headers(response)
    return ResearchRunCollectionResponse(research_runs=[_view_response(view) for view in views])


@router.get("/{research_run_id}", response_model=ResearchRunDetailResponse, responses=_RESPONSES)
async def get_research(
    workspace_id: UUID,
    research_run_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[ResearchQueryService, Depends(get_query_service)],
) -> ResearchRunDetailResponse:
    view = await service.get(_workspace_scope(principal, workspace_id), research_run_id)
    set_no_store_headers(response)
    return _view_response(view)


@router.get(
    "/{research_run_id}/durability",
    response_model=ResearchDurabilityTimelineResponse,
    responses=_RESPONSES,
)
async def get_research_durability(
    workspace_id: UUID,
    research_run_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[ResearchDurabilityService, Depends(get_durability_service)],
) -> ResearchDurabilityTimelineResponse:
    timeline = await service.timeline(_workspace_scope(principal, workspace_id), research_run_id)
    set_no_store_headers(response)
    return ResearchDurabilityTimelineResponse(
        checkpoints=[
            ResearchCheckpointResponse(
                checkpoint_id=item.checkpoint_id,
                revision=item.revision,
                run_state_revision=item.run_state_revision,
                node=item.node,
                next_node=item.next_node,
                saved_at=item.saved_at,
                state_diff=dict(item.state_diff),
            )
            for item in timeline.checkpoints
        ],
        approvals=[_approval_response(service, item) for item in timeline.approvals],
        duplicate_side_effect_count=timeline.duplicate_side_effect_count,
    )


@router.post(
    "/{research_run_id}/approval-decisions",
    response_model=ResearchApprovalResponse,
    responses=_RESPONSES,
)
async def decide_research_approval(
    workspace_id: UUID,
    research_run_id: UUID,
    payload: DecideResearchApprovalRequest,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[ResearchDurabilityService, Depends(get_durability_service)],
) -> ResearchApprovalResponse:
    approval = await service.decide(
        _workspace_scope(principal, workspace_id),
        DecideResearchApproval(
            research_run_id=research_run_id,
            approval_request_id=payload.approval_request_id,
            checkpoint_revision=payload.checkpoint_revision,
            outcome=payload.outcome,
        ),
    )
    set_no_store_headers(response)
    return _approval_response(service, approval)


@router.post(
    "/{research_run_id}/resume",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ResumeResearchResponse,
    responses=_RESPONSES,
)
async def resume_research(
    workspace_id: UUID,
    research_run_id: UUID,
    payload: ResumeResearchRequest,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[ResearchDurabilityService, Depends(get_durability_service)],
) -> ResumeResearchResponse:
    receipt = await service.resume(
        _workspace_scope(principal, workspace_id),
        ResumeResearch(
            research_run_id=research_run_id,
            approval_request_id=payload.approval_request_id,
            checkpoint_revision=payload.checkpoint_revision,
            resume_token=payload.resume_token,
        ),
    )
    set_no_store_headers(response)
    return ResumeResearchResponse(
        agent_run_id=receipt.run_id,
        job_id=receipt.job_id,
        created=receipt.created,
    )


def _view_response(view: ResearchRunView) -> ResearchRunDetailResponse:
    brief = view.brief
    plan = view.plan
    draft = view.draft
    return ResearchRunDetailResponse(
        id=view.research_run.research_run_id,
        workspace_id=view.research_run.workspace_id,
        owner_user_id=view.research_run.owner_user_id,
        agent_run_id=view.research_run.agent_run_id,
        status=view.research_run.status,
        revision=view.research_run.revision,
        graph_version=view.research_run.graph_version,
        state_schema_version=view.research_run.state_schema_version,
        current_node=view.research_run.current_node,
        agent_status=view.agent_status,
        stop_reason=view.stop_reason,
        step_count=view.step_count,
        event_count=view.event_count,
        input_tokens_used=view.input_tokens_used,
        output_tokens_used=view.output_tokens_used,
        cost_micro_usd=view.cost_micro_usd,
        brief=ResearchBriefResponse(
            id=brief.brief_id,
            revision=brief.revision,
            original_question=brief.input.original_question,
            confirmed_scope=list(brief.input.confirmed_scope),
            exclusions=list(brief.input.exclusions),
            completion_criteria=list(brief.input.completion_criteria),
            financial_scope=(
                None
                if brief.input.financial_scope is None
                else FinancialScopePayload.from_domain(brief.input.financial_scope)
            ),
            approval_reason=brief.input.approval_reason,
            budget=ResearchBudgetResponse(
                max_steps=brief.budget.max_steps,
                max_total_tokens=brief.budget.max_total_tokens,
                max_cost_micro_usd=brief.budget.max_cost_micro_usd,
                deadline=brief.budget.deadline,
            ),
            confirmed_by_user_id=brief.confirmed_by_user_id,
            confirmed_at=brief.confirmed_at,
        ),
        plan=(
            None
            if plan is None
            else ResearchPlanResponse(
                id=plan.plan_id,
                brief_revision=plan.brief_revision,
                revision=plan.revision,
                actions=[
                    ResearchPlanActionResponse(
                        ordinal=action.ordinal,
                        objective=action.objective,
                        allowed_tool_names=list(action.allowed_tool_names),
                    )
                    for action in plan.actions
                ],
                planner_summary=plan.planner_summary,
                created_at=plan.created_at,
            )
        ),
        draft=(
            None
            if draft is None
            else ResearchDraftResponse(
                id=draft.draft_id,
                status=draft.status,
                content_markdown=draft.content_markdown,
                outline=list(draft.outline),
                evidence_refs=list(draft.evidence_refs),
                claim_refs=list(draft.claim_refs),
                uncertainty_summary=draft.uncertainty_summary,
                created_at=draft.created_at,
                updated_at=draft.updated_at,
            )
        ),
        created_at=view.research_run.created_at,
        updated_at=view.research_run.updated_at,
    )


def _approval_response(
    service: ResearchDurabilityService,
    approval: ResearchApprovalRequest,
) -> ResearchApprovalResponse:
    token = (
        service.token_for(approval)
        if approval.status in {ResearchApprovalStatus.PENDING, ResearchApprovalStatus.ALLOWED}
        and not approval.resume_claimed
        else None
    )
    return ResearchApprovalResponse(
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
        resume_token=token,
    )


def _workspace_scope(principal: AuthenticatedPrincipal, workspace_id: UUID) -> WorkspaceScope:
    membership = next(
        (item for item in principal.workspaces if item.workspace_id == workspace_id),
        None,
    )
    if membership is None:
        raise WorkspaceAccessDeniedError
    return WorkspaceScope(
        workspace_id=workspace_id,
        user_id=principal.user_id,
        role=membership.role,
    )
