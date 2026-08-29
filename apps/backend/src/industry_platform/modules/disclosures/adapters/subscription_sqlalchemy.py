"""PostgreSQL transaction for approved SEC Monitor subscriptions and read models."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from industry_platform.core.database import AsyncSessionFactory, safe_sqlstate
from industry_platform.modules.agent_runtime.adapters.persistence import (
    _append_locked_agent_event,
)
from industry_platform.modules.agent_runtime.domain import AgentRunStatus, RunStopReason
from industry_platform.modules.agent_runtime.events import AgentEvent, AgentEventType
from industry_platform.modules.agent_runtime.models import AgentRunRecord
from industry_platform.modules.disclosures.models import (
    SecDisclosureCaseEvidenceRecord,
    SecDisclosureCaseRecord,
    SecDisclosureMonitorRecord,
    SecDisclosureMonitorRuleRecord,
    SecDisclosureMonitorWatermarkRecord,
    SecFilerRecord,
)
from industry_platform.modules.disclosures.monitor import (
    SEC_MONITOR_DIFF_VERSION,
    SEC_MONITOR_RULE_SET_VERSION,
    SEC_MONITOR_TASK_NAME,
    SecMonitorRule,
    SecMonitorRuleKind,
    SecMonitorStatus,
)
from industry_platform.modules.disclosures.subscription import (
    ChangeSecMonitorStatus,
    DecideSecMonitorSubscription,
    SecCaseEvidenceLink,
    SecDisclosureCaseView,
    SecMonitorNotFoundError,
    SecMonitorRevisionConflictError,
    SecMonitorSubscriptionDecisionResult,
    SecMonitorSubscriptionError,
    SecMonitorView,
)
from industry_platform.modules.disclosures.tool import (
    SEC_MONITOR_SUBSCRIBE_TOOL_NAME,
    SEC_MONITOR_SUBSCRIBE_TOOL_VERSION,
    SecMonitorSubscribeInput,
)
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.jobs.adapters.sqlalchemy import (
    SqlAlchemyJobWriter,
    SqlAlchemyScheduleWriter,
)
from industry_platform.modules.jobs.domain import (
    ExecutionScope,
    PreparedJobSubmission,
    ScheduleDefinition,
    ScheduleMisfirePolicy,
)
from industry_platform.modules.jobs.models import Schedule
from industry_platform.modules.knowledge.domain import KnowledgeBaseStatus
from industry_platform.modules.knowledge.models import KnowledgeBaseRecord
from industry_platform.modules.research.domain import (
    RESEARCH_QUEUE_NAME,
    RESEARCH_TASK_NAME,
    ResearchApprovalOutcome,
    ResearchApprovalReason,
    ResearchApprovalStatus,
    ResearchSideEffectStatus,
)
from industry_platform.modules.research.durability import (
    ApprovalToolRequest,
    ResearchApprovalConflictError,
    ResearchApprovalNotFoundError,
    ResearchApprovalRequest,
    ResearchResumeStateError,
)
from industry_platform.modules.research.models import (
    ResearchApprovalDecisionRecord,
    ResearchApprovalRequestRecord,
    ResearchRunRecord,
    ResearchSideEffectRecord,
)
from industry_platform.modules.tools.domain import ToolReference
from industry_platform.modules.workspaces.domain import WorkspaceScope


class SecMonitorSubscriptionPersistenceError(SecMonitorSubscriptionError):
    code = "monitor_subscription_persistence_unavailable"

    def __init__(self, *, sqlstate: str | None = None) -> None:
        super().__init__(self.code)
        self.sqlstate = sqlstate


@dataclass(frozen=True, slots=True)
class SqlAlchemySecMonitorSubscriptionRepository:
    session_factory: AsyncSessionFactory

    async def decide(
        self,
        scope: WorkspaceScope,
        command: DecideSecMonitorSubscription,
        *,
        decided_at: datetime,
        decision_id: UUID,
        resume_job_id: UUID,
        resume_outbox_event_id: UUID,
    ) -> SecMonitorSubscriptionDecisionResult:
        try:
            async with self.session_factory.begin() as session:
                approval = await session.scalar(
                    select(ResearchApprovalRequestRecord)
                    .where(
                        ResearchApprovalRequestRecord.id == command.approval_request_id,
                        ResearchApprovalRequestRecord.workspace_id == scope.workspace_id,
                    )
                    .with_for_update()
                )
                research = await session.scalar(
                    select(ResearchRunRecord)
                    .where(
                        ResearchRunRecord.id == command.research_run_id,
                        ResearchRunRecord.workspace_id == scope.workspace_id,
                        ResearchRunRecord.owner_user_id == scope.user_id,
                    )
                    .with_for_update()
                )
                if (
                    approval is None
                    or research is None
                    or approval.run_id != research.agent_run_id
                    or approval.reason is not ResearchApprovalReason.MONITOR_SUBSCRIPTION
                ):
                    raise ResearchApprovalNotFoundError
                if approval.checkpoint_revision != command.checkpoint_revision:
                    raise ResearchApprovalConflictError
                run = await session.scalar(
                    select(AgentRunRecord)
                    .where(
                        AgentRunRecord.id == approval.run_id,
                        AgentRunRecord.workspace_id == scope.workspace_id,
                        AgentRunRecord.user_id == scope.user_id,
                    )
                    .with_for_update()
                )
                if run is None:
                    raise ResearchResumeStateError
                existing_monitor = await session.scalar(
                    select(SecDisclosureMonitorRecord).where(
                        SecDisclosureMonitorRecord.workspace_id == scope.workspace_id,
                        SecDisclosureMonitorRecord.created_from_approval_id == approval.id,
                    )
                )
                if approval.status is not ResearchApprovalStatus.PENDING:
                    if approval.status is ResearchApprovalStatus.TIMED_OUT:
                        return SecMonitorSubscriptionDecisionResult(
                            approval=_approval(approval),
                            monitor=None,
                            resume_job_id=None,
                            created=False,
                        )
                    expected = {
                        ResearchApprovalOutcome.ALLOW: ResearchApprovalStatus.ALLOWED,
                        ResearchApprovalOutcome.DENY: ResearchApprovalStatus.DENIED,
                    }[command.outcome]
                    if approval.status is not expected:
                        raise ResearchApprovalConflictError
                    monitor = (
                        None
                        if existing_monitor is None
                        else await _monitor_view(session, existing_monitor)
                    )
                    if command.outcome is ResearchApprovalOutcome.ALLOW and monitor is None:
                        raise SecMonitorSubscriptionPersistenceError()
                    return SecMonitorSubscriptionDecisionResult(
                        approval=_approval(approval),
                        monitor=monitor,
                        resume_job_id=approval.resume_job_id,
                        created=False,
                    )
                if run.status is not AgentRunStatus.PAUSED or run.cancel_requested_at is not None:
                    raise ResearchResumeStateError
                if decided_at >= approval.expires_at:
                    approval.status = ResearchApprovalStatus.TIMED_OUT
                    approval.decided_at = decided_at
                    approval.updated_at = decided_at
                    await _append_decision_events(
                        session,
                        run,
                        approval,
                        outcome="timeout",
                        occurred_at=decided_at,
                        terminal_reason=RunStopReason.APPROVAL_TIMED_OUT,
                    )
                    return SecMonitorSubscriptionDecisionResult(
                        approval=_approval(approval),
                        monitor=None,
                        resume_job_id=None,
                        created=False,
                    )

                approval.status = {
                    ResearchApprovalOutcome.ALLOW: ResearchApprovalStatus.ALLOWED,
                    ResearchApprovalOutcome.DENY: ResearchApprovalStatus.DENIED,
                }[command.outcome]
                approval.decided_by_user_id = scope.user_id
                approval.decided_at = decided_at
                approval.updated_at = decided_at
                session.add(
                    ResearchApprovalDecisionRecord(
                        id=decision_id,
                        workspace_id=scope.workspace_id,
                        approval_request_id=approval.id,
                        outcome=command.outcome,
                        decided_by_user_id=scope.user_id,
                        decided_at=decided_at,
                    )
                )
                if command.outcome is ResearchApprovalOutcome.DENY:
                    await _append_decision_events(
                        session,
                        run,
                        approval,
                        outcome=command.outcome.value,
                        occurred_at=decided_at,
                        terminal_reason=RunStopReason.APPROVAL_DENIED,
                    )
                    return SecMonitorSubscriptionDecisionResult(
                        approval=_approval(approval),
                        monitor=None,
                        resume_job_id=None,
                        created=False,
                    )
                if (
                    decided_at >= run.deadline
                    or run.step_count >= run.max_steps
                    or run.input_tokens_used + run.output_tokens_used >= run.max_total_tokens
                    or run.cost_micro_usd >= run.max_cost_micro_usd
                ):
                    raise ResearchResumeStateError

                parsed = _subscription_input(approval)
                filer = await session.scalar(
                    select(SecFilerRecord).where(SecFilerRecord.cik == parsed.cik)
                )
                knowledge_base = await session.scalar(
                    select(KnowledgeBaseRecord).where(
                        KnowledgeBaseRecord.id == UUID(parsed.knowledge_base_id),
                        KnowledgeBaseRecord.workspace_id == scope.workspace_id,
                        KnowledgeBaseRecord.status == KnowledgeBaseStatus.ACTIVE,
                    )
                )
                if filer is None or knowledge_base is None:
                    raise ResearchResumeStateError
                monitor_id = uuid5(NAMESPACE_URL, f"sec-monitor-approval:{approval.id}")
                schedule = await SqlAlchemyScheduleWriter(session).ensure_schedule(
                    ScheduleDefinition(
                        scope=ExecutionScope(workspace_id=scope.workspace_id),
                        name=f"sec-monitor-{monitor_id}",
                        task_name=SEC_MONITOR_TASK_NAME,
                        cron_expression=parsed.cron_expression,
                        timezone_name=parsed.timezone_name,
                        payload={"schema_version": 1, "monitor_id": str(monitor_id)},
                        max_attempts=3,
                        soft_time_limit_seconds=300,
                        hard_time_limit_seconds=360,
                        misfire_policy=ScheduleMisfirePolicy.COALESCE_LATEST,
                    )
                )
                monitor_record = SecDisclosureMonitorRecord(
                    id=monitor_id,
                    workspace_id=scope.workspace_id,
                    owner_user_id=scope.user_id,
                    filer_id=filer.id,
                    knowledge_base_id=knowledge_base.id,
                    schedule_id=schedule.schedule_id,
                    allowed_forms=sorted(parsed.allowed_forms),
                    rule_set_version=SEC_MONITOR_RULE_SET_VERSION,
                    diff_version=SEC_MONITOR_DIFF_VERSION,
                    timezone_name=parsed.timezone_name,
                    status=SecMonitorStatus.ACTIVE.value,
                    current_watermark_id=None,
                    created_from_approval_id=approval.id,
                    revision=1,
                    created_at=decided_at,
                    updated_at=decided_at,
                )
                session.add(monitor_record)
                await session.flush()
                for ordinal, rule in enumerate(parsed.rules, start=1):
                    session.add(
                        SecDisclosureMonitorRuleRecord(
                            id=uuid5(monitor_id, f"rule:{ordinal}"),
                            monitor_id=monitor_id,
                            workspace_id=scope.workspace_id,
                            ordinal=ordinal,
                            kind=rule.kind,
                            rule_version=SEC_MONITOR_RULE_SET_VERSION,
                            section_query=rule.section_query,
                            taxonomy=rule.taxonomy,
                            concept=rule.concept,
                            unit=rule.unit,
                            threshold=rule.threshold,
                            comparator=rule.comparator,
                            created_at=decided_at,
                            updated_at=decided_at,
                        )
                    )
                watermark_id = uuid5(monitor_id, "watermark:1")
                session.add(
                    SecDisclosureMonitorWatermarkRecord(
                        id=watermark_id,
                        monitor_id=monitor_id,
                        workspace_id=scope.workspace_id,
                        revision=1,
                        coverage_version=f"sec-monitor-initial-{filer.source_version}"[:128],
                        accepted_at=None,
                        accession=None,
                        monitor_run_id=None,
                        created_at=decided_at,
                    )
                )
                await session.flush()
                monitor_record.current_watermark_id = watermark_id

                effect_key = hashlib.sha256(
                    b"industry-platform:sec-monitor-subscription:v1\x00" + approval.id.bytes
                ).digest()
                resource_ref = f"sec-monitor:{monitor_id}"
                session.add(
                    ResearchSideEffectRecord(
                        id=uuid5(approval.id, "monitor-side-effect-v1"),
                        workspace_id=scope.workspace_id,
                        run_id=run.id,
                        effect_kind="monitor_subscription",
                        idempotency_key_hash=effect_key,
                        status=ResearchSideEffectStatus.COMPLETED,
                        resource_ref=resource_ref,
                        result_sha256=hashlib.sha256(resource_ref.encode("ascii")).hexdigest(),
                        completed_at=decided_at,
                        created_at=decided_at,
                        updated_at=decided_at,
                    )
                )
                submitted = await SqlAlchemyJobWriter(session).submit(
                    PreparedJobSubmission(
                        job_id=resume_job_id,
                        outbox_event_id=resume_outbox_event_id,
                        scope=ExecutionScope(workspace_id=scope.workspace_id),
                        task_name=RESEARCH_TASK_NAME,
                        queue_name=RESEARCH_QUEUE_NAME,
                        payload={"schema_version": 1, "agent_run_id": str(run.id)},
                        available_at=decided_at,
                        max_attempts=3,
                        priority=0,
                        soft_time_limit_seconds=1_500,
                        hard_time_limit_seconds=1_800,
                        trace_id=TraceId(run.trace_id),
                        idempotency_key_hash=None,
                        request_fingerprint=None,
                        submitted_at=decided_at,
                    )
                )
                run.job_id = submitted.job_id
                run.updated_at = decided_at
                approval.resume_claimed = True
                approval.resume_job_id = submitted.job_id
                approval.resumed_at = decided_at
                await _append_decision_events(
                    session,
                    run,
                    approval,
                    outcome=command.outcome.value,
                    occurred_at=decided_at,
                    terminal_reason=None,
                )
                await session.flush()
                await session.refresh(monitor_record)
                return SecMonitorSubscriptionDecisionResult(
                    approval=_approval(approval),
                    monitor=await _monitor_view(session, monitor_record),
                    resume_job_id=submitted.job_id,
                    created=True,
                )
        except (
            ResearchApprovalConflictError,
            ResearchApprovalNotFoundError,
            ResearchResumeStateError,
            SecMonitorSubscriptionError,
        ):
            raise
        except (TypeError, ValueError):
            raise ResearchResumeStateError from None
        except SQLAlchemyError as error:
            raise SecMonitorSubscriptionPersistenceError(sqlstate=safe_sqlstate(error)) from None

    async def list_monitors(self, scope: WorkspaceScope) -> tuple[SecMonitorView, ...]:
        try:
            async with self.session_factory() as session:
                records = tuple(
                    await session.scalars(
                        select(SecDisclosureMonitorRecord)
                        .where(SecDisclosureMonitorRecord.workspace_id == scope.workspace_id)
                        .order_by(SecDisclosureMonitorRecord.updated_at.desc())
                    )
                )
                return tuple([await _monitor_view(session, record) for record in records])
        except SQLAlchemyError as error:
            raise SecMonitorSubscriptionPersistenceError(sqlstate=safe_sqlstate(error)) from None

    async def get_monitor(self, scope: WorkspaceScope, monitor_id: UUID) -> SecMonitorView:
        try:
            async with self.session_factory() as session:
                record = await _owned_monitor(session, scope, monitor_id)
                if record is None:
                    raise SecMonitorNotFoundError
                return await _monitor_view(session, record)
        except SecMonitorNotFoundError:
            raise
        except SQLAlchemyError as error:
            raise SecMonitorSubscriptionPersistenceError(sqlstate=safe_sqlstate(error)) from None

    async def change_status(
        self,
        scope: WorkspaceScope,
        command: ChangeSecMonitorStatus,
        *,
        changed_at: datetime,
    ) -> SecMonitorView:
        try:
            async with self.session_factory.begin() as session:
                record = await _owned_monitor(session, scope, command.monitor_id, lock=True)
                if record is None:
                    raise SecMonitorNotFoundError
                if record.revision != command.expected_revision:
                    raise SecMonitorRevisionConflictError
                if (
                    record.status == SecMonitorStatus.DELETED.value
                    and command.status is not SecMonitorStatus.DELETED
                ):
                    raise SecMonitorRevisionConflictError
                schedule = await session.scalar(
                    select(Schedule).where(Schedule.id == record.schedule_id).with_for_update()
                )
                if schedule is None:
                    raise SecMonitorSubscriptionPersistenceError()
                if record.status != command.status.value:
                    record.status = command.status.value
                    record.revision += 1
                    record.updated_at = changed_at
                    schedule.enabled = command.status is SecMonitorStatus.ACTIVE
                    schedule.next_due_at = (
                        ScheduleDefinition(
                            scope=ExecutionScope(workspace_id=scope.workspace_id),
                            name=schedule.name,
                            task_name=schedule.task_name,
                            cron_expression=schedule.cron_expression,
                            timezone_name=schedule.timezone_name,
                            payload=schedule.payload,
                            queue_name=schedule.queue_name,
                            max_attempts=schedule.max_attempts,
                            priority=schedule.priority,
                            soft_time_limit_seconds=schedule.soft_time_limit_seconds,
                            hard_time_limit_seconds=schedule.hard_time_limit_seconds,
                            misfire_policy=schedule.misfire_policy,
                            catch_up_window_seconds=schedule.catch_up_window_seconds,
                            max_catch_up=schedule.max_catch_up,
                        ).next_after(changed_at)
                        if schedule.enabled
                        else None
                    )
                    schedule.updated_at = changed_at
                await session.flush()
                await session.refresh(record)
                return await _monitor_view(session, record)
        except (SecMonitorNotFoundError, SecMonitorRevisionConflictError):
            raise
        except SQLAlchemyError as error:
            raise SecMonitorSubscriptionPersistenceError(sqlstate=safe_sqlstate(error)) from None

    async def list_cases(
        self,
        scope: WorkspaceScope,
        *,
        monitor_id: UUID | None,
    ) -> tuple[SecDisclosureCaseView, ...]:
        try:
            async with self.session_factory() as session:
                statement = select(SecDisclosureCaseRecord).where(
                    SecDisclosureCaseRecord.workspace_id == scope.workspace_id
                )
                if monitor_id is not None:
                    statement = statement.where(SecDisclosureCaseRecord.monitor_id == monitor_id)
                records = tuple(
                    await session.scalars(
                        statement.order_by(SecDisclosureCaseRecord.created_at.desc())
                    )
                )
                return tuple([await _case_view(session, record) for record in records])
        except SQLAlchemyError as error:
            raise SecMonitorSubscriptionPersistenceError(sqlstate=safe_sqlstate(error)) from None

    async def get_case(self, scope: WorkspaceScope, case_id: UUID) -> SecDisclosureCaseView:
        try:
            async with self.session_factory() as session:
                record = await session.scalar(
                    select(SecDisclosureCaseRecord).where(
                        SecDisclosureCaseRecord.id == case_id,
                        SecDisclosureCaseRecord.workspace_id == scope.workspace_id,
                    )
                )
                if record is None:
                    raise SecMonitorNotFoundError
                return await _case_view(session, record)
        except SecMonitorNotFoundError:
            raise
        except SQLAlchemyError as error:
            raise SecMonitorSubscriptionPersistenceError(sqlstate=safe_sqlstate(error)) from None


def _subscription_input(record: ResearchApprovalRequestRecord) -> SecMonitorSubscribeInput:
    if (
        record.tool_name != SEC_MONITOR_SUBSCRIBE_TOOL_NAME
        or record.tool_version != SEC_MONITOR_SUBSCRIBE_TOOL_VERSION
        or record.tool_arguments is None
    ):
        raise ResearchApprovalConflictError
    return SecMonitorSubscribeInput.model_validate(record.tool_arguments)


async def _owned_monitor(
    session: object,
    scope: WorkspaceScope,
    monitor_id: UUID,
    *,
    lock: bool = False,
) -> SecDisclosureMonitorRecord | None:
    from sqlalchemy.ext.asyncio import AsyncSession

    if not isinstance(session, AsyncSession):
        raise SecMonitorSubscriptionPersistenceError()
    statement = select(SecDisclosureMonitorRecord).where(
        SecDisclosureMonitorRecord.id == monitor_id,
        SecDisclosureMonitorRecord.workspace_id == scope.workspace_id,
    )
    return cast(
        SecDisclosureMonitorRecord | None,
        await session.scalar(statement.with_for_update() if lock else statement),
    )


async def _monitor_view(session: object, record: SecDisclosureMonitorRecord) -> SecMonitorView:
    from sqlalchemy.ext.asyncio import AsyncSession

    if not isinstance(session, AsyncSession) or record.current_watermark_id is None:
        raise SecMonitorSubscriptionPersistenceError()
    filer = await session.get(SecFilerRecord, record.filer_id)
    schedule = await session.get(Schedule, record.schedule_id)
    watermark = await session.get(SecDisclosureMonitorWatermarkRecord, record.current_watermark_id)
    rules = tuple(
        await session.scalars(
            select(SecDisclosureMonitorRuleRecord)
            .where(SecDisclosureMonitorRuleRecord.monitor_id == record.id)
            .order_by(SecDisclosureMonitorRuleRecord.ordinal)
        )
    )
    if filer is None or schedule is None or watermark is None:
        raise SecMonitorSubscriptionPersistenceError()
    return SecMonitorView(
        monitor_id=record.id,
        workspace_id=record.workspace_id,
        owner_user_id=record.owner_user_id,
        cik=filer.cik,
        canonical_name=filer.canonical_name,
        knowledge_base_id=record.knowledge_base_id,
        schedule_id=record.schedule_id,
        cron_expression=schedule.cron_expression,
        timezone_name=record.timezone_name,
        allowed_forms=tuple(record.allowed_forms),
        rules=tuple(
            SecMonitorRule(
                rule_id=rule.id,
                kind=SecMonitorRuleKind(rule.kind),
                rule_version=rule.rule_version,
                section_query=rule.section_query,
                taxonomy=rule.taxonomy,
                concept=rule.concept,
                unit=rule.unit,
                threshold=rule.threshold,
                comparator=rule.comparator,
            )
            for rule in rules
        ),
        status=SecMonitorStatus(record.status),
        revision=record.revision,
        watermark_revision=watermark.revision,
        watermark_coverage_version=watermark.coverage_version,
        watermark_accepted_at=watermark.accepted_at,
        watermark_accession=watermark.accession,
        created_from_approval_id=record.created_from_approval_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


async def _case_view(session: object, record: SecDisclosureCaseRecord) -> SecDisclosureCaseView:
    from sqlalchemy.ext.asyncio import AsyncSession

    if not isinstance(session, AsyncSession):
        raise SecMonitorSubscriptionPersistenceError()
    evidence = tuple(
        await session.scalars(
            select(SecDisclosureCaseEvidenceRecord)
            .where(SecDisclosureCaseEvidenceRecord.case_id == record.id)
            .order_by(SecDisclosureCaseEvidenceRecord.side)
        )
    )
    return SecDisclosureCaseView(
        case_id=record.id,
        monitor_id=record.monitor_id,
        monitor_run_id=record.monitor_run_id,
        rule_id=record.rule_id,
        trigger_kind=record.trigger_kind,
        source_coverage_version=record.source_coverage_version,
        baseline_accession=record.baseline_accession,
        target_accession=record.target_accession,
        diff_version=record.diff_version,
        diff_payload=dict(record.diff_payload),
        diff_sha256=record.diff_sha256.hex(),
        verification_status=record.verification_status,
        notification_status=record.notification_status,
        evidence=tuple(
            SecCaseEvidenceLink(side=item.side, evidence_id=item.evidence_id) for item in evidence
        ),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _approval(record: ResearchApprovalRequestRecord) -> ResearchApprovalRequest:
    tool_request = None
    if record.tool_call_id is not None:
        if (
            record.tool_name is None
            or record.tool_version is None
            or record.tool_arguments is None
            or record.tool_arguments_sha256 is None
        ):
            raise SecMonitorSubscriptionPersistenceError()
        tool_request = ApprovalToolRequest(
            call_id=record.tool_call_id,
            tool=ToolReference(record.tool_name, record.tool_version),
            arguments=record.tool_arguments,
            arguments_sha256=record.tool_arguments_sha256,
        )
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
        tool_request=tool_request,
    )


async def _append_decision_events(
    session: object,
    run: AgentRunRecord,
    request: ResearchApprovalRequestRecord,
    *,
    outcome: str,
    occurred_at: datetime,
    terminal_reason: RunStopReason | None,
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    if not isinstance(session, AsyncSession):
        raise SecMonitorSubscriptionPersistenceError()
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
    await _append_locked_agent_event(
        session,
        run,
        AgentEvent(
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
        ),
    )
