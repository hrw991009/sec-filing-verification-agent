"""Select two adjacent annual periods from authorized, source-typed XBRL facts."""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from itertools import pairwise

from industry_platform.modules.disclosures.domain import SecXbrlFact, SecXbrlPeriodKind
from industry_platform.modules.disclosures.xbrl_numeric import supported_numeric_format
from industry_platform.modules.financial_verification.domain import FinancialScope
from industry_platform.modules.financial_verification.dupont import DUPONT_REVENUE_CONCEPTS


@dataclass(frozen=True, slots=True)
class DuPontPeriod:
    start_date: date
    end_date: date
    operands: tuple[SecXbrlFact, ...]


@dataclass(frozen=True, slots=True)
class DuPontSelection:
    periods: tuple[DuPontPeriod, ...] = ()
    issues: tuple[str, ...] = ()

    @property
    def facts(self) -> tuple[SecXbrlFact, ...]:
        return tuple(
            {fact.id: fact for period in self.periods for fact in period.operands}.values()
        )


def select_dupont_facts(
    scope: FinancialScope, candidates: tuple[SecXbrlFact, ...]
) -> DuPontSelection:
    """Reject conflicting aliases/restatements instead of choosing a convenient number.

    Aggregate SEC facts are entity-wide but omit the raw context; prefer an
    equivalent raw consolidated fact when available. Segment facts are excluded.
    """

    eligible = tuple(
        fact
        for fact in candidates
        if fact.cik == scope.cik
        and fact.form.value == "10-K"
        and fact.taxonomy == "us-gaap"
        and not fact.is_custom
        and not fact.dimensions
        and supported_numeric_format(fact.format)
        and fact.unit in {scope.unit, f"iso4217:{scope.unit}"}
        and fact.source_available_at <= scope.as_of
    )
    annual = {
        (fact.period.start_date, fact.period.end_date)
        for fact in eligible
        if fact.concept in DUPONT_REVENUE_CONCEPTS
        and fact.period.kind is SecXbrlPeriodKind.DURATION
        and fact.period.start_date is not None
        and fact.period.end_date is not None
        and 350 <= (fact.period.end_date - fact.period.start_date).days + 1 <= 380
        and (
            fact.period.end_date == scope.report_period
            or any(
                350 * offset <= (scope.report_period - fact.period.end_date).days <= 380 * offset
                for offset in range(1, scope.annual_period_count)
            )
        )
    }
    ordered = sorted(annual, key=lambda period: period[1], reverse=True)
    if (
        len(ordered) != scope.annual_period_count
        or ordered[0][1] != scope.report_period
        or any(newer[0] != older[1] + timedelta(days=1) for newer, older in pairwise(ordered))
    ):
        return DuPontSelection(
            issues=(
                "two_consecutive_annual_periods_required"
                if scope.schema_version == 1
                else "requested_consecutive_annual_periods_required",
            )
        )
    periods: list[DuPontPeriod] = []
    issues: list[str] = []
    for start, end in ordered:
        opening = start - timedelta(days=1)
        slots = (
            ("revenue", DUPONT_REVENUE_CONCEPTS, None),
            ("net_income", ("NetIncomeLoss",), None),
            ("opening_assets", ("Assets",), opening),
            ("closing_assets", ("Assets",), end),
            ("opening_equity", ("StockholdersEquity",), opening),
            ("closing_equity", ("StockholdersEquity",), end),
        )
        selected: list[SecXbrlFact] = []
        for name, concepts, instant in slots:
            matches = tuple(
                fact
                for fact in eligible
                if fact.concept in concepts
                and (
                    (
                        instant is not None
                        and fact.period.kind is SecXbrlPeriodKind.INSTANT
                        and fact.period.instant == instant
                    )
                    or (
                        instant is None
                        and fact.period.kind is SecXbrlPeriodKind.DURATION
                        and fact.period.start_date == start
                        and fact.period.end_date == end
                    )
                )
            )
            if not matches:
                issues.append(f"{end}:{name}:missing")
                continue
            try:
                with localcontext() as context:
                    context.prec = 50
                    values = {Decimal(fact.value).scaleb(fact.scale or 0) for fact in matches}
                if any(not value.is_finite() for value in values) or len(values) != 1:
                    issues.append(f"{end}:{name}:conflicting_disclosures")
                    continue
                if name != "net_income" and next(iter(values)) <= 0:
                    issues.append(f"{end}:{name}:non_positive_denominator")
                    continue
            except InvalidOperation:
                issues.append(f"{end}:{name}:non_numeric")
                continue
            # Stable preference; never use ordering to suppress disagreement.
            selected.append(
                min(
                    matches,
                    key=lambda fact: (
                        fact.accession != scope.accession,
                        fact.source_kind.value == "companyfacts_aggregate",
                        fact.concept,
                        str(fact.id),
                    ),
                )
            )
        if len(selected) == 6:
            periods.append(DuPontPeriod(start, end, tuple(selected)))
    # This Skill promises a comparison, not an apparently successful partial year.
    return DuPontSelection(issues=tuple(issues)) if issues else DuPontSelection(tuple(periods))
