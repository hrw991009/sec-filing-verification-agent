"""create Evidence, Claim, Research ownership, and derived graph ledger

Revision ID: f2a4c6e8b013
Revises: d7c91e4a62bf
Create Date: 2026-08-21 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f2a4c6e8b013"
down_revision: str | Sequence[str] | None = "d7c91e4a62bf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id_column() -> sa.Column:
    return sa.Column(
        "id",
        sa.Uuid(),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def _created_at_column() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def _updated_at_column() -> sa.Column:
    return sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def _recorded_at_column() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False)


def upgrade() -> None:
    """Add the auditable Observation to Evidence to Claim ledger."""

    op.create_table(
        "research_runs",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("agent_run_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="draft", nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        _id_column(),
        _created_at_column(),
        _updated_at_column(),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'completed', 'failed', 'cancelled')",
            name=op.f("ck_research_runs_status_supported"),
        ),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_research_runs_revision_positive")),
        sa.ForeignKeyConstraint(
            ["workspace_id", "owner_user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            name="fk_research_runs_workspace_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id", "workspace_id", "owner_user_id"],
            ["agent_runs.id", "agent_runs.workspace_id", "agent_runs.user_id"],
            name="fk_research_runs_agent_run_workspace_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_research_runs")),
        sa.UniqueConstraint("id", "workspace_id", name=op.f("uq_research_runs_id_workspace_id")),
        sa.UniqueConstraint(
            "id",
            "workspace_id",
            "owner_user_id",
            name=op.f("uq_research_runs_id_workspace_id_owner_user_id"),
        ),
        sa.UniqueConstraint("agent_run_id", name=op.f("uq_research_runs_agent_run_id")),
    )
    op.create_index(
        op.f("ix_research_runs_workspace_id_owner_user_id_created_at"),
        "research_runs",
        ["workspace_id", "owner_user_id", "created_at"],
    )

    op.create_table(
        "evidence",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("title", sa.String(length=1000), nullable=False),
        sa.Column("canonical_url", sa.String(length=2048), nullable=True),
        sa.Column("locator_type", sa.String(length=32), nullable=False),
        sa.Column("locator", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("license_or_terms", sa.String(length=1000), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidation_reason", sa.String(length=200), nullable=True),
        sa.Column("origin_run_id", sa.Uuid(), nullable=False),
        sa.Column("origin_step_id", sa.Uuid(), nullable=False),
        sa.Column("origin_tool_call_id", sa.Uuid(), nullable=False),
        sa.Column("origin_observation_id", sa.Uuid(), nullable=False),
        sa.Column("origin_source_ordinal", sa.SmallInteger(), nullable=False),
        sa.Column("normalizer_version", sa.String(length=128), nullable=False),
        sa.Column(
            "authorization_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("source_resource_version", sa.String(length=128), nullable=False),
        sa.Column("source_item_id", sa.Uuid(), nullable=True),
        sa.Column("query_run_id", sa.Uuid(), nullable=True),
        sa.Column("deduplication_key", sa.String(length=64), nullable=False),
        _id_column(),
        _created_at_column(),
        _updated_at_column(),
        sa.CheckConstraint("schema_version = 1", name=op.f("ck_evidence_schema_version_supported")),
        sa.CheckConstraint(
            "kind IN ('web_snapshot', 'sql_result', 'news', 'policy', 'bidding', 'stock')",
            name=op.f("ck_evidence_kind_supported"),
        ),
        sa.CheckConstraint(
            "locator_type IN ('industry_source_v1', 'sql_result_v1')",
            name=op.f("ck_evidence_locator_type_supported"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'superseded', 'tombstoned', 'unavailable')",
            name=op.f("ck_evidence_status_supported"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(locator) = 'object'", name=op.f("ck_evidence_locator_object")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(authorization_snapshot) = 'object'",
            name=op.f("ck_evidence_authorization_snapshot_object"),
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[a-f0-9]{64}$'",
            name=op.f("ck_evidence_content_hash_lowercase_hex"),
        ),
        sa.CheckConstraint(
            "deduplication_key ~ '^[a-f0-9]{64}$'",
            name=op.f("ck_evidence_dedupe_hash_lowercase_hex"),
        ),
        sa.CheckConstraint("length(btrim(title)) > 0", name=op.f("ck_evidence_title_not_blank")),
        sa.CheckConstraint(
            "length(btrim(license_or_terms)) > 0", name=op.f("ck_evidence_terms_not_blank")
        ),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_evidence_revision_positive")),
        sa.CheckConstraint(
            "(locator_type = 'industry_source_v1' AND source_item_id IS NOT NULL "
            "AND query_run_id IS NULL) OR (locator_type = 'sql_result_v1' "
            "AND query_run_id IS NOT NULL AND source_item_id IS NULL)",
            name=op.f("ck_evidence_source_reference_matches_locator"),
        ),
        sa.CheckConstraint(
            "(status = 'active' AND excerpt IS NOT NULL AND invalidated_at IS NULL "
            "AND invalidation_reason IS NULL) OR (status <> 'active' AND excerpt IS NULL "
            "AND invalidated_at IS NOT NULL AND invalidation_reason IS NOT NULL)",
            name=op.f("ck_evidence_lifecycle_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["origin_run_id", "workspace_id"],
            ["agent_runs.id", "agent_runs.workspace_id"],
            name="fk_evidence_origin_run_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["origin_step_id", "origin_run_id", "workspace_id"],
            ["agent_steps.id", "agent_steps.run_id", "agent_steps.workspace_id"],
            name="fk_evidence_origin_step_run_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["origin_tool_call_id", "origin_run_id", "workspace_id"],
            ["tool_calls.id", "tool_calls.run_id", "tool_calls.workspace_id"],
            name="fk_evidence_origin_tool_call_run_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_item_id"],
            ["source_items.id"],
            name="fk_evidence_source_item",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["query_run_id", "workspace_id"],
            ["query_runs.id", "query_runs.workspace_id"],
            name="fk_evidence_query_run_workspace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence")),
        sa.UniqueConstraint("id", "workspace_id", name=op.f("uq_evidence_id_workspace_id")),
        sa.UniqueConstraint(
            "workspace_id",
            "deduplication_key",
            name=op.f("uq_evidence_workspace_id_deduplication_key"),
        ),
    )
    op.create_index(
        op.f("ix_evidence_workspace_id_status_created_at"),
        "evidence",
        ["workspace_id", "status", "created_at"],
    )
    op.create_index(
        op.f("ix_evidence_workspace_id_origin_run_id_origin_tool_call_id"),
        "evidence",
        ["workspace_id", "origin_run_id", "origin_tool_call_id"],
    )

    op.create_table(
        "evidence_normalization_decisions",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("tool_call_id", sa.Uuid(), nullable=False),
        sa.Column("observation_id", sa.Uuid(), nullable=False),
        sa.Column("source_ordinal", sa.SmallInteger(), nullable=False),
        sa.Column("normalizer_version", sa.String(length=128), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=True),
        _id_column(),
        _recorded_at_column(),
        sa.CheckConstraint(
            "source_ordinal BETWEEN 1 AND 16",
            name=op.f("ck_evidence_normalization_decisions_source_ordinal_bounded"),
        ),
        sa.CheckConstraint(
            "decision IN ('accepted', 'rejected')",
            name=op.f("ck_evidence_normalization_decisions_decision_supported"),
        ),
        sa.CheckConstraint(
            "(decision = 'accepted' AND reason = 'accepted' AND evidence_id IS NOT NULL) OR "
            "(decision = 'rejected' AND reason <> 'accepted' AND evidence_id IS NULL)",
            name=op.f("ck_evidence_normalization_decisions_decision_result_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["tool_call_id", "run_id", "workspace_id"],
            ["tool_calls.id", "tool_calls.run_id", "tool_calls.workspace_id"],
            name="fk_evidence_decisions_tool_call_run_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id", "workspace_id"],
            ["evidence.id", "evidence.workspace_id"],
            name="fk_evidence_decisions_evidence_workspace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence_normalization_decisions")),
        sa.UniqueConstraint(
            "workspace_id",
            "tool_call_id",
            "observation_id",
            "source_ordinal",
            "normalizer_version",
            name=op.f(
                "uq_evidence_normalization_decisions_workspace_id_tool_call_id_observation_id_source_ordinal_normalizer_version"
            ),
        ),
    )
    op.create_index(
        op.f("ix_evidence_normalization_decisions_workspace_id_observation_id_created_at"),
        "evidence_normalization_decisions",
        ["workspace_id", "observation_id", "created_at"],
    )

    op.create_table(
        "research_claims",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("research_run_id", sa.Uuid(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("verification_status", sa.String(length=16), nullable=False),
        sa.Column("coverage", sa.Float(), nullable=False),
        sa.Column("conflict", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        _id_column(),
        _created_at_column(),
        _updated_at_column(),
        sa.CheckConstraint(
            "length(btrim(statement)) > 0", name=op.f("ck_research_claims_statement_not_blank")
        ),
        sa.CheckConstraint(
            "confidence BETWEEN 0 AND 1", name=op.f("ck_research_claims_confidence_bounded")
        ),
        sa.CheckConstraint(
            "coverage BETWEEN 0 AND 1", name=op.f("ck_research_claims_coverage_bounded")
        ),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_research_claims_revision_positive")),
        sa.CheckConstraint(
            "verification_status IN ('supported', 'refuted', 'uncertain', 'conflicted')",
            name=op.f("ck_research_claims_verification_status_supported"),
        ),
        sa.CheckConstraint(
            "(verification_status = 'conflicted') = conflict",
            name=op.f("ck_research_claims_conflict_status_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["research_run_id", "workspace_id"],
            ["research_runs.id", "research_runs.workspace_id"],
            name="fk_research_claims_research_run_workspace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_research_claims")),
        sa.UniqueConstraint("id", "workspace_id", name=op.f("uq_research_claims_id_workspace_id")),
    )
    op.create_index(
        op.f("ix_research_claims_workspace_id_research_run_id_created_at"),
        "research_claims",
        ["workspace_id", "research_run_id", "created_at"],
    )

    op.create_table(
        "claim_evidence",
        sa.Column("claim_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("relation", sa.String(length=16), nullable=False),
        sa.Column("relation_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("ordinal", sa.SmallInteger(), nullable=False),
        sa.Column("origin_run_id", sa.Uuid(), nullable=False),
        sa.Column("origin_step_id", sa.Uuid(), nullable=False),
        _recorded_at_column(),
        sa.CheckConstraint(
            "relation IN ('supports', 'refutes', 'context')",
            name=op.f("ck_claim_evidence_relation_supported"),
        ),
        sa.CheckConstraint(
            "relation_version >= 1", name=op.f("ck_claim_evidence_relation_version_positive")
        ),
        sa.CheckConstraint(
            "status IN ('active', 'invalidated')",
            name=op.f("ck_claim_evidence_status_supported"),
        ),
        sa.CheckConstraint(
            "ordinal BETWEEN 1 AND 32", name=op.f("ck_claim_evidence_ordinal_bounded")
        ),
        sa.ForeignKeyConstraint(
            ["claim_id", "workspace_id"],
            ["research_claims.id", "research_claims.workspace_id"],
            name="fk_claim_evidence_claim_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id", "workspace_id"],
            ["evidence.id", "evidence.workspace_id"],
            name="fk_claim_evidence_evidence_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["origin_step_id", "origin_run_id", "workspace_id"],
            ["agent_steps.id", "agent_steps.run_id", "agent_steps.workspace_id"],
            name="fk_claim_evidence_origin_step_run_workspace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("claim_id", "evidence_id", name=op.f("pk_claim_evidence")),
        sa.UniqueConstraint("claim_id", "ordinal", name=op.f("uq_claim_evidence_claim_id_ordinal")),
    )

    op.create_table(
        "graph_nodes",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("research_run_id", sa.Uuid(), nullable=False),
        sa.Column("node_type", sa.String(length=16), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        _id_column(),
        _recorded_at_column(),
        sa.CheckConstraint(
            "node_type IN ('claim', 'evidence', 'entity')",
            name=op.f("ck_graph_nodes_node_type_supported"),
        ),
        sa.CheckConstraint("length(btrim(label)) > 0", name=op.f("ck_graph_nodes_label_not_blank")),
        sa.CheckConstraint(
            "status IN ('active', 'invalidated')", name=op.f("ck_graph_nodes_status_supported")
        ),
        sa.ForeignKeyConstraint(
            ["research_run_id", "workspace_id"],
            ["research_runs.id", "research_runs.workspace_id"],
            name="fk_graph_nodes_research_run_workspace",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_graph_nodes")),
        sa.UniqueConstraint(
            "id",
            "workspace_id",
            "research_run_id",
            name=op.f("uq_graph_nodes_id_workspace_id_research_run_id"),
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "research_run_id",
            "node_type",
            "resource_id",
            name=op.f("uq_graph_nodes_workspace_id_research_run_id_node_type_resource_id"),
        ),
    )

    op.create_table(
        "graph_edges",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("research_run_id", sa.Uuid(), nullable=False),
        sa.Column("source_node_id", sa.Uuid(), nullable=False),
        sa.Column("target_node_id", sa.Uuid(), nullable=False),
        sa.Column("relation", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        _id_column(),
        _recorded_at_column(),
        sa.CheckConstraint(
            "relation IN ('supports', 'refutes', 'context')",
            name=op.f("ck_graph_edges_relation_supported"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'invalidated')", name=op.f("ck_graph_edges_status_supported")
        ),
        sa.ForeignKeyConstraint(
            ["research_run_id", "workspace_id"],
            ["research_runs.id", "research_runs.workspace_id"],
            name="fk_graph_edges_research_run_workspace",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_node_id", "workspace_id", "research_run_id"],
            ["graph_nodes.id", "graph_nodes.workspace_id", "graph_nodes.research_run_id"],
            name="fk_graph_edges_source_node",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_node_id", "workspace_id", "research_run_id"],
            ["graph_nodes.id", "graph_nodes.workspace_id", "graph_nodes.research_run_id"],
            name="fk_graph_edges_target_node",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_graph_edges")),
        sa.UniqueConstraint(
            "workspace_id",
            "research_run_id",
            "source_node_id",
            "target_node_id",
            "relation",
            name=op.f(
                "uq_graph_edges_workspace_id_research_run_id_source_node_id_target_node_id_relation"
            ),
        ),
    )


def downgrade() -> None:
    """Remove only the Day 4 Evidence and Claim ledger."""

    op.drop_table("graph_edges")
    op.drop_table("graph_nodes")
    op.drop_table("claim_evidence")
    op.drop_index(
        op.f("ix_research_claims_workspace_id_research_run_id_created_at"),
        table_name="research_claims",
    )
    op.drop_table("research_claims")
    op.drop_index(
        op.f("ix_evidence_normalization_decisions_workspace_id_observation_id_created_at"),
        table_name="evidence_normalization_decisions",
    )
    op.drop_table("evidence_normalization_decisions")
    op.drop_index(
        op.f("ix_evidence_workspace_id_origin_run_id_origin_tool_call_id"),
        table_name="evidence",
    )
    op.drop_index(op.f("ix_evidence_workspace_id_status_created_at"), table_name="evidence")
    op.drop_table("evidence")
    op.drop_index(
        op.f("ix_research_runs_workspace_id_owner_user_id_created_at"),
        table_name="research_runs",
    )
    op.drop_table("research_runs")
