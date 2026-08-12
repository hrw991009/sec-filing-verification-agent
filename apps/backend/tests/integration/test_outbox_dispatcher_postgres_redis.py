"""Prove dispatcher fencing in PostgreSQL and message shape in real Redis."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import cast
from uuid import uuid4

import pytest
from anyio import to_thread
from celery import Celery
from kombu import Connection
from sqlalchemy import func, select, update

from industry_platform.core.database import (
    AsyncSessionFactory,
    create_database_engine,
    create_database_session_factory,
)
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.jobs.adapters.celery import (
    CeleryJobDispatchPublisher,
)
from industry_platform.modules.jobs.adapters.sqlalchemy import (
    SqlAlchemyJobTransactionFactory,
    SqlAlchemyOutboxTransactionFactory,
)
from industry_platform.modules.jobs.domain import (
    CELERY_JOB_DISPATCH_TASK_NAME,
    AcquireJobCommand,
    ClaimOutboxCommand,
    ExecutionScope,
    JobDefinition,
    JobEventType,
    JobStatus,
    OutboxPublishError,
    OutboxPublishErrorCode,
    OutboxStatus,
    SubmitJobCommand,
)
from industry_platform.modules.jobs.models import Job, JobEvent, OutboxEvent
from industry_platform.modules.jobs.service import JobApplicationService
from industry_platform.server import create_selector_event_loop
from industry_platform.workers.celery_app import create_celery_app
from industry_platform.workers.dispatcher import OutboxDispatcher

from .postgres import PostgresProbe

REDIS_TESTS_REQUIRED = "REDIS_TESTS_REQUIRED"


def _job_service(session_factory: AsyncSessionFactory) -> JobApplicationService:
    return JobApplicationService(
        transaction_factory=SqlAlchemyJobTransactionFactory(session_factory),
        lease_seconds=120,
    )


def _submission(*, queue_name: str) -> SubmitJobCommand:
    return SubmitJobCommand(
        definition=JobDefinition(
            scope=ExecutionScope(system_scope_key="outbox-integration"),
            task_name="research.collect",
            queue_name=queue_name,
            payload={"private": "must-never-reach-redis"},
            available_at=datetime.now(UTC) - timedelta(seconds=1),
            max_attempts=3,
        ),
        trace_id=TraceId("outbox-integration-trace"),
    )


def _consume_one(app: Celery, queue_name: str) -> tuple[object, dict[str, object], str]:
    with cast(Connection, app.connection_for_read()) as connection:
        queue = connection.SimpleQueue(queue_name)
        try:
            message = queue.get(block=True, timeout=5)
            payload = message.payload
            headers = dict(message.headers or {})
            routing_key = str(message.delivery_info.get("routing_key"))
            message.ack()
            return payload, headers, routing_key
        finally:
            queue.close()


def _delete_queue(app: Celery, queue_name: str) -> None:
    with cast(Connection, app.connection_for_write()) as connection:
        channel = connection.channel()
        try:
            channel.queue_delete(queue=queue_name)
        finally:
            channel.close()


def test_real_postgres_dispatches_fixed_message_to_real_redis(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    if os.getenv(REDIS_TESTS_REQUIRED) != "1":
        pytest.skip(f"Set {REDIS_TESTS_REQUIRED}=1 to run Redis integration tests")

    queue_name = f"outbox-test-{uuid4().hex}"
    settings = migrated_postgres_probe.settings
    celery_app = create_celery_app(settings)

    async def exercise() -> tuple[dict[str, str | int], str]:
        engine = create_database_engine(settings)
        session_factory = create_database_session_factory(engine)
        service = _job_service(session_factory)
        try:
            submitted = await service.submit(_submission(queue_name=queue_name))
            dispatcher = OutboxDispatcher(
                transaction_factory=SqlAlchemyOutboxTransactionFactory(session_factory),
                publisher=CeleryJobDispatchPublisher(celery_app),
                dispatcher_id="redis-integration-dispatcher",
                batch_size=10,
                claim_seconds=60,
            )

            result = await dispatcher.dispatch_once()
            assert result.claimed == 1
            assert result.published == 1

            async with session_factory() as session:
                job = await session.get(Job, submitted.job_id)
                outbox = await session.get(OutboxEvent, submitted.outbox_event_id)
                dispatched_events = (
                    await session.scalar(
                        select(func.count())
                        .select_from(JobEvent)
                        .where(JobEvent.event_type == JobEventType.DISPATCHED)
                    )
                ) or 0

            assert job is not None
            assert outbox is not None
            assert job.status is JobStatus.DISPATCHED
            assert job.dispatch_attempt == 1
            assert outbox.status is OutboxStatus.PUBLISHED
            assert outbox.attempt_count == 1
            assert outbox.claim_generation == 1
            assert outbox.locked_by is None
            assert outbox.claim_token is None
            assert outbox.published_at is not None
            assert dispatched_events == 1
            return (
                {
                    "job_id": str(submitted.job_id),
                    "dispatch_generation": submitted.dispatch_generation,
                    "outbox_id": str(submitted.outbox_event_id),
                    "trace_id": "outbox-integration-trace",
                },
                str(submitted.outbox_event_id),
            )
        finally:
            await engine.dispose()

    try:
        with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
            expected_kwargs, expected_task_id = runner.run(exercise())
            payload, headers, routing_key = runner.run(
                to_thread.run_sync(partial(_consume_one, celery_app, queue_name))
            )

        assert isinstance(payload, list | tuple)
        assert payload[0] == []
        assert payload[1] == expected_kwargs
        assert headers["task"] == CELERY_JOB_DISPATCH_TASK_NAME
        assert headers["id"] == expected_task_id
        assert routing_key == queue_name
        assert "private" not in str(payload)
        assert "research.collect" not in str(payload)
    finally:
        _delete_queue(celery_app, queue_name)
        celery_app.close()


def test_postgres_skip_locked_expired_claim_and_worker_first_settlement(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        service = _job_service(session_factory)
        outbox_factory = SqlAlchemyOutboxTransactionFactory(session_factory)
        try:
            submitted = await service.submit(_submission(queue_name="default"))
            first_command = ClaimOutboxCommand(
                dispatcher_id="dispatcher-one",
                batch_size=1,
                claim_seconds=60,
            )
            second_command = ClaimOutboxCommand(
                dispatcher_id="dispatcher-two",
                batch_size=1,
                claim_seconds=60,
            )

            async with outbox_factory() as first_writer:
                first_claim = await first_writer.claim_job_dispatches(first_command)
                async with outbox_factory() as second_writer:
                    skipped = await second_writer.claim_job_dispatches(second_command)
                assert len(first_claim) == 1
                assert skipped == ()

            async with session_factory.begin() as session:
                expired_at = datetime.now(UTC) - timedelta(seconds=1)
                await session.execute(
                    update(OutboxEvent)
                    .where(OutboxEvent.id == submitted.outbox_event_id)
                    .values(
                        locked_at=expired_at - timedelta(seconds=60),
                        lock_expires_at=expired_at,
                    )
                )

            async with outbox_factory() as writer:
                reclaimed = await writer.claim_job_dispatches(second_command)
            assert len(reclaimed) == 1
            assert reclaimed[0].attempt_count == 2
            assert reclaimed[0].proof.claim_generation == 2
            assert reclaimed[0].proof.claim_token != first_claim[0].proof.claim_token

            async with outbox_factory() as writer:
                stale_retained = await writer.mark_published(first_claim[0].proof)
            assert stale_retained is False

            await service.acquire(
                AcquireJobCommand(
                    job_id=submitted.job_id,
                    dispatch_generation=submitted.dispatch_generation,
                    worker_id="worker-first",
                )
            )
            async with outbox_factory() as writer:
                retained = await writer.mark_published(reclaimed[0].proof)
            assert retained is True

            async with session_factory() as session:
                job = await session.get(Job, submitted.job_id)
                outbox = await session.get(OutboxEvent, submitted.outbox_event_id)
                dispatched_events = (
                    await session.scalar(
                        select(func.count())
                        .select_from(JobEvent)
                        .where(JobEvent.event_type == JobEventType.DISPATCHED)
                    )
                ) or 0

            assert job is not None
            assert outbox is not None
            assert job.status is JobStatus.RUNNING
            assert outbox.status is OutboxStatus.PUBLISHED
            assert dispatched_events == 0
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())


def test_postgres_exhausted_publish_dead_letters_outbox_job_and_one_event(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    class FailingPublisher:
        async def publish(self, dispatch: object) -> None:
            del dispatch
            raise OutboxPublishError(OutboxPublishErrorCode.CELERY_PUBLISH_FAILED)

    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        service = _job_service(session_factory)
        try:
            submitted = await service.submit(_submission(queue_name="default"))
            async with session_factory.begin() as session:
                await session.execute(
                    update(OutboxEvent)
                    .where(OutboxEvent.id == submitted.outbox_event_id)
                    .values(max_attempts=1)
                )

            dispatcher = OutboxDispatcher(
                transaction_factory=SqlAlchemyOutboxTransactionFactory(session_factory),
                publisher=FailingPublisher(),
                dispatcher_id="failing-dispatcher",
                batch_size=1,
                claim_seconds=60,
            )
            result = await dispatcher.dispatch_once()
            assert result.dead_lettered == 1

            async with session_factory() as session:
                job = await session.get(Job, submitted.job_id)
                outbox = await session.get(OutboxEvent, submitted.outbox_event_id)
                terminal_events = (
                    await session.scalar(
                        select(func.count())
                        .select_from(JobEvent)
                        .where(JobEvent.event_type == JobEventType.DEAD_LETTER)
                    )
                ) or 0

            assert job is not None
            assert outbox is not None
            assert job.status is JobStatus.DEAD_LETTER
            assert outbox.status is OutboxStatus.DEAD_LETTER
            assert job.last_error_code == "celery_publish_failed"
            assert outbox.last_error_code == "celery_publish_failed"
            assert terminal_events == 1
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())
