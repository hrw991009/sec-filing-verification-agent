"""Deterministic financial calculation domain tests."""

from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from industry_platform.modules.financial_verification.domain import (
    FinancialCalculation,
    FinancialEvidenceOperand,
    FinancialForm,
    FinancialOperand,
    FinancialOperator,
    FinancialPeriodKind,
    FinancialReconciliationIssueCode,
    FinancialReconciliationStatus,
    FinancialScope,
    calculate_financial_result,
    reconcile_financial_operands,
    sec_xbrl_evidence_ref,
)

EVIDENCE_ID = UUID("11111111-1111-4111-8111-111111111111")
SECOND_EVIDENCE_ID = UUID("22222222-2222-4222-8222-222222222222")
WORKSPACE_ID = UUID("33333333-3333-4333-8333-333333333333")
FACT_ID = UUID("44444444-4444-4444-8444-444444444444")


def scope() -> FinancialScope:
    return FinancialScope(
        cik="0000320193",
        accession="0000320193-23-000106",
        form=FinancialForm.TEN_K,
        report_period=date(2023, 9, 30),
        as_of=datetime(2023, 11, 3, tzinfo=UTC),
        unit="USD",
        scale=6,
    )


def evidence_operand(**changes: object) -> FinancialEvidenceOperand:
    values: dict[str, object] = {
        "evidence_ref": EVIDENCE_ID,
        "source_fact_id": FACT_ID,
        "value": "383285",
        "cik": "0000320193",
        "accession": "0000320193-23-000106",
        "form": FinancialForm.TEN_K,
        "report_period": date(2023, 9, 30),
        "unit": "USD",
        "scale": 6,
        "period_kind": FinancialPeriodKind.DURATION,
        "instant": None,
        "start_date": date(2022, 10, 1),
        "end_date": date(2023, 9, 30),
        "context_id": "D2023",
        "dimensions": (),
        "taxonomy": "us-gaap",
        "concept": "Revenue",
        "is_custom": False,
        "source_kind": "raw_instance",
        "source_version": "sec-xbrl-raw-instance-v1",
        "source_available_at": datetime(2023, 11, 3, tzinfo=UTC),
        "amendment_relation_status": "not_amendment",
        "base_accession": None,
    }
    values.update(changes)
    return FinancialEvidenceOperand(**values)  # type: ignore[arg-type]


def test_percent_change_is_decimal_deterministic_and_allows_one_shared_source() -> None:
    result = calculate_financial_result(
        scope(),
        FinancialCalculation(
            operator=FinancialOperator.PERCENT_CHANGE,
            operands=(
                FinancialOperand(value="383285", evidence_ref=EVIDENCE_ID),
                FinancialOperand(value="394328", evidence_ref=EVIDENCE_ID),
            ),
            decimal_places=2,
        ),
    )

    assert result.value == "-2.80"
    assert result.formula == "((383285 - 394328) / 394328) * 100"
    assert result.unit == "PERCENT"
    assert result.scale == 0
    assert result.evidence_refs == (EVIDENCE_ID, EVIDENCE_ID)


def test_calculation_rejects_noncanonical_numbers_and_zero_divisors() -> None:
    with pytest.raises(ValueError, match="canonical"):
        FinancialOperand(value="0383285", evidence_ref=EVIDENCE_ID)

    with pytest.raises(ValueError, match="division by zero"):
        calculate_financial_result(
            scope(),
            FinancialCalculation(
                operator=FinancialOperator.RATIO,
                operands=(
                    FinancialOperand(value="1", evidence_ref=EVIDENCE_ID),
                    FinancialOperand(value="0", evidence_ref=EVIDENCE_ID),
                ),
                decimal_places=2,
            ),
        )


def test_financial_scope_round_trips_without_losing_as_of_or_scale() -> None:
    restored = FinancialScope.from_mapping(dict(scope().to_mapping()))

    assert restored == scope()


def test_calculation_normalizes_operand_scales_into_scope_scale() -> None:
    result = calculate_financial_result(
        scope(),
        FinancialCalculation(
            operator=FinancialOperator.ADD,
            operands=(
                FinancialOperand(
                    value="1.25",
                    evidence_ref=EVIDENCE_ID,
                    unit="USD",
                    scale=6,
                ),
                FinancialOperand(
                    value="750000",
                    evidence_ref=SECOND_EVIDENCE_ID,
                    unit="USD",
                    scale=0,
                ),
            ),
            decimal_places=2,
        ),
    )

    assert result.value == "2.00"
    assert result.formula == "1.25 + 0.750000"
    assert (result.unit, result.scale) == ("USD", 6)


def test_percentage_is_distinct_from_ratio_and_uses_percent_unit() -> None:
    result = calculate_financial_result(
        scope(),
        FinancialCalculation(
            operator=FinancialOperator.PERCENTAGE,
            operands=(
                FinancialOperand(value="1", evidence_ref=EVIDENCE_ID),
                FinancialOperand(value="8", evidence_ref=SECOND_EVIDENCE_ID),
            ),
            decimal_places=2,
        ),
    )

    assert result.value == "12.50"
    assert result.formula == "(1 / 8) * 100"
    assert (result.unit, result.scale) == ("PERCENT", 0)


def test_reconciliation_accepts_comparable_period_change_and_preserves_lineage() -> None:
    current = evidence_operand()
    previous = evidence_operand(
        evidence_ref=SECOND_EVIDENCE_ID,
        source_fact_id=UUID("55555555-5555-4555-8555-555555555555"),
        value="394328",
        start_date=date(2021, 10, 2),
        end_date=date(2022, 10, 1),
        context_id="D2022",
        scale=3,
    )

    result = reconcile_financial_operands(
        scope(),
        FinancialOperator.PERCENT_CHANGE,
        (current, previous),
    )

    assert result.status is FinancialReconciliationStatus.CONSISTENT
    assert result.issues == ()
    assert result.evidence_refs == (EVIDENCE_ID, SECOND_EVIDENCE_ID)


@pytest.mark.parametrize(
    ("changed", "expected_status", "expected_issue"),
    [
        (
            {"unit": "EUR"},
            FinancialReconciliationStatus.CONFLICT,
            FinancialReconciliationIssueCode.UNIT_MISMATCH,
        ),
        (
            {
                "period_kind": FinancialPeriodKind.INSTANT,
                "instant": date(2022, 10, 1),
                "start_date": None,
                "end_date": None,
            },
            FinancialReconciliationStatus.NOT_COMPARABLE,
            FinancialReconciliationIssueCode.PERIOD_KIND_MISMATCH,
        ),
        (
            {"dimensions": (("dei:LegalEntityAxis", "aapl:AppleIncMember"),)},
            FinancialReconciliationStatus.NOT_COMPARABLE,
            FinancialReconciliationIssueCode.DIMENSIONS_MISMATCH,
        ),
        (
            {"taxonomy": "aapl", "concept": "CustomRevenue", "is_custom": True},
            FinancialReconciliationStatus.NOT_COMPARABLE,
            FinancialReconciliationIssueCode.CONCEPT_MISMATCH,
        ),
    ],
)
def test_reconciliation_fails_closed_for_incomparable_operands(
    changed: dict[str, object],
    expected_status: FinancialReconciliationStatus,
    expected_issue: FinancialReconciliationIssueCode,
) -> None:
    current = evidence_operand()
    previous_values: dict[str, object] = {
        "evidence_ref": SECOND_EVIDENCE_ID,
        "source_fact_id": UUID("55555555-5555-4555-8555-555555555555"),
        "start_date": date(2021, 10, 2),
        "end_date": date(2022, 10, 1),
        "context_id": "D2022",
    }
    previous_values.update(changed)
    previous = evidence_operand(**previous_values)

    result = reconcile_financial_operands(
        scope(),
        FinancialOperator.PERCENT_CHANGE,
        (current, previous),
    )

    assert result.status is expected_status
    assert expected_issue in {issue.code for issue in result.issues}


def test_reconciliation_rejects_unresolved_amendment() -> None:
    amendment_scope = replace(scope(), form=FinancialForm.TEN_K_AMENDMENT)
    operand = evidence_operand(
        form=FinancialForm.TEN_K_AMENDMENT,
        amendment_relation_status="unresolved",
    )
    result = reconcile_financial_operands(
        amendment_scope,
        FinancialOperator.ADD,
        (operand, replace(operand, evidence_ref=SECOND_EVIDENCE_ID)),
    )

    assert result.status is FinancialReconciliationStatus.CONFLICT
    assert FinancialReconciliationIssueCode.AMENDMENT_UNRESOLVED in {
        issue.code for issue in result.issues
    }


def test_sec_xbrl_evidence_ref_is_workspace_scoped_and_deterministic() -> None:
    first = sec_xbrl_evidence_ref(
        workspace_id=WORKSPACE_ID,
        fact_id=FACT_ID,
        as_of=scope().as_of,
        authorization_role="member",
    )

    assert first == sec_xbrl_evidence_ref(
        workspace_id=WORKSPACE_ID,
        fact_id=FACT_ID,
        as_of=scope().as_of,
        authorization_role="member",
    )
    assert first != sec_xbrl_evidence_ref(
        workspace_id=UUID("66666666-6666-4666-8666-666666666666"),
        fact_id=FACT_ID,
        as_of=scope().as_of,
        authorization_role="member",
    )
