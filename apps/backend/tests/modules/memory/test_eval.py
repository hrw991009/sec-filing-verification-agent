"""Memory Scorer stays versioned and matches its checked-in deterministic report."""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from industry_platform.modules.agent_harness.scenarios import load_scenario_dataset
from industry_platform.modules.memory.eval import MEMORY_SCORER_VERSION, score_memory_dataset

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
DATASET_PATH = REPOSITORY_ROOT / "evals" / "scenarios" / "day4-memory-v1.json"
REPORT_PATH = REPOSITORY_ROOT / "evals" / "reports" / "day4-memory-v1.json"


def test_memory_dataset_uses_shared_harness_contract_and_matches_report() -> None:
    dataset = load_scenario_dataset(DATASET_PATH)
    report = score_memory_dataset(dataset)
    checked_in = json.loads(REPORT_PATH.read_text(encoding="utf-8"))

    assert dataset.dataset_id == "day4-memory"
    assert dataset.dataset_version == "v1"
    assert len(dataset.cases) == 8
    assert report.scorer_version == MEMORY_SCORER_VERSION
    assert {
        "schema_version": 1,
        "report_version": "v1",
        **asdict(report),
    } == checked_in
    assert report.metrics["deletion_residual"].denominator == 1
    assert report.metrics["deletion_residual"].value == 0


def test_memory_scorer_rejects_a_missing_fixed_denominator(tmp_path: Path) -> None:
    # The shared loader freezes inputs, so the checked-in dataset cannot be
    # mutated in place. Denominator validation is covered through this private
    # contract by replacing every deletion expectation in serialized JSON.
    serialized = DATASET_PATH.read_text(encoding="utf-8").replace(
        '"deletion_expected": 1',
        '"deletion_expected": 0',
    )
    temporary = tmp_path / "day4-memory-no-deletion-denominator.json"
    temporary.write_text(serialized, encoding="utf-8")
    with pytest.raises(ValueError, match="deletion_expected has no denominator"):
        score_memory_dataset(load_scenario_dataset(temporary))
