"""Prove durable Beat materialization and misfire handling in PostgreSQL."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from industry_platform.core.database import (
    AsyncSessionFactory,
    create_database_engine,
    create_database_session_factory,
)
from industry_platform.modules.jobs.adapters.sqlalchemy import (
    SqlAlchemyScheduleTransactionFactory,
    SqlAlchemyScheduleWriter,
)
from industry_platform.modules.jobs.domain import (
    EnsuredSchedule,
    ExecutionScope,
    ManualScheduleTriggerCommand,
    ManualScheduleTriggerResult,
    ScheduleDefinition,
    ScheduleDefinitionConflictError,
    ScheduleMisfireErrorCode,
    ScheduleMisfirePolicy,
    ScheduleOccurrenceStatus,
    ScheduleTickCommand,
    ScheduleTriggerConflictError,
)
from industry_platform.modules.jobs.models import (
    Job,
    JobEvent,
    OutboxEvent,
    Schedule,
    ScheduleOccurrence,
)
from industry_platform.modules.jobs.service import ScheduleApplicationService
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe


def definition(
    name: str,
    *,
    policy: ScheduleMisfirePolicy = ScheduleMisfirePolicy.CATCH_UP_EACH,
    payload: dict[str, object] | None = None,
) -> ScheduleDefinition:
    """Return one short-running system schedule for integration probes."""

    return ScheduleDefinition(
        scope=ExecutionScope(system_scope_key="schedule-integration"),
        name=name,
        task_name="identity.refresh_recovery.cleanup.v1",
        cron_expression="* * * * *",
        timezone_name="Asia/Shanghai",
        payload=payload if payload is not None else {"batch_size": 1000},
        queue_name="default",
        max_attempts=3,
        soft_time_limit_seconds=60,
        hard_time_limit_seconds=120,
        misfire_policy=policy,
    )


async def force_due(
    session_factory: AsyncSessionFactory,
    schedule_id: UUID,
    *,
    overdue_by: timedelta = timedelta(0),
) -> datetime:
    """Set one test schedule due using PostgreSQL's authoritative clock."""

    async with session_factory.begin() as session:
        database_now = await session.scalar(select(func.clock_timestamp()))
        assert isinstance(database_now, datetime)
        schedule = await session.get(Schedule, schedule_id)
        assert schedule is not None
        due_at = (
            database_now.astimezone(UTC).replace(
                second=0,
                microsecond=0,
            )
            - overdue_by
        )
        schedule.next_due_at = due_at
    return due_at


async def force_materialization_rollback(session_factory: AsyncSessionFactory) -> None:
    """Materialize one due schedule and force its surrounding transaction to roll back."""

    async with session_factory.begin() as session:
        writer = SqlAlchemyScheduleWriter(session)
        result = await writer.materialize_due(ScheduleTickCommand(batch_size=5))
        assert result.jobs_created == 1
        raise RuntimeError("force scheduling rollback")


def test_concurrent_beats_and_manual_retries_create_one_durable_graph(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        scheduler = ScheduleApplicationService(
            transaction_factory=SqlAlchemyScheduleTransactionFactory(session_factory),
            batch_size=1,
        )
        schedule_definition = definition("concurrent-cleanup")

        try:
            ensured = await scheduler.ensure_schedule(schedule_definition)
            reused = await scheduler.ensure_schedule(schedule_definition)
            assert ensured.created is True
            assert reused == EnsuredSchedule(
                schedule_id=ensured.schedule_id,
                created=False,
            )
            with pytest.raises(ScheduleDefinitionConflictError):
                await scheduler.ensure_schedule(
                    definition(
                        "concurrent-cleanup",
                        payload={"batch_size": 25},
                    )
                )

            await force_due(session_factory, ensured.schedule_id)
            results = await asyncio.gather(
                scheduler.run_due_once(),
                scheduler.run_due_once(),
            )
            assert sum(result.selected_schedules for result in results) == 1
            assert sum(result.materialized_occurrences for result in results) == 1
            assert sum(result.jobs_created for result in results) == 1

            async with session_factory() as session:
                schedule = await session.get(Schedule, ensured.schedule_id)
                assert schedule is not None
                next_due_before_manual = schedule.next_due_at

            trigger_id = uuid4()
            manual_results = await asyncio.gather(
                scheduler.trigger_manual(
                    ManualScheduleTriggerCommand(
                        schedule_id=ensured.schedule_id,
                        trigger_id=trigger_id,
                    )
                ),
                scheduler.trigger_manual(
                    ManualScheduleTriggerCommand(
                        schedule_id=ensured.schedule_id,
                        trigger_id=trigger_id,
                    )
                ),
            )
            assert len({result.occurrence_id for result in manual_results}) == 1
            assert len({result.job_id for result in manual_results}) == 1
            assert sum(result.created for result in manual_results) == 1

            async with session_factory() as session:
                schedule = await session.get(Schedule, ensured.schedule_id)
                occurrence_count = (
                    await session.scalar(
                        select(func.count())
                        .select_from(ScheduleOccurrence)
                        .where(ScheduleOccurrence.schedule_id == ensured.schedule_id)
                    )
                ) or 0
                job_count = (await session.scalar(select(func.count()).select_from(Job))) or 0
                event_count = (
                    await session.scalar(select(func.count()).select_from(JobEvent))
                ) or 0
                outbox_count = (
                    await session.scalar(select(func.count()).select_from(OutboxEvent))
                ) or 0

            assert schedule is not None
            assert schedule.next_due_at == next_due_before_manual
            assert occurrence_count == 2
            assert (job_count, event_count, outbox_count) == (2, 2, 2)
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())


def test_blocked_misfire_is_visible_and_failed_transaction_does_not_advance(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        scheduler = ScheduleApplicationService(
            transaction_factory=SqlAlchemyScheduleTransactionFactory(session_factory),
            batch_size=5,
        )

        try:
            manual = await scheduler.ensure_schedule(
                definition(
                    "manual-review",
                    policy=ScheduleMisfirePolicy.MANUAL,
                )
            )
            manual_due = await force_due(session_factory, manual.schedule_id)
            blocked = await scheduler.run_due_once()
            assert blocked.blocked_schedules == 1
            assert blocked.jobs_created == 0

            async with session_factory() as session:
                schedule = await session.get(Schedule, manual.schedule_id)
                occurrence = await session.scalar(
                    select(ScheduleOccurrence).where(
                        ScheduleOccurrence.schedule_id == manual.schedule_id
                    )
                )
            assert schedule is not None
            assert schedule.enabled is False
            assert schedule.next_due_at == manual_due
            assert schedule.misfire_error_code == (
                ScheduleMisfireErrorCode.MANUAL_REVIEW_REQUIRED.value
            )
            assert occurrence is not None
            assert occurrence.status is ScheduleOccurrenceStatus.MISFIRE_BLOCKED
            assert occurrence.job_id is None

            rollback = await scheduler.ensure_schedule(definition("rollback"))
            rollback_due = await force_due(session_factory, rollback.schedule_id)
            with pytest.raises(RuntimeError, match="force scheduling rollback"):
                await force_materialization_rollback(session_factory)

            async with session_factory() as session:
                schedule = await session.get(Schedule, rollback.schedule_id)
                occurrence_count = (
                    await session.scalar(
                        select(func.count())
                        .select_from(ScheduleOccurrence)
                        .where(ScheduleOccurrence.schedule_id == rollback.schedule_id)
                    )
                ) or 0
                job_count = (await session.scalar(select(func.count()).select_from(Job))) or 0

            assert schedule is not None
            assert schedule.next_due_at == rollback_due
            assert occurrence_count == 0
            assert job_count == 0
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())


def test_concurrent_cross_schedule_manual_trigger_has_one_global_winner(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        scheduler = ScheduleApplicationService(
            transaction_factory=SqlAlchemyScheduleTransactionFactory(session_factory)
        )

        try:
            first = await scheduler.ensure_schedule(definition("manual-first"))
            second = await scheduler.ensure_schedule(definition("manual-second"))
            trigger_id = uuid4()
            outcomes = await asyncio.gather(
                scheduler.trigger_manual(
                    ManualScheduleTriggerCommand(
                        schedule_id=first.schedule_id,
                        trigger_id=trigger_id,
                    )
                ),
                scheduler.trigger_manual(
                    ManualScheduleTriggerCommand(
                        schedule_id=second.schedule_id,
                        trigger_id=trigger_id,
                    )
                ),
                return_exceptions=True,
            )

            created = [
                outcome for outcome in outcomes if isinstance(outcome, ManualScheduleTriggerResult)
            ]
            conflicts = [
                outcome for outcome in outcomes if isinstance(outcome, ScheduleTriggerConflictError)
            ]
            assert len(created) == 1
            assert created[0].created is True
            assert len(conflicts) == 1

            async with session_factory() as session:
                occurrence_count = (
                    await session.scalar(
                        select(func.count())
                        .select_from(ScheduleOccurrence)
                        .where(ScheduleOccurrence.trigger_id == trigger_id)
                    )
                ) or 0
                job_count = (await session.scalar(select(func.count()).select_from(Job))) or 0
                event_count = (
                    await session.scalar(select(func.count()).select_from(JobEvent))
                ) or 0
                outbox_count = (
                    await session.scalar(select(func.count()).select_from(OutboxEvent))
                ) or 0

            assert occurrence_count == 1
            assert (job_count, event_count, outbox_count) == (1, 1, 1)
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())
