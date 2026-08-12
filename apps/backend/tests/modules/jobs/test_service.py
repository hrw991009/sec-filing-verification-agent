"""Tests for job application orchestration and persistence-safe hashing."""

from collections.abc import Iterator
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from types import TracebackType
from typing import cast
from uuid import UUID

import pytest

from industry_platform.core.config import Settings
from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.jobs.domain import (
    AcquiredJob,
    AcquireJobCommand,
    CheckpointJobCommand,
    ExecutionScope,
    FinishJobCommand,
    HeartbeatJobCommand,
    JobDefinition,
    JobExecutionErrorCode,
    JobLeaseProof,
    JobNotAcquirableError,
    JobPersistenceError,
    JobReconciliationResult,
    JobRetryDisposition,
    JobRetryRecord,
    JobStatus,
    JobSubmissionRecord,
    LostJobLeaseError,
    PreparedJobSubmission,
    ReconcileJobsCommand,
    RetryJobCommand,
    SubmitJobCommand,
    fingerprint_job_request,
    hash_job_idempotency_key,
)
from industry_platform.modules.jobs.ports import JobWriter
from industry_platform.modules.jobs.resources import create_job_resources
from industry_platform.modules.jobs.service import (
    JobApplicationService,
    JobReconciliationService,
)

JOB_ID = UUID("11111111-1111-4111-8111-111111111111")
OUTBOX_ID = UUID("22222222-2222-4222-8222-222222222222")
LEASE_TOKEN = UUID("33333333-3333-4333-8333-333333333333")
RECONCILE_OUTBOX_ID = UUID("44444444-4444-4444-8444-444444444444")
RECONCILE_SPARE_ID = UUID("55555555-5555-4555-8555-555555555555")
NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
RAW_KEY = "customer-visible-retry-key"


class RecordingJobWriter:
    """Record persistence-safe arguments crossing the application port."""

    def __init__(self) -> None:
        self.prepared: list[PreparedJobSubmission] = []
        self.acquired: AcquiredJob | None = None
        self.retains_lease = True
        self.retries: list[tuple[RetryJobCommand, UUID]] = []
        self.reconciliations: list[tuple[ReconcileJobsCommand, tuple[UUID, ...]]] = []

    async def submit(self, prepared: PreparedJobSubmission) -> JobSubmissionRecord:
        self.prepared.append(prepared)
        return JobSubmissionRecord(
            job_id=JOB_ID,
            outbox_event_id=OUTBOX_ID,
            status=JobStatus.PENDING,
            dispatch_generation=1,
            created=True,
        )

    async def acquire(
        self,
        command: AcquireJobCommand,
        *,
        lease_token: UUID,
        lease_seconds: int,
    ) -> AcquiredJob | None:
        del command, lease_token, lease_seconds
        return self.acquired

    async def heartbeat(
        self,
        command: HeartbeatJobCommand,
        *,
        lease_seconds: int,
    ) -> bool:
        del command, lease_seconds
        return self.retains_lease

    async def checkpoint(
        self,
        command: CheckpointJobCommand,
        *,
        lease_seconds: int,
    ) -> bool:
        del command, lease_seconds
        return self.retains_lease

    async def finish(self, command: FinishJobCommand) -> bool:
        del command
        return self.retains_lease

    async def retry(
        self,
        command: RetryJobCommand,
        *,
        outbox_event_id: UUID,
    ) -> JobRetryRecord | None:
        self.retries.append((command, outbox_event_id))
        if not self.retains_lease:
            return None
        return JobRetryRecord(
            disposition=JobRetryDisposition.RETRY_SCHEDULED,
            dispatch_generation=2,
            outbox_event_id=outbox_event_id,
        )

    async def reconcile(
        self,
        command: ReconcileJobsCommand,
        *,
        outbox_event_ids: tuple[UUID, ...],
    ) -> JobReconciliationResult:
        self.reconciliations.append((command, outbox_event_ids))
        return JobReconciliationResult(
            selected=1,
            unstarted=1,
            expired_leases=0,
            retry_scheduled=1,
            cancelled=0,
            dead_lettered=0,
        )


class RecordingTransaction(AbstractAsyncContextManager[JobWriter]):
    """Expose commit ordering and optionally fail while exiting."""

    def __init__(
        self,
        writer: RecordingJobWriter,
        events: list[str],
        *,
        fail_commit: bool,
    ) -> None:
        self.writer = writer
        self.events = events
        self.fail_commit = fail_commit

    async def __aenter__(self) -> JobWriter:
        self.events.append("transaction.enter")
        return self.writer

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self.events.append("transaction.exit")
        if self.fail_commit:
            raise JobPersistenceError


class RecordingTransactionFactory:
    """Create a fresh observable context around one shared test writer."""

    def __init__(self, writer: RecordingJobWriter, *, fail_commit: bool = False) -> None:
        self.writer = writer
        self.fail_commit = fail_commit
        self.events: list[str] = []

    def __call__(self) -> RecordingTransaction:
        self.events.append("transaction.create")
        return RecordingTransaction(
            self.writer,
            self.events,
            fail_commit=self.fail_commit,
        )


def fixed_ids(*values: UUID) -> Iterator[UUID]:
    """Return deterministic UUIDs in the order requested by the service."""

    yield from values


def job_definition(*, payload: dict[str, object] | None = None) -> JobDefinition:
    return JobDefinition(
        scope=ExecutionScope(system_scope_key="test-suite"),
        task_name="research.collect",
        queue_name="default",
        payload=payload if payload is not None else {"source": "sensitive-business-value"},
        available_at=NOW,
        max_attempts=3,
        idempotency_key=RAW_KEY,
    )


@pytest.mark.asyncio
async def test_submit_hashes_raw_key_and_commits_before_returning() -> None:
    writer = RecordingJobWriter()
    factory = RecordingTransactionFactory(writer)
    identifiers = fixed_ids(JOB_ID, OUTBOX_ID)
    service = JobApplicationService(
        transaction_factory=factory,
        lease_seconds=120,
        clock=lambda: NOW,
        id_source=lambda: next(identifiers),
    )
    command = SubmitJobCommand(
        definition=job_definition(),
        trace_id=TraceId("job-submit-trace"),
    )

    record = await service.submit(command)

    assert record.job_id == JOB_ID
    assert factory.events == [
        "transaction.create",
        "transaction.enter",
        "transaction.exit",
    ]
    assert len(writer.prepared) == 1
    prepared = writer.prepared[0]
    assert prepared.idempotency_key_hash == hash_job_idempotency_key(RAW_KEY)
    assert prepared.request_fingerprint == fingerprint_job_request(command.definition)
    assert prepared.idempotency_key_hash is not None
    assert prepared.request_fingerprint is not None
    assert len(prepared.idempotency_key_hash) == 32
    assert len(prepared.request_fingerprint) == 32
    assert RAW_KEY not in repr(command)
    assert RAW_KEY not in repr(prepared)
    assert "sensitive-business-value" not in repr(command)
    assert "sensitive-business-value" not in repr(prepared)


def test_request_fingerprint_is_canonical_but_changes_with_semantics() -> None:
    first = job_definition(payload={"b": 2, "a": [1, "二"]})
    reordered = job_definition(payload={"a": [1, "二"], "b": 2})
    changed = job_definition(payload={"a": [1, "二"], "b": 3})

    assert fingerprint_job_request(first) == fingerprint_job_request(reordered)
    assert fingerprint_job_request(first) != fingerprint_job_request(changed)
    assert hash_job_idempotency_key(RAW_KEY) != RAW_KEY.encode()


@pytest.mark.asyncio
async def test_commit_failure_never_returns_a_submission_record() -> None:
    writer = RecordingJobWriter()
    factory = RecordingTransactionFactory(writer, fail_commit=True)
    identifiers = fixed_ids(JOB_ID, OUTBOX_ID)
    service = JobApplicationService(
        transaction_factory=factory,
        lease_seconds=120,
        clock=lambda: NOW,
        id_source=lambda: next(identifiers),
    )

    with pytest.raises(JobPersistenceError):
        await service.submit(
            SubmitJobCommand(
                definition=job_definition(),
                trace_id=TraceId("failed-commit-trace"),
            )
        )

    assert factory.events[-1] == "transaction.exit"


@pytest.mark.asyncio
async def test_stale_delivery_and_lost_lease_fail_closed() -> None:
    writer = RecordingJobWriter()
    writer.retains_lease = False
    service = JobApplicationService(
        transaction_factory=RecordingTransactionFactory(writer),
        lease_seconds=120,
        clock=lambda: NOW,
        id_source=lambda: LEASE_TOKEN,
    )
    acquire = AcquireJobCommand(
        job_id=JOB_ID,
        dispatch_generation=1,
        worker_id="worker-one",
    )
    proof = JobLeaseProof(
        job_id=JOB_ID,
        owner="worker-one",
        lease_token=LEASE_TOKEN,
        fencing_token=1,
    )

    with pytest.raises(JobNotAcquirableError):
        await service.acquire(acquire)
    with pytest.raises(LostJobLeaseError):
        await service.heartbeat(HeartbeatJobCommand(proof=proof))
    with pytest.raises(LostJobLeaseError):
        await service.retry(
            RetryJobCommand(
                proof=proof,
                error_code=JobExecutionErrorCode.CLEANUP_UNAVAILABLE,
                retry_delay_seconds=2,
            )
        )

    assert writer.retries[0][1] == LEASE_TOKEN


def test_resources_use_the_validated_lease_duration(test_settings: Settings) -> None:
    resources = create_job_resources(
        test_settings,
        cast(AsyncSessionFactory, object()),
    )

    assert isinstance(resources.application_service, JobApplicationService)
    assert resources.application_service.lease_seconds == test_settings.job_lease_seconds


@pytest.mark.asyncio
async def test_reconciliation_service_commits_one_bounded_database_scan() -> None:
    writer = RecordingJobWriter()
    factory = RecordingTransactionFactory(writer)
    identifiers = fixed_ids(RECONCILE_OUTBOX_ID, RECONCILE_SPARE_ID)
    service = JobReconciliationService(
        transaction_factory=factory,
        unstarted_timeout_seconds=300,
        batch_size=2,
        id_source=lambda: next(identifiers),
    )

    result = await service.reconcile_once()

    assert result.retry_scheduled == 1
    assert factory.events == [
        "transaction.create",
        "transaction.enter",
        "transaction.exit",
    ]
    assert writer.reconciliations == [
        (
            ReconcileJobsCommand(
                unstarted_timeout_seconds=300,
                batch_size=2,
            ),
            (RECONCILE_OUTBOX_ID, RECONCILE_SPARE_ID),
        )
    ]
