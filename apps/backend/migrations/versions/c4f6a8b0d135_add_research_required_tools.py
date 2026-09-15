"""Persist the user-confirmed Research required Tool sequence.

Revision ID: c4f6a8b0d135
Revises: b3e5f7a9c024
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4f6a8b0d135"
down_revision: str | Sequence[str] | None = "b3e5f7a9c024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "research_briefs",
        sa.Column(
            "required_tool_names",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("research_briefs", "required_tool_names")
