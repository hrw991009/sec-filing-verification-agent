"""Deterministic Evidence locator and Claim semantics."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from industry_platform.modules.evidence.domain import (
    AuthorizationSnapshot,
    ClaimEvidenceInput,
    ClaimEvidenceRelation,
    ClaimVerificationStatus,
    Evidence,
    EvidenceKind,
    EvidenceLocatorType,
    EvidenceStatus,
    FinancialCalculationLocatorV1,
    IndustrySourceLocatorV1,
    SecFilingChunkLocatorV1,
    SqlResultLocatorV1,
    claim_coverage,
    claim_verification_status,
    parse_evidence_locator,
)

NOW = datetime(2026, 8, 21, 8, 0, tzinfo=UTC)
WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
USER_ID = UUID("22222222-2222-4222-8222-222222222222")
EVIDENCE_ID = UUID("33333333-3333-4333-8333-333333333333")
RUN_ID = UUID("44444444-4444-4444-8444-444444444444")
STEP_ID = UUID("55555555-5555-4555-8555-555555555555")
CALL_ID = UUID("66666666-6666-4666-8666-666666666666")
OBSERVATION_ID = UUID("77777777-7777-4777-8777-777777777777")
SOURCE_ITEM_ID = UUID("88888888-8888-4888-8888-888888888888")
DOCUMENT_ID = UUID("99999999-9999-4999-8999-999999999998")
VERSION_ID = UUID("99999999-9999-4999-8999-999999999997")
CHUNK_ID = UUID("99999999-9999-4999-8999-999999999996")


def industry_locator() -> IndustrySourceLocatorV1:
    return IndustrySourceLocatorV1(
        source_item_id=SOURCE_ITEM_ID,
        source_kind="news",
        provider="world_bank_news",
        source_version="api-v2-2026-08",
        content_sha256="a" * 64,
    )


def evidence(*, status: EvidenceStatus = EvidenceStatus.ACTIVE) -> Evidence:
    active = status is EvidenceStatus.ACTIVE
    return Evidence(
        evidence_id=EVIDENCE_ID,
        workspace_id=WORKSPACE_ID,
        kind=EvidenceKind.NEWS,
        title="A durable source snapshot",
        canonical_url="https://example.test/source",
        locator=industry_locator(),
        excerpt="A bounded attributable excerpt." if active else None,
        content_sha256="a" * 64,
        source_published_at=NOW,
        retrieved_at=NOW,
        license_or_terms="Public metadata with attribution.",
        status=status,
        revision=1 if active else 2,
        invalidated_at=None if active else NOW,
        invalidation_reason=None if active else "withdrawn",
        origin_run_id=RUN_ID,
        origin_step_id=STEP_ID,
        origin_tool_call_id=CALL_ID,
        origin_observation_id=OBSERVATION_ID,
        origin_source_ordinal=1,
        normalizer_version="evidence-normalizer-v1",
        authorization_snapshot=AuthorizationSnapshot(
            workspace_id=WORKSPACE_ID,
            actor_user_id=USER_ID,
            role="owner",
            action="evidence.normalize",
            captured_at=NOW,
        ),
        source_resource_version="api-v2-2026-08:aaaaaaaa",
        created_at=NOW,
        updated_at=NOW,
    )


def test_locators_round_trip_without_implicit_coercion() -> None:
    industry = industry_locator()
    sql = SqlResultLocatorV1(
        query_run_id=UUID("99999999-9999-4999-8999-999999999999"),
        connection_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        schema_snapshot_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        schema_snapshot_sha256="b" * 64,
        tables=("public.sample_company_metrics",),
        columns=("industry", "revenue"),
        row_start=0,
        row_end=3,
    )

    assert parse_evidence_locator(industry.to_mapping()) == industry
    assert parse_evidence_locator(sql.to_mapping()) == sql
    assert industry.locator_type is EvidenceLocatorType.INDUSTRY_SOURCE_V1
    with pytest.raises(ValueError, match="locator"):
        parse_evidence_locator({**sql.to_mapping(), "row_start": "0"})


def test_filing_and_calculation_locators_preserve_full_lineage() -> None:
    filing = SecFilingChunkLocatorV1(
        cik="0000320193",
        accession="0000320193-23-000106",
        form="10-K",
        report_period="2023-09-30",
        filed_at="2023-11-03T00:00:00+00:00",
        accepted_at="2023-11-02T18:08:27+00:00",
        primary_document="aapl-20230930.htm",
        canonical_url=(
            "https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/aapl-20230930.htm"
        ),
        dataset_version="sec-fixture-v1",
        fixture_sha256="b" * 64,
        knowledge_base_id=SOURCE_ITEM_ID,
        document_id=DOCUMENT_ID,
        document_version_id=VERSION_ID,
        chunk_id=CHUNK_ID,
        section="Item 8. Consolidated Statements of Operations",
        page_number=29,
        content_sha256="c" * 64,
        parser_version="1.0.0",
        chunker_version="1.0.0",
        index_version="knowledge-index-v1",
    )
    calculation = FinancialCalculationLocatorV1(
        financial_scope={
            "schema_version": 1,
            "cik": "0000320193",
            "accession": "0000320193-23-000106",
            "form": "10-K",
            "report_period": "2023-09-30",
            "as_of": "2023-11-03T12:00:00+00:00",
            "unit": "USD",
            "scale": 6,
        },
        operator="percent_change",
        operand_values=("383285", "394328"),
        input_evidence_refs=(EVIDENCE_ID, EVIDENCE_ID),
        decimal_places=2,
        rounding_mode="half_even",
        formula="((383285 - 394328) / 394328) * 100",
        result="-2.80",
        unit="PERCENT",
        scale=0,
        observation_sha256="d" * 64,
    )

    assert parse_evidence_locator(filing.to_mapping()) == filing
    assert parse_evidence_locator(calculation.to_mapping()) == calculation
    assert calculation.input_evidence_refs == (EVIDENCE_ID, EVIDENCE_ID)


def test_evidence_lifecycle_requires_excerpt_or_explicit_invalidation() -> None:
    assert evidence().excerpt is not None
    assert evidence(status=EvidenceStatus.TOMBSTONED).excerpt is None

    with pytest.raises(ValueError, match="lifecycle"):
        replace(evidence(), excerpt=None)


def test_claim_status_and_coverage_are_derived_from_active_relations() -> None:
    support = ClaimEvidenceInput(EVIDENCE_ID, ClaimEvidenceRelation.SUPPORTS)
    refute = ClaimEvidenceInput(
        UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        ClaimEvidenceRelation.REFUTES,
    )
    context = ClaimEvidenceInput(
        UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
        ClaimEvidenceRelation.CONTEXT,
    )

    assert claim_verification_status((support,)) is ClaimVerificationStatus.SUPPORTED
    assert claim_verification_status((refute,)) is ClaimVerificationStatus.REFUTED
    assert claim_verification_status((support, refute)) is ClaimVerificationStatus.CONFLICTED
    assert claim_verification_status((context,)) is ClaimVerificationStatus.UNCERTAIN
    assert claim_coverage((support, context)) == 0.5
