"""PostgreSQL proofs for exactly-once Agent Run failure convergence."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from industry_platform.core.database import (
    create_database_engine,
    create_database_session_factory,
)
from industry_platform.modules.agent_runtime.adapters.persistence import (
    AgentEventPersistenceError,
    SqlAlchemyAgentEventCommitter,
    SqlAlchemyAgentRunTerminalizer,
)
from industry_platform.modules.agent_runtime.domain import (
    AgentRunStatus,
    AgentStepStatus,
    RunStopReason,
)
from industry_platform.modules.agent_runtime.events import (
    TERMINAL_AGENT_EVENT_TYPES,
    AgentEvent,
    AgentEventType,
)
from industry_platform.modules.agent_runtime.models import (
    AgentEventRecord,
    AgentRunRecord,
    AgentStepRecord,
)
from industry_platform.modules.conversations.adapters.sqlalchemy import (
    SqlAlchemyDirectAnswerTurnTransactionFactory,
)
from industry_platform.modules.conversations.models import Message, MessageRole, MessageStatus
from industry_platform.modules.conversations.service import ConversationApplicationService
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.jobs.domain import JobEventType, JobStatus
from industry_platform.modules.jobs.models import Job, JobEvent
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe
from .test_conversation_agent_postgres import (
    NOW,
    WORKSPACE_ID,
    command,
    seed_workspace,
)


def test_agent_event_batch_rolls_back_every_projection_when_one_event_is_invalid(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        try:
            await seed_workspace(session_factory)
            receipt = await ConversationApplicationService(
                transaction_factory=SqlAlchemyDirectAnswerTurnTransactionFactory(session_factory),
                clock=lambda: NOW,
            ).start_direct_answer(
                replace(
                    command(),
                    idempotency_key="postgres-agent-event-batch-rollback",
                    question="Prove that a projected Event batch is atomic.",
                )
            )
            async with session_factory() as session:
                run = await session.get(AgentRunRecord, receipt.run_id)
            assert run is not None
            committer = SqlAlchemyAgentEventCommitter(session_factory)
            events = (
                AgentEvent(
                    schema_version=1,
                    stream_id=run.event_stream_id,
                    run_id=run.id,
                    workspace_id=run.workspace_id,
                    sequence=2,
                    occurred_at=NOW + timedelta(seconds=1),
                    trace_id=TraceId(run.trace_id),
                    event_type=AgentEventType.RUN_STARTED,
                    payload={"state_revision": 1},
                ),
                AgentEvent(
                    schema_version=1,
                    stream_id=run.event_stream_id,
                    run_id=run.id,
                    workspace_id=run.workspace_id,
                    sequence=3,
                    occurred_at=NOW + timedelta(seconds=2),
                    trace_id=TraceId(run.trace_id),
                    event_type=AgentEventType.STEP_COMPLETED,
                    payload={
                        "step_id": str(uuid4()),
                        "step_kind": "tool",
                        "cost_micro_usd": 17,
                    },
                ),
            )

            with pytest.raises(AgentEventPersistenceError):
                await committer.append_batch(events)

            async with session_factory() as session:
                persisted_run = await session.get(AgentRunRecord, receipt.run_id)
                event_count = await session.scalar(
                    select(func.count())
                    .select_from(AgentEventRecord)
                    .where(AgentEventRecord.run_id == receipt.run_id)
                )
            assert persisted_run is not None
            assert persisted_run.status is AgentRunStatus.QUEUED
            assert persisted_run.event_count == 1
            assert persisted_run.cost_micro_usd == 0
            assert event_count == 1
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())


def test_cancelled_completed_model_usage_is_preserved_in_run_and_step(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        try:
            await seed_workspace(session_factory)
            receipt = await ConversationApplicationService(
                transaction_factory=SqlAlchemyDirectAnswerTurnTransactionFactory(session_factory),
                clock=lambda: NOW,
            ).start_direct_answer(
                replace(
                    command(),
                    idempotency_key="postgres-cancelled-model-usage",
                    question="Preserve usage when cancellation races model completion.",
                )
            )
            async with session_factory() as session:
                run = await session.get(AgentRunRecord, receipt.run_id)
            assert run is not None
            step_id = uuid4()
            committer = SqlAlchemyAgentEventCommitter(session_factory)
            await committer.append_batch(
                (
                    AgentEvent(
                        schema_version=1,
                        stream_id=run.event_stream_id,
                        run_id=run.id,
                        workspace_id=run.workspace_id,
                        sequence=2,
                        occurred_at=NOW + timedelta(seconds=1),
                        trace_id=TraceId(run.trace_id),
                        event_type=AgentEventType.RUN_STARTED,
                        payload={"state_revision": 1},
                    ),
                    AgentEvent(
                        schema_version=1,
                        stream_id=run.event_stream_id,
                        run_id=run.id,
                        workspace_id=run.workspace_id,
                        sequence=3,
                        occurred_at=NOW + timedelta(seconds=2),
                        trace_id=TraceId(run.trace_id),
                        event_type=AgentEventType.STEP_STARTED,
                        payload={
                            "step_id": str(step_id),
                            "step_sequence": 1,
                            "step_kind": "model",
                        },
                    ),
                    AgentEvent(
                        schema_version=1,
                        stream_id=run.event_stream_id,
                        run_id=run.id,
                        workspace_id=run.workspace_id,
                        sequence=4,
                        occurred_at=NOW + timedelta(seconds=3),
                        trace_id=TraceId(run.trace_id),
                        event_type=AgentEventType.RUN_CANCELLED,
                        payload={
                            "stop_reason": RunStopReason.CANCELLED.value,
                            "cancelled_step_id": str(step_id),
                            "input_tokens": 11,
                            "output_tokens": 5,
                            "cached_input_tokens": 2,
                            "cost_micro_usd": 17,
                        },
                    ),
                )
            )

            async with session_factory() as session:
                persisted_run = await session.get(AgentRunRecord, run.id)
                persisted_step = await session.get(AgentStepRecord, step_id)
            assert persisted_run is not None
            assert persisted_step is not None
            assert persisted_run.status is AgentRunStatus.CANCELLED
            assert persisted_run.input_tokens_used == 11
            assert persisted_run.output_tokens_used == 5
            assert persisted_run.cached_input_tokens_used == 2
            assert persisted_run.cost_micro_usd == 17
            assert persisted_step.status is AgentStepStatus.CANCELLED
            assert persisted_step.input_tokens == 11
            assert persisted_step.output_tokens == 5
            assert persisted_step.cost_micro_usd == 17
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())


def test_unrecoverable_execution_and_dead_letter_each_commit_one_terminal_event(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        try:
            await seed_workspace(session_factory)
            conversations = ConversationApplicationService(
                transaction_factory=SqlAlchemyDirectAnswerTurnTransactionFactory(session_factory),
                clock=lambda: NOW,
            )
            interrupted = await conversations.start_direct_answer(command())
            async with session_factory() as session:
                run = await session.scalar(
                    select(AgentRunRecord).where(AgentRunRecord.id == interrupted.run_id)
                )
            assert run is not None
            committer = SqlAlchemyAgentEventCommitter(session_factory)
            await committer.append(
                AgentEvent(
                    schema_version=1,
                    stream_id=run.event_stream_id,
                    run_id=run.id,
                    workspace_id=run.workspace_id,
                    sequence=2,
                    occurred_at=NOW + timedelta(seconds=1),
                    trace_id=TraceId(run.trace_id),
                    event_type=AgentEventType.RUN_STARTED,
                    payload={"state_revision": 1},
                )
            )
            step_id = uuid4()
            await committer.append(
                AgentEvent(
                    schema_version=1,
                    stream_id=run.event_stream_id,
                    run_id=run.id,
                    workspace_id=run.workspace_id,
                    sequence=3,
                    occurred_at=NOW + timedelta(seconds=2),
                    trace_id=TraceId(run.trace_id),
                    event_type=AgentEventType.STEP_STARTED,
                    payload={
                        "step_id": str(step_id),
                        "step_sequence": 1,
                        "step_kind": "model",
                    },
                )
            )
            for sequence, delta in ((4, "Interrupted but "), (5, "preserved.")):
                await committer.append(
                    AgentEvent(
                        schema_version=1,
                        stream_id=run.event_stream_id,
                        run_id=run.id,
                        workspace_id=run.workspace_id,
                        sequence=sequence,
                        occurred_at=NOW + timedelta(seconds=sequence - 1),
                        trace_id=TraceId(run.trace_id),
                        event_type=AgentEventType.MODEL_DELTA,
                        payload={
                            "step_id": str(step_id),
                            "model_sequence": sequence - 3,
                            "delta": delta,
                        },
                    )
                )

            terminalizer = SqlAlchemyAgentRunTerminalizer(session_factory)
            assert await terminalizer.settle_unrecoverable(
                run.id,
                error_code="execution_failed",
            )
            assert await terminalizer.settle_unrecoverable(
                run.id,
                error_code="execution_failed",
            )

            abandoned = await conversations.start_direct_answer(
                replace(
                    command(),
                    idempotency_key="postgres-browser-request-dead-letter",
                    question="This input must survive a broker dead letter.",
                    new_conversation_title="Dead letter test",
                )
            )
            async with session_factory.begin() as session:
                job = await session.scalar(
                    select(Job).where(Job.id == abandoned.job_id).with_for_update()
                )
                database_now = await session.scalar(select(func.clock_timestamp()))
                assert job is not None
                assert database_now is not None
                job.status = JobStatus.DEAD_LETTER
                job.terminal_at = database_now
                job.last_error_code = "broker_publish_abandoned"
                job.stage_name = JobStatus.DEAD_LETTER.value
                job.stage_sequence += 1
                job.updated_at = database_now

            assert await terminalizer.reconcile_orphans(batch_size=10) == 1
            assert await terminalizer.reconcile_orphans(batch_size=10) == 0

            async with session_factory() as session:
                interrupted_run = await session.scalar(
                    select(AgentRunRecord).where(AgentRunRecord.id == interrupted.run_id)
                )
                interrupted_step = await session.scalar(
                    select(AgentStepRecord).where(AgentStepRecord.id == step_id)
                )
                interrupted_events = tuple(
                    await session.scalars(
                        select(AgentEventRecord)
                        .where(AgentEventRecord.run_id == interrupted.run_id)
                        .order_by(AgentEventRecord.sequence)
                    )
                )
                interrupted_message = await session.scalar(
                    select(Message).where(
                        Message.agent_run_id == interrupted.run_id,
                        Message.role == MessageRole.ASSISTANT,
                    )
                )
                abandoned_run = await session.scalar(
                    select(AgentRunRecord).where(AgentRunRecord.id == abandoned.run_id)
                )
                abandoned_events = tuple(
                    await session.scalars(
                        select(AgentEventRecord)
                        .where(AgentEventRecord.run_id == abandoned.run_id)
                        .order_by(AgentEventRecord.sequence)
                    )
                )

            assert interrupted_run is not None
            assert interrupted_step is not None
            assert interrupted_run.status is AgentRunStatus.FAILED
            assert interrupted_run.stop_reason is RunStopReason.RUNTIME_ERROR
            assert interrupted_step.status is AgentStepStatus.FAILED
            assert interrupted_step.error_code == "execution_failed"
            assert interrupted_message is not None
            assert interrupted_message.status is MessageStatus.PARTIAL
            assert interrupted_message.content_markdown == "Interrupted but preserved."
            assert (
                sum(event.event_type in TERMINAL_AGENT_EVENT_TYPES for event in interrupted_events)
                == 1
            )
            terminal_record = interrupted_events[-1]
            await committer.append(
                AgentEvent(
                    schema_version=terminal_record.schema_version,
                    stream_id=terminal_record.stream_id,
                    run_id=terminal_record.run_id,
                    workspace_id=terminal_record.workspace_id,
                    sequence=terminal_record.sequence,
                    occurred_at=terminal_record.occurred_at,
                    trace_id=TraceId(terminal_record.trace_id),
                    event_type=terminal_record.event_type,
                    payload=terminal_record.payload,
                )
            )
            with pytest.raises(AgentEventPersistenceError):
                await committer.append(
                    AgentEvent(
                        schema_version=terminal_record.schema_version,
                        stream_id=terminal_record.stream_id,
                        run_id=terminal_record.run_id,
                        workspace_id=terminal_record.workspace_id,
                        sequence=terminal_record.sequence + 1,
                        occurred_at=terminal_record.occurred_at + timedelta(seconds=1),
                        trace_id=TraceId(terminal_record.trace_id),
                        event_type=AgentEventType.MODEL_DELTA,
                        payload={"delta": "stale writer must not append"},
                    )
                )
            assert abandoned_run is not None
            assert abandoned_run.status is AgentRunStatus.FAILED
            assert abandoned_run.stop_reason is RunStopReason.RUNTIME_ERROR
            assert [event.event_type for event in abandoned_events] == [
                AgentEventType.RUN_QUEUED,
                AgentEventType.RUN_FAILED,
            ]
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())


def test_orphan_reconciliation_atomically_settles_retry_and_cancelled_jobs(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        try:
            await seed_workspace(session_factory)
            conversations = ConversationApplicationService(
                transaction_factory=SqlAlchemyDirectAnswerTurnTransactionFactory(session_factory),
                clock=lambda: NOW,
            )
            retrying = await conversations.start_direct_answer(
                replace(
                    command(),
                    idempotency_key="postgres-agent-retry-orphan",
                    question="This Run cannot resume after its Job schedules a retry.",
                    new_conversation_title="Retry orphan",
                )
            )
            cancelling = await conversations.start_direct_answer(
                replace(
                    command(),
                    idempotency_key="postgres-agent-cancel-orphan",
                    question="Preserve this cancelled partial response.",
                    new_conversation_title="Cancel orphan",
                )
            )
            committer = SqlAlchemyAgentEventCommitter(session_factory)
            streams: dict[str, tuple[UUID, str]] = {}
            async with session_factory() as session:
                for name, receipt in (("retry", retrying), ("cancel", cancelling)):
                    selected_run = await session.scalar(
                        select(AgentRunRecord).where(AgentRunRecord.id == receipt.run_id)
                    )
                    assert selected_run is not None
                    streams[name] = (selected_run.event_stream_id, selected_run.trace_id)

            for receipt, name in ((retrying, "retry"), (cancelling, "cancel")):
                stream_id, trace_id = streams[name]
                await committer.append(
                    AgentEvent(
                        schema_version=1,
                        stream_id=stream_id,
                        run_id=receipt.run_id,
                        workspace_id=WORKSPACE_ID,
                        sequence=2,
                        occurred_at=NOW + timedelta(seconds=1),
                        trace_id=TraceId(trace_id),
                        event_type=AgentEventType.RUN_STARTED,
                        payload={"state_revision": 1},
                    )
                )

            cancel_stream_id, cancel_trace_id = streams["cancel"]
            await committer.append(
                AgentEvent(
                    schema_version=1,
                    stream_id=cancel_stream_id,
                    run_id=cancelling.run_id,
                    workspace_id=WORKSPACE_ID,
                    sequence=3,
                    occurred_at=NOW + timedelta(seconds=2),
                    trace_id=TraceId(cancel_trace_id),
                    event_type=AgentEventType.MODEL_DELTA,
                    payload={"model_sequence": 1, "delta": "Cancelled but preserved."},
                )
            )

            async with session_factory.begin() as session:
                retry_job = await session.scalar(
                    select(Job).where(Job.id == retrying.job_id).with_for_update()
                )
                cancel_job = await session.scalar(
                    select(Job).where(Job.id == cancelling.job_id).with_for_update()
                )
                cancel_run = await session.scalar(
                    select(AgentRunRecord)
                    .where(AgentRunRecord.id == cancelling.run_id)
                    .with_for_update()
                )
                database_now = await session.scalar(select(func.clock_timestamp()))
                assert retry_job is not None
                assert cancel_job is not None
                assert cancel_run is not None
                assert database_now is not None

                retry_job.status = JobStatus.RETRY_WAIT
                retry_job.attempt_count = 1
                retry_job.dispatch_generation += 1
                retry_job.available_at = database_now + timedelta(minutes=1)
                retry_job.stage_name = JobStatus.RETRY_WAIT.value
                retry_job.stage_sequence += 1
                retry_job.last_error_code = "job_handler_failed"
                retry_job.updated_at = database_now
                session.add(
                    JobEvent(
                        id=uuid4(),
                        job_id=retry_job.id,
                        event_type=JobEventType.RETRY_SCHEDULED,
                        generation=retry_job.generation,
                        dispatch_generation=retry_job.dispatch_generation,
                        fencing_token=retry_job.fencing_token,
                        event_sequence=retry_job.stage_sequence,
                        occurred_at=database_now,
                        details={"error_code": "job_handler_failed"},
                    )
                )

                cancellation_time = database_now + timedelta(seconds=5)
                cancel_run.cancel_requested_at = cancellation_time
                cancel_job.cancel_requested_at = cancellation_time

            terminalizer = SqlAlchemyAgentRunTerminalizer(session_factory)
            assert await terminalizer.reconcile_orphans(batch_size=10) == 2
            assert await terminalizer.reconcile_orphans(batch_size=10) == 0

            async with session_factory() as session:
                retry_run = await session.scalar(
                    select(AgentRunRecord).where(AgentRunRecord.id == retrying.run_id)
                )
                retry_job = await session.scalar(select(Job).where(Job.id == retrying.job_id))
                cancel_run = await session.scalar(
                    select(AgentRunRecord).where(AgentRunRecord.id == cancelling.run_id)
                )
                cancel_job = await session.scalar(select(Job).where(Job.id == cancelling.job_id))
                cancel_message = await session.scalar(
                    select(Message).where(
                        Message.agent_run_id == cancelling.run_id,
                        Message.role == MessageRole.ASSISTANT,
                    )
                )
                retry_terminal_count = await _terminal_job_event_count(
                    session, job_id=retrying.job_id
                )
                cancel_terminal_count = await _terminal_job_event_count(
                    session, job_id=cancelling.job_id
                )

            assert retry_run is not None
            assert retry_job is not None
            assert retry_run.status is AgentRunStatus.FAILED
            assert retry_job.status is JobStatus.FAILED
            assert retry_job.last_error_code == "job_execution_abandoned"
            assert retry_job.attempt_count < retry_job.max_attempts
            assert retry_run.terminal_at == retry_job.terminal_at
            assert retry_terminal_count == 1

            assert cancel_run is not None
            assert cancel_job is not None
            assert cancel_run.status is AgentRunStatus.CANCELLED
            assert cancel_job.status is JobStatus.CANCELLED
            assert cancel_run.terminal_at == cancel_job.terminal_at == cancellation_time
            assert cancel_terminal_count == 1
            assert cancel_message is not None
            assert cancel_message.status is MessageStatus.PARTIAL
            assert cancel_message.content_markdown == "Cancelled but preserved."
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())


async def _terminal_job_event_count(session: object, *, job_id: UUID) -> int:
    from sqlalchemy.ext.asyncio import AsyncSession

    if not isinstance(session, AsyncSession):
        raise TypeError("Expected an async SQLAlchemy session")
    value = await session.scalar(
        select(func.count())
        .select_from(JobEvent)
        .where(
            JobEvent.job_id == job_id,
            JobEvent.event_type.in_(
                (
                    JobEventType.SUCCEEDED,
                    JobEventType.FAILED,
                    JobEventType.CANCELLED,
                    JobEventType.DEAD_LETTER,
                )
            ),
        )
    )
    return 0 if value is None else value
