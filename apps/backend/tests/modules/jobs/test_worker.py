"""Unit contracts for fenced execution, heartbeat, handlers, and Celery shape."""

import asyncio
import inspect
import logging
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import httpx2
import pytest
from celery import Celery

from industry_platform.core.config import Settings
from industry_platform.core.database import create_database_engine, create_database_session_factory
from industry_platform.modules.agent_runtime.domain import AgentRunStatus, RunStopReason
from industry_platform.modules.agent_runtime.execution import (
    DirectAnswerExecutionResult,
    DirectAnswerRunExecutionUseCase,
)
from industry_platform.modules.conversations.domain import DIRECT_ANSWER_TASK_NAME
from industry_platform.modules.identity.domain import (
    RefreshRecoveryCleanupCommand,
    RefreshRecoveryCleanupResult,
    RefreshRecoveryCleanupUnavailableError,
    TraceId,
)
from industry_platform.modules.identity.ports import RefreshRecoveryCleanupUseCase
from industry_platform.modules.industry.domain import INDUSTRY_COLLECTION_TASK_NAME
from industry_platform.modules.ingestion.domain import DocumentParserError, ParserErrorCode
from industry_platform.modules.ingestion.service import KnowledgeIngestionService
from industry_platform.modules.jobs.domain import (
    CELERY_JOB_DISPATCH_TASK_NAME,
    AcquiredJob,
    AcquireJobCommand,
    CheckpointJobCommand,
    ExecutionScope,
    FinishJobCommand,
    HeartbeatJobCommand,
    JobDispatchMessage,
    JobExecutionErrorCode,
    JobLease,
    JobNotAcquirableError,
    JobRetryDisposition,
    JobRetryRecord,
    JobStatus,
    LostJobLeaseError,
    RetryJobCommand,
    SubmitJobCommand,
)
from industry_platform.modules.jobs.ports import JobApplicationUseCase
from industry_platform.workers.runtime import (
    IDENTITY_REFRESH_RECOVERY_CLEANUP_HANDLER,
    DirectAnswerJobHandler,
    FixedJobHandlerRegistry,
    IndustryCollectionJobHandler,
    JobExecutionDisposition,
    JobExecutionRuntime,
    KnowledgeIngestionJobHandler,
    PermanentJobHandlerError,
    RetryableJobHandlerError,
    create_job_delivery_runtime,
)
from industry_platform.workers.tasks import register_job_execution_task

JOB_ID = UUID("11111111-1111-4111-8111-111111111111")
OUTBOX_ID = UUID("22222222-2222-4222-8222-222222222222")
LEASE_TOKEN = UUID("33333333-3333-4333-8333-333333333333")
RETRY_OUTBOX_ID = UUID("44444444-4444-4444-8444-444444444444")
NOW = datetime(2026, 8, 12, 0, 0, tzinfo=UTC)
TRACE_ID = TraceId("worker-unit-trace")
SENSITIVE_VALUE = "private-cleanup-control-value"


class RecordingCleanupUseCase:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.commands: list[RefreshRecoveryCleanupCommand] = []

    async def cleanup_expired(
        self,
        command: RefreshRecoveryCleanupCommand,
    ) -> RefreshRecoveryCleanupResult:
        self.commands.append(command)
        if self.failure is not None:
            raise self.failure
        return RefreshRecoveryCleanupResult(scanned_count=3, cleared_count=2)


class BlockingCleanupUseCase:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def cleanup_expired(
        self,
        command: RefreshRecoveryCleanupCommand,
    ) -> RefreshRecoveryCleanupResult:
        del command
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class RecordingDirectAnswerUseCase:
    def __init__(self) -> None:
        self.run_ids: list[UUID] = []

    async def execute_run(self, run_id: UUID) -> DirectAnswerExecutionResult:
        self.run_ids.append(run_id)
        return DirectAnswerExecutionResult(
            run_id=run_id,
            status=AgentRunStatus.COMPLETED,
            stop_reason=RunStopReason.FINAL,
            terminal_event_sequence=11,
        )


class FailingIngestionUseCase:
    def __init__(self, code: ParserErrorCode) -> None:
        self.code = code

    async def execute(self, job: AcquiredJob) -> object:
        del job
        raise DocumentParserError(self.code)


class RecordingJobUseCase:
    def __init__(
        self,
        acquired: AcquiredJob,
        *,
        lose_heartbeat: bool = False,
        retry_disposition: JobRetryDisposition = (JobRetryDisposition.RETRY_SCHEDULED),
    ) -> None:
        self.acquired = acquired
        self.lose_heartbeat = lose_heartbeat
        self.retry_disposition = retry_disposition
        self.acquire_commands: list[AcquireJobCommand] = []
        self.heartbeat_commands: list[HeartbeatJobCommand] = []
        self.finish_commands: list[FinishJobCommand] = []
        self.retry_commands: list[RetryJobCommand] = []
        self.settled = False

    async def submit(self, command: SubmitJobCommand) -> object:
        del command
        raise AssertionError("worker must not submit jobs")

    async def acquire(self, command: AcquireJobCommand) -> AcquiredJob:
        self.acquire_commands.append(command)
        if self.settled or len(self.acquire_commands) > 1:
            raise JobNotAcquirableError
        return self.acquired

    async def heartbeat(self, command: HeartbeatJobCommand) -> None:
        self.heartbeat_commands.append(command)
        if self.lose_heartbeat:
            raise LostJobLeaseError

    async def checkpoint(self, command: CheckpointJobCommand) -> None:
        del command
        raise AssertionError("cleanup does not checkpoint")

    async def finish(self, command: FinishJobCommand) -> None:
        self.finish_commands.append(command)
        self.settled = True

    async def retry(self, command: RetryJobCommand) -> JobRetryRecord:
        self.retry_commands.append(command)
        self.settled = True
        return JobRetryRecord(
            disposition=self.retry_disposition,
            dispatch_generation=(
                self.acquired.dispatch_generation + 1
                if self.retry_disposition is JobRetryDisposition.RETRY_SCHEDULED
                else self.acquired.dispatch_generation
            ),
            outbox_event_id=(
                RETRY_OUTBOX_ID
                if self.retry_disposition is JobRetryDisposition.RETRY_SCHEDULED
                else None
            ),
        )


def delivery() -> JobDispatchMessage:
    return JobDispatchMessage(
        job_id=JOB_ID,
        dispatch_generation=1,
        outbox_id=OUTBOX_ID,
        trace_id=TRACE_ID,
    )


def acquired_job(
    *,
    task_name: str = IDENTITY_REFRESH_RECOVERY_CLEANUP_HANDLER,
    payload: dict[str, object] | None = None,
    max_attempts: int = 3,
    soft_time_limit_seconds: int = 30,
) -> AcquiredJob:
    return AcquiredJob(
        job_id=JOB_ID,
        scope=ExecutionScope(system_scope_key="worker-unit"),
        trace_id=TRACE_ID,
        task_name=task_name,
        queue_name="default",
        payload=payload if payload is not None else {"batch_size": 25},
        dispatch_generation=1,
        lease=JobLease(
            owner="worker-unit-1",
            lease_token=LEASE_TOKEN,
            generation=1,
            fencing_token=7,
            heartbeat_at=NOW,
            expires_at=NOW + timedelta(minutes=2),
        ),
        stage_sequence=1,
        attempt_count=1,
        max_attempts=max_attempts,
        soft_time_limit_seconds=soft_time_limit_seconds,
        hard_time_limit_seconds=60,
    )


def runtime(
    jobs: RecordingJobUseCase,
    cleanup: RefreshRecoveryCleanupUseCase,
    *,
    direct_answer: DirectAnswerRunExecutionUseCase | None = None,
    heartbeat_seconds: float = 60,
) -> JobExecutionRuntime:
    return JobExecutionRuntime(
        jobs=cast(JobApplicationUseCase, jobs),
        handlers=FixedJobHandlerRegistry.production(cleanup, direct_answer),
        worker_id="worker-unit-1",
        heartbeat_seconds=heartbeat_seconds,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "exception_type", "job_error"),
    [
        (
            ParserErrorCode.TIMEOUT,
            RetryableJobHandlerError,
            JobExecutionErrorCode.INGESTION_PARSER_RETRYABLE,
        ),
        (
            ParserErrorCode.CORRUPT_DOCUMENT,
            PermanentJobHandlerError,
            JobExecutionErrorCode.INGESTION_PARSER_FAILED,
        ),
    ],
)
async def test_knowledge_handler_maps_parser_retryability_to_stable_job_errors(
    code: ParserErrorCode,
    exception_type: type[RetryableJobHandlerError | PermanentJobHandlerError],
    job_error: JobExecutionErrorCode,
) -> None:
    handler = KnowledgeIngestionJobHandler(
        cast(KnowledgeIngestionService, FailingIngestionUseCase(code))
    )

    with pytest.raises(exception_type) as captured:
        await handler.execute(acquired_job())

    error = cast(RetryableJobHandlerError | PermanentJobHandlerError, captured.value)
    assert error.error_code is job_error


@pytest.mark.asyncio
async def test_successful_cleanup_uses_pg_payload_shape_and_duplicate_is_no_op() -> None:
    cleanup = RecordingCleanupUseCase()
    jobs = RecordingJobUseCase(acquired_job())
    worker = runtime(jobs, cleanup)

    first = await worker.execute(delivery())
    duplicate = await worker.execute(delivery())

    assert first is JobExecutionDisposition.SUCCEEDED
    assert duplicate is JobExecutionDisposition.NO_OP
    assert cleanup.commands == [RefreshRecoveryCleanupCommand(batch_size=25)]
    assert len(jobs.finish_commands) == 1
    assert jobs.finish_commands[0].outcome is JobStatus.SUCCEEDED
    assert jobs.finish_commands[0].result == {
        "scanned_count": 3,
        "cleared_count": 2,
    }
    assert jobs.acquire_commands[0].outbox_id == OUTBOX_ID
    assert jobs.acquire_commands[0].trace_id == TRACE_ID


@pytest.mark.asyncio
async def test_direct_answer_job_delegates_only_the_run_id_to_agent_runtime(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    execution = RecordingDirectAnswerUseCase()
    jobs = RecordingJobUseCase(
        acquired_job(
            task_name=DIRECT_ANSWER_TASK_NAME,
            payload={"schema_version": 1, "agent_run_id": str(JOB_ID)},
        )
    )

    result = await runtime(jobs, RecordingCleanupUseCase(), direct_answer=execution).execute(
        delivery()
    )

    assert result is JobExecutionDisposition.SUCCEEDED
    assert execution.run_ids == [JOB_ID]
    assert jobs.finish_commands[0].result == {
        "agent_run_id": str(JOB_ID),
        "run_status": "completed",
        "stop_reason": "final",
        "terminal_event_sequence": 11,
    }
    assert f"job_id={JOB_ID}" in caplog.text
    assert f"trace_id={TRACE_ID}" in caplog.text
    assert "status=succeeded" in caplog.text
    assert "duration_ms=" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 1},
        {"schema_version": True, "agent_run_id": str(JOB_ID)},
        {"schema_version": 1, "agent_run_id": "not-a-uuid"},
        {"schema_version": 1, "agent_run_id": str(UUID(int=0))},
    ],
)
async def test_direct_answer_job_rejects_invalid_payload_without_running_agent(
    payload: dict[str, object],
) -> None:
    execution = RecordingDirectAnswerUseCase()
    jobs = RecordingJobUseCase(acquired_job(task_name=DIRECT_ANSWER_TASK_NAME, payload=payload))

    result = await runtime(jobs, RecordingCleanupUseCase(), direct_answer=execution).execute(
        delivery()
    )

    assert result is JobExecutionDisposition.FAILED
    assert execution.run_ids == []
    assert jobs.finish_commands[0].error_code == JobExecutionErrorCode.INVALID_PAYLOAD.value


@pytest.mark.asyncio
async def test_heartbeat_losing_live_lease_cancels_handler_without_finish() -> None:
    cleanup = BlockingCleanupUseCase()
    jobs = RecordingJobUseCase(acquired_job(), lose_heartbeat=True)
    worker = runtime(jobs, cleanup, heartbeat_seconds=0.001)

    result = await asyncio.wait_for(worker.execute(delivery()), timeout=1)

    assert cleanup.started.is_set()
    assert result is JobExecutionDisposition.LEASE_LOST
    assert len(jobs.heartbeat_commands) == 1
    assert jobs.finish_commands == []
    assert jobs.retry_commands == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (
            RefreshRecoveryCleanupUnavailableError(sqlstate="40001"),
            JobExecutionErrorCode.CLEANUP_UNAVAILABLE,
        ),
    ],
)
async def test_retryable_cleanup_failures_persist_only_stable_codes(
    failure: Exception,
    expected_code: JobExecutionErrorCode,
) -> None:
    jobs = RecordingJobUseCase(acquired_job())
    worker = runtime(jobs, RecordingCleanupUseCase(failure))

    result = await worker.execute(delivery())

    assert result is JobExecutionDisposition.RETRY_SCHEDULED
    assert jobs.finish_commands == []
    assert len(jobs.retry_commands) == 1
    assert jobs.retry_commands[0].error_code is expected_code
    assert 1 <= jobs.retry_commands[0].retry_delay_seconds <= 300


@pytest.mark.asyncio
async def test_application_soft_deadline_cancels_handler_and_persists_retry() -> None:
    cleanup = BlockingCleanupUseCase()
    jobs = RecordingJobUseCase(acquired_job(soft_time_limit_seconds=1))
    worker = runtime(jobs, cleanup, heartbeat_seconds=60)

    result = await asyncio.wait_for(worker.execute(delivery()), timeout=2)

    assert cleanup.started.is_set()
    assert result is JobExecutionDisposition.RETRY_SCHEDULED
    assert jobs.finish_commands == []
    assert len(jobs.retry_commands) == 1
    assert jobs.retry_commands[0].error_code is JobExecutionErrorCode.SOFT_TIME_LIMIT_EXCEEDED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("job", "expected_code"),
    [
        (
            acquired_job(task_name="unregistered.handler.v1"),
            JobExecutionErrorCode.UNKNOWN_HANDLER,
        ),
        (
            acquired_job(payload={"batch_size": SENSITIVE_VALUE}),
            JobExecutionErrorCode.INVALID_PAYLOAD,
        ),
    ],
)
async def test_unknown_handler_and_invalid_payload_are_stable_failed(
    job: AcquiredJob,
    expected_code: JobExecutionErrorCode,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    jobs = RecordingJobUseCase(job)
    worker = runtime(jobs, RecordingCleanupUseCase())

    result = await worker.execute(delivery())

    assert result is JobExecutionDisposition.FAILED
    assert len(jobs.finish_commands) == 1
    assert jobs.finish_commands[0].outcome is JobStatus.FAILED
    assert jobs.finish_commands[0].error_code == expected_code.value
    assert SENSITIVE_VALUE not in repr(job)
    assert SENSITIVE_VALUE not in repr(worker)
    assert SENSITIVE_VALUE not in caplog.text


def test_celery_task_is_keyword_only_and_broker_body_has_no_handler_or_payload() -> None:
    first_received: list[JobDispatchMessage] = []
    second_received: list[JobDispatchMessage] = []

    async def record_first(message: JobDispatchMessage) -> JobExecutionDisposition:
        first_received.append(message)
        return JobExecutionDisposition.SUCCEEDED

    async def record_second(message: JobDispatchMessage) -> JobExecutionDisposition:
        second_received.append(message)
        return JobExecutionDisposition.SUCCEEDED

    first_app = Celery("worker-contract-first", broker="memory://", backend=None)
    second_app = Celery("worker-contract-second", broker="memory://", backend=None)
    first_task = register_job_execution_task(
        first_app,
        delivery_runner=record_first,
    )
    assert CELERY_JOB_DISPATCH_TASK_NAME not in second_app.tasks
    second_task = register_job_execution_task(
        second_app,
        delivery_runner=record_second,
    )

    first_task.run(**delivery().as_json_kwargs())
    second_task.run(**delivery().as_json_kwargs())

    assert first_task.name == CELERY_JOB_DISPATCH_TASK_NAME
    assert tuple(inspect.signature(first_task.run).parameters) == (
        "job_id",
        "dispatch_generation",
        "outbox_id",
        "trace_id",
    )
    assert first_received == [delivery()]
    assert second_received == [delivery()]
    serialized = repr(delivery().as_json_kwargs())
    assert IDENTITY_REFRESH_RECOVERY_CLEANUP_HANDLER not in serialized
    assert "batch_size" not in serialized
    assert SENSITIVE_VALUE not in serialized
    first_app.close()
    second_app.close()


@pytest.mark.asyncio
async def test_production_composition_registers_the_direct_answer_runtime(
    test_settings: Settings,
) -> None:
    engine = create_database_engine(test_settings)
    client = httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _request: httpx2.Response(500)),
        follow_redirects=False,
        trust_env=False,
    )
    try:
        worker = create_job_delivery_runtime(
            test_settings,
            create_database_session_factory(engine),
            client,
        )

        assert isinstance(
            worker.handlers.resolve(DIRECT_ANSWER_TASK_NAME),
            DirectAnswerJobHandler,
        )
        assert isinstance(
            worker.handlers.resolve(INDUSTRY_COLLECTION_TASK_NAME),
            IndustryCollectionJobHandler,
        )
    finally:
        await client.aclose()
        await engine.dispose()
