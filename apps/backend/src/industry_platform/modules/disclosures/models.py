"""SQLAlchemy models for canonical SEC filer and filing source facts."""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    UniqueConstraint,
    Uuid,
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


class SecFilingDocumentRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Canonical identity for one official document within an accession."""

    __tablename__ = "sec_filing_documents"
    __table_args__ = (
        UniqueConstraint("id", "filing_id"),
        UniqueConstraint("filing_id", "document_kind", "filename"),
        ForeignKeyConstraint(
            ["current_snapshot_id", "id"],
            ["sec_source_snapshots.id", "sec_source_snapshots.filing_document_id"],
            name="fk_sec_filing_documents_current_snapshot",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        CheckConstraint(
            "document_kind IN ('complete_submission', 'primary_document', "
            "'xbrl_instance', 'xbrl_attachment')",
            name="document_kind_supported",
        ),
        CheckConstraint(
            "accession ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'",
            name="accession_valid",
        ),
        CheckConstraint("length(btrim(filename)) > 0", name="filename_not_blank"),
        Index(None, "filing_id", "document_kind"),
    )

    filing_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sec_filings.id", ondelete="RESTRICT"), nullable=False
    )
    accession: Mapped[str] = mapped_column(String(20), nullable=False)
    document_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    current_snapshot_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class SecSourceSnapshotRecord(UUIDPrimaryKeyMixin, Base):
    """Append-only immutable bytes for one canonical filing document."""

    __tablename__ = "sec_source_snapshots"
    __table_args__ = (
        UniqueConstraint("id", "filing_document_id"),
        UniqueConstraint("filing_document_id", "source_version"),
        UniqueConstraint("filing_document_id", "content_sha256"),
        CheckConstraint(
            "source_url LIKE 'https://www.sec.gov/Archives/edgar/data/%'",
            name="source_url_allowlisted",
        ),
        CheckConstraint(
            "content_type IN ('text/plain', 'text/html', 'application/xhtml+xml', "
            "'application/xml', 'text/xml')",
            name="content_type_supported",
        ),
        CheckConstraint("octet_length(content_sha256) = 32", name="content_sha256_length"),
        CheckConstraint("byte_size > 0", name="byte_size_positive"),
        CheckConstraint("length(btrim(object_bucket)) > 0", name="object_bucket_not_blank"),
        CheckConstraint("length(btrim(object_key)) > 0", name="object_key_not_blank"),
        CheckConstraint("status IN ('active', 'quarantined')", name="status_supported"),
        CheckConstraint(
            "(status = 'quarantined') = (anomaly_code IS NOT NULL)",
            name="anomaly_state_consistent",
        ),
        CheckConstraint("source_available_at <= retrieved_at", name="availability_order"),
        CheckConstraint(
            "valid_to IS NULL OR valid_to >= source_available_at",
            name="validity_order",
        ),
        Index(None, "filing_document_id", "status", "source_available_at"),
    )

    filing_document_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sec_filing_documents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_version: Mapped[str] = mapped_column(String(128), nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    content_sha256: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    object_bucket: Mapped[str] = mapped_column(String(128), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    adapter_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    anomaly_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SecXbrlSourceRecord(UUIDPrimaryKeyMixin, Base):
    """Immutable aggregate response or raw filing snapshot used for XBRL facts."""

    __tablename__ = "sec_xbrl_sources"
    __table_args__ = (
        UniqueConstraint("id", "source_kind"),
        UniqueConstraint("source_url", "source_version"),
        CheckConstraint("cik ~ '^[0-9]{10}$' AND cik <> '0000000000'", name="cik_valid"),
        CheckConstraint(
            "source_kind IN ('companyfacts_aggregate', 'raw_inline', 'raw_instance')",
            name="source_kind_supported",
        ),
        CheckConstraint(
            "(source_kind = 'companyfacts_aggregate' "
            "AND source_url LIKE 'https://data.sec.gov/api/xbrl/companyfacts/CIK%.json' "
            "AND content_type = 'application/json' AND filing_snapshot_id IS NULL "
            "AND object_bucket IS NOT NULL AND object_key IS NOT NULL) OR "
            "(source_kind IN ('raw_inline', 'raw_instance') "
            "AND source_url LIKE 'https://www.sec.gov/Archives/edgar/data/%' "
            "AND content_type IN ('text/html', 'application/xhtml+xml', "
            "'application/xml', 'text/xml') AND filing_snapshot_id IS NOT NULL "
            "AND object_bucket IS NULL AND object_key IS NULL)",
            name="source_boundary_valid",
        ),
        CheckConstraint("octet_length(content_sha256) = 32", name="content_sha256_length"),
        CheckConstraint("byte_size > 0", name="byte_size_positive"),
        CheckConstraint("source_available_at <= retrieved_at", name="availability_order"),
        Index(None, "cik", "source_kind", "source_available_at"),
    )

    cik: Mapped[str] = mapped_column(String(10), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    filing_snapshot_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sec_source_snapshots.id", ondelete="RESTRICT"),
        nullable=True,
    )
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_version: Mapped[str] = mapped_column(String(128), nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    content_sha256: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    object_bucket: Mapped[str | None] = mapped_column(String(128), nullable=True)
    object_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SecXbrlContextRecord(UUIDPrimaryKeyMixin, Base):
    """Raw XBRL context; aggregate API facts intentionally have no context row."""

    __tablename__ = "sec_xbrl_contexts"
    __table_args__ = (
        UniqueConstraint("id", "source_id"),
        UniqueConstraint("source_id", "raw_context_id"),
        CheckConstraint("length(btrim(raw_context_id)) > 0", name="context_id_not_blank"),
        CheckConstraint("length(btrim(entity_identifier)) > 0", name="entity_not_blank"),
        CheckConstraint(
            "period_kind IN ('instant', 'duration', 'forever')",
            name="period_kind_supported",
        ),
        CheckConstraint(
            "(period_kind = 'instant' AND instant IS NOT NULL "
            "AND start_date IS NULL AND end_date IS NULL) OR "
            "(period_kind = 'duration' AND instant IS NULL "
            "AND start_date IS NOT NULL AND end_date IS NOT NULL AND end_date >= start_date) OR "
            "(period_kind = 'forever' AND instant IS NULL "
            "AND start_date IS NULL AND end_date IS NULL)",
            name="period_valid",
        ),
        Index(None, "source_id", "period_kind"),
    )

    source_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sec_xbrl_sources.id", ondelete="RESTRICT"), nullable=False
    )
    raw_context_id: Mapped[str] = mapped_column(String(255), nullable=False)
    entity_identifier: Mapped[str] = mapped_column(String(255), nullable=False)
    period_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    instant: Mapped[date | None] = mapped_column(Date(), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date(), nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date(), nullable=True)
    dimensions: Mapped[dict[str, str]] = mapped_column(JSON(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SecXbrlFactRecord(UUIDPrimaryKeyMixin, Base):
    """One immutable source-typed fact from a locked accession."""

    __tablename__ = "sec_xbrl_facts"
    __table_args__ = (
        UniqueConstraint("source_id", "locator_key"),
        ForeignKeyConstraint(
            ["context_id", "source_id"],
            ["sec_xbrl_contexts.id", "sec_xbrl_contexts.source_id"],
            name="fk_sec_xbrl_facts_context_source",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "accession ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'",
            name="accession_valid",
        ),
        CheckConstraint("form IN ('10-K', '10-K/A', '10-Q', '10-Q/A')", name="form_supported"),
        CheckConstraint("length(btrim(taxonomy)) > 0", name="taxonomy_not_blank"),
        CheckConstraint("length(btrim(concept)) > 0", name="concept_not_blank"),
        CheckConstraint("length(btrim(value)) > 0", name="value_not_blank"),
        CheckConstraint("length(btrim(locator_key)) > 0", name="locator_not_blank"),
        CheckConstraint(
            "period_kind IN ('instant', 'duration', 'forever')",
            name="period_kind_supported",
        ),
        CheckConstraint(
            "(period_kind = 'instant' AND instant IS NOT NULL "
            "AND start_date IS NULL AND end_date IS NULL) OR "
            "(period_kind = 'duration' AND instant IS NULL "
            "AND start_date IS NOT NULL AND end_date IS NOT NULL AND end_date >= start_date) OR "
            "(period_kind = 'forever' AND instant IS NULL "
            "AND start_date IS NULL AND end_date IS NULL)",
            name="period_valid",
        ),
        CheckConstraint("ordinal >= 0", name="ordinal_nonnegative"),
        Index(None, "filing_id", "taxonomy", "concept", "filed_date"),
        Index(None, "source_id", "context_id"),
    )

    filing_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sec_filings.id", ondelete="RESTRICT"), nullable=False
    )
    source_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sec_xbrl_sources.id", ondelete="RESTRICT"), nullable=False
    )
    context_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    accession: Mapped[str] = mapped_column(String(20), nullable=False)
    taxonomy: Mapped[str] = mapped_column(String(128), nullable=False)
    concept: Mapped[str] = mapped_column(String(256), nullable=False)
    value: Mapped[str] = mapped_column(String(20_000), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(255), nullable=True)
    period_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    instant: Mapped[date | None] = mapped_column(Date(), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date(), nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date(), nullable=True)
    filed_date: Mapped[date] = mapped_column(Date(), nullable=False)
    form: Mapped[str] = mapped_column(String(16), nullable=False)
    raw_context_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dimensions: Mapped[dict[str, str]] = mapped_column(JSON(), nullable=False)
    decimals: Mapped[str | None] = mapped_column(String(32), nullable=True)
    scale: Mapped[int | None] = mapped_column(nullable=True)
    format: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_custom: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    ordinal: Mapped[int] = mapped_column(nullable=False)
    locator_key: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WorkspaceSecImportRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Workspace authorization binding from a canonical snapshot to Knowledge."""

    __tablename__ = "workspace_sec_imports"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id"),
        UniqueConstraint("workspace_id", "primary_snapshot_id", "knowledge_base_id"),
        ForeignKeyConstraint(
            ["workspace_id", "created_by_user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            name="fk_workspace_sec_imports_creator",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["knowledge_base_id", "workspace_id"],
            ["knowledge_bases.id", "knowledge_bases.workspace_id"],
            name="fk_workspace_sec_imports_knowledge_base",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["file_id", "workspace_id"],
            ["file_objects.id", "file_objects.workspace_id"],
            name="fk_workspace_sec_imports_file",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["document_id", "knowledge_base_id", "workspace_id"],
            ["documents.id", "documents.knowledge_base_id", "documents.workspace_id"],
            name="fk_workspace_sec_imports_document",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["document_version_id", "document_id", "workspace_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.workspace_id",
            ],
            name="fk_workspace_sec_imports_document_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["ingestion_job_id", "workspace_id"],
            ["jobs.id", "jobs.workspace_id"],
            name="fk_workspace_sec_imports_job",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "accession ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'",
            name="accession_valid",
        ),
        Index(None, "workspace_id", "accession", "updated_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    filing_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sec_filings.id", ondelete="RESTRICT"), nullable=False
    )
    accession: Mapped[str] = mapped_column(String(20), nullable=False)
    primary_snapshot_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sec_source_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
    )
    complete_submission_snapshot_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sec_source_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
    )
    knowledge_base_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    file_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    document_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    ingestion_job_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)


class SecDisclosureMonitorRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Workspace-owned immutable-rule Monitor with one append-only watermark head."""

    __tablename__ = "sec_disclosure_monitors"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id"),
        ForeignKeyConstraint(
            ["workspace_id", "owner_user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            name="fk_sec_disclosure_monitors_owner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["knowledge_base_id", "workspace_id"],
            ["knowledge_bases.id", "knowledge_bases.workspace_id"],
            name="fk_sec_disclosure_monitors_knowledge_base",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["current_watermark_id", "id", "workspace_id"],
            [
                "sec_disclosure_monitor_watermarks.id",
                "sec_disclosure_monitor_watermarks.monitor_id",
                "sec_disclosure_monitor_watermarks.workspace_id",
            ],
            name="fk_sec_disclosure_monitors_watermark_head",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        CheckConstraint("status IN ('active', 'paused')", name="status_supported"),
        CheckConstraint("rule_set_version = 'sec-monitor-rules-v1'", name="rules_supported"),
        CheckConstraint("diff_version = 'sec-filing-diff-v1'", name="diff_supported"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("json_array_length(allowed_forms) BETWEEN 1 AND 4", name="forms_bounded"),
        Index(None, "workspace_id", "status", "updated_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    owner_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    filer_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sec_filers.id", ondelete="RESTRICT"), nullable=False
    )
    knowledge_base_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    schedule_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("schedules.id", ondelete="RESTRICT"), nullable=False
    )
    allowed_forms: Mapped[list[str]] = mapped_column(JSON(), nullable=False)
    rule_set_version: Mapped[str] = mapped_column(String(64), nullable=False)
    diff_version: Mapped[str] = mapped_column(String(64), nullable=False)
    timezone_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    current_watermark_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    created_from_approval_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )


class SecDisclosureMonitorRuleRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Typed executable rule; prose model output is never stored as executable policy."""

    __tablename__ = "sec_disclosure_monitor_rules"
    __table_args__ = (
        UniqueConstraint("id", "monitor_id", "workspace_id"),
        UniqueConstraint("monitor_id", "ordinal"),
        ForeignKeyConstraint(
            ["monitor_id", "workspace_id"],
            ["sec_disclosure_monitors.id", "sec_disclosure_monitors.workspace_id"],
            name="fk_sec_disclosure_monitor_rules_monitor",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "kind IN ('new_filing', 'amendment', 'fact_absolute_change', 'section_change')",
            name="kind_supported",
        ),
        CheckConstraint("rule_version = 'sec-monitor-rules-v1'", name="version_supported"),
        CheckConstraint("ordinal BETWEEN 1 AND 16", name="ordinal_bounded"),
        CheckConstraint(
            "length(btrim(section_query)) BETWEEN 1 AND 500", name="section_query_valid"
        ),
        CheckConstraint(
            "(kind = 'fact_absolute_change' AND taxonomy IS NOT NULL AND concept IS NOT NULL "
            "AND unit IS NOT NULL AND threshold IS NOT NULL "
            "AND comparator = 'absolute_delta_gte') OR "
            "(kind <> 'fact_absolute_change' AND taxonomy IS NULL AND concept IS NULL "
            "AND unit IS NULL AND threshold IS NULL AND comparator IS NULL)",
            name="fact_configuration_consistent",
        ),
        Index(None, "workspace_id", "monitor_id", "ordinal"),
    )

    monitor_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    ordinal: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    section_query: Mapped[str] = mapped_column(String(500), nullable=False)
    taxonomy: Mapped[str | None] = mapped_column(String(128), nullable=True)
    concept: Mapped[str | None] = mapped_column(String(256), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(255), nullable=True)
    threshold: Mapped[str | None] = mapped_column(String(200), nullable=True)
    comparator: Mapped[str | None] = mapped_column(String(64), nullable=True)


class SecDisclosureMonitorWatermarkRecord(UUIDPrimaryKeyMixin, Base):
    """Append-only proof that one complete official-source coverage set was processed."""

    __tablename__ = "sec_disclosure_monitor_watermarks"
    __table_args__ = (
        UniqueConstraint("id", "monitor_id", "workspace_id"),
        UniqueConstraint("monitor_id", "revision"),
        ForeignKeyConstraint(
            ["monitor_id", "workspace_id"],
            ["sec_disclosure_monitors.id", "sec_disclosure_monitors.workspace_id"],
            name="fk_sec_disclosure_monitor_watermarks_monitor",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("length(btrim(coverage_version)) > 0", name="coverage_version_not_blank"),
        CheckConstraint(
            "(accepted_at IS NULL AND accession IS NULL) OR "
            "(accepted_at IS NOT NULL AND accession ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$')",
            name="cursor_complete",
        ),
        Index(None, "workspace_id", "monitor_id", "revision"),
    )

    monitor_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    coverage_version: Mapped[str] = mapped_column(String(128), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accession: Mapped[str | None] = mapped_column(String(20), nullable=True)
    monitor_run_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SecDisclosureMonitorRunRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One Schedule occurrence projection and its exact source watermark fence."""

    __tablename__ = "sec_disclosure_monitor_runs"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id"),
        UniqueConstraint("schedule_occurrence_id"),
        UniqueConstraint("job_id"),
        ForeignKeyConstraint(
            ["monitor_id", "workspace_id"],
            ["sec_disclosure_monitors.id", "sec_disclosure_monitors.workspace_id"],
            name="fk_sec_disclosure_monitor_runs_monitor",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_watermark_id", "monitor_id", "workspace_id"],
            [
                "sec_disclosure_monitor_watermarks.id",
                "sec_disclosure_monitor_watermarks.monitor_id",
                "sec_disclosure_monitor_watermarks.workspace_id",
            ],
            name="fk_sec_disclosure_monitor_runs_source_watermark",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["result_watermark_id", "monitor_id", "workspace_id"],
            [
                "sec_disclosure_monitor_watermarks.id",
                "sec_disclosure_monitor_watermarks.monitor_id",
                "sec_disclosure_monitor_watermarks.workspace_id",
            ],
            name="fk_sec_disclosure_monitor_runs_result_watermark",
            ondelete="RESTRICT",
        ),
        CheckConstraint("status IN ('queued', 'running', 'succeeded')", name="status_supported"),
        CheckConstraint("coalesced_count >= 1", name="coalesced_count_positive"),
        CheckConstraint("window_end >= window_start", name="window_order"),
        CheckConstraint(
            "(status = 'succeeded' AND result_watermark_id IS NOT NULL "
            "AND completed_at IS NOT NULL) "
            "OR (status <> 'succeeded' AND result_watermark_id IS NULL AND completed_at IS NULL)",
            name="terminal_state_consistent",
        ),
        Index(None, "workspace_id", "monitor_id", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    monitor_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    schedule_occurrence_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("schedule_occurrences.id", ondelete="RESTRICT"),
        nullable=False,
    )
    job_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False
    )
    source_watermark_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    result_watermark_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    coalesced_count: Mapped[int] = mapped_column(Integer, nullable=False)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SecDisclosureCaseRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Idempotent verified alert generated from one comparison pair and executable rule."""

    __tablename__ = "sec_disclosure_cases"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id"),
        UniqueConstraint("workspace_id", "idempotency_key"),
        ForeignKeyConstraint(
            ["monitor_id", "workspace_id"],
            ["sec_disclosure_monitors.id", "sec_disclosure_monitors.workspace_id"],
            name="fk_sec_disclosure_cases_monitor",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["monitor_run_id", "workspace_id"],
            ["sec_disclosure_monitor_runs.id", "sec_disclosure_monitor_runs.workspace_id"],
            name="fk_sec_disclosure_cases_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["rule_id", "monitor_id", "workspace_id"],
            [
                "sec_disclosure_monitor_rules.id",
                "sec_disclosure_monitor_rules.monitor_id",
                "sec_disclosure_monitor_rules.workspace_id",
            ],
            name="fk_sec_disclosure_cases_rule",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "trigger_kind IN ('new_filing', 'amendment', 'fact_absolute_change', 'section_change')",
            name="trigger_supported",
        ),
        CheckConstraint("rule_version = 'sec-monitor-rules-v1'", name="rule_version_supported"),
        CheckConstraint("diff_version = 'sec-filing-diff-v1'", name="diff_version_supported"),
        CheckConstraint("octet_length(diff_sha256) = 32", name="diff_hash_length"),
        CheckConstraint("length(idempotency_key) = 64", name="idempotency_key_length"),
        CheckConstraint("verification_status = 'verified'", name="verification_status_supported"),
        CheckConstraint("notification_status = 'pending'", name="notification_status_supported"),
        CheckConstraint(
            "baseline_accession ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$' AND "
            "target_accession ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$' AND "
            "baseline_accession <> target_accession",
            name="comparison_accessions_valid",
        ),
        Index(None, "workspace_id", "monitor_id", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    monitor_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    monitor_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    rule_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    trigger_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_coverage_version: Mapped[str] = mapped_column(String(128), nullable=False)
    baseline_filing_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sec_filings.id", ondelete="RESTRICT"), nullable=False
    )
    target_filing_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sec_filings.id", ondelete="RESTRICT"), nullable=False
    )
    baseline_accession: Mapped[str] = mapped_column(String(20), nullable=False)
    target_accession: Mapped[str] = mapped_column(String(20), nullable=False)
    diff_version: Mapped[str] = mapped_column(String(64), nullable=False)
    diff_payload: Mapped[dict[str, object]] = mapped_column(JSON(), nullable=False)
    diff_sha256: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    verification_status: Mapped[str] = mapped_column(String(16), nullable=False)
    notification_status: Mapped[str] = mapped_column(String(16), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)


class SecDisclosureCaseEvidenceRecord(UUIDPrimaryKeyMixin, Base):
    """Ordered Case-to-Evidence links preserving both comparison sides."""

    __tablename__ = "sec_disclosure_case_evidence"
    __table_args__ = (
        UniqueConstraint("case_id", "side"),
        UniqueConstraint("case_id", "evidence_id"),
        ForeignKeyConstraint(
            ["case_id", "workspace_id"],
            ["sec_disclosure_cases.id", "sec_disclosure_cases.workspace_id"],
            name="fk_sec_disclosure_case_evidence_case",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["evidence_id", "workspace_id"],
            ["evidence.id", "evidence.workspace_id"],
            name="fk_sec_disclosure_case_evidence_evidence",
            ondelete="RESTRICT",
        ),
        CheckConstraint("side IN ('baseline', 'target')", name="side_supported"),
        Index(None, "workspace_id", "case_id", "side"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    case_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    evidence_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
