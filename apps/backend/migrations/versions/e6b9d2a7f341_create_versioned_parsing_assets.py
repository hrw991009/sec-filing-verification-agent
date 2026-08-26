"""create versioned parsing assets and fenced stage checkpoints

Revision ID: e6b9d2a7f341
Revises: c5d8e1f4a720
Create Date: 2026-08-24 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e6b9d2a7f341"
down_revision: str | Sequence[str] | None = "c5d8e1f4a720"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_PARSER_CONFIG = sa.text(
    "jsonb_build_object("
    "'budget', jsonb_build_object("
    "'max_input_bytes', 26214400, "
    "'max_output_bytes', 67108864, "
    "'max_page_image_pixels', 24000000, "
    "'max_pages', 250, "
    "'max_text_characters', 5000000, "
    "'timeout_seconds', 1200), "
    "'ocr_render_dpi', 144, "
    "'schema_version', 1)"
)
_CHUNKER_CONFIG = sa.text("jsonb_build_object('max_characters', 1200, 'overlap_characters', 120)")


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
        op.f("uq_document_versions_file_object_id"),
        "document_versions",
        type_="unique",
    )
    op.drop_constraint(
        op.f("ck_document_versions_status_supported"), "document_versions", type_="check"
    )
    op.add_column(
        "document_versions",
        sa.Column(
            "parser_name",
            sa.String(length=64),
            server_default="pdfplumber-rapidocr",
            nullable=False,
        ),
    )
    op.add_column(
        "document_versions",
        sa.Column("parser_version", sa.String(length=32), server_default="1.0.0", nullable=False),
    )
    op.add_column(
        "document_versions",
        sa.Column("parser_schema_version", sa.SmallInteger(), server_default="1", nullable=False),
    )
    op.add_column(
        "document_versions",
        sa.Column(
            "parser_config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=_PARSER_CONFIG,
            nullable=False,
        ),
    )
    op.add_column(
        "document_versions",
        sa.Column(
            "chunker_name",
            sa.String(length=64),
            server_default="bounded-page-chunker",
            nullable=False,
        ),
    )
    op.add_column(
        "document_versions",
        sa.Column("chunker_version", sa.String(length=32), server_default="1.0.0", nullable=False),
    )
    op.add_column(
        "document_versions",
        sa.Column(
            "chunker_config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=_CHUNKER_CONFIG,
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_document_versions_status_supported"),
        "document_versions",
        "status IN ('queued', 'validating', 'parsing', 'extracting_assets', 'chunking', "
        "'parsed', 'embedding', 'vector_indexing', 'lexical_indexing', 'retrying', 'ready', "
        "'failed', 'cancelled', 'deleting', 'deleted')",
    )
    op.create_check_constraint(
        op.f("ck_document_versions_parser_contract"),
        "document_versions",
        "length(btrim(parser_name)) > 0 AND length(btrim(parser_version)) > 0 "
        "AND parser_schema_version = 1 AND jsonb_typeof(parser_config) = 'object'",
    )
    op.create_check_constraint(
        op.f("ck_document_versions_chunker_contract"),
        "document_versions",
        "length(btrim(chunker_name)) > 0 AND length(btrim(chunker_version)) > 0 "
        "AND jsonb_typeof(chunker_config) = 'object'",
    )

    op.create_table(
        "ingestion_checkpoints",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("ingestion_job_id", sa.Uuid(), nullable=False),
        sa.Column(
            "stage",
            sa.Enum(
                "validating",
                "parsing",
                "extracting_assets",
                "chunking",
                name="ingestion_checkpoint_stage",
                native_enum=False,
                length=24,
            ),
            nullable=False,
        ),
        sa.Column("stage_sequence", sa.SmallInteger(), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("attempt_count", sa.SmallInteger(), nullable=False),
        sa.Column("stage_idempotency_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("input_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("output_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("output_bucket", sa.String(length=255), nullable=True),
        sa.Column("output_object_key", sa.String(length=1024), nullable=True),
        sa.Column(
            "stats",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        _id(),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            "stage_sequence BETWEEN 1 AND 4",
            name=op.f("ck_ingestion_checkpoints_stage_sequence_supported"),
        ),
        sa.CheckConstraint(
            "(stage = 'validating' AND stage_sequence = 1) OR "
            "(stage = 'parsing' AND stage_sequence = 2) OR "
            "(stage = 'extracting_assets' AND stage_sequence = 3) OR "
            "(stage = 'chunking' AND stage_sequence = 4)",
            name=op.f("ck_ingestion_checkpoints_stage_sequence_consistent"),
        ),
        sa.CheckConstraint(
            "fencing_token >= 1",
            name=op.f("ck_ingestion_checkpoints_fencing_token_positive"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 1",
            name=op.f("ck_ingestion_checkpoints_attempt_count_positive"),
        ),
        sa.CheckConstraint(
            "octet_length(stage_idempotency_hash) = 32",
            name=op.f("ck_ingestion_checkpoints_idempotency_hash_length"),
        ),
        sa.CheckConstraint(
            "octet_length(input_hash) = 32",
            name=op.f("ck_ingestion_checkpoints_input_hash_length"),
        ),
        sa.CheckConstraint(
            "octet_length(output_hash) = 32",
            name=op.f("ck_ingestion_checkpoints_output_hash_length"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(stats) = 'object'",
            name=op.f("ck_ingestion_checkpoints_stats_object"),
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id", "document_id", "workspace_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.workspace_id",
            ],
            name="fk_ingestion_checkpoints_version_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_job_id", "workspace_id"],
            ["jobs.id", "jobs.workspace_id"],
            name="fk_ingestion_checkpoints_job_workspace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingestion_checkpoints")),
        sa.UniqueConstraint(
            "document_version_id",
            "stage",
            name=op.f("uq_ingestion_checkpoints_document_version_id_stage"),
        ),
    )
    op.create_index(
        op.f("ix_ingestion_checkpoints_workspace_id_document_version_id_stage_sequence"),
        "ingestion_checkpoints",
        ["workspace_id", "document_version_id", "stage_sequence"],
    )

    op.create_table(
        "document_pages",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("width_points", sa.Float(), nullable=False),
        sa.Column("height_points", sa.Float(), nullable=False),
        sa.Column("text_content", sa.Text(), nullable=False),
        sa.Column(
            "text_source",
            sa.Enum(
                "digital",
                "ocr",
                "plain_text",
                "markdown",
                name="document_page_text_source",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("bbox", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "title_path",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("parser_version", sa.String(length=32), nullable=False),
        _id(),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint("page_number >= 1", name=op.f("ck_document_pages_page_number_positive")),
        sa.CheckConstraint(
            "width_points > 0 AND height_points > 0",
            name=op.f("ck_document_pages_geometry_positive"),
        ),
        sa.CheckConstraint(
            "length(btrim(text_content)) > 0", name=op.f("ck_document_pages_text_not_blank")
        ),
        sa.CheckConstraint(
            "octet_length(content_hash) = 32",
            name=op.f("ck_document_pages_content_hash_length"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(bbox) = 'array' AND jsonb_array_length(bbox) = 4",
            name=op.f("ck_document_pages_bbox_shape"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(title_path) = 'array'",
            name=op.f("ck_document_pages_title_path_array"),
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id", "document_id", "workspace_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.workspace_id",
            ],
            name="fk_document_pages_version_workspace",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_pages")),
        sa.UniqueConstraint(
            "id",
            "document_version_id",
            "workspace_id",
            name=op.f("uq_document_pages_id_document_version_id_workspace_id"),
        ),
        sa.UniqueConstraint(
            "document_version_id",
            "page_number",
            name=op.f("uq_document_pages_document_version_id_page_number"),
        ),
    )
    op.create_index(
        op.f("ix_document_pages_workspace_id_document_version_id_page_number"),
        "document_pages",
        ["workspace_id", "document_version_id", "page_number"],
    )

    op.create_table(
        "document_chunks",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("text_content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("bbox", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "title_path",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("chunker_version", sa.String(length=32), nullable=False),
        _id(),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            "ordinal >= 1 AND page_number >= 1",
            name=op.f("ck_document_chunks_locator_positive"),
        ),
        sa.CheckConstraint(
            "length(btrim(text_content)) > 0", name=op.f("ck_document_chunks_text_not_blank")
        ),
        sa.CheckConstraint(
            "token_count >= 1", name=op.f("ck_document_chunks_token_count_positive")
        ),
        sa.CheckConstraint(
            "octet_length(content_hash) = 32",
            name=op.f("ck_document_chunks_content_hash_length"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(bbox) = 'array' AND jsonb_array_length(bbox) = 4",
            name=op.f("ck_document_chunks_bbox_shape"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(title_path) = 'array'",
            name=op.f("ck_document_chunks_title_path_array"),
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id", "document_id", "workspace_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.workspace_id",
            ],
            name="fk_document_chunks_version_workspace",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_chunks")),
        sa.UniqueConstraint(
            "id",
            "document_version_id",
            "workspace_id",
            name=op.f("uq_document_chunks_id_document_version_id_workspace_id"),
        ),
        sa.UniqueConstraint(
            "document_version_id",
            "ordinal",
            name=op.f("uq_document_chunks_document_version_id_ordinal"),
        ),
    )
    op.create_index(
        op.f("ix_document_chunks_workspace_id_document_version_id_ordinal"),
        "document_chunks",
        ["workspace_id", "document_version_id", "ordinal"],
    )

    op.create_table(
        "document_assets",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("page_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("image", "table", name="document_asset_kind", native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column("bbox", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "title_path",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("preview_sha256", sa.LargeBinary(length=32), nullable=False),
        sa.Column("preview_mime_type", sa.String(length=64), nullable=False),
        sa.Column("preview_bucket", sa.String(length=255), nullable=False),
        sa.Column("preview_object_key", sa.String(length=1024), nullable=False),
        sa.Column("html_content", sa.Text(), nullable=True),
        sa.Column("parser_version", sa.String(length=32), nullable=False),
        _id(),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            "ordinal >= 1 AND page_number >= 1",
            name=op.f("ck_document_assets_locator_positive"),
        ),
        sa.CheckConstraint(
            "octet_length(content_hash) = 32",
            name=op.f("ck_document_assets_content_hash_length"),
        ),
        sa.CheckConstraint(
            "octet_length(preview_sha256) = 32",
            name=op.f("ck_document_assets_preview_hash_length"),
        ),
        sa.CheckConstraint(
            "preview_mime_type = 'image/png'",
            name=op.f("ck_document_assets_preview_mime_type_supported"),
        ),
        sa.CheckConstraint(
            "length(btrim(preview_bucket)) > 0",
            name=op.f("ck_document_assets_preview_bucket_not_blank"),
        ),
        sa.CheckConstraint(
            "length(btrim(preview_object_key)) > 0",
            name=op.f("ck_document_assets_preview_key_not_blank"),
        ),
        sa.CheckConstraint(
            "(kind = 'table' AND html_content IS NOT NULL) OR "
            "(kind = 'image' AND html_content IS NULL)",
            name=op.f("ck_document_assets_kind_payload_consistent"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(bbox) = 'array' AND jsonb_array_length(bbox) = 4",
            name=op.f("ck_document_assets_bbox_shape"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(title_path) = 'array'",
            name=op.f("ck_document_assets_title_path_array"),
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id", "document_id", "workspace_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.workspace_id",
            ],
            name="fk_document_assets_version_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["page_id", "document_version_id", "workspace_id"],
            [
                "document_pages.id",
                "document_pages.document_version_id",
                "document_pages.workspace_id",
            ],
            name="fk_document_assets_page_workspace",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_assets")),
        sa.UniqueConstraint(
            "id",
            "document_version_id",
            "workspace_id",
            name=op.f("uq_document_assets_id_document_version_id_workspace_id"),
        ),
        sa.UniqueConstraint(
            "document_version_id",
            "ordinal",
            name=op.f("uq_document_assets_document_version_id_ordinal"),
        ),
    )
    op.create_index(
        op.f("ix_document_assets_workspace_id_document_version_id_page_number_ordinal"),
        "document_assets",
        ["workspace_id", "document_version_id", "page_number", "ordinal"],
    )

    op.create_table(
        "chunk_asset_links",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        _id(),
        _created_at(),
        _updated_at(),
        sa.ForeignKeyConstraint(
            ["chunk_id", "document_version_id", "workspace_id"],
            [
                "document_chunks.id",
                "document_chunks.document_version_id",
                "document_chunks.workspace_id",
            ],
            name="fk_chunk_asset_links_chunk_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["asset_id", "document_version_id", "workspace_id"],
            [
                "document_assets.id",
                "document_assets.document_version_id",
                "document_assets.workspace_id",
            ],
            name="fk_chunk_asset_links_asset_workspace",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunk_asset_links")),
        sa.UniqueConstraint(
            "chunk_id", "asset_id", name=op.f("uq_chunk_asset_links_chunk_id_asset_id")
        ),
    )
    op.create_index(
        op.f("ix_chunk_asset_links_workspace_id_document_version_id_chunk_id"),
        "chunk_asset_links",
        ["workspace_id", "document_version_id", "chunk_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_chunk_asset_links_workspace_id_document_version_id_chunk_id"),
        table_name="chunk_asset_links",
    )
    op.drop_table("chunk_asset_links")
    op.drop_index(
        op.f("ix_document_assets_workspace_id_document_version_id_page_number_ordinal"),
        table_name="document_assets",
    )
    op.drop_table("document_assets")
    op.drop_index(
        op.f("ix_document_chunks_workspace_id_document_version_id_ordinal"),
        table_name="document_chunks",
    )
    op.drop_table("document_chunks")
    op.drop_index(
        op.f("ix_document_pages_workspace_id_document_version_id_page_number"),
        table_name="document_pages",
    )
    op.drop_table("document_pages")
    op.drop_index(
        op.f("ix_ingestion_checkpoints_workspace_id_document_version_id_stage_sequence"),
        table_name="ingestion_checkpoints",
    )
    op.drop_table("ingestion_checkpoints")

    op.drop_constraint(
        op.f("ck_document_versions_chunker_contract"), "document_versions", type_="check"
    )
    op.drop_constraint(
        op.f("ck_document_versions_parser_contract"), "document_versions", type_="check"
    )
    op.drop_constraint(
        op.f("ck_document_versions_status_supported"), "document_versions", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_document_versions_status_supported"),
        "document_versions",
        "status IN ('queued', 'validating', 'parsing', 'extracting_assets', 'chunking', "
        "'embedding', 'vector_indexing', 'lexical_indexing', 'retrying', 'ready', "
        "'failed', 'cancelled', 'deleting', 'deleted')",
    )
    op.drop_column("document_versions", "chunker_config")
    op.drop_column("document_versions", "chunker_version")
    op.drop_column("document_versions", "chunker_name")
    op.drop_column("document_versions", "parser_config")
    op.drop_column("document_versions", "parser_schema_version")
    op.drop_column("document_versions", "parser_version")
    op.drop_column("document_versions", "parser_name")
    op.execute(
        sa.text(
            "ALTER TABLE document_versions DROP CONSTRAINT IF EXISTS "
            "uq_document_versions_file_object_id"
        )
    )
    op.create_unique_constraint(
        op.f("uq_document_versions_file_object_id"),
        "document_versions",
        ["file_object_id"],
    )
