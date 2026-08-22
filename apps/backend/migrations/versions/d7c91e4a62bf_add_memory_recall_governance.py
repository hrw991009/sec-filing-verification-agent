"""add Memory recall governance and feedback

Revision ID: d7c91e4a62bf
Revises: b4d8f3a9c210
Create Date: 2026-08-20 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7c91e4a62bf"
down_revision: str | Sequence[str] | None = "b4d8f3a9c210"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add resource CAS, revision expiry, and user feedback facts."""

    op.add_column(
        "memories",
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_memories_revision_positive"),
        "memories",
        "revision >= 1",
    )
    op.add_column(
        "memory_revisions",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE memory_revisions AS revision
        SET expires_at = memory.expires_at
        FROM memories AS memory
        WHERE revision.memory_id = memory.id
          AND revision.workspace_id = memory.workspace_id
        """
    )

    op.create_table(
        "memory_feedback",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("memory_id", sa.Uuid(), nullable=False),
        sa.Column("memory_revision_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("value", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("schema_version", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
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
            "value IN ('helpful', 'not_helpful')",
            name=op.f("ck_memory_feedback_value_supported"),
        ),
        sa.CheckConstraint(
            "schema_version = 1",
            name=op.f("ck_memory_feedback_schema_version_supported"),
        ),
        sa.ForeignKeyConstraint(
            ["memory_id", "workspace_id"],
            ["memories.id", "memories.workspace_id"],
            name="fk_memory_feedback_memory_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["memory_revision_id", "memory_id", "workspace_id"],
            ["memory_revisions.id", "memory_revisions.memory_id", "memory_revisions.workspace_id"],
            name="fk_memory_feedback_revision_memory_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "actor_user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            name="fk_memory_feedback_workspace_actor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memory_feedback")),
        sa.UniqueConstraint(
            "memory_id",
            "memory_revision_id",
            "actor_user_id",
            name=op.f("uq_memory_feedback_memory_id_memory_revision_id_actor_user_id"),
        ),
    )
    op.create_index(
        op.f("ix_memory_feedback_workspace_id_actor_user_id_updated_at"),
        "memory_feedback",
        ["workspace_id", "actor_user_id", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    """Remove feedback and CAS additions without touching base Memory facts."""

    op.drop_index(
        op.f("ix_memory_feedback_workspace_id_actor_user_id_updated_at"),
        table_name="memory_feedback",
    )
    op.drop_table("memory_feedback")
    op.drop_column("memory_revisions", "expires_at")
    op.drop_constraint(op.f("ck_memories_revision_positive"), "memories", type_="check")
    op.drop_column("memories", "revision")
