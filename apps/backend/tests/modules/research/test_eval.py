"""Research Scorer stays versioned and matches its deterministic report."""

import json
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import cast

import pytest

from industry_platform.modules.agent_harness.scenarios import load_scenario_dataset
from industry_platform.modules.research.eval import (
    RESEARCH_SCORER_VERSION,
    score_research_dataset,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
DATASET_PATH = REPOSITORY_ROOT / "evals" / "scenarios" / "day4-research-v1.json"
REPORT_PATH = REPOSITORY_ROOT / "evals" / "reports" / "day4-research-v1.json"
AGGREGATE_REPORT_PATH = REPOSITORY_ROOT / "evals" / "reports" / "day4-v1.json"
L4_DATASET_PATH = REPOSITORY_ROOT / "evals" / "scenarios" / "sec-fixture-l4-v1.json"
L4_REPORT_PATH = REPOSITORY_ROOT / "evals" / "reports" / "sec-fixture-l4-v1.json"


def test_research_dataset_uses_shared_harness_contract_and_matches_report() -> None:
    dataset = load_scenario_dataset(DATASET_PATH)
    report = score_research_dataset(dataset)
    checked_in = json.loads(REPORT_PATH.read_text(encoding="utf-8"))

    assert dataset.dataset_id == "day4-research"
    assert dataset.dataset_version == "v1"
    assert len(dataset.cases) == 10
    assert report.scorer_version == RESEARCH_SCORER_VERSION
    assert {
        "schema_version": 1,
        "report_version": "v1",
        **asdict(report),
    } == checked_in
    assert set(report.same_question_comparison) == {"l0", "l2", "l3"}
    assert report.same_question_comparison["l3"].evidence_count > (
        report.same_question_comparison["l2"].evidence_count
    )
    assert report.metrics["authorization_leakage"].value == 0

    aggregate = json.loads(AGGREGATE_REPORT_PATH.read_text(encoding="utf-8"))
    assert aggregate["scenario_inventory"] == {
        "retained_day2_day3_case_count": 24,
        "day4_case_count": 26,
        "cumulative_case_count": 50,
    }
    assert sum(item["case_count"] for item in aggregate["day4_datasets"]) == 26


def test_research_scorer_rejects_a_missing_fixed_denominator(tmp_path: Path) -> None:
    serialized = (
        DATASET_PATH.read_text(encoding="utf-8")
        .replace(
            '"cancellation_expected": 1',
            '"cancellation_expected": 0',
        )
        .replace(
            '"cancellation_correct": 1',
            '"cancellation_correct": 0',
        )
    )
    temporary = tmp_path / "day4-research-no-cancellation-denominator.json"
    temporary.write_text(serialized, encoding="utf-8")
    with pytest.raises(ValueError, match="cancellation_expected has no denominator"):
        score_research_dataset(load_scenario_dataset(temporary))


def test_research_scorer_requires_exactly_one_case_per_comparison_tier(
    tmp_path: Path,
) -> None:
    serialized = DATASET_PATH.read_text(encoding="utf-8").replace(
        '"comparison_tier": "l0"',
        '"comparison_tier": "none"',
        1,
    )
    temporary = tmp_path / "day4-research-missing-l0.json"
    temporary.write_text(serialized, encoding="utf-8")
    with pytest.raises(ValueError, match="requires one l0 case"):
        score_research_dataset(load_scenario_dataset(temporary))


def test_l4_recovery_report_matches_scenarios_and_executable_evidence() -> None:
    dataset = load_scenario_dataset(L4_DATASET_PATH)
    report = cast(
        dict[str, object],
        json.loads(L4_REPORT_PATH.read_text(encoding="utf-8")),
    )
    evaluations = [
        cast(Mapping[str, object], item.expected_behavior["recovery_eval"])
        for item in dataset.cases
    ]

    assert dataset.dataset_id == "sec-fixture-research-l4-recovery"
    assert dataset.dataset_version == "sec-fixture-l4-v1"
    assert report["case_count"] == len(dataset.cases) == 7
    assert report["coverage"] == [item["gate"] for item in evaluations]

    metrics = cast(Mapping[str, Mapping[str, int | float]], report["metrics"])
    for metric_name, expected_field, correct_field in (
        (
            "checkpoint_recovery_accuracy",
            "checkpoint_recovery_expected",
            "checkpoint_recovery_correct",
        ),
        ("decision_accuracy", "decision_expected", "decision_correct"),
        ("resume_gate_accuracy", "resume_expected", "resume_correct"),
    ):
        denominator = sum(cast(int, item[expected_field]) for item in evaluations)
        numerator = sum(cast(int, item[correct_field]) for item in evaluations)
        assert metrics[metric_name] == {
            "numerator": numerator,
            "denominator": denominator,
            "value": numerator / denominator,
        }

    zero_duplicate_count = sum(
        cast(int, item["duplicate_side_effects"]) == 0 for item in evaluations
    )
    assert metrics["zero_duplicate_side_effect_rate"] == {
        "numerator": zero_duplicate_count,
        "denominator": len(evaluations),
        "value": zero_duplicate_count / len(evaluations),
    }

    evidence = cast(Mapping[str, str], report["executable_evidence"])
    for reference in evidence.values():
        relative_path = reference.split("::", maxsplit=1)[0]
        evidence_path = (REPOSITORY_ROOT / relative_path).resolve()
        assert evidence_path.is_relative_to(REPOSITORY_ROOT)
        assert evidence_path.is_file()

    assert report["execution_boundary"] == {
        "fixture_only": True,
        "same_run_resume": True,
        "live_sec": False,
        "production_model_quality": False,
        "cross_refresh_worker_restart": False,
    }
