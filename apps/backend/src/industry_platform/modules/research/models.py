"""PostgreSQL records for versioned Research L3 business facts."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from industry_platform.core.database import Base, TimestampMixin, UUIDPrimaryKeyMixin
from industry_platform.modules.identity.models import enum_values
from industry_platform.modules.research.domain import (
    RESEARCH_GRAPH_VERSION,
    RESEARCH_STATE_SCHEMA_VERSION,
    ResearchDraftStatus,
    ResearchNode,
    ResearchRunStatus,
)


class ResearchRunRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Domain extension of one unified AgentRun; never a second execution history."""

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
        CheckConstraint("state_schema_version = 1", name="state_schema_version_supported"),
        CheckConstraint("length(btrim(graph_version)) > 0", name="graph_version_not_blank"),
        CheckConstraint(
            "current_node IS NULL OR current_node IN ("
            "'clarify_scope', 'write_research_brief', 'plan', 'research_loop', "
            "'normalize_evidence', 'synthesize_claims', 'outline', 'draft')",
            name="current_node_supported",
        ),
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
    graph_version: Mapped[str] = mapped_column(
        String(128), nullable=False, default=RESEARCH_GRAPH_VERSION
    )
    state_schema_version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=RESEARCH_STATE_SCHEMA_VERSION
    )
    current_node: Mapped[ResearchNode | None] = mapped_column(
        SqlEnum(
            ResearchNode,
            name="research_node",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=32,
        ),
        nullable=True,
    )
    state: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    error_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)


class ResearchBriefRecord(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "research_briefs"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id"),
        UniqueConstraint("research_run_id", "revision"),
        ForeignKeyConstraint(
            ["research_run_id", "workspace_id"],
            ["research_runs.id", "research_runs.workspace_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "confirmed_by_user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("length(btrim(original_question)) > 0", name="question_not_blank"),
        CheckConstraint("jsonb_array_length(confirmed_scope) > 0", name="scope_not_empty"),
        CheckConstraint("jsonb_array_length(completion_criteria) > 0", name="criteria_not_empty"),
        CheckConstraint(
            "financial_scope IS NULL OR jsonb_typeof(financial_scope) = 'object'",
            name="financial_scope_object",
        ),
        Index(None, "workspace_id", "research_run_id", "revision"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    research_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    original_question: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_scope: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    exclusions: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    completion_criteria: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    financial_scope: Mapped[dict[str, object] | None] = mapped_column(
        JSONB(none_as_null=True),
        nullable=True,
    )
    budget: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    confirmed_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResearchPlanRecord(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "research_plans"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id"),
        UniqueConstraint("research_run_id", "revision"),
        ForeignKeyConstraint(
            ["research_run_id", "workspace_id"],
            ["research_runs.id", "research_runs.workspace_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("brief_revision >= 1 AND revision >= 1", name="revision_positive"),
        CheckConstraint("jsonb_array_length(actions) > 0", name="actions_not_empty"),
        CheckConstraint("length(btrim(planner_summary)) > 0", name="summary_not_blank"),
        Index(None, "workspace_id", "research_run_id", "revision"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    research_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    brief_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    actions: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    planner_summary: Mapped[str] = mapped_column(String(4_000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResearchDraftRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "research_drafts"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id"),
        UniqueConstraint("research_run_id"),
        ForeignKeyConstraint(
            ["research_run_id", "workspace_id"],
            ["research_runs.id", "research_runs.workspace_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["plan_id", "workspace_id"],
            ["research_plans.id", "research_plans.workspace_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('explainable_draft', 'uncertain_draft')", name="status_supported"
        ),
        CheckConstraint("length(btrim(content_markdown)) > 0", name="content_not_blank"),
        CheckConstraint("jsonb_array_length(outline) > 0", name="outline_not_empty"),
        Index(None, "workspace_id", "research_run_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    research_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    plan_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    status: Mapped[ResearchDraftStatus] = mapped_column(
        SqlEnum(
            ResearchDraftStatus,
            name="research_draft_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=24,
        ),
        nullable=False,
    )
    content_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    outline: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    evidence_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    claim_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    uncertainty_summary: Mapped[str | None] = mapped_column(String(4_000), nullable=True)
    content_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
