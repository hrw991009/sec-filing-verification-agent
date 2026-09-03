"""Create SEC bulk snapshot and post-watermark gap ledger.

Revision ID: b3e5f7a9c024
Revises: a2d4f6b8c913
Create Date: 2026-09-01 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3e5f7a9c024"
down_revision: str | Sequence[str] | None = "a2d4f6b8c913"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sec_bulk_sources",
        sa.Column("dataset_kind", sa.String(length=32), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
        sa.Column("source_version", sa.String(length=128), nullable=False),
        sa.Column("content_sha256", sa.LargeBinary(length=32), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("object_bucket", sa.String(length=128), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bulk_published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("coverage_through", sa.DateTime(timezone=True), nullable=False),
        sa.Column("adapter_version", sa.String(length=128), nullable=False),
        sa.Column("watermark_policy_version", sa.String(length=128), nullable=False),
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "dataset_kind IN ('submissions', 'companyfacts')",
            name=op.f("ck_sec_bulk_sources_dataset_kind_supported"),
        ),
        sa.CheckConstraint(
            "(dataset_kind = 'submissions' AND source_url = "
            "'https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip') OR "
            "(dataset_kind = 'companyfacts' AND source_url = "
            "'https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip')",
            name=op.f("ck_sec_bulk_sources_source_url_allowlisted"),
        ),
        sa.CheckConstraint(
            "octet_length(content_sha256) = 32",
            name=op.f("ck_sec_bulk_sources_content_sha256_length"),
        ),
        sa.CheckConstraint(
            "byte_size > 0",
            name=op.f("ck_sec_bulk_sources_byte_size_positive"),
        ),
        sa.CheckConstraint(
            "length(btrim(object_bucket)) > 0",
            name=op.f("ck_sec_bulk_sources_object_bucket_not_blank"),
        ),
        sa.CheckConstraint(
            "length(btrim(object_key)) > 0",
            name=op.f("ck_sec_bulk_sources_object_key_not_blank"),
        ),
        sa.CheckConstraint(
            "coverage_through < bulk_published_at AND bulk_published_at <= retrieved_at",
            name=op.f("ck_sec_bulk_sources_watermark_order"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sec_bulk_sources")),
        sa.UniqueConstraint(
            "source_url",
            "source_version",
            name=op.f("uq_sec_bulk_sources_source_url_source_version"),
        ),
    )
    op.create_index(
        op.f("ix_sec_bulk_sources_dataset_kind_bulk_published_at"),
        "sec_bulk_sources",
        ["dataset_kind", "bulk_published_at"],
    )

    op.create_table(
        "sec_bulk_entries",
        sa.Column("bulk_source_id", sa.Uuid(), nullable=False),
        sa.Column("cik", sa.String(length=10), nullable=False),
        sa.Column("entry_name", sa.String(length=32), nullable=False),
        sa.Column("content_sha256", sa.LargeBinary(length=32), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "cik ~ '^[0-9]{10}$' AND cik <> '0000000000'",
            name=op.f("ck_sec_bulk_entries_cik_valid"),
        ),
        sa.CheckConstraint(
            "entry_name = 'CIK' || cik || '.json'",
            name=op.f("ck_sec_bulk_entries_entry_name_valid"),
        ),
        sa.CheckConstraint(
            "octet_length(content_sha256) = 32",
            name=op.f("ck_sec_bulk_entries_content_sha256_length"),
        ),
        sa.CheckConstraint(
            "byte_size > 0",
            name=op.f("ck_sec_bulk_entries_byte_size_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["bulk_source_id"],
            ["sec_bulk_sources.id"],
            name=op.f("fk_sec_bulk_entries_bulk_source_id_sec_bulk_sources"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sec_bulk_entries")),
        sa.UniqueConstraint(
            "bulk_source_id",
            "cik",
            name=op.f("uq_sec_bulk_entries_bulk_source_id_cik"),
        ),
    )
    op.create_index(
        op.f("ix_sec_bulk_entries_cik_bulk_source_id"),
        "sec_bulk_entries",
        ["cik", "bulk_source_id"],
    )

    op.create_table(
        "sec_bulk_gap_closures",
        sa.Column("bulk_entry_id", sa.Uuid(), nullable=False),
        sa.Column("coverage_from_exclusive", sa.DateTime(timezone=True), nullable=False),
        sa.Column("gap_observed_through", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "coverage_from_exclusive < gap_observed_through",
            name=op.f("ck_sec_bulk_gap_closures_gap_order"),
        ),
        sa.ForeignKeyConstraint(
            ["bulk_entry_id"],
            ["sec_bulk_entries.id"],
            name=op.f("fk_sec_bulk_gap_closures_bulk_entry_id_sec_bulk_entries"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sec_bulk_gap_closures")),
        sa.UniqueConstraint(
            "bulk_entry_id",
            "gap_observed_through",
            name=op.f("uq_sec_bulk_gap_closures_bulk_entry_id_gap_observed_through"),
        ),
    )
    op.create_index(
        op.f("ix_sec_bulk_gap_closures_bulk_entry_id_gap_observed_through"),
        "sec_bulk_gap_closures",
        ["bulk_entry_id", "gap_observed_through"],
    )

    op.create_table(
        "sec_bulk_gap_sources",
        sa.Column("gap_closure_id", sa.Uuid(), nullable=False),
        sa.Column("source_kind", sa.String(length=64), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
        sa.Column("source_version", sa.String(length=128), nullable=False),
        sa.Column("content_sha256", sa.LargeBinary(length=32), nullable=False),
        sa.Column("source_available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_url LIKE 'https://data.sec.gov/%'",
            name=op.f("ck_sec_bulk_gap_sources_source_url_allowlisted"),
        ),
        sa.CheckConstraint(
            "octet_length(content_sha256) = 32",
            name=op.f("ck_sec_bulk_gap_sources_content_sha256_length"),
        ),
        sa.CheckConstraint(
            "source_available_at <= retrieved_at",
            name=op.f("ck_sec_bulk_gap_sources_availability_order"),
        ),
        sa.ForeignKeyConstraint(
            ["gap_closure_id"],
            ["sec_bulk_gap_closures.id"],
            name=op.f("fk_sec_bulk_gap_sources_gap_closure_id_sec_bulk_gap_closures"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sec_bulk_gap_sources")),
        sa.UniqueConstraint(
            "gap_closure_id",
            "source_url",
            "source_version",
            name=op.f("uq_sec_bulk_gap_sources_gap_closure_id_source_url_source_version"),
        ),
    )


def downgrade() -> None:
    op.drop_table("sec_bulk_gap_sources")
    op.drop_index(
        op.f("ix_sec_bulk_gap_closures_bulk_entry_id_gap_observed_through"),
        table_name="sec_bulk_gap_closures",
    )
    op.drop_table("sec_bulk_gap_closures")
    op.drop_index(
        op.f("ix_sec_bulk_entries_cik_bulk_source_id"),
        table_name="sec_bulk_entries",
    )
    op.drop_table("sec_bulk_entries")
    op.drop_index(
        op.f("ix_sec_bulk_sources_dataset_kind_bulk_published_at"),
        table_name="sec_bulk_sources",
    )
    op.drop_table("sec_bulk_sources")
