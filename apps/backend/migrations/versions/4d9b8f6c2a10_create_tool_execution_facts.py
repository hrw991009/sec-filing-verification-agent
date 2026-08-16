"""create tool execution facts

Revision ID: 4d9b8f6c2a10
Revises: 0ed29898ae52
Create Date: 2026-08-16 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# Revision identifiers, used by Alembic.
revision: str = "4d9b8f6c2a10"
down_revision: str | Sequence[str] | None = "0ed29898ae52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""

    op.create_unique_constraint(
        op.f("uq_agent_runs_id_workspace_id_user_id"),
        "agent_runs",
        ["id", "workspace_id", "user_id"],
    )
    op.create_table(
        "tool_calls",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_step_id", sa.Uuid(), nullable=False),
        sa.Column("execution_step_id", sa.Uuid(), nullable=True),
        sa.Column("schema_version", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("requested_tool_name", sa.String(length=128), nullable=False),
        sa.Column("requested_tool_version", sa.String(length=128), nullable=False),
        sa.Column("resolved_tool_name", sa.String(length=128), nullable=True),
        sa.Column("tool_version", sa.String(length=128), nullable=True),
        sa.Column("toolset_version", sa.String(length=128), nullable=False),
        sa.Column("input_schema_version", sa.String(length=128), nullable=True),
        sa.Column("output_schema_version", sa.String(length=128), nullable=True),
        sa.Column("required_capability", sa.String(length=100), nullable=True),
        sa.Column("cost_class", sa.String(length=32), nullable=True),
        sa.Column("side_effect_class", sa.String(length=32), nullable=True),
        sa.Column("approval_policy", sa.String(length=32), nullable=True),
        sa.Column("retry_classification", sa.String(length=32), nullable=True),
        sa.Column("policy_version", sa.String(length=128), nullable=False),
        sa.Column("policy_decision", sa.String(length=32), nullable=True),
        sa.Column("policy_reason_code", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="requested", nullable=False),
        sa.Column("timeout_ms", sa.Integer(), nullable=True),
        sa.Column("max_result_bytes", sa.Integer(), nullable=True),
        sa.Column("max_cost_micro_usd", sa.BigInteger(), nullable=True),
        sa.Column("cost_micro_usd", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("sanitized_arguments_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("idempotency_key_hash", sa.LargeBinary(length=32), nullable=True),
        sa.Column("observation_schema_version", sa.SmallInteger(), nullable=True),
        sa.Column(
            "observation",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("observation_content_sha256", sa.String(length=64), nullable=True),
        sa.Column("observation_envelope_sha256", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
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
            "status IN ('requested', 'approval_required', 'denied', 'running', "
            "'completed', 'failed', 'cancelled')",
            name=op.f("ck_tool_calls_status"),
        ),
        sa.CheckConstraint(
            "policy_decision IS NULL OR policy_decision IN ('allow', 'deny', 'approval_required')",
            name=op.f("ck_tool_calls_policy_decision"),
        ),
        sa.CheckConstraint(
            "schema_version >= 1",
            name=op.f("ck_tool_calls_schema_version_positive"),
        ),
        sa.CheckConstraint(
            "length(btrim(requested_tool_name)) > 0 AND length(btrim(requested_tool_version)) > 0",
            name=op.f("ck_tool_calls_requested_tool_not_blank"),
        ),
        sa.CheckConstraint(
            "length(btrim(toolset_version)) > 0 AND length(btrim(policy_version)) > 0",
            name=op.f("ck_tool_calls_required_versions_not_blank"),
        ),
        sa.CheckConstraint(
            "(resolved_tool_name IS NULL AND tool_version IS NULL "
            "AND input_schema_version IS NULL AND output_schema_version IS NULL "
            "AND required_capability IS NULL AND cost_class IS NULL "
            "AND side_effect_class IS NULL AND approval_policy IS NULL "
            "AND retry_classification IS NULL "
            "AND timeout_ms IS NULL AND max_result_bytes IS NULL "
            "AND max_cost_micro_usd IS NULL) OR "
            "(resolved_tool_name IS NOT NULL AND length(btrim(resolved_tool_name)) > 0 "
            "AND tool_version IS NOT NULL AND length(btrim(tool_version)) > 0 "
            "AND input_schema_version IS NOT NULL "
            "AND length(btrim(input_schema_version)) > 0 "
            "AND output_schema_version IS NOT NULL "
            "AND length(btrim(output_schema_version)) > 0 "
            "AND required_capability IS NOT NULL "
            "AND length(btrim(required_capability)) > 0 "
            "AND cost_class IS NOT NULL AND length(btrim(cost_class)) > 0 "
            "AND side_effect_class IS NOT NULL "
            "AND length(btrim(side_effect_class)) > 0 "
            "AND approval_policy IS NOT NULL AND length(btrim(approval_policy)) > 0 "
            "AND retry_classification IS NOT NULL "
            "AND length(btrim(retry_classification)) > 0 "
            "AND timeout_ms IS NOT NULL AND max_result_bytes IS NOT NULL "
            "AND max_cost_micro_usd IS NOT NULL)",
            name=op.f("ck_tool_calls_registry_metadata_paired"),
        ),
        sa.CheckConstraint(
            "cost_class IS NULL OR cost_class IN ('low', 'medium', 'high')",
            name=op.f("ck_tool_calls_cost_class"),
        ),
        sa.CheckConstraint(
            "side_effect_class IS NULL OR side_effect_class IN "
            "('read_only', 'idempotent_write', 'non_idempotent_write')",
            name=op.f("ck_tool_calls_side_effect_class"),
        ),
        sa.CheckConstraint(
            "approval_policy IS NULL OR approval_policy IN "
            "('auto_allow', 'auto_deny', 'require_approval')",
            name=op.f("ck_tool_calls_approval_policy"),
        ),
        sa.CheckConstraint(
            "retry_classification IS NULL OR retry_classification IN "
            "('never', 'safe_read_only', 'idempotent_write')",
            name=op.f("ck_tool_calls_retry_classification"),
        ),
        sa.CheckConstraint(
            "(policy_decision IS NULL AND policy_reason_code IS NULL) OR "
            "(policy_decision IS NOT NULL AND policy_reason_code IS NOT NULL "
            "AND length(btrim(policy_reason_code)) > 0)",
            name=op.f("ck_tool_calls_policy_reason_paired"),
        ),
        sa.CheckConstraint(
            "octet_length(sanitized_arguments_hash) = 32",
            name=op.f("ck_tool_calls_sanitized_arguments_hash_length"),
        ),
        sa.CheckConstraint(
            "idempotency_key_hash IS NULL OR octet_length(idempotency_key_hash) = 32",
            name=op.f("ck_tool_calls_idempotency_key_hash_length"),
        ),
        sa.CheckConstraint(
            "idempotency_key_hash IS NULL OR "
            "(side_effect_class IS NOT NULL AND side_effect_class <> 'read_only')",
            name=op.f("ck_tool_calls_idempotency_requires_side_effect"),
        ),
        sa.CheckConstraint(
            "policy_decision IS NULL OR policy_decision <> 'allow' "
            "OR (side_effect_class IS NOT NULL AND "
            "(side_effect_class = 'read_only' OR idempotency_key_hash IS NOT NULL))",
            name=op.f("ck_tool_calls_allowed_write_requires_idempotency"),
        ),
        sa.CheckConstraint(
            "timeout_ms IS NULL OR timeout_ms BETWEEN 1 AND 300000",
            name=op.f("ck_tool_calls_timeout_bounds"),
        ),
        sa.CheckConstraint(
            "max_result_bytes IS NULL OR max_result_bytes BETWEEN 1 AND 10000000",
            name=op.f("ck_tool_calls_result_size_bounds"),
        ),
        sa.CheckConstraint(
            "(max_cost_micro_usd IS NULL OR "
            "max_cost_micro_usd BETWEEN 1 AND 1000000000) AND cost_micro_usd >= 0",
            name=op.f("ck_tool_calls_cost_bounds"),
        ),
        sa.CheckConstraint(
            "(observation_schema_version IS NULL AND observation IS NULL "
            "AND observation_content_sha256 IS NULL AND observation_envelope_sha256 IS NULL) OR "
            "(observation_schema_version IS NOT NULL AND observation_schema_version >= 1 "
            "AND observation IS NOT NULL "
            "AND jsonb_typeof(observation) = 'object' "
            "AND octet_length(observation::text) <= 524288 "
            "AND observation_content_sha256 IS NOT NULL "
            "AND observation_content_sha256 ~ '^[0-9a-f]{64}$' "
            "AND observation_envelope_sha256 IS NOT NULL "
            "AND observation_envelope_sha256 ~ '^[0-9a-f]{64}$')",
            name=op.f("ck_tool_calls_observation_fields_paired_and_bounded"),
        ),
        sa.CheckConstraint(
            "started_at IS NULL OR started_at >= created_at",
            name=op.f("ck_tool_calls_start_after_request"),
        ),
        sa.CheckConstraint(
            "terminal_at IS NULL OR terminal_at >= COALESCE(started_at, created_at)",
            name=op.f("ck_tool_calls_terminal_after_start"),
        ),
        sa.CheckConstraint(
            "(status = 'requested' AND policy_decision IS NULL "
            "AND execution_step_id IS NULL AND started_at IS NULL "
            "AND terminal_at IS NULL AND error_code IS NULL AND observation IS NULL) OR "
            "(status = 'approval_required' AND policy_decision IS NOT NULL "
            "AND policy_decision = 'approval_required' "
            "AND execution_step_id IS NULL AND started_at IS NULL "
            "AND terminal_at IS NOT NULL AND error_code IS NULL AND observation IS NULL) OR "
            "(status = 'denied' AND policy_decision IS NOT NULL "
            "AND policy_decision = 'deny' "
            "AND execution_step_id IS NULL AND started_at IS NULL "
            "AND terminal_at IS NOT NULL AND error_code IS NOT NULL AND observation IS NULL) OR "
            "(status = 'running' AND policy_decision IS NOT NULL "
            "AND policy_decision = 'allow' "
            "AND execution_step_id IS NOT NULL AND started_at IS NOT NULL "
            "AND terminal_at IS NULL AND error_code IS NULL AND observation IS NULL) OR "
            "(status = 'completed' AND policy_decision IS NOT NULL "
            "AND policy_decision = 'allow' "
            "AND execution_step_id IS NOT NULL AND started_at IS NOT NULL "
            "AND terminal_at IS NOT NULL AND error_code IS NULL AND observation IS NOT NULL) OR "
            "(status = 'failed' AND terminal_at IS NOT NULL "
            "AND error_code IS NOT NULL AND observation IS NULL AND "
            "((policy_decision IS NULL AND execution_step_id IS NULL AND started_at IS NULL) OR "
            "(policy_decision IS NOT NULL AND policy_decision = 'allow' "
            "AND execution_step_id IS NOT NULL "
            "AND started_at IS NOT NULL))) OR "
            "(status = 'cancelled' AND terminal_at IS NOT NULL "
            "AND error_code IS NULL AND observation IS NULL AND "
            "((policy_decision IS NULL AND execution_step_id IS NULL AND started_at IS NULL) OR "
            "(policy_decision IS NOT NULL AND policy_decision = 'allow' "
            "AND execution_step_id IS NOT NULL "
            "AND started_at IS NOT NULL)))",
            name=op.f("ck_tool_calls_lifecycle_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["execution_step_id", "run_id", "workspace_id"],
            ["agent_steps.id", "agent_steps.run_id", "agent_steps.workspace_id"],
            name=op.f("fk_tool_calls_execution_step_run_workspace"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_step_id", "run_id", "workspace_id"],
            ["agent_steps.id", "agent_steps.run_id", "agent_steps.workspace_id"],
            name=op.f("fk_tool_calls_request_step_run_workspace"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tool_calls")),
        sa.UniqueConstraint(
            "execution_step_id",
            name=op.f("uq_tool_calls_execution_step_id"),
        ),
        sa.UniqueConstraint(
            "id", "run_id", "workspace_id", name=op.f("uq_tool_calls_id_run_id_workspace_id")
        ),
        sa.UniqueConstraint("id", "workspace_id", name=op.f("uq_tool_calls_id_workspace_id")),
        sa.UniqueConstraint(
            "requested_by_step_id",
            name=op.f("uq_tool_calls_requested_by_step_id"),
        ),
    )
    op.create_index(
        op.f("ix_tool_calls_workspace_id_run_id_created_at_id"),
        "tool_calls",
        ["workspace_id", "run_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_tool_calls_workspace_id_status_updated_at"),
        "tool_calls",
        ["workspace_id", "status", "updated_at"],
        unique=False,
    )
    op.create_index(
        "uq_tool_calls_workspace_idempotency",
        "tool_calls",
        ["workspace_id", "requested_tool_name", "idempotency_key_hash"],
        unique=True,
        postgresql_where=sa.text("idempotency_key_hash IS NOT NULL"),
    )

    op.create_table(
        "tool_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("actor_role", sa.String(length=16), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("requested_tool_name", sa.String(length=128), nullable=False),
        sa.Column("requested_tool_version", sa.String(length=128), nullable=False),
        sa.Column("resolved_tool_name", sa.String(length=128), nullable=True),
        sa.Column("tool_version", sa.String(length=128), nullable=True),
        sa.Column("toolset_version", sa.String(length=128), nullable=False),
        sa.Column("input_schema_version", sa.String(length=128), nullable=True),
        sa.Column("output_schema_version", sa.String(length=128), nullable=True),
        sa.Column("required_capability", sa.String(length=100), nullable=True),
        sa.Column("cost_class", sa.String(length=32), nullable=True),
        sa.Column("side_effect_class", sa.String(length=32), nullable=True),
        sa.Column("approval_policy", sa.String(length=32), nullable=True),
        sa.Column("retry_classification", sa.String(length=32), nullable=True),
        sa.Column("policy_version", sa.String(length=128), nullable=False),
        sa.Column("policy_decision", sa.String(length=32), nullable=True),
        sa.Column("policy_reason_code", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="requested", nullable=False),
        sa.Column("sanitizer_version", sa.String(length=128), nullable=False),
        sa.Column(
            "sanitized_input_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "sanitized_output_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "source_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("timeout_ms", sa.Integer(), nullable=True),
        sa.Column("max_result_bytes", sa.Integer(), nullable=True),
        sa.Column("max_cost_micro_usd", sa.BigInteger(), nullable=True),
        sa.Column("cost_micro_usd", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
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
            "status IN ('requested', 'approval_required', 'denied', 'running', "
            "'completed', 'failed', 'cancelled')",
            name=op.f("ck_tool_runs_status"),
        ),
        sa.CheckConstraint(
            "policy_decision IS NULL OR policy_decision IN ('allow', 'deny', 'approval_required')",
            name=op.f("ck_tool_runs_policy_decision"),
        ),
        sa.CheckConstraint(
            "schema_version >= 1",
            name=op.f("ck_tool_runs_schema_version_positive"),
        ),
        sa.CheckConstraint(
            "actor_role IN ('owner', 'admin', 'member', 'viewer')",
            name=op.f("ck_tool_runs_actor_role"),
        ),
        sa.CheckConstraint(
            "length(btrim(trace_id)) > 0 "
            "AND length(btrim(requested_tool_name)) > 0 "
            "AND length(btrim(requested_tool_version)) > 0",
            name=op.f("ck_tool_runs_required_names_not_blank"),
        ),
        sa.CheckConstraint(
            "length(btrim(toolset_version)) > 0 "
            "AND length(btrim(policy_version)) > 0 "
            "AND length(btrim(sanitizer_version)) > 0",
            name=op.f("ck_tool_runs_required_versions_not_blank"),
        ),
        sa.CheckConstraint(
            "(resolved_tool_name IS NULL AND tool_version IS NULL "
            "AND input_schema_version IS NULL AND output_schema_version IS NULL "
            "AND required_capability IS NULL AND cost_class IS NULL "
            "AND side_effect_class IS NULL AND approval_policy IS NULL "
            "AND retry_classification IS NULL "
            "AND timeout_ms IS NULL AND max_result_bytes IS NULL "
            "AND max_cost_micro_usd IS NULL) OR "
            "(resolved_tool_name IS NOT NULL AND length(btrim(resolved_tool_name)) > 0 "
            "AND tool_version IS NOT NULL AND length(btrim(tool_version)) > 0 "
            "AND input_schema_version IS NOT NULL "
            "AND length(btrim(input_schema_version)) > 0 "
            "AND output_schema_version IS NOT NULL "
            "AND length(btrim(output_schema_version)) > 0 "
            "AND required_capability IS NOT NULL "
            "AND length(btrim(required_capability)) > 0 "
            "AND cost_class IS NOT NULL AND length(btrim(cost_class)) > 0 "
            "AND side_effect_class IS NOT NULL "
            "AND length(btrim(side_effect_class)) > 0 "
            "AND approval_policy IS NOT NULL AND length(btrim(approval_policy)) > 0 "
            "AND retry_classification IS NOT NULL "
            "AND length(btrim(retry_classification)) > 0 "
            "AND timeout_ms IS NOT NULL AND max_result_bytes IS NOT NULL "
            "AND max_cost_micro_usd IS NOT NULL)",
            name=op.f("ck_tool_runs_registry_metadata_paired"),
        ),
        sa.CheckConstraint(
            "cost_class IS NULL OR cost_class IN ('low', 'medium', 'high')",
            name=op.f("ck_tool_runs_cost_class"),
        ),
        sa.CheckConstraint(
            "side_effect_class IS NULL OR side_effect_class IN "
            "('read_only', 'idempotent_write', 'non_idempotent_write')",
            name=op.f("ck_tool_runs_side_effect_class"),
        ),
        sa.CheckConstraint(
            "approval_policy IS NULL OR approval_policy IN "
            "('auto_allow', 'auto_deny', 'require_approval')",
            name=op.f("ck_tool_runs_approval_policy"),
        ),
        sa.CheckConstraint(
            "retry_classification IS NULL OR retry_classification IN "
            "('never', 'safe_read_only', 'idempotent_write')",
            name=op.f("ck_tool_runs_retry_classification"),
        ),
        sa.CheckConstraint(
            "(policy_decision IS NULL AND policy_reason_code IS NULL) OR "
            "(policy_decision IS NOT NULL AND policy_reason_code IS NOT NULL "
            "AND length(btrim(policy_reason_code)) > 0)",
            name=op.f("ck_tool_runs_policy_reason_paired"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(sanitized_input_summary) = 'object' "
            "AND octet_length(sanitized_input_summary::text) <= 16384",
            name=op.f("ck_tool_runs_input_summary_bounded"),
        ),
        sa.CheckConstraint(
            "sanitized_output_summary IS NULL OR "
            "(jsonb_typeof(sanitized_output_summary) = 'object' "
            "AND octet_length(sanitized_output_summary::text) <= 32768)",
            name=op.f("ck_tool_runs_output_summary_bounded"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(source_summary) = 'array' "
            "AND octet_length(source_summary::text) <= 262144",
            name=op.f("ck_tool_runs_source_summary_bounded"),
        ),
        sa.CheckConstraint(
            "timeout_ms IS NULL OR timeout_ms BETWEEN 1 AND 300000",
            name=op.f("ck_tool_runs_timeout_bounds"),
        ),
        sa.CheckConstraint(
            "max_result_bytes IS NULL OR max_result_bytes BETWEEN 1 AND 10000000",
            name=op.f("ck_tool_runs_result_size_bounds"),
        ),
        sa.CheckConstraint(
            "(max_cost_micro_usd IS NULL OR "
            "max_cost_micro_usd BETWEEN 1 AND 1000000000) "
            "AND cost_micro_usd >= 0 "
            "AND (duration_ms IS NULL OR duration_ms >= 0)",
            name=op.f("ck_tool_runs_usage_nonnegative"),
        ),
        sa.CheckConstraint(
            "terminal_at IS NULL OR terminal_at >= created_at",
            name=op.f("ck_tool_runs_terminal_after_request"),
        ),
        sa.CheckConstraint(
            "(status = 'requested' AND policy_decision IS NULL "
            "AND terminal_at IS NULL AND duration_ms IS NULL "
            "AND error_code IS NULL AND sanitized_output_summary IS NULL) OR "
            "(status = 'approval_required' AND policy_decision IS NOT NULL "
            "AND policy_decision = 'approval_required' "
            "AND terminal_at IS NOT NULL AND duration_ms IS NULL "
            "AND error_code IS NULL AND sanitized_output_summary IS NULL) OR "
            "(status = 'denied' AND policy_decision IS NOT NULL "
            "AND policy_decision = 'deny' "
            "AND terminal_at IS NOT NULL AND duration_ms IS NULL "
            "AND error_code IS NOT NULL AND sanitized_output_summary IS NULL) OR "
            "(status = 'running' AND policy_decision IS NOT NULL "
            "AND policy_decision = 'allow' "
            "AND terminal_at IS NULL AND duration_ms IS NULL "
            "AND error_code IS NULL AND sanitized_output_summary IS NULL) OR "
            "(status = 'completed' AND policy_decision IS NOT NULL "
            "AND policy_decision = 'allow' "
            "AND terminal_at IS NOT NULL AND duration_ms IS NOT NULL "
            "AND error_code IS NULL AND sanitized_output_summary IS NOT NULL) OR "
            "(status = 'failed' AND terminal_at IS NOT NULL "
            "AND error_code IS NOT NULL AND sanitized_output_summary IS NULL AND "
            "((policy_decision IS NULL AND duration_ms IS NULL) OR "
            "(policy_decision IS NOT NULL AND policy_decision = 'allow' "
            "AND duration_ms IS NOT NULL))) OR "
            "(status = 'cancelled' AND terminal_at IS NOT NULL "
            "AND error_code IS NULL AND sanitized_output_summary IS NULL AND "
            "((policy_decision IS NULL AND duration_ms IS NULL) OR "
            "(policy_decision IS NOT NULL AND policy_decision = 'allow' "
            "AND duration_ms IS NOT NULL)))",
            name=op.f("ck_tool_runs_lifecycle_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_tool_runs_actor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["id", "run_id", "workspace_id"],
            ["tool_calls.id", "tool_calls.run_id", "tool_calls.workspace_id"],
            name=op.f("fk_tool_runs_call_run_workspace"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "workspace_id", "actor_user_id"],
            ["agent_runs.id", "agent_runs.workspace_id", "agent_runs.user_id"],
            name="fk_tool_runs_actor_run_workspace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tool_runs")),
        sa.UniqueConstraint("id", "workspace_id", name=op.f("uq_tool_runs_id_workspace_id")),
    )
    op.create_index(
        op.f("ix_tool_runs_workspace_id_run_id_created_at_id"),
        "tool_runs",
        ["workspace_id", "run_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_tool_runs_workspace_id_status_created_at_id"),
        "tool_runs",
        ["workspace_id", "status", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_tool_runs_workspace_id_trace_id"),
        "tool_runs",
        ["workspace_id", "trace_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_index(op.f("ix_tool_runs_workspace_id_trace_id"), table_name="tool_runs")
    op.drop_index(op.f("ix_tool_runs_workspace_id_status_created_at_id"), table_name="tool_runs")
    op.drop_index(op.f("ix_tool_runs_workspace_id_run_id_created_at_id"), table_name="tool_runs")
    op.drop_table("tool_runs")
    op.drop_index(
        "uq_tool_calls_workspace_idempotency",
        table_name="tool_calls",
        postgresql_where=sa.text("idempotency_key_hash IS NOT NULL"),
    )
    op.drop_index(op.f("ix_tool_calls_workspace_id_status_updated_at"), table_name="tool_calls")
    op.drop_index(op.f("ix_tool_calls_workspace_id_run_id_created_at_id"), table_name="tool_calls")
    op.drop_table("tool_calls")
    op.drop_constraint(
        op.f("uq_agent_runs_id_workspace_id_user_id"),
        "agent_runs",
        type_="unique",
    )
