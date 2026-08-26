"""Add Research L4 approvals and side-effect ledger.

Revision ID: c2e6f8a0b431
Revises: b1d5e7f9a320
Create Date: 2026-08-25 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c2e6f8a0b431"
down_revision: str | Sequence[str] | None = "b1d5e7f9a320"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "DO $$ BEGIN "
            "IF EXISTS (SELECT 1 FROM pg_constraint "
            "WHERE conname = "
            "'ck_conversation_turns_ck_conversation_turns_web_require_2b06') THEN "
            "ALTER TABLE conversation_turns RENAME CONSTRAINT "
            "ck_conversation_turns_ck_conversation_turns_web_require_2b06 "
            "TO ck_conversation_turns_web_requires_industry; "
            "END IF; END $$"
        )
    )
    op.drop_constraint(op.f("ck_research_runs_status_supported"), "research_runs", type_="check")
    op.create_check_constraint(
        op.f("ck_research_runs_status_supported"),
        "research_runs",
        "status IN ('draft', 'active', 'paused', 'completed', 'failed', 'cancelled')",
    )
    op.create_unique_constraint(
        op.f("uq_agent_checkpoints_id_run_id_workspace_id"),
        "agent_checkpoints",
        ["id", "run_id", "workspace_id"],
    )
    op.add_column(
        "research_briefs",
        sa.Column("approval_reason", sa.String(length=40), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_research_briefs_approval_reason_supported"),
        "research_briefs",
        "approval_reason IS NULL OR approval_reason = 'company_or_period_ambiguity'",
    )
    op.create_check_constraint(
        op.f("ck_research_briefs_approval_requires_financial_scope"),
        "research_briefs",
        "approval_reason IS NULL OR financial_scope IS NOT NULL",
    )
    op.create_table(
        "research_approval_requests",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("checkpoint_id", sa.Uuid(), nullable=False),
        sa.Column("checkpoint_revision", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("resume_token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "resume_claimed",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("resume_job_id", sa.Uuid(), nullable=True),
        sa.Column("resumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "checkpoint_revision >= 0",
            name=op.f("ck_research_approval_requests_checkpoint_revision_nonnegative"),
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name=op.f("ck_research_approval_requests_expiry_after_creation"),
        ),
        sa.CheckConstraint(
            "octet_length(resume_token_hash) = 32",
            name=op.f("ck_research_approval_requests_resume_token_hash_length"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'allowed', 'denied', 'timed_out')",
            name=op.f("ck_research_approval_requests_status_supported"),
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND decided_at IS NULL AND decided_by_user_id IS NULL) "
            "OR (status <> 'pending' AND decided_at IS NOT NULL)",
            name=op.f("ck_research_approval_requests_decision_consistent"),
        ),
        sa.CheckConstraint(
            "(resume_claimed = false AND resume_job_id IS NULL AND resumed_at IS NULL) "
            "OR (resume_claimed = true AND resume_job_id IS NOT NULL AND resumed_at IS NOT NULL)",
            name=op.f("ck_research_approval_requests_resume_claim_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["checkpoint_id", "run_id", "workspace_id"],
            [
                "agent_checkpoints.id",
                "agent_checkpoints.run_id",
                "agent_checkpoints.workspace_id",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "workspace_id"],
            ["agent_runs.id", "agent_runs.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_research_approval_requests")),
        sa.UniqueConstraint(
            "id",
            "workspace_id",
            name=op.f("uq_research_approval_requests_id_workspace_id"),
        ),
        sa.UniqueConstraint(
            "run_id",
            "checkpoint_revision",
            name=op.f("uq_research_approval_requests_run_id_checkpoint_revision"),
        ),
    )
    op.create_index(
        op.f("ix_research_approval_requests_workspace_id_run_id_created_at"),
        "research_approval_requests",
        ["workspace_id", "run_id", "created_at"],
    )
    op.create_table(
        "research_approval_decisions",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("approval_request_id", sa.Uuid(), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["approval_request_id", "workspace_id"],
            [
                "research_approval_requests.id",
                "research_approval_requests.workspace_id",
            ],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_research_approval_decisions")),
        sa.UniqueConstraint(
            "approval_request_id",
            name=op.f("uq_research_approval_decisions_approval_request_id"),
        ),
    )
    op.create_table(
        "research_side_effects",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("effect_kind", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("resource_ref", sa.String(length=200), nullable=True),
        sa.Column("result_sha256", sa.String(length=64), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(btrim(effect_kind)) > 0",
            name=op.f("ck_research_side_effects_effect_kind_not_blank"),
        ),
        sa.CheckConstraint(
            "octet_length(idempotency_key_hash) = 32",
            name=op.f("ck_research_side_effects_key_hash_length"),
        ),
        sa.CheckConstraint(
            "status IN ('intent', 'completed', 'failed')",
            name=op.f("ck_research_side_effects_status_supported"),
        ),
        sa.CheckConstraint(
            "(status = 'completed' AND completed_at IS NOT NULL) OR "
            "(status <> 'completed' AND completed_at IS NULL)",
            name=op.f("ck_research_side_effects_completion_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "workspace_id"],
            ["agent_runs.id", "agent_runs.workspace_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_research_side_effects")),
        sa.UniqueConstraint(
            "workspace_id",
            "effect_kind",
            "idempotency_key_hash",
            name=op.f("uq_research_side_effects_workspace_id_effect_kind_idempotency_key_hash"),
        ),
    )
    op.create_index(
        op.f("ix_research_side_effects_workspace_id_run_id_created_at"),
        "research_side_effects",
        ["workspace_id", "run_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_research_side_effects_workspace_id_run_id_created_at"),
        table_name="research_side_effects",
    )
    op.drop_table("research_side_effects")
    op.drop_table("research_approval_decisions")
    op.drop_index(
        op.f("ix_research_approval_requests_workspace_id_run_id_created_at"),
        table_name="research_approval_requests",
    )
    op.drop_table("research_approval_requests")
    op.drop_constraint(
        op.f("ck_research_briefs_approval_requires_financial_scope"),
        "research_briefs",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_research_briefs_approval_reason_supported"),
        "research_briefs",
        type_="check",
    )
    op.drop_column("research_briefs", "approval_reason")
    op.drop_constraint(
        op.f("uq_agent_checkpoints_id_run_id_workspace_id"),
        "agent_checkpoints",
        type_="unique",
    )
    op.drop_constraint(op.f("ck_research_runs_status_supported"), "research_runs", type_="check")
    op.create_check_constraint(
        op.f("ck_research_runs_status_supported"),
        "research_runs",
        "status IN ('draft', 'active', 'completed', 'failed', 'cancelled')",
    )
