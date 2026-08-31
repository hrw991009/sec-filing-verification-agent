from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from industry_platform.modules.evaluation.release_suite import (
    CapabilityMetric,
    Decision,
    LiveTarget,
    MetricStatus,
    OfflineDatasetResult,
    PairwiseAblationDecision,
    ReleaseSuiteSources,
    build_release_suite,
    load_deterministic_release_report,
    load_failure_taxonomy_report,
    load_live_release_report,
    load_offline_release_report,
)
from industry_platform.modules.evaluation.release_suite import (
    main as release_suite_main,
)

ROOT = Path(__file__).resolve().parents[5]
REPORTS = ROOT / "evals" / "reports"
SCHEMA_PATH = ROOT / "evals" / "schemas" / "release-suite-v1.schema.json"


def _sources(**updates: Path) -> ReleaseSuiteSources:
    values = {
        "registry": ROOT / "evals" / "registry" / "sec-agent-datasets-v1.json",
        "release_manifest": ROOT / "evals" / "manifests" / "sec-agent-release-v1.json",
        "sec_tool": REPORTS / "sec-tool-v1.json",
        "verification": REPORTS / "sec-verification-v1.json",
        "temporal": REPORTS / "sec-temporal-v1.json",
        "agent_security": REPORTS / "agent-security-v1.json",
        "release_evidence": REPORTS / "sec-release-evidence-v1.json",
        "finqa": REPORTS / "finqa-adapter-v1.json",
        "tatqa": REPORTS / "tatqa-adapter-v1.json",
        "financebench": REPORTS / "financebench-adapter-v1.json",
        "finsearch_historical": REPORTS / "finsearchcomp-historical-v1.json",
        "finsearch_live": REPORTS / "finsearchcomp-live-v1.json",
    }
    values.update(updates)
    return ReleaseSuiteSources(**values)


def test_release_suite_recomputes_checked_reports() -> None:
    bundle = build_release_suite(_sources())

    assert bundle.deterministic == load_deterministic_release_report(
        REPORTS / "sec-release-deterministic-v1.json"
    )
    assert bundle.offline == load_offline_release_report(REPORTS / "sec-release-offline-v1.json")
    assert bundle.live == load_live_release_report(REPORTS / "sec-release-live-v1.json")
    assert bundle.failure_taxonomy == load_failure_taxonomy_report(
        REPORTS / "sec-release-failure-taxonomy-v1.json"
    )
    assert bundle.deterministic.deterministic_contract_gate_passed is True
    assert bundle.deterministic.global_a0_a4_comparable is False
    assert bundle.deterministic.global_a0_a4_score is None
    assert bundle.deterministic.production_default_strategy is None
    assert bundle.offline.executed is False
    assert bundle.live.executed is False


def test_release_suite_binds_source_report_hashes() -> None:
    bundle = build_release_suite(_sources())
    paths = {
        "sec-agent-release-v1": ROOT / "evals" / "manifests" / "sec-agent-release-v1.json",
        "sec-tool-v1": REPORTS / "sec-tool-v1.json",
        "sec-verification-v1": REPORTS / "sec-verification-v1.json",
        "sec-temporal-v1": REPORTS / "sec-temporal-v1.json",
        "agent-security-v1": REPORTS / "agent-security-v1.json",
        "sec-release-evidence-v1": REPORTS / "sec-release-evidence-v1.json",
    }

    for reference in bundle.deterministic.source_reports:
        assert (
            reference.sha256 == hashlib.sha256(paths[reference.report_id].read_bytes()).hexdigest()
        )


def test_release_suite_rejects_source_identity_drift(tmp_path: Path) -> None:
    value = json.loads((REPORTS / "finqa-adapter-v1.json").read_text(encoding="utf-8"))
    value["dataset_id"] = "not-finqa"
    changed = tmp_path / "finqa.json"
    changed.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ValueError, match="source identity changed"):
        build_release_suite(_sources(finqa=changed))


def test_release_suite_rejects_cross_manifest_retention_and_unmeasured_scores() -> None:
    with pytest.raises(ValueError, match="passing comparable ablation"):
        PairwiseAblationDecision(
            segment_id="invalid",
            source_report_id="source",
            baseline_strategy="a1",
            candidate_strategy="a2",
            common_case_count=10,
            same_manifest=False,
            same_data_scope_budget=True,
            primary_metric="accuracy",
            primary_gain=0.5,
            simple_degradation=0,
            cost_increase_micro_usd=1,
            latency_increase_ms=1,
            source_gate_passed=True,
            decision=Decision.RETAIN_FOR_NEXT_LAYER,
            blockers=(),
        )
    with pytest.raises(ValueError, match="cannot claim a value"):
        CapabilityMetric(
            metric_name="retrieval_recall_at_5",
            status=MetricStatus.NOT_MEASURED,
            value=1,
            unit="ratio",
            limitation="missing ranked candidates",
        )


def test_unexecuted_layers_reject_runtime_or_score_claims() -> None:
    with pytest.raises(ValueError, match="cannot claim model predictions"):
        OfflineDatasetResult(
            dataset_id="finqa",
            eligible_case_count=10,
            prediction_count=10,
            model_executed=True,
            blockers=("still-blocked",),
        )
    with pytest.raises(ValueError, match="cannot claim runtime identity"):
        LiveTarget(
            target_id="live",
            case_count=10,
            required_repetitions=3,
            completed_repetitions=0,
            provider="provider",
            blockers=("not-executed",),
        )


def test_failure_taxonomy_reconciles_without_inventing_runtime_failures() -> None:
    report = load_failure_taxonomy_report(REPORTS / "sec-release-failure-taxonomy-v1.json")

    assert report.release_blocking_count == len(report.items) == 9
    assert report.observed_runtime_failure_count == 0
    assert sum(report.category_counts.values()) == len(report.items)
    assert all(item.release_blocking for item in report.items)


def test_release_suite_cli_writes_all_layers(tmp_path: Path) -> None:
    output = tmp_path / "reports"
    schema = tmp_path / "schemas" / "release-suite.json"
    sources = _sources()

    assert (
        release_suite_main(
            [
                "--registry",
                str(sources.registry),
                "--release-manifest",
                str(sources.release_manifest),
                "--sec-tool-report",
                str(sources.sec_tool),
                "--verification-report",
                str(sources.verification),
                "--temporal-report",
                str(sources.temporal),
                "--agent-security-report",
                str(sources.agent_security),
                "--release-evidence-report",
                str(sources.release_evidence),
                "--finqa-report",
                str(sources.finqa),
                "--tatqa-report",
                str(sources.tatqa),
                "--financebench-report",
                str(sources.financebench),
                "--finsearch-historical-report",
                str(sources.finsearch_historical),
                "--finsearch-live-report",
                str(sources.finsearch_live),
                "--report-directory",
                str(output),
                "--schema-output",
                str(schema),
            ]
        )
        == 0
    )

    for name in (
        "sec-release-deterministic-v1",
        "sec-release-offline-v1",
        "sec-release-live-v1",
        "sec-release-failure-taxonomy-v1",
    ):
        assert (output / f"{name}.json").is_file()
        assert (output / f"{name}.md").is_file()
    generated_schema = json.loads(schema.read_text(encoding="utf-8"))
    assert generated_schema["title"] == "ReleaseSuiteBundle"
    assert generated_schema == json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
