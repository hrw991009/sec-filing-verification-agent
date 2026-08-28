"""Machine-check the Day 7 sec-tool-v1 A0/A1/A2 report."""

from pathlib import Path

import pytest

from industry_platform.modules.disclosures.profile import SEC_L4_TOOL_REFERENCES
from industry_platform.modules.disclosures.tool_eval import (
    SecToolCaseKind,
    SecToolStrategy,
    build_sec_tool_report,
    load_sec_tool_dataset,
    load_sec_tool_observations,
    load_sec_tool_report,
    main,
    score_sec_tool_dataset,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
DATASET_PATH = REPOSITORY_ROOT / "evals" / "scenarios" / "sec-tool-v1.json"
OBSERVATIONS_PATH = REPOSITORY_ROOT / "evals" / "observations" / "sec-tool-v1.json"
REPORT_PATH = REPOSITORY_ROOT / "evals" / "reports" / "sec-tool-v1.json"
MARKDOWN_PATH = REPOSITORY_ROOT / "evals" / "reports" / "sec-tool-v1.md"


def test_sec_tool_report_is_recomputed_from_one_manifest_and_30_strategy_runs() -> None:
    dataset = load_sec_tool_dataset(DATASET_PATH)
    observations = load_sec_tool_observations(OBSERVATIONS_PATH)
    checked = load_sec_tool_report(REPORT_PATH)
    recomputed = build_sec_tool_report(DATASET_PATH, dataset, observations)

    assert recomputed == checked
    assert checked.case_count == 10
    assert checked.run_count == 30
    assert checked.deterministic_gate_passed is True
    assert checked.deterministic_blockers == ()
    assert checked.day7_closeout_ready is False
    assert "live_model_not_executed" in checked.closeout_blockers
    assert "main_ci_not_passed" in checked.closeout_blockers


def test_sec_tool_manifest_freezes_shared_data_budget_and_strategy_surfaces() -> None:
    dataset = load_sec_tool_dataset(DATASET_PATH)
    by_strategy = {item.strategy: item for item in dataset.strategies}

    assert dataset.data_version == "sec-tool-contract-data-v1"
    assert dataset.model_fixture_version == "sec-tool-frozen-output-v1"
    assert dataset.shared_budget.max_steps == 8
    assert by_strategy[SecToolStrategy.A0].available_tools == ()
    assert by_strategy[SecToolStrategy.A1].available_tools == (
        "sec.search_filing@v1",
        "sec.read_filing_section@v1",
    )
    assert by_strategy[SecToolStrategy.A2].available_tools == tuple(
        f"{reference.name}@{reference.version}" for reference in SEC_L4_TOOL_REFERENCES
    )
    assert {kind: sum(case.kind is kind for case in dataset.cases) for kind in SecToolCaseKind} == {
        kind: 2 for kind in SecToolCaseKind
    }


def test_sec_tool_a2_has_net_complex_gain_without_simple_regression_or_identity_error() -> None:
    dataset = load_sec_tool_dataset(DATASET_PATH)
    observations = load_sec_tool_observations(OBSERVATIONS_PATH)
    score = score_sec_tool_dataset(dataset, observations.observations)
    a2 = score.strategy_scores[SecToolStrategy.A2]

    assert score.comparison.a2_complex_gain_over_a1 == 0.833333
    assert score.comparison.a2_simple_degradation_from_a1 == 0
    assert a2.metrics["no_answer_abstention"].value == 1
    assert a2.metrics["citation_resolvability"].value == 1
    assert a2.metrics["calculation_lineage"].value == 1
    assert a2.metrics["wrong_company_rate"].value == 0
    assert a2.metrics["wrong_period_rate"].value == 0
    assert a2.metrics["wrong_accession_rate"].value == 0


def test_sec_tool_cases_and_observations_bind_existing_executable_evidence() -> None:
    dataset = load_sec_tool_dataset(DATASET_PATH)
    observations = load_sec_tool_observations(OBSERVATIONS_PATH)
    references = {
        reference for case in dataset.cases for reference in case.executable_evidence_refs
    } | {item.evidence_ref for item in observations.observations}

    for reference in references:
        relative_path, test_name = reference.split("::", maxsplit=1)
        source_path = REPOSITORY_ROOT / relative_path
        assert source_path.is_file(), reference
        assert f"def {test_name}(" in source_path.read_text(encoding="utf-8"), reference


def test_sec_tool_scorer_rejects_missing_runs_and_flags_wrong_accession() -> None:
    dataset = load_sec_tool_dataset(DATASET_PATH)
    observation_set = load_sec_tool_observations(OBSERVATIONS_PATH)

    with pytest.raises(ValueError, match="every case and strategy exactly"):
        score_sec_tool_dataset(dataset, observation_set.observations[:-1])

    first = observation_set.observations[0]
    tampered = first.model_copy(update={"selected_accessions": ("0000320193-23-999999",)})
    score = score_sec_tool_dataset(
        dataset,
        (tampered, *observation_set.observations[1:]),
    )
    assert score.deterministic_gate_passed is False
    assert "a0:wrong_accession_rate" in score.deterministic_blockers


def test_sec_tool_cli_reproduces_checked_in_json_and_markdown(tmp_path: Path) -> None:
    json_output = tmp_path / "report.json"
    markdown_output = tmp_path / "report.md"

    assert (
        main(
            [
                "--dataset",
                str(DATASET_PATH),
                "--observations",
                str(OBSERVATIONS_PATH),
                "--json-output",
                str(json_output),
                "--markdown-output",
                str(markdown_output),
            ]
        )
        == 0
    )
    assert load_sec_tool_report(json_output) == load_sec_tool_report(REPORT_PATH)
    assert markdown_output.read_bytes() == MARKDOWN_PATH.read_bytes()
