"""Create SEC filing point-in-time source and coverage facts.

Revision ID: f5a7b9c1d246
Revises: d4e6f8a0c135
Create Date: 2026-08-26 00:00:00.000000
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "f5a7b9c1d246"
down_revision: str | Sequence[str] | None = "d4e6f8a0c135"
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


def upgrade() -> None:
    op.create_table(
        "sec_submission_sources",
        sa.Column("cik", sa.String(length=10), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("source_name", sa.String(length=128), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
        sa.Column("source_version", sa.String(length=128), nullable=False),
        sa.Column("content_sha256", sa.LargeBinary(length=32), nullable=False),
        sa.Column("object_bucket", sa.String(length=128), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("filing_from", sa.Date(), nullable=True),
        sa.Column("filing_to", sa.Date(), nullable=True),
        _id_column(),
        _created_at_column(),
        sa.CheckConstraint(
            "cik ~ '^[0-9]{10}$' AND cik <> '0000000000'",
            name=op.f("ck_sec_submission_sources_cik_valid"),
        ),
        sa.CheckConstraint(
            "source_kind IN ('submissions_current', 'submissions_supplemental')",
            name=op.f("ck_sec_submission_sources_source_kind_supported"),
        ),
        sa.CheckConstraint(
            "source_url LIKE 'https://data.sec.gov/submissions/CIK%.json'",
            name=op.f("ck_sec_submission_sources_source_url_allowlisted"),
        ),
        sa.CheckConstraint(
            "octet_length(content_sha256) = 32",
            name=op.f("ck_sec_submission_sources_content_sha256_length"),
        ),
        sa.CheckConstraint(
            "length(btrim(object_bucket)) > 0",
            name=op.f("ck_sec_submission_sources_object_bucket_not_blank"),
        ),
        sa.CheckConstraint(
            "length(btrim(object_key)) > 0",
            name=op.f("ck_sec_submission_sources_object_key_not_blank"),
        ),
        sa.CheckConstraint(
            "(filing_from IS NULL) = (filing_to IS NULL)",
            name=op.f("ck_sec_submission_sources_coverage_paired"),
        ),
        sa.CheckConstraint(
            "filing_from IS NULL OR filing_to >= filing_from",
            name=op.f("ck_sec_submission_sources_coverage_order"),
        ),
        sa.CheckConstraint(
            "source_available_at <= retrieved_at",
            name=op.f("ck_sec_submission_sources_availability_order"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sec_submission_sources")),
        sa.UniqueConstraint(
            "source_url",
            "source_version",
            name=op.f("uq_sec_submission_sources_source_url_source_version"),
        ),
    )
    op.create_index(
        op.f("ix_sec_submission_sources_cik_source_kind_source_available_at"),
        "sec_submission_sources",
        ["cik", "source_kind", "source_available_at"],
    )

    op.create_table(
        "sec_filing_observations",
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("cik", sa.String(length=10), nullable=False),
        sa.Column("accession", sa.String(length=20), nullable=False),
        sa.Column("form", sa.String(length=16), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("filed_date", sa.Date(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("primary_document", sa.String(length=255), nullable=False),
        _id_column(),
        _created_at_column(),
        sa.CheckConstraint(
            "cik ~ '^[0-9]{10}$' AND cik <> '0000000000'",
            name=op.f("ck_sec_filing_observations_cik_valid"),
        ),
        sa.CheckConstraint(
            "accession ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'",
            name=op.f("ck_sec_filing_observations_accession_valid"),
        ),
        sa.CheckConstraint(
            "form IN ('10-K', '10-K/A', '10-Q', '10-Q/A')",
            name=op.f("ck_sec_filing_observations_form_supported"),
        ),
        sa.CheckConstraint(
            "filed_date >= report_date",
            name=op.f("ck_sec_filing_observations_filing_date_order"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sec_submission_sources.id"],
            name=op.f("fk_sec_filing_observations_source_id_sec_submission_sources"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sec_filing_observations")),
        sa.UniqueConstraint(
            "source_id",
            "accession",
            name=op.f("uq_sec_filing_observations_source_id_accession"),
        ),
    )
    op.create_index(
        op.f("ix_sec_filing_observations_cik_report_date_accepted_at"),
        "sec_filing_observations",
        ["cik", "report_date", "accepted_at"],
    )

    op.create_table(
        "sec_filings",
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("cik", sa.String(length=10), nullable=False),
        sa.Column("accession", sa.String(length=20), nullable=False),
        sa.Column("form", sa.String(length=16), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("filed_date", sa.Date(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("public_available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("visibility_policy_version", sa.String(length=64), nullable=False),
        sa.Column("primary_document", sa.String(length=255), nullable=False),
        sa.Column("amendment_relation_status", sa.String(length=32), nullable=False),
        sa.Column("base_accession", sa.String(length=20), nullable=True),
        _id_column(),
        _created_at_column(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "cik ~ '^[0-9]{10}$' AND cik <> '0000000000'",
            name=op.f("ck_sec_filings_cik_valid"),
        ),
        sa.CheckConstraint(
            "accession ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'",
            name=op.f("ck_sec_filings_accession_valid"),
        ),
        sa.CheckConstraint(
            "form IN ('10-K', '10-K/A', '10-Q', '10-Q/A')",
            name=op.f("ck_sec_filings_form_supported"),
        ),
        sa.CheckConstraint(
            "amendment_relation_status IN ('not_amendment', 'resolved', 'unresolved')",
            name=op.f("ck_sec_filings_amendment_status_supported"),
        ),
        sa.CheckConstraint(
            "(amendment_relation_status = 'resolved') = (base_accession IS NOT NULL)",
            name=op.f("ck_sec_filings_base_accession_consistent"),
        ),
        sa.CheckConstraint(
            "visibility_policy_version = 'sec-acceptance-source-v1'",
            name=op.f("ck_sec_filings_visibility_policy_supported"),
        ),
        sa.CheckConstraint(
            "public_available_at = accepted_at",
            name=op.f("ck_sec_filings_public_availability_policy"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sec_submission_sources.id"],
            name=op.f("fk_sec_filings_source_id_sec_submission_sources"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sec_filings")),
        sa.UniqueConstraint("accession", name=op.f("uq_sec_filings_accession")),
    )
    op.create_index(
        op.f("ix_sec_filings_cik_report_date_accepted_at"),
        "sec_filings",
        ["cik", "report_date", "accepted_at"],
    )

    op.create_table(
        "sec_filing_coverage_manifests",
        sa.Column("coverage_version", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("cik", sa.String(length=10), nullable=False),
        sa.Column("allowed_forms", sa.JSON(), nullable=False),
        sa.Column("report_period_start", sa.Date(), nullable=False),
        sa.Column("report_period_end", sa.Date(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("amendment_policy", sa.String(length=64), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False),
        _id_column(),
        _created_at_column(),
        sa.CheckConstraint(
            "cik ~ '^[0-9]{10}$' AND cik <> '0000000000'",
            name=op.f("ck_sec_filing_coverage_manifests_cik_valid"),
        ),
        sa.CheckConstraint(
            "schema_version = 1",
            name=op.f("ck_sec_filing_coverage_manifests_schema_version_supported"),
        ),
        sa.CheckConstraint(
            "report_period_end >= report_period_start",
            name=op.f("ck_sec_filing_coverage_manifests_period_order"),
        ),
        sa.CheckConstraint(
            "amendment_policy IN ('as_filed', 'latest_amendment_known_by_as_of')",
            name=op.f("ck_sec_filing_coverage_manifests_amendment_policy_supported"),
        ),
        sa.CheckConstraint(
            "source_count > 0",
            name=op.f("ck_sec_filing_coverage_manifests_source_count_positive"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sec_filing_coverage_manifests")),
        sa.UniqueConstraint(
            "coverage_version",
            name=op.f("uq_sec_filing_coverage_manifests_coverage_version"),
        ),
    )

    op.create_table(
        "sec_filing_coverage_sources",
        sa.Column("coverage_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        _id_column(),
        _created_at_column(),
        sa.ForeignKeyConstraint(
            ["coverage_id"],
            ["sec_filing_coverage_manifests.id"],
            name=op.f("fk_sec_filing_coverage_sources_coverage_id_sec_filing_coverage_manifests"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sec_submission_sources.id"],
            name=op.f("fk_sec_filing_coverage_sources_source_id_sec_submission_sources"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sec_filing_coverage_sources")),
        sa.UniqueConstraint(
            "coverage_id",
            "source_id",
            name=op.f("uq_sec_filing_coverage_sources_coverage_id_source_id"),
        ),
    )


def downgrade() -> None:
    op.drop_table("sec_filing_coverage_sources")
    op.drop_table("sec_filing_coverage_manifests")
    op.drop_index(op.f("ix_sec_filings_cik_report_date_accepted_at"), table_name="sec_filings")
    op.drop_table("sec_filings")
    op.drop_index(
        op.f("ix_sec_filing_observations_cik_report_date_accepted_at"),
        table_name="sec_filing_observations",
    )
    op.drop_table("sec_filing_observations")
    op.drop_index(
        op.f("ix_sec_submission_sources_cik_source_kind_source_available_at"),
        table_name="sec_submission_sources",
    )
    op.drop_table("sec_submission_sources")
