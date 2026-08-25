"""Machine-check the SEC fixture scenario/report contract."""

import json
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from industry_platform.modules.agent_harness.scenarios import load_scenario_dataset

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
DATASET_PATH = REPOSITORY_ROOT / "evals" / "scenarios" / "sec-fixture-v1.json"
REPORT_PATH = REPOSITORY_ROOT / "evals" / "reports" / "sec-fixture-v1.json"


def _as_object_dict(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise AssertionError("financial_eval must be a JSON object with string keys")
    return cast(dict[str, object], dict(value))


def _as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AssertionError("financial_eval metric values must be integers")
    return value


def _eval_rows() -> list[dict[str, object]]:
    dataset = load_scenario_dataset(DATASET_PATH)
    return [_as_object_dict(case.expected_behavior["financial_eval"]) for case in dataset.cases]


def _metric(
    rows: list[dict[str, object]],
    expected_key: str,
    correct_key: str,
) -> dict[str, float | int]:
    denominator = sum(_as_int(row[expected_key]) for row in rows)
    numerator = sum(_as_int(row[correct_key]) for row in rows)
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator,
    }


def _average(rows: list[dict[str, object]], key: str) -> dict[str, float | int]:
    numerator = sum(_as_int(row[key]) for row in rows)
    denominator = len(rows)
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator,
    }


def test_sec_fixture_report_is_recomputed_from_the_versioned_scenarios() -> None:
    rows = _eval_rows()
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    expected_metrics = {
        "source_accuracy": _metric(rows, "source_expected", "source_correct"),
        "numeric_accuracy": _metric(rows, "numeric_expected", "numeric_correct"),
        "formula_accuracy": _metric(rows, "formula_expected", "formula_correct"),
        "evidence_support_accuracy": _metric(
            rows,
            "evidence_expected",
            "evidence_correct",
        ),
        "typed_calculation_accuracy": _metric(
            rows,
            "typed_calculation_expected",
            "typed_calculation_correct",
        ),
        "uncertainty_accuracy": _metric(
            rows,
            "uncertainty_expected",
            "uncertainty_correct",
        ),
        "average_steps": _average(rows, "steps"),
        "average_total_tokens": {
            "numerator": sum(
                _as_int(row["input_tokens"]) + _as_int(row["output_tokens"]) for row in rows
            ),
            "denominator": len(rows),
            "value": sum(
                _as_int(row["input_tokens"]) + _as_int(row["output_tokens"]) for row in rows
            )
            / len(rows),
        },
        "average_cost_micro_usd": _average(rows, "cost_micro_usd"),
        "average_latency_ms": _average(rows, "latency_ms"),
    }

    assert report["case_count"] == len(rows) == 5
    assert report["metrics"] == expected_metrics


def test_same_question_comparison_preserves_the_f0_f1_f2_boundary() -> None:
    rows = _eval_rows()
    derived_by_tier = {
        str(row["comparison_tier"]): row for row in rows if row["case_kind"] == "derived"
    }
    comparison = json.loads(REPORT_PATH.read_text(encoding="utf-8"))["same_question_comparison"]

    assert set(derived_by_tier) == {"f0", "f1", "f2"}
    for tier, row in derived_by_tier.items():
        assert comparison[tier] == {
            "steps": row["steps"],
            "total_tokens": _as_int(row["input_tokens"]) + _as_int(row["output_tokens"]),
            "cost_micro_usd": row["cost_micro_usd"],
            "latency_ms": row["latency_ms"],
            "source_count": row["source_count"],
            "evidence_count": row["evidence_count"],
            "typed_calculation_count": row["typed_calculation_count"],
        }

    assert comparison["f0"]["evidence_count"] == 0
    assert comparison["f1"]["typed_calculation_count"] == 0
    assert comparison["f2"]["typed_calculation_count"] == 1


def test_dataset_covers_fact_calculation_and_evidence_insufficiency() -> None:
    kinds: defaultdict[str, int] = defaultdict(int)
    for row in _eval_rows():
        kinds[str(row["case_kind"])] += 1

    assert kinds == {"derived": 3, "fact": 1, "insufficient": 1}
