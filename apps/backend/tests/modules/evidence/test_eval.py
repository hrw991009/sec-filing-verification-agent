"""Evidence Scorer stays versioned and matches its deterministic report."""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from industry_platform.modules.agent_harness.scenarios import load_scenario_dataset
from industry_platform.modules.evidence.eval import (
    EVIDENCE_SCORER_VERSION,
    score_evidence_dataset,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
DATASET_PATH = REPOSITORY_ROOT / "evals" / "scenarios" / "day4-evidence-v1.json"
REPORT_PATH = REPOSITORY_ROOT / "evals" / "reports" / "day4-evidence-v1.json"


def test_evidence_dataset_uses_shared_harness_contract_and_matches_report() -> None:
    dataset = load_scenario_dataset(DATASET_PATH)
    report = score_evidence_dataset(dataset)
    checked_in = json.loads(REPORT_PATH.read_text(encoding="utf-8"))

    assert dataset.dataset_id == "day4-evidence"
    assert dataset.dataset_version == "v1"
    assert len(dataset.cases) == 6
    assert report.scorer_version == EVIDENCE_SCORER_VERSION
    assert {
        "schema_version": 1,
        "report_version": "v1",
        **asdict(report),
    } == checked_in
    assert report.metrics["authorization_leakage"].denominator == 2
    assert report.metrics["authorization_leakage"].value == 0


def test_evidence_scorer_rejects_a_missing_fixed_denominator(tmp_path: Path) -> None:
    serialized = (
        DATASET_PATH.read_text(encoding="utf-8")
        .replace(
            '"conflict_expected": 1',
            '"conflict_expected": 0',
        )
        .replace(
            '"conflict_correct": 1',
            '"conflict_correct": 0',
        )
    )
    temporary = tmp_path / "day4-evidence-no-conflict-denominator.json"
    temporary.write_text(serialized, encoding="utf-8")
    with pytest.raises(ValueError, match="conflict_expected has no denominator"):
        score_evidence_dataset(load_scenario_dataset(temporary))
