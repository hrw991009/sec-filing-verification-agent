"""PostgreSQL source-of-truth models for jobs, outbox, and schedules."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from industry_platform.core.database import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from industry_platform.modules.identity.models import enum_values
from industry_platform.modules.jobs.domain import (
    JobEventType,
    JobStatus,
    OutboxStatus,
    ScheduleMisfirePolicy,
    ScheduleOccurrenceStatus,
    ScheduleTriggerKind,
)

_SCOPE_CHECK = (
    "(workspace_id IS NOT NULL AND system_scope_key IS NULL) OR "
    "(workspace_id IS NULL AND system_scope_key IS NOT NULL "
    "AND length(btrim(system_scope_key)) > 0)"
)
_TERMINAL_JOB_VALUES = "'succeeded', 'failed', 'cancelled', 'dead_letter'"
_TERMINAL_EVENT_VALUES = "'succeeded', 'failed', 'cancelled', 'dead_letter'"
_TERMINAL_OUTBOX_VALUES = "'published', 'dead_letter'"


class Job(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One logical operation whose authoritative state lives in PostgreSQL."""

    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id"),
        CheckConstraint(_SCOPE_CHECK, name="execution_scope"),
        CheckConstraint(
            "status IN ('pending', 'dispatched', 'running', 'retry_wait', "
            "'succeeded', 'failed', 'cancelled', 'dead_letter')",
            name="status",
        ),
        CheckConstraint("length(btrim(task_name)) > 0", name="task_name_not_blank"),
        CheckConstraint("length(btrim(queue_name)) > 0", name="queue_name_not_blank"),
        CheckConstraint("length(btrim(trace_id)) > 0", name="trace_id_not_blank"),
        CheckConstraint(
            "(idempotency_key_hash IS NULL AND request_fingerprint IS NULL) OR "
            "(idempotency_key_hash IS NOT NULL AND request_fingerprint IS NOT NULL)",
            name="idempotency_fields_paired",
        ),
        CheckConstraint(
            "idempotency_key_hash IS NULL OR octet_length(idempotency_key_hash) = 32",
            name="idempotency_key_hash_length",
        ),
        CheckConstraint(
            "request_fingerprint IS NULL OR octet_length(request_fingerprint) = 32",
            name="request_fingerprint_length",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 1 AND attempt_count <= max_attempts",
            name="attempt_bounds",
        ),
        CheckConstraint("generation >= 0", name="generation_nonnegative"),
        CheckConstraint("dispatch_generation >= 0", name="dispatch_generation_nonnegative"),
        CheckConstraint("dispatch_attempt >= 0", name="dispatch_attempt_nonnegative"),
        CheckConstraint("fencing_token >= 0", name="fencing_token_nonnegative"),
        CheckConstraint("priority BETWEEN -100 AND 100", name="priority_bounds"),
        CheckConstraint("stage_sequence >= 0", name="stage_sequence_nonnegative"),
        CheckConstraint("length(btrim(stage_name)) > 0", name="stage_name_not_blank"),
        CheckConstraint(
            "lease_owner IS NULL OR length(btrim(lease_owner)) > 0",
            name="lease_owner_not_blank",
        ),
        CheckConstraint(
            "soft_time_limit_seconds >= 1 "
            "AND hard_time_limit_seconds > soft_time_limit_seconds "
            "AND hard_time_limit_seconds <= 1800",
            name="time_limit_bounds",
        ),
        CheckConstraint(
            "(dispatch_generation = 0 AND dispatch_attempt = 0 "
            "AND dispatched_at IS NULL AND started_at IS NULL) OR "
            "(dispatch_generation > 0 AND "
            "((dispatch_attempt = 0 AND dispatched_at IS NULL "
            "AND started_at IS NULL) OR "
            "(dispatch_attempt > 0 AND dispatched_at IS NOT NULL)))",
            name="dispatch_state_consistent",
        ),
        CheckConstraint(
            "status <> 'dispatched' OR (dispatched_at IS NOT NULL AND started_at IS NULL)",
            name="dispatched_not_started",
        ),
        CheckConstraint(
            "status <> 'running' OR started_at IS NOT NULL",
            name="running_has_started",
        ),
        CheckConstraint(
            "started_at IS NULL OR dispatched_at <= started_at",
            name="dispatch_start_order",
        ),
        CheckConstraint(
            "(status = 'running' AND lease_owner IS NOT NULL "
            "AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND heartbeat_at IS NOT NULL AND fencing_token > 0 "
            "AND lease_expires_at > heartbeat_at) OR "
            "(status <> 'running' AND lease_owner IS NULL "
            "AND lease_token IS NULL AND lease_expires_at IS NULL "
            "AND heartbeat_at IS NULL)",
            name="lease_state_consistent",
        ),
        CheckConstraint(
            f"(status IN ({_TERMINAL_JOB_VALUES}) AND terminal_at IS NOT NULL) OR "
            f"(status NOT IN ({_TERMINAL_JOB_VALUES}) AND terminal_at IS NULL)",
            name="terminal_state_consistent",
        ),
        CheckConstraint(
            "result IS NULL OR status = 'succeeded'",
            name="result_only_on_success",
        ),
        CheckConstraint("payload_schema_version > 0", name="payload_schema_version_positive"),
        Index(
            "uq_jobs_workspace_idempotency",
            "workspace_id",
            "task_name",
            "idempotency_key_hash",
            unique=True,
            postgresql_where=text("workspace_id IS NOT NULL AND idempotency_key_hash IS NOT NULL"),
        ),
        Index(
            "uq_jobs_system_idempotency",
            "system_scope_key",
            "task_name",
            "idempotency_key_hash",
            unique=True,
            postgresql_where=text(
                "system_scope_key IS NOT NULL AND idempotency_key_hash IS NOT NULL"
            ),
        ),
        Index(None, "status", "available_at", "priority", "created_at"),
        Index(
            "ix_jobs_expired_leases",
            "lease_expires_at",
            postgresql_where=text("status = 'running'"),
        ),
    )

    workspace_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=True,
    )
    system_scope_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    task_name: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    queue_name: Mapped[str] = mapped_column(String(64), nullable=False, server_default="default")
    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    payload_schema_version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default=text("1")
    )
    status: Mapped[JobStatus] = mapped_column(
        SqlEnum(
            JobStatus,
            name="job_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
        default=JobStatus.PENDING,
        server_default=JobStatus.PENDING.value,
    )
    idempotency_key_hash: Mapped[bytes | None] = mapped_column(LargeBinary(32), nullable=True)
    request_fingerprint: Mapped[bytes | None] = mapped_column(LargeBinary(32), nullable=True)
    priority: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default=text("0")
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    attempt_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=3, server_default=text("3")
    )
    generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    dispatch_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    dispatch_attempt: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default=text("0")
    )
    fencing_token: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    stage_name: Mapped[str] = mapped_column(
        String(100), nullable=False, default="pending", server_default="pending"
    )
    stage_sequence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    soft_time_limit_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1_500, server_default=text("1500")
    )
    hard_time_limit_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1_800, server_default=text("1800")
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_token: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    result: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)


class JobEvent(UUIDPrimaryKeyMixin, Base):
    """Immutable append-only fact for auditing one job lifecycle."""

    __tablename__ = "job_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('created', 'dispatched', 'started', 'heartbeat', "
            "'retry_scheduled', 'lease_expired', 'succeeded', 'failed', "
            "'cancelled', 'dead_letter')",
            name="event_type",
        ),
        CheckConstraint("generation >= 0", name="generation_nonnegative"),
        CheckConstraint("dispatch_generation >= 0", name="dispatch_generation_nonnegative"),
        CheckConstraint("fencing_token >= 0", name="fencing_token_nonnegative"),
        CheckConstraint("event_sequence >= 0", name="event_sequence_nonnegative"),
        UniqueConstraint("job_id", "generation", "event_sequence"),
        Index(
            "uq_job_events_one_terminal_per_job",
            "job_id",
            unique=True,
            postgresql_where=text(f"event_type IN ({_TERMINAL_EVENT_VALUES})"),
        ),
        Index(None, "job_id", "occurred_at"),
    )

    job_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[JobEventType] = mapped_column(
        SqlEnum(
            JobEventType,
            name="job_event_type",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=24,
        ),
        nullable=False,
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    dispatch_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    details: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class OutboxEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Durable event claimed before publication to the Celery broker."""

    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint(_SCOPE_CHECK, name="execution_scope"),
        CheckConstraint(
            "status IN ('pending', 'publishing', 'published', 'dead_letter')",
            name="status",
        ),
        CheckConstraint("length(btrim(topic)) > 0", name="topic_not_blank"),
        CheckConstraint("length(btrim(event_type)) > 0", name="event_type_not_blank"),
        CheckConstraint(
            "deduplication_key IS NULL OR length(btrim(deduplication_key)) > 0",
            name="deduplication_key_not_blank",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 1 AND attempt_count <= max_attempts",
            name="attempt_bounds",
        ),
        CheckConstraint("claim_generation >= 0", name="claim_generation_nonnegative"),
        CheckConstraint(
            "locked_by IS NULL OR length(btrim(locked_by)) > 0",
            name="locked_by_not_blank",
        ),
        CheckConstraint(
            "(source_job_id IS NULL AND job_dispatch_generation IS NULL) OR "
            "(source_job_id IS NOT NULL AND job_dispatch_generation > 0)",
            name="job_dispatch_generation_paired",
        ),
        CheckConstraint(
            "(status = 'publishing' AND locked_by IS NOT NULL "
            "AND claim_token IS NOT NULL AND locked_at IS NOT NULL "
            "AND lock_expires_at IS NOT NULL AND claim_generation > 0 "
            "AND lock_expires_at > locked_at) OR "
            "(status <> 'publishing' AND locked_by IS NULL "
            "AND claim_token IS NULL AND locked_at IS NULL "
            "AND lock_expires_at IS NULL)",
            name="claim_state_consistent",
        ),
        CheckConstraint(
            f"(status IN ({_TERMINAL_OUTBOX_VALUES}) AND terminal_at IS NOT NULL) OR "
            f"(status NOT IN ({_TERMINAL_OUTBOX_VALUES}) AND terminal_at IS NULL)",
            name="terminal_state_consistent",
        ),
        CheckConstraint(
            "(status = 'published' AND published_at IS NOT NULL) OR "
            "(status <> 'published' AND published_at IS NULL)",
            name="published_state_consistent",
        ),
        Index(
            "uq_outbox_workspace_deduplication",
            "workspace_id",
            "topic",
            "deduplication_key",
            unique=True,
            postgresql_where=text("workspace_id IS NOT NULL AND deduplication_key IS NOT NULL"),
        ),
        Index(
            "uq_outbox_system_deduplication",
            "system_scope_key",
            "topic",
            "deduplication_key",
            unique=True,
            postgresql_where=text("system_scope_key IS NOT NULL AND deduplication_key IS NOT NULL"),
        ),
        Index(
            "uq_outbox_job_dispatch_generation",
            "source_job_id",
            "job_dispatch_generation",
            unique=True,
            postgresql_where=text("source_job_id IS NOT NULL"),
        ),
        Index(None, "status", "next_attempt_at", "created_at"),
        Index(
            "ix_outbox_events_expired_claims",
            "lock_expires_at",
            postgresql_where=text("status = 'publishing'"),
        ),
    )

    workspace_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=True,
    )
    system_scope_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_job_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=True
    )
    job_dispatch_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    topic: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    deduplication_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[OutboxStatus] = mapped_column(
        SqlEnum(
            OutboxStatus,
            name="outbox_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
        default=OutboxStatus.PENDING,
        server_default=OutboxStatus.PENDING.value,
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    attempt_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=20, server_default=text("20")
    )
    claim_generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    locked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claim_token: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lock_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)


class Schedule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Durable cron definition scanned by a high-availability beat process."""

    __tablename__ = "schedules"
    __table_args__ = (
        CheckConstraint(_SCOPE_CHECK, name="execution_scope"),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        CheckConstraint("length(btrim(task_name)) > 0", name="task_name_not_blank"),
        CheckConstraint("length(btrim(queue_name)) > 0", name="queue_name_not_blank"),
        CheckConstraint("length(btrim(cron_expression)) > 0", name="cron_not_blank"),
        CheckConstraint("length(btrim(timezone_name)) > 0", name="timezone_not_blank"),
        CheckConstraint(
            "misfire_policy IN ('catch_up_each', 'coalesce_latest', 'manual')",
            name="misfire_policy",
        ),
        CheckConstraint(
            "max_attempts >= 1 AND max_attempts <= 100",
            name="attempt_bounds",
        ),
        CheckConstraint("priority BETWEEN -100 AND 100", name="priority_bounds"),
        CheckConstraint(
            "soft_time_limit_seconds >= 1 "
            "AND hard_time_limit_seconds > soft_time_limit_seconds "
            "AND hard_time_limit_seconds <= 1800",
            name="time_limit_bounds",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "catch_up_window_seconds >= 1 AND catch_up_window_seconds <= 604800",
            name="catch_up_window_bounds",
        ),
        CheckConstraint(
            "max_catch_up >= 1 AND max_catch_up <= 1000",
            name="max_catch_up_bounds",
        ),
        CheckConstraint(
            "NOT enabled OR next_due_at IS NOT NULL",
            name="enabled_has_next_fire",
        ),
        CheckConstraint(
            "last_fired_at IS NULL OR next_due_at IS NULL OR last_fired_at < next_due_at",
            name="fire_order",
        ),
        CheckConstraint(
            "(misfire_blocked_at IS NULL AND misfire_error_code IS NULL "
            "AND missed_from IS NULL AND missed_through IS NULL "
            "AND missed_count = 0 AND NOT missed_count_is_lower_bound) OR "
            "(misfire_blocked_at IS NOT NULL AND NOT enabled "
            "AND misfire_error_code IS NOT NULL AND next_due_at IS NOT NULL "
            "AND missed_from IS NOT NULL AND missed_through IS NOT NULL "
            "AND missed_count > 0 AND missed_from <= missed_through)",
            name="misfire_block_state_consistent",
        ),
        Index(
            "uq_schedules_workspace_name",
            "workspace_id",
            "name",
            unique=True,
            postgresql_where=text("workspace_id IS NOT NULL"),
        ),
        Index(
            "uq_schedules_system_name",
            "system_scope_key",
            "name",
            unique=True,
            postgresql_where=text("system_scope_key IS NOT NULL"),
        ),
        Index(
            "ix_schedules_due",
            "next_due_at",
            "id",
            postgresql_where=text("enabled"),
        ),
    )

    workspace_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=True,
    )
    system_scope_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    task_name: Mapped[str] = mapped_column(String(128), nullable=False)
    queue_name: Mapped[str] = mapped_column(String(64), nullable=False, server_default="default")
    max_attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=3, server_default=text("3")
    )
    priority: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default=text("0")
    )
    soft_time_limit_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1_500, server_default=text("1500")
    )
    hard_time_limit_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1_800, server_default=text("1800")
    )
    cron_expression: Mapped[str] = mapped_column(String(120), nullable=False)
    timezone_name: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    misfire_policy: Mapped[ScheduleMisfirePolicy] = mapped_column(
        SqlEnum(
            ScheduleMisfirePolicy,
            name="schedule_misfire_policy",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
        default=ScheduleMisfirePolicy.CATCH_UP_EACH,
        server_default=ScheduleMisfirePolicy.CATCH_UP_EACH.value,
    )
    catch_up_window_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=86_400, server_default=text("86400")
    )
    max_catch_up: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=100, server_default=text("100")
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    misfire_blocked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    misfire_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    missed_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    missed_through: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    missed_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    missed_count_is_lower_bound: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


class ScheduleOccurrence(UUIDPrimaryKeyMixin, Base):
    """Idempotent scheduled or manual materialization into one logical job."""

    __tablename__ = "schedule_occurrences"
    __table_args__ = (
        UniqueConstraint("job_id"),
        CheckConstraint(
            "trigger_kind IN ('scheduled', 'manual')",
            name="trigger_kind",
        ),
        CheckConstraint(
            "status IN ('materialized', 'misfire_blocked')",
            name="status",
        ),
        CheckConstraint(
            "(trigger_kind = 'scheduled' AND scheduled_for IS NOT NULL "
            "AND trigger_id IS NULL) OR "
            "(trigger_kind = 'manual' AND scheduled_for IS NULL "
            "AND trigger_id IS NOT NULL)",
            name="trigger_fields_consistent",
        ),
        CheckConstraint(
            "(status = 'materialized' AND job_id IS NOT NULL "
            "AND error_code IS NULL) OR "
            "(status = 'misfire_blocked' AND job_id IS NULL "
            "AND error_code IS NOT NULL)",
            name="materialization_state_consistent",
        ),
        CheckConstraint(
            "trigger_kind = 'manual' OR "
            "(window_start <= scheduled_for AND scheduled_for <= window_end)",
            name="window_contains_scheduled_time",
        ),
        CheckConstraint("coalesced_count >= 1", name="coalesced_count_positive"),
        CheckConstraint(
            "utc_offset_seconds > -86400 AND utc_offset_seconds < 86400",
            name="utc_offset_bounds",
        ),
        CheckConstraint("schedule_version >= 1", name="version_positive"),
        Index(
            "uq_schedule_occurrences_scheduled",
            "schedule_id",
            "scheduled_for",
            unique=True,
            postgresql_where=text("trigger_kind = 'scheduled'"),
        ),
        Index(
            "uq_schedule_occurrences_manual_trigger",
            "trigger_id",
            unique=True,
            postgresql_where=text("trigger_kind = 'manual'"),
        ),
        Index(None, "schedule_id", "created_at"),
    )

    schedule_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("schedules.id", ondelete="RESTRICT"),
        nullable=False,
    )
    job_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("jobs.id", ondelete="RESTRICT"),
        nullable=True,
    )
    trigger_kind: Mapped[ScheduleTriggerKind] = mapped_column(
        SqlEnum(
            ScheduleTriggerKind,
            name="schedule_trigger_kind",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trigger_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    schedule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ScheduleOccurrenceStatus] = mapped_column(
        SqlEnum(
            ScheduleOccurrenceStatus,
            name="schedule_occurrence_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    coalesced_count: Mapped[int] = mapped_column(Integer, nullable=False)
    dst_adjusted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    utc_offset_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
