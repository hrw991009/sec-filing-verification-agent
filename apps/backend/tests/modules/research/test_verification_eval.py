"""Machine-check the frozen Day 8 A2/A3/A4 verification report."""

from datetime import date, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from industry_platform.modules.disclosures.profile import (
    SEC_L4_TOOL_REFERENCES,
    SEC_L5_TOOL_REFERENCES,
)
from industry_platform.modules.research.verification import VerificationStatus
from industry_platform.modules.research.verification_eval import (
    VerificationComplexity,
    VerificationDatabaseFacts,
    VerificationLayer,
    VerificationMetric,
    VerificationScope,
    VerificationStrategy,
    build_verification_report,
    load_verification_dataset,
    load_verification_observations,
    load_verification_report,
    main,
    score_verification_dataset,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
DATASET_PATH = REPOSITORY_ROOT / "evals" / "scenarios" / "sec-verification-v1.json"
OBSERVATIONS_PATH = REPOSITORY_ROOT / "evals" / "observations" / "sec-verification-v1.json"
REPORT_PATH = REPOSITORY_ROOT / "evals" / "reports" / "sec-verification-v1.json"
MARKDOWN_PATH = REPOSITORY_ROOT / "evals" / "reports" / "sec-verification-v1.md"


def test_verification_report_recomputes_14_cases_42_runs_and_three_layers() -> None:
    dataset = load_verification_dataset(DATASET_PATH)
    observation_set = load_verification_observations(OBSERVATIONS_PATH)
    checked = load_verification_report(REPORT_PATH)

    assert build_verification_report(DATASET_PATH, dataset, observation_set) == checked
    assert (checked.case_count, checked.run_count) == (14, 42)
    assert checked.deterministic_gate_passed is True
    assert checked.security_gate_passed is True
    assert checked.fault_gate_passed is True
    assert checked.day8_closeout_ready is False
    assert all(metric.value == 1 for metric in checked.layer_metrics.values())
    assert "dedicated_monitor_browser_not_executed" in checked.closeout_blockers
    assert "main_ci_not_passed" in checked.closeout_blockers


def test_manifest_freezes_a2_a3_a4_surface_budget_and_required_coverage() -> None:
    dataset = load_verification_dataset(DATASET_PATH)
    strategies = {item.strategy: item for item in dataset.strategies}
    tags = {tag for case in dataset.cases for tag in case.coverage_tags}

    assert dataset.shared_budget.max_revisions == 1
    assert strategies[VerificationStrategy.A2].mandatory_verifier is False
    assert strategies[VerificationStrategy.A3].mandatory_verifier is True
    assert strategies[VerificationStrategy.A3].available_tools == tuple(
        f"{item.name}@{item.version}" for item in SEC_L4_TOOL_REFERENCES
    )
    assert strategies[VerificationStrategy.A4].monitor_hitl is True
    assert strategies[VerificationStrategy.A4].available_tools == tuple(
        f"{item.name}@{item.version}" for item in SEC_L5_TOOL_REFERENCES
    )
    assert sum(case.complexity is VerificationComplexity.SIMPLE for case in dataset.cases) == 2
    assert {"wrong_period", "indirect_injection", "worker_hard_stop"} <= tags


def test_a3_has_complex_gain_and_a4_reports_operations_separately() -> None:
    dataset = load_verification_dataset(DATASET_PATH)
    observation_set = load_verification_observations(OBSERVATIONS_PATH)
    score = score_verification_dataset(dataset, observation_set.observations)
    a3 = score.strategy_scores[VerificationStrategy.A3]
    a4 = score.strategy_scores[VerificationStrategy.A4]

    assert score.comparison.a3_complex_gain_over_a2 == 0.714286
    assert score.comparison.a3_simple_degradation_from_a2 == 0
    assert a3.metrics["question_accuracy"].value == 1
    assert a3.metrics["operational_accuracy"].value == 0
    assert a4.metrics["question_accuracy"].value == 1
    assert a4.metrics["operational_accuracy"].value == 1
    assert a4.metrics["recovery_success"].value == 1
    assert a4.metrics["duplicate_effect_rate"].value == 0


def test_scorer_does_not_trust_verified_label_or_duplicate_effect_counts() -> None:
    dataset = load_verification_dataset(DATASET_PATH)
    observation_set = load_verification_observations(OBSERVATIONS_PATH)
    observations = list(observation_set.observations)
    supported = observations[0]
    false_support = supported.runs[VerificationStrategy.A3].model_copy(
        update={"evidence_keys": (), "resolved_citation_keys": ()}
    )
    observations[0] = supported.model_copy(
        update={"runs": {**supported.runs, VerificationStrategy.A3: false_support}}
    )

    score = score_verification_dataset(dataset, observations)
    assert score.security_gate_passed is False
    assert "a3:verified_false_support_rate" in score.security_blockers

    observations = list(observation_set.observations)
    monitor_case = observations[-2]
    a4 = monitor_case.runs[VerificationStrategy.A4]
    duplicated = a4.model_copy(
        update={
            "final_facts": a4.final_facts.model_copy(
                update={"case_rows": 2, "notification_intents": 2}
            )
        }
    )
    observations[-2] = monitor_case.model_copy(
        update={"runs": {**monitor_case.runs, VerificationStrategy.A4: duplicated}}
    )
    score = score_verification_dataset(dataset, observations)
    assert score.fault_gate_passed is False
    assert "a4:duplicate_effect_rate" in score.fault_blockers


def test_scorer_rejects_missing_cases_and_flags_forbidden_action() -> None:
    dataset = load_verification_dataset(DATASET_PATH)
    observation_set = load_verification_observations(OBSERVATIONS_PATH)
    with pytest.raises(ValueError, match="cover every case exactly"):
        score_verification_dataset(dataset, observation_set.observations[:-1])

    observations = list(observation_set.observations)
    injection = observations[8]
    a4 = injection.runs[VerificationStrategy.A4]
    attacked = a4.model_copy(update={"trajectory": (*a4.trajectory, "sec.monitor.subscribe")})
    observations[8] = injection.model_copy(
        update={"runs": {**injection.runs, VerificationStrategy.A4: attacked}}
    )
    score = score_verification_dataset(dataset, observations)
    assert score.security_gate_passed is False
    assert "a4:unauthorized_write_rate" in score.security_blockers


def test_cases_and_observations_reference_existing_executable_tests() -> None:
    dataset = load_verification_dataset(DATASET_PATH)
    observation_set = load_verification_observations(OBSERVATIONS_PATH)
    references = {
        reference for case in dataset.cases for reference in case.executable_evidence_refs
    } | {
        run.evidence_ref
        for observation in observation_set.observations
        for run in observation.runs.values()
    }

    for reference in references:
        relative_path, test_name = reference.split("::", maxsplit=1)
        source = REPOSITORY_ROOT / relative_path
        assert source.is_file(), reference
        assert f"def {test_name}(" in source.read_text(encoding="utf-8"), reference


def test_strict_models_reject_invalid_identity_ratio_and_json(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        VerificationScope(
            workspace_id=UUID("11111111-1111-4111-8111-111111111111"),
            cik="0000320193",
            report_period=date(2023, 9, 30),
            as_of=datetime(2023, 11, 4),  # noqa: DTZ001 - intentionally invalid contract
            accessions=("0000320193-23-000106",),
            unit="USD",
        )
    with pytest.raises(ValidationError, match="accession is invalid"):
        VerificationDatabaseFacts(base_accession="bad")
    with pytest.raises(ValidationError, match="inconsistent"):
        VerificationMetric(numerator=1, denominator=2, value=1)

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        load_verification_dataset(duplicate)
    non_finite = tmp_path / "non-finite.json"
    non_finite.write_text('{"schema_version":NaN}', encoding="utf-8")
    with pytest.raises(ValueError, match="Non-finite JSON number"):
        load_verification_dataset(non_finite)


def test_cli_reproduces_checked_json_and_markdown(tmp_path: Path) -> None:
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
    assert load_verification_report(json_output) == load_verification_report(REPORT_PATH)
    assert markdown_output.read_bytes() == MARKDOWN_PATH.read_bytes()


def test_report_uses_all_three_named_layers() -> None:
    report = load_verification_report(REPORT_PATH)
    assert set(report.layer_metrics) == set(VerificationLayer)
    assert (
        report.strategy_scores[VerificationStrategy.A3].metrics["verified_false_support_rate"].value
        == 0
    )
    assert (
        report.strategy_scores[VerificationStrategy.A4].metrics["unauthorized_write_rate"].value
        == 0
    )
    assert VerificationStatus.VERIFIED.value == "verified"
