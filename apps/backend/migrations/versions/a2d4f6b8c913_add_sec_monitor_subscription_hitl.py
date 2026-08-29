"""Add durable Tool intent and Monitor approval idempotency.

Revision ID: a2d4f6b8c913
Revises: f1c3e5a7b902
Create Date: 2026-08-29 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a2d4f6b8c913"
down_revision: str | Sequence[str] | None = "f1c3e5a7b902"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_sec_disclosure_monitors_status_supported"),
        "sec_disclosure_monitors",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_sec_disclosure_monitors_status_supported"),
        "sec_disclosure_monitors",
        "status IN ('active', 'paused', 'deleted')",
    )
    op.add_column(
        "research_approval_requests",
        sa.Column("tool_call_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "research_approval_requests",
        sa.Column("tool_name", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "research_approval_requests",
        sa.Column("tool_version", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "research_approval_requests",
        sa.Column(
            "tool_arguments",
            postgresql.JSONB(astext_type=sa.Text(), none_as_null=True),
            nullable=True,
        ),
    )
    op.add_column(
        "research_approval_requests",
        sa.Column("tool_arguments_sha256", sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_research_approval_requests_tool_request_consistent"),
        "research_approval_requests",
        "(reason = 'monitor_subscription' AND tool_call_id IS NOT NULL "
        "AND tool_name IS NOT NULL AND tool_version IS NOT NULL "
        "AND tool_arguments IS NOT NULL AND tool_arguments_sha256 IS NOT NULL) OR "
        "(reason <> 'monitor_subscription' AND tool_call_id IS NULL "
        "AND tool_name IS NULL AND tool_version IS NULL "
        "AND tool_arguments IS NULL AND tool_arguments_sha256 IS NULL)",
    )
    op.create_foreign_key(
        op.f("fk_sec_disclosure_monitors_created_from_approval_id_research_approval_requests"),
        "sec_disclosure_monitors",
        "research_approval_requests",
        ["created_from_approval_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "uq_sec_disclosure_monitors_created_from_approval",
        "sec_disclosure_monitors",
        ["created_from_approval_id"],
        unique=True,
        postgresql_where=sa.text("created_from_approval_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.execute("UPDATE sec_disclosure_monitors SET status = 'paused' WHERE status = 'deleted'")
    op.drop_constraint(
        op.f("ck_sec_disclosure_monitors_status_supported"),
        "sec_disclosure_monitors",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_sec_disclosure_monitors_status_supported"),
        "sec_disclosure_monitors",
        "status IN ('active', 'paused')",
    )
    op.drop_index(
        "uq_sec_disclosure_monitors_created_from_approval",
        table_name="sec_disclosure_monitors",
        postgresql_where=sa.text("created_from_approval_id IS NOT NULL"),
    )
    op.drop_constraint(
        op.f("fk_sec_disclosure_monitors_created_from_approval_id_research_approval_requests"),
        "sec_disclosure_monitors",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("ck_research_approval_requests_tool_request_consistent"),
        "research_approval_requests",
        type_="check",
    )
    op.drop_column("research_approval_requests", "tool_arguments_sha256")
    op.drop_column("research_approval_requests", "tool_arguments")
    op.drop_column("research_approval_requests", "tool_version")
    op.drop_column("research_approval_requests", "tool_name")
    op.drop_column("research_approval_requests", "tool_call_id")
