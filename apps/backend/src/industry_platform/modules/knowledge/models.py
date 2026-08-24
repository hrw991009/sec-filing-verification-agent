"""PostgreSQL source-of-truth records for Knowledge acceptance."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
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
    func,
    text,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column

from industry_platform.core.database import Base, TimestampMixin, UUIDPrimaryKeyMixin
from industry_platform.modules.identity.models import enum_values
from industry_platform.modules.knowledge.domain import (
    KNOWLEDGE_SCHEMA_VERSION,
    DocumentStatus,
    DocumentVersionStatus,
    KnowledgeBaseStatus,
)


class KnowledgeBaseRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "created_by_user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            name="fk_knowledge_bases_workspace_creator",
            ondelete="RESTRICT",
        ),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        CheckConstraint("status IN ('active', 'deleted')", name="status_supported"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint(
            "(status = 'deleted' AND deleted_at IS NOT NULL) OR "
            "(status <> 'deleted' AND deleted_at IS NULL)",
            name="deletion_state_consistent",
        ),
        Index(None, "workspace_id", "status", "updated_at", "id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[KnowledgeBaseStatus] = mapped_column(
        SqlEnum(
            KnowledgeBaseStatus,
            name="knowledge_base_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
        default=KnowledgeBaseStatus.ACTIVE,
        server_default=KnowledgeBaseStatus.ACTIVE.value,
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DocumentRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id"),
        UniqueConstraint("id", "knowledge_base_id", "workspace_id"),
        ForeignKeyConstraint(
            ["knowledge_base_id", "workspace_id"],
            ["knowledge_bases.id", "knowledge_bases.workspace_id"],
            name="fk_documents_knowledge_base_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "created_by_user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            name="fk_documents_workspace_creator",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["active_version_id", "id", "workspace_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.workspace_id",
            ],
            name="fk_documents_active_version_document_workspace",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        CheckConstraint("length(btrim(title)) > 0", name="title_not_blank"),
        CheckConstraint("status IN ('active', 'deleted')", name="status_supported"),
        CheckConstraint("latest_version_number >= 1", name="latest_version_positive"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint(
            "(status = 'deleted' AND deleted_at IS NOT NULL) OR "
            "(status <> 'deleted' AND deleted_at IS NULL)",
            name="deletion_state_consistent",
        ),
        Index(None, "workspace_id", "knowledge_base_id", "status", "updated_at", "id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    knowledge_base_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        SqlEnum(
            DocumentStatus,
            name="document_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
        default=DocumentStatus.ACTIVE,
        server_default=DocumentStatus.ACTIVE.value,
    )
    active_version_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    latest_version_number: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DocumentVersionRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id"),
        UniqueConstraint("id", "document_id", "workspace_id"),
        UniqueConstraint("document_id", "version"),
        UniqueConstraint("file_object_id"),
        UniqueConstraint("ingestion_job_id"),
        ForeignKeyConstraint(
            ["document_id", "knowledge_base_id", "workspace_id"],
            ["documents.id", "documents.knowledge_base_id", "documents.workspace_id"],
            name="fk_document_versions_document_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["file_object_id", "workspace_id"],
            ["file_objects.id", "file_objects.workspace_id"],
            name="fk_document_versions_file_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["ingestion_job_id", "workspace_id"],
            ["jobs.id", "jobs.workspace_id"],
            name="fk_document_versions_job_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "created_by_user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            name="fk_document_versions_workspace_creator",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint(
            f"ingestion_schema_version = {KNOWLEDGE_SCHEMA_VERSION}",
            name="ingestion_schema_version_supported",
        ),
        CheckConstraint(
            "status IN ('queued', 'validating', 'parsing', 'extracting_assets', 'chunking', "
            "'embedding', 'vector_indexing', 'lexical_indexing', 'retrying', 'ready', "
            "'failed', 'cancelled', 'deleting', 'deleted')",
            name="status_supported",
        ),
        CheckConstraint("octet_length(idempotency_key_hash) = 32", name="idempotency_hash_length"),
        CheckConstraint("octet_length(request_fingerprint) = 32", name="request_hash_length"),
        CheckConstraint("uploaded_at <= queued_at", name="upload_queue_order"),
        Index(
            "uq_document_versions_workspace_idempotency",
            "workspace_id",
            "idempotency_key_hash",
            unique=True,
        ),
        Index(None, "workspace_id", "knowledge_base_id", "status", "updated_at", "id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    knowledge_base_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    file_object_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    ingestion_job_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[DocumentVersionStatus] = mapped_column(
        SqlEnum(
            DocumentVersionStatus,
            name="document_version_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=24,
        ),
        nullable=False,
        default=DocumentVersionStatus.QUEUED,
        server_default=DocumentVersionStatus.QUEUED.value,
    )
    ingestion_schema_version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=KNOWLEDGE_SCHEMA_VERSION, server_default=text("1")
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    idempotency_key_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    request_fingerprint: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
