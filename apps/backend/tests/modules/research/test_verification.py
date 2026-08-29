"""Deterministic SEC Claim verifier decision-table tests."""

from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from industry_platform.modules.evidence.domain import (
    AuthorizationSnapshot,
    ClaimEvidenceLink,
    ClaimEvidenceRelation,
    ClaimVerificationStatus,
    Evidence,
    EvidenceKind,
    EvidenceStatus,
    FinancialCalculationLocatorV1,
    RelationStatus,
    ResearchClaim,
    SecFilingTextLocatorV1,
    SecXbrlFactLocatorV1,
)
from industry_platform.modules.financial_verification.domain import FinancialForm, FinancialScope
from industry_platform.modules.research.verification import (
    VerificationClaimVerdict,
    VerificationEvidenceState,
    VerificationIssueCode,
    VerificationSnapshot,
    VerificationStatus,
    evaluate_verification_snapshot,
)

NOW = datetime(2026, 8, 29, 8, 0, tzinfo=UTC)
AS_OF = datetime(2023, 11, 3, 12, 0, tzinfo=UTC)
WORKSPACE_ID = UUID("81000000-0000-4000-8000-000000000001")
USER_ID = UUID("81000000-0000-4000-8000-000000000002")
RESEARCH_RUN_ID = UUID("81000000-0000-4000-8000-000000000003")
AGENT_RUN_ID = UUID("81000000-0000-4000-8000-000000000004")
DRAFT_ID = UUID("81000000-0000-4000-8000-000000000005")
REPORT_ID = UUID("81000000-0000-4000-8000-000000000006")
STEP_ID = UUID("81000000-0000-4000-8000-000000000007")
CALL_ID = UUID("81000000-0000-4000-8000-000000000008")
OBSERVATION_ID = UUID("81000000-0000-4000-8000-000000000009")
KNOWLEDGE_BASE_ID = UUID("81000000-0000-4000-8000-000000000010")
DOCUMENT_ID = UUID("81000000-0000-4000-8000-000000000011")
DOCUMENT_VERSION_ID = UUID("81000000-0000-4000-8000-000000000012")
CHUNK_ID = UUID("81000000-0000-4000-8000-000000000013")
SNAPSHOT_ID = UUID("81000000-0000-4000-8000-000000000014")


def financial_scope() -> FinancialScope:
    return FinancialScope(
        cik="0000320193",
        accession="0000320193-23-000106",
        form=FinancialForm.TEN_K,
        report_period=date(2023, 9, 30),
        as_of=AS_OF,
        unit="USD",
        scale=6,
    )


def filing_evidence(evidence_id: UUID, *, content_hash: str) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        workspace_id=WORKSPACE_ID,
        kind=EvidenceKind.FILING,
        title="Apple 2023 Form 10-K filing excerpt",
        canonical_url=(
            "https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/aapl-20230930.htm"
        ),
        locator=SecFilingTextLocatorV1(
            cik="0000320193",
            accession="0000320193-23-000106",
            form="10-K",
            report_period="2023-09-30",
            as_of=AS_OF.isoformat(),
            filed_at="2023-11-03T00:00:00+00:00",
            accepted_at="2023-11-03T06:01:00+00:00",
            canonical_url=(
                "https://www.sec.gov/Archives/edgar/data/320193/"
                "000032019323000106/aapl-20230930.htm"
            ),
            snapshot_id=SNAPSHOT_ID,
            source_version="sec-filing-primary-v1",
            source_content_sha256="b" * 64,
            knowledge_base_id=KNOWLEDGE_BASE_ID,
            document_id=DOCUMENT_ID,
            document_version_id=DOCUMENT_VERSION_ID,
            chunk_id=CHUNK_ID,
            section="Item 8",
            page_number=29,
            content_sha256=content_hash,
            parser_version="1.0.0",
            chunker_version="1.0.0",
            index_version="knowledge-index-v1",
            retrieval_profile_version="hybrid-v1",
            retrieval_channels=("dense", "lexical"),
        ),
        excerpt="Net sales were reported in the audited filing.",
        content_sha256=content_hash,
        source_published_at=AS_OF,
        retrieved_at=NOW,
        license_or_terms="Official SEC public filing.",
        status=EvidenceStatus.ACTIVE,
        revision=1,
        invalidated_at=None,
        invalidation_reason=None,
        origin_run_id=AGENT_RUN_ID,
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
        source_resource_version="sec-filing-primary-v1",
        created_at=NOW,
        updated_at=NOW,
    )


def claim(
    claim_id: UUID,
    *relations: tuple[Evidence, ClaimEvidenceRelation],
) -> ResearchClaim:
    links = tuple(
        ClaimEvidenceLink(
            evidence=evidence,
            relation=relation,
            relation_version=1,
            status=RelationStatus.ACTIVE,
            ordinal=ordinal,
            origin_run_id=AGENT_RUN_ID,
            origin_step_id=STEP_ID,
        )
        for ordinal, (evidence, relation) in enumerate(relations, start=1)
    )
    kinds = {relation for _evidence, relation in relations}
    if kinds == {ClaimEvidenceRelation.SUPPORTS}:
        stored_status = ClaimVerificationStatus.SUPPORTED
    elif kinds == {ClaimEvidenceRelation.REFUTES}:
        stored_status = ClaimVerificationStatus.REFUTED
    elif {
        ClaimEvidenceRelation.SUPPORTS,
        ClaimEvidenceRelation.REFUTES,
    }.issubset(kinds):
        stored_status = ClaimVerificationStatus.CONFLICTED
    else:
        stored_status = ClaimVerificationStatus.UNCERTAIN
    return ResearchClaim(
        claim_id=claim_id,
        workspace_id=WORKSPACE_ID,
        research_run_id=RESEARCH_RUN_ID,
        statement="A critical SEC filing Claim.",
        confidence=0.9,
        verification_status=stored_status,
        coverage=1 if links else 0,
        conflict=stored_status is ClaimVerificationStatus.CONFLICTED,
        revision=1,
        relations=links,
        created_at=NOW,
        updated_at=NOW,
    )


def snapshot(
    claims: tuple[ResearchClaim, ...],
    evidence_states: tuple[VerificationEvidenceState, ...],
    *,
    required_claim_ids: tuple[UUID, ...] | None = None,
) -> VerificationSnapshot:
    return VerificationSnapshot(
        report_id=REPORT_ID,
        research_run_id=RESEARCH_RUN_ID,
        agent_run_id=AGENT_RUN_ID,
        workspace_id=WORKSPACE_ID,
        draft_id=DRAFT_ID,
        revision=1,
        graph_version="research-l4-graph-v1",
        financial_scope=financial_scope(),
        required_claim_ids=required_claim_ids or tuple(item.claim_id for item in claims),
        claims=claims,
        evidence_states=evidence_states,
        runtime_stop_reason=None,
        created_at=NOW,
    )


def test_all_supported_claims_produce_verified_without_false_support() -> None:
    evidence = filing_evidence(UUID("81000000-0000-4000-8000-000000000101"), content_hash="a" * 64)
    selected_claim = claim(
        UUID("81000000-0000-4000-8000-000000000201"),
        (evidence, ClaimEvidenceRelation.SUPPORTS),
    )

    report = evaluate_verification_snapshot(
        snapshot(
            (selected_claim,),
            (VerificationEvidenceState(evidence=evidence, available=True),),
        )
    )

    assert report.status is VerificationStatus.VERIFIED
    assert report.coverage == 1
    assert report.claims[0].verdict is VerificationClaimVerdict.SUPPORTED
    assert report.issues == ()


def test_supported_and_missing_claims_produce_partial_with_explicit_coverage_issue() -> None:
    evidence = filing_evidence(UUID("81000000-0000-4000-8000-000000000102"), content_hash="b" * 64)
    supported_claim = claim(
        UUID("81000000-0000-4000-8000-000000000202"),
        (evidence, ClaimEvidenceRelation.SUPPORTS),
    )
    missing_claim_id = UUID("81000000-0000-4000-8000-000000000203")

    report = evaluate_verification_snapshot(
        snapshot(
            (supported_claim,),
            (VerificationEvidenceState(evidence=evidence, available=True),),
            required_claim_ids=(supported_claim.claim_id, missing_claim_id),
        )
    )

    assert report.status is VerificationStatus.PARTIAL
    assert report.coverage == 0.5
    assert {issue.code for issue in report.issues} == {
        VerificationIssueCode.CLAIM_NOT_FOUND,
        VerificationIssueCode.COVERAGE_INCOMPLETE,
    }


def test_mutually_valid_support_and_refute_produce_conflict() -> None:
    support = filing_evidence(UUID("81000000-0000-4000-8000-000000000103"), content_hash="c" * 64)
    refute = filing_evidence(UUID("81000000-0000-4000-8000-000000000104"), content_hash="d" * 64)
    selected_claim = claim(
        UUID("81000000-0000-4000-8000-000000000204"),
        (support, ClaimEvidenceRelation.SUPPORTS),
        (refute, ClaimEvidenceRelation.REFUTES),
    )

    report = evaluate_verification_snapshot(
        snapshot(
            (selected_claim,),
            (
                VerificationEvidenceState(evidence=support, available=True),
                VerificationEvidenceState(evidence=refute, available=True),
            ),
        )
    )

    assert report.status is VerificationStatus.CONFLICT
    assert report.claims[0].verdict is VerificationClaimVerdict.CONFLICTING
    assert report.issues[0].code is VerificationIssueCode.CLAIM_CONFLICT


@pytest.mark.parametrize("available", [False, True])
def test_unresolvable_or_refuting_only_evidence_never_produces_verified(available: bool) -> None:
    evidence = filing_evidence(UUID("81000000-0000-4000-8000-000000000105"), content_hash="e" * 64)
    relation = ClaimEvidenceRelation.SUPPORTS if not available else ClaimEvidenceRelation.REFUTES
    selected_claim = claim(
        UUID("81000000-0000-4000-8000-000000000205"),
        (evidence, relation),
    )

    report = evaluate_verification_snapshot(
        snapshot(
            (selected_claim,),
            (VerificationEvidenceState(evidence=evidence, available=available),),
        )
    )

    assert report.status is VerificationStatus.INSUFFICIENT_EVIDENCE
    assert report.claims[0].verdict in {
        VerificationClaimVerdict.INSUFFICIENT,
        VerificationClaimVerdict.REFUTED,
    }


def test_calculation_evidence_is_recomputed_before_support_is_accepted() -> None:
    left = xbrl_evidence(UUID("81000000-0000-4000-8000-000000000106"), "f" * 64)
    right = xbrl_evidence(UUID("81000000-0000-4000-8000-000000000107"), "1" * 64)
    calculation = calculation_evidence(UUID("81000000-0000-4000-8000-000000000108"), left, right)
    selected_claim = claim(
        UUID("81000000-0000-4000-8000-000000000206"),
        (calculation, ClaimEvidenceRelation.SUPPORTS),
    )
    states = tuple(
        VerificationEvidenceState(evidence=item, available=True)
        for item in (left, right, calculation)
    )

    verified = evaluate_verification_snapshot(snapshot((selected_claim,), states))
    assert isinstance(calculation.locator, FinancialCalculationLocatorV1)
    tampered = replace(
        calculation,
        locator=replace(calculation.locator, result="31.00"),
    )
    tampered_claim = claim(selected_claim.claim_id, (tampered, ClaimEvidenceRelation.SUPPORTS))
    rejected = evaluate_verification_snapshot(
        snapshot(
            (tampered_claim,),
            (
                VerificationEvidenceState(evidence=left, available=True),
                VerificationEvidenceState(evidence=right, available=True),
                VerificationEvidenceState(evidence=tampered, available=True),
            ),
        )
    )

    assert verified.status is VerificationStatus.VERIFIED
    assert rejected.status is VerificationStatus.INSUFFICIENT_EVIDENCE
    assert VerificationIssueCode.CALCULATION_MISMATCH in {issue.code for issue in rejected.issues}


def test_comparative_period_xbrl_fact_cannot_support_current_period_claim() -> None:
    evidence = xbrl_evidence(UUID("81000000-0000-4000-8000-000000000109"), "4" * 64)
    assert isinstance(evidence.locator, SecXbrlFactLocatorV1)
    comparative = replace(
        evidence,
        locator=replace(
            evidence.locator,
            start_date="2021-09-26",
            end_date="2022-09-24",
        ),
    )
    selected_claim = claim(
        UUID("81000000-0000-4000-8000-000000000207"),
        (comparative, ClaimEvidenceRelation.SUPPORTS),
    )

    report = evaluate_verification_snapshot(
        snapshot(
            (selected_claim,),
            (VerificationEvidenceState(evidence=comparative, available=True),),
        )
    )

    assert report.status is VerificationStatus.INSUFFICIENT_EVIDENCE
    assert VerificationIssueCode.SCOPE_IDENTITY_MISMATCH in {issue.code for issue in report.issues}


def xbrl_evidence(evidence_id: UUID, content_hash: str) -> Evidence:
    evidence = filing_evidence(evidence_id, content_hash=content_hash)
    return replace(
        evidence,
        locator=SecXbrlFactLocatorV1(
            cik="0000320193",
            accession="0000320193-23-000106",
            form="10-K",
            report_period="2023-09-30",
            as_of=AS_OF.isoformat(),
            fact_id=evidence_id,
            filing_id=UUID("81000000-0000-4000-8000-000000000301"),
            source_id=UUID("81000000-0000-4000-8000-000000000302"),
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
            scale=6,
            source_url="https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
            source_version="sec-companyfacts-v1",
            source_content_sha256="2" * 64,
            content_sha256=content_hash,
            source_available_at="2023-11-03T06:01:00+00:00",
            retrieved_at=NOW.isoformat(),
        ),
    )


def calculation_evidence(evidence_id: UUID, left: Evidence, right: Evidence) -> Evidence:
    evidence = filing_evidence(evidence_id, content_hash="3" * 64)
    return replace(
        evidence,
        kind=EvidenceKind.CALCULATION,
        locator=FinancialCalculationLocatorV1(
            financial_scope=dict(financial_scope().to_mapping()),
            operator="add",
            operand_values=("10", "20"),
            input_evidence_refs=(left.evidence_id, right.evidence_id),
            decimal_places=2,
            rounding_mode="half_even",
            formula="10 + 20",
            result="30.00",
            unit="USD",
            scale=6,
            observation_sha256="3" * 64,
            reconciliation_status="consistent",
            reconciliation_version="financial-reconciliation-v1",
        ),
    )
