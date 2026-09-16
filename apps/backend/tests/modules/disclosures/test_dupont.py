"""Multi-year source selection rejects incomplete, ambiguous and incompatible inputs."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from industry_platform.modules.agent_runtime.context import ContextDecisionReason
from industry_platform.modules.agent_runtime.context_compiler import (
    FinancialContextCompilerV1,
    Utf8UpperBoundTokenCounter,
)
from industry_platform.modules.agent_runtime.tool_runtime import ToolL2Runtime
from industry_platform.modules.disclosures.domain import (
    SecXbrlFact,
    SecXbrlPeriod,
    SecXbrlPeriodKind,
)
from industry_platform.modules.disclosures.dupont import select_dupont_facts
from industry_platform.modules.disclosures.tool import SecGetXbrlFactsOutput, SecGetXbrlFactsTool
from industry_platform.modules.financial_verification.domain import FinancialForm, FinancialScope
from industry_platform.modules.tools.domain import ToolAction
from industry_platform.modules.tools.registry import RegistryToolExecutor, ToolRegistry

from .test_filing_content_tool import context, prepare
from .test_xbrl_service_tool import aggregate_fact


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


def candidates(years: int = 2) -> tuple[SecXbrlFact, ...]:
    scope = dupont_scope()
    facts = []
    for year in range(2024, 2024 - years, -1):
        for concept, value in (("Revenues", "1000"), ("NetIncomeLoss", "100")):
            facts.append(
                replace(
                    aggregate_fact(),
                    id=uuid4(),
                    cik=scope.cik,
                    accession=scope.accession,
                    concept=concept,
                    value=value,
                    filed_date=date(2024, 8, 1),
                    period=SecXbrlPeriod(
                        SecXbrlPeriodKind.DURATION,
                        start_date=date(year - 1, 7, 1),
                        end_date=date(year, 6, 30),
                    ),
                )
            )
    for year in range(2024 - years, 2025):
        for concept, value in (("Assets", "600"), ("StockholdersEquity", "250")):
            facts.append(
                replace(
                    aggregate_fact(),
                    id=uuid4(),
                    cik=scope.cik,
                    accession=scope.accession if year > 2022 else "0000950170-23-035122",
                    concept=concept,
                    value=value,
                    filed_date=date(2024, 8, 1),
                    period=SecXbrlPeriod(SecXbrlPeriodKind.INSTANT, instant=date(year, 6, 30)),
                )
            )
    return tuple(facts)


def test_two_years_share_three_balance_dates() -> None:
    result = select_dupont_facts(dupont_scope(), candidates())
    assert not result.issues
    assert len(result.periods) == 2
    assert len(result.facts) == 10
    assert [p.end_date for p in result.periods] == [date(2024, 6, 30), date(2023, 6, 30)]


@pytest.mark.asyncio
@pytest.mark.parametrize("years", [2, 3, 4, 5])
async def test_dupont_tool_and_context_keep_requested_years_without_relaxing_ordinary_scope(
    years: int,
) -> None:
    scope = (
        dupont_scope()
        if years == 2
        else replace(dupont_scope(), schema_version=2, analysis_years=years)
    )
    selected = select_dupont_facts(scope, candidates(years))
    service = AsyncMock()
    service.get_dupont_facts.return_value = selected
    registry = ToolRegistry((SecGetXbrlFactsTool(service),))
    call = prepare(registry, ToolAction(1, "sec.get_xbrl_facts", "v1", {"purpose": "dupont"}))
    assert dict(call.arguments) == {"purpose": "dupont"}
    result = await RegistryToolExecutor(registry, clock=lambda: scope.as_of).execute(
        call,
        replace(context(), financial_scope=scope),
    )
    output = SecGetXbrlFactsOutput.model_validate_json(result.observation.model_text)
    assert len(output.dupont_periods) == years
    assert len(output.facts) == 4 * years + 2
    assert len(result.observation.sources) == 4 * years + 2
    context_source = ToolL2Runtime._context_observation(result.observation)
    compiler = FinancialContextCompilerV1(token_counter=Utf8UpperBoundTokenCounter())
    projection = compiler._tool_observation_payload(context_source)
    assert projection["content_projection"] == "dupont-model-context-v2"
    assert projection["content_sha256"] == result.observation.content_sha256
    assert isinstance(projection["content"], str)
    compact = json.loads(projection["content"])
    assert compact["dupont_periods"] == output.model_dump(mode="json")["dupont_periods"]
    assert len(projection["content"]) < len(result.observation.model_text) // 2
    assert context_source.model_text == result.observation.model_text
    document = json.loads(result.observation.model_text)
    assert (
        FinancialContextCompilerV1._xbrl_fact_decision(document, scope)
        is ContextDecisionReason.INCLUDED
    )
    document["purpose"] = "facts"
    assert (
        FinancialContextCompilerV1._xbrl_fact_decision(document, scope)
        is ContextDecisionReason.EXCLUDED_FINANCIAL_SCOPE_MISMATCH
    )
    assert [f.concept for f in selected.periods[0].operands] == [
        "Revenues",
        "NetIncomeLoss",
        "Assets",
        "Assets",
        "StockholdersEquity",
        "StockholdersEquity",
    ]


@pytest.mark.parametrize("index", range(10))
def test_every_missing_input_blocks_complete_comparison(index: int) -> None:
    facts = candidates()
    result = select_dupont_facts(dupont_scope(), facts[:index] + facts[index + 1 :])
    assert result.issues
    assert not result.periods
    assert not result.facts


def test_equal_duplicates_are_deduplicated_but_restatements_are_not_hidden() -> None:
    facts = candidates()
    duplicate = replace(facts[0], id=uuid4(), accession="0000950170-23-035122")
    assert not select_dupont_facts(dupont_scope(), (*facts, duplicate)).issues
    result = select_dupont_facts(dupont_scope(), (*facts, replace(duplicate, value="999")))
    assert any("conflicting_disclosures" in issue for issue in result.issues)


def test_ambiguous_revenue_aliases_are_not_added_or_arbitrarily_chosen() -> None:
    facts = candidates()
    alternate = replace(facts[0], id=uuid4(), concept="SalesRevenueNet", value="500")
    result = select_dupont_facts(dupont_scope(), (*facts, alternate))
    assert any("revenue:conflicting_disclosures" in issue for issue in result.issues)


def test_non_positive_equity_does_not_report_ready() -> None:
    facts = list(candidates())
    facts[-1] = replace(facts[-1], value="-1")
    result = select_dupont_facts(dupont_scope(), tuple(facts))
    assert any("non_positive_denominator" in issue for issue in result.issues)


def test_non_numeric_fact_does_not_escape_as_decimal_exception() -> None:
    facts = list(candidates())
    facts[-1] = replace(facts[-1], value="not-a-number")
    assert select_dupont_facts(dupont_scope(), tuple(facts)).issues


def test_other_currency_and_custom_taxonomy_are_not_substituted() -> None:
    facts = list(candidates())
    facts[-1] = replace(facts[-1], unit="EUR")
    assert select_dupont_facts(dupont_scope(), tuple(facts)).issues
    facts[-1] = replace(facts[-1], unit="USD", taxonomy="custom", is_custom=True)
    assert select_dupont_facts(dupont_scope(), tuple(facts)).issues
