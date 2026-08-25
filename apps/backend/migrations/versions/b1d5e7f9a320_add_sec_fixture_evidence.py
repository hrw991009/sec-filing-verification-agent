"""Add SEC fixture and calculation Evidence locators.

Revision ID: b1d5e7f9a320
Revises: f7c4a1e9d260
Create Date: 2026-08-25 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b1d5e7f9a320"
down_revision: str | Sequence[str] | None = "f7c4a1e9d260"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "research_briefs",
        sa.Column("financial_scope", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_research_briefs_financial_scope_object"),
        "research_briefs",
        "financial_scope IS NULL OR jsonb_typeof(financial_scope) = 'object'",
    )
    op.add_column("evidence", sa.Column("document_version_id", sa.Uuid(), nullable=True))
    op.add_column("evidence", sa.Column("chunk_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_evidence_document_version_workspace"),
        "evidence",
        "document_versions",
        ["document_version_id", "workspace_id"],
        ["id", "workspace_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_evidence_chunk_version_workspace"),
        "evidence",
        "document_chunks",
        ["chunk_id", "document_version_id", "workspace_id"],
        ["id", "document_version_id", "workspace_id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(op.f("ck_evidence_kind_supported"), "evidence", type_="check")
    op.drop_constraint(op.f("ck_evidence_locator_type_supported"), "evidence", type_="check")
    op.drop_constraint(
        op.f("ck_evidence_source_reference_matches_locator"),
        "evidence",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_evidence_kind_supported"),
        "evidence",
        "kind IN ('web_snapshot', 'sql_result', 'news', 'policy', 'bidding', 'stock', "
        "'filing', 'calculation')",
    )
    op.create_check_constraint(
        op.f("ck_evidence_locator_type_supported"),
        "evidence",
        "locator_type IN ('industry_source_v1', 'sql_result_v1', "
        "'sec_filing_chunk_v1', 'financial_calculation_v1')",
    )
    op.create_check_constraint(
        op.f("ck_evidence_source_reference_matches_locator"),
        "evidence",
        "(locator_type = 'industry_source_v1' AND source_item_id IS NOT NULL "
        "AND query_run_id IS NULL AND document_version_id IS NULL AND chunk_id IS NULL) OR "
        "(locator_type = 'sql_result_v1' AND query_run_id IS NOT NULL "
        "AND source_item_id IS NULL AND document_version_id IS NULL AND chunk_id IS NULL) OR "
        "(locator_type = 'sec_filing_chunk_v1' AND source_item_id IS NULL "
        "AND query_run_id IS NULL AND document_version_id IS NOT NULL AND chunk_id IS NOT NULL) OR "
        "(locator_type = 'financial_calculation_v1' AND source_item_id IS NULL "
        "AND query_run_id IS NULL AND document_version_id IS NULL AND chunk_id IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_evidence_source_reference_matches_locator"),
        "evidence",
        type_="check",
    )
    op.drop_constraint(op.f("ck_evidence_locator_type_supported"), "evidence", type_="check")
    op.drop_constraint(op.f("ck_evidence_kind_supported"), "evidence", type_="check")
    op.create_check_constraint(
        op.f("ck_evidence_kind_supported"),
        "evidence",
        "kind IN ('web_snapshot', 'sql_result', 'news', 'policy', 'bidding', 'stock')",
    )
    op.create_check_constraint(
        op.f("ck_evidence_locator_type_supported"),
        "evidence",
        "locator_type IN ('industry_source_v1', 'sql_result_v1')",
    )
    op.create_check_constraint(
        op.f("ck_evidence_source_reference_matches_locator"),
        "evidence",
        "(locator_type = 'industry_source_v1' AND source_item_id IS NOT NULL "
        "AND query_run_id IS NULL) OR (locator_type = 'sql_result_v1' "
        "AND query_run_id IS NOT NULL AND source_item_id IS NULL)",
    )
    op.drop_constraint(
        op.f("fk_evidence_chunk_version_workspace"),
        "evidence",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_evidence_document_version_workspace"),
        "evidence",
        type_="foreignkey",
    )
    op.drop_column("evidence", "chunk_id")
    op.drop_column("evidence", "document_version_id")
    op.drop_constraint(
        op.f("ck_research_briefs_financial_scope_object"),
        "research_briefs",
        type_="check",
    )
    op.drop_column("research_briefs", "financial_scope")
