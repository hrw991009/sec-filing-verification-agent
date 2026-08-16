"""Prove chat input, AgentRun, Job, and Outbox share PostgreSQL truth."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from industry_platform.core.database import (
    create_database_engine,
    create_database_session_factory,
)
from industry_platform.modules.agent_runtime.adapters.persistence import (
    SqlAlchemyAgentEventCommitter,
    SqlAlchemyAgentRunControl,
    SqlAlchemyCommittedEventSource,
)
from industry_platform.modules.agent_runtime.domain import AgentRunStatus, RunBudget, RunStopReason
from industry_platform.modules.agent_runtime.events import AgentEvent, AgentEventType
from industry_platform.modules.agent_runtime.models import AgentEventRecord, AgentRunRecord
from industry_platform.modules.agent_runtime.streaming import select_committed_replay
from industry_platform.modules.conversations.adapters.sqlalchemy import (
    SqlAlchemyDirectAnswerTurnTransactionFactory,
)
from industry_platform.modules.conversations.domain import StartDirectAnswerTurn
from industry_platform.modules.conversations.models import Conversation, Message, Turn
from industry_platform.modules.conversations.service import ConversationApplicationService
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.identity.models import (
    User,
    UserStatus,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
    WorkspaceStatus,
)
from industry_platform.modules.jobs.domain import (
    JobEventType,
    JobIdempotencyConflictError,
    JobStatus,
)
from industry_platform.modules.jobs.models import Job, JobEvent, OutboxEvent
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe

NOW = datetime(2026, 8, 13, 8, 0, tzinfo=UTC)
WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
USER_ID = UUID("22222222-2222-4222-8222-222222222222")


async def seed_workspace(session_factory: object) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    if not isinstance(session_factory, async_sessionmaker):
        raise TypeError("Expected an async SQLAlchemy session factory")
    async with session_factory.begin() as session:
        assert isinstance(session, AsyncSession)
        session.add(
            User(
                id=USER_ID,
                email="agent-day2@example.test",
                password_hash=str(USER_ID),
                status=UserStatus.ACTIVE,
                password_changed_at=NOW,
            )
        )
        session.add(
            Workspace(
                id=WORKSPACE_ID,
                name="Agent Day 2",
                created_by_user_id=USER_ID,
                status=WorkspaceStatus.ACTIVE,
            )
        )
        session.add(
            WorkspaceMembership(
                id=uuid4(),
                workspace_id=WORKSPACE_ID,
                user_id=USER_ID,
                role=WorkspaceRole.OWNER,
            )
        )


def command() -> StartDirectAnswerTurn:
    return StartDirectAnswerTurn(
        workspace_id=WORKSPACE_ID,
        user_id=USER_ID,
        trace_id=TraceId("postgres-agent-turn"),
        budget=RunBudget(
            schema_version=1,
            max_steps=2,
            max_total_tokens=1_000,
            max_cost_micro_usd=100_000,
            deadline=NOW + timedelta(minutes=5),
        ),
        runtime_version="direct-answer-runtime-v0",
        harness_version="harness-v0",
        idempotency_key="postgres-browser-request-1",
        question="This input must survive a later model failure.",
        new_conversation_title="Durable failure test",
    )


def test_atomic_turn_is_idempotent_and_survives_later_runtime_failure(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        try:
            await seed_workspace(session_factory)
            service = ConversationApplicationService(
                transaction_factory=SqlAlchemyDirectAnswerTurnTransactionFactory(session_factory),
                clock=lambda: NOW,
            )
            created = await service.start_direct_answer(command())
            reused = await service.start_direct_answer(command())

            assert created.created is True
            assert reused.created is False
            assert reused.run_id == created.run_id
            assert reused.job_id == created.job_id
            assert reused.outbox_event_id == created.outbox_event_id

            changed = replace(command(), question="A changed retry must conflict.")
            with pytest.raises(JobIdempotencyConflictError):
                await service.start_direct_answer(changed)

            failure = AgentEvent(
                schema_version=1,
                stream_id=await _stream_id(session_factory, created.run_id),
                run_id=created.run_id,
                workspace_id=WORKSPACE_ID,
                sequence=2,
                occurred_at=NOW + timedelta(seconds=1),
                trace_id=TraceId("postgres-agent-turn"),
                event_type=AgentEventType.RUN_FAILED,
                payload={"stop_reason": RunStopReason.RUNTIME_ERROR.value},
            )
            await SqlAlchemyAgentEventCommitter(session_factory).append(failure)

            async with session_factory() as session:
                count_values: list[int] = []
                for model in (
                    Conversation,
                    Turn,
                    Message,
                    AgentRunRecord,
                    AgentEventRecord,
                    Job,
                    OutboxEvent,
                ):
                    count_values.append(
                        (await session.scalar(select(func.count()).select_from(model))) or 0
                    )
                counts = tuple(count_values)
                stored_message = await session.scalar(select(Message))
                stored_run = await session.scalar(select(AgentRunRecord))
            assert counts == (1, 1, 1, 1, 2, 1, 1)
            assert stored_message is not None
            assert stored_message.content_markdown == command().question
            assert stored_run is not None
            assert stored_run.status.value == "failed"
            assert stored_run.stop_reason is RunStopReason.RUNTIME_ERROR
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())


def test_queued_cancellation_terminalizes_run_and_job_together_once(
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
            ).start_direct_answer(command())
            control = SqlAlchemyAgentRunControl(session_factory)

            accepted = await control.request_cancel(
                run_id=receipt.run_id,
                workspace_id=WORKSPACE_ID,
                requested_at=NOW + timedelta(seconds=1),
            )
            accepted_again = await control.request_cancel(
                run_id=receipt.run_id,
                workspace_id=WORKSPACE_ID,
                requested_at=NOW + timedelta(seconds=2),
            )

            assert accepted is True
            assert accepted_again is True
            assert await control.is_cancel_requested(
                run_id=receipt.run_id,
                workspace_id=WORKSPACE_ID,
            )
            async with session_factory() as session:
                run = await session.scalar(
                    select(AgentRunRecord).where(AgentRunRecord.id == receipt.run_id)
                )
                job = await session.scalar(select(Job).where(Job.id == receipt.job_id))
                agent_events = tuple(
                    await session.scalars(
                        select(AgentEventRecord)
                        .where(AgentEventRecord.run_id == receipt.run_id)
                        .order_by(AgentEventRecord.sequence)
                    )
                )
                job_events = tuple(
                    await session.scalars(
                        select(JobEvent)
                        .where(JobEvent.job_id == receipt.job_id)
                        .order_by(JobEvent.event_sequence)
                    )
                )
            assert run is not None
            assert job is not None
            assert run.cancel_requested_at == job.cancel_requested_at == NOW + timedelta(seconds=1)
            assert run.status is AgentRunStatus.CANCELLED
            assert run.stop_reason is RunStopReason.CANCELLED
            assert run.terminal_at is not None
            assert run.terminal_at == job.terminal_at
            assert run.terminal_at >= NOW + timedelta(seconds=1)
            assert run.event_count == 2
            assert tuple(event.event_type for event in agent_events) == (
                AgentEventType.RUN_QUEUED,
                AgentEventType.RUN_CANCELLED,
            )
            assert job.status is JobStatus.CANCELLED
            assert job_events[-1].event_type is JobEventType.CANCELLED
            assert sum(event.event_type is JobEventType.CANCELLED for event in job_events) == 1
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())


def test_cancellation_repairs_a_previously_cancelled_job_with_a_queued_run(
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
            ).start_direct_answer(command())
            async with session_factory.begin() as session:
                job = await session.scalar(
                    select(Job).where(Job.id == receipt.job_id).with_for_update()
                )
                database_now = await session.scalar(select(func.clock_timestamp()))
                assert job is not None
                assert database_now is not None
                job.cancel_requested_at = NOW + timedelta(seconds=1)
                job.status = JobStatus.CANCELLED
                job.terminal_at = database_now
                job.stage_name = JobStatus.CANCELLED.value
                job.stage_sequence += 1
                job.updated_at = database_now
                session.add(
                    JobEvent(
                        id=uuid4(),
                        job_id=job.id,
                        event_type=JobEventType.CANCELLED,
                        generation=job.generation,
                        dispatch_generation=job.dispatch_generation,
                        fencing_token=job.fencing_token,
                        event_sequence=job.stage_sequence,
                        occurred_at=database_now,
                        details={"source": "legacy_reconciler"},
                    )
                )

            accepted = await SqlAlchemyAgentRunControl(session_factory).request_cancel(
                run_id=receipt.run_id,
                workspace_id=WORKSPACE_ID,
                requested_at=NOW + timedelta(seconds=2),
            )

            assert accepted is True
            async with session_factory() as session:
                run = await session.scalar(
                    select(AgentRunRecord).where(AgentRunRecord.id == receipt.run_id)
                )
                agent_events = tuple(
                    await session.scalars(
                        select(AgentEventRecord)
                        .where(AgentEventRecord.run_id == receipt.run_id)
                        .order_by(AgentEventRecord.sequence)
                    )
                )
                cancelled_job_event_count = await session.scalar(
                    select(func.count())
                    .select_from(JobEvent)
                    .where(
                        JobEvent.job_id == receipt.job_id,
                        JobEvent.event_type == JobEventType.CANCELLED,
                    )
                )
            assert run is not None
            assert run.status is AgentRunStatus.CANCELLED
            assert tuple(event.event_type for event in agent_events) == (
                AgentEventType.RUN_QUEUED,
                AgentEventType.RUN_CANCELLED,
            )
            assert cancelled_job_event_count == 1
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())


def test_committed_event_reader_resolves_run_inside_workspace_and_reads_bounded_batches(
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
            ).start_direct_answer(command())
            reader = SqlAlchemyCommittedEventSource(session_factory)

            descriptor = await reader.find_run(
                run_id=receipt.run_id,
                workspace_id=WORKSPACE_ID,
            )
            hidden = await reader.find_run(
                run_id=receipt.run_id,
                workspace_id=uuid4(),
            )

            assert descriptor is not None
            assert descriptor.run_id == receipt.run_id
            assert descriptor.user_id == USER_ID
            assert descriptor.latest_committed_sequence == 1
            assert hidden is None

            events = await reader.load_events_after(
                run_id=receipt.run_id,
                stream_id=descriptor.stream_id,
                workspace_id=WORKSPACE_ID,
                after_sequence=0,
                limit=1,
            )
            assert len(events) == 1
            assert events[0].event_type is AgentEventType.RUN_QUEUED
            assert events[0].sequence == 1
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())


def test_committed_event_reader_bounds_initial_replay_and_builds_authoritative_snapshot(
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
            ).start_direct_answer(command())
            stream_id = await _stream_id(session_factory, receipt.run_id)
            event_time = datetime.now(UTC) + timedelta(minutes=1)
            committer = SqlAlchemyAgentEventCommitter(session_factory)
            declared_events: tuple[tuple[AgentEventType, dict[str, object]], ...] = (
                (AgentEventType.RUN_STARTED, {"state_revision": 1}),
                (AgentEventType.MODEL_DELTA, {"delta": "A"}),
                (AgentEventType.MODEL_DELTA, {"delta": "B"}),
                (AgentEventType.MODEL_DELTA, {"delta": "C"}),
                (
                    AgentEventType.RUN_FAILED,
                    {"stop_reason": RunStopReason.RUNTIME_ERROR.value},
                ),
            )
            for sequence, (event_type, payload) in enumerate(declared_events, start=2):
                await committer.append(
                    AgentEvent(
                        schema_version=1,
                        stream_id=stream_id,
                        run_id=receipt.run_id,
                        workspace_id=WORKSPACE_ID,
                        sequence=sequence,
                        occurred_at=event_time + timedelta(seconds=sequence),
                        trace_id=TraceId("postgres-agent-turn"),
                        event_type=event_type,
                        payload=payload,
                    )
                )

            window = await SqlAlchemyCommittedEventSource(
                session_factory,
                window_size=2,
            ).load_window(stream_id=stream_id, workspace_id=WORKSPACE_ID)
            replay = select_committed_replay(window, cursor=1)

            assert window.earliest_available_sequence == 5
            assert window.latest_committed_sequence == 6
            assert tuple(event.sequence for event in window.events) == (5, 6)
            assert replay.events == ()
            assert replay.snapshot is not None
            assert replay.snapshot.last_sequence == 6
            assert replay.snapshot.payload == {
                "run_id": str(receipt.run_id),
                "status": "failed",
                "stop_reason": "runtime_error",
                "terminal": True,
                "content_markdown": "ABC",
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_input_tokens": 0,
                "cost_micro_usd": 0,
            }
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())


async def _stream_id(session_factory: object, run_id: UUID) -> UUID:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    if not isinstance(session_factory, async_sessionmaker):
        raise TypeError("Expected an async SQLAlchemy session factory")
    async with session_factory() as session:
        value = await session.scalar(
            select(AgentRunRecord.event_stream_id).where(AgentRunRecord.id == run_id)
        )
    if not isinstance(value, UUID):
        raise AssertionError("Expected a persisted stream ID")
    return value
