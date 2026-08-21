"""PostgreSQL ownership shell for the later typed Research L3 aggregate."""

from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column

from industry_platform.core.database import Base, TimestampMixin, UUIDPrimaryKeyMixin
from industry_platform.modules.identity.models import enum_values
from industry_platform.modules.research.domain import ResearchRunStatus


class ResearchRunRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Minimal aggregate root; workflow state is added only in Day 4 step 4."""

    __tablename__ = "research_runs"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id"),
        UniqueConstraint("id", "workspace_id", "owner_user_id"),
        UniqueConstraint("agent_run_id"),
        ForeignKeyConstraint(
            ["workspace_id", "owner_user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            name="fk_research_runs_workspace_owner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["agent_run_id", "workspace_id", "owner_user_id"],
            ["agent_runs.id", "agent_runs.workspace_id", "agent_runs.user_id"],
            name="fk_research_runs_agent_run_workspace_owner",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('draft', 'active', 'completed', 'failed', 'cancelled')",
            name="status_supported",
        ),
        CheckConstraint("revision >= 1", name="revision_positive"),
        Index(None, "workspace_id", "owner_user_id", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    owner_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    agent_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    status: Mapped[ResearchRunStatus] = mapped_column(
        SqlEnum(
            ResearchRunStatus,
            name="research_run_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
        default=ResearchRunStatus.DRAFT,
        server_default=ResearchRunStatus.DRAFT.value,
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
