"""DuPont arithmetic, fiscal alignment and parent/consolidated boundaries."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Any
from uuid import uuid4

import pytest

from industry_platform.modules.agent_runtime.context import ToolObservationContextSource
from industry_platform.modules.financial_verification.domain import (
    FinancialCalculation,
    FinancialEvidenceOperand,
    FinancialForm,
    FinancialOperand,
    FinancialOperator,
    FinancialPeriodKind,
    FinancialReconciliationStatus,
    FinancialScope,
    calculate_financial_result,
    reconcile_financial_operands,
)
from industry_platform.modules.financial_verification.dupont import (
    calculate_dupont_components,
    dupont_filing_in_scope,
)
from industry_platform.modules.financial_verification.schemas import FinancialEvidenceOperandPayload
from industry_platform.modules.financial_verification.tool import FinanceCalculateOutput
from industry_platform.modules.research.dupont_report import render_dupont_report


def dupont_scope() -> FinancialScope:
    return FinancialScope(
        "0000789019",
        "0000950170-24-087843",
        FinancialForm.TEN_K,
        date(2024, 6, 30),
        datetime(2026, 9, 15, tzinfo=UTC),
        "USD",
        6,
    )


def dupont_operands() -> tuple[FinancialEvidenceOperand, ...]:
    scope = dupont_scope()
    return tuple(
        FinancialEvidenceOperand(
            evidence_ref=uuid4(),
            source_fact_id=uuid4(),
            value=value,
            cik=scope.cik,
            accession=scope.accession,
            form=scope.form,
            report_period=scope.report_period,
            unit="USD",
            scale=6,
            period_kind=FinancialPeriodKind.DURATION if index < 2 else FinancialPeriodKind.INSTANT,
            instant=None if index < 2 else date(2023 if index % 2 == 0 else 2024, 6, 30),
            start_date=date(2023, 7, 1) if index < 2 else None,
            end_date=scope.report_period if index < 2 else None,
            context_id=f"c{index}",
            dimensions=(),
            taxonomy="us-gaap",
            concept=concept,
            is_custom=False,
            source_kind="raw_inline",
            source_version="dupont-test-v1",
            source_available_at=datetime(2024, 8, 1, tzinfo=UTC),
            amendment_relation_status="not_amendment",
            base_accession=None,
        )
        for index, (concept, value) in enumerate(
            zip(
                (
                    "Revenues",
                    "NetIncomeLoss",
                    "Assets",
                    "Assets",
                    "StockholdersEquity",
                    "StockholdersEquity",
                ),
                ("1000", "100", "500", "700", "200", "300"),
                strict=True,
            )
        )
    )


def test_dupont_returns_all_factors_without_intermediate_rounding() -> None:
    values = tuple(Decimal(item.value) for item in dupont_operands())
    assert calculate_dupont_components(values, 4) == {
        "net_profit_margin_percent": "10.0000",
        "asset_turnover": "1.6667",
        "equity_multiplier": "2.4000",
        "roe_percent": "40.0000",
        "average_assets": "600.0000",
        "average_equity": "250.0000",
    }
    # Multiplying displayed factors would yield 40.0008, not the authoritative 40.0000.
    assert calculate_dupont_components(values, 0)["roe_percent"] == "40"


def test_report_renders_both_years_only_from_normalized_calculations() -> None:
    scope = dupont_scope()
    observations = []
    for ordinal, year in enumerate((2023, 2024), start=1):
        operands = tuple(
            replace(
                item,
                start_date=date(year - 1, 7, 1) if index < 2 else None,
                end_date=date(year, 6, 30) if index < 2 else None,
                instant=None if index < 2 else date(year - 1 if index % 2 == 0 else year, 6, 30),
            )
            for index, item in enumerate(dupont_operands())
        )
        result = calculate_financial_result(
            scope,
            FinancialCalculation(
                FinancialOperator.DUPONT,
                tuple(
                    FinancialOperand(item.value, item.evidence_ref, item.unit, item.scale)
                    for item in operands
                ),
                4,
            ),
        )
        output = FinanceCalculateOutput.model_validate(
            {
                "status": "ok",
                "financial_scope": dict(scope.to_mapping()),
                "operator": "dupont",
                "operands": [],
                "decimal_places": 4,
                "rounding_mode": "half_even",
                "result": result.value,
                "formula": result.formula,
                "unit": result.unit,
                "scale": result.scale,
                "evidence_refs": [],
                "components": dict(result.components),
                "resolved_operands": [
                    FinancialEvidenceOperandPayload.from_domain(item) for item in operands
                ],
            }
        )
        content = output.model_dump_json()
        observations.append(
            ToolObservationContextSource(
                observation_id=uuid4(),
                tool_call_id=uuid4(),
                workspace_id=uuid4(),
                ordinal=ordinal,
                tool_name="finance.calculate",
                tool_version="v1",
                source_name="finance",
                source_version="v1",
                observed_at=scope.as_of,
                locator={"source": "test"},
                content_sha256=sha256(content.encode()).hexdigest(),
                model_text=content,
            )
        )
    citations = {"T1S1": uuid4(), "T2S1": uuid4()}
    report = render_dupont_report(scope, tuple(observations), citations)
    assert "2023-06-30" in report
    assert "2024-06-30" in report
    assert "40.0000% [T1S1] | 40.0000% [T2S1]" in report
    assert "1.6667" in report
    assert "40.0008" not in report
    assert "尚未完成" in render_dupont_report(scope, tuple(observations), {"T2S1": uuid4()})
    assert "尚未完成" in render_dupont_report(scope, (), {})


def test_dupont_normalized_lineage_recomputes_identically() -> None:
    operands = tuple(
        FinancialOperand(item.value, item.evidence_ref, "USD", 6) for item in dupont_operands()
    )
    scope = replace(dupont_scope(), scale=0)
    result = calculate_financial_result(
        scope, FinancialCalculation(FinancialOperator.DUPONT, operands, 4)
    )
    restored = tuple(
        FinancialOperand(format(item.value_in_scope(scope), "f"), item.evidence_ref)
        for item in operands
    )
    assert (
        calculate_financial_result(
            scope, FinancialCalculation(FinancialOperator.DUPONT, restored, 4)
        )
        == result
    )
    assert result.value == "40.0000"
    assert dict(result.components)["average_assets"] == "600000000.0000"


@pytest.mark.parametrize("index", [0, 2, 3, 4, 5])
@pytest.mark.parametrize("value", ["0", "-1"])
def test_non_positive_denominators_are_not_success(index: int, value: str) -> None:
    values = [Decimal(item.value) for item in dupont_operands()]
    values[index] = Decimal(value)
    with pytest.raises(ValueError, match="DuPont"):
        calculate_dupont_components(tuple(values), 4)


def test_loss_is_reportable_with_positive_equity() -> None:
    values = [Decimal(item.value) for item in dupont_operands()]
    values[1] = Decimal(-100)
    assert calculate_dupont_components(tuple(values), 4)["roe_percent"] == "-40.0000"


def test_annual_mixed_instant_duration_is_only_valid_for_dupont() -> None:
    assert (
        reconcile_financial_operands(
            dupont_scope(), FinancialOperator.DUPONT, dupont_operands()
        ).status
        is FinancialReconciliationStatus.CONSISTENT
    )
    assert (
        reconcile_financial_operands(
            dupont_scope(), FinancialOperator.ADD, dupont_operands()
        ).status
        is not FinancialReconciliationStatus.CONSISTENT
    )


@pytest.mark.parametrize(
    "change",
    [
        {"cik": "0000320193"},
        {"unit": "EUR"},
        {"dimensions": (("axis", "segment"),)},
        {"taxonomy": "custom"},
        {"is_custom": True},
        {"concept": "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"},
        {"source_available_at": datetime(2027, 1, 1, tzinfo=UTC)},
        {"form": FinancialForm.TEN_Q},
        {"report_period": date(2022, 6, 30)},
        {"amendment_relation_status": "unresolved"},
    ],
)
def test_scope_and_consolidation_fail_closed(change: dict[str, Any]) -> None:
    operands = list(dupont_operands())
    operands[5] = replace(operands[5], **change)
    assert (
        reconcile_financial_operands(
            dupont_scope(), FinancialOperator.DUPONT, tuple(operands)
        ).status
        is not FinancialReconciliationStatus.CONSISTENT
    )


def test_wrong_opening_balance_and_quarterly_income_are_rejected() -> None:
    operands = list(dupont_operands())
    operands[2] = replace(operands[2], instant=date(2023, 7, 1))
    assert reconcile_financial_operands(
        dupont_scope(), FinancialOperator.DUPONT, tuple(operands)
    ).issues
    operands = list(dupont_operands())
    operands[:2] = [replace(item, start_date=date(2024, 4, 1)) for item in operands[:2]]
    assert reconcile_financial_operands(
        dupont_scope(), FinancialOperator.DUPONT, tuple(operands)
    ).issues


def test_fifty_three_week_year_is_supported() -> None:
    scope = dupont_scope()
    operands = list(dupont_operands())
    start = scope.report_period - timedelta(days=370)
    operands[:2] = [replace(item, start_date=start) for item in operands[:2]]
    for index in (2, 4):
        operands[index] = replace(operands[index], instant=start - timedelta(days=1))
    assert not reconcile_financial_operands(scope, FinancialOperator.DUPONT, tuple(operands)).issues


def test_filing_window_is_not_an_arbitrary_cross_filing_permission() -> None:
    scope = dupont_scope()
    assert dupont_filing_in_scope(
        scope,
        cik=scope.cik,
        accession="0000950170-23-035122",
        form="10-K",
        report_period=date(2023, 6, 30),
    )
    assert not dupont_filing_in_scope(
        scope,
        cik=scope.cik,
        accession="0000950170-24-999999",
        form="10-K",
        report_period=scope.report_period,
    )
    assert not dupont_filing_in_scope(
        scope,
        cik=scope.cik,
        accession=scope.accession,
        form="10-K",
        report_period=date(2023, 6, 30),
    )
