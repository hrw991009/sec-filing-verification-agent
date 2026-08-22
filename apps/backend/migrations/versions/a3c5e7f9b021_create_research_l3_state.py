"""create versioned Research L3 brief, plan, state, and draft facts

Revision ID: a3c5e7f9b021
Revises: f2a4c6e8b013
Create Date: 2026-08-21 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a3c5e7f9b021"
down_revision: str | Sequence[str] | None = "f2a4c6e8b013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id_column() -> sa.Column:
    return sa.Column(
        "id",
        sa.Uuid(),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def upgrade() -> None:
    op.add_column(
        "research_runs",
        sa.Column(
            "graph_version",
            sa.String(length=128),
            server_default="research-l3-graph-v1",
            nullable=False,
        ),
    )
    op.add_column(
        "research_runs",
        sa.Column("state_schema_version", sa.SmallInteger(), server_default="1", nullable=False),
    )
    op.add_column("research_runs", sa.Column("current_node", sa.String(length=32)))
    op.add_column(
        "research_runs",
        sa.Column(
            "state",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column("research_runs", sa.Column("error_summary", sa.String(length=500)))
    op.create_check_constraint(
        op.f("ck_research_runs_state_schema_version_supported"),
        "research_runs",
        "state_schema_version = 1",
    )
    op.create_check_constraint(
        op.f("ck_research_runs_graph_version_not_blank"),
        "research_runs",
        "length(btrim(graph_version)) > 0",
    )
    op.create_check_constraint(
        op.f("ck_research_runs_current_node_supported"),
        "research_runs",
        "current_node IS NULL OR current_node IN ("
        "'clarify_scope', 'write_research_brief', 'plan', 'research_loop', "
        "'normalize_evidence', 'synthesize_claims', 'outline', 'draft')",
    )
    op.alter_column("research_runs", "graph_version", server_default=None)
    op.alter_column("research_runs", "state_schema_version", server_default=None)

    op.create_table(
        "research_briefs",
        _id_column(),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("research_run_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("original_question", sa.Text(), nullable=False),
        sa.Column("confirmed_scope", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("exclusions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("completion_criteria", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("budget", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("confirmed_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_research_briefs_revision_positive")),
        sa.CheckConstraint(
            "length(btrim(original_question)) > 0",
            name=op.f("ck_research_briefs_question_not_blank"),
        ),
        sa.CheckConstraint(
            "jsonb_array_length(confirmed_scope) > 0",
            name=op.f("ck_research_briefs_scope_not_empty"),
        ),
        sa.CheckConstraint(
            "jsonb_array_length(completion_criteria) > 0",
            name=op.f("ck_research_briefs_criteria_not_empty"),
        ),
        sa.ForeignKeyConstraint(
            ["research_run_id", "workspace_id"],
            ["research_runs.id", "research_runs.workspace_id"],
            name=op.f("fk_research_briefs_research_run_id_research_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "confirmed_by_user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            name=op.f("fk_research_briefs_workspace_id_workspace_members"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_research_briefs")),
        sa.UniqueConstraint("id", "workspace_id", name=op.f("uq_research_briefs_id_workspace_id")),
        sa.UniqueConstraint(
            "research_run_id",
            "revision",
            name=op.f("uq_research_briefs_research_run_id_revision"),
        ),
    )
    op.create_index(
        op.f("ix_research_briefs_workspace_id_research_run_id_revision"),
        "research_briefs",
        ["workspace_id", "research_run_id", "revision"],
    )

    op.create_table(
        "research_plans",
        _id_column(),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("research_run_id", sa.Uuid(), nullable=False),
        sa.Column("brief_revision", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("actions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("planner_summary", sa.String(length=4000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "brief_revision >= 1 AND revision >= 1",
            name=op.f("ck_research_plans_revision_positive"),
        ),
        sa.CheckConstraint(
            "jsonb_array_length(actions) > 0",
            name=op.f("ck_research_plans_actions_not_empty"),
        ),
        sa.CheckConstraint(
            "length(btrim(planner_summary)) > 0",
            name=op.f("ck_research_plans_summary_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["research_run_id", "workspace_id"],
            ["research_runs.id", "research_runs.workspace_id"],
            name=op.f("fk_research_plans_research_run_id_research_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_research_plans")),
        sa.UniqueConstraint("id", "workspace_id", name=op.f("uq_research_plans_id_workspace_id")),
        sa.UniqueConstraint(
            "research_run_id",
            "revision",
            name=op.f("uq_research_plans_research_run_id_revision"),
        ),
    )
    op.create_index(
        op.f("ix_research_plans_workspace_id_research_run_id_revision"),
        "research_plans",
        ["workspace_id", "research_run_id", "revision"],
    )

    op.create_table(
        "research_drafts",
        _id_column(),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("research_run_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("content_markdown", sa.Text(), nullable=False),
        sa.Column("outline", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("claim_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("uncertainty_summary", sa.String(length=4000)),
        sa.Column("content_bytes", sa.BigInteger(), nullable=False),
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
            "status IN ('explainable_draft', 'uncertain_draft')",
            name=op.f("ck_research_drafts_status_supported"),
        ),
        sa.CheckConstraint(
            "length(btrim(content_markdown)) > 0",
            name=op.f("ck_research_drafts_content_not_blank"),
        ),
        sa.CheckConstraint(
            "jsonb_array_length(outline) > 0",
            name=op.f("ck_research_drafts_outline_not_empty"),
        ),
        sa.ForeignKeyConstraint(
            ["research_run_id", "workspace_id"],
            ["research_runs.id", "research_runs.workspace_id"],
            name=op.f("fk_research_drafts_research_run_id_research_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id", "workspace_id"],
            ["research_plans.id", "research_plans.workspace_id"],
            name=op.f("fk_research_drafts_plan_id_research_plans"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_research_drafts")),
        sa.UniqueConstraint("id", "workspace_id", name=op.f("uq_research_drafts_id_workspace_id")),
        sa.UniqueConstraint("research_run_id", name=op.f("uq_research_drafts_research_run_id")),
    )
    op.create_index(
        op.f("ix_research_drafts_workspace_id_research_run_id"),
        "research_drafts",
        ["workspace_id", "research_run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_research_drafts_workspace_id_research_run_id"),
        table_name="research_drafts",
    )
    op.drop_table("research_drafts")
    op.drop_index(
        op.f("ix_research_plans_workspace_id_research_run_id_revision"),
        table_name="research_plans",
    )
    op.drop_table("research_plans")
    op.drop_index(
        op.f("ix_research_briefs_workspace_id_research_run_id_revision"),
        table_name="research_briefs",
    )
    op.drop_table("research_briefs")
    op.drop_constraint(
        op.f("ck_research_runs_current_node_supported"), "research_runs", type_="check"
    )
    op.drop_constraint(
        op.f("ck_research_runs_graph_version_not_blank"), "research_runs", type_="check"
    )
    op.drop_constraint(
        op.f("ck_research_runs_state_schema_version_supported"),
        "research_runs",
        type_="check",
    )
    op.drop_column("research_runs", "error_summary")
    op.drop_column("research_runs", "state")
    op.drop_column("research_runs", "current_node")
    op.drop_column("research_runs", "state_schema_version")
    op.drop_column("research_runs", "graph_version")
