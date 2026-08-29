"""Add append-only Research verification reports, Claim verdicts, and issues.

Revision ID: d8a1c3e5f702
Revises: c8d2f4a6b901
Create Date: 2026-08-29 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d8a1c3e5f702"
down_revision: str | Sequence[str] | None = "c8d2f4a6b901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id_column() -> sa.Column[object]:
    return sa.Column(
        "id",
        sa.Uuid(),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def upgrade() -> None:
    op.alter_column(
        "research_runs",
        "current_node",
        existing_type=sa.String(length=32),
        type_=sa.String(length=40),
        existing_nullable=True,
    )

    op.create_table(
        "research_verification_reports",
        _id_column(),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("research_run_id", sa.Uuid(), nullable=False),
        sa.Column("agent_run_id", sa.Uuid(), nullable=False),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.SmallInteger(), nullable=False),
        sa.Column("checker_version", sa.String(length=64), nullable=False),
        sa.Column("graph_version", sa.String(length=128), nullable=False),
        sa.Column("financial_scope", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("coverage", sa.Float(), nullable=False),
        sa.Column("required_claim_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence_snapshots", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("runtime_stop_reason", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "schema_version = 1",
            name=op.f("ck_research_verification_reports_schema_version_supported"),
        ),
        sa.CheckConstraint(
            "checker_version = 'sec-claim-verifier-v1'",
            name=op.f("ck_research_verification_reports_checker_version_supported"),
        ),
        sa.CheckConstraint(
            "revision >= 1", name=op.f("ck_research_verification_reports_revision_positive")
        ),
        sa.CheckConstraint(
            "coverage BETWEEN 0 AND 1",
            name=op.f("ck_research_verification_reports_coverage_bounded"),
        ),
        sa.CheckConstraint(
            "status IN ('verified', 'partial', 'conflict', 'insufficient_evidence')",
            name=op.f("ck_research_verification_reports_status_supported"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(financial_scope) = 'object'",
            name=op.f("ck_research_verification_reports_scope_object"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(required_claim_ids) = 'array' "
            "AND jsonb_array_length(required_claim_ids) > 0",
            name=op.f("ck_research_verification_reports_required_claims_not_empty"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_snapshots) = 'array'",
            name=op.f("ck_research_verification_reports_evidence_snapshots_array"),
        ),
        sa.ForeignKeyConstraint(
            ["research_run_id", "workspace_id"],
            ["research_runs.id", "research_runs.workspace_id"],
            ondelete="RESTRICT",
            name=op.f("fk_research_verification_reports_research_run_id_research_runs"),
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id", "workspace_id"],
            ["agent_runs.id", "agent_runs.workspace_id"],
            ondelete="RESTRICT",
            name=op.f("fk_research_verification_reports_agent_run_id_agent_runs"),
        ),
        sa.ForeignKeyConstraint(
            ["draft_id", "workspace_id"],
            ["research_drafts.id", "research_drafts.workspace_id"],
            ondelete="RESTRICT",
            name=op.f("fk_research_verification_reports_draft_id_research_drafts"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_research_verification_reports")),
        sa.UniqueConstraint(
            "id", "workspace_id", name=op.f("uq_research_verification_reports_id_workspace_id")
        ),
        sa.UniqueConstraint(
            "research_run_id",
            "revision",
            name=op.f("uq_research_verification_reports_research_run_id_revision"),
        ),
    )
    op.create_index(
        op.f("ix_research_verification_reports_workspace_id_research_run_id_revision"),
        "research_verification_reports",
        ["workspace_id", "research_run_id", "revision"],
        unique=False,
    )

    op.create_table(
        "research_verification_claims",
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("claim_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.SmallInteger(), nullable=False),
        sa.Column("claim_revision", sa.Integer(), nullable=True),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("coverage", sa.Float(), nullable=False),
        sa.Column("evidence_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("citation_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("calculation_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "ordinal BETWEEN 1 AND 100",
            name=op.f("ck_research_verification_claims_ordinal_bounded"),
        ),
        sa.CheckConstraint(
            "claim_revision IS NULL OR claim_revision >= 1",
            name=op.f("ck_research_verification_claims_claim_revision_positive"),
        ),
        sa.CheckConstraint(
            "coverage BETWEEN 0 AND 1",
            name=op.f("ck_research_verification_claims_coverage_bounded"),
        ),
        sa.CheckConstraint(
            "verdict IN ('supported', 'refuted', 'conflicting', 'insufficient')",
            name=op.f("ck_research_verification_claims_verdict_supported"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_refs) = 'array' "
            "AND jsonb_typeof(citation_refs) = 'array' "
            "AND jsonb_typeof(calculation_refs) = 'array'",
            name=op.f("ck_research_verification_claims_refs_are_arrays"),
        ),
        sa.ForeignKeyConstraint(
            ["report_id", "workspace_id"],
            ["research_verification_reports.id", "research_verification_reports.workspace_id"],
            ondelete="CASCADE",
            name=op.f("fk_research_verification_claims_report_id_research_verification_reports"),
        ),
        sa.PrimaryKeyConstraint(
            "report_id", "claim_id", name=op.f("pk_research_verification_claims")
        ),
        sa.UniqueConstraint(
            "report_id",
            "ordinal",
            name=op.f("uq_research_verification_claims_report_id_ordinal"),
        ),
    )

    op.create_table(
        "research_verification_issues",
        _id_column(),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.SmallInteger(), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("claim_id", sa.Uuid(), nullable=True),
        sa.Column("expected_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("observed_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("repairability", sa.String(length=16), nullable=False),
        sa.Column("allowed_action", sa.String(length=24), nullable=True),
        sa.Column("details_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "ordinal BETWEEN 1 AND 1000",
            name=op.f("ck_research_verification_issues_ordinal_bounded"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(expected_refs) = 'array' AND jsonb_typeof(observed_refs) = 'array'",
            name=op.f("ck_research_verification_issues_refs_are_arrays"),
        ),
        sa.CheckConstraint(
            "details_digest ~ '^[a-f0-9]{64}$'",
            name=op.f("ck_research_verification_issues_digest_valid"),
        ),
        sa.CheckConstraint(
            "code IN ('claim_not_found', 'relation_invalidated', 'evidence_inactive', "
            "'authorization_mismatch', 'citation_unresolvable', "
            "'scope_identity_mismatch', 'future_source', 'source_hash_mismatch', "
            "'calculation_input_missing', 'calculation_mismatch', 'claim_conflict', "
            "'claim_refuted', 'missing_evidence', 'coverage_incomplete')",
            name=op.f("ck_research_verification_issues_code_supported"),
        ),
        sa.CheckConstraint(
            "severity IN ('error', 'warning')",
            name=op.f("ck_research_verification_issues_severity_supported"),
        ),
        sa.CheckConstraint(
            "repairability IN ('repairable', 'terminal')",
            name=op.f("ck_research_verification_issues_repairability_supported"),
        ),
        sa.CheckConstraint(
            "allowed_action IS NULL OR allowed_action IN ('targeted_retrieve', 'recalculate')",
            name=op.f("ck_research_verification_issues_allowed_action_supported"),
        ),
        sa.CheckConstraint(
            "(repairability = 'repairable' AND allowed_action IS NOT NULL) OR "
            "(repairability = 'terminal' AND allowed_action IS NULL)",
            name=op.f("ck_research_verification_issues_repair_action_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["report_id", "workspace_id"],
            ["research_verification_reports.id", "research_verification_reports.workspace_id"],
            ondelete="CASCADE",
            name=op.f("fk_research_verification_issues_report_id_research_verification_reports"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_research_verification_issues")),
        sa.UniqueConstraint(
            "report_id",
            "ordinal",
            name=op.f("uq_research_verification_issues_report_id_ordinal"),
        ),
    )
    op.create_index(
        op.f("ix_research_verification_issues_workspace_id_report_id_ordinal"),
        "research_verification_issues",
        ["workspace_id", "report_id", "ordinal"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_research_verification_issues_workspace_id_report_id_ordinal"),
        table_name="research_verification_issues",
    )
    op.drop_table("research_verification_issues")
    op.drop_table("research_verification_claims")
    op.drop_index(
        op.f("ix_research_verification_reports_workspace_id_research_run_id_revision"),
        table_name="research_verification_reports",
    )
    op.drop_table("research_verification_reports")
    op.alter_column(
        "research_runs",
        "current_node",
        existing_type=sa.String(length=40),
        type_=sa.String(length=32),
        existing_nullable=True,
    )
