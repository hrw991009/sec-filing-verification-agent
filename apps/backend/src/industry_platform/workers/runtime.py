"""Fenced execution runtime and the fixed production job-handler registry."""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol
from uuid import uuid4

from industry_platform.core.config import Settings
from industry_platform.core.database import (
    create_database_engine,
    create_database_session_factory,
)
from industry_platform.modules.identity.adapters.refresh_cleanup import (
    SqlAlchemyRefreshRecoveryCleanupTransactionFactory,
)
from industry_platform.modules.identity.domain import (
    RefreshRecoveryCleanupCommand,
    RefreshRecoveryCleanupUnavailableError,
)
from industry_platform.modules.identity.ports import RefreshRecoveryCleanupUseCase
from industry_platform.modules.identity.service import RefreshRecoveryCleanupService
from industry_platform.modules.jobs.domain import (
    AcquiredJob,
    AcquireJobCommand,
    FinishJobCommand,
    HeartbeatJobCommand,
    JobDispatchMessage,
    JobExecutionErrorCode,
    JobNotAcquirableError,
    JobPersistenceError,
    JobRetryDisposition,
    JobStatus,
    LostJobLeaseError,
    RetryJobCommand,
    job_retry_delay_seconds,
)
from industry_platform.modules.jobs.ports import JobApplicationUseCase
from industry_platform.modules.jobs.resources import create_job_resources

IDENTITY_REFRESH_RECOVERY_CLEANUP_HANDLER = "identity.refresh_recovery.cleanup.v1"


class JobExecutionDisposition(StrEnum):
    """Payload-free result returned to the Celery acknowledgement boundary."""

    NO_OP = "no_op"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRY_SCHEDULED = "retry_scheduled"
    DEAD_LETTER = "dead_letter"
    LEASE_LOST = "lease_lost"


class JobHandler(Protocol):
    """One statically registered business handler."""

    async def execute(
        self,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]: ...


class UnknownJobHandlerError(RuntimeError):
    """A persisted task name has no production handler in this release."""


class InvalidJobPayloadError(RuntimeError):
    """A persisted payload does not match its handler's bounded schema."""


class RetryableJobHandlerError(RuntimeError):
    """Carry only a stable retry code across the handler boundary."""

    def __init__(self, error_code: JobExecutionErrorCode) -> None:
        super().__init__("Job handler infrastructure is unavailable")
        self.error_code = error_code


@dataclass(frozen=True, slots=True)
class IdentityRefreshRecoveryCleanupHandler:
    """Parse one bounded cleanup batch and invoke the existing identity use case."""

    cleanup_use_case: RefreshRecoveryCleanupUseCase = field(repr=False)

    async def execute(
        self,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        if set(payload) != {"batch_size"}:
            raise InvalidJobPayloadError

        batch_size = payload["batch_size"]
        if isinstance(batch_size, bool) or not isinstance(batch_size, int):
            raise InvalidJobPayloadError

        try:
            command = RefreshRecoveryCleanupCommand(
                batch_size=batch_size,
            )
        except (KeyError, TypeError, ValueError):
            raise InvalidJobPayloadError from None

        try:
            result = await self.cleanup_use_case.cleanup_expired(command)
        except RefreshRecoveryCleanupUnavailableError:
            raise RetryableJobHandlerError(JobExecutionErrorCode.CLEANUP_UNAVAILABLE) from None

        return {
            "scanned_count": result.scanned_count,
            "cleared_count": result.cleared_count,
        }


@dataclass(frozen=True, slots=True)
class FixedJobHandlerRegistry:
    """Immutable allowlist; persisted names never trigger dynamic imports."""

    _handlers: Mapping[str, JobHandler] = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_handlers", MappingProxyType(dict(self._handlers)))

    @classmethod
    def production(
        cls,
        cleanup_use_case: RefreshRecoveryCleanupUseCase,
    ) -> "FixedJobHandlerRegistry":
        return cls(
            {
                IDENTITY_REFRESH_RECOVERY_CLEANUP_HANDLER: (
                    IdentityRefreshRecoveryCleanupHandler(cleanup_use_case)
                )
            }
        )

    def resolve(self, task_name: str) -> JobHandler:
        handler = self._handlers.get(task_name)
        if handler is None:
            raise UnknownJobHandlerError
        return handler


@dataclass(frozen=True, slots=True)
class JobExecutionRuntime:
    """Acquire from PostgreSQL, heartbeat independently, then settle by fence."""

    jobs: JobApplicationUseCase = field(repr=False)
    handlers: FixedJobHandlerRegistry = field(repr=False)
    worker_id: str
    heartbeat_seconds: float

    def __post_init__(self) -> None:
        if self.heartbeat_seconds <= 0:
            raise ValueError("Job heartbeat interval must be positive")

    async def execute(
        self,
        delivery: JobDispatchMessage,
    ) -> JobExecutionDisposition:
        try:
            acquired = await self.jobs.acquire(
                AcquireJobCommand(
                    job_id=delivery.job_id,
                    dispatch_generation=delivery.dispatch_generation,
                    worker_id=self.worker_id,
                    outbox_id=delivery.outbox_id,
                    trace_id=delivery.trace_id,
                )
            )
        except JobNotAcquirableError:
            return JobExecutionDisposition.NO_OP

        try:
            async with asyncio.timeout(acquired.soft_time_limit_seconds):
                result = await self._execute_with_heartbeat(acquired)
        except LostJobLeaseError:
            return JobExecutionDisposition.LEASE_LOST
        except TimeoutError:
            return await self._retry(
                acquired,
                JobExecutionErrorCode.SOFT_TIME_LIMIT_EXCEEDED,
            )
        except RetryableJobHandlerError as error:
            return await self._retry(acquired, error.error_code)
        except UnknownJobHandlerError:
            return await self._fail(
                acquired,
                JobExecutionErrorCode.UNKNOWN_HANDLER,
            )
        except InvalidJobPayloadError:
            return await self._fail(
                acquired,
                JobExecutionErrorCode.INVALID_PAYLOAD,
            )
        except JobPersistenceError:
            # The lease state is unknown; a later reconciler must decide recovery.
            raise
        except Exception:
            return await self._fail(
                acquired,
                JobExecutionErrorCode.HANDLER_FAILED,
            )

        try:
            await self.jobs.finish(
                FinishJobCommand(
                    proof=acquired.lease_proof,
                    outcome=JobStatus.SUCCEEDED,
                    result=result,
                )
            )
        except LostJobLeaseError:
            return JobExecutionDisposition.LEASE_LOST
        return JobExecutionDisposition.SUCCEEDED

    async def _execute_with_heartbeat(
        self,
        acquired: AcquiredJob,
    ) -> Mapping[str, object]:
        stop = asyncio.Event()
        handler_task = asyncio.create_task(self._invoke_handler(acquired))
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(acquired, stop))

        try:
            done, _ = await asyncio.wait(
                (handler_task, heartbeat_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat_task in done:
                await heartbeat_task
                raise LostJobLeaseError

            result = await handler_task
            stop.set()
            await heartbeat_task
            return result
        finally:
            stop.set()
            for task in (handler_task, heartbeat_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(handler_task, heartbeat_task, return_exceptions=True)

    async def _invoke_handler(self, acquired: AcquiredJob) -> Mapping[str, object]:
        handler = self.handlers.resolve(acquired.task_name)
        return await handler.execute(acquired.payload)

    async def _heartbeat_loop(
        self,
        acquired: AcquiredJob,
        stop: asyncio.Event,
    ) -> None:
        while True:
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=self.heartbeat_seconds,
                )
            except TimeoutError:
                await self.jobs.heartbeat(HeartbeatJobCommand(proof=acquired.lease_proof))
            else:
                return

    async def _fail(
        self,
        acquired: AcquiredJob,
        error_code: JobExecutionErrorCode,
    ) -> JobExecutionDisposition:
        try:
            await self.jobs.finish(
                FinishJobCommand(
                    proof=acquired.lease_proof,
                    outcome=JobStatus.FAILED,
                    error_code=error_code.value,
                )
            )
        except LostJobLeaseError:
            return JobExecutionDisposition.LEASE_LOST
        return JobExecutionDisposition.FAILED

    async def _retry(
        self,
        acquired: AcquiredJob,
        error_code: JobExecutionErrorCode,
    ) -> JobExecutionDisposition:
        try:
            record = await self.jobs.retry(
                RetryJobCommand(
                    proof=acquired.lease_proof,
                    error_code=error_code,
                    retry_delay_seconds=job_retry_delay_seconds(
                        acquired.job_id,
                        acquired.attempt_count,
                    ),
                )
            )
        except LostJobLeaseError:
            return JobExecutionDisposition.LEASE_LOST

        if record.disposition is JobRetryDisposition.RETRY_SCHEDULED:
            return JobExecutionDisposition.RETRY_SCHEDULED
        return JobExecutionDisposition.DEAD_LETTER


async def run_job_delivery(
    delivery: JobDispatchMessage,
    *,
    settings: Settings,
) -> JobExecutionDisposition:
    """Compose fresh task resources; every operation borrows its own session."""

    engine = create_database_engine(settings)
    try:
        session_factory = create_database_session_factory(engine)
        cleanup_service = RefreshRecoveryCleanupService(
            transaction_factory=SqlAlchemyRefreshRecoveryCleanupTransactionFactory(session_factory)
        )
        runtime = JobExecutionRuntime(
            jobs=create_job_resources(
                settings,
                session_factory,
            ).application_service,
            handlers=FixedJobHandlerRegistry.production(cleanup_service),
            worker_id=f"celery-{uuid4().hex}",
            heartbeat_seconds=settings.job_heartbeat_seconds,
        )
        return await runtime.execute(delivery)
    finally:
        await engine.dispose()
