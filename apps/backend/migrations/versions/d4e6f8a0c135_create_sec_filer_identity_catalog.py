"""Create the canonical SEC filer identity catalog.

Revision ID: d4e6f8a0c135
Revises: c2e6f8a0b431
Create Date: 2026-08-26 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e6f8a0c135"
down_revision: str | Sequence[str] | None = "c2e6f8a0b431"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SOURCE_URL = "https://www.sec.gov/files/company_tickers.json"


def upgrade() -> None:
    op.create_table(
        "sec_filers",
        sa.Column("cik", sa.String(length=10), nullable=False),
        sa.Column("canonical_name", sa.String(length=500), nullable=False),
        sa.Column("normalized_name", sa.String(length=500), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("source_version", sa.String(length=128), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
        sa.Column("source_content_sha256", sa.LargeBinary(length=32), nullable=False),
        sa.Column("source_observed_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "cik ~ '^[0-9]{10}$' AND cik <> '0000000000'",
            name=op.f("ck_sec_filers_cik_valid"),
        ),
        sa.CheckConstraint(
            "length(btrim(canonical_name)) > 0",
            name=op.f("ck_sec_filers_canonical_name_not_blank"),
        ),
        sa.CheckConstraint(
            "length(btrim(normalized_name)) > 0",
            name=op.f("ck_sec_filers_normalized_name_not_blank"),
        ),
        sa.CheckConstraint(
            "source_kind = 'company_tickers'",
            name=op.f("ck_sec_filers_source_kind_supported"),
        ),
        sa.CheckConstraint(
            f"source_url = '{_SOURCE_URL}'",
            name=op.f("ck_sec_filers_source_url_allowlisted"),
        ),
        sa.CheckConstraint(
            "octet_length(source_content_sha256) = 32",
            name=op.f("ck_sec_filers_source_content_sha256_length"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sec_filers")),
        sa.UniqueConstraint("cik", name=op.f("uq_sec_filers_cik")),
    )
    op.create_index(
        op.f("ix_sec_filers_normalized_name_cik"),
        "sec_filers",
        ["normalized_name", "cik"],
    )

    op.create_table(
        "sec_filer_aliases",
        sa.Column("filer_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("display_value", sa.String(length=500), nullable=False),
        sa.Column("normalized_value", sa.String(length=500), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("source_version", sa.String(length=128), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
        sa.Column("source_content_sha256", sa.LargeBinary(length=32), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('name', 'ticker')",
            name=op.f("ck_sec_filer_aliases_kind_supported"),
        ),
        sa.CheckConstraint(
            "length(btrim(display_value)) > 0",
            name=op.f("ck_sec_filer_aliases_display_value_not_blank"),
        ),
        sa.CheckConstraint(
            "length(btrim(normalized_value)) > 0",
            name=op.f("ck_sec_filer_aliases_normalized_value_not_blank"),
        ),
        sa.CheckConstraint(
            "source_kind = 'company_tickers'",
            name=op.f("ck_sec_filer_aliases_source_kind_supported"),
        ),
        sa.CheckConstraint(
            f"source_url = '{_SOURCE_URL}'",
            name=op.f("ck_sec_filer_aliases_source_url_allowlisted"),
        ),
        sa.CheckConstraint(
            "octet_length(source_content_sha256) = 32",
            name=op.f("ck_sec_filer_aliases_source_content_sha256_length"),
        ),
        sa.CheckConstraint(
            "valid_from IS NULL OR valid_to IS NULL OR valid_to > valid_from",
            name=op.f("ck_sec_filer_aliases_validity_order"),
        ),
        sa.ForeignKeyConstraint(
            ["filer_id"],
            ["sec_filers.id"],
            name=op.f("fk_sec_filer_aliases_filer_id_sec_filers"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sec_filer_aliases")),
        sa.UniqueConstraint(
            "filer_id",
            "kind",
            "normalized_value",
            "source_version",
            name=op.f("uq_sec_filer_aliases_filer_id_kind_normalized_value_source_version"),
        ),
    )
    op.create_index(
        op.f("ix_sec_filer_aliases_kind_normalized_value_valid_to"),
        "sec_filer_aliases",
        ["kind", "normalized_value", "valid_to"],
    )
    op.create_index(
        op.f("ix_sec_filer_aliases_filer_id_valid_to"),
        "sec_filer_aliases",
        ["filer_id", "valid_to"],
    )

    op.create_table(
        "sec_catalog_syncs",
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("source_version", sa.String(length=128), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
        sa.Column("content_sha256", sa.LargeBinary(length=32), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("filer_count", sa.Integer(), nullable=False),
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
        sa.Column(
            "committed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_kind = 'company_tickers'",
            name=op.f("ck_sec_catalog_syncs_source_kind_supported"),
        ),
        sa.CheckConstraint(
            f"source_url = '{_SOURCE_URL}'",
            name=op.f("ck_sec_catalog_syncs_source_url_allowlisted"),
        ),
        sa.CheckConstraint(
            "octet_length(content_sha256) = 32",
            name=op.f("ck_sec_catalog_syncs_content_sha256_length"),
        ),
        sa.CheckConstraint(
            "filer_count > 0",
            name=op.f("ck_sec_catalog_syncs_filer_count_positive"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sec_catalog_syncs")),
        sa.UniqueConstraint(
            "source_kind",
            "source_version",
            name=op.f("uq_sec_catalog_syncs_source_kind_source_version"),
        ),
    )


def downgrade() -> None:
    op.drop_table("sec_catalog_syncs")
    op.drop_index(
        op.f("ix_sec_filer_aliases_filer_id_valid_to"),
        table_name="sec_filer_aliases",
    )
    op.drop_index(
        op.f("ix_sec_filer_aliases_kind_normalized_value_valid_to"),
        table_name="sec_filer_aliases",
    )
    op.drop_table("sec_filer_aliases")
    op.drop_index(op.f("ix_sec_filers_normalized_name_cik"), table_name="sec_filers")
    op.drop_table("sec_filers")
