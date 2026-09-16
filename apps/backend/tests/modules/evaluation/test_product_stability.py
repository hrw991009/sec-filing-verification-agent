"""Acceptance judges distinguish valid refusal from outages and incomplete batches."""

import json
from pathlib import Path
from typing import Any

import pytest

from industry_platform.modules.evaluation.product_stability import check_case, official_values


def negative_record() -> dict[str, Any]:
    return {
        "status": "failed",
        "stop_reason": "tool_error",
        "model_calls": 1,
        "input_tokens": 100,
        "output_tokens": 20,
        "max_total_tokens": 1000,
        "step_count": 4,
        "max_steps": 20,
        "cost_micro_usd": 1,
        "max_cost_micro_usd": 100,
        "terminal_at": "2026-09-15T12:00:00+00:00",
        "deadline": "2026-09-15T12:05:00+00:00",
        "terminal_event_count": 1,
        "observations": [],
        "answer": "",
        "tool_requests": ["sec.get_xbrl_facts"],
        "errors": [{"event_type": "agent.tool.failed", "code": "sec_snapshot_not_visible"}],
    }


def test_current_suite_has_ten_product_cases_and_exactly_fifty_executions() -> None:
    document = json.loads(
        (
            Path(__file__).resolve().parents[5] / "evals/scenarios/product-stability-v1.json"
        ).read_text(encoding="utf-8")
    )
    assert len(document["cases"]) == len({case["id"] for case in document["cases"]}) == 10
    assert document["repetitions"] == 5
    assert [case["years"] for case in document["cases"] if case["mode"] == "dupont"] == [3, 4, 5]
    assert sum(case["mode"] == "l2" for case in document["cases"]) == 4
    assert all("strategy_id" not in case for case in document["cases"])


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "outage",
        "future_evidence",
        "no_tool",
        "two_terminals",
        "budget",
        "steps",
        "cost",
        "deadline",
    ],
)
def test_only_the_exact_expected_asof_refusal_passes(mutation: str | None) -> None:
    record = negative_record()
    case = {
        "mode": "unavailable",
        "expected_error": "sec_snapshot_not_visible",
        "answer_patterns": [],
    }
    if mutation == "outage":
        record["errors"][0]["code"] = "provider_unavailable"
    elif mutation == "future_evidence":
        record["observations"] = [{"tool": "sec.get_xbrl_facts", "source_count": 1}]
    elif mutation == "no_tool":
        record["tool_requests"] = []
    elif mutation == "two_terminals":
        record["terminal_event_count"] = 2
    elif mutation == "budget":
        record["output_tokens"] = 1001
    elif mutation == "steps":
        record["step_count"] = 21
    elif mutation == "cost":
        record["cost_micro_usd"] = 101
    elif mutation == "deadline":
        record["terminal_at"] = "2026-09-15T12:06:00+00:00"
    assert bool(check_case(case, record)) == (mutation is not None)


def test_l2_selection_must_match_skill_and_cannot_silently_expand_tools() -> None:
    record = {
        **negative_record(),
        "status": "completed",
        "errors": [],
        "harness_version": "conversation-l2-skills-v1",
        "observations": [{"tool": "skill.read", "skill": "financial-excerpt-explanation"}],
    }
    case = {"mode": "l2", "skill": "financial-excerpt-explanation", "answer_patterns": []}
    assert check_case(case, record) == []
    record["observations"].append({"tool": "industry.web_search"})
    assert "skill_selection_or_tool_surface" in check_case(case, record)


@pytest.mark.parametrize("years", [3, 4, 5])
@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "period",
        "missing",
        "value",
        "source",
        "change",
        "first_change",
        "invalid",
        "unit",
        "calculation",
        "verification",
    ],
)
def test_annual_acceptance_checks_every_value_and_source(years: int, mutation: str | None) -> None:
    selected = list(range(2026 - years, 2026))
    rows: list[dict[str, Any]] = [
        {
            "key": key,
            "unit": "USD",
            "values": [
                {
                    "value": str(official_values(year)[key]),
                    "evidence_refs": ["source"],
                    "change": None
                    if index == 0
                    else str(official_values(year)[key] - official_values(year - 1)[key]),
                }
                for index, year in enumerate(selected)
            ],
        }
        for key in official_values(2025)
    ]
    result: dict[str, Any] = {
        "status": "ready",
        "verification_status": "verified",
        "periods": [f"{year}-06-30" for year in selected],
        "rows": rows,
    }
    record = {
        **negative_record(),
        "status": "completed",
        "errors": [],
        "verification_status": "verified",
        "result_view": result,
        "observations": [{"tool": "sec.get_xbrl_facts"}]
        + [{"tool": "finance.calculate"} for _ in selected],
    }
    if mutation == "period":
        result["periods"] = ["2025-06-30"] * years
    elif mutation == "missing":
        rows.pop()
    elif mutation in {"value", "invalid"}:
        rows[0]["values"][0]["value"] = "999" if mutation == "value" else "not-numeric"
    elif mutation == "source":
        rows[0]["values"][0]["evidence_refs"] = []
    elif mutation in {"change", "first_change"}:
        rows[0]["values"][0 if mutation == "first_change" else 1]["change"] = "999"
    elif mutation == "unit":
        rows[0]["unit"] = ""
    elif mutation == "calculation":
        record["observations"].pop()
    elif mutation == "verification":
        result["verification_status"] = None
    assert bool(check_case({"mode": "dupont", "years": years, "answer_patterns": []}, record)) == (
        mutation is not None
    )


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "no_calculation",
        "wrong_result",
        "no_model",
        "wrong_status",
        "wrong_tool",
        "failed_step",
        "unverified",
        "answer",
    ],
)
def test_margin_acceptance_does_not_trust_a_completed_or_verified_label(
    mutation: str | None,
) -> None:
    record = {
        **negative_record(),
        "status": "completed",
        "errors": [],
        "verification_status": "verified",
        "answer": "36.1460%",
        "observations": [
            {"tool": "sec.get_xbrl_facts"},
            {"tool": "finance.calculate", "result": "36.1460"},
        ],
    }
    if mutation == "no_calculation":
        record["observations"].pop()
    elif mutation == "wrong_result":
        record["observations"][-1]["result"] = "869.1749"
    elif mutation == "no_model":
        record["model_calls"] = 0
    elif mutation == "wrong_status":
        record["status"] = "failed"
    elif mutation == "wrong_tool":
        record["observations"].append({"tool": "skill.read"})
    elif mutation == "failed_step":
        record["errors"] = [{"event_type": "agent.step.failed", "code": "provider_unavailable"}]
    elif mutation == "unverified":
        record["verification_status"] = "partial"
    elif mutation == "answer":
        record["answer"] = "Cannot calculate"
    assert bool(
        check_case(
            {"mode": "research", "metric": "margin", "answer_patterns": [r"36\.146"]}, record
        )
    ) == (mutation is not None)
