"""create Knowledge ingestion acceptance facts

Revision ID: c5d8e1f4a720
Revises: a3c5e7f9b021
Create Date: 2026-08-23 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5d8e1f4a720"
down_revision: str | Sequence[str] | None = "a3c5e7f9b021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id() -> sa.Column:
    return sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False)


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def _updated_at() -> sa.Column:
    return sa.Column(
        "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_file_objects_ready_state_consistent"), "file_objects", type_="check"
    )
    op.drop_constraint(
        op.f("ck_file_objects_kind_payload_consistent"), "file_objects", type_="check"
    )
    op.add_column(
        "file_objects",
        sa.Column(
            "purpose",
            sa.Enum(
                "chat_attachment",
                "knowledge_source",
                name="file_object_purpose",
                native_enum=False,
                length=24,
            ),
            server_default="chat_attachment",
            nullable=False,
        ),
    )
    op.add_column("file_objects", sa.Column("knowledge_base_id", sa.Uuid(), nullable=True))
    op.create_check_constraint(
        op.f("ck_file_objects_purpose"),
        "file_objects",
        "purpose IN ('chat_attachment', 'knowledge_source')",
    )
    op.create_check_constraint(
        op.f("ck_file_objects_purpose_owner_consistent"),
        "file_objects",
        "(purpose = 'chat_attachment' AND knowledge_base_id IS NULL) OR "
        "(purpose = 'knowledge_source' AND knowledge_base_id IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_file_objects_ready_state_consistent"),
        "file_objects",
        "(status = 'ready' AND object_key IS NOT NULL AND detected_media_type IS NOT NULL "
        "AND kind IS NOT NULL AND actual_size IS NOT NULL AND safe_size IS NOT NULL "
        "AND source_sha256 IS NOT NULL AND safe_sha256 IS NOT NULL AND ready_at IS NOT NULL "
        "AND (purpose = 'knowledge_source' OR (parser_version IS NOT NULL "
        "AND sanitizer_version IS NOT NULL)) AND error_code IS NULL) OR status <> 'ready'",
    )
    op.create_check_constraint(
        op.f("ck_file_objects_kind_payload_consistent"),
        "file_objects",
        "purpose = 'knowledge_source' OR "
        "(kind = 'text' AND extracted_text IS NOT NULL AND width IS NULL AND height IS NULL) "
        "OR (kind = 'image' AND extracted_text IS NULL AND width IS NOT NULL "
        "AND height IS NOT NULL) OR kind IS NULL",
    )

    op.create_unique_constraint(op.f("uq_jobs_id_workspace_id"), "jobs", ["id", "workspace_id"])

    op.create_table(
        "knowledge_bases",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "active", "deleted", name="knowledge_base_status", native_enum=False, length=16
            ),
            server_default="active",
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        _id(),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            "length(btrim(name)) > 0", name=op.f("ck_knowledge_bases_name_not_blank")
        ),
        sa.CheckConstraint(
            "status IN ('active', 'deleted')", name=op.f("ck_knowledge_bases_status_supported")
        ),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_knowledge_bases_revision_positive")),
        sa.CheckConstraint(
            "(status = 'deleted' AND deleted_at IS NOT NULL) OR "
            "(status <> 'deleted' AND deleted_at IS NULL)",
            name=op.f("ck_knowledge_bases_deletion_state_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "created_by_user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            name="fk_knowledge_bases_workspace_creator",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_bases")),
        sa.UniqueConstraint("id", "workspace_id", name=op.f("uq_knowledge_bases_id_workspace_id")),
    )
    op.create_index(
        op.f("ix_knowledge_bases_workspace_id_status_updated_at_id"),
        "knowledge_bases",
        ["workspace_id", "status", "updated_at", "id"],
    )
    op.create_foreign_key(
        "fk_file_objects_knowledge_base_workspace",
        "file_objects",
        "knowledge_bases",
        ["knowledge_base_id", "workspace_id"],
        ["id", "workspace_id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "documents",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.Enum("active", "deleted", name="document_status", native_enum=False, length=16),
            server_default="active",
            nullable=False,
        ),
        sa.Column("active_version_id", sa.Uuid(), nullable=True),
        sa.Column(
            "latest_version_number", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        _id(),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint("length(btrim(title)) > 0", name=op.f("ck_documents_title_not_blank")),
        sa.CheckConstraint(
            "status IN ('active', 'deleted')", name=op.f("ck_documents_status_supported")
        ),
        sa.CheckConstraint(
            "latest_version_number >= 1", name=op.f("ck_documents_latest_version_positive")
        ),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_documents_revision_positive")),
        sa.CheckConstraint(
            "(status = 'deleted' AND deleted_at IS NOT NULL) OR "
            "(status <> 'deleted' AND deleted_at IS NULL)",
            name=op.f("ck_documents_deletion_state_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id", "workspace_id"],
            ["knowledge_bases.id", "knowledge_bases.workspace_id"],
            name="fk_documents_knowledge_base_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "created_by_user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            name="fk_documents_workspace_creator",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        sa.UniqueConstraint("id", "workspace_id", name=op.f("uq_documents_id_workspace_id")),
        sa.UniqueConstraint(
            "id",
            "knowledge_base_id",
            "workspace_id",
            name=op.f("uq_documents_id_knowledge_base_id_workspace_id"),
        ),
    )
    op.create_index(
        op.f("ix_documents_workspace_id_knowledge_base_id_status_updated_at_id"),
        "documents",
        ["workspace_id", "knowledge_base_id", "status", "updated_at", "id"],
    )

    op.create_table(
        "document_versions",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("file_object_id", sa.Uuid(), nullable=False),
        sa.Column("ingestion_job_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "validating",
                "parsing",
                "extracting_assets",
                "chunking",
                "embedding",
                "vector_indexing",
                "lexical_indexing",
                "retrying",
                "ready",
                "failed",
                "cancelled",
                "deleting",
                "deleted",
                name="document_version_status",
                native_enum=False,
                length=24,
            ),
            server_default="queued",
            nullable=False,
        ),
        sa.Column(
            "ingestion_schema_version",
            sa.SmallInteger(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("idempotency_key_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("request_fingerprint", sa.LargeBinary(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "queued_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        _id(),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint("version >= 1", name=op.f("ck_document_versions_version_positive")),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_document_versions_revision_positive")),
        sa.CheckConstraint(
            "ingestion_schema_version = 1",
            name=op.f("ck_document_versions_ingestion_schema_version_supported"),
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'validating', 'parsing', 'extracting_assets', 'chunking', "
            "'embedding', 'vector_indexing', 'lexical_indexing', 'retrying', 'ready', "
            "'failed', 'cancelled', 'deleting', 'deleted')",
            name=op.f("ck_document_versions_status_supported"),
        ),
        sa.CheckConstraint(
            "octet_length(idempotency_key_hash) = 32",
            name=op.f("ck_document_versions_idempotency_hash_length"),
        ),
        sa.CheckConstraint(
            "octet_length(request_fingerprint) = 32",
            name=op.f("ck_document_versions_request_hash_length"),
        ),
        sa.CheckConstraint(
            "uploaded_at <= queued_at", name=op.f("ck_document_versions_upload_queue_order")
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "knowledge_base_id", "workspace_id"],
            ["documents.id", "documents.knowledge_base_id", "documents.workspace_id"],
            name="fk_document_versions_document_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["file_object_id", "workspace_id"],
            ["file_objects.id", "file_objects.workspace_id"],
            name="fk_document_versions_file_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_job_id", "workspace_id"],
            ["jobs.id", "jobs.workspace_id"],
            name="fk_document_versions_job_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "created_by_user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            name="fk_document_versions_workspace_creator",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_versions")),
        sa.UniqueConstraint(
            "id", "workspace_id", name=op.f("uq_document_versions_id_workspace_id")
        ),
        sa.UniqueConstraint(
            "id",
            "document_id",
            "workspace_id",
            name=op.f("uq_document_versions_id_document_id_workspace_id"),
        ),
        sa.UniqueConstraint(
            "document_id", "version", name=op.f("uq_document_versions_document_id_version")
        ),
        sa.UniqueConstraint("file_object_id", name=op.f("uq_document_versions_file_object_id")),
        sa.UniqueConstraint("ingestion_job_id", name=op.f("uq_document_versions_ingestion_job_id")),
    )
    op.create_index(
        "uq_document_versions_workspace_idempotency",
        "document_versions",
        ["workspace_id", "idempotency_key_hash"],
        unique=True,
    )
    op.create_index(
        op.f("ix_document_versions_workspace_id_knowledge_base_id_status_updated_at_id"),
        "document_versions",
        ["workspace_id", "knowledge_base_id", "status", "updated_at", "id"],
    )
    op.create_foreign_key(
        "fk_documents_active_version_document_workspace",
        "documents",
        "document_versions",
        ["active_version_id", "id", "workspace_id"],
        ["id", "document_id", "workspace_id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_documents_active_version_document_workspace", "documents", type_="foreignkey"
    )
    op.drop_index(
        op.f("ix_document_versions_workspace_id_knowledge_base_id_status_updated_at_id"),
        table_name="document_versions",
    )
    op.drop_index("uq_document_versions_workspace_idempotency", table_name="document_versions")
    op.drop_table("document_versions")
    op.drop_index(
        op.f("ix_documents_workspace_id_knowledge_base_id_status_updated_at_id"),
        table_name="documents",
    )
    op.drop_table("documents")
    op.drop_constraint(
        "fk_file_objects_knowledge_base_workspace", "file_objects", type_="foreignkey"
    )
    op.drop_index(
        op.f("ix_knowledge_bases_workspace_id_status_updated_at_id"), table_name="knowledge_bases"
    )
    op.drop_table("knowledge_bases")
    op.drop_constraint(op.f("uq_jobs_id_workspace_id"), "jobs", type_="unique")
    op.drop_constraint(
        op.f("ck_file_objects_kind_payload_consistent"), "file_objects", type_="check"
    )
    op.drop_constraint(
        op.f("ck_file_objects_ready_state_consistent"), "file_objects", type_="check"
    )
    op.drop_constraint(
        op.f("ck_file_objects_purpose_owner_consistent"), "file_objects", type_="check"
    )
    op.drop_constraint(op.f("ck_file_objects_purpose"), "file_objects", type_="check")
    op.drop_column("file_objects", "knowledge_base_id")
    op.drop_column("file_objects", "purpose")
    op.create_check_constraint(
        op.f("ck_file_objects_ready_state_consistent"),
        "file_objects",
        "(status = 'ready' AND object_key IS NOT NULL AND detected_media_type IS NOT NULL "
        "AND kind IS NOT NULL AND actual_size IS NOT NULL AND safe_size IS NOT NULL "
        "AND source_sha256 IS NOT NULL AND safe_sha256 IS NOT NULL AND parser_version IS NOT NULL "
        "AND sanitizer_version IS NOT NULL AND ready_at IS NOT NULL AND error_code IS NULL) "
        "OR status <> 'ready'",
    )
    op.create_check_constraint(
        op.f("ck_file_objects_kind_payload_consistent"),
        "file_objects",
        "(kind = 'text' AND extracted_text IS NOT NULL AND width IS NULL AND height IS NULL) "
        "OR (kind = 'image' AND extracted_text IS NULL AND width IS NOT NULL "
        "AND height IS NOT NULL) OR kind IS NULL",
    )
