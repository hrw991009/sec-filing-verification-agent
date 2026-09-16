"""Accommodate long identifiers in official inline XBRL filings.

Revision ID: d5a7c9e1f246
Revises: c4f6a8b0d135
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5a7c9e1f246"
down_revision: str | Sequence[str] | None = "c4f6a8b0d135"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("sec_xbrl_contexts", "sec_xbrl_facts"):
        op.alter_column(table, "raw_context_id", type_=sa.String(512))
    op.alter_column("sec_xbrl_facts", "locator_key", type_=sa.String(1024))


def downgrade() -> None:
    # Never truncate identifiers or silently break source identity on downgrade.
    connection = op.get_bind()
    for table, column, limit in (
        ("sec_xbrl_contexts", "raw_context_id", 255),
        ("sec_xbrl_facts", "raw_context_id", 255),
        ("sec_xbrl_facts", "locator_key", 512),
    ):
        if connection.execute(
            sa.select(
                sa.exists().where(
                    sa.func.length(sa.table(table, sa.column(column)).c[column])
                    > sa.bindparam("limit")
                )
            ),
            {"limit": limit},
        ).scalar_one():
            raise ValueError("Downgrade would truncate SEC XBRL source identifiers")
        op.alter_column(table, column, type_=sa.String(limit))
