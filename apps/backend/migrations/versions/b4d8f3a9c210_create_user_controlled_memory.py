"""create user controlled Memory candidates and revisions

Revision ID: b4d8f3a9c210
Revises: a8f42d91e3b7
Create Date: 2026-08-20 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b4d8f3a9c210"
down_revision: str | Sequence[str] | None = "a8f42d91e3b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create candidate-first Memory facts with tenant-bound lineage."""

    op.create_table(
        "thread_memory_states",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("source_message_ids", postgresql.ARRAY(sa.Uuid()), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("compaction_revision", sa.Integer(), nullable=False),
        sa.Column("freshness_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schema_version", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
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
            "cardinality(source_message_ids) BETWEEN 1 AND 8",
            name=op.f("ck_thread_memory_states_sources_bounded"),
        ),
        sa.CheckConstraint(
            "compaction_revision >= 1",
            name=op.f("ck_thread_memory_states_compaction_revision_positive"),
        ),
        sa.CheckConstraint(
            "length(btrim(summary)) > 0",
            name=op.f("ck_thread_memory_states_summary_not_blank"),
        ),
        sa.CheckConstraint(
            "revision >= 1",
            name=op.f("ck_thread_memory_states_revision_positive"),
        ),
        sa.CheckConstraint(
            "schema_version = 1",
            name=op.f("ck_thread_memory_states_schema_version_supported"),
        ),
        sa.CheckConstraint(
            "octet_length(summary) <= 16000",
            name=op.f("ck_thread_memory_states_summary_bytes_bounded"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id", "workspace_id"],
            ["conversations.id", "conversations.workspace_id"],
            name="fk_thread_memory_states_conversation_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "owner_user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            name="fk_thread_memory_states_workspace_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_thread_memory_states")),
        sa.UniqueConstraint(
            "id", "workspace_id", name=op.f("uq_thread_memory_states_id_workspace_id")
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "conversation_id",
            "owner_user_id",
            name=op.f("uq_thread_memory_states_workspace_id_conversation_id_owner_user_id"),
        ),
    )
    op.create_index(
        op.f("ix_thread_memory_states_workspace_id_owner_user_id_updated_at"),
        "thread_memory_states",
        ["workspace_id", "owner_user_id", "updated_at"],
        unique=False,
    )

    op.create_table(
        "memories",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("source_conversation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "scope",
            sa.Enum("user", "workspace", name="memory_scope", native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column(
            "kind",
            sa.Enum(
                "preference",
                "fact",
                "instruction",
                "note",
                name="memory_kind",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "confirmed",
                "disabled",
                "expired",
                "deleted",
                name="memory_status",
                native_enum=False,
                length=16,
            ),
            server_default=sa.text("'confirmed'"),
            nullable=False,
        ),
        sa.Column("current_revision_id", sa.Uuid(), nullable=False),
        sa.Column("current_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
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
            "confidence BETWEEN 0 AND 1", name=op.f("ck_memories_confidence_bounded")
        ),
        sa.CheckConstraint(
            "current_version >= 1", name=op.f("ck_memories_current_version_positive")
        ),
        sa.CheckConstraint(
            "(status = 'deleted' AND deleted_at IS NOT NULL) OR "
            "(status <> 'deleted' AND deleted_at IS NULL)",
            name=op.f("ck_memories_deletion_state_consistent"),
        ),
        sa.CheckConstraint(
            "kind IN ('preference', 'fact', 'instruction', 'note')",
            name=op.f("ck_memories_kind_supported"),
        ),
        sa.CheckConstraint(
            "scope IN ('user', 'workspace')", name=op.f("ck_memories_scope_supported")
        ),
        sa.CheckConstraint(
            "status IN ('confirmed', 'disabled', 'expired', 'deleted')",
            name=op.f("ck_memories_status_supported"),
        ),
        sa.ForeignKeyConstraint(
            ["source_conversation_id", "workspace_id"],
            ["conversations.id", "conversations.workspace_id"],
            name="fk_memories_source_conversation_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "owner_user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            name="fk_memories_workspace_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memories")),
        sa.UniqueConstraint("id", "workspace_id", name=op.f("uq_memories_id_workspace_id")),
        sa.UniqueConstraint(
            "id",
            "workspace_id",
            "owner_user_id",
            name=op.f("uq_memories_id_workspace_id_owner_user_id"),
        ),
    )
    op.create_index(
        op.f("ix_memories_workspace_id_owner_user_id_status_updated_at"),
        "memories",
        ["workspace_id", "owner_user_id", "status", "updated_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_memories_workspace_id_scope_status_updated_at"),
        "memories",
        ["workspace_id", "scope", "status", "updated_at"],
        unique=False,
    )

    op.create_table(
        "memory_revisions",
        sa.Column("memory_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "scope",
            sa.Enum(
                "user",
                "workspace",
                name="memory_revision_scope",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column(
            "kind",
            sa.Enum(
                "preference",
                "fact",
                "instruction",
                "note",
                name="memory_revision_kind",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column(
            "write_action",
            sa.Enum(
                "create",
                "update",
                "merge",
                name="memory_write_action",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("write_reason", sa.String(length=200), nullable=False),
        sa.Column(
            "policy_decision",
            sa.Enum(
                "allowed",
                "requires_edit",
                "rejected",
                name="memory_revision_policy_decision",
                native_enum=False,
                length=24,
            ),
            nullable=False,
        ),
        sa.Column("editor_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "validity",
            sa.Enum(
                "valid",
                "withdrawn",
                name="memory_revision_validity",
                native_enum=False,
                length=16,
            ),
            server_default=sa.text("'valid'"),
            nullable=False,
        ),
        sa.Column("schema_version", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.CheckConstraint(
            "write_action IN ('create', 'update', 'merge')",
            name=op.f("ck_memory_revisions_action_supported"),
        ),
        sa.CheckConstraint(
            "length(btrim(content)) > 0",
            name=op.f("ck_memory_revisions_content_not_blank"),
        ),
        sa.CheckConstraint(
            "octet_length(content) <= 16000",
            name=op.f("ck_memory_revisions_content_bytes_bounded"),
        ),
        sa.CheckConstraint(
            "kind IN ('preference', 'fact', 'instruction', 'note')",
            name=op.f("ck_memory_revisions_kind_supported"),
        ),
        sa.CheckConstraint(
            "policy_decision IN ('allowed', 'requires_edit', 'rejected')",
            name=op.f("ck_memory_revisions_policy_decision_supported"),
        ),
        sa.CheckConstraint(
            "schema_version = 1",
            name=op.f("ck_memory_revisions_schema_version_supported"),
        ),
        sa.CheckConstraint(
            "scope IN ('user', 'workspace')",
            name=op.f("ck_memory_revisions_scope_supported"),
        ),
        sa.CheckConstraint(
            "validity IN ('valid', 'withdrawn')",
            name=op.f("ck_memory_revisions_validity_supported"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_memory_revisions_version_positive")),
        sa.CheckConstraint(
            "length(btrim(write_reason)) > 0",
            name=op.f("ck_memory_revisions_write_reason_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["memory_id", "workspace_id", "owner_user_id"],
            ["memories.id", "memories.workspace_id", "memories.owner_user_id"],
            name="fk_memory_revisions_memory_workspace_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "editor_user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            name="fk_memory_revisions_workspace_editor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memory_revisions")),
        sa.UniqueConstraint(
            "id",
            "memory_id",
            "workspace_id",
            name=op.f("uq_memory_revisions_id_memory_id_workspace_id"),
        ),
        sa.UniqueConstraint(
            "memory_id", "version", name=op.f("uq_memory_revisions_memory_id_version")
        ),
    )
    op.create_index(
        op.f("ix_memory_revisions_workspace_id_memory_id_version"),
        "memory_revisions",
        ["workspace_id", "memory_id", "version"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_memories_current_revision_memory_workspace",
        "memories",
        "memory_revisions",
        ["current_revision_id", "id", "workspace_id"],
        ["id", "memory_id", "workspace_id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )

    op.create_table(
        "memory_candidates",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("suggested_content", sa.Text(), nullable=True),
        sa.Column("suggested_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "scope",
            sa.Enum(
                "user",
                "workspace",
                name="memory_candidate_scope",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("write_reason", sa.String(length=200), nullable=False),
        sa.Column(
            "policy_decision",
            sa.Enum(
                "allowed",
                "requires_edit",
                "rejected",
                name="memory_candidate_policy_decision",
                native_enum=False,
                length=24,
            ),
            nullable=False,
        ),
        sa.Column(
            "policy_reason",
            sa.Enum(
                "user_authored",
                "mixed_sources",
                "assistant_only_requires_edit",
                "sensitive_content",
                name="memory_candidate_policy_reason",
                native_enum=False,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "candidate",
                "confirmed",
                "rejected",
                name="memory_candidate_status",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("idempotency_key_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "resolution_action",
            sa.Enum(
                "create",
                "update",
                "merge",
                name="memory_candidate_resolution_action",
                native_enum=False,
                length=16,
            ),
            nullable=True,
        ),
        sa.Column("resolution_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("resolved_memory_id", sa.Uuid(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
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
            "confidence BETWEEN 0 AND 1",
            name=op.f("ck_memory_candidates_confidence_bounded"),
        ),
        sa.CheckConstraint(
            "octet_length(idempotency_key_hash) = 32",
            name=op.f("ck_memory_candidates_idempotency_hash_length"),
        ),
        sa.CheckConstraint(
            "(status = 'candidate' AND suggested_content IS NOT NULL "
            "AND resolved_memory_id IS NULL AND resolution_action IS NULL "
            "AND resolution_fingerprint IS NULL AND resolved_at IS NULL) OR "
            "(status = 'confirmed' AND resolved_memory_id IS NOT NULL "
            "AND resolution_action IS NOT NULL AND resolution_fingerprint IS NOT NULL "
            "AND resolved_at IS NOT NULL) OR "
            "(status = 'rejected' AND resolved_memory_id IS NULL "
            "AND resolution_action IS NULL AND resolved_at IS NOT NULL)",
            name=op.f("ck_memory_candidates_lifecycle_consistent"),
        ),
        sa.CheckConstraint(
            "policy_decision IN ('allowed', 'requires_edit', 'rejected')",
            name=op.f("ck_memory_candidates_policy_decision_supported"),
        ),
        sa.CheckConstraint(
            "policy_reason IN ('user_authored', 'mixed_sources', "
            "'assistant_only_requires_edit', 'sensitive_content')",
            name=op.f("ck_memory_candidates_policy_reason_supported"),
        ),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[a-f0-9]{64}$'",
            name=op.f("ck_memory_candidates_request_fingerprint_lowercase_hex"),
        ),
        sa.CheckConstraint(
            "resolution_fingerprint IS NULL OR resolution_fingerprint ~ '^[a-f0-9]{64}$'",
            name=op.f("ck_memory_candidates_resolution_fingerprint_lowercase_hex"),
        ),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_memory_candidates_revision_positive")),
        sa.CheckConstraint(
            "scope IN ('user', 'workspace')",
            name=op.f("ck_memory_candidates_scope_supported"),
        ),
        sa.CheckConstraint(
            "status IN ('candidate', 'confirmed', 'rejected')",
            name=op.f("ck_memory_candidates_status_supported"),
        ),
        sa.CheckConstraint(
            "suggested_content IS NULL OR (length(btrim(suggested_content)) > 0 "
            "AND octet_length(suggested_content) <= 16000)",
            name=op.f("ck_memory_candidates_suggested_content_bounded"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id", "workspace_id"],
            ["conversations.id", "conversations.workspace_id"],
            name="fk_memory_candidates_conversation_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_memory_id", "workspace_id", "owner_user_id"],
            ["memories.id", "memories.workspace_id", "memories.owner_user_id"],
            name="fk_memory_candidates_resolved_memory_workspace_owner",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "owner_user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            name="fk_memory_candidates_workspace_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memory_candidates")),
        sa.UniqueConstraint(
            "id",
            "workspace_id",
            "owner_user_id",
            name=op.f("uq_memory_candidates_id_workspace_id_owner_user_id"),
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "owner_user_id",
            "idempotency_key_hash",
            name="uq_memory_candidates_workspace_owner_idempotency",
        ),
    )
    op.create_index(
        op.f("ix_memory_candidates_workspace_id_conversation_id_created_at"),
        "memory_candidates",
        ["workspace_id", "conversation_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_memory_candidates_workspace_id_owner_user_id_status_created_at"),
        "memory_candidates",
        ["workspace_id", "owner_user_id", "status", "created_at"],
        unique=False,
    )

    op.create_table(
        "memory_candidate_sources",
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.SmallInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "ordinal BETWEEN 0 AND 7",
            name=op.f("ck_memory_candidate_sources_ordinal_bounded"),
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id", "workspace_id", "owner_user_id"],
            [
                "memory_candidates.id",
                "memory_candidates.workspace_id",
                "memory_candidates.owner_user_id",
            ],
            name="fk_memory_candidate_sources_candidate_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["message_id", "workspace_id"],
            ["conversation_messages.id", "conversation_messages.workspace_id"],
            name="fk_memory_candidate_sources_message_workspace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "candidate_id", "message_id", name=op.f("pk_memory_candidate_sources")
        ),
        sa.UniqueConstraint(
            "candidate_id",
            "ordinal",
            name=op.f("uq_memory_candidate_sources_candidate_id_ordinal"),
        ),
    )

    op.create_table(
        "memory_revision_sources",
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("memory_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.SmallInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "ordinal BETWEEN 0 AND 7",
            name=op.f("ck_memory_revision_sources_ordinal_bounded"),
        ),
        sa.ForeignKeyConstraint(
            ["message_id", "workspace_id"],
            ["conversation_messages.id", "conversation_messages.workspace_id"],
            name="fk_memory_revision_sources_message_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["revision_id", "memory_id", "workspace_id"],
            ["memory_revisions.id", "memory_revisions.memory_id", "memory_revisions.workspace_id"],
            name="fk_memory_revision_sources_revision_memory_workspace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "revision_id", "message_id", name=op.f("pk_memory_revision_sources")
        ),
        sa.UniqueConstraint(
            "revision_id",
            "ordinal",
            name=op.f("uq_memory_revision_sources_revision_id_ordinal"),
        ),
    )


def downgrade() -> None:
    """Remove Memory lineage before candidates, revisions, and projections."""

    op.drop_table("memory_revision_sources")
    op.drop_table("memory_candidate_sources")
    op.drop_index(
        op.f("ix_memory_candidates_workspace_id_owner_user_id_status_created_at"),
        table_name="memory_candidates",
    )
    op.drop_index(
        op.f("ix_memory_candidates_workspace_id_conversation_id_created_at"),
        table_name="memory_candidates",
    )
    op.drop_table("memory_candidates")
    op.drop_constraint(
        "fk_memories_current_revision_memory_workspace",
        "memories",
        type_="foreignkey",
    )
    op.drop_index(
        op.f("ix_memory_revisions_workspace_id_memory_id_version"),
        table_name="memory_revisions",
    )
    op.drop_table("memory_revisions")
    op.drop_index(
        op.f("ix_memories_workspace_id_scope_status_updated_at"),
        table_name="memories",
    )
    op.drop_index(
        op.f("ix_memories_workspace_id_owner_user_id_status_updated_at"),
        table_name="memories",
    )
    op.drop_table("memories")
    op.drop_index(
        op.f("ix_thread_memory_states_workspace_id_owner_user_id_updated_at"),
        table_name="thread_memory_states",
    )
    op.drop_table("thread_memory_states")
