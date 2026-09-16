"""Bounded annual, consolidated US-GAAP three-factor DuPont contracts.

Operand order is revenue, parent net income, opening/closing total assets,
opening/closing parent equity. No model-produced number is authoritative.
"""

from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from industry_platform.modules.financial_verification.domain import (
    FinancialEvidenceOperand,
    FinancialForm,
    FinancialPeriodKind,
    FinancialReconciliationIssue,
    FinancialReconciliationIssueCode,
    FinancialReconciliationResult,
    FinancialReconciliationStatus,
    FinancialScope,
)

DUPONT_REVENUE_CONCEPTS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
)
DUPONT_CONCEPTS = (*DUPONT_REVENUE_CONCEPTS, "NetIncomeLoss", "Assets", "StockholdersEquity")
DUPONT_COMPONENT_KEYS = (
    "net_profit_margin_percent",
    "asset_turnover",
    "equity_multiplier",
    "roe_percent",
    "average_assets",
    "average_equity",
)


def dupont_filing_in_scope(
    scope: FinancialScope, *, cik: str, accession: str, form: str, report_period: date
) -> bool:
    """Only the anchor and the explicitly authorized annual comparison range are eligible."""

    lag = (scope.report_period - report_period).days
    return (
        scope.form is FinancialForm.TEN_K
        and cik == scope.cik
        and form == "10-K"
        and (
            (lag == 0 and accession == scope.accession)
            or (
                accession != scope.accession
                and any(
                    350 * offset <= lag <= 380 * offset
                    for offset in range(1, scope.annual_period_count)
                )
            )
        )
    )


def reconcile_dupont_operands(
    scope: FinancialScope, operands: tuple[FinancialEvidenceOperand, ...]
) -> FinancialReconciliationResult:
    if len(operands) != 6:
        raise ValueError("DuPont requires six ordered source operands")
    refs = tuple(item.evidence_ref for item in operands)
    issues: list[FinancialReconciliationIssue] = []

    def reject(code: FinancialReconciliationIssueCode) -> None:
        issue = FinancialReconciliationIssue(code, refs)
        if issue not in issues:
            issues.append(issue)

    for item in operands:
        if not dupont_filing_in_scope(
            scope,
            cik=item.cik,
            accession=item.accession,
            form=item.form.value,
            report_period=item.report_period,
        ):
            reject(FinancialReconciliationIssueCode.SCOPE_MISMATCH)
        if item.source_available_at > scope.as_of:
            reject(FinancialReconciliationIssueCode.FUTURE_SOURCE)
        if item.unit != scope.unit:
            reject(FinancialReconciliationIssueCode.UNIT_MISMATCH)
        if item.dimensions:
            reject(FinancialReconciliationIssueCode.DIMENSIONS_MISMATCH)
        if item.is_custom or item.taxonomy != "us-gaap":
            reject(FinancialReconciliationIssueCode.CONCEPT_MISMATCH)
        if item.amendment_relation_status != "not_amendment":
            reject(FinancialReconciliationIssueCode.AMENDMENT_UNRESOLVED)
    expected = (
        DUPONT_REVENUE_CONCEPTS,
        ("NetIncomeLoss",),
        ("Assets",),
        ("Assets",),
        ("StockholdersEquity",),
        ("StockholdersEquity",),
    )
    if any(item.concept not in concepts for item, concepts in zip(operands, expected, strict=True)):
        reject(FinancialReconciliationIssueCode.CONCEPT_MISMATCH)
    revenue, income, opening_assets, closing_assets, opening_equity, closing_equity = operands
    if (
        revenue.period_kind is not FinancialPeriodKind.DURATION
        or income.period_kind is not FinancialPeriodKind.DURATION
        or revenue.period_key != income.period_key
        or any(item.period_kind is not FinancialPeriodKind.INSTANT for item in operands[2:])
    ):
        reject(FinancialReconciliationIssueCode.PERIOD_KIND_MISMATCH)
    if revenue.start_date is None or revenue.end_date is None:
        reject(FinancialReconciliationIssueCode.PERIOD_NOT_COMPARABLE)
    else:
        duration = (revenue.end_date - revenue.start_date).days + 1
        lag = (scope.report_period - revenue.end_date).days
        if not 350 <= duration <= 380 or not (
            lag == 0
            or any(
                350 * offset <= lag <= 380 * offset
                for offset in range(1, scope.annual_period_count)
            )
        ):
            reject(FinancialReconciliationIssueCode.PERIOD_NOT_COMPARABLE)
        opening = revenue.start_date - timedelta(days=1)
        if (
            opening_assets.instant != opening
            or opening_equity.instant != opening
            or closing_assets.instant != revenue.end_date
            or closing_equity.instant != revenue.end_date
            or any(
                item.period_anchor > item.report_period
                for item in operands
                if item.period_anchor is not None
            )
        ):
            reject(FinancialReconciliationIssueCode.REPORT_PERIOD_MISMATCH)
    return FinancialReconciliationResult(
        FinancialReconciliationStatus.NOT_COMPARABLE
        if issues
        else FinancialReconciliationStatus.CONSISTENT,
        tuple(issues),
        refs,
    )


def calculate_dupont_components(values: tuple[Decimal, ...], places: int) -> dict[str, str]:
    if len(values) != 6 or not 0 <= places <= 12 or any(not v.is_finite() for v in values):
        raise ValueError("DuPont calculation inputs are invalid")
    revenue, income, opening_assets, closing_assets, opening_equity, closing_equity = values
    if revenue <= 0 or min(opening_assets, closing_assets) <= 0:
        raise ValueError("DuPont requires positive revenue and asset balances")
    if min(opening_equity, closing_equity) <= 0:
        raise ValueError("DuPont is not comparable with non-positive parent equity")
    with localcontext() as context:
        context.prec = 50
        assets = (opening_assets + closing_assets) / 2
        equity = (opening_equity + closing_equity) / 2
        raw = (
            income / revenue * 100,
            revenue / assets,
            assets / equity,
            income / equity * 100,
            assets,
            equity,
        )
        quantum = Decimal(1).scaleb(-places)
        return {
            key: format(value.quantize(quantum, rounding=ROUND_HALF_EVEN), "f")
            for key, value in zip(DUPONT_COMPONENT_KEYS, raw, strict=True)
        }
