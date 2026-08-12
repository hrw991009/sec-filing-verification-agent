"""Permanent tests for reliable job and schedule domain rules."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from industry_platform.modules.jobs.domain import (
    TERMINAL_JOB_EVENT_TYPES,
    TERMINAL_JOB_STATUSES,
    ExecutionScope,
    JobDefinition,
    JobEventType,
    JobLease,
    JobStatus,
    ScheduleDefinition,
    ScheduleOccurrenceDefinition,
    ScheduleTriggerKind,
)

WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
LEASE_TOKEN = UUID("22222222-2222-4222-8222-222222222222")
SCHEDULE_ID = UUID("33333333-3333-4333-8333-333333333333")
TRIGGER_ID = UUID("44444444-4444-4444-8444-444444444444")
NOW = datetime(2026, 8, 11, 0, 7, tzinfo=UTC)


def test_execution_scope_requires_exactly_one_owner_kind() -> None:
    assert ExecutionScope(workspace_id=WORKSPACE_ID).workspace_id == WORKSPACE_ID
    assert ExecutionScope(system_scope_key="maintenance").system_scope_key == "maintenance"

    with pytest.raises(ValueError, match="workspace or system"):
        ExecutionScope()
    with pytest.raises(ValueError, match="workspace or system"):
        ExecutionScope(workspace_id=WORKSPACE_ID, system_scope_key="maintenance")
    with pytest.raises(ValueError, match="System scope"):
        ExecutionScope(system_scope_key=" invalid scope ")


def test_job_definition_is_utc_bounded_and_payload_is_immutable() -> None:
    job = JobDefinition(
        scope=ExecutionScope(workspace_id=WORKSPACE_ID),
        task_name="research.collect",
        queue_name="default",
        payload={"source": "example"},
        available_at=NOW,
        max_attempts=3,
        idempotency_key="workspace:source:2026-08-11",
    )

    assert job.payload == {"source": "example"}
    assert "example" not in repr(job)
    assert "workspace:source:2026-08-11" not in repr(job)
    with pytest.raises(TypeError):
        job.payload["source"] = "changed"  # type: ignore[index]

    with pytest.raises(ValueError, match="timezone-aware UTC"):
        JobDefinition(
            scope=job.scope,
            task_name=job.task_name,
            queue_name=job.queue_name,
            payload={},
            available_at=NOW.replace(tzinfo=None),
            max_attempts=1,
        )


def test_job_lease_requires_live_fencing_and_heartbeat_window() -> None:
    lease = JobLease(
        owner="worker-1",
        lease_token=LEASE_TOKEN,
        generation=2,
        fencing_token=9,
        heartbeat_at=NOW,
        expires_at=NOW + timedelta(minutes=2),
    )
    assert lease.fencing_token == 9

    with pytest.raises(ValueError, match="expiration"):
        JobLease(
            owner=lease.owner,
            lease_token=lease.lease_token,
            generation=lease.generation,
            fencing_token=lease.fencing_token,
            heartbeat_at=NOW,
            expires_at=NOW,
        )


def test_terminal_sets_are_explicit_and_do_not_include_retryable_states() -> None:
    assert {
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
        JobStatus.DEAD_LETTER,
    } == TERMINAL_JOB_STATUSES
    assert {
        JobEventType.SUCCEEDED,
        JobEventType.FAILED,
        JobEventType.CANCELLED,
        JobEventType.DEAD_LETTER,
    } == TERMINAL_JOB_EVENT_TYPES
    assert JobStatus.RETRY_WAIT not in TERMINAL_JOB_STATUSES
    assert JobEventType.LEASE_EXPIRED not in TERMINAL_JOB_EVENT_TYPES


def test_schedule_validates_cron_timezone_and_calculates_next_utc_occurrence() -> None:
    schedule = ScheduleDefinition(
        scope=ExecutionScope(system_scope_key="maintenance"),
        name="reconcile-jobs",
        task_name="jobs.reconcile",
        cron_expression="*/15 * * * *",
        timezone_name="UTC",
        payload={},
    )

    assert schedule.next_after(NOW) == datetime(2026, 8, 11, 0, 15, tzinfo=UTC)
    assert "payload" not in repr(schedule)

    with pytest.raises(ValueError, match="Cron expression"):
        ScheduleDefinition(
            scope=schedule.scope,
            name=schedule.name,
            task_name=schedule.task_name,
            cron_expression="not a cron expression",
            timezone_name="UTC",
            payload={},
        )


def test_schedule_occurrence_separates_cron_and_manual_idempotency() -> None:
    scheduled = ScheduleOccurrenceDefinition(
        schedule_id=SCHEDULE_ID,
        schedule_version=3,
        trigger_kind=ScheduleTriggerKind.SCHEDULED,
        scheduled_for=NOW,
    )
    manual = ScheduleOccurrenceDefinition(
        schedule_id=SCHEDULE_ID,
        schedule_version=3,
        trigger_kind=ScheduleTriggerKind.MANUAL,
        trigger_id=TRIGGER_ID,
    )

    assert scheduled.scheduled_for == NOW
    assert scheduled.trigger_id is None
    assert manual.scheduled_for is None
    assert manual.trigger_id == TRIGGER_ID

    with pytest.raises(ValueError, match="Scheduled triggers"):
        ScheduleOccurrenceDefinition(
            schedule_id=SCHEDULE_ID,
            schedule_version=3,
            trigger_kind=ScheduleTriggerKind.SCHEDULED,
            trigger_id=TRIGGER_ID,
        )

    with pytest.raises(ValueError, match="Manual triggers"):
        ScheduleOccurrenceDefinition(
            schedule_id=SCHEDULE_ID,
            schedule_version=3,
            trigger_kind=ScheduleTriggerKind.MANUAL,
        )
