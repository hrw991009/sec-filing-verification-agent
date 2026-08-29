"""Create SEC disclosure Monitors, append-only watermarks, and idempotent Cases.

Revision ID: f1c3e5a7b902
Revises: e9b2d4f6a813
Create Date: 2026-08-29 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1c3e5a7b902"
down_revision: str | Sequence[str] | None = "e9b2d4f6a813"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id_column() -> sa.Column[object]:
    return sa.Column(
        "id",
        sa.Uuid(),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def _timestamps() -> tuple[sa.Column[object], sa.Column[object]]:
    return (
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def upgrade() -> None:
    op.create_table(
        "sec_disclosure_monitors",
        _id_column(),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("filer_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("schedule_id", sa.Uuid(), nullable=False),
        sa.Column("allowed_forms", sa.JSON(), nullable=False),
        sa.Column("rule_set_version", sa.String(length=64), nullable=False),
        sa.Column("diff_version", sa.String(length=64), nullable=False),
        sa.Column("timezone_name", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("current_watermark_id", sa.Uuid(), nullable=True),
        sa.Column("created_from_approval_id", sa.Uuid(), nullable=True),
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('active', 'paused')",
            name=op.f("ck_sec_disclosure_monitors_status_supported"),
        ),
        sa.CheckConstraint(
            "rule_set_version = 'sec-monitor-rules-v1'",
            name=op.f("ck_sec_disclosure_monitors_rules_supported"),
        ),
        sa.CheckConstraint(
            "diff_version = 'sec-filing-diff-v1'",
            name=op.f("ck_sec_disclosure_monitors_diff_supported"),
        ),
        sa.CheckConstraint(
            "revision >= 1",
            name=op.f("ck_sec_disclosure_monitors_revision_positive"),
        ),
        sa.CheckConstraint(
            "json_array_length(allowed_forms) BETWEEN 1 AND 4",
            name=op.f("ck_sec_disclosure_monitors_forms_bounded"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_sec_disclosure_monitors_workspace_id_workspaces"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "owner_user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            name="fk_sec_disclosure_monitors_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["filer_id"],
            ["sec_filers.id"],
            name=op.f("fk_sec_disclosure_monitors_filer_id_sec_filers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id", "workspace_id"],
            ["knowledge_bases.id", "knowledge_bases.workspace_id"],
            name="fk_sec_disclosure_monitors_knowledge_base",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["schedule_id"],
            ["schedules.id"],
            name=op.f("fk_sec_disclosure_monitors_schedule_id_schedules"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sec_disclosure_monitors")),
        sa.UniqueConstraint(
            "id", "workspace_id", name=op.f("uq_sec_disclosure_monitors_id_workspace_id")
        ),
    )
    op.create_index(
        op.f("ix_sec_disclosure_monitors_workspace_id_status_updated_at"),
        "sec_disclosure_monitors",
        ["workspace_id", "status", "updated_at"],
        unique=False,
    )

    op.create_table(
        "sec_disclosure_monitor_rules",
        _id_column(),
        sa.Column("monitor_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.SmallInteger(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("rule_version", sa.String(length=64), nullable=False),
        sa.Column("section_query", sa.String(length=500), nullable=False),
        sa.Column("taxonomy", sa.String(length=128), nullable=True),
        sa.Column("concept", sa.String(length=256), nullable=True),
        sa.Column("unit", sa.String(length=255), nullable=True),
        sa.Column("threshold", sa.String(length=200), nullable=True),
        sa.Column("comparator", sa.String(length=64), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "kind IN ('new_filing', 'amendment', 'fact_absolute_change', 'section_change')",
            name=op.f("ck_sec_disclosure_monitor_rules_kind_supported"),
        ),
        sa.CheckConstraint(
            "rule_version = 'sec-monitor-rules-v1'",
            name=op.f("ck_sec_disclosure_monitor_rules_version_supported"),
        ),
        sa.CheckConstraint(
            "ordinal BETWEEN 1 AND 16",
            name=op.f("ck_sec_disclosure_monitor_rules_ordinal_bounded"),
        ),
        sa.CheckConstraint(
            "length(btrim(section_query)) BETWEEN 1 AND 500",
            name=op.f("ck_sec_disclosure_monitor_rules_section_query_valid"),
        ),
        sa.CheckConstraint(
            "(kind = 'fact_absolute_change' AND taxonomy IS NOT NULL AND concept IS NOT NULL "
            "AND unit IS NOT NULL AND threshold IS NOT NULL "
            "AND comparator = 'absolute_delta_gte') OR "
            "(kind <> 'fact_absolute_change' AND taxonomy IS NULL AND concept IS NULL "
            "AND unit IS NULL AND threshold IS NULL AND comparator IS NULL)",
            name=op.f("ck_sec_disclosure_monitor_rules_fact_configuration_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["monitor_id", "workspace_id"],
            ["sec_disclosure_monitors.id", "sec_disclosure_monitors.workspace_id"],
            name="fk_sec_disclosure_monitor_rules_monitor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sec_disclosure_monitor_rules")),
        sa.UniqueConstraint(
            "id",
            "monitor_id",
            "workspace_id",
            name=op.f("uq_sec_disclosure_monitor_rules_id_monitor_id_workspace_id"),
        ),
        sa.UniqueConstraint(
            "monitor_id", "ordinal", name=op.f("uq_sec_disclosure_monitor_rules_monitor_id_ordinal")
        ),
    )
    op.create_index(
        op.f("ix_sec_disclosure_monitor_rules_workspace_id_monitor_id_ordinal"),
        "sec_disclosure_monitor_rules",
        ["workspace_id", "monitor_id", "ordinal"],
        unique=False,
    )

    op.create_table(
        "sec_disclosure_monitor_watermarks",
        _id_column(),
        sa.Column("monitor_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("coverage_version", sa.String(length=128), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accession", sa.String(length=20), nullable=True),
        sa.Column("monitor_run_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "revision >= 1",
            name=op.f("ck_sec_disclosure_monitor_watermarks_revision_positive"),
        ),
        sa.CheckConstraint(
            "length(btrim(coverage_version)) > 0",
            name=op.f("ck_sec_disclosure_monitor_watermarks_coverage_version_not_blank"),
        ),
        sa.CheckConstraint(
            "(accepted_at IS NULL AND accession IS NULL) OR "
            "(accepted_at IS NOT NULL AND accession ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$')",
            name=op.f("ck_sec_disclosure_monitor_watermarks_cursor_complete"),
        ),
        sa.ForeignKeyConstraint(
            ["monitor_id", "workspace_id"],
            ["sec_disclosure_monitors.id", "sec_disclosure_monitors.workspace_id"],
            name="fk_sec_disclosure_monitor_watermarks_monitor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sec_disclosure_monitor_watermarks")),
        sa.UniqueConstraint(
            "id",
            "monitor_id",
            "workspace_id",
            name=op.f("uq_sec_disclosure_monitor_watermarks_id_monitor_id_workspace_id"),
        ),
        sa.UniqueConstraint(
            "monitor_id",
            "revision",
            name=op.f("uq_sec_disclosure_monitor_watermarks_monitor_id_revision"),
        ),
    )
    op.create_index(
        op.f("ix_sec_disclosure_monitor_watermarks_workspace_id_monitor_id_revision"),
        "sec_disclosure_monitor_watermarks",
        ["workspace_id", "monitor_id", "revision"],
        unique=False,
    )

    op.create_table(
        "sec_disclosure_monitor_runs",
        _id_column(),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("monitor_id", sa.Uuid(), nullable=False),
        sa.Column("schedule_occurrence_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("source_watermark_id", sa.Uuid(), nullable=False),
        sa.Column("result_watermark_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("coalesced_count", sa.Integer(), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded')",
            name=op.f("ck_sec_disclosure_monitor_runs_status_supported"),
        ),
        sa.CheckConstraint(
            "coalesced_count >= 1",
            name=op.f("ck_sec_disclosure_monitor_runs_coalesced_count_positive"),
        ),
        sa.CheckConstraint(
            "window_end >= window_start",
            name=op.f("ck_sec_disclosure_monitor_runs_window_order"),
        ),
        sa.CheckConstraint(
            "(status = 'succeeded' AND result_watermark_id IS NOT NULL "
            "AND completed_at IS NOT NULL) "
            "OR (status <> 'succeeded' AND result_watermark_id IS NULL AND completed_at IS NULL)",
            name=op.f("ck_sec_disclosure_monitor_runs_terminal_state_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["monitor_id", "workspace_id"],
            ["sec_disclosure_monitors.id", "sec_disclosure_monitors.workspace_id"],
            name="fk_sec_disclosure_monitor_runs_monitor",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["schedule_occurrence_id"],
            ["schedule_occurrences.id"],
            name=op.f("fk_sec_disclosure_monitor_runs_schedule_occurrence_id_schedule_occurrences"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name=op.f("fk_sec_disclosure_monitor_runs_job_id_jobs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_watermark_id", "monitor_id", "workspace_id"],
            [
                "sec_disclosure_monitor_watermarks.id",
                "sec_disclosure_monitor_watermarks.monitor_id",
                "sec_disclosure_monitor_watermarks.workspace_id",
            ],
            name="fk_sec_disclosure_monitor_runs_source_watermark",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["result_watermark_id", "monitor_id", "workspace_id"],
            [
                "sec_disclosure_monitor_watermarks.id",
                "sec_disclosure_monitor_watermarks.monitor_id",
                "sec_disclosure_monitor_watermarks.workspace_id",
            ],
            name="fk_sec_disclosure_monitor_runs_result_watermark",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sec_disclosure_monitor_runs")),
        sa.UniqueConstraint(
            "id", "workspace_id", name=op.f("uq_sec_disclosure_monitor_runs_id_workspace_id")
        ),
        sa.UniqueConstraint(
            "schedule_occurrence_id",
            name=op.f("uq_sec_disclosure_monitor_runs_schedule_occurrence_id"),
        ),
        sa.UniqueConstraint("job_id", name=op.f("uq_sec_disclosure_monitor_runs_job_id")),
    )
    op.create_index(
        op.f("ix_sec_disclosure_monitor_runs_workspace_id_monitor_id_created_at"),
        "sec_disclosure_monitor_runs",
        ["workspace_id", "monitor_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "sec_disclosure_cases",
        _id_column(),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("monitor_id", sa.Uuid(), nullable=False),
        sa.Column("monitor_run_id", sa.Uuid(), nullable=False),
        sa.Column("rule_id", sa.Uuid(), nullable=False),
        sa.Column("trigger_kind", sa.String(length=32), nullable=False),
        sa.Column("rule_version", sa.String(length=64), nullable=False),
        sa.Column("source_coverage_version", sa.String(length=128), nullable=False),
        sa.Column("baseline_filing_id", sa.Uuid(), nullable=False),
        sa.Column("target_filing_id", sa.Uuid(), nullable=False),
        sa.Column("baseline_accession", sa.String(length=20), nullable=False),
        sa.Column("target_accession", sa.String(length=20), nullable=False),
        sa.Column("diff_version", sa.String(length=64), nullable=False),
        sa.Column("diff_payload", sa.JSON(), nullable=False),
        sa.Column("diff_sha256", sa.LargeBinary(length=32), nullable=False),
        sa.Column("verification_status", sa.String(length=16), nullable=False),
        sa.Column("notification_status", sa.String(length=16), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "trigger_kind IN ('new_filing', 'amendment', 'fact_absolute_change', 'section_change')",
            name=op.f("ck_sec_disclosure_cases_trigger_supported"),
        ),
        sa.CheckConstraint(
            "rule_version = 'sec-monitor-rules-v1'",
            name=op.f("ck_sec_disclosure_cases_rule_version_supported"),
        ),
        sa.CheckConstraint(
            "diff_version = 'sec-filing-diff-v1'",
            name=op.f("ck_sec_disclosure_cases_diff_version_supported"),
        ),
        sa.CheckConstraint(
            "octet_length(diff_sha256) = 32",
            name=op.f("ck_sec_disclosure_cases_diff_hash_length"),
        ),
        sa.CheckConstraint(
            "length(idempotency_key) = 64",
            name=op.f("ck_sec_disclosure_cases_idempotency_key_length"),
        ),
        sa.CheckConstraint(
            "verification_status = 'verified'",
            name=op.f("ck_sec_disclosure_cases_verification_status_supported"),
        ),
        sa.CheckConstraint(
            "notification_status = 'pending'",
            name=op.f("ck_sec_disclosure_cases_notification_status_supported"),
        ),
        sa.CheckConstraint(
            "baseline_accession ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$' AND "
            "target_accession ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$' AND "
            "baseline_accession <> target_accession",
            name=op.f("ck_sec_disclosure_cases_comparison_accessions_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["monitor_id", "workspace_id"],
            ["sec_disclosure_monitors.id", "sec_disclosure_monitors.workspace_id"],
            name="fk_sec_disclosure_cases_monitor",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["monitor_run_id", "workspace_id"],
            ["sec_disclosure_monitor_runs.id", "sec_disclosure_monitor_runs.workspace_id"],
            name="fk_sec_disclosure_cases_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rule_id", "monitor_id", "workspace_id"],
            [
                "sec_disclosure_monitor_rules.id",
                "sec_disclosure_monitor_rules.monitor_id",
                "sec_disclosure_monitor_rules.workspace_id",
            ],
            name="fk_sec_disclosure_cases_rule",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["baseline_filing_id"],
            ["sec_filings.id"],
            name=op.f("fk_sec_disclosure_cases_baseline_filing_id_sec_filings"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_filing_id"],
            ["sec_filings.id"],
            name=op.f("fk_sec_disclosure_cases_target_filing_id_sec_filings"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sec_disclosure_cases")),
        sa.UniqueConstraint(
            "id", "workspace_id", name=op.f("uq_sec_disclosure_cases_id_workspace_id")
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name=op.f("uq_sec_disclosure_cases_workspace_id_idempotency_key"),
        ),
    )
    op.create_index(
        op.f("ix_sec_disclosure_cases_workspace_id_monitor_id_created_at"),
        "sec_disclosure_cases",
        ["workspace_id", "monitor_id", "created_at"],
        unique=False,
    )

    op.add_column("evidence", sa.Column("origin_case_id", sa.Uuid(), nullable=True))
    op.alter_column("evidence", "origin_run_id", existing_type=sa.Uuid(), nullable=True)
    op.alter_column("evidence", "origin_step_id", existing_type=sa.Uuid(), nullable=True)
    op.alter_column("evidence", "origin_tool_call_id", existing_type=sa.Uuid(), nullable=True)
    op.create_check_constraint(
        op.f("ck_evidence_origin_exactly_one"),
        "evidence",
        "(origin_case_id IS NULL AND origin_run_id IS NOT NULL "
        "AND origin_step_id IS NOT NULL AND origin_tool_call_id IS NOT NULL) OR "
        "(origin_case_id IS NOT NULL AND origin_run_id IS NULL "
        "AND origin_step_id IS NULL AND origin_tool_call_id IS NULL)",
    )
    op.create_foreign_key(
        "fk_evidence_origin_case_workspace",
        "evidence",
        "sec_disclosure_cases",
        ["origin_case_id", "workspace_id"],
        ["id", "workspace_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        op.f("ix_evidence_workspace_id_origin_case_id"),
        "evidence",
        ["workspace_id", "origin_case_id"],
        unique=False,
    )

    op.create_table(
        "sec_disclosure_case_evidence",
        _id_column(),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.Column("side", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "side IN ('baseline', 'target')",
            name=op.f("ck_sec_disclosure_case_evidence_side_supported"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id", "workspace_id"],
            ["sec_disclosure_cases.id", "sec_disclosure_cases.workspace_id"],
            name="fk_sec_disclosure_case_evidence_case",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id", "workspace_id"],
            ["evidence.id", "evidence.workspace_id"],
            name="fk_sec_disclosure_case_evidence_evidence",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sec_disclosure_case_evidence")),
        sa.UniqueConstraint(
            "case_id", "side", name=op.f("uq_sec_disclosure_case_evidence_case_id_side")
        ),
        sa.UniqueConstraint(
            "case_id",
            "evidence_id",
            name=op.f("uq_sec_disclosure_case_evidence_case_id_evidence_id"),
        ),
    )
    op.create_index(
        op.f("ix_sec_disclosure_case_evidence_workspace_id_case_id_side"),
        "sec_disclosure_case_evidence",
        ["workspace_id", "case_id", "side"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_sec_disclosure_monitors_watermark_head",
        "sec_disclosure_monitors",
        "sec_disclosure_monitor_watermarks",
        ["current_watermark_id", "id", "workspace_id"],
        ["id", "monitor_id", "workspace_id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_sec_disclosure_monitors_watermark_head",
        "sec_disclosure_monitors",
        type_="foreignkey",
    )
    op.drop_index(
        op.f("ix_sec_disclosure_case_evidence_workspace_id_case_id_side"),
        table_name="sec_disclosure_case_evidence",
    )
    op.drop_table("sec_disclosure_case_evidence")
    op.drop_index(op.f("ix_evidence_workspace_id_origin_case_id"), table_name="evidence")
    op.drop_constraint("fk_evidence_origin_case_workspace", "evidence", type_="foreignkey")
    op.drop_constraint(op.f("ck_evidence_origin_exactly_one"), "evidence", type_="check")
    op.execute("DELETE FROM evidence WHERE origin_case_id IS NOT NULL")
    op.alter_column("evidence", "origin_tool_call_id", existing_type=sa.Uuid(), nullable=False)
    op.alter_column("evidence", "origin_step_id", existing_type=sa.Uuid(), nullable=False)
    op.alter_column("evidence", "origin_run_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_column("evidence", "origin_case_id")
    op.drop_index(
        op.f("ix_sec_disclosure_cases_workspace_id_monitor_id_created_at"),
        table_name="sec_disclosure_cases",
    )
    op.drop_table("sec_disclosure_cases")
    op.drop_index(
        op.f("ix_sec_disclosure_monitor_runs_workspace_id_monitor_id_created_at"),
        table_name="sec_disclosure_monitor_runs",
    )
    op.drop_table("sec_disclosure_monitor_runs")
    op.drop_index(
        op.f("ix_sec_disclosure_monitor_watermarks_workspace_id_monitor_id_revision"),
        table_name="sec_disclosure_monitor_watermarks",
    )
    op.drop_table("sec_disclosure_monitor_watermarks")
    op.drop_index(
        op.f("ix_sec_disclosure_monitor_rules_workspace_id_monitor_id_ordinal"),
        table_name="sec_disclosure_monitor_rules",
    )
    op.drop_table("sec_disclosure_monitor_rules")
    op.drop_index(
        op.f("ix_sec_disclosure_monitors_workspace_id_status_updated_at"),
        table_name="sec_disclosure_monitors",
    )
    op.drop_table("sec_disclosure_monitors")
