"""Bearer-authenticated workspace-owner recovery commands."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response

from industry_platform.core.http import get_trace_id, set_no_store_headers
from industry_platform.modules.conversations.schemas import IdempotencyKey
from industry_platform.modules.identity.domain import AuthenticatedPrincipal, TraceId
from industry_platform.modules.identity.http_auth import require_authenticated_principal
from industry_platform.modules.jobs.domain import JobPersistenceError
from industry_platform.modules.jobs.recovery import (
    JobRecoveryConflictError,
    JobRecoveryNotFoundError,
    JobRecoveryService,
    ReplayDeadLetter,
)
from industry_platform.modules.jobs.resources import JobResources, get_job_resources
from industry_platform.modules.jobs.schemas import JobSubmissionResponse, ReplayJobRequest
from industry_platform.modules.workspaces.domain import WorkspaceAccessDeniedError, WorkspaceScope

router = APIRouter(prefix="/workspaces/{workspace_id}/jobs", tags=["jobs"])


def get_recovery_service(
    resources: Annotated[JobResources, Depends(get_job_resources)],
) -> JobRecoveryService:
    return resources.recovery_service


@router.post("/{job_id}/replay", response_model=JobSubmissionResponse, status_code=202)
async def replay_job(
    workspace_id: UUID,
    job_id: UUID,
    payload: ReplayJobRequest,
    request: Request,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    service: Annotated[JobRecoveryService, Depends(get_recovery_service)],
    idempotency_key: Annotated[IdempotencyKey, Header(alias="Idempotency-Key")],
) -> JobSubmissionResponse:
    membership = next(
        (item for item in principal.workspaces if item.workspace_id == workspace_id), None
    )
    if membership is None:
        raise WorkspaceAccessDeniedError
    try:
        receipt = await service.replay(
            WorkspaceScope(workspace_id, principal.user_id, membership.role),
            ReplayDeadLetter(
                job_id=job_id,
                expected_dispatch_generation=payload.expected_dispatch_generation,
                additional_attempts=payload.additional_attempts,
                idempotency_key=idempotency_key,
                trace_id=TraceId(get_trace_id(request)),
            ),
        )
    except JobRecoveryNotFoundError:
        raise HTTPException(status_code=404, detail="Job not found") from None
    except JobRecoveryConflictError:
        raise HTTPException(status_code=409, detail="Job recovery conflict") from None
    except JobPersistenceError:
        raise HTTPException(status_code=503, detail="Job recovery unavailable") from None
    set_no_store_headers(response)
    return JobSubmissionResponse(
        job_id=receipt.job_id,
        outbox_event_id=receipt.outbox_event_id,
        dispatch_generation=receipt.dispatch_generation,
        status=receipt.status,
        created=receipt.created,
    )
