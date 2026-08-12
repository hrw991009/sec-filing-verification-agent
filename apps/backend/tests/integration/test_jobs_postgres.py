"""Prove job atomicity, idempotency, and fencing against real PostgreSQL."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

from industry_platform.core.database import (
    AsyncSessionFactory,
    create_database_engine,
    create_database_session_factory,
)
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.jobs.adapters.sqlalchemy import (
    SqlAlchemyJobTransactionFactory,
    SqlAlchemyOutboxTransactionFactory,
)
from industry_platform.modules.jobs.domain import (
    AcquiredJob,
    AcquireJobCommand,
    CheckpointJobCommand,
    ClaimOutboxCommand,
    ExecutionScope,
    FinishJobCommand,
    HeartbeatJobCommand,
    JobDefinition,
    JobEventType,
    JobExecutionErrorCode,
    JobIdempotencyConflictError,
    JobLeaseProof,
    JobNotAcquirableError,
    JobPersistenceError,
    JobRetryDisposition,
    JobStatus,
    LostJobLeaseError,
    OutboxFailureDisposition,
    OutboxPublishErrorCode,
    OutboxStatus,
    PreparedJobSubmission,
    RetryJobCommand,
    SubmitJobCommand,
)
from industry_platform.modules.jobs.models import Job, JobEvent, OutboxEvent
from industry_platform.modules.jobs.service import (
    JobApplicationService,
    JobReconciliationService,
)
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe

AVAILABLE_AT = datetime(2026, 1, 1, tzinfo=UTC)
SUBMITTED_AT = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
RAW_IDEMPOTENCY_KEY = "integration-visible-retry-key"


def submission(
    *,
    payload: dict[str, object] | None = None,
    raw_key: str | None = RAW_IDEMPOTENCY_KEY,
    max_attempts: int = 3,
) -> SubmitJobCommand:
    return SubmitJobCommand(
        definition=JobDefinition(
            scope=ExecutionScope(system_scope_key="integration-tests"),
            task_name="research.collect",
            queue_name="default",
            payload=(payload if payload is not None else {"document": "private-business-input"}),
            available_at=AVAILABLE_AT,
            max_attempts=max_attempts,
            idempotency_key=raw_key,
        ),
        trace_id=TraceId("job-integration-trace"),
    )


def job_service(session_factory: AsyncSessionFactory) -> JobApplicationService:
    return JobApplicationService(
        transaction_factory=SqlAlchemyJobTransactionFactory(session_factory),
        lease_seconds=120,
        clock=lambda: SUBMITTED_AT,
    )


def reconciliation_service(
    session_factory: AsyncSessionFactory,
    *,
    batch_size: int = 10,
) -> JobReconciliationService:
    return JobReconciliationService(
        transaction_factory=SqlAlchemyJobTransactionFactory(session_factory),
        unstarted_timeout_seconds=10,
        batch_size=batch_size,
    )


async def table_counts(session_factory: AsyncSessionFactory) -> tuple[int, int, int]:
    async with session_factory() as session:
        jobs = (await session.scalar(select(func.count()).select_from(Job))) or 0
        events = (await session.scalar(select(func.count()).select_from(JobEvent))) or 0
        outbox = (await session.scalar(select(func.count()).select_from(OutboxEvent))) or 0
    return jobs, events, outbox


def test_submission_is_atomic_reusable_and_conflict_aware(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        service = job_service(session_factory)

        try:
            created = await service.submit(submission())
            reused = await service.submit(submission())

            assert created.created is True
            assert reused.created is False
            assert reused.job_id == created.job_id
            assert reused.outbox_event_id == created.outbox_event_id

            with pytest.raises(JobIdempotencyConflictError):
                await service.submit(submission(payload={"document": "changed"}))

            duplicate_outbox = PreparedJobSubmission(
                job_id=uuid4(),
                outbox_event_id=created.outbox_event_id,
                scope=ExecutionScope(system_scope_key="integration-tests"),
                task_name="research.other",
                queue_name="default",
                payload={},
                available_at=AVAILABLE_AT,
                max_attempts=1,
                priority=0,
                soft_time_limit_seconds=1_500,
                hard_time_limit_seconds=1_800,
                trace_id=TraceId("forced-rollback-trace"),
                idempotency_key_hash=None,
                request_fingerprint=None,
                submitted_at=SUBMITTED_AT,
            )
            with pytest.raises(JobPersistenceError):
                async with SqlAlchemyJobTransactionFactory(session_factory)() as writer:
                    await writer.submit(duplicate_outbox)

            assert await table_counts(session_factory) == (1, 1, 1)

            async with session_factory() as session:
                job = (await session.scalars(select(Job))).one()
                event = (await session.scalars(select(JobEvent))).one()
                outbox = (await session.scalars(select(OutboxEvent))).one()

            assert job.id == created.job_id
            assert job.status is JobStatus.PENDING
            assert job.dispatch_generation == 1
            assert job.dispatch_attempt == 0
            assert job.dispatched_at is None
            assert job.trace_id == "job-integration-trace"
            assert job.payload == {"document": "private-business-input"}
            assert job.idempotency_key_hash is not None
            assert len(job.idempotency_key_hash) == 32
            assert job.request_fingerprint is not None
            assert len(job.request_fingerprint) == 32
            assert event.event_type is JobEventType.CREATED
            assert outbox.payload == {
                "job_id": str(created.job_id),
                "outbox_id": str(created.outbox_event_id),
                "dispatch_generation": 1,
                "trace_id": "job-integration-trace",
            }
            assert RAW_IDEMPOTENCY_KEY not in str(outbox.payload)
            assert "private-business-input" not in str(outbox.payload)
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())


def test_concurrent_reconcilers_recover_lost_published_message_once(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        producer = job_service(session_factory)
        first_reconciler = reconciliation_service(session_factory, batch_size=1)
        second_reconciler = reconciliation_service(session_factory, batch_size=1)

        try:
            submitted = await producer.submit(submission(raw_key=None, max_attempts=2))
            async with session_factory.begin() as session:
                database_now = await session.scalar(select(func.clock_timestamp()))
                assert isinstance(database_now, datetime)
                outbox = await session.get(OutboxEvent, submitted.outbox_event_id)
                job = await session.get(Job, submitted.job_id)
                assert outbox is not None
                assert job is not None
                outbox.status = OutboxStatus.PUBLISHED
                outbox.published_at = database_now
                outbox.terminal_at = database_now
                job.status = JobStatus.DISPATCHED
                job.dispatch_attempt = 1
                job.dispatched_at = database_now - timedelta(seconds=11)
                job.stage_name = JobStatus.DISPATCHED.value

            results = await asyncio.gather(
                first_reconciler.reconcile_once(),
                second_reconciler.reconcile_once(),
            )

            assert sum(result.selected for result in results) == 1
            assert sum(result.retry_scheduled for result in results) == 1
            async with session_factory() as session:
                recovered = await session.get(Job, submitted.job_id)
                outboxes = tuple(
                    await session.scalars(
                        select(OutboxEvent)
                        .where(OutboxEvent.source_job_id == submitted.job_id)
                        .order_by(OutboxEvent.job_dispatch_generation)
                    )
                )
                retry_events = (
                    await session.scalar(
                        select(func.count())
                        .select_from(JobEvent)
                        .where(
                            JobEvent.job_id == submitted.job_id,
                            JobEvent.event_type == JobEventType.RETRY_SCHEDULED,
                        )
                    )
                ) or 0

            assert recovered is not None
            assert recovered.status is JobStatus.RETRY_WAIT
            assert recovered.dispatch_generation == 2
            assert len(outboxes) == 2
            assert outboxes[1].status is OutboxStatus.PENDING
            assert retry_events == 1

            async with session_factory.begin() as session:
                database_now = await session.scalar(select(func.clock_timestamp()))
                assert isinstance(database_now, datetime)
                recovered = await session.get(Job, submitted.job_id)
                retry_outbox = await session.get(OutboxEvent, outboxes[1].id)
                assert recovered is not None
                assert retry_outbox is not None
                retry_outbox.status = OutboxStatus.PUBLISHED
                retry_outbox.published_at = database_now
                retry_outbox.terminal_at = database_now
                recovered.status = JobStatus.DISPATCHED
                recovered.dispatch_attempt = 1
                recovered.dispatched_at = database_now - timedelta(seconds=11)
                recovered.stage_name = JobStatus.DISPATCHED.value

            exhausted = await first_reconciler.reconcile_once()
            repeated = await second_reconciler.reconcile_once()
            assert exhausted.dead_lettered == 1
            assert repeated.selected == 0

            async with session_factory() as session:
                terminal_job = await session.get(Job, submitted.job_id)
                terminal_events = (
                    await session.scalar(
                        select(func.count())
                        .select_from(JobEvent)
                        .where(
                            JobEvent.job_id == submitted.job_id,
                            JobEvent.event_type == JobEventType.DEAD_LETTER,
                        )
                    )
                ) or 0
            assert terminal_job is not None
            assert terminal_job.status is JobStatus.DEAD_LETTER
            assert terminal_job.last_error_code == (JobExecutionErrorCode.UNSTARTED_TIMEOUT.value)
            assert terminal_events == 1
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())


def test_hard_kill_expiry_refences_old_worker_and_honours_cancellation(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        worker = job_service(session_factory)
        reconciler = reconciliation_service(session_factory)

        try:
            hard_killed = await worker.submit(submission(raw_key=None, max_attempts=2))
            abandoned = await worker.acquire(
                AcquireJobCommand(
                    job_id=hard_killed.job_id,
                    dispatch_generation=1,
                    worker_id="hard-killed-worker",
                )
            )
            old_proof = abandoned.lease_proof
            async with session_factory.begin() as session:
                database_now = await session.scalar(select(func.clock_timestamp()))
                assert isinstance(database_now, datetime)
                await session.execute(
                    update(Job)
                    .where(Job.id == hard_killed.job_id)
                    .values(
                        heartbeat_at=database_now - timedelta(seconds=2),
                        lease_expires_at=database_now - timedelta(seconds=1),
                    )
                )

            recovered = await reconciler.reconcile_once()
            assert recovered.expired_leases == 1
            assert recovered.retry_scheduled == 1

            async with session_factory.begin() as session:
                retry_outbox = await session.scalar(
                    select(OutboxEvent).where(
                        OutboxEvent.source_job_id == hard_killed.job_id,
                        OutboxEvent.job_dispatch_generation == 2,
                    )
                )
                retry_job = await session.get(Job, hard_killed.job_id)
                assert retry_outbox is not None
                assert retry_job is not None
                retry_outbox.status = OutboxStatus.PUBLISHED
                retry_outbox.published_at = retry_job.updated_at
                retry_outbox.terminal_at = retry_job.updated_at
                retry_job.available_at = AVAILABLE_AT

            replacement = await worker.acquire(
                AcquireJobCommand(
                    job_id=hard_killed.job_id,
                    dispatch_generation=2,
                    worker_id="replacement-worker",
                    outbox_id=retry_outbox.id,
                    trace_id=TraceId("job-integration-trace"),
                )
            )
            assert replacement.lease.fencing_token > old_proof.fencing_token

            with pytest.raises(LostJobLeaseError):
                await worker.heartbeat(HeartbeatJobCommand(proof=old_proof))
            with pytest.raises(LostJobLeaseError):
                await worker.checkpoint(
                    CheckpointJobCommand(
                        proof=old_proof,
                        stage_name="stale-hard-kill",
                        stage_sequence=abandoned.stage_sequence + 1,
                    )
                )
            with pytest.raises(LostJobLeaseError):
                await worker.finish(
                    FinishJobCommand(
                        proof=old_proof,
                        outcome=JobStatus.SUCCEEDED,
                        result={"stale": True},
                    )
                )

            cancelled_submission = await worker.submit(submission(raw_key=None, max_attempts=3))
            cancelled_execution = await worker.acquire(
                AcquireJobCommand(
                    job_id=cancelled_submission.job_id,
                    dispatch_generation=1,
                    worker_id="cancelled-worker",
                )
            )
            async with session_factory.begin() as session:
                database_now = await session.scalar(select(func.clock_timestamp()))
                assert isinstance(database_now, datetime)
                await session.execute(
                    update(Job)
                    .where(Job.id == cancelled_submission.job_id)
                    .values(
                        heartbeat_at=database_now - timedelta(seconds=2),
                        lease_expires_at=database_now - timedelta(seconds=1),
                        cancel_requested_at=database_now,
                    )
                )

            cancelled = await reconciler.reconcile_once()
            assert cancelled.cancelled == 1
            with pytest.raises(LostJobLeaseError):
                await worker.finish(
                    FinishJobCommand(
                        proof=cancelled_execution.lease_proof,
                        outcome=JobStatus.SUCCEEDED,
                        result={"late": True},
                    )
                )

            async with session_factory() as session:
                cancelled_job = await session.get(
                    Job,
                    cancelled_submission.job_id,
                )
                lease_expired_events = (
                    await session.scalar(
                        select(func.count())
                        .select_from(JobEvent)
                        .where(
                            JobEvent.job_id.in_((hard_killed.job_id, cancelled_submission.job_id)),
                            JobEvent.event_type == JobEventType.LEASE_EXPIRED,
                        )
                    )
                ) or 0
                cancelled_terminal_events = (
                    await session.scalar(
                        select(func.count())
                        .select_from(JobEvent)
                        .where(
                            JobEvent.job_id == cancelled_submission.job_id,
                            JobEvent.event_type == JobEventType.CANCELLED,
                        )
                    )
                ) or 0

            assert cancelled_job is not None
            assert cancelled_job.status is JobStatus.CANCELLED
            assert cancelled_job.lease_owner is None
            assert lease_expired_events == 2
            assert cancelled_terminal_events == 1
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())


def test_concurrent_same_key_creates_exactly_one_job_event_and_outbox(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        service = job_service(session_factory)

        try:
            records = await asyncio.gather(*(service.submit(submission()) for _ in range(12)))

            assert len({record.job_id for record in records}) == 1
            assert len({record.outbox_event_id for record in records}) == 1
            assert sum(record.created for record in records) == 1
            assert await table_counts(session_factory) == (1, 1, 1)
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())


def test_worker_lease_is_single_owner_and_old_fence_cannot_mutate(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        service = job_service(session_factory)

        try:
            submitted = await service.submit(submission(raw_key=None))

            async with session_factory.begin() as session:
                database_now = await session.scalar(select(func.clock_timestamp()))
                assert isinstance(database_now, datetime)
                dispatch_outbox = await session.get(
                    OutboxEvent,
                    submitted.outbox_event_id,
                )
                assert dispatch_outbox is not None
                dispatch_outbox.status = OutboxStatus.PUBLISHED
                dispatch_outbox.published_at = database_now
                dispatch_outbox.terminal_at = database_now

            with pytest.raises(JobNotAcquirableError):
                await service.acquire(
                    AcquireJobCommand(
                        job_id=submitted.job_id,
                        dispatch_generation=2,
                        worker_id="stale-delivery-worker",
                    )
                )
            with pytest.raises(JobNotAcquirableError):
                await service.acquire(
                    AcquireJobCommand(
                        job_id=submitted.job_id,
                        dispatch_generation=1,
                        worker_id="mismatched-trace-worker",
                        outbox_id=submitted.outbox_event_id,
                        trace_id=TraceId("wrong-trace"),
                    )
                )

            results = await asyncio.gather(
                service.acquire(
                    AcquireJobCommand(
                        job_id=submitted.job_id,
                        dispatch_generation=1,
                        worker_id="worker-one",
                        outbox_id=submitted.outbox_event_id,
                        trace_id=TraceId("job-integration-trace"),
                    )
                ),
                service.acquire(
                    AcquireJobCommand(
                        job_id=submitted.job_id,
                        dispatch_generation=1,
                        worker_id="worker-two",
                        outbox_id=submitted.outbox_event_id,
                        trace_id=TraceId("job-integration-trace"),
                    )
                ),
                return_exceptions=True,
            )
            acquired = [result for result in results if isinstance(result, AcquiredJob)]

            assert len(acquired) == 1
            assert sum(isinstance(result, JobNotAcquirableError) for result in results) == 1
            winner = acquired[0]
            old_proof = winner.lease_proof

            async with session_factory.begin() as session:
                await session.execute(
                    update(Job)
                    .where(Job.id == submitted.job_id)
                    .values(fencing_token=Job.fencing_token + 1)
                )

            with pytest.raises(LostJobLeaseError):
                await service.heartbeat(HeartbeatJobCommand(proof=old_proof))
            with pytest.raises(LostJobLeaseError):
                await service.finish(
                    FinishJobCommand(
                        proof=old_proof,
                        outcome=JobStatus.SUCCEEDED,
                        result={"documents": 4},
                    )
                )

            current_proof = JobLeaseProof(
                job_id=old_proof.job_id,
                owner=old_proof.owner,
                lease_token=old_proof.lease_token,
                fencing_token=old_proof.fencing_token + 1,
            )
            await service.checkpoint(
                CheckpointJobCommand(
                    proof=current_proof,
                    stage_name="persisted",
                    stage_sequence=winner.stage_sequence + 1,
                )
            )
            await service.checkpoint(
                CheckpointJobCommand(
                    proof=current_proof,
                    stage_name="persisted",
                    stage_sequence=winner.stage_sequence + 1,
                )
            )
            with pytest.raises(LostJobLeaseError):
                await service.checkpoint(
                    CheckpointJobCommand(
                        proof=current_proof,
                        stage_name="conflicting-stage",
                        stage_sequence=winner.stage_sequence + 1,
                    )
                )
            await service.finish(
                FinishJobCommand(
                    proof=current_proof,
                    outcome=JobStatus.SUCCEEDED,
                    result={"documents": 4},
                )
            )
            with pytest.raises(LostJobLeaseError):
                await service.finish(
                    FinishJobCommand(
                        proof=current_proof,
                        outcome=JobStatus.SUCCEEDED,
                        result={"documents": 4},
                    )
                )

            async with session_factory() as session:
                job = await session.get(Job, submitted.job_id)
                terminal_count = (
                    await session.scalar(
                        select(func.count())
                        .select_from(JobEvent)
                        .where(JobEvent.event_type == JobEventType.SUCCEEDED)
                    )
                ) or 0

            assert job is not None
            assert job.status is JobStatus.SUCCEEDED
            assert job.result == {"documents": 4}
            assert job.attempt_count == 1
            assert job.generation == 1
            assert job.fencing_token == 2
            assert job.lease_owner is None
            assert terminal_count == 1
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())


def test_retry_generation_outbox_and_bounded_dead_letter_are_atomic(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        service = job_service(session_factory)

        try:
            retryable = await service.submit(submission(raw_key=None, max_attempts=2))
            first_attempt = await service.acquire(
                AcquireJobCommand(
                    job_id=retryable.job_id,
                    dispatch_generation=1,
                    worker_id="retry-worker-one",
                )
            )
            scheduled = await service.retry(
                RetryJobCommand(
                    proof=first_attempt.lease_proof,
                    error_code=JobExecutionErrorCode.CLEANUP_UNAVAILABLE,
                    retry_delay_seconds=2,
                )
            )

            assert scheduled.disposition is JobRetryDisposition.RETRY_SCHEDULED
            assert scheduled.dispatch_generation == 2
            assert scheduled.outbox_event_id is not None

            async with session_factory.begin() as session:
                database_now = await session.scalar(select(func.clock_timestamp()))
                assert isinstance(database_now, datetime)
                initial_outbox = await session.get(
                    OutboxEvent,
                    retryable.outbox_event_id,
                )
                retry_outbox = await session.get(
                    OutboxEvent,
                    scheduled.outbox_event_id,
                )
                assert initial_outbox is not None
                assert retry_outbox is not None
                initial_outbox.status = OutboxStatus.PUBLISHED
                initial_outbox.published_at = database_now
                initial_outbox.terminal_at = database_now
                retry_outbox.max_attempts = 1
                retry_outbox.next_attempt_at = AVAILABLE_AT

            outbox_transactions = SqlAlchemyOutboxTransactionFactory(session_factory)
            async with outbox_transactions() as writer:
                claims = await writer.claim_job_dispatches(
                    ClaimOutboxCommand(
                        dispatcher_id="retry-test-dispatcher",
                        batch_size=10,
                        claim_seconds=60,
                    )
                )

            assert len(claims) == 1
            assert claims[0].proof.outbox_id == scheduled.outbox_event_id
            async with outbox_transactions() as writer:
                publish_failure = await writer.mark_failed(
                    claims[0].proof,
                    error_code=OutboxPublishErrorCode.CELERY_PUBLISH_FAILED,
                    retry_delay_seconds=2,
                )

            assert publish_failure is OutboxFailureDisposition.DEAD_LETTER

            publishable = await service.submit(submission(raw_key=None, max_attempts=2))
            publishable_attempt = await service.acquire(
                AcquireJobCommand(
                    job_id=publishable.job_id,
                    dispatch_generation=1,
                    worker_id="retry-worker-publishable",
                )
            )
            publishable_retry = await service.retry(
                RetryJobCommand(
                    proof=publishable_attempt.lease_proof,
                    error_code=JobExecutionErrorCode.CLEANUP_UNAVAILABLE,
                    retry_delay_seconds=2,
                )
            )
            assert publishable_retry.outbox_event_id is not None

            async with session_factory.begin() as session:
                database_now = await session.scalar(select(func.clock_timestamp()))
                assert isinstance(database_now, datetime)
                publishable_initial_outbox = await session.get(
                    OutboxEvent,
                    publishable.outbox_event_id,
                )
                publishable_retry_outbox = await session.get(
                    OutboxEvent,
                    publishable_retry.outbox_event_id,
                )
                assert publishable_initial_outbox is not None
                assert publishable_retry_outbox is not None
                publishable_initial_outbox.status = OutboxStatus.PUBLISHED
                publishable_initial_outbox.published_at = database_now
                publishable_initial_outbox.terminal_at = database_now
                publishable_retry_outbox.next_attempt_at = AVAILABLE_AT

            async with outbox_transactions() as writer:
                publishable_claims = await writer.claim_job_dispatches(
                    ClaimOutboxCommand(
                        dispatcher_id="retry-publish-dispatcher",
                        batch_size=10,
                        claim_seconds=60,
                    )
                )
            assert len(publishable_claims) == 1
            async with outbox_transactions() as writer:
                retained = await writer.mark_published(publishable_claims[0].proof)
            assert retained is True

            async with session_factory() as session:
                published_retry_job = await session.get(Job, publishable.job_id)
                dispatched_event_count = (
                    await session.scalar(
                        select(func.count())
                        .select_from(JobEvent)
                        .where(
                            JobEvent.job_id == publishable.job_id,
                            JobEvent.event_type == JobEventType.DISPATCHED,
                            JobEvent.dispatch_generation == 2,
                        )
                    )
                ) or 0
            assert published_retry_job is not None
            assert published_retry_job.status is JobStatus.DISPATCHED
            assert dispatched_event_count == 1

            exhausted = await service.submit(submission(raw_key=None, max_attempts=1))
            final_attempt = await service.acquire(
                AcquireJobCommand(
                    job_id=exhausted.job_id,
                    dispatch_generation=1,
                    worker_id="retry-worker-final",
                )
            )
            exhausted_record = await service.retry(
                RetryJobCommand(
                    proof=final_attempt.lease_proof,
                    error_code=JobExecutionErrorCode.SOFT_TIME_LIMIT_EXCEEDED,
                    retry_delay_seconds=2,
                )
            )

            assert exhausted_record.disposition is JobRetryDisposition.DEAD_LETTER
            assert exhausted_record.outbox_event_id is None

            async with session_factory() as session:
                retry_job = await session.get(Job, retryable.job_id)
                failed_retry_outbox = await session.get(
                    OutboxEvent,
                    scheduled.outbox_event_id,
                )
                exhausted_job = await session.get(Job, exhausted.job_id)
                retry_terminal_count = (
                    await session.scalar(
                        select(func.count())
                        .select_from(JobEvent)
                        .where(
                            JobEvent.job_id == retryable.job_id,
                            JobEvent.event_type == JobEventType.DEAD_LETTER,
                        )
                    )
                ) or 0
                exhausted_terminal_count = (
                    await session.scalar(
                        select(func.count())
                        .select_from(JobEvent)
                        .where(
                            JobEvent.job_id == exhausted.job_id,
                            JobEvent.event_type == JobEventType.DEAD_LETTER,
                        )
                    )
                ) or 0
                exhausted_outbox_count = (
                    await session.scalar(
                        select(func.count())
                        .select_from(OutboxEvent)
                        .where(OutboxEvent.source_job_id == exhausted.job_id)
                    )
                ) or 0

            assert retry_job is not None
            assert retry_job.status is JobStatus.DEAD_LETTER
            assert retry_job.dispatch_generation == 2
            assert retry_job.lease_owner is None
            assert failed_retry_outbox is not None
            assert failed_retry_outbox.status is OutboxStatus.DEAD_LETTER
            assert retry_terminal_count == 1

            assert exhausted_job is not None
            assert exhausted_job.status is JobStatus.DEAD_LETTER
            assert exhausted_job.last_error_code == (
                JobExecutionErrorCode.SOFT_TIME_LIMIT_EXCEEDED.value
            )
            assert exhausted_job.lease_owner is None
            assert exhausted_terminal_count == 1
            assert exhausted_outbox_count == 1
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())
