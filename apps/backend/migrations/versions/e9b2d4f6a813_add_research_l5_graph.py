"""Add the Research L5 graph and append-only Draft revisions.

Revision ID: e9b2d4f6a813
Revises: d8a1c3e5f702
Create Date: 2026-08-29 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e9b2d4f6a813"
down_revision: str | Sequence[str] | None = "d8a1c3e5f702"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_research_runs_state_schema_version_supported"),
        "research_runs",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_research_runs_state_schema_version_supported"),
        "research_runs",
        "state_schema_version IN (1, 2)",
    )
    op.drop_constraint(
        op.f("ck_research_runs_current_node_supported"),
        "research_runs",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_research_runs_current_node_supported"),
        "research_runs",
        "current_node IS NULL OR current_node IN ("
        "'clarify_scope', 'write_research_brief', 'plan', 'research_loop', "
        "'normalize_evidence', 'synthesize_claims', 'outline', 'draft', "
        "'verify', 'revise', 'finalize')",
    )

    op.add_column(
        "research_drafts",
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_research_drafts_revision_positive"),
        "research_drafts",
        "revision >= 1",
    )
    op.drop_constraint(
        op.f("uq_research_drafts_research_run_id"),
        "research_drafts",
        type_="unique",
    )
    op.create_unique_constraint(
        op.f("uq_research_drafts_research_run_id_revision"),
        "research_drafts",
        ["research_run_id", "revision"],
    )
    op.drop_index(
        op.f("ix_research_drafts_workspace_id_research_run_id"),
        table_name="research_drafts",
    )
    op.create_index(
        op.f("ix_research_drafts_workspace_id_research_run_id_revision"),
        "research_drafts",
        ["workspace_id", "research_run_id", "revision"],
        unique=False,
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM research_verification_reports AS report "
        "USING research_drafts AS draft "
        "WHERE report.draft_id = draft.id AND draft.revision > 1"
    )
    op.execute("DELETE FROM research_drafts WHERE revision > 1")
    op.execute(
        "UPDATE research_runs SET current_node = 'draft', "
        "graph_version = 'research-l4-graph-v1', state_schema_version = 1 "
        "WHERE state_schema_version = 2"
    )
    op.drop_index(
        op.f("ix_research_drafts_workspace_id_research_run_id_revision"),
        table_name="research_drafts",
    )
    op.create_index(
        op.f("ix_research_drafts_workspace_id_research_run_id"),
        "research_drafts",
        ["workspace_id", "research_run_id"],
        unique=False,
    )
    op.drop_constraint(
        op.f("uq_research_drafts_research_run_id_revision"),
        "research_drafts",
        type_="unique",
    )
    op.create_unique_constraint(
        op.f("uq_research_drafts_research_run_id"),
        "research_drafts",
        ["research_run_id"],
    )
    op.drop_constraint(
        op.f("ck_research_drafts_revision_positive"),
        "research_drafts",
        type_="check",
    )
    op.drop_column("research_drafts", "revision")

    op.drop_constraint(
        op.f("ck_research_runs_current_node_supported"),
        "research_runs",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_research_runs_current_node_supported"),
        "research_runs",
        "current_node IS NULL OR current_node IN ("
        "'clarify_scope', 'write_research_brief', 'plan', 'research_loop', "
        "'normalize_evidence', 'synthesize_claims', 'outline', 'draft')",
    )
    op.drop_constraint(
        op.f("ck_research_runs_state_schema_version_supported"),
        "research_runs",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_research_runs_state_schema_version_supported"),
        "research_runs",
        "state_schema_version = 1",
    )
