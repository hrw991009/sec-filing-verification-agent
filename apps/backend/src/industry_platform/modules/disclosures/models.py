"""SQLAlchemy models for canonical SEC filer and filing source facts."""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
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


class SecSubmissionSourceRecord(UUIDPrimaryKeyMixin, Base):
    """One immutable official submissions JSON response stored in MinIO."""

    __tablename__ = "sec_submission_sources"
    __table_args__ = (
        UniqueConstraint("source_url", "source_version"),
        CheckConstraint("cik ~ '^[0-9]{10}$' AND cik <> '0000000000'", name="cik_valid"),
        CheckConstraint(
            "source_kind IN ('submissions_current', 'submissions_supplemental')",
            name="source_kind_supported",
        ),
        CheckConstraint(
            "source_url LIKE 'https://data.sec.gov/submissions/CIK%.json'",
            name="source_url_allowlisted",
        ),
        CheckConstraint("octet_length(content_sha256) = 32", name="content_sha256_length"),
        CheckConstraint("length(btrim(object_bucket)) > 0", name="object_bucket_not_blank"),
        CheckConstraint("length(btrim(object_key)) > 0", name="object_key_not_blank"),
        CheckConstraint(
            "(filing_from IS NULL) = (filing_to IS NULL)",
            name="coverage_paired",
        ),
        CheckConstraint(
            "filing_from IS NULL OR filing_to >= filing_from",
            name="coverage_order",
        ),
        CheckConstraint(
            "source_available_at <= retrieved_at",
            name="availability_order",
        ),
        Index(None, "cik", "source_kind", "source_available_at"),
    )

    cik: Mapped[str] = mapped_column(String(10), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_name: Mapped[str] = mapped_column(String(128), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_version: Mapped[str] = mapped_column(String(128), nullable=False)
    content_sha256: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    object_bucket: Mapped[str] = mapped_column(String(128), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    filing_from: Mapped[date | None] = mapped_column(Date(), nullable=True)
    filing_to: Mapped[date | None] = mapped_column(Date(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SecFilingObservationRecord(UUIDPrimaryKeyMixin, Base):
    """One accession as observed in one immutable submissions response."""

    __tablename__ = "sec_filing_observations"
    __table_args__ = (
        UniqueConstraint("source_id", "accession"),
        CheckConstraint("cik ~ '^[0-9]{10}$' AND cik <> '0000000000'", name="cik_valid"),
        CheckConstraint(
            "accession ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'",
            name="accession_valid",
        ),
        CheckConstraint("form IN ('10-K', '10-K/A', '10-Q', '10-Q/A')", name="form_supported"),
        CheckConstraint("filed_date >= report_date", name="filing_date_order"),
        Index(None, "cik", "report_date", "accepted_at"),
    )

    source_id: Mapped[UUID] = mapped_column(
        ForeignKey("sec_submission_sources.id", ondelete="RESTRICT"), nullable=False
    )
    cik: Mapped[str] = mapped_column(String(10), nullable=False)
    accession: Mapped[str] = mapped_column(String(20), nullable=False)
    form: Mapped[str] = mapped_column(String(16), nullable=False)
    report_date: Mapped[date] = mapped_column(Date(), nullable=False)
    filed_date: Mapped[date] = mapped_column(Date(), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    primary_document: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SecFilingRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Current canonical projection for an accession; observations remain append-only."""

    __tablename__ = "sec_filings"
    __table_args__ = (
        UniqueConstraint("accession"),
        CheckConstraint("cik ~ '^[0-9]{10}$' AND cik <> '0000000000'", name="cik_valid"),
        CheckConstraint(
            "accession ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'",
            name="accession_valid",
        ),
        CheckConstraint("form IN ('10-K', '10-K/A', '10-Q', '10-Q/A')", name="form_supported"),
        CheckConstraint(
            "amendment_relation_status IN ('not_amendment', 'resolved', 'unresolved')",
            name="amendment_status_supported",
        ),
        CheckConstraint(
            "(amendment_relation_status = 'resolved') = (base_accession IS NOT NULL)",
            name="base_accession_consistent",
        ),
        CheckConstraint(
            "visibility_policy_version = 'sec-acceptance-source-v1'",
            name="visibility_policy_supported",
        ),
        CheckConstraint("public_available_at = accepted_at", name="public_availability_policy"),
        Index(None, "cik", "report_date", "accepted_at"),
    )

    source_id: Mapped[UUID] = mapped_column(
        ForeignKey("sec_submission_sources.id", ondelete="RESTRICT"), nullable=False
    )
    cik: Mapped[str] = mapped_column(String(10), nullable=False)
    accession: Mapped[str] = mapped_column(String(20), nullable=False)
    form: Mapped[str] = mapped_column(String(16), nullable=False)
    report_date: Mapped[date] = mapped_column(Date(), nullable=False)
    filed_date: Mapped[date] = mapped_column(Date(), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    public_available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    visibility_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    primary_document: Mapped[str] = mapped_column(String(255), nullable=False)
    amendment_relation_status: Mapped[str] = mapped_column(String(32), nullable=False)
    base_accession: Mapped[str | None] = mapped_column(String(20), nullable=True)


class SecFilingCoverageRecord(UUIDPrimaryKeyMixin, Base):
    """Exact query scope proven complete by a frozen set of source versions."""

    __tablename__ = "sec_filing_coverage_manifests"
    __table_args__ = (
        UniqueConstraint("coverage_version"),
        CheckConstraint("cik ~ '^[0-9]{10}$' AND cik <> '0000000000'", name="cik_valid"),
        CheckConstraint("schema_version = 1", name="schema_version_supported"),
        CheckConstraint("report_period_end >= report_period_start", name="period_order"),
        CheckConstraint(
            "amendment_policy IN ('as_filed', 'latest_amendment_known_by_as_of')",
            name="amendment_policy_supported",
        ),
        CheckConstraint("source_count > 0", name="source_count_positive"),
    )

    coverage_version: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[int] = mapped_column(nullable=False)
    cik: Mapped[str] = mapped_column(String(10), nullable=False)
    allowed_forms: Mapped[list[str]] = mapped_column(JSON(), nullable=False)
    report_period_start: Mapped[date] = mapped_column(Date(), nullable=False)
    report_period_end: Mapped[date] = mapped_column(Date(), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    amendment_policy: Mapped[str] = mapped_column(String(64), nullable=False)
    source_count: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SecFilingCoverageSourceRecord(UUIDPrimaryKeyMixin, Base):
    """Many-to-many link freezing the sources used by one coverage manifest."""

    __tablename__ = "sec_filing_coverage_sources"
    __table_args__ = (UniqueConstraint("coverage_id", "source_id"),)

    coverage_id: Mapped[UUID] = mapped_column(
        ForeignKey("sec_filing_coverage_manifests.id", ondelete="RESTRICT"), nullable=False
    )
    source_id: Mapped[UUID] = mapped_column(
        ForeignKey("sec_submission_sources.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
