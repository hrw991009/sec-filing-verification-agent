"""Application orchestration for durable job submission and fenced workers."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from industry_platform.modules.jobs.domain import (
    AcquiredJob,
    AcquireJobCommand,
    CheckpointJobCommand,
    EnsuredSchedule,
    FinishJobCommand,
    HeartbeatJobCommand,
    JobNotAcquirableError,
    JobReconciliationResult,
    JobRetryRecord,
    JobSubmissionRecord,
    LostJobLeaseError,
    ManualScheduleTriggerCommand,
    ManualScheduleTriggerResult,
    PreparedJobSubmission,
    ReconcileJobsCommand,
    RetryJobCommand,
    ScheduleDefinition,
    ScheduleTickCommand,
    ScheduleTickResult,
    SubmitJobCommand,
    fingerprint_job_request,
    hash_job_idempotency_key,
    require_utc,
)
from industry_platform.modules.jobs.ports import (
    JobTransactionFactory,
    ScheduleTransactionFactory,
)

type UtcClock = Callable[[], datetime]
type JobIdSource = Callable[[], UUID]


def utc_now() -> datetime:
    """Return an aware UTC timestamp through an injectable boundary."""

    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class JobApplicationService:
    """Commit each producer or worker operation before reporting success."""

    transaction_factory: JobTransactionFactory
    lease_seconds: int
    clock: UtcClock = utc_now
    id_source: JobIdSource = uuid4

    def __post_init__(self) -> None:
        if self.lease_seconds < 1:
            raise ValueError("Job lease duration must be positive")

    async def submit(self, command: SubmitJobCommand) -> JobSubmissionRecord:
        definition = command.definition
        raw_key = definition.idempotency_key
        idempotency_hash = hash_job_idempotency_key(raw_key) if raw_key is not None else None
        request_fingerprint = fingerprint_job_request(definition) if raw_key is not None else None
        now = self._now()
        prepared = PreparedJobSubmission(
            job_id=self._new_id(),
            outbox_event_id=self._new_id(),
            scope=definition.scope,
            task_name=definition.task_name,
            queue_name=definition.queue_name,
            payload=definition.payload,
            available_at=definition.available_at,
            max_attempts=definition.max_attempts,
            priority=definition.priority,
            soft_time_limit_seconds=definition.soft_time_limit_seconds,
            hard_time_limit_seconds=definition.hard_time_limit_seconds,
            trace_id=command.trace_id,
            idempotency_key_hash=idempotency_hash,
            request_fingerprint=request_fingerprint,
            submitted_at=now,
        )

        async with self.transaction_factory() as writer:
            record = await writer.submit(prepared)

        return record

    async def acquire(self, command: AcquireJobCommand) -> AcquiredJob:
        async with self.transaction_factory() as writer:
            acquired = await writer.acquire(
                command,
                lease_token=self._new_id(),
                lease_seconds=self.lease_seconds,
            )

        if acquired is None:
            raise JobNotAcquirableError
        return acquired

    async def heartbeat(self, command: HeartbeatJobCommand) -> None:
        async with self.transaction_factory() as writer:
            retained = await writer.heartbeat(
                command,
                lease_seconds=self.lease_seconds,
            )

        if not retained:
            raise LostJobLeaseError

    async def checkpoint(self, command: CheckpointJobCommand) -> None:
        async with self.transaction_factory() as writer:
            retained = await writer.checkpoint(
                command,
                lease_seconds=self.lease_seconds,
            )

        if not retained:
            raise LostJobLeaseError

    async def finish(self, command: FinishJobCommand) -> None:
        async with self.transaction_factory() as writer:
            retained = await writer.finish(command)

        if not retained:
            raise LostJobLeaseError

    async def retry(self, command: RetryJobCommand) -> JobRetryRecord:
        """Atomically schedule the next delivery or exhaust the logical job."""

        async with self.transaction_factory() as writer:
            record = await writer.retry(
                command,
                outbox_event_id=self._new_id(),
            )

        if record is None:
            raise LostJobLeaseError
        return record

    def _now(self) -> datetime:
        now = self.clock()
        require_utc(now, field_name="clock")
        return now

    def _new_id(self) -> UUID:
        identifier = self.id_source()
        if identifier.int == 0:
            raise ValueError("Job identifier source returned a nil UUID")
        return identifier


@dataclass(frozen=True, slots=True)
class JobReconciliationService:
    """Run bounded recovery transactions independently from workers and broker."""

    transaction_factory: JobTransactionFactory
    unstarted_timeout_seconds: int
    batch_size: int
    id_source: JobIdSource = uuid4

    def __post_init__(self) -> None:
        ReconcileJobsCommand(
            unstarted_timeout_seconds=self.unstarted_timeout_seconds,
            batch_size=self.batch_size,
        )

    async def reconcile_once(self) -> JobReconciliationResult:
        command = ReconcileJobsCommand(
            unstarted_timeout_seconds=self.unstarted_timeout_seconds,
            batch_size=self.batch_size,
        )
        outbox_event_ids = tuple(self._new_id() for _ in range(command.batch_size))
        async with self.transaction_factory() as writer:
            return await writer.reconcile(
                command,
                outbox_event_ids=outbox_event_ids,
            )

    def _new_id(self) -> UUID:
        identifier = self.id_source()
        if identifier.int == 0:
            raise ValueError("Job identifier source returned a nil UUID")
        return identifier


@dataclass(frozen=True, slots=True)
class ScheduleApplicationService:
    """Use the same transaction boundary for Beat and manual schedule triggers."""

    transaction_factory: ScheduleTransactionFactory
    batch_size: int = 5

    def __post_init__(self) -> None:
        ScheduleTickCommand(batch_size=self.batch_size)

    async def ensure_schedule(
        self,
        definition: ScheduleDefinition,
    ) -> EnsuredSchedule:
        async with self.transaction_factory() as writer:
            return await writer.ensure_schedule(definition)

    async def run_due_once(self) -> ScheduleTickResult:
        command = ScheduleTickCommand(batch_size=self.batch_size)
        async with self.transaction_factory() as writer:
            return await writer.materialize_due(command)

    async def trigger_manual(
        self,
        command: ManualScheduleTriggerCommand,
    ) -> ManualScheduleTriggerResult:
        async with self.transaction_factory() as writer:
            return await writer.trigger_manual(command)
