"""Technology-independent ports for durable job submission and execution."""

from contextlib import AbstractAsyncContextManager
from typing import Protocol
from uuid import UUID

from industry_platform.modules.jobs.domain import (
    AcquiredJob,
    AcquireJobCommand,
    CheckpointJobCommand,
    ClaimedJobDispatch,
    ClaimOutboxCommand,
    EnsuredSchedule,
    FinishJobCommand,
    HeartbeatJobCommand,
    JobReconciliationResult,
    JobRetryRecord,
    JobSubmissionRecord,
    ManualScheduleTriggerCommand,
    ManualScheduleTriggerResult,
    OutboxClaimProof,
    OutboxFailureDisposition,
    OutboxPublishErrorCode,
    PreparedJobSubmission,
    ReconcileJobsCommand,
    RetryJobCommand,
    ScheduleDefinition,
    ScheduleTickCommand,
    ScheduleTickResult,
    SubmitJobCommand,
)


class JobWriter(Protocol):
    """Atomic PostgreSQL mutations available inside one job transaction."""

    async def submit(self, prepared: PreparedJobSubmission) -> JobSubmissionRecord:
        """Create Job, created Event, and generation-one Outbox or reuse it."""

        ...

    async def acquire(
        self,
        command: AcquireJobCommand,
        *,
        lease_token: UUID,
        lease_seconds: int,
    ) -> AcquiredJob | None:
        """Atomically grant one live lease to at most one matching worker."""

        ...

    async def heartbeat(
        self,
        command: HeartbeatJobCommand,
        *,
        lease_seconds: int,
    ) -> bool:
        """CAS-refresh a live lease and return whether ownership still matched."""

        ...

    async def checkpoint(
        self,
        command: CheckpointJobCommand,
        *,
        lease_seconds: int,
    ) -> bool:
        """CAS-advance the visible stage under the same live lease."""

        ...

    async def finish(self, command: FinishJobCommand) -> bool:
        """CAS-write one terminal state and its unique terminal event."""

        ...

    async def retry(
        self,
        command: RetryJobCommand,
        *,
        outbox_event_id: UUID,
    ) -> JobRetryRecord | None:
        """CAS-settle retry/dead-letter and create any next outbox atomically."""

        ...

    async def reconcile(
        self,
        command: ReconcileJobsCommand,
        *,
        outbox_event_ids: tuple[UUID, ...],
    ) -> JobReconciliationResult:
        """Lock and recover one bounded batch using PostgreSQL time."""

        ...


class JobTransactionFactory(Protocol):
    """Open a transaction that commits before its context exits."""

    def __call__(self) -> AbstractAsyncContextManager[JobWriter]:
        """Return one all-or-nothing PostgreSQL job transaction."""

        ...


class JobApplicationUseCase(Protocol):
    """Application boundary for trusted producers and worker adapters."""

    async def submit(self, command: SubmitJobCommand) -> JobSubmissionRecord: ...

    async def acquire(self, command: AcquireJobCommand) -> AcquiredJob: ...

    async def heartbeat(self, command: HeartbeatJobCommand) -> None: ...

    async def checkpoint(self, command: CheckpointJobCommand) -> None: ...

    async def finish(self, command: FinishJobCommand) -> None: ...

    async def retry(self, command: RetryJobCommand) -> JobRetryRecord: ...


class JobReconciliationUseCase(Protocol):
    """Application boundary for the independent reconciliation process."""

    async def reconcile_once(self) -> JobReconciliationResult: ...


class ScheduleWriter(Protocol):
    """Atomic schedule, occurrence, Job, Event, and Outbox mutations."""

    async def ensure_schedule(
        self,
        definition: ScheduleDefinition,
    ) -> EnsuredSchedule: ...

    async def materialize_due(
        self,
        command: ScheduleTickCommand,
    ) -> ScheduleTickResult: ...

    async def trigger_manual(
        self,
        command: ManualScheduleTriggerCommand,
    ) -> ManualScheduleTriggerResult: ...


class ScheduleTransactionFactory(Protocol):
    """Open one all-or-nothing PostgreSQL scheduling transaction."""

    def __call__(self) -> AbstractAsyncContextManager[ScheduleWriter]: ...


class ScheduleApplicationUseCase(Protocol):
    """Shared trusted boundary used by Beat and future manual-trigger APIs."""

    async def ensure_schedule(
        self,
        definition: ScheduleDefinition,
    ) -> EnsuredSchedule: ...

    async def run_due_once(self) -> ScheduleTickResult: ...

    async def trigger_manual(
        self,
        command: ManualScheduleTriggerCommand,
    ) -> ManualScheduleTriggerResult: ...


class OutboxWriter(Protocol):
    """Short transactional mutations used around broker publication."""

    async def claim_job_dispatches(
        self,
        command: ClaimOutboxCommand,
    ) -> tuple[ClaimedJobDispatch, ...]:
        """Claim due or expired-lock rows with PostgreSQL skip-locked semantics."""

        ...

    async def mark_published(self, proof: OutboxClaimProof) -> bool:
        """CAS-settle an owned claim after the broker accepts its message."""

        ...

    async def mark_failed(
        self,
        proof: OutboxClaimProof,
        *,
        error_code: OutboxPublishErrorCode,
        retry_delay_seconds: int,
    ) -> OutboxFailureDisposition:
        """CAS-release for retry or atomically dead-letter an exhausted claim."""

        ...


class OutboxTransactionFactory(Protocol):
    """Open and commit one bounded outbox claim or settlement transaction."""

    def __call__(self) -> AbstractAsyncContextManager[OutboxWriter]: ...


class JobDispatchPublisher(Protocol):
    """Publish one fixed-shape generic job delivery outside PostgreSQL."""

    async def publish(self, dispatch: ClaimedJobDispatch) -> None: ...
