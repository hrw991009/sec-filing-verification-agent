"""Permanent tests for cron planning and the database-only Beat boundary."""

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from celery import Celery

import industry_platform.workers.beat as beat_module
from industry_platform.core.config import Settings
from industry_platform.modules.jobs.domain import (
    EnsuredSchedule,
    ExecutionScope,
    ManualScheduleTriggerCommand,
    ManualScheduleTriggerResult,
    PlannedScheduleOccurrence,
    ScheduleDefinition,
    ScheduleMisfireErrorCode,
    ScheduleMisfirePolicy,
    ScheduleOccurrenceStatus,
    SchedulePlan,
    ScheduleTickResult,
    plan_due_schedule,
)
from industry_platform.workers.beat import (
    SCHEDULE_BATCH_SIZE,
    DatabaseScheduleScheduler,
    default_refresh_cleanup_schedule,
)

SCHEDULE_ID = UUID("11111111-1111-4111-8111-111111111111")


def schedule_definition(
    *,
    cron_expression: str = "0 * * * *",
    timezone_name: str = "UTC",
    policy: ScheduleMisfirePolicy = ScheduleMisfirePolicy.CATCH_UP_EACH,
    catch_up_window_seconds: int = 86_400,
    max_catch_up: int = 100,
) -> ScheduleDefinition:
    return ScheduleDefinition(
        scope=ExecutionScope(system_scope_key="test-scheduler"),
        name="test-schedule",
        task_name="test.schedule.task",
        cron_expression=cron_expression,
        timezone_name=timezone_name,
        payload={"safe": True},
        misfire_policy=policy,
        catch_up_window_seconds=catch_up_window_seconds,
        max_catch_up=max_catch_up,
    )


@pytest.mark.parametrize(
    ("cron_expression", "timezone_name", "message"),
    [
        ("0 0 * * * *", "UTC", "Cron expression"),
        ("0 * * * *", "Not/A-Timezone", "Schedule timezone"),
    ],
)
def test_schedule_rejects_non_five_field_cron_and_unknown_timezone(
    cron_expression: str,
    timezone_name: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        schedule_definition(
            cron_expression=cron_expression,
            timezone_name=timezone_name,
        )


@pytest.mark.parametrize(
    ("policy", "expected_times", "expected_window", "blocked"),
    [
        (
            ScheduleMisfirePolicy.CATCH_UP_EACH,
            (
                datetime(2026, 8, 11, 0, tzinfo=UTC),
                datetime(2026, 8, 11, 1, tzinfo=UTC),
                datetime(2026, 8, 11, 2, tzinfo=UTC),
                datetime(2026, 8, 11, 3, tzinfo=UTC),
            ),
            None,
            False,
        ),
        (
            ScheduleMisfirePolicy.COALESCE_LATEST,
            (datetime(2026, 8, 11, 3, tzinfo=UTC),),
            (
                datetime(2026, 8, 11, 0, tzinfo=UTC),
                datetime(2026, 8, 11, 3, tzinfo=UTC),
                4,
            ),
            False,
        ),
        (
            ScheduleMisfirePolicy.MANUAL,
            (datetime(2026, 8, 11, 0, tzinfo=UTC),),
            (
                datetime(2026, 8, 11, 0, tzinfo=UTC),
                datetime(2026, 8, 11, 3, tzinfo=UTC),
                4,
            ),
            True,
        ),
    ],
)
def test_misfire_policies_preserve_each_coalesced_and_manual_evidence(
    policy: ScheduleMisfirePolicy,
    expected_times: tuple[datetime, ...],
    expected_window: tuple[datetime, datetime, int] | None,
    blocked: bool,
) -> None:
    next_due_at = datetime(2026, 8, 11, 0, tzinfo=UTC)
    plan = plan_due_schedule(
        schedule_definition(policy=policy),
        next_due_at=next_due_at,
        database_now=datetime(2026, 8, 11, 3, 30, tzinfo=UTC),
    )

    assert tuple(item.scheduled_for for item in plan.occurrences) == expected_times
    assert plan.blocked is blocked
    if expected_window is None:
        assert all(item.coalesced_count == 1 for item in plan.occurrences)
        assert plan.next_due_at == datetime(2026, 8, 11, 4, tzinfo=UTC)
    else:
        occurrence = plan.occurrences[0]
        assert (
            occurrence.window_start,
            occurrence.window_end,
            occurrence.coalesced_count,
        ) == expected_window

    if policy is ScheduleMisfirePolicy.MANUAL:
        assert plan.next_due_at == next_due_at
        assert plan.error_code is ScheduleMisfireErrorCode.MANUAL_REVIEW_REQUIRED
        assert plan.missed_count_is_lower_bound is False


def test_catch_up_window_allows_exact_boundary_and_blocks_one_second_beyond() -> None:
    definition = schedule_definition(catch_up_window_seconds=86_400)
    next_due_at = datetime(2026, 8, 10, 0, tzinfo=UTC)

    at_boundary = plan_due_schedule(
        definition,
        next_due_at=next_due_at,
        database_now=next_due_at + timedelta(hours=24),
    )
    beyond_boundary = plan_due_schedule(
        definition,
        next_due_at=next_due_at,
        database_now=next_due_at + timedelta(hours=24, seconds=1),
    )

    assert at_boundary.blocked is False
    assert len(at_boundary.occurrences) == 25
    assert beyond_boundary.blocked is True
    assert beyond_boundary.next_due_at == next_due_at
    assert beyond_boundary.error_code is ScheduleMisfireErrorCode.WINDOW_EXCEEDED
    assert beyond_boundary.missed_count_is_lower_bound is True


def test_catch_up_limit_allows_one_hundred_and_blocks_one_hundred_one() -> None:
    definition = schedule_definition(
        cron_expression="* * * * *",
        max_catch_up=100,
    )
    next_due_at = datetime(2026, 8, 11, 0, tzinfo=UTC)

    at_limit = plan_due_schedule(
        definition,
        next_due_at=next_due_at,
        database_now=next_due_at + timedelta(minutes=99),
    )
    beyond_limit = plan_due_schedule(
        definition,
        next_due_at=next_due_at,
        database_now=next_due_at + timedelta(minutes=100),
    )

    assert at_limit.blocked is False
    assert len(at_limit.occurrences) == 100
    assert beyond_limit.blocked is True
    assert beyond_limit.next_due_at == next_due_at
    assert beyond_limit.error_code is ScheduleMisfireErrorCode.LIMIT_EXCEEDED
    assert beyond_limit.missed_count == 101
    assert beyond_limit.missed_count_is_lower_bound is True


def test_paris_spring_gap_moves_to_first_valid_minute_with_dst_evidence() -> None:
    definition = schedule_definition(
        cron_expression="1 2 * * *",
        timezone_name="Europe/Paris",
    )

    occurrence = definition.next_occurrence_after(datetime(2026, 3, 28, 1, 1, tzinfo=UTC))

    assert occurrence.scheduled_for == datetime(2026, 3, 29, 1, tzinfo=UTC)
    assert occurrence.dst_adjusted is True
    assert occurrence.utc_offset_seconds == 7_200


def test_paris_fall_fold_uses_earlier_offset_once() -> None:
    definition = schedule_definition(
        cron_expression="30 2 * * *",
        timezone_name="Europe/Paris",
    )

    folded = definition.next_occurrence_after(datetime(2026, 10, 24, 0, 30, tzinfo=UTC))
    following = definition.next_occurrence_after(folded.scheduled_for)

    assert folded.scheduled_for == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    assert folded.utc_offset_seconds == 7_200
    assert folded.dst_adjusted is False
    assert following.scheduled_for == datetime(2026, 10, 26, 1, 30, tzinfo=UTC)
    assert following.utc_offset_seconds == 3_600


def test_paris_previous_occurrence_uses_absolute_time_inside_second_fold() -> None:
    definition = schedule_definition(
        cron_expression="30 2 * * *",
        timezone_name="Europe/Paris",
    )

    occurrence = definition.previous_occurrence_at_or_before(
        datetime(2026, 10, 25, 1, 15, tzinfo=UTC)
    )

    assert occurrence.scheduled_for == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    assert occurrence.utc_offset_seconds == 7_200
    assert occurrence.dst_adjusted is False


def test_shanghai_cron_has_stable_offset_and_no_dst_adjustment() -> None:
    definition = schedule_definition(
        cron_expression="*/15 * * * *",
        timezone_name="Asia/Shanghai",
    )

    occurrence = definition.next_occurrence_after(datetime(2026, 8, 11, 0, 7, tzinfo=UTC))

    assert occurrence.scheduled_for == datetime(2026, 8, 11, 0, 15, tzinfo=UTC)
    assert occurrence.utc_offset_seconds == 28_800
    assert occurrence.dst_adjusted is False


def test_schedule_plan_rejects_inconsistent_blocked_and_runnable_states() -> None:
    due = datetime(2026, 8, 11, 0, tzinfo=UTC)
    materialized = PlannedScheduleOccurrence(
        status=ScheduleOccurrenceStatus.MATERIALIZED,
        scheduled_for=due,
        window_start=due,
        window_end=due,
        coalesced_count=1,
        dst_adjusted=False,
        utc_offset_seconds=0,
    )

    with pytest.raises(ValueError, match="error state"):
        PlannedScheduleOccurrence(
            status=ScheduleOccurrenceStatus.MISFIRE_BLOCKED,
            scheduled_for=due,
            window_start=due,
            window_end=due,
            coalesced_count=1,
            dst_adjusted=False,
            utc_offset_seconds=0,
        )
    with pytest.raises(ValueError, match="misfire evidence"):
        SchedulePlan(
            occurrences=(materialized,),
            next_due_at=due + timedelta(hours=1),
            blocked=False,
            error_code=ScheduleMisfireErrorCode.LIMIT_EXCEEDED,
        )


def test_default_refresh_cleanup_schedule_is_frozen_to_operational_contract(
    test_settings: Settings,
) -> None:
    definition = default_refresh_cleanup_schedule(test_settings)

    assert definition.scope == ExecutionScope(system_scope_key="maintenance")
    assert definition.name == "identity-refresh-recovery-cleanup"
    assert definition.task_name == "identity.refresh_recovery.cleanup.v1"
    assert definition.timezone_name == "Asia/Shanghai"
    assert definition.cron_expression == "*/15 * * * *"
    assert definition.misfire_policy is ScheduleMisfirePolicy.COALESCE_LATEST
    assert definition.catch_up_window_seconds == 86_400
    assert definition.max_catch_up == 100
    assert definition.payload == {"batch_size": 1_000}
    assert definition.queue_name == test_settings.job_default_queue


class RecordingScheduleService:
    """Small service double for the synchronous Celery scheduler boundary."""

    def __init__(self, results: list[ScheduleTickResult]) -> None:
        self.results = results
        self.ensured: list[ScheduleDefinition] = []
        self.tick_count = 0

    async def ensure_schedule(
        self,
        definition: ScheduleDefinition,
    ) -> EnsuredSchedule:
        self.ensured.append(definition)
        return EnsuredSchedule(schedule_id=SCHEDULE_ID, created=True)

    async def run_due_once(self) -> ScheduleTickResult:
        result = self.results[self.tick_count]
        self.tick_count += 1
        return result

    async def trigger_manual(
        self,
        command: ManualScheduleTriggerCommand,
    ) -> ManualScheduleTriggerResult:
        del command
        raise AssertionError("Beat must not issue manual triggers")


def test_database_scheduler_ensures_once_and_ticks_without_celery_entries(
    test_settings: Settings,
) -> None:
    service = RecordingScheduleService(
        [
            ScheduleTickResult(
                selected_schedules=SCHEDULE_BATCH_SIZE,
                materialized_occurrences=SCHEDULE_BATCH_SIZE,
                jobs_created=SCHEDULE_BATCH_SIZE,
                blocked_schedules=0,
            ),
            ScheduleTickResult(
                selected_schedules=1,
                materialized_occurrences=1,
                jobs_created=1,
                blocked_schedules=0,
            ),
        ]
    )
    app = Celery("scheduler-test", broker="memory://")
    scheduler = DatabaseScheduleScheduler(
        app=app,
        schedule={"must-not-publish": object()},
        settings=test_settings,
        service=service,
    )
    try:
        assert scheduler.schedule == {}
        assert app.conf.beat_schedule == {}
        assert service.ensured == [default_refresh_cleanup_schedule(test_settings)]
        assert scheduler.tick() == 0.0
        assert scheduler.tick() == float(test_settings.scheduler_scan_interval_seconds)
        assert service.tick_count == 2
    finally:
        scheduler.close()
        app.close()


def test_lazy_celery_scheduler_introspection_opens_no_database_resources(
    test_settings: Settings,
) -> None:
    service = RecordingScheduleService([])
    app = Celery("lazy-scheduler-test", broker="memory://")
    scheduler = DatabaseScheduleScheduler(
        app=app,
        settings=test_settings,
        service=service,
        lazy=True,
    )
    try:
        assert scheduler.schedule == {}
        assert service.ensured == []
        assert service.tick_count == 0
    finally:
        scheduler.close()
        app.close()


def test_beat_module_never_calls_celery_publication_apis() -> None:
    module_path = beat_module.__file__
    assert module_path is not None
    source_path = Path(module_path)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    called_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert {"send_task", "delay", "apply_async"}.isdisjoint(called_attributes)
