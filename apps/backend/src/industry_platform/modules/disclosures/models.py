"""SQLAlchemy models for the global canonical SEC filer catalog."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from industry_platform.core.database import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SecFilerRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Current canonical projection for one stable SEC CIK."""

    __tablename__ = "sec_filers"
    __table_args__ = (
        UniqueConstraint("cik"),
        CheckConstraint("cik ~ '^[0-9]{10}$' AND cik <> '0000000000'", name="cik_valid"),
        CheckConstraint("length(btrim(canonical_name)) > 0", name="canonical_name_not_blank"),
        CheckConstraint("length(btrim(normalized_name)) > 0", name="normalized_name_not_blank"),
        CheckConstraint(
            "source_kind = 'company_tickers'",
            name="source_kind_supported",
        ),
        CheckConstraint(
            "source_url = 'https://www.sec.gov/files/company_tickers.json'",
            name="source_url_allowlisted",
        ),
        CheckConstraint(
            "octet_length(source_content_sha256) = 32",
            name="source_content_sha256_length",
        ),
        Index(None, "normalized_name", "cik"),
    )

    cik: Mapped[str] = mapped_column(String(10), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(500), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(500), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_version: Mapped[str] = mapped_column(String(128), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_content_sha256: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    source_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SecFilerAliasRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Versioned name or ticker observation belonging to one canonical filer."""

    __tablename__ = "sec_filer_aliases"
    __table_args__ = (
        UniqueConstraint(
            "filer_id",
            "kind",
            "normalized_value",
            "source_version",
        ),
        CheckConstraint("kind IN ('name', 'ticker')", name="kind_supported"),
        CheckConstraint("length(btrim(display_value)) > 0", name="display_value_not_blank"),
        CheckConstraint("length(btrim(normalized_value)) > 0", name="normalized_value_not_blank"),
        CheckConstraint(
            "source_kind = 'company_tickers'",
            name="source_kind_supported",
        ),
        CheckConstraint(
            "source_url = 'https://www.sec.gov/files/company_tickers.json'",
            name="source_url_allowlisted",
        ),
        CheckConstraint(
            "octet_length(source_content_sha256) = 32",
            name="source_content_sha256_length",
        ),
        CheckConstraint(
            "valid_from IS NULL OR valid_to IS NULL OR valid_to > valid_from",
            name="validity_order",
        ),
        Index(None, "kind", "normalized_value", "valid_to"),
        Index(None, "filer_id", "valid_to"),
    )

    filer_id: Mapped[UUID] = mapped_column(
        ForeignKey("sec_filers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    display_value: Mapped[str] = mapped_column(String(500), nullable=False)
    normalized_value: Mapped[str] = mapped_column(String(500), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_version: Mapped[str] = mapped_column(String(128), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_content_sha256: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SecCatalogSyncRecord(UUIDPrimaryKeyMixin, Base):
    """One immutable successful canonical catalog version marker."""

    __tablename__ = "sec_catalog_syncs"
    __table_args__ = (
        UniqueConstraint("source_kind", "source_version"),
        CheckConstraint(
            "source_kind = 'company_tickers'",
            name="source_kind_supported",
        ),
        CheckConstraint(
            "source_url = 'https://www.sec.gov/files/company_tickers.json'",
            name="source_url_allowlisted",
        ),
        CheckConstraint(
            "octet_length(content_sha256) = 32",
            name="content_sha256_length",
        ),
        CheckConstraint("filer_count > 0", name="filer_count_positive"),
    )

    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_version: Mapped[str] = mapped_column(String(128), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    content_sha256: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    filer_count: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    committed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
