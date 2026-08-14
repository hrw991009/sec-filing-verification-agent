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
)
from industry_platform.modules.agent_runtime.domain import RunBudget, RunStopReason
from industry_platform.modules.agent_runtime.events import AgentEvent, AgentEventType
from industry_platform.modules.agent_runtime.models import AgentEventRecord, AgentRunRecord
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
from industry_platform.modules.jobs.domain import JobIdempotencyConflictError
from industry_platform.modules.jobs.models import Job, OutboxEvent
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


def test_cancellation_is_written_to_run_and_job_together(
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

            assert accepted is True
            assert await control.is_cancel_requested(
                run_id=receipt.run_id,
                workspace_id=WORKSPACE_ID,
            )
            async with session_factory() as session:
                run_cancelled_at = await session.scalar(
                    select(AgentRunRecord.cancel_requested_at).where(
                        AgentRunRecord.id == receipt.run_id
                    )
                )
                job_cancelled_at = await session.scalar(
                    select(Job.cancel_requested_at).where(Job.id == receipt.job_id)
                )
            assert run_cancelled_at == job_cancelled_at == NOW + timedelta(seconds=1)
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
