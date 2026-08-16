"""PostgreSQL source-of-truth models for unified Agent execution facts."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from industry_platform.core.database import Base, TimestampMixin, UUIDPrimaryKeyMixin
from industry_platform.modules.agent_runtime.domain import (
    AgentRunStatus,
    AgentRunType,
    AgentStepKind,
    AgentStepStatus,
    RunArtifactKind,
    RunStopReason,
)
from industry_platform.modules.agent_runtime.events import AgentEventType
from industry_platform.modules.identity.models import enum_values

_TERMINAL_RUNS = "'completed', 'failed', 'cancelled'"
_TERMINAL_EVENTS = "'agent.run.completed', 'agent.run.failed', 'agent.run.cancelled'"


class AgentRunRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Current authoritative projection of one AgentRun."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id"),
        UniqueConstraint("event_stream_id"),
        UniqueConstraint("job_id"),
        ForeignKeyConstraint(
            ["conversation_id", "workspace_id"],
            ["conversations.id", "conversations.workspace_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["turn_id", "workspace_id"],
            ["conversation_turns.id", "conversation_turns.workspace_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("run_type IN ('direct_answer', 'tool_loop', 'research')", name="run_type"),
        CheckConstraint(
            "status IN ('queued', 'running', 'paused', 'completed', 'failed', 'cancelled')",
            name="status",
        ),
        CheckConstraint("state_revision >= 0", name="state_revision_nonnegative"),
        CheckConstraint("event_count >= 1", name="event_count_positive"),
        CheckConstraint("step_count >= 0", name="step_count_nonnegative"),
        CheckConstraint(
            "input_tokens_used >= 0 AND output_tokens_used >= 0 "
            "AND cached_input_tokens_used >= 0 AND cost_micro_usd >= 0",
            name="usage_nonnegative",
        ),
        CheckConstraint(
            f"(status IN ({_TERMINAL_RUNS}) AND terminal_at IS NOT NULL "
            "AND stop_reason IS NOT NULL) OR "
            f"(status NOT IN ({_TERMINAL_RUNS}) AND terminal_at IS NULL "
            "AND stop_reason IS NULL)",
            name="terminal_state_consistent",
        ),
        CheckConstraint("deadline > created_at", name="deadline_after_creation"),
        Index(None, "workspace_id", "conversation_id", "created_at", "id"),
        Index(None, "workspace_id", "status", "updated_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    conversation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    turn_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    job_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False
    )
    event_stream_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    run_type: Mapped[AgentRunType] = mapped_column(
        SqlEnum(
            AgentRunType,
            name="agent_run_type",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=24,
        ),
        nullable=False,
    )
    status: Mapped[AgentRunStatus] = mapped_column(
        SqlEnum(
            AgentRunStatus,
            name="agent_run_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
        default=AgentRunStatus.QUEUED,
        server_default=AgentRunStatus.QUEUED.value,
    )
    stop_reason: Mapped[RunStopReason | None] = mapped_column(
        SqlEnum(
            RunStopReason,
            name="run_stop_reason",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=40,
        ),
        nullable=True,
    )
    runtime_version: Mapped[str] = mapped_column(String(128), nullable=False)
    harness_version: Mapped[str] = mapped_column(String(128), nullable=False)
    state_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    max_steps: Mapped[int] = mapped_column(Integer, nullable=False)
    max_total_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    max_cost_micro_usd: Mapped[int] = mapped_column(BigInteger, nullable=False)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    step_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cached_input_tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_micro_usd: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AgentStepRecord(UUIDPrimaryKeyMixin, Base):
    """Current projection of one auditable step, derived from committed Events."""

    __tablename__ = "agent_steps"
    __table_args__ = (
        UniqueConstraint("id", "run_id", "workspace_id"),
        UniqueConstraint("run_id", "sequence"),
        ForeignKeyConstraint(
            ["run_id", "workspace_id"],
            ["agent_runs.id", "agent_runs.workspace_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint(
            "kind IN ('model', 'tool', 'approval', 'checkpoint', 'final')", name="kind"
        ),
        CheckConstraint("status IN ('running', 'completed', 'failed', 'cancelled')", name="status"),
        CheckConstraint("last_event_sequence >= 1", name="last_event_sequence_positive"),
        CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND cost_micro_usd >= 0",
            name="usage_nonnegative",
        ),
        CheckConstraint(
            "(status = 'running' AND completed_at IS NULL) OR "
            "(status <> 'running' AND completed_at IS NOT NULL)",
            name="completion_state_consistent",
        ),
        Index(None, "workspace_id", "run_id", "sequence"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[AgentStepKind] = mapped_column(
        SqlEnum(
            AgentStepKind,
            name="agent_step_kind",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    status: Mapped[AgentStepStatus] = mapped_column(
        SqlEnum(
            AgentStepStatus,
            name="agent_step_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    last_event_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_micro_usd: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)


class AgentEventRecord(UUIDPrimaryKeyMixin, Base):
    """Append-only committed Agent Event used for replay and recovery audit."""

    __tablename__ = "agent_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "workspace_id"],
            ["agent_runs.id", "agent_runs.workspace_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("stream_id", "sequence"),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        Index(
            "uq_agent_events_one_terminal_per_run",
            "run_id",
            unique=True,
            postgresql_where=text(f"event_type IN ({_TERMINAL_EVENTS})"),
        ),
        Index(None, "workspace_id", "run_id", "sequence"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    stream_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    event_type: Mapped[AgentEventType] = mapped_column(
        SqlEnum(
            AgentEventType,
            name="agent_event_type",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=40,
        ),
        nullable=False,
    )
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)


class ContextManifestRecord(UUIDPrimaryKeyMixin, Base):
    """Per-step explanation of selected Context without the original text."""

    __tablename__ = "context_manifests"
    __table_args__ = (
        UniqueConstraint("step_id"),
        ForeignKeyConstraint(
            ["step_id", "run_id", "workspace_id"],
            ["agent_steps.id", "agent_steps.run_id", "agent_steps.workspace_id"],
            ondelete="CASCADE",
        ),
        Index(None, "workspace_id", "run_id", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    step_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    compiler_version: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(128), nullable=False)
    runtime_projection_version: Mapped[str] = mapped_column(String(128), nullable=False)
    token_counter_version: Mapped[str] = mapped_column(String(128), nullable=False)
    budget: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    sources: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RunArtifactRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Versioned resource produced by a Run; normal final chat text is not one."""

    __tablename__ = "run_artifacts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "workspace_id"],
            ["agent_runs.id", "agent_runs.workspace_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["originating_step_id", "run_id", "workspace_id"],
            ["agent_steps.id", "agent_steps.run_id", "agent_steps.workspace_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("length(content_sha256) = 64", name="content_hash_length"),
        CheckConstraint("length(btrim(resource_ref)) > 0", name="resource_ref_not_blank"),
        Index(None, "workspace_id", "run_id", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    originating_step_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    kind: Mapped[RunArtifactKind] = mapped_column(
        SqlEnum(
            RunArtifactKind,
            name="run_artifact_kind",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=20,
        ),
        nullable=False,
    )
    resource_ref: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)


class AgentCheckpointRecord(UUIDPrimaryKeyMixin, Base):
    """Versioned optimistic snapshot for one Run; graph resume remains a later step."""

    __tablename__ = "agent_checkpoints"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "workspace_id"],
            ["agent_runs.id", "agent_runs.workspace_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("run_id", "revision"),
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
        CheckConstraint(
            "envelope_schema_version >= 1 AND state_schema_version >= 1",
            name="schema_versions_positive",
        ),
        Index(None, "workspace_id", "run_id", "revision"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    envelope_schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    state_schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    state: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
