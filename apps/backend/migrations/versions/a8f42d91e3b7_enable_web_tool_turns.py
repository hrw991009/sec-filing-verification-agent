"""enable web Tool turns

Revision ID: a8f42d91e3b7
Revises: c6a8e1d4f290
Create Date: 2026-08-17 16:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a8f42d91e3b7"
down_revision: str | Sequence[str] | None = "c6a8e1d4f290"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Bind a web Turn to one real preset captured with its Runtime command."""

    op.create_foreign_key(
        "fk_conversation_turns_industry",
        "conversation_turns",
        "industries",
        ["industry_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_conversation_turns_web_requires_industry"),
        "conversation_turns",
        "search_mode <> 'web' OR industry_id IS NOT NULL",
    )


def downgrade() -> None:
    """Remove only the Day 3 web Turn constraints."""

    op.drop_constraint(
        op.f("ck_conversation_turns_web_requires_industry"),
        "conversation_turns",
        type_="check",
    )
    op.drop_constraint(
        "fk_conversation_turns_industry",
        "conversation_turns",
        type_="foreignkey",
    )
