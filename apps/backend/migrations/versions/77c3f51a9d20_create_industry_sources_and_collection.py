"""create industry sources and collection

Revision ID: 77c3f51a9d20
Revises: 4d9b8f6c2a10
Create Date: 2026-08-17 10:45:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "77c3f51a9d20"
down_revision: str | Sequence[str] | None = "4d9b8f6c2a10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add preset context, source provenance, collection runs, and domain projections."""

    op.create_table(
        "industries",
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("default_query", sa.String(length=200), nullable=False),
        sa.Column("default_symbol", sa.String(length=16), nullable=False),
        sa.Column("version", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.CheckConstraint("length(btrim(code)) > 0", name=op.f("ck_industries_code_not_blank")),
        sa.CheckConstraint(
            "length(btrim(default_query)) > 0",
            name=op.f("ck_industries_default_query_not_blank"),
        ),
        sa.CheckConstraint(
            "length(btrim(default_symbol)) > 0",
            name=op.f("ck_industries_default_symbol_not_blank"),
        ),
        sa.CheckConstraint("length(btrim(name)) > 0", name=op.f("ck_industries_name_not_blank")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_industries_version_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_industries")),
        sa.UniqueConstraint("code", name=op.f("uq_industries_code")),
    )
    industries = sa.table(
        "industries",
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("default_query", sa.String()),
        sa.column("default_symbol", sa.String()),
        sa.column("version", sa.SmallInteger()),
    )
    op.bulk_insert(
        industries,
        [
            {
                "id": "5ae94c40-4441-5e6f-b4cb-0679e8a92f9e",
                "code": "smart_transport",
                "name": "智慧交通",
                "default_query": "smart transport mobility autonomous vehicles",
                "default_symbol": "TSLA",
                "version": 1,
            },
            {
                "id": "56edef5d-ee4d-5978-8069-f89bd391ac20",
                "code": "fintech",
                "name": "金融科技",
                "default_query": "financial technology digital payments fintech",
                "default_symbol": "PYPL",
                "version": 1,
            },
            {
                "id": "5ecae69a-b8d1-54a3-9fe4-e0a6a3c86cbe",
                "code": "healthcare",
                "name": "医疗健康",
                "default_query": "healthcare medical health technology",
                "default_symbol": "UNH",
                "version": 1,
            },
            {
                "id": "a985dc08-83d8-5efb-b84e-034ffd453e38",
                "code": "energy_power",
                "name": "能源电力",
                "default_query": "energy power electricity renewable",
                "default_symbol": "XOM",
                "version": 1,
            },
        ],
    )
    op.create_table(
        "data_sources",
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("version", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("usage_constraints", sa.String(length=1000), nullable=False),
        sa.Column("requires_secret", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.CheckConstraint(
            "length(btrim(display_name)) > 0",
            name=op.f("ck_data_sources_display_name_not_blank"),
        ),
        sa.CheckConstraint(
            "kind IN ('news', 'policy', 'tender', 'stock')",
            name=op.f("ck_data_sources_kind"),
        ),
        sa.CheckConstraint(
            "length(btrim(provider)) > 0",
            name=op.f("ck_data_sources_provider_not_blank"),
        ),
        sa.CheckConstraint(
            "length(btrim(usage_constraints)) > 0",
            name=op.f("ck_data_sources_usage_constraints_not_blank"),
        ),
        sa.CheckConstraint(
            "length(btrim(version)) > 0",
            name=op.f("ck_data_sources_version_not_blank"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_data_sources")),
        sa.UniqueConstraint("id", "kind", name=op.f("uq_data_sources_id_kind")),
        sa.UniqueConstraint("provider", "version", name=op.f("uq_data_sources_provider_version")),
    )
    sources = sa.table(
        "data_sources",
        sa.column("id", sa.Uuid()),
        sa.column("provider", sa.String()),
        sa.column("kind", sa.String()),
        sa.column("version", sa.String()),
        sa.column("display_name", sa.String()),
        sa.column("usage_constraints", sa.String()),
        sa.column("requires_secret", sa.Boolean()),
    )
    op.bulk_insert(
        sources,
        [
            {
                "id": "3dadb35b-658d-5fa9-a88c-108f159e9b4c",
                "provider": "world_bank_news",
                "kind": "news",
                "version": "api-v2-2026-08",
                "display_name": "World Bank News",
                "usage_constraints": (
                    "Public World Bank news metadata; preserve attribution and original link."
                ),
                "requires_secret": False,
            },
            {
                "id": "e410cbbf-b09d-5daa-ab9c-4a7f98c160d1",
                "provider": "federal_register",
                "kind": "policy",
                "version": "api-v1-2026-08",
                "display_name": "Federal Register",
                "usage_constraints": (
                    "United States government public metadata; preserve agency and document link."
                ),
                "requires_secret": False,
            },
            {
                "id": "8ae93bf1-af49-5ff7-95ca-68325e14935c",
                "provider": "ted",
                "kind": "tender",
                "version": "api-v3-2026-08",
                "display_name": "Tenders Electronic Daily",
                "usage_constraints": (
                    "EU public procurement metadata; preserve publication number and TED link."
                ),
                "requires_secret": False,
            },
            {
                "id": "f55cc967-812e-5934-b734-b9446c1346d9",
                "provider": "alpha_vantage",
                "kind": "stock",
                "version": "global-quote-v1",
                "display_name": "Alpha Vantage",
                "usage_constraints": (
                    "Market data is informational and may be delayed; preserve source and "
                    "observation time."
                ),
                "requires_secret": True,
            },
        ],
    )
    op.create_table(
        "user_industry_preferences",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("industry_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["industry_id"],
            ["industries.id"],
            name=op.f("fk_user_industry_preferences_industry_id_industries"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            name="fk_industry_preferences_workspace_user_membership",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "workspace_id", "user_id", name=op.f("pk_user_industry_preferences")
        ),
    )
    op.create_table(
        "collection_runs",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("industry_id", sa.Uuid(), nullable=False),
        sa.Column("data_source_id", sa.Uuid(), nullable=False),
        sa.Column("source_kind", sa.String(length=16), nullable=False),
        sa.Column("schedule_occurrence_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("trigger_kind", sa.String(length=16), nullable=False),
        sa.Column("query", sa.String(length=200), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="queued", nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("coalesced_count", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("inserted_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("duplicate_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_cursor", sa.String(length=512), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "fetched_count >= 0 AND inserted_count >= 0 AND duplicate_count >= 0 "
            "AND inserted_count + duplicate_count <= fetched_count",
            name=op.f("ck_collection_runs_count_bounds"),
        ),
        sa.CheckConstraint(
            "(status = 'failed' AND last_error_code IS NOT NULL) OR "
            "(status <> 'failed' AND last_error_code IS NULL)",
            name=op.f("ck_collection_runs_error_consistent"),
        ),
        sa.CheckConstraint(
            "(status = 'queued' AND started_at IS NULL AND terminal_at IS NULL) OR "
            "(status = 'running' AND started_at IS NOT NULL AND terminal_at IS NULL) OR "
            "(status IN ('succeeded', 'failed') AND started_at IS NOT NULL "
            "AND terminal_at IS NOT NULL AND terminal_at >= started_at)",
            name=op.f("ck_collection_runs_lifecycle_consistent"),
        ),
        sa.CheckConstraint(
            "coalesced_count >= 1 AND window_end >= window_start",
            name=op.f("ck_collection_runs_materialization_window_consistent"),
        ),
        sa.CheckConstraint(
            "length(btrim(query)) > 0", name=op.f("ck_collection_runs_query_not_blank")
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name=op.f("ck_collection_runs_status"),
        ),
        sa.CheckConstraint(
            "length(btrim(trace_id)) > 0",
            name=op.f("ck_collection_runs_trace_id_not_blank"),
        ),
        sa.CheckConstraint(
            "trigger_kind IN ('scheduled', 'manual')",
            name=op.f("ck_collection_runs_trigger_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["data_source_id", "source_kind"],
            ["data_sources.id", "data_sources.kind"],
            name="fk_collection_runs_source_kind",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["industry_id"],
            ["industries.id"],
            name=op.f("fk_collection_runs_industry_id_industries"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name=op.f("fk_collection_runs_job_id_jobs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["schedule_occurrence_id"],
            ["schedule_occurrences.id"],
            name=op.f("fk_collection_runs_schedule_occurrence_id_schedule_occurrences"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_collection_runs_workspace_id_workspaces"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_collection_runs")),
        sa.UniqueConstraint("job_id", name=op.f("uq_collection_runs_job_id")),
        sa.UniqueConstraint(
            "schedule_occurrence_id",
            name=op.f("uq_collection_runs_schedule_occurrence_id"),
        ),
    )
    op.create_index(
        op.f("ix_collection_runs_workspace_id_created_at_id"),
        "collection_runs",
        ["workspace_id", "created_at", "id"],
    )
    op.create_index(
        op.f("ix_collection_runs_workspace_id_source_kind_status_created_at"),
        "collection_runs",
        ["workspace_id", "source_kind", "status", "created_at"],
    )
    op.create_table(
        "collection_cursors",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("industry_id", sa.Uuid(), nullable=False),
        sa.Column("data_source_id", sa.Uuid(), nullable=False),
        sa.Column("source_kind", sa.String(length=16), nullable=False),
        sa.Column("cursor", sa.String(length=512), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("success_count", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("failure_count", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "success_count >= 0 AND failure_count >= 0",
            name=op.f("ck_collection_cursors_count_bounds"),
        ),
        sa.CheckConstraint(
            "last_error_code IS NULL OR last_failure_at IS NOT NULL",
            name=op.f("ck_collection_cursors_failure_fields_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["data_source_id", "source_kind"],
            ["data_sources.id", "data_sources.kind"],
            name="fk_collection_cursors_source_kind",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["industry_id"],
            ["industries.id"],
            name=op.f("fk_collection_cursors_industry_id_industries"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_collection_cursors_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "workspace_id", "industry_id", "data_source_id", name=op.f("pk_collection_cursors")
        ),
    )
    op.create_table(
        "source_items",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("industry_id", sa.Uuid(), nullable=False),
        sa.Column("data_source_id", sa.Uuid(), nullable=False),
        sa.Column("source_kind", sa.String(length=16), nullable=False),
        sa.Column("external_id", sa.String(length=256), nullable=False),
        sa.Column("title", sa.String(length=1000), nullable=False),
        sa.Column("summary", sa.String(length=10000), nullable=False),
        sa.Column("locator", sa.String(length=2048), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "collected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("content_sha256", sa.LargeBinary(length=32), nullable=False),
        sa.Column(
            "source_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("usage_constraints", sa.String(length=1000), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.CheckConstraint(
            "octet_length(content_sha256) = 32",
            name=op.f("ck_source_items_content_hash_length"),
        ),
        sa.CheckConstraint(
            "length(btrim(external_id)) > 0",
            name=op.f("ck_source_items_external_id_not_blank"),
        ),
        sa.CheckConstraint(
            "locator LIKE 'https://%'", name=op.f("ck_source_items_locator_is_https")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(source_metadata) = 'object'",
            name=op.f("ck_source_items_metadata_is_object"),
        ),
        sa.CheckConstraint(
            "length(btrim(summary)) > 0", name=op.f("ck_source_items_summary_not_blank")
        ),
        sa.CheckConstraint(
            "length(btrim(title)) > 0", name=op.f("ck_source_items_title_not_blank")
        ),
        sa.ForeignKeyConstraint(
            ["data_source_id", "source_kind"],
            ["data_sources.id", "data_sources.kind"],
            name="fk_source_items_source_kind",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["industry_id"],
            ["industries.id"],
            name=op.f("fk_source_items_industry_id_industries"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_source_items_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_items")),
        sa.UniqueConstraint("id", "source_kind", name=op.f("uq_source_items_id_source_kind")),
        sa.UniqueConstraint(
            "workspace_id",
            "data_source_id",
            "content_sha256",
            name=op.f("uq_source_items_workspace_id_data_source_id_content_sha256"),
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "data_source_id",
            "external_id",
            name=op.f("uq_source_items_workspace_id_data_source_id_external_id"),
        ),
    )
    op.create_index(
        op.f("ix_source_items_workspace_id_industry_id_source_kind_published_at_id"),
        "source_items",
        ["workspace_id", "industry_id", "source_kind", "published_at", "id"],
    )
    op.create_table(
        "collection_run_items",
        sa.Column("collection_run_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(length=256), nullable=False),
        sa.Column("source_item_id", sa.Uuid(), nullable=False),
        sa.Column("content_sha256", sa.LargeBinary(length=32), nullable=False),
        sa.Column("disposition", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "octet_length(content_sha256) = 32",
            name=op.f("ck_collection_run_items_content_hash_length"),
        ),
        sa.CheckConstraint(
            "disposition IN ('inserted', 'duplicate_external_id', 'duplicate_content')",
            name=op.f("ck_collection_run_items_disposition"),
        ),
        sa.CheckConstraint(
            "length(btrim(external_id)) > 0",
            name=op.f("ck_collection_run_items_external_id_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["collection_run_id"],
            ["collection_runs.id"],
            name=op.f("fk_collection_run_items_collection_run_id_collection_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_item_id"],
            ["source_items.id"],
            name=op.f("fk_collection_run_items_source_item_id_source_items"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "collection_run_id", "external_id", name=op.f("pk_collection_run_items")
        ),
        sa.UniqueConstraint(
            "collection_run_id",
            "external_id",
            name=op.f("uq_collection_run_items_collection_run_id_external_id"),
        ),
    )
    _create_domain_tables()


def _create_domain_tables() -> None:
    op.create_table(
        "news_items",
        sa.Column("source_item_id", sa.Uuid(), nullable=False),
        sa.Column("source_kind", sa.String(length=16), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.CheckConstraint("source_kind = 'news'", name=op.f("ck_news_items_source_kind")),
        sa.ForeignKeyConstraint(
            ["source_item_id", "source_kind"],
            ["source_items.id", "source_items.source_kind"],
            name="fk_news_items_source_kind",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("source_item_id", name=op.f("pk_news_items")),
    )
    op.create_table(
        "policy_items",
        sa.Column("source_item_id", sa.Uuid(), nullable=False),
        sa.Column("source_kind", sa.String(length=16), nullable=False),
        sa.Column("jurisdiction", sa.String(length=100), nullable=False),
        sa.Column("document_number", sa.String(length=100), nullable=False),
        sa.Column("agency", sa.String(length=500), nullable=False),
        sa.CheckConstraint("source_kind = 'policy'", name=op.f("ck_policy_items_source_kind")),
        sa.ForeignKeyConstraint(
            ["source_item_id", "source_kind"],
            ["source_items.id", "source_items.source_kind"],
            name="fk_policy_items_source_kind",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("source_item_id", name=op.f("pk_policy_items")),
    )
    op.create_table(
        "bidding_items",
        sa.Column("source_item_id", sa.Uuid(), nullable=False),
        sa.Column("source_kind", sa.String(length=16), nullable=False),
        sa.Column("notice_type", sa.String(length=100), nullable=False),
        sa.Column("region", sa.String(length=100), nullable=False),
        sa.CheckConstraint("source_kind = 'tender'", name=op.f("ck_bidding_items_source_kind")),
        sa.ForeignKeyConstraint(
            ["source_item_id", "source_kind"],
            ["source_items.id", "source_items.source_kind"],
            name="fk_bidding_items_source_kind",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("source_item_id", name=op.f("pk_bidding_items")),
    )
    op.create_table(
        "market_snapshots",
        sa.Column("source_item_id", sa.Uuid(), nullable=False),
        sa.Column("source_kind", sa.String(length=16), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("price", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("price > 0", name=op.f("ck_market_snapshots_price_positive")),
        sa.CheckConstraint("source_kind = 'stock'", name=op.f("ck_market_snapshots_source_kind")),
        sa.ForeignKeyConstraint(
            ["source_item_id", "source_kind"],
            ["source_items.id", "source_items.source_kind"],
            name="fk_market_snapshots_source_kind",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("source_item_id", name=op.f("pk_market_snapshots")),
    )


def downgrade() -> None:
    """Remove industry collection facts and preset context."""

    op.drop_table("market_snapshots")
    op.drop_table("bidding_items")
    op.drop_table("policy_items")
    op.drop_table("news_items")
    op.drop_table("collection_run_items")
    op.drop_index(
        op.f("ix_source_items_workspace_id_industry_id_source_kind_published_at_id"),
        table_name="source_items",
    )
    op.drop_table("source_items")
    op.drop_table("collection_cursors")
    op.drop_index(
        op.f("ix_collection_runs_workspace_id_source_kind_status_created_at"),
        table_name="collection_runs",
    )
    op.drop_index(
        op.f("ix_collection_runs_workspace_id_created_at_id"),
        table_name="collection_runs",
    )
    op.drop_table("collection_runs")
    op.drop_table("user_industry_preferences")
    op.drop_table("data_sources")
    op.drop_table("industries")
