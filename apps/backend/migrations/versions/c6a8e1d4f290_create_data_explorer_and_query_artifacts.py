"""create data explorer and query artifacts

Revision ID: c6a8e1d4f290
Revises: 77c3f51a9d20
Create Date: 2026-08-17 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c6a8e1d4f290"
down_revision: str | Sequence[str] | None = "77c3f51a9d20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the fixed sample relation and Workspace-scoped Text2SQL facts."""

    op.create_table(
        "sample_company_metrics",
        sa.Column("company_name", sa.String(length=120), nullable=False),
        sa.Column("metric_date", sa.Date(), nullable=False),
        sa.Column("industry", sa.String(length=64), nullable=False),
        sa.Column("revenue", sa.Numeric(precision=20, scale=2), nullable=False),
        sa.Column("employees", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "length(btrim(company_name)) > 0",
            name=op.f("ck_sample_company_metrics_company_name_not_blank"),
        ),
        sa.CheckConstraint(
            "length(btrim(industry)) > 0",
            name=op.f("ck_sample_company_metrics_industry_not_blank"),
        ),
        sa.CheckConstraint(
            "revenue >= 0 AND employees >= 0",
            name=op.f("ck_sample_company_metrics_metrics_nonnegative"),
        ),
        sa.PrimaryKeyConstraint(
            "company_name", "metric_date", name=op.f("pk_sample_company_metrics")
        ),
    )
    op.create_index(
        op.f("ix_sample_company_metrics_industry_metric_date"),
        "sample_company_metrics",
        ["industry", "metric_date"],
        unique=False,
    )
    sample_table = sa.table(
        "sample_company_metrics",
        sa.column("company_name", sa.String()),
        sa.column("metric_date", sa.Date()),
        sa.column("industry", sa.String()),
        sa.column("revenue", sa.Numeric()),
        sa.column("employees", sa.Integer()),
    )
    op.bulk_insert(
        sample_table,
        [
            {
                "company_name": "MetroLink Mobility",
                "metric_date": "2026-06-30",
                "industry": "smart_transport",
                "revenue": "125000000.00",
                "employees": 1200,
            },
            {
                "company_name": "Harbor Fintech",
                "metric_date": "2026-06-30",
                "industry": "fintech",
                "revenue": "83000000.00",
                "employees": 640,
            },
            {
                "company_name": "Northstar Health",
                "metric_date": "2026-06-30",
                "industry": "healthcare",
                "revenue": "97000000.00",
                "employees": 880,
            },
            {
                "company_name": "GridWorks Energy",
                "metric_date": "2026-06-30",
                "industry": "energy_power",
                "revenue": "142000000.00",
                "employees": 1500,
            },
        ],
    )

    op.create_table(
        "data_connections",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "dialect", sa.String(length=32), server_default=sa.text("'postgres'"), nullable=False
        ),
        sa.Column("secret_reference", sa.String(length=200), nullable=False),
        sa.Column("allowed_tables", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "configuration_required",
                "ready",
                "error",
                name="data_connection_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "dialect = 'postgres'", name=op.f("ck_data_connections_dialect_supported")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(allowed_tables) = 'array'",
            name=op.f("ck_data_connections_allowed_tables_array"),
        ),
        sa.CheckConstraint(
            "length(btrim(name)) > 0", name=op.f("ck_data_connections_name_not_blank")
        ),
        sa.CheckConstraint(
            "secret_reference = 'settings:text2sql_database_url'",
            name=op.f("ck_data_connections_secret_reference_supported"),
        ),
        sa.CheckConstraint(
            "status IN ('configuration_required', 'ready', 'error')",
            name=op.f("ck_data_connections_status_supported"),
        ),
        sa.CheckConstraint(
            "(status = 'error' AND last_error_code IS NOT NULL) OR "
            "(status <> 'error' AND last_error_code IS NULL)",
            name=op.f("ck_data_connections_status_error_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_data_connections_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_data_connections_workspace_id_workspaces"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_data_connections")),
        sa.UniqueConstraint("id", "workspace_id", name=op.f("uq_data_connections_id_workspace_id")),
        sa.UniqueConstraint(
            "workspace_id", "name", name=op.f("uq_data_connections_workspace_id_name")
        ),
    )
    op.create_index(
        op.f("ix_data_connections_workspace_id_status_created_at"),
        "data_connections",
        ["workspace_id", "status", "created_at"],
        unique=False,
    )

    op.create_table(
        "schema_snapshots",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("version", sa.String(length=128), nullable=False),
        sa.Column("tables", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.CheckConstraint(
            "content_sha256 ~ '^[a-f0-9]{64}$'",
            name=op.f("ck_schema_snapshots_content_hash_lowercase_hex"),
        ),
        sa.CheckConstraint(
            "schema_version = 1", name=op.f("ck_schema_snapshots_schema_version_supported")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(tables) = 'array'", name=op.f("ck_schema_snapshots_tables_array")
        ),
        sa.CheckConstraint(
            "length(btrim(version)) > 0", name=op.f("ck_schema_snapshots_version_not_blank")
        ),
        sa.ForeignKeyConstraint(
            ["connection_id", "workspace_id"],
            ["data_connections.id", "data_connections.workspace_id"],
            name="fk_schema_snapshots_connection_workspace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_schema_snapshots")),
        sa.UniqueConstraint("id", "workspace_id", name=op.f("uq_schema_snapshots_id_workspace_id")),
    )
    op.create_index(
        op.f("ix_schema_snapshots_workspace_id_connection_id_captured_at"),
        "schema_snapshots",
        ["workspace_id", "connection_id", "captured_at"],
        unique=False,
    )

    op.create_table(
        "query_runs",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("schema_snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("agent_run_id", sa.Uuid(), nullable=True),
        sa.Column("tool_call_id", sa.Uuid(), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("generated_sql", sa.Text(), nullable=False),
        sa.Column("validated_sql", sa.Text(), nullable=True),
        sa.Column("validator_version", sa.String(length=128), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "running",
                "completed",
                "failed",
                name="query_run_status",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("statement_timeout_ms", sa.Integer(), nullable=False),
        sa.Column("max_rows", sa.Integer(), nullable=False),
        sa.Column("max_plan_cost", sa.BigInteger(), nullable=False),
        sa.Column("max_plan_rows", sa.BigInteger(), nullable=False),
        sa.Column("plan_cost", sa.Numeric(precision=20, scale=4), nullable=True),
        sa.Column("plan_rows", sa.BigInteger(), nullable=True),
        sa.Column("row_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("result_content_sha256", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(agent_run_id IS NULL AND tool_call_id IS NULL) OR "
            "(agent_run_id IS NOT NULL AND tool_call_id IS NOT NULL)",
            name=op.f("ck_query_runs_agent_tool_refs_paired"),
        ),
        sa.CheckConstraint(
            "statement_timeout_ms BETWEEN 100 AND 30000 AND max_rows BETWEEN 1 AND 200 "
            "AND max_plan_cost >= 1 AND max_plan_rows >= 1",
            name=op.f("ck_query_runs_budgets_bounded"),
        ),
        sa.CheckConstraint(
            "(status = 'running' AND validated_sql IS NULL AND terminal_at IS NULL "
            "AND error_code IS NULL AND row_count = 0 AND result_content_sha256 IS NULL) OR "
            "(status = 'completed' AND schema_snapshot_id IS NOT NULL "
            "AND validated_sql IS NOT NULL AND terminal_at IS NOT NULL "
            "AND plan_cost IS NOT NULL AND plan_rows IS NOT NULL "
            "AND error_code IS NULL AND result_content_sha256 IS NOT NULL) OR "
            "(status = 'failed' AND terminal_at IS NOT NULL AND error_code IS NOT NULL "
            "AND result_content_sha256 IS NULL)",
            name=op.f("ck_query_runs_lifecycle_consistent"),
        ),
        sa.CheckConstraint(
            "length(btrim(generated_sql)) > 0",
            name=op.f("ck_query_runs_generated_sql_not_blank"),
        ),
        sa.CheckConstraint(
            "length(btrim(question)) > 0", name=op.f("ck_query_runs_question_not_blank")
        ),
        sa.CheckConstraint(
            "length(btrim(trace_id)) > 0", name=op.f("ck_query_runs_trace_id_not_blank")
        ),
        sa.CheckConstraint(
            "result_content_sha256 IS NULL OR result_content_sha256 ~ '^[a-f0-9]{64}$'",
            name=op.f("ck_query_runs_result_hash_lowercase_hex"),
        ),
        sa.CheckConstraint(
            "row_count BETWEEN 0 AND 200", name=op.f("ck_query_runs_row_count_bounded")
        ),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name=op.f("ck_query_runs_status_supported"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_query_runs_actor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id", "workspace_id", "actor_user_id"],
            ["agent_runs.id", "agent_runs.workspace_id", "agent_runs.user_id"],
            name="fk_query_runs_agent_run_workspace_actor",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["connection_id", "workspace_id"],
            ["data_connections.id", "data_connections.workspace_id"],
            name="fk_query_runs_connection_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["schema_snapshot_id", "workspace_id"],
            ["schema_snapshots.id", "schema_snapshots.workspace_id"],
            name="fk_query_runs_snapshot_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tool_call_id", "agent_run_id", "workspace_id"],
            ["tool_calls.id", "tool_calls.run_id", "tool_calls.workspace_id"],
            name="fk_query_runs_tool_call_run_workspace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_query_runs")),
        sa.UniqueConstraint("id", "workspace_id", name=op.f("uq_query_runs_id_workspace_id")),
        sa.UniqueConstraint("tool_call_id", name=op.f("uq_query_runs_tool_call_id")),
    )
    op.create_index(op.f("ix_query_runs_trace_id"), "query_runs", ["trace_id"], unique=False)
    op.create_index(
        op.f("ix_query_runs_workspace_id_created_at_id"),
        "query_runs",
        ["workspace_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_query_runs_workspace_id_status_created_at"),
        "query_runs",
        ["workspace_id", "status", "created_at"],
        unique=False,
    )

    op.create_table(
        "query_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("query_run_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("columns", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rows", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("truncated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "jsonb_typeof(columns) = 'array'", name=op.f("ck_query_results_columns_array")
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[a-f0-9]{64}$'",
            name=op.f("ck_query_results_content_hash_lowercase_hex"),
        ),
        sa.CheckConstraint(
            "octet_length(rows::text) <= 524288",
            name=op.f("ck_query_results_rows_bytes_bounded"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(rows) = 'array'", name=op.f("ck_query_results_rows_array")
        ),
        sa.CheckConstraint(
            "jsonb_array_length(rows) <= 200", name=op.f("ck_query_results_rows_bounded")
        ),
        sa.CheckConstraint(
            "schema_version = 1", name=op.f("ck_query_results_schema_version_supported")
        ),
        sa.ForeignKeyConstraint(
            ["query_run_id", "workspace_id"],
            ["query_runs.id", "query_runs.workspace_id"],
            name="fk_query_results_query_run_workspace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_query_results")),
        sa.UniqueConstraint("id", "workspace_id", name=op.f("uq_query_results_id_workspace_id")),
        sa.UniqueConstraint("query_run_id", name=op.f("uq_query_results_query_run_id")),
    )

    op.create_table(
        "chart_specs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("query_run_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "chart_type",
            sa.Enum(
                "line",
                "bar",
                "pie",
                "scatter",
                name="chart_type",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("option", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "chart_type IN ('line', 'bar', 'pie', 'scatter')",
            name=op.f("ck_chart_specs_chart_type_supported"),
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[a-f0-9]{64}$'",
            name=op.f("ck_chart_specs_content_hash_lowercase_hex"),
        ),
        sa.CheckConstraint(
            "octet_length(option::text) <= 524288",
            name=op.f("ck_chart_specs_option_bytes_bounded"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(option) = 'object'", name=op.f("ck_chart_specs_option_object")
        ),
        sa.CheckConstraint(
            "schema_version = 1", name=op.f("ck_chart_specs_schema_version_supported")
        ),
        sa.ForeignKeyConstraint(
            ["query_run_id", "workspace_id"],
            ["query_runs.id", "query_runs.workspace_id"],
            name="fk_chart_specs_query_run_workspace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chart_specs")),
        sa.UniqueConstraint("id", "workspace_id", name=op.f("uq_chart_specs_id_workspace_id")),
        sa.UniqueConstraint("query_run_id", name=op.f("uq_chart_specs_query_run_id")),
    )


def downgrade() -> None:
    """Remove Text2SQL artifacts before their parent query and connection facts."""

    op.drop_table("chart_specs")
    op.drop_table("query_results")
    op.drop_index(op.f("ix_query_runs_workspace_id_status_created_at"), table_name="query_runs")
    op.drop_index(op.f("ix_query_runs_workspace_id_created_at_id"), table_name="query_runs")
    op.drop_index(op.f("ix_query_runs_trace_id"), table_name="query_runs")
    op.drop_table("query_runs")
    op.drop_index(
        op.f("ix_schema_snapshots_workspace_id_connection_id_captured_at"),
        table_name="schema_snapshots",
    )
    op.drop_table("schema_snapshots")
    op.drop_index(
        op.f("ix_data_connections_workspace_id_status_created_at"),
        table_name="data_connections",
    )
    op.drop_table("data_connections")
    op.drop_index(
        op.f("ix_sample_company_metrics_industry_metric_date"),
        table_name="sample_company_metrics",
    )
    op.drop_table("sample_company_metrics")
