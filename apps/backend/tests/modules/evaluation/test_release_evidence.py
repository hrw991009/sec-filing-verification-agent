from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID

import pytest

from industry_platform.modules.agent_runtime.domain import AgentRunStatus, RunStopReason
from industry_platform.modules.disclosures.tool_eval import SecToolOutcome, load_sec_tool_dataset
from industry_platform.modules.evaluation.release_evidence import (
    AlertStatus,
    RankedCandidate,
    ReleaseEvidenceLayer,
    ReleaseExecutionStatus,
    ReleaseObservationSet,
    ReleaseRunObservation,
    ReleaseStrategy,
    _canonical_sha256,
    build_release_evidence_report,
    load_release_evidence_manifest,
    load_release_evidence_report,
    load_release_observations,
    main,
)

ROOT = Path(__file__).resolve().parents[5]
MANIFEST_PATH = ROOT / "evals" / "manifests" / "sec-release-evidence-v1.json"
SOURCE_PATH = ROOT / "evals" / "scenarios" / "sec-release-cases-v1.json"
OBSERVATIONS_PATH = ROOT / "evals" / "observations" / "sec-release-evidence-v1.json"
REPORT_PATH = ROOT / "evals" / "reports" / "sec-release-evidence-v1.json"
SCHEMA_PATH = ROOT / "evals" / "schemas" / "release-evidence-v1.schema.json"
SOURCE_SHA256 = hashlib.sha256(SOURCE_PATH.read_bytes()).hexdigest()


def _executed_observations(*, breach: bool = False) -> ReleaseObservationSet:
    manifest = load_release_evidence_manifest(MANIFEST_PATH)
    source = load_sec_tool_dataset(SOURCE_PATH)
    contracts = {item.strategy_id: item for item in manifest.strategies}
    observations: list[ReleaseRunObservation] = []
    identifier = 1
    for case in source.cases:
        for strategy in ReleaseStrategy:
            contract = contracts[strategy]
            answered = case.expected_outcome is SecToolOutcome.ANSWERED
            candidates = tuple(
                RankedCandidate(rank=rank, locator=locator)
                for rank, locator in enumerate(case.expected_evidence_keys, start=1)
            )
            observations.append(
                ReleaseRunObservation(
                    case_id=case.case_id,
                    strategy_id=strategy,
                    repetition=1,
                    run_id=UUID(int=identifier),
                    trace_id=f"release-trace-{identifier}",
                    workspace_id=UUID("11111111-1111-4111-8111-111111111111"),
                    result_workspace_id=(
                        UUID("22222222-2222-4222-8222-222222222222")
                        if breach and identifier == 1
                        else UUID("11111111-1111-4111-8111-111111111111")
                    ),
                    run_status=AgentRunStatus.COMPLETED,
                    stop_reason=RunStopReason.FINAL,
                    runtime_version=contract.runtime_version,
                    harness_version=contract.harness_version,
                    profile_version=contract.profile_version,
                    graph_version=contract.graph_version,
                    prompt_version=contract.prompt_version,
                    toolset_version=contract.toolset_version,
                    verifier_executed=contract.verifier_required,
                    durable_monitor_enabled=contract.durable_monitor_required,
                    observed_outcome=case.expected_outcome,
                    answer_key=case.expected_answer_key,
                    selected_cik=case.expected_cik,
                    selected_report_period=case.expected_report_period.isoformat(),
                    selected_accessions=case.expected_accessions,
                    evidence_keys=case.expected_evidence_keys,
                    program=case.expected_program,
                    ranked_candidates=candidates,
                    evidence_ids=(UUID(int=10_000 + identifier),)
                    if answered and strategy is not ReleaseStrategy.A0
                    else (),
                    calculation_ids=(UUID(int=20_000 + identifier),)
                    if case.expected_program and strategy is not ReleaseStrategy.A0
                    else (),
                    tool_calls=(),
                    citations_resolvable=True,
                    final_state_matches=True,
                    final_state_sha256=f"{identifier:064x}",
                    trace_event_count=4,
                    future_source_count=0,
                    cross_workspace_access_count=1 if breach and identifier == 1 else 0,
                    unauthorized_write_count=0,
                    duplicate_side_effect_count=0,
                    injection_attempted=identifier == 1,
                    injection_succeeded=False,
                    recovery_required=identifier == 2,
                    recovered=identifier == 2,
                    steps=4,
                    total_tokens=400,
                    cost_micro_usd=100,
                    latency_ms=500,
                )
            )
            identifier += 1
    return ReleaseObservationSet(
        manifest_sha256=_canonical_sha256(manifest),
        execution_status=ReleaseExecutionStatus.EXECUTED,
        evidence_layer=ReleaseEvidenceLayer.OFFLINE,
        provider="openai-compatible",
        model="release-model",
        model_version="v1",
        runtime_version="strategy-bound-v1",
        harness_version="strategy-bound-v1",
        prompt_version="strategy-bound-v1",
        toolset_version="strategy-bound-v1",
        observations=tuple(observations),
        limitations=("Synthetic unit fixture; not a checked release capability result.",),
    )


def test_checked_release_evidence_report_recomputes_without_runtime_claims() -> None:
    manifest = load_release_evidence_manifest(MANIFEST_PATH)
    source = load_sec_tool_dataset(SOURCE_PATH)
    observations = load_release_observations(OBSERVATIONS_PATH)

    report = build_release_evidence_report(
        manifest,
        source,
        observations,
        source_manifest_sha256=SOURCE_SHA256,
    )

    assert report == load_release_evidence_report(REPORT_PATH)
    assert report.execution_status is ReleaseExecutionStatus.NOT_EXECUTED
    assert report.expected_run_count == 50
    assert report.observed_run_count == 0
    assert report.global_a0_a4_comparable is False
    assert all(metric.value is None for metric in report.metrics.values())
    assert all(alert.status is AlertStatus.UNKNOWN for alert in report.alerts)
    assert "common_case_runtime_runs_not_executed" in report.blockers


def test_executed_common_cases_compute_recall_binding_security_and_recovery() -> None:
    report = build_release_evidence_report(
        load_release_evidence_manifest(MANIFEST_PATH),
        load_sec_tool_dataset(SOURCE_PATH),
        _executed_observations(),
        source_manifest_sha256=SOURCE_SHA256,
    )

    assert report.observed_run_count == report.expected_run_count == 50
    assert report.global_a0_a4_comparable is True
    assert report.metrics["retrieval_recall_at_5"].value == 1
    assert report.metrics["runtime_binding_completeness"].value == 1
    assert report.metrics["injection_attack_success_rate"].value == 0
    assert report.metrics["recovery_success"].value == 1
    assert report.capability_gate_passed is True
    assert report.observability_gate_passed is True
    assert report.security_gate_passed is True
    assert report.release_ready is False
    assert report.production_default_strategy is None


def test_cross_suite_metrics_do_not_block_the_release_run_evidence_gate() -> None:
    observations = _executed_observations()
    without_cross_suite_trials = ReleaseObservationSet.model_validate(
        {
            **observations.model_dump(),
            "observations": [
                {
                    **item.model_dump(),
                    "injection_attempted": False,
                    "injection_succeeded": False,
                    "recovery_required": False,
                    "recovered": False,
                }
                for item in observations.observations
            ],
        }
    )

    report = build_release_evidence_report(
        load_release_evidence_manifest(MANIFEST_PATH),
        load_sec_tool_dataset(SOURCE_PATH),
        without_cross_suite_trials,
        source_manifest_sha256=SOURCE_SHA256,
    )

    assert report.metrics["injection_attack_success_rate"].value is None
    assert report.metrics["recovery_success"].value is None
    assert report.security_gate_passed is True
    assert "injection_attack_success_rate_not_measured" not in report.blockers
    assert "recovery_success_not_measured" not in report.blockers


def test_cross_workspace_result_fires_a_critical_security_alert() -> None:
    report = build_release_evidence_report(
        load_release_evidence_manifest(MANIFEST_PATH),
        load_sec_tool_dataset(SOURCE_PATH),
        _executed_observations(breach=True),
        source_manifest_sha256=SOURCE_SHA256,
    )

    metric = report.metrics["cross_workspace_rate"]
    assert metric.value == 0.02
    assert metric.gate_passed is False
    alert = next(item for item in report.alerts if item.metric_name == metric.metric_name)
    assert alert.status is AlertStatus.FIRING
    assert alert.severity == "critical"


def test_executed_run_set_requires_every_case_strategy_and_repetition() -> None:
    observations = _executed_observations()
    incomplete = ReleaseObservationSet.model_validate(
        {**observations.model_dump(), "observations": observations.observations[:-1]}
    )

    with pytest.raises(ValueError, match="cover every common case and strategy"):
        build_release_evidence_report(
            load_release_evidence_manifest(MANIFEST_PATH),
            load_sec_tool_dataset(SOURCE_PATH),
            incomplete,
            source_manifest_sha256=SOURCE_SHA256,
        )


def test_unexecuted_input_rejects_runtime_identity_and_observations() -> None:
    current = load_release_observations(OBSERVATIONS_PATH)
    with pytest.raises(ValueError, match="cannot claim Runs or runtime identity"):
        ReleaseObservationSet.model_validate(
            {
                **current.model_dump(),
                "provider": "provider",
            }
        )


def test_manifest_hash_and_common_case_drift_fail_closed() -> None:
    manifest = load_release_evidence_manifest(MANIFEST_PATH)
    source = load_sec_tool_dataset(SOURCE_PATH)
    observations = load_release_observations(OBSERVATIONS_PATH)
    with pytest.raises(ValueError, match="source manifest checksum changed"):
        build_release_evidence_report(
            manifest,
            source,
            observations,
            source_manifest_sha256="0" * 64,
        )

    bad_observations = ReleaseObservationSet.model_validate(
        {**observations.model_dump(), "manifest_sha256": "0" * 64}
    )
    with pytest.raises(ValueError, match="do not match the evidence manifest"):
        build_release_evidence_report(
            manifest,
            source,
            bad_observations,
            source_manifest_sha256=SOURCE_SHA256,
        )

    changed = manifest.model_copy(
        update={"common_case_ids": tuple(reversed(manifest.common_case_ids))}
    )
    with pytest.raises(ValueError, match="common cases changed"):
        build_release_evidence_report(
            changed,
            source,
            observations,
            source_manifest_sha256=SOURCE_SHA256,
        )


def test_release_evidence_cli_writes_checked_report_markdown_and_schema(
    tmp_path: Path,
) -> None:
    json_output = tmp_path / "report.json"
    markdown_output = tmp_path / "report.md"
    schema_output = tmp_path / "schema.json"

    assert (
        main(
            [
                "--manifest",
                str(MANIFEST_PATH),
                "--source-manifest",
                str(SOURCE_PATH),
                "--observations",
                str(OBSERVATIONS_PATH),
                "--json-output",
                str(json_output),
                "--markdown-output",
                str(markdown_output),
                "--schema-output",
                str(schema_output),
            ]
        )
        == 0
    )

    assert json.loads(json_output.read_text(encoding="utf-8")) == json.loads(
        REPORT_PATH.read_text(encoding="utf-8")
    )
    assert "Runs: 0/50" in markdown_output.read_text(encoding="utf-8")
    assert json.loads(schema_output.read_text(encoding="utf-8")) == json.loads(
        SCHEMA_PATH.read_text(encoding="utf-8")
    )
