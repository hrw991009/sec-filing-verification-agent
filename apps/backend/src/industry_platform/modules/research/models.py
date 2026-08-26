"""PostgreSQL records for versioned Research L3/L4 business facts."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
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
    ResearchApprovalOutcome,
    ResearchApprovalReason,
    ResearchApprovalStatus,
    ResearchDraftStatus,
    ResearchNode,
    ResearchRunStatus,
    ResearchSideEffectStatus,
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
            "status IN ('draft', 'active', 'paused', 'completed', 'failed', 'cancelled')",
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
        CheckConstraint(
            "approval_reason IS NULL OR approval_reason = 'company_or_period_ambiguity'",
            name="approval_reason_supported",
        ),
        CheckConstraint(
            "approval_reason IS NULL OR financial_scope IS NOT NULL",
            name="approval_requires_financial_scope",
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
    approval_reason: Mapped[ResearchApprovalReason | None] = mapped_column(
        SqlEnum(
            ResearchApprovalReason,
            name="research_approval_reason",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=40,
        ),
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


class ResearchApprovalRequestRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One durable interrupt bound to an exact Agent Checkpoint."""

    __tablename__ = "research_approval_requests"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id"),
        UniqueConstraint("run_id", "checkpoint_revision"),
        ForeignKeyConstraint(
            ["run_id", "workspace_id"],
            ["agent_runs.id", "agent_runs.workspace_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["checkpoint_id", "run_id", "workspace_id"],
            ["agent_checkpoints.id", "agent_checkpoints.run_id", "agent_checkpoints.workspace_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('pending', 'allowed', 'denied', 'timed_out')",
            name="status_supported",
        ),
        CheckConstraint("checkpoint_revision >= 0", name="checkpoint_revision_nonnegative"),
        CheckConstraint("octet_length(resume_token_hash) = 32", name="resume_token_hash_length"),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        CheckConstraint(
            "(status = 'pending' AND decided_at IS NULL AND decided_by_user_id IS NULL) OR "
            "(status <> 'pending' AND decided_at IS NOT NULL)",
            name="decision_consistent",
        ),
        CheckConstraint(
            "(resume_claimed = false AND resume_job_id IS NULL AND resumed_at IS NULL) OR "
            "(resume_claimed = true AND resume_job_id IS NOT NULL AND resumed_at IS NOT NULL)",
            name="resume_claim_consistent",
        ),
        Index(None, "workspace_id", "run_id", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    checkpoint_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    checkpoint_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[ResearchApprovalReason] = mapped_column(
        SqlEnum(
            ResearchApprovalReason,
            name="research_approval_reason",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=40,
        ),
        nullable=False,
    )
    status: Mapped[ResearchApprovalStatus] = mapped_column(
        SqlEnum(
            ResearchApprovalStatus,
            name="research_approval_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    resume_token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    requested_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_by_user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resume_claimed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    resume_job_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResearchApprovalDecisionRecord(UUIDPrimaryKeyMixin, Base):
    """Append-only human decision audit for one Approval Request."""

    __tablename__ = "research_approval_decisions"
    __table_args__ = (
        UniqueConstraint("approval_request_id"),
        ForeignKeyConstraint(
            ["approval_request_id", "workspace_id"],
            ["research_approval_requests.id", "research_approval_requests.workspace_id"],
            ondelete="CASCADE",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    approval_request_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    outcome: Mapped[ResearchApprovalOutcome] = mapped_column(
        SqlEnum(
            ResearchApprovalOutcome,
            name="research_approval_outcome",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    decided_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResearchSideEffectRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Domain-separated idempotency ledger consulted before L4 resume."""

    __tablename__ = "research_side_effects"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "workspace_id"],
            ["agent_runs.id", "agent_runs.workspace_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "effect_kind", "idempotency_key_hash"),
        CheckConstraint("length(btrim(effect_kind)) > 0", name="effect_kind_not_blank"),
        CheckConstraint("octet_length(idempotency_key_hash) = 32", name="key_hash_length"),
        CheckConstraint("status IN ('intent', 'completed', 'failed')", name="status_supported"),
        CheckConstraint(
            "(status = 'completed' AND completed_at IS NOT NULL) OR "
            "(status <> 'completed' AND completed_at IS NULL)",
            name="completion_consistent",
        ),
        Index(None, "workspace_id", "run_id", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    effect_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    status: Mapped[ResearchSideEffectStatus] = mapped_column(
        SqlEnum(
            ResearchSideEffectStatus,
            name="research_side_effect_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    resource_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    result_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
