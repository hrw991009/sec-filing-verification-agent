"""Two-year source reconciliation and recomputation are publication gates."""

import json
from dataclasses import replace
from datetime import date, timedelta
from uuid import uuid4

import pytest

from industry_platform.modules.evidence.domain import (
    ClaimEvidenceRelation,
    Evidence,
    FinancialCalculationLocatorV1,
    SecXbrlFactLocatorV1,
)
from industry_platform.modules.financial_verification.domain import (
    FinancialCalculation,
    FinancialOperand,
    FinancialOperator,
    calculate_financial_result,
)
from industry_platform.modules.research.verification import (
    VerificationEvidenceState,
    VerificationIssueCode,
    VerificationSnapshot,
    VerificationStatus,
    evaluate_verification_snapshot,
)

from .test_verification import (
    calculation_evidence,
    claim,
    financial_scope,
    snapshot,
    xbrl_evidence,
)


def dupont_snapshot() -> VerificationSnapshot:
    scope = financial_scope()
    calculations: list[Evidence] = []
    sources: list[Evidence] = []
    for start, end in (
        (date(2021, 9, 26), date(2022, 9, 24)),
        (date(2022, 9, 25), date(2023, 9, 30)),
    ):
        facts: list[Evidence] = []
        concepts = (
            "Revenues",
            "NetIncomeLoss",
            "Assets",
            "Assets",
            "StockholdersEquity",
            "StockholdersEquity",
        )
        values = ("1000", "100", "500", "700", "200", "300")
        for index, (concept, value) in enumerate(zip(concepts, values, strict=True)):
            evidence = xbrl_evidence(uuid4(), "a" * 64)
            assert isinstance(evidence.locator, SecXbrlFactLocatorV1)
            facts.append(
                replace(
                    evidence,
                    excerpt=json.dumps({"value": value}),
                    locator=replace(
                        evidence.locator,
                        concept=concept,
                        period_kind="duration" if index < 2 else "instant",
                        start_date=start.isoformat() if index < 2 else None,
                        end_date=end.isoformat() if index < 2 else None,
                        instant=None
                        if index < 2
                        else (start - timedelta(days=1) if index in (2, 4) else end).isoformat(),
                    ),
                )
            )
        result = calculate_financial_result(
            scope,
            FinancialCalculation(
                FinancialOperator.DUPONT,
                tuple(
                    FinancialOperand(value, fact.evidence_id, "USD", 6)
                    for value, fact in zip(values, facts, strict=True)
                ),
                4,
            ),
        )
        evidence = calculation_evidence(uuid4(), facts[0], facts[1])
        assert isinstance(evidence.locator, FinancialCalculationLocatorV1)
        calculations.append(
            replace(
                evidence,
                locator=replace(
                    evidence.locator,
                    operator="dupont",
                    operand_values=values,
                    input_evidence_refs=tuple(item.evidence_id for item in facts),
                    decimal_places=4,
                    result=result.value,
                    formula=result.formula,
                    unit=result.unit,
                    scale=result.scale,
                    components=result.components,
                ),
            )
        )
        sources.extend(facts)
    selected_claim = claim(
        uuid4(), *((item, ClaimEvidenceRelation.SUPPORTS) for item in calculations)
    )
    return replace(
        snapshot(
            (selected_claim,),
            tuple(
                VerificationEvidenceState(evidence=item, available=True)
                for item in (*sources, *calculations)
            ),
        ),
        require_dupont_comparison=True,
    )


def test_complete_dupont_is_verified_from_immutable_source_values() -> None:
    report = evaluate_verification_snapshot(dupont_snapshot())
    assert report.status is VerificationStatus.VERIFIED
    assert report.issues == ()


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_year",
        "unavailable_input",
        "source_value",
        "source_unit",
        "opening_date",
        "component",
        "result",
    ],
)
def test_incomplete_or_tampered_dupont_never_passes(mutation: str) -> None:
    selected = dupont_snapshot()
    states = list(selected.evidence_states)
    if mutation == "missing_year":
        removed = states.pop().evidence.evidence_id
        selected = replace(
            selected,
            claims=(
                replace(
                    selected.claims[0],
                    relations=tuple(
                        link
                        for link in selected.claims[0].relations
                        if link.evidence.evidence_id != removed
                    ),
                ),
            ),
        )
    elif mutation == "unavailable_input":
        states[0] = replace(states[0], available=False)
    elif mutation in {"source_value", "source_unit", "opening_date"}:
        index = 2 if mutation == "opening_date" else 0
        evidence = states[index].evidence
        assert isinstance(evidence.locator, SecXbrlFactLocatorV1)
        changed = (
            replace(evidence, excerpt='{"value":"1001"}')
            if mutation == "source_value"
            else replace(
                evidence,
                locator=replace(evidence.locator, unit="EUR")
                if mutation == "source_unit"
                else replace(evidence.locator, instant="2021-09-24"),
            )
        )
        states[index] = replace(states[index], evidence=changed)
    else:
        evidence = states[-1].evidence
        assert isinstance(evidence.locator, FinancialCalculationLocatorV1)
        changed_locator = (
            replace(evidence.locator, result="99.0000")
            if mutation == "result"
            else replace(
                evidence.locator,
                components=tuple(
                    (key, "99.0000" if key == "roe_percent" else value)
                    for key, value in evidence.locator.components
                ),
            )
        )
        changed = replace(evidence, locator=changed_locator)
        states[-1] = replace(states[-1], evidence=changed)
        selected = replace(
            selected,
            claims=(
                replace(
                    selected.claims[0],
                    relations=tuple(
                        replace(link, evidence=changed)
                        if link.evidence.evidence_id == changed.evidence_id
                        else link
                        for link in selected.claims[0].relations
                    ),
                ),
            ),
        )
    report = evaluate_verification_snapshot(replace(selected, evidence_states=tuple(states)))
    assert report.status is not VerificationStatus.VERIFIED
    assert VerificationIssueCode.COVERAGE_INCOMPLETE in {item.code for item in report.issues}
