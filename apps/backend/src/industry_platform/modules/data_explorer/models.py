"""PostgreSQL records for data connections, query audit, and artifacts."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
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
from industry_platform.modules.data_explorer.domain import (
    ChartType,
    DataConnectionStatus,
    QueryRunStatus,
)
from industry_platform.modules.identity.models import enum_values


class SampleCompanyMetricRecord(Base):
    """Small explicit sample dataset queried through a separate read-only account."""

    __tablename__ = "sample_company_metrics"
    __table_args__ = (
        CheckConstraint("length(btrim(company_name)) > 0", name="company_name_not_blank"),
        CheckConstraint("length(btrim(industry)) > 0", name="industry_not_blank"),
        CheckConstraint("revenue >= 0 AND employees >= 0", name="metrics_nonnegative"),
        Index(None, "industry", "metric_date"),
    )

    company_name: Mapped[str] = mapped_column(String(120), primary_key=True)
    metric_date: Mapped[date] = mapped_column(Date, primary_key=True)
    industry: Mapped[str] = mapped_column(String(64), nullable=False)
    revenue: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    employees: Mapped[int] = mapped_column(Integer, nullable=False)


class DataConnectionRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Workspace-owned metadata; the credential remains an external secret reference."""

    __tablename__ = "data_connections"
    __table_args__ = (
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        CheckConstraint("dialect = 'postgres'", name="dialect_supported"),
        CheckConstraint(
            "secret_reference = 'settings:text2sql_database_url'",
            name="secret_reference_supported",
        ),
        CheckConstraint(
            "status IN ('configuration_required', 'ready', 'error')",
            name="status_supported",
        ),
        CheckConstraint(
            "(status = 'error' AND last_error_code IS NOT NULL) OR "
            "(status <> 'error' AND last_error_code IS NULL)",
            name="status_error_consistent",
        ),
        CheckConstraint("jsonb_typeof(allowed_tables) = 'array'", name="allowed_tables_array"),
        UniqueConstraint("workspace_id", "name"),
        UniqueConstraint("id", "workspace_id"),
        Index(None, "workspace_id", "status", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    dialect: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'postgres'")
    )
    secret_reference: Mapped[str] = mapped_column(String(200), nullable=False)
    allowed_tables: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    status: Mapped[DataConnectionStatus] = mapped_column(
        SqlEnum(
            DataConnectionStatus,
            name="data_connection_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=32,
        ),
        nullable=False,
    )
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)


class SchemaSnapshotRecord(UUIDPrimaryKeyMixin, Base):
    """Immutable allowlisted schema facts used for one validation decision."""

    __tablename__ = "schema_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ("connection_id", "workspace_id"),
            ("data_connections.id", "data_connections.workspace_id"),
            ondelete="RESTRICT",
        ),
        CheckConstraint("schema_version = 1", name="schema_version_supported"),
        CheckConstraint("length(btrim(version)) > 0", name="version_not_blank"),
        CheckConstraint("jsonb_typeof(tables) = 'array'", name="tables_array"),
        CheckConstraint("content_sha256 ~ '^[a-f0-9]{64}$'", name="content_hash_lowercase_hex"),
        UniqueConstraint("id", "workspace_id"),
        Index(None, "workspace_id", "connection_id", "captured_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    connection_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    schema_version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("1")
    )
    version: Mapped[str] = mapped_column(String(128), nullable=False)
    tables: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class QueryRunRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Authoritative question, SQL validation, budget, and terminal audit record."""

    __tablename__ = "query_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ("connection_id", "workspace_id"),
            ("data_connections.id", "data_connections.workspace_id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("schema_snapshot_id", "workspace_id"),
            ("schema_snapshots.id", "schema_snapshots.workspace_id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("agent_run_id", "workspace_id", "actor_user_id"),
            ("agent_runs.id", "agent_runs.workspace_id", "agent_runs.user_id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("tool_call_id", "agent_run_id", "workspace_id"),
            ("tool_calls.id", "tool_calls.run_id", "tool_calls.workspace_id"),
            ondelete="RESTRICT",
        ),
        CheckConstraint("status IN ('running', 'completed', 'failed')", name="status_supported"),
        CheckConstraint("length(btrim(question)) > 0", name="question_not_blank"),
        CheckConstraint("length(btrim(generated_sql)) > 0", name="generated_sql_not_blank"),
        CheckConstraint("length(btrim(trace_id)) > 0", name="trace_id_not_blank"),
        CheckConstraint(
            "statement_timeout_ms BETWEEN 100 AND 30000 AND max_rows BETWEEN 1 AND 200 "
            "AND max_plan_cost >= 1 AND max_plan_rows >= 1",
            name="budgets_bounded",
        ),
        CheckConstraint(
            "(agent_run_id IS NULL AND tool_call_id IS NULL) OR "
            "(agent_run_id IS NOT NULL AND tool_call_id IS NOT NULL)",
            name="agent_tool_refs_paired",
        ),
        CheckConstraint(
            "(status = 'running' AND validated_sql IS NULL AND terminal_at IS NULL "
            "AND error_code IS NULL AND row_count = 0 AND result_content_sha256 IS NULL) OR "
            "(status = 'completed' AND schema_snapshot_id IS NOT NULL "
            "AND validated_sql IS NOT NULL AND terminal_at IS NOT NULL "
            "AND plan_cost IS NOT NULL AND plan_rows IS NOT NULL "
            "AND error_code IS NULL AND result_content_sha256 IS NOT NULL) OR "
            "(status = 'failed' AND terminal_at IS NOT NULL AND error_code IS NOT NULL "
            "AND result_content_sha256 IS NULL)",
            name="lifecycle_consistent",
        ),
        CheckConstraint("row_count BETWEEN 0 AND 200", name="row_count_bounded"),
        CheckConstraint(
            "result_content_sha256 IS NULL OR result_content_sha256 ~ '^[a-f0-9]{64}$'",
            name="result_hash_lowercase_hex",
        ),
        UniqueConstraint("id", "workspace_id"),
        UniqueConstraint("tool_call_id"),
        Index(None, "workspace_id", "created_at", "id"),
        Index(None, "workspace_id", "status", "created_at"),
        Index(None, "trace_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    connection_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    schema_snapshot_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    actor_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    agent_run_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    tool_call_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    generated_sql: Mapped[str] = mapped_column(Text, nullable=False)
    validated_sql: Mapped[str | None] = mapped_column(Text, nullable=True)
    validator_version: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[QueryRunStatus] = mapped_column(
        SqlEnum(
            QueryRunStatus,
            name="query_run_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    statement_timeout_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    max_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    max_plan_cost: Mapped[int] = mapped_column(BigInteger, nullable=False)
    max_plan_rows: Mapped[int] = mapped_column(BigInteger, nullable=False)
    plan_cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    plan_rows: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    result_content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class QueryResultRecord(Base):
    """Bounded PostgreSQL JSON table Artifact; raw unbounded results never reach this table."""

    __tablename__ = "query_results"
    __table_args__ = (
        ForeignKeyConstraint(
            ("query_run_id", "workspace_id"),
            ("query_runs.id", "query_runs.workspace_id"),
            ondelete="RESTRICT",
        ),
        CheckConstraint("schema_version = 1", name="schema_version_supported"),
        CheckConstraint("jsonb_typeof(columns) = 'array'", name="columns_array"),
        CheckConstraint("jsonb_typeof(rows) = 'array'", name="rows_array"),
        CheckConstraint("jsonb_array_length(rows) <= 200", name="rows_bounded"),
        CheckConstraint("octet_length(rows::text) <= 524288", name="rows_bytes_bounded"),
        CheckConstraint("content_sha256 ~ '^[a-f0-9]{64}$'", name="content_hash_lowercase_hex"),
        UniqueConstraint("id", "workspace_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    query_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), unique=True, nullable=False)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    schema_version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("1")
    )
    columns: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    rows: Mapped[list[list[object]]] = mapped_column(JSONB, nullable=False)
    truncated: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ChartSpecRecord(Base):
    """Versioned, allowlisted ECharts option generated from a bounded query result."""

    __tablename__ = "chart_specs"
    __table_args__ = (
        ForeignKeyConstraint(
            ("query_run_id", "workspace_id"),
            ("query_runs.id", "query_runs.workspace_id"),
            ondelete="RESTRICT",
        ),
        CheckConstraint("schema_version = 1", name="schema_version_supported"),
        CheckConstraint(
            "chart_type IN ('line', 'bar', 'pie', 'scatter')", name="chart_type_supported"
        ),
        CheckConstraint("jsonb_typeof(option) = 'object'", name="option_object"),
        CheckConstraint("octet_length(option::text) <= 524288", name="option_bytes_bounded"),
        CheckConstraint("content_sha256 ~ '^[a-f0-9]{64}$'", name="content_hash_lowercase_hex"),
        UniqueConstraint("id", "workspace_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    query_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), unique=True, nullable=False)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    schema_version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("1")
    )
    chart_type: Mapped[ChartType] = mapped_column(
        SqlEnum(
            ChartType,
            name="chart_type",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    option: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
