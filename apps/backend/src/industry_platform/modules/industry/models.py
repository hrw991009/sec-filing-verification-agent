"""PostgreSQL source of truth for industry context and collected source facts."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
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
from industry_platform.modules.industry.domain import (
    CollectionRunStatus,
    CollectionTriggerKind,
    ProviderCode,
    SourceItemDisposition,
    SourceKind,
)


class IndustryRecord(UUIDPrimaryKeyMixin, Base):
    """One global, versioned preset; it is product context, not authorization."""

    __tablename__ = "industries"
    __table_args__ = (
        CheckConstraint("length(btrim(code)) > 0", name="code_not_blank"),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        CheckConstraint("length(btrim(default_query)) > 0", name="default_query_not_blank"),
        CheckConstraint("length(btrim(default_symbol)) > 0", name="default_symbol_not_blank"),
        CheckConstraint("version >= 1", name="version_positive"),
        UniqueConstraint("code"),
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    default_query: Mapped[str] = mapped_column(String(200), nullable=False)
    default_symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default=text("1")
    )


class UserIndustryPreference(TimestampMixin, Base):
    """Current preset for one active user membership in one Workspace."""

    __tablename__ = "user_industry_preferences"
    __table_args__ = (
        ForeignKeyConstraint(
            ("workspace_id", "user_id"),
            ("workspace_members.workspace_id", "workspace_members.user_id"),
            name="fk_industry_preferences_workspace_user_membership",
            ondelete="CASCADE",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    industry_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("industries.id", ondelete="RESTRICT"),
        nullable=False,
    )


class DataSourceRecord(UUIDPrimaryKeyMixin, Base):
    """Trusted Provider catalog and immutable provenance contract."""

    __tablename__ = "data_sources"
    __table_args__ = (
        CheckConstraint("length(btrim(provider)) > 0", name="provider_not_blank"),
        CheckConstraint("kind IN ('news', 'policy', 'tender', 'stock')", name="kind"),
        CheckConstraint("length(btrim(version)) > 0", name="version_not_blank"),
        CheckConstraint("length(btrim(display_name)) > 0", name="display_name_not_blank"),
        CheckConstraint("length(btrim(usage_constraints)) > 0", name="usage_constraints_not_blank"),
        UniqueConstraint("provider", "version"),
        UniqueConstraint("id", "kind"),
    )

    provider: Mapped[ProviderCode] = mapped_column(
        SqlEnum(
            ProviderCode,
            name="industry_provider_code",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=32,
        ),
        nullable=False,
    )
    kind: Mapped[SourceKind] = mapped_column(
        SqlEnum(
            SourceKind,
            name="industry_source_kind",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    usage_constraints: Mapped[str] = mapped_column(String(1_000), nullable=False)
    requires_secret: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))


class CollectionRunRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One durable collection attempt, pre-created with its Schedule occurrence."""

    __tablename__ = "collection_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ("data_source_id", "source_kind"),
            ("data_sources.id", "data_sources.kind"),
            name="fk_collection_runs_source_kind",
            ondelete="RESTRICT",
        ),
        CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed')", name="status"),
        CheckConstraint("trigger_kind IN ('scheduled', 'manual')", name="trigger_kind"),
        CheckConstraint("length(btrim(query)) > 0", name="query_not_blank"),
        CheckConstraint("length(btrim(trace_id)) > 0", name="trace_id_not_blank"),
        CheckConstraint(
            "coalesced_count >= 1 AND window_end >= window_start",
            name="materialization_window_consistent",
        ),
        CheckConstraint(
            "fetched_count >= 0 AND inserted_count >= 0 AND duplicate_count >= 0 "
            "AND inserted_count + duplicate_count <= fetched_count",
            name="count_bounds",
        ),
        CheckConstraint(
            "(status = 'queued' AND started_at IS NULL AND terminal_at IS NULL) OR "
            "(status = 'running' AND started_at IS NOT NULL AND terminal_at IS NULL) OR "
            "(status IN ('succeeded', 'failed') AND started_at IS NOT NULL "
            "AND terminal_at IS NOT NULL AND terminal_at >= started_at)",
            name="lifecycle_consistent",
        ),
        CheckConstraint(
            "(status = 'failed' AND last_error_code IS NOT NULL) OR "
            "(status <> 'failed' AND last_error_code IS NULL)",
            name="error_consistent",
        ),
        UniqueConstraint("schedule_occurrence_id"),
        UniqueConstraint("job_id"),
        Index(None, "workspace_id", "created_at", "id"),
        Index(None, "workspace_id", "source_kind", "status", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    industry_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("industries.id", ondelete="RESTRICT"), nullable=False
    )
    data_source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_kind: Mapped[SourceKind] = mapped_column(
        SqlEnum(
            SourceKind,
            name="collection_source_kind",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    schedule_occurrence_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("schedule_occurrences.id", ondelete="RESTRICT"),
        nullable=False,
    )
    job_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False
    )
    trigger_kind: Mapped[CollectionTriggerKind] = mapped_column(
        SqlEnum(
            CollectionTriggerKind,
            name="collection_trigger_kind",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    query: Mapped[str] = mapped_column(String(200), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[CollectionRunStatus] = mapped_column(
        SqlEnum(
            CollectionRunStatus,
            name="collection_run_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
        default=CollectionRunStatus.QUEUED,
        server_default=CollectionRunStatus.QUEUED.value,
    )
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    coalesced_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    inserted_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    duplicate_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    next_cursor: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)


class CollectionCursorRecord(TimestampMixin, Base):
    """Last committed Provider cursor and operational readiness per scope."""

    __tablename__ = "collection_cursors"
    __table_args__ = (
        ForeignKeyConstraint(
            ("data_source_id", "source_kind"),
            ("data_sources.id", "data_sources.kind"),
            name="fk_collection_cursors_source_kind",
            ondelete="RESTRICT",
        ),
        CheckConstraint("success_count >= 0 AND failure_count >= 0", name="count_bounds"),
        CheckConstraint(
            "last_error_code IS NULL OR last_failure_at IS NOT NULL",
            name="failure_fields_consistent",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        primary_key=True,
    )
    industry_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("industries.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    data_source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    source_kind: Mapped[SourceKind] = mapped_column(
        SqlEnum(
            SourceKind,
            name="cursor_source_kind",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    cursor: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    success_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    failure_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )


class SourceItemRecord(UUIDPrimaryKeyMixin, Base):
    """Provider-neutral source snapshot with immutable provenance."""

    __tablename__ = "source_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ("data_source_id", "source_kind"),
            ("data_sources.id", "data_sources.kind"),
            name="fk_source_items_source_kind",
            ondelete="RESTRICT",
        ),
        CheckConstraint("length(btrim(external_id)) > 0", name="external_id_not_blank"),
        CheckConstraint("length(btrim(title)) > 0", name="title_not_blank"),
        CheckConstraint("length(btrim(summary)) > 0", name="summary_not_blank"),
        CheckConstraint("locator LIKE 'https://%'", name="locator_is_https"),
        CheckConstraint("octet_length(content_sha256) = 32", name="content_hash_length"),
        CheckConstraint("jsonb_typeof(source_metadata) = 'object'", name="metadata_is_object"),
        UniqueConstraint("workspace_id", "data_source_id", "external_id"),
        UniqueConstraint("workspace_id", "data_source_id", "content_sha256"),
        UniqueConstraint("id", "source_kind"),
        Index(None, "workspace_id", "industry_id", "source_kind", "published_at", "id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    industry_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("industries.id", ondelete="RESTRICT"), nullable=False
    )
    data_source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_kind: Mapped[SourceKind] = mapped_column(
        SqlEnum(
            SourceKind,
            name="source_item_kind",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    external_id: Mapped[str] = mapped_column(String(256), nullable=False)
    title: Mapped[str] = mapped_column(String(1_000), nullable=False)
    summary: Mapped[str] = mapped_column(String(10_000), nullable=False)
    locator: Mapped[str] = mapped_column(String(2_048), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    content_sha256: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    source_metadata: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    usage_constraints: Mapped[str] = mapped_column(String(1_000), nullable=False)


class CollectionRunItemRecord(Base):
    """Per-run ingest outcome, including visible duplicate decisions."""

    __tablename__ = "collection_run_items"
    __table_args__ = (
        CheckConstraint("length(btrim(external_id)) > 0", name="external_id_not_blank"),
        CheckConstraint("octet_length(content_sha256) = 32", name="content_hash_length"),
        CheckConstraint(
            "disposition IN ('inserted', 'duplicate_external_id', 'duplicate_content')",
            name="disposition",
        ),
    )

    collection_run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("collection_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    external_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    source_item_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("source_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    content_sha256: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    disposition: Mapped[SourceItemDisposition] = mapped_column(
        SqlEnum(
            SourceItemDisposition,
            name="source_item_disposition",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=enum_values,
            length=32,
        ),
        nullable=False,
    )


class NewsItemRecord(Base):
    __tablename__ = "news_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ("source_item_id", "source_kind"),
            ("source_items.id", "source_items.source_kind"),
            name="fk_news_items_source_kind",
            ondelete="CASCADE",
        ),
        CheckConstraint("source_kind = 'news'", name="source_kind"),
    )

    source_item_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="news")
    category: Mapped[str] = mapped_column(String(100), nullable=False)


class PolicyItemRecord(Base):
    __tablename__ = "policy_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ("source_item_id", "source_kind"),
            ("source_items.id", "source_items.source_kind"),
            name="fk_policy_items_source_kind",
            ondelete="CASCADE",
        ),
        CheckConstraint("source_kind = 'policy'", name="source_kind"),
    )

    source_item_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="policy")
    jurisdiction: Mapped[str] = mapped_column(String(100), nullable=False)
    document_number: Mapped[str] = mapped_column(String(100), nullable=False)
    agency: Mapped[str] = mapped_column(String(500), nullable=False)


class BiddingItemRecord(Base):
    __tablename__ = "bidding_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ("source_item_id", "source_kind"),
            ("source_items.id", "source_items.source_kind"),
            name="fk_bidding_items_source_kind",
            ondelete="CASCADE",
        ),
        CheckConstraint("source_kind = 'tender'", name="source_kind"),
    )

    source_item_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="tender")
    notice_type: Mapped[str] = mapped_column(String(100), nullable=False)
    region: Mapped[str] = mapped_column(String(100), nullable=False)


class MarketSnapshotRecord(Base):
    __tablename__ = "market_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ("source_item_id", "source_kind"),
            ("source_items.id", "source_items.source_kind"),
            name="fk_market_snapshots_source_kind",
            ondelete="CASCADE",
        ),
        CheckConstraint("source_kind = 'stock'", name="source_kind"),
        CheckConstraint("price > 0", name="price_positive"),
    )

    source_item_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="stock")
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
