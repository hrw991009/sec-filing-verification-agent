"""Keep Evidence source ordinals aligned with the bounded Tool source contract."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e6b8d0f2a357"
down_revision: str | Sequence[str] | None = "d5a7c9e1f246"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _constraint(limit: int) -> None:
    name = op.f("ck_evidence_normalization_decisions_source_ordinal_bounded")
    op.drop_constraint(name, "evidence_normalization_decisions", type_="check")
    op.create_check_constraint(
        name, "evidence_normalization_decisions", f"source_ordinal BETWEEN 1 AND {limit}"
    )


def upgrade() -> None:
    _constraint(32)


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(sa.text("SELECT EXISTS (SELECT 1 FROM evidence WHERE origin_source_ordinal > 16)"))
        .scalar_one()
    ):
        raise ValueError("Downgrade would invalidate published Evidence source ordinals")
    _constraint(16)
