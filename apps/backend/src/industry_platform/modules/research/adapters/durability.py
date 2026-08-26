"""PostgreSQL approvals, resume jobs, and Research L4 timeline facts."""

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from industry_platform.core.database import AsyncSessionFactory, safe_sqlstate
from industry_platform.modules.agent_runtime.adapters.persistence import (
    _append_locked_agent_event,
)
from industry_platform.modules.agent_runtime.checkpoints import CheckpointEnvelope
from industry_platform.modules.agent_runtime.domain import AgentRunStatus, RunStopReason
from industry_platform.modules.agent_runtime.events import AgentEvent, AgentEventType
from industry_platform.modules.agent_runtime.models import (
    AgentCheckpointRecord,
    AgentRunRecord,
)
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.jobs.adapters.sqlalchemy import SqlAlchemyJobWriter
from industry_platform.modules.jobs.domain import (
    ExecutionScope,
    PreparedJobSubmission,
)
from industry_platform.modules.research.domain import (
    RESEARCH_QUEUE_NAME,
    RESEARCH_TASK_NAME,
    ResearchApprovalOutcome,
    ResearchApprovalReason,
    ResearchApprovalStatus,
    ResearchNode,
    ResearchSideEffectStatus,
)
from industry_platform.modules.research.durability import (
    DecideResearchApproval,
    ResearchApprovalConflictError,
    ResearchApprovalNotFoundError,
    ResearchApprovalRequest,
    ResearchCheckpointSummary,
    ResearchDurabilityTimeline,
    ResearchResumeReceipt,
    ResearchResumeStateError,
    ResearchResumeTokenError,
    ResumeResearch,
)
from industry_platform.modules.research.models import (
    ResearchApprovalDecisionRecord,
    ResearchApprovalRequestRecord,
    ResearchRunRecord,
    ResearchSideEffectRecord,
)
from industry_platform.modules.research.service import ResearchNotFoundError
from industry_platform.modules.workspaces.domain import WorkspaceScope


class ResearchDurabilityPersistenceError(RuntimeError):
    def __init__(self, *, sqlstate: str | None = None) -> None:
        super().__init__("Research durability persistence is unavailable")
        self.sqlstate = sqlstate


@dataclass(frozen=True, slots=True)
class SqlAlchemyResearchDurabilityRepository:
    session_factory: AsyncSessionFactory

    async def record_completed_effects(
        self,
        scope: WorkspaceScope,
        *,
        run_id: UUID,
        effects: tuple[tuple[str, str, str], ...],
        completed_at: datetime,
    ) -> None:
        if len(effects) != len({(kind, reference) for kind, reference, _digest in effects}):
            raise ValueError("Research side-effect references must be unique")
        try:
            async with self.session_factory.begin() as session:
                research = await _owned_research_for_run(session, scope, run_id)
                if research is None:
                    raise ResearchNotFoundError
                for effect_kind, resource_ref, result_sha256 in effects:
                    key = hashlib.sha256(
                        f"research-effect-v1:{effect_kind}:{resource_ref}".encode()
                    ).digest()
                    await session.execute(
                        insert(ResearchSideEffectRecord)
                        .values(
                            id=uuid4(),
                            workspace_id=scope.workspace_id,
                            run_id=run_id,
                            effect_kind=effect_kind,
                            idempotency_key_hash=key,
                            status=ResearchSideEffectStatus.COMPLETED,
                            resource_ref=resource_ref,
                            result_sha256=result_sha256,
                            completed_at=completed_at,
                            created_at=completed_at,
                            updated_at=completed_at,
                        )
                        .on_conflict_do_nothing(
                            index_elements=(
                                ResearchSideEffectRecord.workspace_id,
                                ResearchSideEffectRecord.effect_kind,
                                ResearchSideEffectRecord.idempotency_key_hash,
                            )
                        )
                    )
        except ResearchNotFoundError:
            raise
        except SQLAlchemyError as error:
            raise ResearchDurabilityPersistenceError(sqlstate=safe_sqlstate(error)) from None

    async def create_approval(
        self,
        scope: WorkspaceScope,
        *,
        checkpoint: CheckpointEnvelope,
        reason: ResearchApprovalReason,
        approval_request_id: UUID,
        resume_token_hash: bytes,
        requested_at: datetime,
        expires_at: datetime,
    ) -> ResearchApprovalRequest:
        try:
            async with self.session_factory.begin() as session:
                research = await _owned_research_for_run(
                    session,
                    scope,
                    checkpoint.run_id,
                    lock=True,
                )
                if research is None:
                    raise ResearchNotFoundError
                existing = await session.scalar(
                    select(ResearchApprovalRequestRecord).where(
                        ResearchApprovalRequestRecord.run_id == checkpoint.run_id,
                        ResearchApprovalRequestRecord.checkpoint_revision == checkpoint.revision,
                    )
                )
                if existing is not None:
                    if (
                        existing.id != approval_request_id
                        or existing.reason is not reason
                        or not hmac.compare_digest(existing.resume_token_hash, resume_token_hash)
                    ):
                        raise ResearchApprovalConflictError
                    return _approval(existing)
                record = ResearchApprovalRequestRecord(
                    id=approval_request_id,
                    workspace_id=scope.workspace_id,
                    run_id=checkpoint.run_id,
                    checkpoint_id=checkpoint.checkpoint_id,
                    checkpoint_revision=checkpoint.revision,
                    reason=reason,
                    status=ResearchApprovalStatus.PENDING,
                    resume_token_hash=resume_token_hash,
                    requested_by_user_id=scope.user_id,
                    expires_at=expires_at,
                    decided_by_user_id=None,
                    decided_at=None,
                    resume_claimed=False,
                    resume_job_id=None,
                    resumed_at=None,
                    created_at=requested_at,
                    updated_at=requested_at,
                )
                session.add(record)
                await session.flush()
                return _approval(record)
        except (ResearchNotFoundError, ResearchApprovalConflictError):
            raise
        except SQLAlchemyError as error:
            raise ResearchDurabilityPersistenceError(sqlstate=safe_sqlstate(error)) from None

    async def decide(
        self,
        scope: WorkspaceScope,
        command: DecideResearchApproval,
        *,
        decided_at: datetime,
        decision_id: UUID,
    ) -> ResearchApprovalRequest:
        try:
            async with self.session_factory.begin() as session:
                request = await _locked_approval(session, scope, command.approval_request_id)
                research = await _owned_research(
                    session,
                    scope,
                    command.research_run_id,
                    lock=True,
                )
                if request is None or research is None or request.run_id != research.agent_run_id:
                    raise ResearchApprovalNotFoundError
                if request.checkpoint_revision != command.checkpoint_revision:
                    raise ResearchApprovalConflictError
                if request.status is not ResearchApprovalStatus.PENDING:
                    expected = _status_for_outcome(command.outcome)
                    if request.status is expected:
                        return _approval(request)
                    raise ResearchApprovalConflictError
                run = await session.scalar(
                    select(AgentRunRecord)
                    .where(
                        AgentRunRecord.id == request.run_id,
                        AgentRunRecord.workspace_id == scope.workspace_id,
                        AgentRunRecord.user_id == scope.user_id,
                    )
                    .with_for_update()
                )
                if run is None or run.status is not AgentRunStatus.PAUSED:
                    raise ResearchResumeStateError
                if decided_at >= request.expires_at:
                    request.status = ResearchApprovalStatus.TIMED_OUT
                    request.decided_at = decided_at
                    request.updated_at = decided_at
                    await _append_approval_decision_events(
                        session,
                        run,
                        request=request,
                        outcome="timeout",
                        occurred_at=decided_at,
                        terminal_reason=RunStopReason.APPROVAL_TIMED_OUT,
                    )
                    return _approval(request)
                request.status = _status_for_outcome(command.outcome)
                request.decided_by_user_id = scope.user_id
                request.decided_at = decided_at
                request.updated_at = decided_at
                session.add(
                    ResearchApprovalDecisionRecord(
                        id=decision_id,
                        workspace_id=scope.workspace_id,
                        approval_request_id=request.id,
                        outcome=command.outcome,
                        decided_by_user_id=scope.user_id,
                        decided_at=decided_at,
                    )
                )
                await _append_approval_decision_events(
                    session,
                    run,
                    request=request,
                    outcome=command.outcome.value,
                    occurred_at=decided_at,
                    terminal_reason=(
                        RunStopReason.APPROVAL_DENIED
                        if command.outcome is ResearchApprovalOutcome.DENY
                        else None
                    ),
                )
                return _approval(request)
        except (
            ResearchApprovalConflictError,
            ResearchApprovalNotFoundError,
            ResearchResumeStateError,
        ):
            raise
        except SQLAlchemyError as error:
            raise ResearchDurabilityPersistenceError(sqlstate=safe_sqlstate(error)) from None

    async def resume(
        self,
        scope: WorkspaceScope,
        command: ResumeResearch,
        *,
        resume_token_hash: bytes,
        resumed_at: datetime,
        job_id: UUID,
        outbox_event_id: UUID,
    ) -> ResearchResumeReceipt:
        try:
            async with self.session_factory.begin() as session:
                request = await _locked_approval(session, scope, command.approval_request_id)
                research = await _owned_research(
                    session,
                    scope,
                    command.research_run_id,
                    lock=True,
                )
                if request is None or research is None or request.run_id != research.agent_run_id:
                    raise ResearchApprovalNotFoundError
                if request.checkpoint_revision != command.checkpoint_revision:
                    raise ResearchApprovalConflictError
                if not hmac.compare_digest(request.resume_token_hash, resume_token_hash):
                    raise ResearchResumeTokenError
                if request.resume_claimed:
                    if request.resume_job_id is None:
                        raise ResearchDurabilityPersistenceError()
                    return ResearchResumeReceipt(
                        run_id=request.run_id,
                        job_id=request.resume_job_id,
                        created=False,
                    )
                run = await session.scalar(
                    select(AgentRunRecord)
                    .where(
                        AgentRunRecord.id == request.run_id,
                        AgentRunRecord.workspace_id == scope.workspace_id,
                        AgentRunRecord.user_id == scope.user_id,
                    )
                    .with_for_update()
                )
                if (
                    request.status is not ResearchApprovalStatus.ALLOWED
                    or resumed_at >= request.expires_at
                    or run is None
                    or run.status is not AgentRunStatus.PAUSED
                    or run.cancel_requested_at is not None
                    or resumed_at >= run.deadline
                    or run.step_count >= run.max_steps
                    or run.input_tokens_used + run.output_tokens_used >= run.max_total_tokens
                    or run.cost_micro_usd >= run.max_cost_micro_usd
                ):
                    raise ResearchResumeStateError
                prepared = PreparedJobSubmission(
                    job_id=job_id,
                    outbox_event_id=outbox_event_id,
                    scope=ExecutionScope(workspace_id=scope.workspace_id),
                    task_name=RESEARCH_TASK_NAME,
                    queue_name=RESEARCH_QUEUE_NAME,
                    payload={"schema_version": 1, "agent_run_id": str(run.id)},
                    available_at=resumed_at,
                    max_attempts=3,
                    priority=0,
                    soft_time_limit_seconds=1_500,
                    hard_time_limit_seconds=1_800,
                    trace_id=TraceId(run.trace_id),
                    idempotency_key_hash=None,
                    request_fingerprint=None,
                    submitted_at=resumed_at,
                )
                submitted = await SqlAlchemyJobWriter(session).submit(prepared)
                run.job_id = submitted.job_id
                run.updated_at = resumed_at
                request.resume_claimed = True
                request.resume_job_id = submitted.job_id
                request.resumed_at = resumed_at
                request.updated_at = resumed_at
                return ResearchResumeReceipt(
                    run_id=run.id,
                    job_id=submitted.job_id,
                    created=submitted.created,
                )
        except (
            ResearchApprovalConflictError,
            ResearchApprovalNotFoundError,
            ResearchResumeStateError,
            ResearchResumeTokenError,
        ):
            raise
        except SQLAlchemyError as error:
            raise ResearchDurabilityPersistenceError(sqlstate=safe_sqlstate(error)) from None

    async def timeline(
        self,
        scope: WorkspaceScope,
        research_run_id: UUID,
    ) -> ResearchDurabilityTimeline:
        try:
            async with self.session_factory() as session:
                research = await _owned_research(session, scope, research_run_id)
                if research is None:
                    raise ResearchNotFoundError
                checkpoints = tuple(
                    await session.scalars(
                        select(AgentCheckpointRecord)
                        .where(
                            AgentCheckpointRecord.run_id == research.agent_run_id,
                            AgentCheckpointRecord.workspace_id == scope.workspace_id,
                        )
                        .order_by(AgentCheckpointRecord.revision)
                    )
                )
                approvals = tuple(
                    await session.scalars(
                        select(ResearchApprovalRequestRecord)
                        .where(
                            ResearchApprovalRequestRecord.run_id == research.agent_run_id,
                            ResearchApprovalRequestRecord.workspace_id == scope.workspace_id,
                        )
                        .order_by(ResearchApprovalRequestRecord.created_at)
                    )
                )
                duplicate_groups = await session.scalar(
                    select(func.count()).select_from(
                        select(
                            ResearchSideEffectRecord.effect_kind,
                            ResearchSideEffectRecord.idempotency_key_hash,
                        )
                        .where(
                            ResearchSideEffectRecord.run_id == research.agent_run_id,
                            ResearchSideEffectRecord.workspace_id == scope.workspace_id,
                        )
                        .group_by(
                            ResearchSideEffectRecord.effect_kind,
                            ResearchSideEffectRecord.idempotency_key_hash,
                        )
                        .having(func.count() > 1)
                        .subquery()
                    )
                )
            return ResearchDurabilityTimeline(
                checkpoints=tuple(_checkpoint(record) for record in checkpoints),
                approvals=tuple(_approval(record) for record in approvals),
                duplicate_side_effect_count=duplicate_groups or 0,
            )
        except ResearchNotFoundError:
            raise
        except (TypeError, ValueError):
            raise ResearchDurabilityPersistenceError() from None
        except SQLAlchemyError as error:
            raise ResearchDurabilityPersistenceError(sqlstate=safe_sqlstate(error)) from None


async def _owned_research(
    session: object,
    scope: WorkspaceScope,
    research_run_id: UUID,
    *,
    lock: bool = False,
) -> ResearchRunRecord | None:
    from sqlalchemy.ext.asyncio import AsyncSession

    if not isinstance(session, AsyncSession):
        raise ResearchDurabilityPersistenceError()
    statement = select(ResearchRunRecord).where(
        ResearchRunRecord.id == research_run_id,
        ResearchRunRecord.workspace_id == scope.workspace_id,
        ResearchRunRecord.owner_user_id == scope.user_id,
    )
    return cast(
        ResearchRunRecord | None,
        await session.scalar(statement.with_for_update() if lock else statement),
    )


async def _owned_research_for_run(
    session: object,
    scope: WorkspaceScope,
    run_id: UUID,
    *,
    lock: bool = False,
) -> ResearchRunRecord | None:
    from sqlalchemy.ext.asyncio import AsyncSession

    if not isinstance(session, AsyncSession):
        raise ResearchDurabilityPersistenceError()
    statement = select(ResearchRunRecord).where(
        ResearchRunRecord.agent_run_id == run_id,
        ResearchRunRecord.workspace_id == scope.workspace_id,
        ResearchRunRecord.owner_user_id == scope.user_id,
    )
    return cast(
        ResearchRunRecord | None,
        await session.scalar(statement.with_for_update() if lock else statement),
    )


async def _locked_approval(
    session: object,
    scope: WorkspaceScope,
    approval_request_id: UUID,
) -> ResearchApprovalRequestRecord | None:
    from sqlalchemy.ext.asyncio import AsyncSession

    if not isinstance(session, AsyncSession):
        raise ResearchDurabilityPersistenceError()
    return cast(
        ResearchApprovalRequestRecord | None,
        await session.scalar(
            select(ResearchApprovalRequestRecord)
            .where(
                ResearchApprovalRequestRecord.id == approval_request_id,
                ResearchApprovalRequestRecord.workspace_id == scope.workspace_id,
            )
            .with_for_update()
        ),
    )


def _status_for_outcome(outcome: ResearchApprovalOutcome) -> ResearchApprovalStatus:
    return {
        ResearchApprovalOutcome.ALLOW: ResearchApprovalStatus.ALLOWED,
        ResearchApprovalOutcome.DENY: ResearchApprovalStatus.DENIED,
    }[outcome]


async def _append_approval_decision_events(
    session: object,
    run: AgentRunRecord,
    *,
    request: ResearchApprovalRequestRecord,
    outcome: str,
    occurred_at: datetime,
    terminal_reason: RunStopReason | None,
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    if not isinstance(session, AsyncSession):
        raise ResearchDurabilityPersistenceError()
    decided = AgentEvent(
        schema_version=run.schema_version,
        stream_id=run.event_stream_id,
        run_id=run.id,
        workspace_id=run.workspace_id,
        sequence=run.event_count + 1,
        occurred_at=occurred_at,
        trace_id=TraceId(run.trace_id),
        event_type=AgentEventType.APPROVAL_DECIDED,
        payload={
            "approval_request_id": str(request.id),
            "checkpoint_revision": request.checkpoint_revision,
            "outcome": outcome,
        },
    )
    await _append_locked_agent_event(session, run, decided)
    if terminal_reason is None:
        return
    terminal = AgentEvent(
        schema_version=run.schema_version,
        stream_id=run.event_stream_id,
        run_id=run.id,
        workspace_id=run.workspace_id,
        sequence=run.event_count + 1,
        occurred_at=occurred_at,
        trace_id=TraceId(run.trace_id),
        event_type=AgentEventType.RUN_FAILED,
        payload={
            "stop_reason": terminal_reason.value,
            "state_revision": run.state_revision + 1,
            "error_code": terminal_reason.value,
        },
    )
    await _append_locked_agent_event(session, run, terminal)


def _approval(record: ResearchApprovalRequestRecord) -> ResearchApprovalRequest:
    return ResearchApprovalRequest(
        approval_request_id=record.id,
        run_id=record.run_id,
        checkpoint_id=record.checkpoint_id,
        checkpoint_revision=record.checkpoint_revision,
        reason=record.reason,
        status=record.status,
        requested_by_user_id=record.requested_by_user_id,
        created_at=record.created_at,
        expires_at=record.expires_at,
        decided_by_user_id=record.decided_by_user_id,
        decided_at=record.decided_at,
        resume_claimed=record.resume_claimed,
        resume_job_id=record.resume_job_id,
        resumed_at=record.resumed_at,
    )


def _checkpoint(record: AgentCheckpointRecord) -> ResearchCheckpointSummary:
    raw_payload = record.state.get("payload")
    raw_state = record.state.get("run_state")
    if not isinstance(raw_payload, dict) or not isinstance(raw_state, dict):
        raise ResearchDurabilityPersistenceError()
    raw_node = raw_payload.get("node")
    raw_next_node = raw_payload.get("next_node")
    graph_state = raw_payload.get("graph_state")
    if not isinstance(raw_node, str) or not isinstance(graph_state, dict):
        raise ResearchDurabilityPersistenceError()
    state_diff = {
        "status": graph_state.get("status"),
        "step_count": graph_state.get("step_count"),
        "evidence_refs": graph_state.get("evidence_refs", []),
        "claim_refs": graph_state.get("claim_refs", []),
        "artifact_refs": graph_state.get("artifact_refs", []),
        "approval_status": graph_state.get("approval_status"),
        "stop_reason": graph_state.get("stop_reason"),
    }
    return ResearchCheckpointSummary(
        checkpoint_id=record.id,
        revision=record.revision,
        run_state_revision=cast(int, raw_state.get("revision")),
        node=ResearchNode(raw_node),
        next_node=None if raw_next_node is None else ResearchNode(cast(str, raw_next_node)),
        saved_at=record.saved_at,
        state_diff=state_diff,
    )
