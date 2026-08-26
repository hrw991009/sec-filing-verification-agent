"""Create versioned embeddings and dual external-index facts.

Revision ID: f7c4a1e9d260
Revises: e6b9d2a7f341
Create Date: 2026-08-24 19:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f7c4a1e9d260"
down_revision: str | Sequence[str] | None = "e6b9d2a7f341"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EMBEDDING_DEFAULT = (
    "jsonb_build_object('provider', 'deterministic-hash', "
    "'model', 'feature-hash-64', 'dimension', 64, 'normalization', 'l2', "
    "'batch_size', 32, 'timeout_seconds', 30, 'version', '1.0.0')"
)
_INDEX_DEFAULT = (
    "jsonb_build_object('index_version', 'knowledge-index-v1', "
    "'milvus_collection', 'knowledge_chunks_v1', "
    "'elasticsearch_index', 'knowledge_chunks_v1')"
)


def upgrade() -> None:
    op.add_column("documents", sa.Column("deletion_job_id", sa.Uuid(), nullable=True))
    op.add_column(
        "documents",
        sa.Column("deletion_error_code", sa.String(length=100), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_documents_deletion_job_workspace"),
        "documents",
        "jobs",
        ["deletion_job_id", "workspace_id"],
        ["id", "workspace_id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(op.f("ck_documents_status_supported"), "documents", type_="check")
    op.drop_constraint(op.f("ck_documents_deletion_state_consistent"), "documents", type_="check")
    op.create_check_constraint(
        op.f("ck_documents_status_supported"),
        "documents",
        "status IN ('active', 'deleting', 'deleted')",
    )
    op.create_check_constraint(
        op.f("ck_documents_deletion_state_consistent"),
        "documents",
        "(status = 'active' AND deleted_at IS NULL AND deletion_job_id IS NULL) OR "
        "(status = 'deleting' AND deleted_at IS NULL AND deletion_job_id IS NOT NULL) OR "
        "(status = 'deleted' AND deleted_at IS NOT NULL)",
    )

    op.add_column(
        "document_versions",
        sa.Column(
            "embedding_config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text(_EMBEDDING_DEFAULT),
            nullable=False,
        ),
    )
    op.add_column(
        "document_versions",
        sa.Column(
            "index_config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text(_INDEX_DEFAULT),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_document_versions_embedding_index_contract"),
        "document_versions",
        "jsonb_typeof(embedding_config) = 'object' AND jsonb_typeof(index_config) = 'object'",
    )

    op.drop_constraint(
        op.f("ck_ingestion_checkpoints_stage_sequence_supported"),
        "ingestion_checkpoints",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_ingestion_checkpoints_stage_sequence_consistent"),
        "ingestion_checkpoints",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_ingestion_checkpoints_stage_sequence_supported"),
        "ingestion_checkpoints",
        "stage_sequence BETWEEN 1 AND 7",
    )
    op.create_check_constraint(
        op.f("ck_ingestion_checkpoints_stage_sequence_consistent"),
        "ingestion_checkpoints",
        "(stage = 'validating' AND stage_sequence = 1) OR "
        "(stage = 'parsing' AND stage_sequence = 2) OR "
        "(stage = 'extracting_assets' AND stage_sequence = 3) OR "
        "(stage = 'chunking' AND stage_sequence = 4) OR "
        "(stage = 'embedding' AND stage_sequence = 5) OR "
        "(stage = 'vector_indexing' AND stage_sequence = 6) OR "
        "(stage = 'lexical_indexing' AND stage_sequence = 7)",
    )

    op.create_table(
        "chunk_embeddings",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("embedding_version", sa.String(length=32), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("normalized", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("content_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("vector", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
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
        sa.CheckConstraint("dimension >= 1", name=op.f("ck_chunk_embeddings_dimension_positive")),
        sa.CheckConstraint(
            "jsonb_typeof(vector) = 'array' AND jsonb_array_length(vector) = dimension",
            name=op.f("ck_chunk_embeddings_vector_dimension_consistent"),
        ),
        sa.CheckConstraint(
            "length(btrim(provider)) > 0",
            name=op.f("ck_chunk_embeddings_provider_not_blank"),
        ),
        sa.CheckConstraint(
            "length(btrim(model)) > 0",
            name=op.f("ck_chunk_embeddings_model_not_blank"),
        ),
        sa.CheckConstraint(
            "length(btrim(embedding_version)) > 0",
            name=op.f("ck_chunk_embeddings_version_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id", "document_version_id", "workspace_id"],
            [
                "document_chunks.id",
                "document_chunks.document_version_id",
                "document_chunks.workspace_id",
            ],
            name=op.f("fk_chunk_embeddings_chunk_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunk_embeddings")),
        sa.UniqueConstraint(
            "chunk_id",
            "document_version_id",
            name=op.f("uq_chunk_embeddings_chunk_id_document_version_id"),
        ),
    )
    op.create_index(
        op.f("ix_chunk_embeddings_workspace_id_document_version_id_chunk_id"),
        "chunk_embeddings",
        ["workspace_id", "document_version_id", "chunk_id"],
        unique=False,
    )

    op.create_table(
        "document_index_records",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("index_version", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=200), nullable=False),
        sa.Column("attempt_count", sa.SmallInteger(), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
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
            "kind IN ('vector', 'lexical')",
            name=op.f("ck_document_index_records_kind_supported"),
        ),
        sa.CheckConstraint(
            "status IN ('succeeded', 'failed')",
            name=op.f("ck_document_index_records_status_supported"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 1",
            name=op.f("ck_document_index_records_attempt_count_positive"),
        ),
        sa.CheckConstraint(
            "length(btrim(index_version)) > 0",
            name=op.f("ck_document_index_records_index_version_not_blank"),
        ),
        sa.CheckConstraint(
            "length(btrim(external_id)) > 0",
            name=op.f("ck_document_index_records_external_id_not_blank"),
        ),
        sa.CheckConstraint(
            "(status = 'succeeded' AND indexed_at IS NOT NULL AND error_code IS NULL) OR "
            "(status = 'failed' AND indexed_at IS NULL AND error_code IS NOT NULL)",
            name=op.f("ck_document_index_records_status_payload_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id", "document_version_id", "workspace_id"],
            [
                "document_chunks.id",
                "document_chunks.document_version_id",
                "document_chunks.workspace_id",
            ],
            name=op.f("fk_document_index_records_chunk_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_index_records")),
        sa.UniqueConstraint(
            "document_version_id",
            "chunk_id",
            "kind",
            "index_version",
            name=op.f("uq_document_index_records_document_version_id_chunk_id_kind_index_version"),
        ),
        sa.UniqueConstraint(
            "kind",
            "external_id",
            name=op.f("uq_document_index_records_kind_external_id"),
        ),
    )
    op.create_index(
        op.f("ix_document_index_records_workspace_id_document_version_id_kind_status"),
        "document_index_records",
        ["workspace_id", "document_version_id", "kind", "status"],
        unique=False,
    )

    op.create_table(
        "document_deletion_targets",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("bucket", sa.String(length=255), nullable=True),
        sa.Column("target_key", sa.String(length=1024), nullable=False),
        sa.Column("attempt_count", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
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
            "kind IN ('vector', 'lexical', 'object', 'object_prefix', 'cache')",
            name=op.f("ck_document_deletion_targets_kind_supported"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'deleted', 'failed')",
            name=op.f("ck_document_deletion_targets_status_supported"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=op.f("ck_document_deletion_targets_attempt_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "length(btrim(target_key)) > 0",
            name=op.f("ck_document_deletion_targets_target_key_not_blank"),
        ),
        sa.CheckConstraint(
            "(kind IN ('object', 'object_prefix') AND bucket IS NOT NULL) OR "
            "(kind NOT IN ('object', 'object_prefix') AND bucket IS NULL)",
            name=op.f("ck_document_deletion_targets_bucket_kind_consistent"),
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND deleted_at IS NULL AND error_code IS NULL) OR "
            "(status = 'failed' AND deleted_at IS NULL AND error_code IS NOT NULL) OR "
            "(status = 'deleted' AND deleted_at IS NOT NULL AND error_code IS NULL)",
            name=op.f("ck_document_deletion_targets_status_payload_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "workspace_id"],
            ["documents.id", "documents.workspace_id"],
            name=op.f("fk_document_deletion_targets_document_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_deletion_targets")),
        sa.UniqueConstraint(
            "document_id",
            "kind",
            "target_key",
            name=op.f("uq_document_deletion_targets_document_id_kind_target_key"),
        ),
    )
    op.create_index(
        op.f("ix_document_deletion_targets_workspace_id_document_id_status_kind"),
        "document_deletion_targets",
        ["workspace_id", "document_id", "status", "kind"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_document_deletion_targets_workspace_id_document_id_status_kind"),
        table_name="document_deletion_targets",
    )
    op.drop_table("document_deletion_targets")
    op.drop_index(
        op.f("ix_document_index_records_workspace_id_document_version_id_kind_status"),
        table_name="document_index_records",
    )
    op.drop_table("document_index_records")
    op.drop_index(
        op.f("ix_chunk_embeddings_workspace_id_document_version_id_chunk_id"),
        table_name="chunk_embeddings",
    )
    op.drop_table("chunk_embeddings")

    op.drop_constraint(
        op.f("ck_ingestion_checkpoints_stage_sequence_consistent"),
        "ingestion_checkpoints",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_ingestion_checkpoints_stage_sequence_supported"),
        "ingestion_checkpoints",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_ingestion_checkpoints_stage_sequence_supported"),
        "ingestion_checkpoints",
        "stage_sequence BETWEEN 1 AND 4",
    )
    op.create_check_constraint(
        op.f("ck_ingestion_checkpoints_stage_sequence_consistent"),
        "ingestion_checkpoints",
        "(stage = 'validating' AND stage_sequence = 1) OR "
        "(stage = 'parsing' AND stage_sequence = 2) OR "
        "(stage = 'extracting_assets' AND stage_sequence = 3) OR "
        "(stage = 'chunking' AND stage_sequence = 4)",
    )

    op.drop_constraint(
        op.f("ck_document_versions_embedding_index_contract"),
        "document_versions",
        type_="check",
    )
    op.drop_column("document_versions", "index_config")
    op.drop_column("document_versions", "embedding_config")

    op.drop_constraint(op.f("ck_documents_deletion_state_consistent"), "documents", type_="check")
    op.drop_constraint(op.f("ck_documents_status_supported"), "documents", type_="check")
    op.create_check_constraint(
        op.f("ck_documents_status_supported"),
        "documents",
        "status IN ('active', 'deleted')",
    )
    op.create_check_constraint(
        op.f("ck_documents_deletion_state_consistent"),
        "documents",
        "(status = 'deleted' AND deleted_at IS NOT NULL) OR "
        "(status <> 'deleted' AND deleted_at IS NULL)",
    )
    op.drop_constraint(
        op.f("fk_documents_deletion_job_workspace"),
        "documents",
        type_="foreignkey",
    )
    op.drop_column("documents", "deletion_error_code")
    op.drop_column("documents", "deletion_job_id")
