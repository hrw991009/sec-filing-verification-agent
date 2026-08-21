"""PostgreSQL records for Memory candidates, projections, and revisions."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
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
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from industry_platform.core.database import Base, TimestampMixin, UUIDPrimaryKeyMixin
from industry_platform.modules.identity.models import enum_values
from industry_platform.modules.memory.domain import (
    MemoryCandidateStatus,
    MemoryKind,
    MemoryPolicyDecision,
    MemoryPolicyReason,
    MemoryRevisionValidity,
    MemoryScope,
    MemoryStatus,
    MemoryWriteAction,
)


class ThreadMemoryStateRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Current short-term summary projection for one user and Conversation."""

    __tablename__ = "thread_memory_states"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id"),
        UniqueConstraint("workspace_id", "conversation_id", "owner_user_id"),
        ForeignKeyConstraint(
            ["conversation_id", "workspace_id"],
            ["conversations.id", "conversations.workspace_id"],
            name="fk_thread_memory_states_conversation_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "owner_user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            name="fk_thread_memory_states_workspace_owner",
            ondelete="RESTRICT",
        ),
        CheckConstraint("schema_version = 1", name="schema_version_supported"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("compaction_revision >= 1", name="compaction_revision_positive"),
        CheckConstraint("cardinality(source_message_ids) BETWEEN 1 AND 8", name="sources_bounded"),
        CheckConstraint("length(btrim(summary)) > 0", name="summary_not_blank"),
        CheckConstraint("octet_length(summary) <= 16000", name="summary_bytes_bounded"),
        Index(None, "workspace_id", "owner_user_id", "updated_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    conversation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    owner_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_message_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(Uuid(as_uuid=True)), nullable=False
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    compaction_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    freshness_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    schema_version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default=text("1")
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )


class MemoryRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Current projection of one user-confirmed long-term Memory."""

    __tablename__ = "memories"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id"),
        UniqueConstraint("id", "workspace_id", "owner_user_id"),
        ForeignKeyConstraint(
            ["workspace_id", "owner_user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            name="fk_memories_workspace_owner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_conversation_id", "workspace_id"],
            ["conversations.id", "conversations.workspace_id"],
            name="fk_memories_source_conversation_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["current_revision_id", "id", "workspace_id"],
            ["memory_revisions.id", "memory_revisions.memory_id", "memory_revisions.workspace_id"],
            name="fk_memories_current_revision_memory_workspace",
            ondelete="RESTRICT",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("scope IN ('user', 'workspace')", name="scope_supported"),
        CheckConstraint(
            "kind IN ('preference', 'fact', 'instruction', 'note')",
            name="kind_supported",
        ),
        CheckConstraint(
            "status IN ('confirmed', 'disabled', 'expired', 'deleted')",
            name="status_supported",
        ),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="confidence_bounded"),
        CheckConstraint("current_version >= 1", name="current_version_positive"),
        CheckConstraint(
            "(status = 'deleted' AND deleted_at IS NOT NULL) OR "
            "(status <> 'deleted' AND deleted_at IS NULL)",
            name="deletion_state_consistent",
        ),
        Index(None, "workspace_id", "owner_user_id", "status", "updated_at"),
        Index(None, "workspace_id", "scope", "status", "updated_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    owner_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_conversation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    scope: Mapped[MemoryScope] = mapped_column(
        SqlEnum(
            MemoryScope,
            name="memory_scope",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    kind: Mapped[MemoryKind] = mapped_column(
        SqlEnum(
            MemoryKind,
            name="memory_kind",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[MemoryStatus] = mapped_column(
        SqlEnum(
            MemoryStatus,
            name="memory_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
        default=MemoryStatus.CONFIRMED,
        server_default=MemoryStatus.CONFIRMED.value,
    )
    current_revision_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    current_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MemoryRevisionRecord(UUIDPrimaryKeyMixin, Base):
    """Append-only content and provenance revision for one long-term Memory."""

    __tablename__ = "memory_revisions"
    __table_args__ = (
        UniqueConstraint("id", "memory_id", "workspace_id"),
        UniqueConstraint("memory_id", "version"),
        ForeignKeyConstraint(
            ["memory_id", "workspace_id", "owner_user_id"],
            ["memories.id", "memories.workspace_id", "memories.owner_user_id"],
            name="fk_memory_revisions_memory_workspace_owner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "editor_user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            name="fk_memory_revisions_workspace_editor",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("scope IN ('user', 'workspace')", name="scope_supported"),
        CheckConstraint(
            "kind IN ('preference', 'fact', 'instruction', 'note')",
            name="kind_supported",
        ),
        CheckConstraint("write_action IN ('create', 'update', 'merge')", name="action_supported"),
        CheckConstraint(
            "policy_decision IN ('allowed', 'requires_edit', 'rejected')",
            name="policy_decision_supported",
        ),
        CheckConstraint("validity IN ('valid', 'withdrawn')", name="validity_supported"),
        CheckConstraint("length(btrim(content)) > 0", name="content_not_blank"),
        CheckConstraint("octet_length(content) <= 16000", name="content_bytes_bounded"),
        CheckConstraint("length(btrim(write_reason)) > 0", name="write_reason_not_blank"),
        CheckConstraint("schema_version = 1", name="schema_version_supported"),
        Index(None, "workspace_id", "memory_id", "version"),
    )

    memory_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    owner_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[MemoryScope] = mapped_column(
        SqlEnum(
            MemoryScope,
            name="memory_revision_scope",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    kind: Mapped[MemoryKind] = mapped_column(
        SqlEnum(
            MemoryKind,
            name="memory_revision_kind",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    write_action: Mapped[MemoryWriteAction] = mapped_column(
        SqlEnum(
            MemoryWriteAction,
            name="memory_write_action",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    write_reason: Mapped[str] = mapped_column(String(200), nullable=False)
    policy_decision: Mapped[MemoryPolicyDecision] = mapped_column(
        SqlEnum(
            MemoryPolicyDecision,
            name="memory_revision_policy_decision",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=24,
        ),
        nullable=False,
    )
    editor_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    validity: Mapped[MemoryRevisionValidity] = mapped_column(
        SqlEnum(
            MemoryRevisionValidity,
            name="memory_revision_validity",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
        default=MemoryRevisionValidity.VALID,
        server_default=MemoryRevisionValidity.VALID.value,
    )
    schema_version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MemoryCandidateRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Persisted proposal that cannot become Memory without a user decision."""

    __tablename__ = "memory_candidates"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id", "owner_user_id"),
        UniqueConstraint(
            "workspace_id",
            "owner_user_id",
            "idempotency_key_hash",
            name="uq_memory_candidates_workspace_owner_idempotency",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "owner_user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            name="fk_memory_candidates_workspace_owner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["conversation_id", "workspace_id"],
            ["conversations.id", "conversations.workspace_id"],
            name="fk_memory_candidates_conversation_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["resolved_memory_id", "workspace_id", "owner_user_id"],
            ["memories.id", "memories.workspace_id", "memories.owner_user_id"],
            name="fk_memory_candidates_resolved_memory_workspace_owner",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("scope IN ('user', 'workspace')", name="scope_supported"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="confidence_bounded"),
        CheckConstraint(
            "policy_decision IN ('allowed', 'requires_edit', 'rejected')",
            name="policy_decision_supported",
        ),
        CheckConstraint(
            "policy_reason IN ('user_authored', 'mixed_sources', "
            "'assistant_only_requires_edit', 'sensitive_content')",
            name="policy_reason_supported",
        ),
        CheckConstraint(
            "status IN ('candidate', 'confirmed', 'rejected')", name="status_supported"
        ),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("octet_length(idempotency_key_hash) = 32", name="idempotency_hash_length"),
        CheckConstraint(
            "request_fingerprint ~ '^[a-f0-9]{64}$'",
            name="request_fingerprint_lowercase_hex",
        ),
        CheckConstraint(
            "resolution_fingerprint IS NULL OR resolution_fingerprint ~ '^[a-f0-9]{64}$'",
            name="resolution_fingerprint_lowercase_hex",
        ),
        CheckConstraint(
            "suggested_content IS NULL OR (length(btrim(suggested_content)) > 0 "
            "AND octet_length(suggested_content) <= 16000)",
            name="suggested_content_bounded",
        ),
        CheckConstraint(
            "(status = 'candidate' AND suggested_content IS NOT NULL "
            "AND resolved_memory_id IS NULL AND resolution_action IS NULL "
            "AND resolution_fingerprint IS NULL AND resolved_at IS NULL) OR "
            "(status = 'confirmed' AND resolved_memory_id IS NOT NULL "
            "AND resolution_action IS NOT NULL AND resolution_fingerprint IS NOT NULL "
            "AND resolved_at IS NOT NULL) OR "
            "(status = 'rejected' AND resolved_memory_id IS NULL "
            "AND resolution_action IS NULL AND resolved_at IS NOT NULL)",
            name="lifecycle_consistent",
        ),
        Index(None, "workspace_id", "owner_user_id", "status", "created_at"),
        Index(None, "workspace_id", "conversation_id", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    owner_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    conversation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    suggested_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scope: Mapped[MemoryScope] = mapped_column(
        SqlEnum(
            MemoryScope,
            name="memory_candidate_scope",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    write_reason: Mapped[str] = mapped_column(String(200), nullable=False)
    policy_decision: Mapped[MemoryPolicyDecision] = mapped_column(
        SqlEnum(
            MemoryPolicyDecision,
            name="memory_candidate_policy_decision",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=24,
        ),
        nullable=False,
    )
    policy_reason: Mapped[MemoryPolicyReason] = mapped_column(
        SqlEnum(
            MemoryPolicyReason,
            name="memory_candidate_policy_reason",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=40,
        ),
        nullable=False,
    )
    status: Mapped[MemoryCandidateStatus] = mapped_column(
        SqlEnum(
            MemoryCandidateStatus,
            name="memory_candidate_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    idempotency_key_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    resolution_action: Mapped[MemoryWriteAction | None] = mapped_column(
        SqlEnum(
            MemoryWriteAction,
            name="memory_candidate_resolution_action",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=True,
    )
    resolution_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolved_memory_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MemoryCandidateSourceRecord(Base):
    """Ordered, tenant-bound source messages for one Memory candidate."""

    __tablename__ = "memory_candidate_sources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["candidate_id", "workspace_id", "owner_user_id"],
            [
                "memory_candidates.id",
                "memory_candidates.workspace_id",
                "memory_candidates.owner_user_id",
            ],
            name="fk_memory_candidate_sources_candidate_owner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["message_id", "workspace_id"],
            ["conversation_messages.id", "conversation_messages.workspace_id"],
            name="fk_memory_candidate_sources_message_workspace",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("candidate_id", "ordinal"),
        CheckConstraint("ordinal BETWEEN 0 AND 7", name="ordinal_bounded"),
    )

    candidate_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    message_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    owner_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    ordinal: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MemoryRevisionSourceRecord(Base):
    """Ordered source lineage for one immutable Memory revision."""

    __tablename__ = "memory_revision_sources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["revision_id", "memory_id", "workspace_id"],
            ["memory_revisions.id", "memory_revisions.memory_id", "memory_revisions.workspace_id"],
            name="fk_memory_revision_sources_revision_memory_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["message_id", "workspace_id"],
            ["conversation_messages.id", "conversation_messages.workspace_id"],
            name="fk_memory_revision_sources_message_workspace",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("revision_id", "ordinal"),
        CheckConstraint("ordinal BETWEEN 0 AND 7", name="ordinal_bounded"),
    )

    revision_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    message_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    memory_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    ordinal: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
