"""Create immutable SEC XBRL sources, contexts, and facts.

Revision ID: b7c9d1e3f468
Revises: a6b8c0d2e357
Create Date: 2026-08-27 00:00:00.000000
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "b7c9d1e3f468"
down_revision: str | Sequence[str] | None = "a6b8c0d2e357"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id_column() -> sa.Column[Any]:
    return sa.Column(
        "id",
        sa.Uuid(),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def _created_at_column() -> sa.Column[Any]:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    )


def _period_constraints(table: str) -> tuple[sa.CheckConstraint, sa.CheckConstraint]:
    return (
        sa.CheckConstraint(
            "period_kind IN ('instant', 'duration', 'forever')",
            name=op.f(f"ck_{table}_period_kind_supported"),
        ),
        sa.CheckConstraint(
            "(period_kind = 'instant' AND instant IS NOT NULL "
            "AND start_date IS NULL AND end_date IS NULL) OR "
            "(period_kind = 'duration' AND instant IS NULL "
            "AND start_date IS NOT NULL AND end_date IS NOT NULL "
            "AND end_date >= start_date) OR "
            "(period_kind = 'forever' AND instant IS NULL "
            "AND start_date IS NULL AND end_date IS NULL)",
            name=op.f(f"ck_{table}_period_valid"),
        ),
    )


def upgrade() -> None:
    op.create_table(
        "sec_xbrl_sources",
        sa.Column("cik", sa.String(length=10), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("filing_snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
        sa.Column("source_version", sa.String(length=128), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("content_sha256", sa.LargeBinary(length=32), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("object_bucket", sa.String(length=128), nullable=True),
        sa.Column("object_key", sa.String(length=1024), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("adapter_version", sa.String(length=64), nullable=False),
        _id_column(),
        _created_at_column(),
        sa.CheckConstraint(
            "cik ~ '^[0-9]{10}$' AND cik <> '0000000000'",
            name=op.f("ck_sec_xbrl_sources_cik_valid"),
        ),
        sa.CheckConstraint(
            "source_kind IN ('companyfacts_aggregate', 'raw_inline', 'raw_instance')",
            name=op.f("ck_sec_xbrl_sources_source_kind_supported"),
        ),
        sa.CheckConstraint(
            "(source_kind = 'companyfacts_aggregate' "
            "AND source_url LIKE 'https://data.sec.gov/api/xbrl/companyfacts/CIK%.json' "
            "AND content_type = 'application/json' AND filing_snapshot_id IS NULL "
            "AND object_bucket IS NOT NULL AND object_key IS NOT NULL) OR "
            "(source_kind IN ('raw_inline', 'raw_instance') "
            "AND source_url LIKE 'https://www.sec.gov/Archives/edgar/data/%' "
            "AND content_type IN ('text/html', 'application/xhtml+xml', "
            "'application/xml', 'text/xml') AND filing_snapshot_id IS NOT NULL "
            "AND object_bucket IS NULL AND object_key IS NULL)",
            name=op.f("ck_sec_xbrl_sources_source_boundary_valid"),
        ),
        sa.CheckConstraint(
            "octet_length(content_sha256) = 32",
            name=op.f("ck_sec_xbrl_sources_content_sha256_length"),
        ),
        sa.CheckConstraint(
            "byte_size > 0",
            name=op.f("ck_sec_xbrl_sources_byte_size_positive"),
        ),
        sa.CheckConstraint(
            "source_available_at <= retrieved_at",
            name=op.f("ck_sec_xbrl_sources_availability_order"),
        ),
        sa.ForeignKeyConstraint(
            ["filing_snapshot_id"],
            ["sec_source_snapshots.id"],
            name=op.f("fk_sec_xbrl_sources_filing_snapshot_id_sec_source_snapshots"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sec_xbrl_sources")),
        sa.UniqueConstraint(
            "id",
            "source_kind",
            name=op.f("uq_sec_xbrl_sources_id_source_kind"),
        ),
        sa.UniqueConstraint(
            "source_url",
            "source_version",
            name=op.f("uq_sec_xbrl_sources_source_url_source_version"),
        ),
    )
    op.create_index(
        op.f("ix_sec_xbrl_sources_cik_source_kind_source_available_at"),
        "sec_xbrl_sources",
        ["cik", "source_kind", "source_available_at"],
    )

    op.create_table(
        "sec_xbrl_contexts",
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("raw_context_id", sa.String(length=255), nullable=False),
        sa.Column("entity_identifier", sa.String(length=255), nullable=False),
        sa.Column("period_kind", sa.String(length=16), nullable=False),
        sa.Column("instant", sa.Date(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("dimensions", sa.JSON(), nullable=False),
        _id_column(),
        _created_at_column(),
        sa.CheckConstraint(
            "length(btrim(raw_context_id)) > 0",
            name=op.f("ck_sec_xbrl_contexts_context_id_not_blank"),
        ),
        sa.CheckConstraint(
            "length(btrim(entity_identifier)) > 0",
            name=op.f("ck_sec_xbrl_contexts_entity_not_blank"),
        ),
        *_period_constraints("sec_xbrl_contexts"),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sec_xbrl_sources.id"],
            name=op.f("fk_sec_xbrl_contexts_source_id_sec_xbrl_sources"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sec_xbrl_contexts")),
        sa.UniqueConstraint(
            "id",
            "source_id",
            name=op.f("uq_sec_xbrl_contexts_id_source_id"),
        ),
        sa.UniqueConstraint(
            "source_id",
            "raw_context_id",
            name=op.f("uq_sec_xbrl_contexts_source_id_raw_context_id"),
        ),
    )
    op.create_index(
        op.f("ix_sec_xbrl_contexts_source_id_period_kind"),
        "sec_xbrl_contexts",
        ["source_id", "period_kind"],
    )

    op.create_table(
        "sec_xbrl_facts",
        sa.Column("filing_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("context_id", sa.Uuid(), nullable=True),
        sa.Column("accession", sa.String(length=20), nullable=False),
        sa.Column("taxonomy", sa.String(length=128), nullable=False),
        sa.Column("concept", sa.String(length=256), nullable=False),
        sa.Column("value", sa.String(length=20_000), nullable=False),
        sa.Column("unit", sa.String(length=255), nullable=True),
        sa.Column("period_kind", sa.String(length=16), nullable=False),
        sa.Column("instant", sa.Date(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("filed_date", sa.Date(), nullable=False),
        sa.Column("form", sa.String(length=16), nullable=False),
        sa.Column("raw_context_id", sa.String(length=255), nullable=True),
        sa.Column("dimensions", sa.JSON(), nullable=False),
        sa.Column("decimals", sa.String(length=32), nullable=True),
        sa.Column("scale", sa.Integer(), nullable=True),
        sa.Column("format", sa.String(length=255), nullable=True),
        sa.Column("is_custom", sa.Boolean(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("locator_key", sa.String(length=512), nullable=False),
        _id_column(),
        _created_at_column(),
        sa.CheckConstraint(
            "accession ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'",
            name=op.f("ck_sec_xbrl_facts_accession_valid"),
        ),
        sa.CheckConstraint(
            "form IN ('10-K', '10-K/A', '10-Q', '10-Q/A')",
            name=op.f("ck_sec_xbrl_facts_form_supported"),
        ),
        sa.CheckConstraint(
            "length(btrim(taxonomy)) > 0",
            name=op.f("ck_sec_xbrl_facts_taxonomy_not_blank"),
        ),
        sa.CheckConstraint(
            "length(btrim(concept)) > 0",
            name=op.f("ck_sec_xbrl_facts_concept_not_blank"),
        ),
        sa.CheckConstraint(
            "length(btrim(value)) > 0",
            name=op.f("ck_sec_xbrl_facts_value_not_blank"),
        ),
        sa.CheckConstraint(
            "length(btrim(locator_key)) > 0",
            name=op.f("ck_sec_xbrl_facts_locator_not_blank"),
        ),
        sa.CheckConstraint(
            "ordinal >= 0",
            name=op.f("ck_sec_xbrl_facts_ordinal_nonnegative"),
        ),
        *_period_constraints("sec_xbrl_facts"),
        sa.ForeignKeyConstraint(
            ["filing_id"],
            ["sec_filings.id"],
            name=op.f("fk_sec_xbrl_facts_filing_id_sec_filings"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sec_xbrl_sources.id"],
            name=op.f("fk_sec_xbrl_facts_source_id_sec_xbrl_sources"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["context_id", "source_id"],
            ["sec_xbrl_contexts.id", "sec_xbrl_contexts.source_id"],
            name="fk_sec_xbrl_facts_context_source",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sec_xbrl_facts")),
        sa.UniqueConstraint(
            "source_id",
            "locator_key",
            name=op.f("uq_sec_xbrl_facts_source_id_locator_key"),
        ),
    )
    op.create_index(
        op.f("ix_sec_xbrl_facts_filing_id_taxonomy_concept_filed_date"),
        "sec_xbrl_facts",
        ["filing_id", "taxonomy", "concept", "filed_date"],
    )
    op.create_index(
        op.f("ix_sec_xbrl_facts_source_id_context_id"),
        "sec_xbrl_facts",
        ["source_id", "context_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_sec_xbrl_facts_source_id_context_id"),
        table_name="sec_xbrl_facts",
    )
    op.drop_index(
        op.f("ix_sec_xbrl_facts_filing_id_taxonomy_concept_filed_date"),
        table_name="sec_xbrl_facts",
    )
    op.drop_table("sec_xbrl_facts")
    op.drop_index(
        op.f("ix_sec_xbrl_contexts_source_id_period_kind"),
        table_name="sec_xbrl_contexts",
    )
    op.drop_table("sec_xbrl_contexts")
    op.drop_index(
        op.f("ix_sec_xbrl_sources_cik_source_kind_source_available_at"),
        table_name="sec_xbrl_sources",
    )
    op.drop_table("sec_xbrl_sources")
