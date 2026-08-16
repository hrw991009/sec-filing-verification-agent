"""PostgreSQL source of truth for private chat attachments."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column

from industry_platform.core.database import Base, TimestampMixin, UUIDPrimaryKeyMixin
from industry_platform.modules.files.domain import (
    AttachmentKind,
    AttachmentMediaType,
    FileObjectStatus,
)
from industry_platform.modules.identity.models import enum_values


class FileObject(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One workspace-owned upload intent and its verified private representation."""

    __tablename__ = "file_objects"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id"),
        UniqueConstraint("bucket", "staging_object_key"),
        CheckConstraint(
            "status IN ('staging', 'processing', 'ready', 'rejected', 'failed', "
            "'deleting', 'deleted')",
            name="status",
        ),
        CheckConstraint("expected_size > 0", name="expected_size_positive"),
        CheckConstraint("actual_size IS NULL OR actual_size > 0", name="actual_size_positive"),
        CheckConstraint("safe_size IS NULL OR safe_size > 0", name="safe_size_positive"),
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
        CheckConstraint(
            "(status = 'staging' AND actual_size IS NULL AND object_key IS NULL "
            "AND ready_at IS NULL AND error_code IS NULL) OR status <> 'staging'",
            name="staging_state_consistent",
        ),
        CheckConstraint(
            "(status = 'ready' AND object_key IS NOT NULL AND detected_media_type IS NOT NULL "
            "AND kind IS NOT NULL AND actual_size IS NOT NULL AND safe_size IS NOT NULL "
            "AND source_sha256 IS NOT NULL "
            "AND safe_sha256 IS NOT NULL AND parser_version IS NOT NULL "
            "AND sanitizer_version IS NOT NULL AND ready_at IS NOT NULL "
            "AND error_code IS NULL) OR status <> 'ready'",
            name="ready_state_consistent",
        ),
        CheckConstraint(
            "(kind = 'text' AND extracted_text IS NOT NULL AND width IS NULL "
            "AND height IS NULL) OR (kind = 'image' AND extracted_text IS NULL "
            "AND width IS NOT NULL AND height IS NOT NULL) OR kind IS NULL",
            name="kind_payload_consistent",
        ),
        CheckConstraint(
            "(status IN ('rejected', 'failed') AND error_code IS NOT NULL) OR "
            "status NOT IN ('rejected', 'failed')",
            name="failure_has_error_code",
        ),
        CheckConstraint(
            "(status = 'deleted' AND deleted_at IS NOT NULL) OR status <> 'deleted'",
            name="deleted_state_consistent",
        ),
        Index(
            "uq_file_objects_final_object_key",
            "bucket",
            "object_key",
            unique=True,
            postgresql_where=text("object_key IS NOT NULL"),
        ),
        Index(None, "workspace_id", "status", "updated_at", "id"),
        Index(None, "upload_expires_at", postgresql_where=text("status = 'staging'")),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    declared_media_type: Mapped[str] = mapped_column(String(64), nullable=False)
    detected_media_type: Mapped[AttachmentMediaType | None] = mapped_column(
        SqlEnum(
            AttachmentMediaType,
            name="attachment_media_type",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=32,
        ),
        nullable=True,
    )
    kind: Mapped[AttachmentKind | None] = mapped_column(
        SqlEnum(
            AttachmentKind,
            name="attachment_kind",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=True,
    )
    bucket: Mapped[str] = mapped_column(String(63), nullable=False)
    staging_object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    expected_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actual_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    safe_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    expected_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    safe_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_etag: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[FileObjectStatus] = mapped_column(
        SqlEnum(
            FileObjectStatus,
            name="file_object_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
        default=FileObjectStatus.STAGING,
        server_default=FileObjectStatus.STAGING.value,
    )
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sanitizer_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    upload_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delete_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
