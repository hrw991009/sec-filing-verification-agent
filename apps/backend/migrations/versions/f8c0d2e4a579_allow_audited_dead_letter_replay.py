"""Preserve terminal facts per delivery generation during authorized replay."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f8c0d2e4a579"
down_revision: str | Sequence[str] | None = "e6b8d0f2a357"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TERMINAL = "event_type IN ('succeeded', 'failed', 'cancelled', 'dead_letter')"


def upgrade() -> None:
    op.drop_index("uq_job_events_one_terminal_per_job", table_name="job_events")
    op.create_index(
        "uq_job_events_one_terminal_per_dispatch",
        "job_events",
        ["job_id", "dispatch_generation"],
        unique=True,
        postgresql_where=sa.text(_TERMINAL),
    )


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM job_events WHERE "
                "event_type IN ('succeeded', 'failed', 'cancelled', 'dead_letter') "
                "GROUP BY job_id HAVING COUNT(*) > 1)"
            )
        )
        .scalar_one()
    ):
        raise ValueError("Downgrade would erase or invalidate dead-letter replay history")
    op.drop_index("uq_job_events_one_terminal_per_dispatch", table_name="job_events")
    op.create_index(
        "uq_job_events_one_terminal_per_job",
        "job_events",
        ["job_id"],
        unique=True,
        postgresql_where=sa.text(_TERMINAL),
    )
