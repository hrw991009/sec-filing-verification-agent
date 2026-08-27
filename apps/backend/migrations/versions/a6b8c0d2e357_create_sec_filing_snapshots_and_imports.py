"""Create immutable SEC filing snapshots and Workspace imports.

Revision ID: a6b8c0d2e357
Revises: f5a7b9c1d246
Create Date: 2026-08-26 00:00:00.000000
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "a6b8c0d2e357"
down_revision: str | Sequence[str] | None = "f5a7b9c1d246"
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
        "sec_filing_documents",
        sa.Column("filing_id", sa.Uuid(), nullable=False),
        sa.Column("accession", sa.String(length=20), nullable=False),
        sa.Column("document_kind", sa.String(length=32), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("current_snapshot_id", sa.Uuid(), nullable=True),
        _id_column(),
        _created_at_column(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "document_kind IN ('complete_submission', 'primary_document', "
            "'xbrl_instance', 'xbrl_attachment')",
            name=op.f("ck_sec_filing_documents_document_kind_supported"),
        ),
        sa.CheckConstraint(
            "accession ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'",
            name=op.f("ck_sec_filing_documents_accession_valid"),
        ),
        sa.CheckConstraint(
            "length(btrim(filename)) > 0",
            name=op.f("ck_sec_filing_documents_filename_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["filing_id"],
            ["sec_filings.id"],
            name=op.f("fk_sec_filing_documents_filing_id_sec_filings"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sec_filing_documents")),
        sa.UniqueConstraint(
            "id",
            "filing_id",
            name=op.f("uq_sec_filing_documents_id_filing_id"),
        ),
        sa.UniqueConstraint(
            "filing_id",
            "document_kind",
            "filename",
            name=op.f("uq_sec_filing_documents_filing_id_document_kind_filename"),
        ),
    )
    op.create_index(
        op.f("ix_sec_filing_documents_filing_id_document_kind"),
        "sec_filing_documents",
        ["filing_id", "document_kind"],
    )

    op.create_table(
        "sec_source_snapshots",
        sa.Column("filing_document_id", sa.Uuid(), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
        sa.Column("source_version", sa.String(length=128), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("content_sha256", sa.LargeBinary(length=32), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("object_bucket", sa.String(length=128), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("adapter_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("anomaly_code", sa.String(length=100), nullable=True),
        _id_column(),
        _created_at_column(),
        sa.CheckConstraint(
            "source_url LIKE 'https://www.sec.gov/Archives/edgar/data/%'",
            name=op.f("ck_sec_source_snapshots_source_url_allowlisted"),
        ),
        sa.CheckConstraint(
            "content_type IN ('text/plain', 'text/html', 'application/xhtml+xml', "
            "'application/xml', 'text/xml')",
            name=op.f("ck_sec_source_snapshots_content_type_supported"),
        ),
        sa.CheckConstraint(
            "octet_length(content_sha256) = 32",
            name=op.f("ck_sec_source_snapshots_content_sha256_length"),
        ),
        sa.CheckConstraint(
            "byte_size > 0",
            name=op.f("ck_sec_source_snapshots_byte_size_positive"),
        ),
        sa.CheckConstraint(
            "length(btrim(object_bucket)) > 0",
            name=op.f("ck_sec_source_snapshots_object_bucket_not_blank"),
        ),
        sa.CheckConstraint(
            "length(btrim(object_key)) > 0",
            name=op.f("ck_sec_source_snapshots_object_key_not_blank"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'quarantined')",
            name=op.f("ck_sec_source_snapshots_status_supported"),
        ),
        sa.CheckConstraint(
            "(status = 'quarantined') = (anomaly_code IS NOT NULL)",
            name=op.f("ck_sec_source_snapshots_anomaly_state_consistent"),
        ),
        sa.CheckConstraint(
            "source_available_at <= retrieved_at",
            name=op.f("ck_sec_source_snapshots_availability_order"),
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to >= source_available_at",
            name=op.f("ck_sec_source_snapshots_validity_order"),
        ),
        sa.ForeignKeyConstraint(
            ["filing_document_id"],
            ["sec_filing_documents.id"],
            name=op.f("fk_sec_source_snapshots_filing_document_id_sec_filing_documents"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sec_source_snapshots")),
        sa.UniqueConstraint(
            "id",
            "filing_document_id",
            name=op.f("uq_sec_source_snapshots_id_filing_document_id"),
        ),
        sa.UniqueConstraint(
            "filing_document_id",
            "source_version",
            name=op.f("uq_sec_source_snapshots_filing_document_id_source_version"),
        ),
        sa.UniqueConstraint(
            "filing_document_id",
            "content_sha256",
            name=op.f("uq_sec_source_snapshots_filing_document_id_content_sha256"),
        ),
    )
    op.create_index(
        op.f("ix_sec_source_snapshots_filing_document_id_status_source_available_at"),
        "sec_source_snapshots",
        ["filing_document_id", "status", "source_available_at"],
    )
    op.create_foreign_key(
        "fk_sec_filing_documents_current_snapshot",
        "sec_filing_documents",
        "sec_source_snapshots",
        ["current_snapshot_id", "id"],
        ["id", "filing_document_id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "workspace_sec_imports",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("filing_id", sa.Uuid(), nullable=False),
        sa.Column("accession", sa.String(length=20), nullable=False),
        sa.Column("primary_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("complete_submission_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("file_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("ingestion_job_id", sa.Uuid(), nullable=False),
        _id_column(),
        _created_at_column(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "accession ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'",
            name=op.f("ck_workspace_sec_imports_accession_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_workspace_sec_imports_workspace_id_workspaces"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "created_by_user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            name="fk_workspace_sec_imports_creator",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["filing_id"],
            ["sec_filings.id"],
            name=op.f("fk_workspace_sec_imports_filing_id_sec_filings"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["primary_snapshot_id"],
            ["sec_source_snapshots.id"],
            name=op.f("fk_workspace_sec_imports_primary_snapshot_id_sec_source_snapshots"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["complete_submission_snapshot_id"],
            ["sec_source_snapshots.id"],
            name=(
                op.f(
                    "fk_workspace_sec_imports_complete_submission_snapshot_id_sec_source_snapshots"
                )
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id", "workspace_id"],
            ["knowledge_bases.id", "knowledge_bases.workspace_id"],
            name="fk_workspace_sec_imports_knowledge_base",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["file_id", "workspace_id"],
            ["file_objects.id", "file_objects.workspace_id"],
            name="fk_workspace_sec_imports_file",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "knowledge_base_id", "workspace_id"],
            ["documents.id", "documents.knowledge_base_id", "documents.workspace_id"],
            name="fk_workspace_sec_imports_document",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id", "document_id", "workspace_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.workspace_id",
            ],
            name="fk_workspace_sec_imports_document_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_job_id", "workspace_id"],
            ["jobs.id", "jobs.workspace_id"],
            name="fk_workspace_sec_imports_job",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_sec_imports")),
        sa.UniqueConstraint(
            "id",
            "workspace_id",
            name=op.f("uq_workspace_sec_imports_id_workspace_id"),
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "primary_snapshot_id",
            "knowledge_base_id",
            name=op.f(
                "uq_workspace_sec_imports_workspace_id_primary_snapshot_id_knowledge_base_id"
            ),
        ),
    )
    op.create_index(
        op.f("ix_workspace_sec_imports_workspace_id_accession_updated_at"),
        "workspace_sec_imports",
        ["workspace_id", "accession", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_workspace_sec_imports_workspace_id_accession_updated_at"),
        table_name="workspace_sec_imports",
    )
    op.drop_table("workspace_sec_imports")
    op.drop_constraint(
        "fk_sec_filing_documents_current_snapshot",
        "sec_filing_documents",
        type_="foreignkey",
    )
    op.drop_index(
        op.f("ix_sec_source_snapshots_filing_document_id_status_source_available_at"),
        table_name="sec_source_snapshots",
    )
    op.drop_table("sec_source_snapshots")
    op.drop_index(
        op.f("ix_sec_filing_documents_filing_id_document_kind"),
        table_name="sec_filing_documents",
    )
    op.drop_table("sec_filing_documents")
