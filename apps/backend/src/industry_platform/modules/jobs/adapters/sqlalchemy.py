"""PostgreSQL implementation of atomic job and fenced-worker mutations."""

import hmac
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, exists, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from industry_platform.core.database import AsyncSessionFactory, safe_sqlstate
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.jobs.domain import (
    JOB_DISPATCH_OUTBOX_EVENT_TYPE,
    JOB_DISPATCH_OUTBOX_TOPIC,
    AcquiredJob,
    AcquireJobCommand,
    CheckpointJobCommand,
    ClaimedJobDispatch,
    ClaimOutboxCommand,
    EnsuredSchedule,
    ExecutionScope,
    FinishJobCommand,
    HeartbeatJobCommand,
    JobDefinition,
    JobDispatchMessage,
    JobEventType,
    JobExecutionErrorCode,
    JobIdempotencyConflictError,
    JobLease,
    JobLeaseProof,
    JobPersistenceError,
    JobReconciliationResult,
    JobRetryDisposition,
    JobRetryRecord,
    JobStatus,
    JobSubmissionRecord,
    ManualScheduleTriggerCommand,
    ManualScheduleTriggerResult,
    OutboxClaimProof,
    OutboxFailureDisposition,
    OutboxPersistenceError,
    OutboxPublishErrorCode,
    OutboxStatus,
    PreparedJobSubmission,
    ReconcileJobsCommand,
    RetryJobCommand,
    ScheduleDefinition,
    ScheduleDefinitionConflictError,
    ScheduleNotFoundError,
    ScheduleOccurrenceMaterialization,
    ScheduleOccurrenceStatus,
    ScheduleTickCommand,
    ScheduleTickResult,
    ScheduleTriggerConflictError,
    ScheduleTriggerKind,
    fingerprint_job_request,
    hash_job_idempotency_key,
    job_retry_delay_seconds,
    plan_due_schedule,
)
from industry_platform.modules.jobs.models import (
    Job,
    JobEvent,
    OutboxEvent,
    Schedule,
    ScheduleOccurrence,
)
from industry_platform.modules.jobs.ports import (
    JobWriter,
    OutboxWriter,
    ScheduleWriter,
)

DISPATCH_GENERATION = 1
CREATED_EVENT_SEQUENCE = 0
STARTED_EVENT_SEQUENCE = 0


class ScheduleOccurrenceObserver(Protocol):
    """Optional same-transaction projection for statically registered task families."""

    async def __call__(
        self,
        session: AsyncSession,
        materialization: ScheduleOccurrenceMaterialization,
    ) -> None: ...


async def ignore_schedule_occurrence(
    session: AsyncSession,
    materialization: ScheduleOccurrenceMaterialization,
) -> None:
    """Default no-op used when no task family owns an extension projection."""

    del session, materialization


def compose_schedule_occurrence_observers(
    *observers: ScheduleOccurrenceObserver,
) -> ScheduleOccurrenceObserver:
    """Run each registered projection inside the materialization transaction."""

    async def observe(
        session: AsyncSession,
        materialization: ScheduleOccurrenceMaterialization,
    ) -> None:
        for observer in observers:
            await observer(session, materialization)

    return observe


@dataclass(slots=True)
class SqlAlchemyJobWriter:
    """Perform every job transition through one explicit database session."""

    session: AsyncSession

    async def submit(self, prepared: PreparedJobSubmission) -> JobSubmissionRecord:
        values = self._job_values(prepared)
        statement = insert(Job).values(**values)

        if prepared.idempotency_key_hash is not None:
            if prepared.scope.workspace_id is not None:
                statement = statement.on_conflict_do_nothing(
                    index_elements=(
                        Job.workspace_id,
                        Job.task_name,
                        Job.idempotency_key_hash,
                    ),
                    index_where=text(
                        "workspace_id IS NOT NULL AND idempotency_key_hash IS NOT NULL"
                    ),
                )
            else:
                statement = statement.on_conflict_do_nothing(
                    index_elements=(
                        Job.system_scope_key,
                        Job.task_name,
                        Job.idempotency_key_hash,
                    ),
                    index_where=text(
                        "system_scope_key IS NOT NULL AND idempotency_key_hash IS NOT NULL"
                    ),
                )

        inserted_job_id = await self.session.scalar(statement.returning(Job.id))
        if inserted_job_id is None:
            return await self._reuse_idempotent_submission(prepared)

        self.session.add(
            JobEvent(
                id=uuid4(),
                job_id=inserted_job_id,
                event_type=JobEventType.CREATED,
                generation=0,
                dispatch_generation=DISPATCH_GENERATION,
                fencing_token=0,
                event_sequence=CREATED_EVENT_SEQUENCE,
                occurred_at=prepared.submitted_at,
                details={},
            )
        )
        self.session.add(
            OutboxEvent(
                id=prepared.outbox_event_id,
                workspace_id=prepared.scope.workspace_id,
                system_scope_key=prepared.scope.system_scope_key,
                source_job_id=inserted_job_id,
                job_dispatch_generation=DISPATCH_GENERATION,
                topic="jobs.dispatch",
                event_type="job.dispatch.requested",
                payload={
                    "job_id": str(inserted_job_id),
                    "outbox_id": str(prepared.outbox_event_id),
                    "dispatch_generation": DISPATCH_GENERATION,
                    "trace_id": str(prepared.trace_id),
                },
                deduplication_key=(f"job:{inserted_job_id}:dispatch:{DISPATCH_GENERATION}"),
                status=OutboxStatus.PENDING,
                next_attempt_at=prepared.available_at,
            )
        )
        return JobSubmissionRecord(
            job_id=inserted_job_id,
            outbox_event_id=prepared.outbox_event_id,
            status=JobStatus.PENDING,
            dispatch_generation=DISPATCH_GENERATION,
            created=True,
        )

    async def acquire(
        self,
        command: AcquireJobCommand,
        *,
        lease_token: UUID,
        lease_seconds: int,
    ) -> AcquiredJob | None:
        database_now = func.clock_timestamp()
        database_expiry = database_now + timedelta(seconds=lease_seconds)
        delivery_predicates: tuple[ColumnElement[bool], ...] = ()
        if command.outbox_id is not None and command.trace_id is not None:
            delivery_predicates = (
                Job.trace_id == command.trace_id,
                exists(
                    select(OutboxEvent.id).where(
                        OutboxEvent.id == command.outbox_id,
                        OutboxEvent.source_job_id == Job.id,
                        OutboxEvent.job_dispatch_generation == command.dispatch_generation,
                        OutboxEvent.topic == JOB_DISPATCH_OUTBOX_TOPIC,
                        OutboxEvent.event_type == JOB_DISPATCH_OUTBOX_EVENT_TYPE,
                        OutboxEvent.status.in_((OutboxStatus.PUBLISHING, OutboxStatus.PUBLISHED)),
                    )
                ),
            )

        statement = (
            update(Job)
            .where(
                Job.id == command.job_id,
                Job.dispatch_generation == command.dispatch_generation,
                Job.status.in_((JobStatus.PENDING, JobStatus.DISPATCHED, JobStatus.RETRY_WAIT)),
                Job.available_at <= database_now,
                Job.cancel_requested_at.is_(None),
                Job.attempt_count < Job.max_attempts,
                *delivery_predicates,
            )
            .values(
                status=JobStatus.RUNNING,
                attempt_count=Job.attempt_count + 1,
                generation=Job.generation + 1,
                fencing_token=Job.fencing_token + 1,
                dispatch_attempt=case(
                    (Job.dispatch_attempt == 0, 1),
                    else_=Job.dispatch_attempt,
                ),
                dispatched_at=func.coalesce(Job.dispatched_at, database_now),
                started_at=database_now,
                lease_owner=command.worker_id,
                lease_token=lease_token,
                lease_expires_at=database_expiry,
                heartbeat_at=database_now,
                stage_name="running",
                stage_sequence=Job.stage_sequence + 1,
                updated_at=database_now,
            )
            .returning(Job)
        )
        job = (await self.session.scalars(statement)).one_or_none()
        if job is None:
            return None
        heartbeat_at = job.heartbeat_at
        lease_expires_at = job.lease_expires_at
        if heartbeat_at is None or lease_expires_at is None:
            raise JobPersistenceError

        self.session.add(
            JobEvent(
                id=uuid4(),
                job_id=job.id,
                event_type=JobEventType.STARTED,
                generation=job.generation,
                dispatch_generation=job.dispatch_generation,
                fencing_token=job.fencing_token,
                event_sequence=STARTED_EVENT_SEQUENCE,
                occurred_at=heartbeat_at,
                details={},
            )
        )
        return AcquiredJob(
            job_id=job.id,
            scope=ExecutionScope(
                workspace_id=job.workspace_id,
                system_scope_key=job.system_scope_key,
            ),
            trace_id=TraceId(job.trace_id),
            task_name=job.task_name,
            queue_name=job.queue_name,
            payload=job.payload,
            dispatch_generation=job.dispatch_generation,
            lease=JobLease(
                owner=command.worker_id,
                lease_token=lease_token,
                generation=job.generation,
                fencing_token=job.fencing_token,
                heartbeat_at=heartbeat_at,
                expires_at=lease_expires_at,
            ),
            stage_sequence=job.stage_sequence,
            attempt_count=job.attempt_count,
            max_attempts=job.max_attempts,
            soft_time_limit_seconds=job.soft_time_limit_seconds,
            hard_time_limit_seconds=job.hard_time_limit_seconds,
        )

    async def heartbeat(
        self,
        command: HeartbeatJobCommand,
        *,
        lease_seconds: int,
    ) -> bool:
        database_now = func.clock_timestamp()
        job_id = await self.session.scalar(
            update(Job)
            .where(*self._live_lease_predicate(command.proof))
            .values(
                heartbeat_at=database_now,
                lease_expires_at=(database_now + timedelta(seconds=lease_seconds)),
                updated_at=database_now,
            )
            .returning(Job.id)
        )
        return job_id is not None

    async def checkpoint(
        self,
        command: CheckpointJobCommand,
        *,
        lease_seconds: int,
    ) -> bool:
        database_now = func.clock_timestamp()
        job_id = await self.session.scalar(
            update(Job)
            .where(
                *self._live_lease_predicate(command.proof),
                or_(
                    Job.stage_sequence < command.stage_sequence,
                    and_(
                        Job.stage_sequence == command.stage_sequence,
                        Job.stage_name == command.stage_name,
                    ),
                ),
            )
            .values(
                stage_name=command.stage_name,
                stage_sequence=command.stage_sequence,
                heartbeat_at=database_now,
                lease_expires_at=(database_now + timedelta(seconds=lease_seconds)),
                updated_at=database_now,
            )
            .returning(Job.id)
        )
        return job_id is not None

    async def finish(self, command: FinishJobCommand) -> bool:
        database_now = func.clock_timestamp()
        result = (
            await self.session.execute(
                update(Job)
                .where(*self._live_lease_predicate(command.proof))
                .values(
                    status=command.outcome,
                    terminal_at=database_now,
                    result=(dict(command.result) if command.result is not None else None),
                    last_error_code=command.error_code,
                    lease_owner=None,
                    lease_token=None,
                    lease_expires_at=None,
                    heartbeat_at=None,
                    stage_name=command.outcome.value,
                    stage_sequence=Job.stage_sequence + 1,
                    updated_at=database_now,
                )
                .returning(
                    Job.id,
                    Job.generation,
                    Job.dispatch_generation,
                    Job.fencing_token,
                    Job.stage_sequence,
                    Job.terminal_at,
                )
            )
        ).one_or_none()
        if result is None:
            return False

        (
            job_id,
            generation,
            dispatch_generation,
            fencing_token,
            event_sequence,
            terminal_at,
        ) = result
        if terminal_at is None:
            raise JobPersistenceError
        self.session.add(
            JobEvent(
                id=uuid4(),
                job_id=job_id,
                event_type=JobEventType(command.outcome.value),
                generation=generation,
                dispatch_generation=dispatch_generation,
                fencing_token=fencing_token,
                event_sequence=event_sequence,
                occurred_at=terminal_at,
                details={},
            )
        )
        return True

    async def retry(
        self,
        command: RetryJobCommand,
        *,
        outbox_event_id: UUID,
    ) -> JobRetryRecord | None:
        """Settle one retryable failure without releasing its fence early."""

        job = await self.session.scalar(
            select(Job).where(*self._live_lease_predicate(command.proof)).with_for_update()
        )
        if job is None:
            return None

        database_now = await self.session.scalar(select(func.clock_timestamp()))
        if database_now is None:
            raise JobPersistenceError

        job.last_error_code = command.error_code.value
        job.lease_owner = None
        job.lease_token = None
        job.lease_expires_at = None
        job.heartbeat_at = None
        job.stage_sequence += 1
        job.updated_at = database_now

        if job.attempt_count >= job.max_attempts:
            job.status = JobStatus.DEAD_LETTER
            job.terminal_at = database_now
            job.stage_name = JobStatus.DEAD_LETTER.value
            self.session.add(
                JobEvent(
                    id=uuid4(),
                    job_id=job.id,
                    event_type=JobEventType.DEAD_LETTER,
                    generation=job.generation,
                    dispatch_generation=job.dispatch_generation,
                    fencing_token=job.fencing_token,
                    event_sequence=job.stage_sequence,
                    occurred_at=database_now,
                    details={"error_code": command.error_code.value},
                )
            )
            await self.session.flush()
            return JobRetryRecord(
                disposition=JobRetryDisposition.DEAD_LETTER,
                dispatch_generation=job.dispatch_generation,
                outbox_event_id=None,
            )

        next_dispatch_generation = job.dispatch_generation + 1
        next_attempt_at = database_now + timedelta(seconds=command.retry_delay_seconds)
        job.status = JobStatus.RETRY_WAIT
        job.dispatch_generation = next_dispatch_generation
        job.dispatch_attempt = 0
        job.dispatched_at = None
        job.started_at = None
        job.available_at = next_attempt_at
        job.stage_name = JobStatus.RETRY_WAIT.value

        self.session.add(
            JobEvent(
                id=uuid4(),
                job_id=job.id,
                event_type=JobEventType.RETRY_SCHEDULED,
                generation=job.generation,
                dispatch_generation=next_dispatch_generation,
                fencing_token=job.fencing_token,
                event_sequence=job.stage_sequence,
                occurred_at=database_now,
                details={
                    "error_code": command.error_code.value,
                    "outbox_id": str(outbox_event_id),
                    "retry_delay_seconds": command.retry_delay_seconds,
                },
            )
        )
        self.session.add(
            OutboxEvent(
                id=outbox_event_id,
                workspace_id=job.workspace_id,
                system_scope_key=job.system_scope_key,
                source_job_id=job.id,
                job_dispatch_generation=next_dispatch_generation,
                topic=JOB_DISPATCH_OUTBOX_TOPIC,
                event_type=JOB_DISPATCH_OUTBOX_EVENT_TYPE,
                payload={
                    "job_id": str(job.id),
                    "outbox_id": str(outbox_event_id),
                    "dispatch_generation": next_dispatch_generation,
                    "trace_id": job.trace_id,
                },
                deduplication_key=(f"job:{job.id}:dispatch:{next_dispatch_generation}"),
                status=OutboxStatus.PENDING,
                next_attempt_at=next_attempt_at,
            )
        )
        await self.session.flush()
        return JobRetryRecord(
            disposition=JobRetryDisposition.RETRY_SCHEDULED,
            dispatch_generation=next_dispatch_generation,
            outbox_event_id=outbox_event_id,
        )

    async def reconcile(
        self,
        command: ReconcileJobsCommand,
        *,
        outbox_event_ids: tuple[UUID, ...],
    ) -> JobReconciliationResult:
        """Recover stalled deliveries and expired executions under row locks."""

        if len(outbox_event_ids) != command.batch_size or any(
            identifier.int == 0 for identifier in outbox_event_ids
        ):
            raise ValueError("Reconciliation outbox identifiers are invalid")

        database_now = await self.session.scalar(select(func.clock_timestamp()))
        if database_now is None:
            raise JobPersistenceError
        unstarted_before = database_now - timedelta(seconds=command.unstarted_timeout_seconds)
        current_dispatch_was_published = exists(
            select(OutboxEvent.id).where(
                OutboxEvent.source_job_id == Job.id,
                OutboxEvent.job_dispatch_generation == Job.dispatch_generation,
                OutboxEvent.topic == JOB_DISPATCH_OUTBOX_TOPIC,
                OutboxEvent.event_type == JOB_DISPATCH_OUTBOX_EVENT_TYPE,
                OutboxEvent.status == OutboxStatus.PUBLISHED,
            )
        )
        unstarted_predicate = and_(
            Job.status == JobStatus.DISPATCHED,
            Job.started_at.is_(None),
            Job.dispatched_at.is_not(None),
            Job.dispatched_at <= unstarted_before,
            current_dispatch_was_published,
        )
        expired_lease_predicate = and_(
            Job.status == JobStatus.RUNNING,
            Job.lease_expires_at.is_not(None),
            Job.lease_expires_at <= database_now,
        )
        jobs = tuple(
            await self.session.scalars(
                select(Job)
                .where(or_(unstarted_predicate, expired_lease_predicate))
                .order_by(
                    case((Job.status == JobStatus.RUNNING, 0), else_=1),
                    func.coalesce(Job.lease_expires_at, Job.dispatched_at),
                    Job.id,
                )
                .limit(command.batch_size)
                .with_for_update(skip_locked=True)
            )
        )

        unstarted = 0
        expired_leases = 0
        retry_scheduled = 0
        cancelled = 0
        dead_lettered = 0
        outbox_id_iterator = iter(outbox_event_ids)

        for job in jobs:
            lease_expired = job.status is JobStatus.RUNNING
            if lease_expired:
                expired_leases += 1
                self._invalidate_expired_lease(job, database_now)
            else:
                unstarted += 1
                job.last_error_code = JobExecutionErrorCode.UNSTARTED_TIMEOUT.value

            if job.cancel_requested_at is not None:
                self._settle_reconciled_terminal(
                    job,
                    database_now,
                    outcome=JobStatus.CANCELLED,
                    error_code=None,
                )
                cancelled += 1
                continue

            retries_exhausted = (
                job.attempt_count >= job.max_attempts
                if lease_expired
                else job.dispatch_generation >= job.max_attempts
            )
            if retries_exhausted:
                self._settle_reconciled_terminal(
                    job,
                    database_now,
                    outcome=JobStatus.DEAD_LETTER,
                    error_code=job.last_error_code,
                )
                dead_lettered += 1
                continue

            retry_coordinate = job.attempt_count if lease_expired else job.dispatch_generation
            self._schedule_reconciled_retry(
                job,
                database_now,
                outbox_event_id=next(outbox_id_iterator),
                retry_delay_seconds=job_retry_delay_seconds(
                    job.id,
                    max(1, retry_coordinate),
                ),
            )
            retry_scheduled += 1

        await self.session.flush()
        return JobReconciliationResult(
            selected=len(jobs),
            unstarted=unstarted,
            expired_leases=expired_leases,
            retry_scheduled=retry_scheduled,
            cancelled=cancelled,
            dead_lettered=dead_lettered,
        )

    def _invalidate_expired_lease(self, job: Job, database_now: datetime) -> None:
        expired_fencing_token = job.fencing_token
        job.fencing_token += 1
        job.lease_owner = None
        job.lease_token = None
        job.lease_expires_at = None
        job.heartbeat_at = None
        job.last_error_code = JobExecutionErrorCode.LEASE_EXPIRED.value
        job.stage_sequence += 1
        job.updated_at = database_now
        self.session.add(
            JobEvent(
                id=uuid4(),
                job_id=job.id,
                event_type=JobEventType.LEASE_EXPIRED,
                generation=job.generation,
                dispatch_generation=job.dispatch_generation,
                fencing_token=job.fencing_token,
                event_sequence=job.stage_sequence,
                occurred_at=database_now,
                details={"expired_fencing_token": expired_fencing_token},
            )
        )

    def _settle_reconciled_terminal(
        self,
        job: Job,
        database_now: datetime,
        *,
        outcome: JobStatus,
        error_code: str | None,
    ) -> None:
        job.status = outcome
        job.terminal_at = database_now
        job.stage_name = outcome.value
        job.stage_sequence += 1
        job.last_error_code = error_code
        job.updated_at = database_now
        self.session.add(
            JobEvent(
                id=uuid4(),
                job_id=job.id,
                event_type=JobEventType(outcome.value),
                generation=job.generation,
                dispatch_generation=job.dispatch_generation,
                fencing_token=job.fencing_token,
                event_sequence=job.stage_sequence,
                occurred_at=database_now,
                details=({"error_code": error_code} if error_code is not None else {}),
            )
        )

    def _schedule_reconciled_retry(
        self,
        job: Job,
        database_now: datetime,
        *,
        outbox_event_id: UUID,
        retry_delay_seconds: int,
    ) -> None:
        next_dispatch_generation = job.dispatch_generation + 1
        next_attempt_at = database_now + timedelta(seconds=retry_delay_seconds)
        error_code = job.last_error_code
        if error_code is None:
            raise JobPersistenceError

        job.status = JobStatus.RETRY_WAIT
        job.dispatch_generation = next_dispatch_generation
        job.dispatch_attempt = 0
        job.dispatched_at = None
        job.started_at = None
        job.available_at = next_attempt_at
        job.stage_name = JobStatus.RETRY_WAIT.value
        job.stage_sequence += 1
        job.updated_at = database_now
        self.session.add(
            JobEvent(
                id=uuid4(),
                job_id=job.id,
                event_type=JobEventType.RETRY_SCHEDULED,
                generation=job.generation,
                dispatch_generation=next_dispatch_generation,
                fencing_token=job.fencing_token,
                event_sequence=job.stage_sequence,
                occurred_at=database_now,
                details={
                    "error_code": error_code,
                    "outbox_id": str(outbox_event_id),
                    "retry_delay_seconds": retry_delay_seconds,
                    "source": "reconciler",
                },
            )
        )
        self.session.add(
            OutboxEvent(
                id=outbox_event_id,
                workspace_id=job.workspace_id,
                system_scope_key=job.system_scope_key,
                source_job_id=job.id,
                job_dispatch_generation=next_dispatch_generation,
                topic=JOB_DISPATCH_OUTBOX_TOPIC,
                event_type=JOB_DISPATCH_OUTBOX_EVENT_TYPE,
                payload={
                    "job_id": str(job.id),
                    "outbox_id": str(outbox_event_id),
                    "dispatch_generation": next_dispatch_generation,
                    "trace_id": job.trace_id,
                },
                deduplication_key=(f"job:{job.id}:dispatch:{next_dispatch_generation}"),
                status=OutboxStatus.PENDING,
                next_attempt_at=next_attempt_at,
            )
        )

    async def _reuse_idempotent_submission(
        self, prepared: PreparedJobSubmission
    ) -> JobSubmissionRecord:
        if prepared.idempotency_key_hash is None or prepared.request_fingerprint is None:
            raise JobPersistenceError

        filters = [
            Job.task_name == prepared.task_name,
            Job.idempotency_key_hash == prepared.idempotency_key_hash,
        ]
        if prepared.scope.workspace_id is not None:
            filters.append(Job.workspace_id == prepared.scope.workspace_id)
        else:
            filters.append(Job.system_scope_key == prepared.scope.system_scope_key)

        job = await self.session.scalar(select(Job).where(*filters).with_for_update())
        if job is None or job.request_fingerprint is None:
            raise JobPersistenceError
        if not hmac.compare_digest(
            job.request_fingerprint,
            prepared.request_fingerprint,
        ):
            raise JobIdempotencyConflictError

        outbox_event_id = await self.session.scalar(
            select(OutboxEvent.id).where(
                OutboxEvent.source_job_id == job.id,
                OutboxEvent.job_dispatch_generation == job.dispatch_generation,
            )
        )
        if outbox_event_id is None:
            raise JobPersistenceError

        return JobSubmissionRecord(
            job_id=job.id,
            outbox_event_id=outbox_event_id,
            status=job.status,
            dispatch_generation=job.dispatch_generation,
            created=False,
        )

    @staticmethod
    def _job_values(prepared: PreparedJobSubmission) -> dict[str, object]:
        return {
            "id": prepared.job_id,
            "workspace_id": prepared.scope.workspace_id,
            "system_scope_key": prepared.scope.system_scope_key,
            "task_name": prepared.task_name,
            "trace_id": prepared.trace_id,
            "queue_name": prepared.queue_name,
            "payload": dict(prepared.payload),
            "payload_schema_version": 1,
            "status": JobStatus.PENDING,
            "idempotency_key_hash": prepared.idempotency_key_hash,
            "request_fingerprint": prepared.request_fingerprint,
            "priority": prepared.priority,
            "available_at": prepared.available_at,
            "attempt_count": 0,
            "max_attempts": prepared.max_attempts,
            "generation": 0,
            "dispatch_generation": DISPATCH_GENERATION,
            "dispatch_attempt": 0,
            "fencing_token": 0,
            "stage_name": "pending",
            "stage_sequence": 0,
            "soft_time_limit_seconds": prepared.soft_time_limit_seconds,
            "hard_time_limit_seconds": prepared.hard_time_limit_seconds,
        }

    @staticmethod
    def _live_lease_predicate(
        proof: JobLeaseProof,
    ) -> tuple[ColumnElement[bool], ...]:
        return (
            Job.id == proof.job_id,
            Job.status == JobStatus.RUNNING,
            Job.lease_owner == proof.owner,
            Job.lease_token == proof.lease_token,
            Job.fencing_token == proof.fencing_token,
            Job.lease_expires_at.is_not(None),
            Job.lease_expires_at > func.clock_timestamp(),
        )


@dataclass(slots=True)
class SqlAlchemyOutboxWriter:
    """Claim and settle job-dispatch outbox rows with fenced ownership."""

    session: AsyncSession

    async def claim_job_dispatches(
        self,
        command: ClaimOutboxCommand,
    ) -> tuple[ClaimedJobDispatch, ...]:
        database_now = await self.session.scalar(select(func.clock_timestamp()))
        if database_now is None:
            raise OutboxPersistenceError

        rows = (
            await self.session.execute(
                select(
                    OutboxEvent,
                    Job.queue_name,
                    Job.trace_id,
                    Job.soft_time_limit_seconds,
                    Job.hard_time_limit_seconds,
                )
                .join(Job, Job.id == OutboxEvent.source_job_id)
                .where(
                    OutboxEvent.topic == JOB_DISPATCH_OUTBOX_TOPIC,
                    OutboxEvent.event_type == JOB_DISPATCH_OUTBOX_EVENT_TYPE,
                    OutboxEvent.source_job_id.is_not(None),
                    OutboxEvent.job_dispatch_generation.is_not(None),
                    or_(
                        and_(
                            OutboxEvent.status == OutboxStatus.PENDING,
                            OutboxEvent.next_attempt_at <= database_now,
                            OutboxEvent.attempt_count < OutboxEvent.max_attempts,
                        ),
                        and_(
                            OutboxEvent.status == OutboxStatus.PUBLISHING,
                            OutboxEvent.lock_expires_at <= database_now,
                        ),
                    ),
                )
                .order_by(
                    OutboxEvent.next_attempt_at,
                    OutboxEvent.created_at,
                    OutboxEvent.id,
                )
                .limit(command.batch_size)
                .with_for_update(of=OutboxEvent, skip_locked=True)
            )
        ).all()

        claimed: list[ClaimedJobDispatch] = []
        lock_expires_at = database_now + timedelta(seconds=command.claim_seconds)

        for (
            outbox,
            queue_name,
            trace_id,
            soft_time_limit_seconds,
            hard_time_limit_seconds,
        ) in rows:
            source_job_id = outbox.source_job_id
            dispatch_generation = outbox.job_dispatch_generation
            if source_job_id is None or dispatch_generation is None:
                raise OutboxPersistenceError

            claim_token = uuid4()
            outbox.status = OutboxStatus.PUBLISHING
            outbox.attempt_count = min(
                outbox.attempt_count + 1,
                outbox.max_attempts,
            )
            outbox.claim_generation += 1
            outbox.locked_by = command.dispatcher_id
            outbox.claim_token = claim_token
            outbox.locked_at = database_now
            outbox.lock_expires_at = lock_expires_at
            outbox.updated_at = database_now

            proof = OutboxClaimProof(
                outbox_id=outbox.id,
                locked_by=command.dispatcher_id,
                claim_token=claim_token,
                claim_generation=outbox.claim_generation,
            )
            claimed.append(
                ClaimedJobDispatch(
                    proof=proof,
                    message=JobDispatchMessage(
                        job_id=source_job_id,
                        dispatch_generation=dispatch_generation,
                        outbox_id=outbox.id,
                        trace_id=TraceId(trace_id),
                    ),
                    queue_name=queue_name,
                    attempt_count=outbox.attempt_count,
                    max_attempts=outbox.max_attempts,
                    soft_time_limit_seconds=soft_time_limit_seconds,
                    hard_time_limit_seconds=hard_time_limit_seconds,
                )
            )

        await self.session.flush()
        return tuple(claimed)

    async def mark_published(self, proof: OutboxClaimProof) -> bool:
        database_now = func.clock_timestamp()
        settled = (
            await self.session.execute(
                update(OutboxEvent)
                .where(*self._claim_predicate(proof))
                .values(
                    status=OutboxStatus.PUBLISHED,
                    published_at=database_now,
                    terminal_at=database_now,
                    last_error_code=None,
                    locked_by=None,
                    claim_token=None,
                    locked_at=None,
                    lock_expires_at=None,
                    updated_at=database_now,
                )
                .returning(
                    OutboxEvent.source_job_id,
                    OutboxEvent.job_dispatch_generation,
                )
            )
        ).one_or_none()
        if settled is None:
            return False

        source_job_id, dispatch_generation = settled
        if source_job_id is None or dispatch_generation is None:
            raise OutboxPersistenceError

        dispatched = (
            await self.session.execute(
                update(Job)
                .where(
                    Job.id == source_job_id,
                    Job.status.in_((JobStatus.PENDING, JobStatus.RETRY_WAIT)),
                    Job.dispatch_generation == dispatch_generation,
                )
                .values(
                    status=JobStatus.DISPATCHED,
                    dispatch_attempt=case(
                        (Job.dispatch_attempt == 0, 1),
                        else_=Job.dispatch_attempt,
                    ),
                    dispatched_at=func.coalesce(Job.dispatched_at, database_now),
                    stage_name=JobStatus.DISPATCHED.value,
                    stage_sequence=Job.stage_sequence + 1,
                    updated_at=database_now,
                )
                .returning(
                    Job.id,
                    Job.generation,
                    Job.dispatch_generation,
                    Job.fencing_token,
                    Job.stage_sequence,
                    Job.dispatched_at,
                )
            )
        ).one_or_none()
        if dispatched is None:
            return True

        (
            job_id,
            generation,
            current_dispatch_generation,
            fencing_token,
            event_sequence,
            dispatched_at,
        ) = dispatched
        if dispatched_at is None:
            raise OutboxPersistenceError

        self.session.add(
            JobEvent(
                id=uuid4(),
                job_id=job_id,
                event_type=JobEventType.DISPATCHED,
                generation=generation,
                dispatch_generation=current_dispatch_generation,
                fencing_token=fencing_token,
                event_sequence=event_sequence,
                occurred_at=dispatched_at,
                details={"outbox_id": str(proof.outbox_id)},
            )
        )
        return True

    async def mark_failed(
        self,
        proof: OutboxClaimProof,
        *,
        error_code: OutboxPublishErrorCode,
        retry_delay_seconds: int,
    ) -> OutboxFailureDisposition:
        if retry_delay_seconds < 1:
            raise ValueError("Outbox retry delay must be positive")

        outbox = await self.session.scalar(
            select(OutboxEvent).where(*self._claim_predicate(proof)).with_for_update()
        )
        if outbox is None:
            return OutboxFailureDisposition.CLAIM_LOST

        database_now = await self.session.scalar(select(func.clock_timestamp()))
        if database_now is None:
            raise OutboxPersistenceError

        outbox.last_error_code = error_code.value
        outbox.locked_by = None
        outbox.claim_token = None
        outbox.locked_at = None
        outbox.lock_expires_at = None
        outbox.updated_at = database_now

        if outbox.attempt_count < outbox.max_attempts:
            outbox.status = OutboxStatus.PENDING
            outbox.next_attempt_at = database_now + timedelta(seconds=retry_delay_seconds)
            await self.session.flush()
            return OutboxFailureDisposition.RETRY_SCHEDULED

        outbox.status = OutboxStatus.DEAD_LETTER
        outbox.terminal_at = database_now
        await self._dead_letter_source_job(
            outbox,
            occurred_at=database_now,
            error_code=error_code,
        )
        await self.session.flush()
        return OutboxFailureDisposition.DEAD_LETTER

    async def _dead_letter_source_job(
        self,
        outbox: OutboxEvent,
        *,
        occurred_at: datetime,
        error_code: OutboxPublishErrorCode,
    ) -> None:
        source_job_id = outbox.source_job_id
        dispatch_generation = outbox.job_dispatch_generation
        if source_job_id is None or dispatch_generation is None:
            raise OutboxPersistenceError

        dead_lettered = (
            await self.session.execute(
                update(Job)
                .where(
                    Job.id == source_job_id,
                    Job.dispatch_generation == dispatch_generation,
                    Job.status.in_(
                        (
                            JobStatus.PENDING,
                            JobStatus.DISPATCHED,
                            JobStatus.RETRY_WAIT,
                        )
                    ),
                )
                .values(
                    status=JobStatus.DEAD_LETTER,
                    terminal_at=occurred_at,
                    last_error_code=error_code.value,
                    stage_name=JobStatus.DEAD_LETTER.value,
                    stage_sequence=Job.stage_sequence + 1,
                    updated_at=occurred_at,
                )
                .returning(
                    Job.id,
                    Job.generation,
                    Job.dispatch_generation,
                    Job.fencing_token,
                    Job.stage_sequence,
                    Job.terminal_at,
                )
            )
        ).one_or_none()
        if dead_lettered is None:
            return

        (
            job_id,
            generation,
            current_dispatch_generation,
            fencing_token,
            event_sequence,
            terminal_at,
        ) = dead_lettered
        if terminal_at is None:
            raise OutboxPersistenceError

        self.session.add(
            JobEvent(
                id=uuid4(),
                job_id=job_id,
                event_type=JobEventType.DEAD_LETTER,
                generation=generation,
                dispatch_generation=current_dispatch_generation,
                fencing_token=fencing_token,
                event_sequence=event_sequence,
                occurred_at=terminal_at,
                details={
                    "error_code": error_code.value,
                    "outbox_id": str(outbox.id),
                },
            )
        )

    @staticmethod
    def _claim_predicate(
        proof: OutboxClaimProof,
    ) -> tuple[ColumnElement[bool], ...]:
        return (
            OutboxEvent.id == proof.outbox_id,
            OutboxEvent.status == OutboxStatus.PUBLISHING,
            OutboxEvent.locked_by == proof.locked_by,
            OutboxEvent.claim_token == proof.claim_token,
            OutboxEvent.claim_generation == proof.claim_generation,
        )


@dataclass(slots=True)
class SqlAlchemyScheduleWriter:
    """Materialize durable cron facts without ever contacting the broker."""

    session: AsyncSession
    occurrence_observer: ScheduleOccurrenceObserver = ignore_schedule_occurrence

    async def ensure_schedule(
        self,
        definition: ScheduleDefinition,
    ) -> EnsuredSchedule:
        database_now = await self._database_now()
        schedule_id = uuid4()
        values = {
            "id": schedule_id,
            "workspace_id": definition.scope.workspace_id,
            "system_scope_key": definition.scope.system_scope_key,
            "name": definition.name,
            "task_name": definition.task_name,
            "queue_name": definition.queue_name,
            "max_attempts": definition.max_attempts,
            "priority": definition.priority,
            "soft_time_limit_seconds": definition.soft_time_limit_seconds,
            "hard_time_limit_seconds": definition.hard_time_limit_seconds,
            "cron_expression": definition.cron_expression,
            "timezone_name": definition.timezone_name,
            "payload": dict(definition.payload),
            "misfire_policy": definition.misfire_policy,
            "catch_up_window_seconds": definition.catch_up_window_seconds,
            "max_catch_up": definition.max_catch_up,
            "next_due_at": definition.next_after(database_now),
            "created_at": database_now,
            "updated_at": database_now,
        }
        statement = insert(Schedule).values(**values)
        if definition.scope.workspace_id is not None:
            statement = statement.on_conflict_do_nothing(
                index_elements=(Schedule.workspace_id, Schedule.name),
                index_where=text("workspace_id IS NOT NULL"),
            )
            existing_filter = (
                Schedule.workspace_id == definition.scope.workspace_id,
                Schedule.name == definition.name,
            )
        else:
            statement = statement.on_conflict_do_nothing(
                index_elements=(Schedule.system_scope_key, Schedule.name),
                index_where=text("system_scope_key IS NOT NULL"),
            )
            existing_filter = (
                Schedule.system_scope_key == definition.scope.system_scope_key,
                Schedule.name == definition.name,
            )

        inserted_id = await self.session.scalar(statement.returning(Schedule.id))
        if isinstance(inserted_id, UUID):
            return EnsuredSchedule(schedule_id=inserted_id, created=True)

        existing = await self.session.scalar(select(Schedule).where(*existing_filter))
        if existing is None:
            raise JobPersistenceError()
        if self._definition(existing) != definition:
            raise ScheduleDefinitionConflictError
        return EnsuredSchedule(schedule_id=existing.id, created=False)

    async def materialize_due(
        self,
        command: ScheduleTickCommand,
    ) -> ScheduleTickResult:
        database_now = await self._database_now()
        schedules = tuple(
            await self.session.scalars(
                select(Schedule)
                .where(
                    Schedule.enabled.is_(True),
                    Schedule.next_due_at.is_not(None),
                    Schedule.next_due_at <= database_now,
                )
                .order_by(Schedule.next_due_at.asc(), Schedule.id.asc())
                .limit(command.batch_size)
                .with_for_update(skip_locked=True)
            )
        )
        materialized = 0
        jobs_created = 0
        blocked = 0

        for schedule in schedules:
            if schedule.next_due_at is None:
                raise JobPersistenceError()
            plan = plan_due_schedule(
                self._definition(schedule),
                next_due_at=schedule.next_due_at,
                database_now=database_now,
            )
            if plan.blocked:
                occurrence = plan.occurrences[0]
                self.session.add(
                    ScheduleOccurrence(
                        id=uuid4(),
                        schedule_id=schedule.id,
                        job_id=None,
                        trigger_kind=ScheduleTriggerKind.SCHEDULED,
                        scheduled_for=occurrence.scheduled_for,
                        trigger_id=None,
                        schedule_version=schedule.version,
                        status=occurrence.status,
                        window_start=occurrence.window_start,
                        window_end=occurrence.window_end,
                        coalesced_count=occurrence.coalesced_count,
                        dst_adjusted=occurrence.dst_adjusted,
                        utc_offset_seconds=occurrence.utc_offset_seconds,
                        error_code=(
                            occurrence.error_code.value
                            if occurrence.error_code is not None
                            else None
                        ),
                        created_at=database_now,
                    )
                )
                schedule.enabled = False
                schedule.misfire_blocked_at = database_now
                schedule.misfire_error_code = (
                    plan.error_code.value if plan.error_code is not None else None
                )
                schedule.missed_from = plan.missed_from
                schedule.missed_through = plan.missed_through
                schedule.missed_count = plan.missed_count
                schedule.missed_count_is_lower_bound = plan.missed_count_is_lower_bound
                schedule.updated_at = database_now
                blocked += 1
                continue

            for occurrence in plan.occurrences:
                occurrence_id = uuid4()
                prepared = self._prepare_submission(
                    schedule,
                    occurrence_id=occurrence_id,
                    available_at=occurrence.scheduled_for,
                    idempotency_suffix=(f"scheduled:{occurrence.scheduled_for.isoformat()}"),
                    database_now=database_now,
                )
                record = await SqlAlchemyJobWriter(self.session).submit(prepared)
                schedule_occurrence = ScheduleOccurrence(
                    id=occurrence_id,
                    schedule_id=schedule.id,
                    job_id=record.job_id,
                    trigger_kind=ScheduleTriggerKind.SCHEDULED,
                    scheduled_for=occurrence.scheduled_for,
                    trigger_id=None,
                    schedule_version=schedule.version,
                    status=occurrence.status,
                    window_start=occurrence.window_start,
                    window_end=occurrence.window_end,
                    coalesced_count=occurrence.coalesced_count,
                    dst_adjusted=occurrence.dst_adjusted,
                    utc_offset_seconds=occurrence.utc_offset_seconds,
                    error_code=None,
                    created_at=database_now,
                )
                self.session.add(schedule_occurrence)
                await self._observe_materialization(
                    schedule,
                    occurrence_id=occurrence_id,
                    job_id=record.job_id,
                    trigger_kind=ScheduleTriggerKind.SCHEDULED,
                    scheduled_for=occurrence.scheduled_for,
                    window_start=occurrence.window_start,
                    window_end=occurrence.window_end,
                    coalesced_count=occurrence.coalesced_count,
                    database_now=database_now,
                )
                materialized += 1
                jobs_created += int(record.created)

            schedule.last_fired_at = plan.occurrences[-1].scheduled_for
            schedule.next_due_at = plan.next_due_at
            schedule.misfire_blocked_at = None
            schedule.misfire_error_code = None
            schedule.missed_from = None
            schedule.missed_through = None
            schedule.missed_count = 0
            schedule.missed_count_is_lower_bound = False
            schedule.updated_at = database_now

        return ScheduleTickResult(
            selected_schedules=len(schedules),
            materialized_occurrences=materialized,
            jobs_created=jobs_created,
            blocked_schedules=blocked,
        )

    async def trigger_manual(
        self,
        command: ManualScheduleTriggerCommand,
    ) -> ManualScheduleTriggerResult:
        await self.session.execute(
            select(func.pg_advisory_xact_lock(self._manual_trigger_lock_key(command.trigger_id)))
        )
        existing = await self._manual_occurrence(command)
        if existing is not None:
            return self._manual_result(existing, command, created=False)

        schedule = await self.session.scalar(
            select(Schedule).where(Schedule.id == command.schedule_id).with_for_update()
        )
        if schedule is None:
            raise ScheduleNotFoundError

        database_now = await self._database_now()
        occurrence_id = uuid4()
        prepared = self._prepare_submission(
            schedule,
            occurrence_id=occurrence_id,
            available_at=database_now,
            idempotency_suffix=f"manual:{command.trigger_id}",
            database_now=database_now,
        )
        record = await SqlAlchemyJobWriter(self.session).submit(prepared)
        offset = database_now.astimezone(ZoneInfo(schedule.timezone_name)).utcoffset()
        if offset is None:
            raise JobPersistenceError()
        schedule_occurrence = ScheduleOccurrence(
            id=occurrence_id,
            schedule_id=schedule.id,
            job_id=record.job_id,
            trigger_kind=ScheduleTriggerKind.MANUAL,
            scheduled_for=None,
            trigger_id=command.trigger_id,
            schedule_version=schedule.version,
            status=ScheduleOccurrenceStatus.MATERIALIZED,
            window_start=database_now,
            window_end=database_now,
            coalesced_count=1,
            dst_adjusted=False,
            utc_offset_seconds=int(offset.total_seconds()),
            error_code=None,
            created_at=database_now,
        )
        self.session.add(schedule_occurrence)
        await self._observe_materialization(
            schedule,
            occurrence_id=occurrence_id,
            job_id=record.job_id,
            trigger_kind=ScheduleTriggerKind.MANUAL,
            scheduled_for=None,
            window_start=database_now,
            window_end=database_now,
            coalesced_count=1,
            database_now=database_now,
        )
        return ManualScheduleTriggerResult(
            occurrence_id=occurrence_id,
            job_id=record.job_id,
            created=True,
        )

    @staticmethod
    def _manual_trigger_lock_key(trigger_id: UUID) -> int:
        """Map a trigger to one stable signed PostgreSQL advisory-lock key."""

        return int.from_bytes(
            trigger_id.bytes[:8],
            byteorder="big",
            signed=True,
        )

    async def _manual_occurrence(
        self,
        command: ManualScheduleTriggerCommand,
    ) -> ScheduleOccurrence | None:
        result = await self.session.scalars(
            select(ScheduleOccurrence).where(
                ScheduleOccurrence.trigger_kind == ScheduleTriggerKind.MANUAL,
                ScheduleOccurrence.trigger_id == command.trigger_id,
            )
        )
        return result.one_or_none()

    async def _observe_materialization(
        self,
        schedule: Schedule,
        *,
        occurrence_id: UUID,
        job_id: UUID,
        trigger_kind: ScheduleTriggerKind,
        scheduled_for: datetime | None,
        window_start: datetime,
        window_end: datetime,
        coalesced_count: int,
        database_now: datetime,
    ) -> None:
        await self.occurrence_observer(
            self.session,
            ScheduleOccurrenceMaterialization(
                occurrence_id=occurrence_id,
                schedule_id=schedule.id,
                job_id=job_id,
                scope=ExecutionScope(
                    workspace_id=schedule.workspace_id,
                    system_scope_key=schedule.system_scope_key,
                ),
                task_name=schedule.task_name,
                payload=schedule.payload,
                trigger_kind=trigger_kind,
                scheduled_for=scheduled_for,
                window_start=window_start,
                window_end=window_end,
                coalesced_count=coalesced_count,
                trace_id=TraceId(f"schedule:{occurrence_id}"),
                materialized_at=database_now,
            ),
        )

    @staticmethod
    def _manual_result(
        occurrence: ScheduleOccurrence,
        command: ManualScheduleTriggerCommand,
        *,
        created: bool,
    ) -> ManualScheduleTriggerResult:
        if occurrence.schedule_id != command.schedule_id:
            raise ScheduleTriggerConflictError
        if occurrence.job_id is None:
            raise JobPersistenceError()
        return ManualScheduleTriggerResult(
            occurrence_id=occurrence.id,
            job_id=occurrence.job_id,
            created=created,
        )

    @staticmethod
    def _definition(schedule: Schedule) -> ScheduleDefinition:
        return ScheduleDefinition(
            scope=ExecutionScope(
                workspace_id=schedule.workspace_id,
                system_scope_key=schedule.system_scope_key,
            ),
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
        )

    @staticmethod
    def _prepare_submission(
        schedule: Schedule,
        *,
        occurrence_id: UUID,
        available_at: datetime,
        idempotency_suffix: str,
        database_now: datetime,
    ) -> PreparedJobSubmission:
        raw_key = f"schedule:{schedule.id}:{idempotency_suffix}"
        definition = JobDefinition(
            scope=ExecutionScope(
                workspace_id=schedule.workspace_id,
                system_scope_key=schedule.system_scope_key,
            ),
            task_name=schedule.task_name,
            queue_name=schedule.queue_name,
            payload=schedule.payload,
            available_at=available_at,
            max_attempts=schedule.max_attempts,
            idempotency_key=raw_key,
            priority=schedule.priority,
            soft_time_limit_seconds=schedule.soft_time_limit_seconds,
            hard_time_limit_seconds=schedule.hard_time_limit_seconds,
        )
        return PreparedJobSubmission(
            job_id=uuid4(),
            outbox_event_id=uuid4(),
            scope=definition.scope,
            task_name=definition.task_name,
            queue_name=definition.queue_name,
            payload=definition.payload,
            available_at=definition.available_at,
            max_attempts=definition.max_attempts,
            priority=definition.priority,
            soft_time_limit_seconds=definition.soft_time_limit_seconds,
            hard_time_limit_seconds=definition.hard_time_limit_seconds,
            trace_id=TraceId(f"schedule:{occurrence_id}"),
            idempotency_key_hash=hash_job_idempotency_key(raw_key),
            request_fingerprint=fingerprint_job_request(definition),
            submitted_at=database_now,
        )

    async def _database_now(self) -> datetime:
        database_now = await self.session.scalar(select(func.clock_timestamp()))
        if not isinstance(database_now, datetime):
            raise JobPersistenceError()
        return database_now.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class SqlAlchemyJobTransactionFactory:
    """Commit one job operation and translate database errors safely."""

    session_factory: AsyncSessionFactory

    def __call__(self) -> AbstractAsyncContextManager[JobWriter]:
        return self._transaction()

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[JobWriter]:
        try:
            async with self.session_factory.begin() as session:
                yield SqlAlchemyJobWriter(session)
        except SQLAlchemyError as error:
            raise JobPersistenceError(sqlstate=safe_sqlstate(error)) from None


@dataclass(frozen=True, slots=True)
class SqlAlchemyScheduleTransactionFactory:
    """Commit one bounded scheduling operation and translate SQL failures."""

    session_factory: AsyncSessionFactory
    occurrence_observer: ScheduleOccurrenceObserver = ignore_schedule_occurrence

    def __call__(self) -> AbstractAsyncContextManager[ScheduleWriter]:
        return self._transaction()

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[ScheduleWriter]:
        try:
            async with self.session_factory.begin() as session:
                yield SqlAlchemyScheduleWriter(
                    session,
                    occurrence_observer=self.occurrence_observer,
                )
        except SQLAlchemyError as error:
            raise JobPersistenceError(sqlstate=safe_sqlstate(error)) from None


@dataclass(frozen=True, slots=True)
class SqlAlchemyOutboxTransactionFactory:
    """Commit one claim/settlement and safely translate database failures."""

    session_factory: AsyncSessionFactory

    def __call__(self) -> AbstractAsyncContextManager[OutboxWriter]:
        return self._transaction()

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[OutboxWriter]:
        try:
            async with self.session_factory.begin() as session:
                yield SqlAlchemyOutboxWriter(session)
        except SQLAlchemyError as error:
            raise OutboxPersistenceError(sqlstate=safe_sqlstate(error)) from None
