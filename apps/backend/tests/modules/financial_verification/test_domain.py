"""Deterministic financial calculation domain tests."""

from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from industry_platform.modules.financial_verification.domain import (
    FinancialCalculation,
    FinancialForm,
    FinancialOperand,
    FinancialOperator,
    FinancialScope,
    calculate_financial_result,
)

EVIDENCE_ID = UUID("11111111-1111-4111-8111-111111111111")


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
