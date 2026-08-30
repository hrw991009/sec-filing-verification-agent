from __future__ import annotations

from pathlib import Path

import pytest

from industry_platform.modules.evaluation.agent_security import (
    AgentSecurityAction,
    AgentSecurityKind,
    build_agent_security_dataset,
    build_agent_security_observations,
    build_agent_security_report,
    load_agent_security_dataset,
    load_agent_security_observations,
    load_agent_security_report,
    score_agent_security,
)
from industry_platform.modules.evaluation.agent_security import (
    main as agent_security_main,
)
from industry_platform.modules.evaluation.sec_temporal import load_sec_temporal_manifest

ROOT = Path(__file__).resolve().parents[5]
TEMPORAL_PATH = ROOT / "evals" / "scenarios" / "sec-temporal-v1.json"
DATASET_PATH = ROOT / "evals" / "scenarios" / "agent-security-v1.json"
OBSERVATIONS_PATH = ROOT / "evals" / "observations" / "agent-security-v1.json"
REPORT_PATH = ROOT / "evals" / "reports" / "agent-security-v1.json"


def test_agent_security_report_recomputes() -> None:
    dataset = load_agent_security_dataset(DATASET_PATH)
    observations = load_agent_security_observations(OBSERVATIONS_PATH)
    report = load_agent_security_report(REPORT_PATH)

    assert (
        build_agent_security_report(DATASET_PATH, OBSERVATIONS_PATH, dataset, observations)
        == report
    )
    assert report.case_count == 6
    assert report.trial_count == 18
    assert report.repetitions == 3
    assert report.metrics["trial_success"].value == 1
    assert report.metrics["case_pass_at_k"].value == 1
    assert report.metrics["attack_success_rate"].value == 0
    assert report.step4_closeout_ready is False
    assert "unified_agent_runtime_not_executed" in report.closeout_blockers


def test_generated_agent_security_contract_matches_committed_files() -> None:
    temporal = load_sec_temporal_manifest(TEMPORAL_PATH)
    generated = build_agent_security_dataset(temporal, temporal_path=TEMPORAL_PATH)
    observations = build_agent_security_observations(generated)

    assert generated == load_agent_security_dataset(DATASET_PATH)
    assert observations == load_agent_security_observations(OBSERVATIONS_PATH)
    assert {case.language.value for case in generated.cases} == {"en", "zh"}
    assert {case.kind for case in generated.cases} == set(AgentSecurityKind)
    assert all(case.repetitions == 3 for case in generated.cases)


def test_scorer_derives_injection_and_cross_workspace_failures() -> None:
    dataset = load_agent_security_dataset(DATASET_PATH)
    observation_set = load_agent_security_observations(OBSERVATIONS_PATH)
    trials = list(observation_set.trials)
    injection_index = next(
        index
        for index, trial in enumerate(trials)
        if trial.case_id.endswith("prompt-injection-denial-en")
    )
    original = trials[injection_index]
    external_workspace = next(
        case.external_workspace_id for case in dataset.cases if case.case_id == original.case_id
    )
    trials[injection_index] = original.model_copy(
        update={
            "actions": (
                *original.actions,
                AgentSecurityAction(
                    action="workspace.export",
                    arguments={},
                    workspace_id=external_workspace,
                    side_effect_id="export-1",
                ),
            )
        }
    )

    score = score_agent_security(
        dataset,
        observation_set.model_copy(update={"trials": tuple(trials)}),
    )

    assert score["attack_success_rate"].numerator == 1
    assert score["cross_workspace_rate"].numerator == 1
    assert score["unauthorized_action_rate"].numerator == 1
    assert score["case_pass_at_k"].numerator == 5


def test_scorer_rejects_missing_trials_and_detects_duplicate_effect() -> None:
    dataset = load_agent_security_dataset(DATASET_PATH)
    observation_set = load_agent_security_observations(OBSERVATIONS_PATH)
    with pytest.raises(ValueError, match="cover each frozen trial exactly"):
        score_agent_security(
            dataset,
            observation_set.model_copy(update={"trials": observation_set.trials[:-1]}),
        )

    trials = list(observation_set.trials)
    original = trials[-1]
    first_action = original.actions[0].model_copy(update={"side_effect_id": "effect-1"})
    second_action = original.actions[1].model_copy(update={"side_effect_id": "effect-1"})
    trials[-1] = original.model_copy(
        update={"actions": (first_action, second_action, *original.actions[2:])}
    )
    score = score_agent_security(
        dataset,
        observation_set.model_copy(update={"trials": tuple(trials)}),
    )
    assert score["duplicate_effect_rate"].numerator == 1
    assert score["trial_success"].numerator == 17


def test_agent_security_executable_references_exist() -> None:
    dataset = load_agent_security_dataset(DATASET_PATH)
    observation_set = load_agent_security_observations(OBSERVATIONS_PATH)
    references = {
        reference for case in dataset.cases for reference in case.executable_evidence_refs
    } | {trial.evidence_ref for trial in observation_set.trials}

    for reference in references:
        relative_path, test_name = reference.split("::", maxsplit=1)
        source = ROOT / relative_path
        assert source.is_file(), reference
        assert f"def {test_name}(" in source.read_text(encoding="utf-8"), reference


def test_agent_security_cli_writes_recomputable_outputs(tmp_path: Path) -> None:
    schema_path = tmp_path / "schemas" / "agent-security.json"
    report_path = tmp_path / "reports" / "agent-security.json"
    markdown_path = tmp_path / "reports" / "agent-security.md"

    assert (
        agent_security_main(
            [
                "--dataset",
                str(DATASET_PATH),
                "--observations",
                str(OBSERVATIONS_PATH),
                "--schema-output",
                str(schema_path),
                "--json-output",
                str(report_path),
                "--markdown-output",
                str(markdown_path),
            ]
        )
        == 0
    )

    assert load_agent_security_report(report_path) == load_agent_security_report(REPORT_PATH)
    assert "AgentSecurityDataset" in schema_path.read_text(encoding="utf-8")
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "Empirical pass^k: 1.000000" in markdown
    assert "it is not a model reliability claim" in markdown
