"""PostgreSQL records for Evidence, Claims, normalization decisions, and derived graphs."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
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
from industry_platform.modules.evidence.domain import (
    ClaimEvidenceRelation,
    ClaimVerificationStatus,
    EvidenceDecision,
    EvidenceDecisionReason,
    EvidenceKind,
    EvidenceLocatorType,
    EvidenceStatus,
    GraphNodeType,
    RelationStatus,
)
from industry_platform.modules.identity.models import enum_values


class EvidenceRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "evidence"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id"),
        UniqueConstraint("workspace_id", "deduplication_key"),
        ForeignKeyConstraint(
            ["origin_run_id", "workspace_id"],
            ["agent_runs.id", "agent_runs.workspace_id"],
            name="fk_evidence_origin_run_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["origin_step_id", "origin_run_id", "workspace_id"],
            ["agent_steps.id", "agent_steps.run_id", "agent_steps.workspace_id"],
            name="fk_evidence_origin_step_run_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["origin_tool_call_id", "origin_run_id", "workspace_id"],
            ["tool_calls.id", "tool_calls.run_id", "tool_calls.workspace_id"],
            name="fk_evidence_origin_tool_call_run_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_item_id"],
            ["source_items.id"],
            name="fk_evidence_source_item",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["query_run_id", "workspace_id"],
            ["query_runs.id", "query_runs.workspace_id"],
            name="fk_evidence_query_run_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["document_version_id", "workspace_id"],
            ["document_versions.id", "document_versions.workspace_id"],
            name="fk_evidence_document_version_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["chunk_id", "document_version_id", "workspace_id"],
            [
                "document_chunks.id",
                "document_chunks.document_version_id",
                "document_chunks.workspace_id",
            ],
            name="fk_evidence_chunk_version_workspace",
            ondelete="RESTRICT",
        ),
        CheckConstraint("schema_version = 1", name="schema_version_supported"),
        CheckConstraint(
            "kind IN ('web_snapshot', 'sql_result', 'news', 'policy', 'bidding', 'stock', "
            "'filing', 'calculation')",
            name="kind_supported",
        ),
        CheckConstraint(
            "locator_type IN ('industry_source_v1', 'sql_result_v1', "
            "'sec_filing_chunk_v1', 'sec_filing_text_v1', 'sec_xbrl_fact_v1', "
            "'financial_calculation_v1')",
            name="locator_type_supported",
        ),
        CheckConstraint(
            "status IN ('active', 'superseded', 'tombstoned', 'unavailable')",
            name="status_supported",
        ),
        CheckConstraint("jsonb_typeof(locator) = 'object'", name="locator_object"),
        CheckConstraint(
            "jsonb_typeof(authorization_snapshot) = 'object'", name="authorization_snapshot_object"
        ),
        CheckConstraint("content_sha256 ~ '^[a-f0-9]{64}$'", name="content_hash_lowercase_hex"),
        CheckConstraint("deduplication_key ~ '^[a-f0-9]{64}$'", name="dedupe_hash_lowercase_hex"),
        CheckConstraint("length(btrim(title)) > 0", name="title_not_blank"),
        CheckConstraint("length(btrim(license_or_terms)) > 0", name="terms_not_blank"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint(
            "(locator_type = 'industry_source_v1' AND source_item_id IS NOT NULL "
            "AND query_run_id IS NULL AND document_version_id IS NULL AND chunk_id IS NULL) OR "
            "(locator_type = 'sql_result_v1' AND query_run_id IS NOT NULL "
            "AND source_item_id IS NULL AND document_version_id IS NULL AND chunk_id IS NULL) OR "
            "(locator_type = 'sec_filing_chunk_v1' AND source_item_id IS NULL "
            "AND query_run_id IS NULL AND document_version_id IS NOT NULL "
            "AND chunk_id IS NOT NULL) OR "
            "(locator_type = 'sec_filing_text_v1' AND source_item_id IS NULL "
            "AND query_run_id IS NULL AND document_version_id IS NOT NULL "
            "AND chunk_id IS NOT NULL) OR "
            "(locator_type = 'sec_xbrl_fact_v1' AND source_item_id IS NULL "
            "AND query_run_id IS NULL AND document_version_id IS NULL "
            "AND chunk_id IS NULL) OR "
            "(locator_type = 'financial_calculation_v1' AND source_item_id IS NULL "
            "AND query_run_id IS NULL AND document_version_id IS NULL AND chunk_id IS NULL)",
            name="source_reference_matches_locator",
        ),
        CheckConstraint(
            "(status = 'active' AND excerpt IS NOT NULL AND invalidated_at IS NULL "
            "AND invalidation_reason IS NULL) OR (status <> 'active' AND excerpt IS NULL "
            "AND invalidated_at IS NOT NULL AND invalidation_reason IS NOT NULL)",
            name="lifecycle_consistent",
        ),
        Index(None, "workspace_id", "status", "created_at"),
        Index(None, "workspace_id", "origin_run_id", "origin_tool_call_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    schema_version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("1")
    )
    kind: Mapped[EvidenceKind] = mapped_column(
        SqlEnum(
            EvidenceKind,
            name="evidence_kind",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=24,
        ),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(1_000), nullable=False)
    canonical_url: Mapped[str | None] = mapped_column(String(2_048), nullable=True)
    locator_type: Mapped[EvidenceLocatorType] = mapped_column(
        SqlEnum(
            EvidenceLocatorType,
            name="evidence_locator_type",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=32,
        ),
        nullable=False,
    )
    locator: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    license_or_terms: Mapped[str] = mapped_column(String(1_000), nullable=False)
    status: Mapped[EvidenceStatus] = mapped_column(
        SqlEnum(
            EvidenceStatus,
            name="evidence_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
        default=EvidenceStatus.ACTIVE,
        server_default=EvidenceStatus.ACTIVE.value,
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invalidation_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    origin_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    origin_step_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    origin_tool_call_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    origin_observation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    origin_source_ordinal: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    normalizer_version: Mapped[str] = mapped_column(String(128), nullable=False)
    authorization_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    source_resource_version: Mapped[str] = mapped_column(String(128), nullable=False)
    source_item_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    query_run_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    document_version_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    chunk_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    deduplication_key: Mapped[str] = mapped_column(String(64), nullable=False)


class EvidenceNormalizationDecisionRecord(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "evidence_normalization_decisions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "tool_call_id", "observation_id", "source_ordinal", "normalizer_version"
        ),
        ForeignKeyConstraint(
            ["tool_call_id", "run_id", "workspace_id"],
            ["tool_calls.id", "tool_calls.run_id", "tool_calls.workspace_id"],
            name="fk_evidence_decisions_tool_call_run_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["evidence_id", "workspace_id"],
            ["evidence.id", "evidence.workspace_id"],
            name="fk_evidence_decisions_evidence_workspace",
            ondelete="RESTRICT",
        ),
        CheckConstraint("source_ordinal BETWEEN 1 AND 16", name="source_ordinal_bounded"),
        CheckConstraint("decision IN ('accepted', 'rejected')", name="decision_supported"),
        CheckConstraint(
            "(decision = 'accepted' AND reason = 'accepted' AND evidence_id IS NOT NULL) OR "
            "(decision = 'rejected' AND reason <> 'accepted' AND evidence_id IS NULL)",
            name="decision_result_consistent",
        ),
        Index(None, "workspace_id", "observation_id", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    tool_call_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    observation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_ordinal: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    normalizer_version: Mapped[str] = mapped_column(String(128), nullable=False)
    decision: Mapped[EvidenceDecision] = mapped_column(
        SqlEnum(
            EvidenceDecision,
            name="evidence_decision",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    reason: Mapped[EvidenceDecisionReason] = mapped_column(
        SqlEnum(
            EvidenceDecisionReason,
            name="evidence_decision_reason",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=40,
        ),
        nullable=False,
    )
    evidence_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResearchClaimRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "research_claims"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id"),
        ForeignKeyConstraint(
            ["research_run_id", "workspace_id"],
            ["research_runs.id", "research_runs.workspace_id"],
            name="fk_research_claims_research_run_workspace",
            ondelete="RESTRICT",
        ),
        CheckConstraint("length(btrim(statement)) > 0", name="statement_not_blank"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="confidence_bounded"),
        CheckConstraint("coverage BETWEEN 0 AND 1", name="coverage_bounded"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint(
            "verification_status IN ('supported', 'refuted', 'uncertain', 'conflicted')",
            name="verification_status_supported",
        ),
        CheckConstraint(
            "(verification_status = 'conflicted') = conflict", name="conflict_status_consistent"
        ),
        Index(None, "workspace_id", "research_run_id", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    research_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    verification_status: Mapped[ClaimVerificationStatus] = mapped_column(
        SqlEnum(
            ClaimVerificationStatus,
            name="claim_verification_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    coverage: Mapped[float] = mapped_column(Float, nullable=False)
    conflict: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class ClaimEvidenceRecord(Base):
    __tablename__ = "claim_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["claim_id", "workspace_id"],
            ["research_claims.id", "research_claims.workspace_id"],
            name="fk_claim_evidence_claim_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["evidence_id", "workspace_id"],
            ["evidence.id", "evidence.workspace_id"],
            name="fk_claim_evidence_evidence_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["origin_step_id", "origin_run_id", "workspace_id"],
            ["agent_steps.id", "agent_steps.run_id", "agent_steps.workspace_id"],
            name="fk_claim_evidence_origin_step_run_workspace",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("claim_id", "ordinal"),
        CheckConstraint(
            "relation IN ('supports', 'refutes', 'context')", name="relation_supported"
        ),
        CheckConstraint("relation_version >= 1", name="relation_version_positive"),
        CheckConstraint("status IN ('active', 'invalidated')", name="status_supported"),
        CheckConstraint("ordinal BETWEEN 1 AND 32", name="ordinal_bounded"),
    )

    claim_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    evidence_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    relation: Mapped[ClaimEvidenceRelation] = mapped_column(
        SqlEnum(
            ClaimEvidenceRelation,
            name="claim_evidence_relation",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    relation_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    status: Mapped[RelationStatus] = mapped_column(
        SqlEnum(
            RelationStatus,
            name="claim_evidence_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
        server_default=RelationStatus.ACTIVE.value,
    )
    ordinal: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    origin_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    origin_step_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GraphNodeRecord(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "graph_nodes"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id", "research_run_id"),
        UniqueConstraint("workspace_id", "research_run_id", "node_type", "resource_id"),
        ForeignKeyConstraint(
            ["research_run_id", "workspace_id"],
            ["research_runs.id", "research_runs.workspace_id"],
            name="fk_graph_nodes_research_run_workspace",
            ondelete="RESTRICT",
        ),
        CheckConstraint("node_type IN ('claim', 'evidence', 'entity')", name="node_type_supported"),
        CheckConstraint("length(btrim(label)) > 0", name="label_not_blank"),
        CheckConstraint("status IN ('active', 'invalidated')", name="status_supported"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    research_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    node_type: Mapped[GraphNodeType] = mapped_column(
        SqlEnum(
            GraphNodeType,
            name="graph_node_type",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    resource_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    label: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[RelationStatus] = mapped_column(
        SqlEnum(
            RelationStatus,
            name="graph_node_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
        server_default=RelationStatus.ACTIVE.value,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GraphEdgeRecord(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "graph_edges"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "research_run_id", "source_node_id", "target_node_id", "relation"
        ),
        ForeignKeyConstraint(
            ["research_run_id", "workspace_id"],
            ["research_runs.id", "research_runs.workspace_id"],
            name="fk_graph_edges_research_run_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_node_id", "workspace_id", "research_run_id"],
            ["graph_nodes.id", "graph_nodes.workspace_id", "graph_nodes.research_run_id"],
            name="fk_graph_edges_source_node",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["target_node_id", "workspace_id", "research_run_id"],
            ["graph_nodes.id", "graph_nodes.workspace_id", "graph_nodes.research_run_id"],
            name="fk_graph_edges_target_node",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "relation IN ('supports', 'refutes', 'context')", name="relation_supported"
        ),
        CheckConstraint("status IN ('active', 'invalidated')", name="status_supported"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    research_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_node_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    target_node_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    relation: Mapped[ClaimEvidenceRelation] = mapped_column(
        SqlEnum(
            ClaimEvidenceRelation,
            name="graph_edge_relation",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    status: Mapped[RelationStatus] = mapped_column(
        SqlEnum(
            RelationStatus,
            name="graph_edge_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
        server_default=RelationStatus.ACTIVE.value,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
