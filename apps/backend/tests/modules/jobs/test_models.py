"""Permanent schema tests for jobs, outbox delivery, and schedules."""

from typing import cast

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    LargeBinary,
    Table,
    UniqueConstraint,
)
from sqlalchemy import Enum as SqlEnum

from industry_platform.model_registry import metadata
from industry_platform.modules.jobs.domain import (
    JobEventType,
    JobStatus,
    OutboxStatus,
    ScheduleMisfirePolicy,
    ScheduleTriggerKind,
)
from industry_platform.modules.jobs.models import (
    Job,
    JobEvent,
    OutboxEvent,
    Schedule,
    ScheduleOccurrence,
)

EXPECTED_JOB_TABLES = {
    "jobs",
    "job_events",
    "outbox_events",
    "schedules",
    "schedule_occurrences",
}

ENUM_SPECS: dict[str, tuple[str, frozenset[str], str]] = {
    "jobs": ("status", frozenset(member.value for member in JobStatus), "ck_jobs_status"),
    "job_events": (
        "event_type",
        frozenset(member.value for member in JobEventType),
        "ck_job_events_event_type",
    ),
    "outbox_events": (
        "status",
        frozenset(member.value for member in OutboxStatus),
        "ck_outbox_events_status",
    ),
    "schedules": (
        "misfire_policy",
        frozenset(member.value for member in ScheduleMisfirePolicy),
        "ck_schedules_misfire_policy",
    ),
    "schedule_occurrences": (
        "trigger_kind",
        frozenset(member.value for member in ScheduleTriggerKind),
        "ck_schedule_occurrences_trigger_kind",
    ),
}


def check_names(table: Table) -> set[str]:
    """Return the stable names of every explicit table check."""

    return {
        str(constraint.name)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }


def named_indexes(table: Table) -> dict[str, Index]:
    """Return every explicitly named index for focused invariant assertions."""

    return {str(index.name): index for index in table.indexes if index.name is not None}


def test_job_models_are_registered_for_alembic() -> None:
    assert set(metadata.tables) >= EXPECTED_JOB_TABLES


def test_job_enums_use_stable_varchar_values_and_explicit_checks() -> None:
    for table_name, (column_name, expected_values, constraint_name) in ENUM_SPECS.items():
        table = metadata.tables[table_name]
        enum_type = table.c[column_name].type

        assert isinstance(enum_type, SqlEnum)
        assert enum_type.native_enum is False
        assert enum_type.create_constraint is False
        assert set(enum_type.enums) == expected_values
        assert constraint_name in check_names(table)


def test_job_schema_freezes_dispatch_lease_fencing_and_terminal_facts() -> None:
    table = cast(Table, Job.__table__)

    assert {
        "dispatch_generation",
        "dispatch_attempt",
        "dispatched_at",
        "started_at",
        "generation",
        "lease_owner",
        "lease_token",
        "lease_expires_at",
        "heartbeat_at",
        "fencing_token",
        "stage_name",
        "stage_sequence",
        "cancel_requested_at",
        "terminal_at",
        "soft_time_limit_seconds",
        "hard_time_limit_seconds",
        "idempotency_key_hash",
        "request_fingerprint",
    } <= set(table.c.keys())
    assert "idempotency_key" not in table.c
    for column_name in ("idempotency_key_hash", "request_fingerprint"):
        column_type = table.c[column_name].type

        assert isinstance(column_type, LargeBinary)
        assert column_type.length == 32
    assert {
        "ck_jobs_execution_scope",
        "ck_jobs_dispatch_state_consistent",
        "ck_jobs_dispatched_not_started",
        "ck_jobs_lease_state_consistent",
        "ck_jobs_terminal_state_consistent",
        "ck_jobs_time_limit_bounds",
        "ck_jobs_idempotency_fields_paired",
        "ck_jobs_idempotency_key_hash_length",
        "ck_jobs_request_fingerprint_length",
    } <= check_names(table)

    indexes = named_indexes(table)
    for index_name in (
        "uq_jobs_workspace_idempotency",
        "uq_jobs_system_idempotency",
    ):
        assert indexes[index_name].unique is True
        assert indexes[index_name].dialect_options["postgresql"]["where"] is not None
        assert tuple(column.name for column in indexes[index_name].columns)[-1] == (
            "idempotency_key_hash"
        )


def test_job_events_have_monotonic_coordinates_and_one_terminal_fact() -> None:
    table = cast(Table, JobEvent.__table__)
    unique_column_sets = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("job_id", "generation", "event_sequence") in unique_column_sets
    terminal_index = named_indexes(table)["uq_job_events_one_terminal_per_job"]
    terminal_predicate = str(terminal_index.dialect_options["postgresql"]["where"])

    assert terminal_index.unique is True
    assert "dead_letter" in terminal_predicate
    assert {
        "ck_job_events_generation_nonnegative",
        "ck_job_events_dispatch_generation_nonnegative",
        "ck_job_events_fencing_token_nonnegative",
        "ck_job_events_event_sequence_nonnegative",
    } <= check_names(table)


def test_outbox_schema_supports_safe_claim_retry_and_dispatch_reconciliation() -> None:
    table = cast(Table, OutboxEvent.__table__)

    assert {
        "source_job_id",
        "job_dispatch_generation",
        "next_attempt_at",
        "locked_by",
        "locked_at",
        "lock_expires_at",
        "claim_token",
        "claim_generation",
        "published_at",
        "terminal_at",
        "last_error_code",
    } <= set(table.c.keys())
    assert {
        "ck_outbox_events_execution_scope",
        "ck_outbox_events_job_dispatch_generation_paired",
        "ck_outbox_events_claim_state_consistent",
        "ck_outbox_events_terminal_state_consistent",
        "ck_outbox_events_published_state_consistent",
    } <= check_names(table)

    indexes = named_indexes(table)
    for index_name in (
        "uq_outbox_workspace_deduplication",
        "uq_outbox_system_deduplication",
        "uq_outbox_job_dispatch_generation",
    ):
        assert indexes[index_name].unique is True
        assert indexes[index_name].dialect_options["postgresql"]["where"] is not None


def test_schedule_schema_keeps_misfires_visible_and_occurrences_idempotent() -> None:
    schedule_table = cast(Table, Schedule.__table__)
    occurrence_table = cast(Table, ScheduleOccurrence.__table__)

    assert {
        "cron_expression",
        "timezone_name",
        "misfire_policy",
        "catch_up_window_seconds",
        "max_catch_up",
        "version",
        "next_due_at",
        "misfire_blocked_at",
        "missed_from",
        "missed_through",
        "missed_count",
    } <= set(schedule_table.c.keys())
    assert {
        "ck_schedules_execution_scope",
        "ck_schedules_misfire_block_state_consistent",
        "ck_schedules_enabled_has_next_fire",
    } <= check_names(schedule_table)

    schedule_indexes = named_indexes(schedule_table)
    for index_name in (
        "uq_schedules_workspace_name",
        "uq_schedules_system_name",
    ):
        assert schedule_indexes[index_name].unique is True
        assert schedule_indexes[index_name].dialect_options["postgresql"]["where"] is not None

    occurrence_unique_sets = {
        tuple(column.name for column in constraint.columns)
        for constraint in occurrence_table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("job_id",) in occurrence_unique_sets

    assert {"trigger_kind", "scheduled_for", "trigger_id"} <= set(occurrence_table.c.keys())
    assert {
        "ck_schedule_occurrences_trigger_kind",
        "ck_schedule_occurrences_trigger_fields_consistent",
    } <= check_names(occurrence_table)

    occurrence_indexes = named_indexes(occurrence_table)
    assert tuple(
        column.name for column in occurrence_indexes["uq_schedule_occurrences_scheduled"].columns
    ) == ("schedule_id", "scheduled_for")
    assert tuple(
        column.name
        for column in occurrence_indexes["uq_schedule_occurrences_manual_trigger"].columns
    ) == ("trigger_id",)
    for index_name in (
        "uq_schedule_occurrences_scheduled",
        "uq_schedule_occurrences_manual_trigger",
    ):
        assert occurrence_indexes[index_name].unique is True
        assert occurrence_indexes[index_name].dialect_options["postgresql"]["where"] is not None


def test_every_persisted_job_timestamp_is_timezone_aware() -> None:
    for table_name in EXPECTED_JOB_TABLES:
        for column in metadata.tables[table_name].columns:
            if isinstance(column.type, DateTime):
                assert column.type.timezone is True
