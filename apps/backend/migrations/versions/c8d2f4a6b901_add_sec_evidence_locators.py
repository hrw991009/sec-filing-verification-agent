"""Add live SEC filing text and XBRL fact Evidence locators.

Revision ID: c8d2f4a6b901
Revises: b7c9d1e3f468
Create Date: 2026-08-27 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c8d2f4a6b901"
down_revision: str | Sequence[str] | None = "b7c9d1e3f468"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_evidence_locator_type_supported"), "evidence", type_="check")
    op.drop_constraint(
        op.f("ck_evidence_source_reference_matches_locator"),
        "evidence",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_evidence_locator_type_supported"),
        "evidence",
        "locator_type IN ('industry_source_v1', 'sql_result_v1', "
        "'sec_filing_chunk_v1', 'sec_filing_text_v1', 'sec_xbrl_fact_v1', "
        "'financial_calculation_v1')",
    )
    op.create_check_constraint(
        op.f("ck_evidence_source_reference_matches_locator"),
        "evidence",
        _source_reference_constraint(include_live_sec=True),
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_evidence_source_reference_matches_locator"),
        "evidence",
        type_="check",
    )
    op.drop_constraint(op.f("ck_evidence_locator_type_supported"), "evidence", type_="check")
    op.create_check_constraint(
        op.f("ck_evidence_locator_type_supported"),
        "evidence",
        "locator_type IN ('industry_source_v1', 'sql_result_v1', "
        "'sec_filing_chunk_v1', 'financial_calculation_v1')",
    )
    op.create_check_constraint(
        op.f("ck_evidence_source_reference_matches_locator"),
        "evidence",
        _source_reference_constraint(include_live_sec=False),
    )


def _source_reference_constraint(*, include_live_sec: bool) -> str:
    clauses = [
        "(locator_type = 'industry_source_v1' AND source_item_id IS NOT NULL "
        "AND query_run_id IS NULL AND document_version_id IS NULL AND chunk_id IS NULL)",
        "(locator_type = 'sql_result_v1' AND query_run_id IS NOT NULL "
        "AND source_item_id IS NULL AND document_version_id IS NULL AND chunk_id IS NULL)",
        "(locator_type = 'sec_filing_chunk_v1' AND source_item_id IS NULL "
        "AND query_run_id IS NULL AND document_version_id IS NOT NULL AND chunk_id IS NOT NULL)",
    ]
    if include_live_sec:
        clauses.extend(
            (
                "(locator_type = 'sec_filing_text_v1' AND source_item_id IS NULL "
                "AND query_run_id IS NULL AND document_version_id IS NOT NULL "
                "AND chunk_id IS NOT NULL)",
                "(locator_type = 'sec_xbrl_fact_v1' AND source_item_id IS NULL "
                "AND query_run_id IS NULL AND document_version_id IS NULL "
                "AND chunk_id IS NULL)",
            )
        )
    clauses.append(
        "(locator_type = 'financial_calculation_v1' AND source_item_id IS NULL "
        "AND query_run_id IS NULL AND document_version_id IS NULL AND chunk_id IS NULL)"
    )
    return " OR ".join(clauses)
