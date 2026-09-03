"""Deterministic Evidence locator and Claim semantics."""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from industry_platform.modules.evidence.domain import (
    AuthorizationSnapshot,
    ClaimEvidenceInput,
    ClaimEvidenceRelation,
    ClaimVerificationStatus,
    CreateClaim,
    Evidence,
    EvidenceDecision,
    EvidenceDecisionReason,
    EvidenceKind,
    EvidenceLocatorType,
    EvidenceNormalizationItem,
    EvidenceNormalizationResult,
    EvidencePersistenceError,
    EvidenceStatus,
    FinancialCalculationLocatorV1,
    IndustrySourceLocatorV1,
    InvalidateEvidence,
    SecFilingChunkLocatorV1,
    SecFilingTextLocatorV1,
    SecXbrlFactLocatorV1,
    SqlResultLocatorV1,
    claim_coverage,
    claim_verification_status,
    parse_evidence_locator,
)
from industry_platform.modules.identity.domain import TraceId

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


def sql_locator() -> SqlResultLocatorV1:
    return SqlResultLocatorV1(
        query_run_id=UUID("99999999-9999-4999-8999-999999999999"),
        connection_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        schema_snapshot_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        schema_snapshot_sha256="b" * 64,
        tables=("public.sample_company_metrics",),
        columns=("industry", "revenue"),
        row_start=0,
        row_end=3,
    )


def filing_chunk_locator() -> SecFilingChunkLocatorV1:
    return SecFilingChunkLocatorV1(
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


def filing_text_locator() -> SecFilingTextLocatorV1:
    return SecFilingTextLocatorV1(
        cik="0000320193",
        accession="0000320193-23-000106",
        form="10-K",
        report_period="2023-09-30",
        as_of="2023-11-03T12:00:00+00:00",
        filed_at="2023-11-03T00:00:00+00:00",
        accepted_at="2023-11-03T06:01:00+00:00",
        canonical_url=(
            "https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/aapl-20230930.htm"
        ),
        snapshot_id=SOURCE_ITEM_ID,
        source_version="sec-filing-primary-v1",
        source_content_sha256="b" * 64,
        knowledge_base_id=SOURCE_ITEM_ID,
        document_id=DOCUMENT_ID,
        document_version_id=VERSION_ID,
        chunk_id=CHUNK_ID,
        section="Item 8",
        page_number=29,
        content_sha256="c" * 64,
        parser_version="1.0.0",
        chunker_version="1.0.0",
        index_version="knowledge-index-v1",
        retrieval_profile_version="hybrid-v1",
        retrieval_channels=("dense", "lexical"),
    )


def test_sec_filing_text_locator_accepts_edgar_utc_evening_rollover() -> None:
    locator = replace(
        filing_text_locator(),
        as_of="2023-11-03T12:00:00+00:00",
        filed_at="2023-11-03T00:00:00+00:00",
        accepted_at="2023-11-02T22:08:27+00:00",
    )

    assert locator.accepted_at == "2023-11-02T22:08:27+00:00"


def test_sec_filing_text_locator_accepts_monitor_diff_retrieval_profile() -> None:
    locator = replace(
        filing_text_locator(),
        retrieval_profile_version="monitor-diff-v1",
    )

    assert locator.retrieval_profile_version == "monitor-diff-v1"


def xbrl_locator() -> SecXbrlFactLocatorV1:
    return SecXbrlFactLocatorV1(
        cik="0000320193",
        accession="0000320193-23-000106",
        form="10-K",
        report_period="2023-09-30",
        as_of="2023-11-03T12:00:00+00:00",
        fact_id=EVIDENCE_ID,
        filing_id=RUN_ID,
        source_id=SOURCE_ITEM_ID,
        source_snapshot_id=None,
        source_kind="companyfacts_aggregate",
        taxonomy="us-gaap",
        concept="RevenueFromContractWithCustomerExcludingAssessedTax",
        unit="USD",
        period_kind="duration",
        instant=None,
        start_date="2022-09-25",
        end_date="2023-09-30",
        context_id=None,
        dimensions={},
        decimals=None,
        scale=None,
        source_url="https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
        source_version="sec-companyfacts-v1",
        source_content_sha256="d" * 64,
        content_sha256="e" * 64,
        source_available_at="2023-11-03T06:01:00+00:00",
        retrieved_at="2026-08-27T00:00:00+00:00",
    )


def calculation_locator() -> FinancialCalculationLocatorV1:
    return FinancialCalculationLocatorV1(
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
    sql = sql_locator()

    assert parse_evidence_locator(industry.to_mapping()) == industry
    assert parse_evidence_locator(sql.to_mapping()) == sql
    assert industry.locator_type is EvidenceLocatorType.INDUSTRY_SOURCE_V1
    with pytest.raises(ValueError, match="locator"):
        parse_evidence_locator({**sql.to_mapping(), "row_start": "0"})


def test_filing_and_calculation_locators_preserve_full_lineage() -> None:
    filing = filing_chunk_locator()
    calculation = calculation_locator()

    assert parse_evidence_locator(filing.to_mapping()) == filing
    assert parse_evidence_locator(calculation.to_mapping()) == calculation
    assert calculation.input_evidence_refs == (EVIDENCE_ID, EVIDENCE_ID)


def test_live_sec_locators_round_trip_scope_source_and_context_identity() -> None:
    filing = filing_text_locator()
    xbrl = xbrl_locator()

    assert parse_evidence_locator(filing.to_mapping()) == filing
    assert parse_evidence_locator(xbrl.to_mapping()) == xbrl
    assert xbrl.locator_type is EvidenceLocatorType.SEC_XBRL_FACT_V1


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


@pytest.mark.parametrize(
    ("build_invalid", "message"),
    [
        (lambda: replace(industry_locator(), source_kind="blog"), "source kind"),
        (lambda: replace(industry_locator(), provider="bad provider"), "provider"),
        (lambda: replace(industry_locator(), content_sha256="bad"), "hash"),
        (lambda: replace(industry_locator(), schema_version=2), "schema version"),
        (lambda: replace(sql_locator(), tables=()), "tables"),
        (lambda: replace(sql_locator(), columns=("BadColumn",)), "columns"),
        (lambda: replace(sql_locator(), row_start=True), "row range"),
        (lambda: replace(sql_locator(), row_end=201), "row range"),
        (lambda: replace(sql_locator(), schema_version=2), "schema version"),
        (lambda: replace(filing_chunk_locator(), cik="bad"), "identity"),
        (lambda: replace(filing_chunk_locator(), form="8-K"), "form"),
        (lambda: replace(filing_chunk_locator(), parser_version="bad value"), "parser"),
        (
            lambda: replace(filing_chunk_locator(), canonical_url="http://www.sec.gov/a"),
            "canonical URL",
        ),
        (lambda: replace(filing_chunk_locator(), page_number=0), "page"),
        (lambda: replace(filing_chunk_locator(), schema_version=2), "schema version"),
        (lambda: replace(filing_text_locator(), cik="0000000000"), "identity"),
        (lambda: replace(filing_text_locator(), accepted_at="not-a-time"), "time"),
        (
            lambda: replace(filing_text_locator(), accepted_at="2023-11-04T00:00:00+00:00"),
            "cutoff",
        ),
        (
            lambda: replace(filing_text_locator(), canonical_url="https://example.test/a"),
            "URL",
        ),
        (
            lambda: replace(filing_text_locator(), retrieval_profile_version="unknown-v1"),
            "profile",
        ),
        (
            lambda: replace(filing_text_locator(), retrieval_channels=("dense", "dense")),
            "channels",
        ),
        (lambda: replace(xbrl_locator(), source_kind="unknown"), "identity"),
        (lambda: replace(xbrl_locator(), report_period="bad"), "time"),
        (
            lambda: replace(xbrl_locator(), source_available_at="2023-11-04T00:00:00+00:00"),
            "cutoff",
        ),
        (lambda: replace(xbrl_locator(), start_date=None), "period"),
        (
            lambda: replace(xbrl_locator(), start_date="2024-01-01", end_date="2023-09-30"),
            "period",
        ),
        (
            lambda: replace(xbrl_locator(), source_snapshot_id=SOURCE_ITEM_ID),
            "source boundary",
        ),
        (lambda: replace(xbrl_locator(), dimensions={"bad axis": "member"}), "dimensions"),
        (lambda: replace(xbrl_locator(), source_url="https://example.test/xbrl"), "source URL"),
        (lambda: replace(calculation_locator(), operand_values=("1",)), "inputs"),
        (lambda: replace(calculation_locator(), decimal_places=True), "decimal places"),
        (lambda: replace(calculation_locator(), scale=13), "scale"),
        (
            lambda: replace(calculation_locator(), reconciliation_status="consistent"),
            "incomplete",
        ),
        (
            lambda: replace(
                calculation_locator(),
                reconciliation_status="conflict",
                reconciliation_version="financial-reconciliation-v1",
            ),
            "identity",
        ),
    ],
)
def test_provenance_locators_fail_closed_on_invalid_identity_and_cutoff(
    build_invalid: Callable[[], object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        build_invalid()


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"schema_version": 1, "locator_type": "unknown"},
        {**industry_locator().to_mapping(), "unexpected": True},
        {**sql_locator().to_mapping(), "row_start": "0"},
        {**xbrl_locator().to_mapping(), "dimensions": []},
        {**calculation_locator().to_mapping(), "operand_values": "not-a-list"},
    ],
)
def test_locator_parser_rejects_ambiguous_or_coerced_documents(
    document: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="Evidence locator is invalid"):
        parse_evidence_locator(document)


def test_evidence_and_normalization_commands_reject_inconsistent_security_state() -> None:
    with pytest.raises(ValueError, match="origin is incomplete"):
        replace(evidence(), origin_step_id=None)
    with pytest.raises(ValueError, match="canonical URL"):
        replace(evidence(), canonical_url="http://example.test/source")
    with pytest.raises(ValueError, match="revision"):
        replace(evidence(), revision=0)
    with pytest.raises(ValueError, match="source ordinal"):
        replace(evidence(), origin_source_ordinal=0)
    with pytest.raises(ValueError, match="authorization snapshot"):
        replace(
            evidence(),
            authorization_snapshot=replace(
                evidence().authorization_snapshot,
                workspace_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
            ),
        )

    rejected = EvidenceNormalizationItem(
        source_ordinal=1,
        decision=EvidenceDecision.REJECTED,
        reason=EvidenceDecisionReason.RESOURCE_UNAUTHORIZED,
        evidence=None,
    )
    with pytest.raises(ValueError, match="result is inconsistent"):
        replace(rejected, decision=EvidenceDecision.ACCEPTED)
    with pytest.raises(ValueError, match="accepted reason"):
        EvidenceNormalizationItem(
            source_ordinal=1,
            decision=EvidenceDecision.ACCEPTED,
            reason=EvidenceDecisionReason.RESOURCE_UNAUTHORIZED,
            evidence=evidence(),
        )
    with pytest.raises(ValueError, match="normalizer version"):
        EvidenceNormalizationResult(
            observation_id=OBSERVATION_ID,
            tool_call_id=CALL_ID,
            normalizer_version="unknown",
            items=(rejected,),
        )
    with pytest.raises(ValueError, match="items are invalid"):
        EvidenceNormalizationResult(
            observation_id=OBSERVATION_ID,
            tool_call_id=CALL_ID,
            normalizer_version="evidence-normalizer-v1",
            items=(replace(rejected, source_ordinal=2),),
        )


def test_claim_and_invalidation_commands_reject_ambiguous_mutations() -> None:
    support = ClaimEvidenceInput(EVIDENCE_ID, ClaimEvidenceRelation.SUPPORTS)
    command = CreateClaim(
        research_run_id=RUN_ID,
        statement="Net sales decreased year over year.",
        confidence=0.9,
        relations=(support,),
        origin_run_id=RUN_ID,
        origin_step_id=STEP_ID,
        trace_id=TraceId("evidence-domain-test"),
    )
    with pytest.raises(ValueError, match="confidence"):
        replace(command, confidence=True)
    with pytest.raises(ValueError, match="relations"):
        replace(command, relations=(support, support))
    with pytest.raises(ValueError, match="expected revision"):
        InvalidateEvidence(
            evidence_id=EVIDENCE_ID,
            expected_revision=0,
            status=EvidenceStatus.TOMBSTONED,
            reason="withdrawn",
            trace_id=TraceId("evidence-domain-test"),
        )
    with pytest.raises(ValueError, match="status"):
        InvalidateEvidence(
            evidence_id=EVIDENCE_ID,
            expected_revision=1,
            status=EvidenceStatus.ACTIVE,
            reason="withdrawn",
            trace_id=TraceId("evidence-domain-test"),
        )

    error = EvidencePersistenceError(sqlstate="40001")
    assert error.sqlstate == "40001"
