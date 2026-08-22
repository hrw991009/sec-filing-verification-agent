"""Research Scorer stays versioned and matches its deterministic report."""

import json
from dataclasses import asdict
from pathlib import Path

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
