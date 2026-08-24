"""PostgreSQL source-of-truth records for Knowledge acceptance."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
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
    func,
    text,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from industry_platform.core.database import Base, TimestampMixin, UUIDPrimaryKeyMixin
from industry_platform.modules.identity.models import enum_values
from industry_platform.modules.knowledge.domain import (
    KNOWLEDGE_SCHEMA_VERSION,
    DocumentAssetKind,
    DocumentPageTextSource,
    DocumentStatus,
    DocumentVersionStatus,
    IngestionCheckpointStage,
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
            "length(btrim(parser_name)) > 0 AND length(btrim(parser_version)) > 0 "
            "AND parser_schema_version = 1 AND jsonb_typeof(parser_config) = 'object'",
            name="parser_contract",
        ),
        CheckConstraint(
            "length(btrim(chunker_name)) > 0 AND length(btrim(chunker_version)) > 0 "
            "AND jsonb_typeof(chunker_config) = 'object'",
            name="chunker_contract",
        ),
        CheckConstraint(
            "status IN ('queued', 'validating', 'parsing', 'extracting_assets', 'chunking', "
            "'parsed', 'embedding', 'vector_indexing', 'lexical_indexing', 'retrying', 'ready', "
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
    parser_name: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="pdfplumber-rapidocr",
        server_default="pdfplumber-rapidocr",
    )
    parser_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="1.0.0", server_default="1.0.0"
    )
    parser_schema_version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default=text("1")
    )
    parser_config: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text(
            "jsonb_build_object("
            "'budget', jsonb_build_object("
            "'max_input_bytes', 26214400, "
            "'max_output_bytes', 67108864, "
            "'max_page_image_pixels', 24000000, "
            "'max_pages', 250, "
            "'max_text_characters', 5000000, "
            "'timeout_seconds', 1200), "
            "'ocr_render_dpi', 144, "
            "'schema_version', 1)"
        ),
    )
    chunker_name: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="bounded-page-chunker",
        server_default="bounded-page-chunker",
    )
    chunker_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="1.0.0", server_default="1.0.0"
    )
    chunker_config: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text(
            "jsonb_build_object('max_characters', 1200, 'overlap_characters', 120)"
        ),
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


class IngestionCheckpointRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ingestion_checkpoints"
    __table_args__ = (
        UniqueConstraint("document_version_id", "stage"),
        ForeignKeyConstraint(
            ["document_version_id", "document_id", "workspace_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.workspace_id",
            ],
            name="fk_ingestion_checkpoints_version_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["ingestion_job_id", "workspace_id"],
            ["jobs.id", "jobs.workspace_id"],
            name="fk_ingestion_checkpoints_job_workspace",
            ondelete="RESTRICT",
        ),
        CheckConstraint("stage_sequence BETWEEN 1 AND 4", name="stage_sequence_supported"),
        CheckConstraint(
            "(stage = 'validating' AND stage_sequence = 1) OR "
            "(stage = 'parsing' AND stage_sequence = 2) OR "
            "(stage = 'extracting_assets' AND stage_sequence = 3) OR "
            "(stage = 'chunking' AND stage_sequence = 4)",
            name="stage_sequence_consistent",
        ),
        CheckConstraint("fencing_token >= 1", name="fencing_token_positive"),
        CheckConstraint("attempt_count >= 1", name="attempt_count_positive"),
        CheckConstraint(
            "octet_length(stage_idempotency_hash) = 32", name="idempotency_hash_length"
        ),
        CheckConstraint("octet_length(input_hash) = 32", name="input_hash_length"),
        CheckConstraint("octet_length(output_hash) = 32", name="output_hash_length"),
        CheckConstraint("jsonb_typeof(stats) = 'object'", name="stats_object"),
        Index(None, "workspace_id", "document_version_id", "stage_sequence"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    document_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    ingestion_job_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    stage: Mapped[IngestionCheckpointStage] = mapped_column(
        SqlEnum(
            IngestionCheckpointStage,
            name="ingestion_checkpoint_stage",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=24,
        ),
        nullable=False,
    )
    stage_sequence: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    attempt_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    stage_idempotency_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    input_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    output_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    output_bucket: Mapped[str | None] = mapped_column(String(255), nullable=True)
    output_object_key: Mapped[str | None] = mapped_column(String(1_024), nullable=True)
    stats: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DocumentPageRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "document_pages"
    __table_args__ = (
        UniqueConstraint("id", "document_version_id", "workspace_id"),
        UniqueConstraint("document_version_id", "page_number"),
        ForeignKeyConstraint(
            ["document_version_id", "document_id", "workspace_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.workspace_id",
            ],
            name="fk_document_pages_version_workspace",
            ondelete="CASCADE",
        ),
        CheckConstraint("page_number >= 1", name="page_number_positive"),
        CheckConstraint("width_points > 0 AND height_points > 0", name="geometry_positive"),
        CheckConstraint("length(btrim(text_content)) > 0", name="text_not_blank"),
        CheckConstraint("octet_length(content_hash) = 32", name="content_hash_length"),
        CheckConstraint(
            "jsonb_typeof(bbox) = 'array' AND jsonb_array_length(bbox) = 4", name="bbox_shape"
        ),
        CheckConstraint("jsonb_typeof(title_path) = 'array'", name="title_path_array"),
        Index(None, "workspace_id", "document_version_id", "page_number"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    document_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    width_points: Mapped[float] = mapped_column(Float, nullable=False)
    height_points: Mapped[float] = mapped_column(Float, nullable=False)
    text_content: Mapped[str] = mapped_column(Text, nullable=False)
    text_source: Mapped[DocumentPageTextSource] = mapped_column(
        SqlEnum(
            DocumentPageTextSource,
            name="document_page_text_source",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    bbox: Mapped[list[float]] = mapped_column(JSONB, nullable=False)
    title_path: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    content_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(32), nullable=False)


class DocumentChunkRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("id", "document_version_id", "workspace_id"),
        UniqueConstraint("document_version_id", "ordinal"),
        ForeignKeyConstraint(
            ["document_version_id", "document_id", "workspace_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.workspace_id",
            ],
            name="fk_document_chunks_version_workspace",
            ondelete="CASCADE",
        ),
        CheckConstraint("ordinal >= 1 AND page_number >= 1", name="locator_positive"),
        CheckConstraint("length(btrim(text_content)) > 0", name="text_not_blank"),
        CheckConstraint("token_count >= 1", name="token_count_positive"),
        CheckConstraint("octet_length(content_hash) = 32", name="content_hash_length"),
        CheckConstraint(
            "jsonb_typeof(bbox) = 'array' AND jsonb_array_length(bbox) = 4", name="bbox_shape"
        ),
        CheckConstraint("jsonb_typeof(title_path) = 'array'", name="title_path_array"),
        Index(None, "workspace_id", "document_version_id", "ordinal"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    document_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    text_content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox: Mapped[list[float]] = mapped_column(JSONB, nullable=False)
    title_path: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    content_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    chunker_version: Mapped[str] = mapped_column(String(32), nullable=False)


class DocumentAssetRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "document_assets"
    __table_args__ = (
        UniqueConstraint("id", "document_version_id", "workspace_id"),
        UniqueConstraint("document_version_id", "ordinal"),
        ForeignKeyConstraint(
            ["document_version_id", "document_id", "workspace_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.workspace_id",
            ],
            name="fk_document_assets_version_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["page_id", "document_version_id", "workspace_id"],
            [
                "document_pages.id",
                "document_pages.document_version_id",
                "document_pages.workspace_id",
            ],
            name="fk_document_assets_page_workspace",
            ondelete="CASCADE",
        ),
        CheckConstraint("ordinal >= 1 AND page_number >= 1", name="locator_positive"),
        CheckConstraint("octet_length(content_hash) = 32", name="content_hash_length"),
        CheckConstraint("octet_length(preview_sha256) = 32", name="preview_hash_length"),
        CheckConstraint("preview_mime_type = 'image/png'", name="preview_mime_type_supported"),
        CheckConstraint("length(btrim(preview_bucket)) > 0", name="preview_bucket_not_blank"),
        CheckConstraint("length(btrim(preview_object_key)) > 0", name="preview_key_not_blank"),
        CheckConstraint(
            "(kind = 'table' AND html_content IS NOT NULL) OR "
            "(kind = 'image' AND html_content IS NULL)",
            name="kind_payload_consistent",
        ),
        CheckConstraint(
            "jsonb_typeof(bbox) = 'array' AND jsonb_array_length(bbox) = 4", name="bbox_shape"
        ),
        CheckConstraint("jsonb_typeof(title_path) = 'array'", name="title_path_array"),
        Index(None, "workspace_id", "document_version_id", "page_number", "ordinal"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    document_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    page_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[DocumentAssetKind] = mapped_column(
        SqlEnum(
            DocumentAssetKind,
            name="document_asset_kind",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    bbox: Mapped[list[float]] = mapped_column(JSONB, nullable=False)
    title_path: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    content_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    preview_sha256: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    preview_mime_type: Mapped[str] = mapped_column(String(64), nullable=False)
    preview_bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    preview_object_key: Mapped[str] = mapped_column(String(1_024), nullable=False)
    html_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    parser_version: Mapped[str] = mapped_column(String(32), nullable=False)


class ChunkAssetLinkRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "chunk_asset_links"
    __table_args__ = (
        UniqueConstraint("chunk_id", "asset_id"),
        ForeignKeyConstraint(
            ["chunk_id", "document_version_id", "workspace_id"],
            [
                "document_chunks.id",
                "document_chunks.document_version_id",
                "document_chunks.workspace_id",
            ],
            name="fk_chunk_asset_links_chunk_workspace",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["asset_id", "document_version_id", "workspace_id"],
            [
                "document_assets.id",
                "document_assets.document_version_id",
                "document_assets.workspace_id",
            ],
            name="fk_chunk_asset_links_asset_workspace",
            ondelete="CASCADE",
        ),
        Index(None, "workspace_id", "document_version_id", "chunk_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    document_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    chunk_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    asset_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
