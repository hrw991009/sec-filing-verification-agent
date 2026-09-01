from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from industry_platform.modules.evaluation.release_recovery import (
    RecoveryAlertStatus,
    RecoveryExecutionStatus,
    RecoveryObservation,
    RecoveryObservationSet,
    _canonical_sha256,
    build_recovery_report,
    load_recovery_manifest,
    load_recovery_observations,
    load_recovery_report,
    main,
)

ROOT = Path(__file__).resolve().parents[5]
MANIFEST_PATH = ROOT / "evals" / "manifests" / "sec-release-recovery-v1.json"
OBSERVATIONS_PATH = ROOT / "evals" / "observations" / "sec-release-recovery-v1.json"
REPORT_PATH = ROOT / "evals" / "reports" / "sec-release-recovery-v1.json"
SCHEMA_PATH = ROOT / "evals" / "schemas" / "release-recovery-v1.schema.json"
STARTED_AT = datetime(2026, 8, 31, 7, 0, tzinfo=UTC)


def _executed_observations(
    root: Path,
    *,
    duplicate_scenario: str | None = None,
) -> RecoveryObservationSet:
    manifest = load_recovery_manifest(MANIFEST_PATH)
    observations: list[RecoveryObservation] = []
    for index, contract in enumerate(manifest.scenarios, start=1):
        relative_path = Path("evidence") / f"{contract.scenario_id}.json"
        artifact = root / relative_path
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(
            json.dumps({"scenario_id": contract.scenario_id}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        observations.append(
            RecoveryObservation(
                scenario_id=contract.scenario_id,
                exercise_id=UUID(int=index),
                run_id=UUID(int=100 + index) if contract.runtime_binding_required else None,
                case_id=UUID(int=200 + index) if contract.runtime_binding_required else None,
                workspace_id=(
                    UUID("11111111-1111-4111-8111-111111111111")
                    if contract.runtime_binding_required
                    else None
                ),
                started_at=STARTED_AT + timedelta(minutes=index),
                completed_at=STARTED_AT + timedelta(minutes=index, seconds=1),
                start_state_sha256=f"{index:064x}",
                final_state_sha256=f"{index + 100:064x}",
                recovery_command_sha256=f"{index + 200:064x}",
                evidence_path=relative_path.as_posix(),
                evidence_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
                recovery_succeeded=True,
                duplicate_side_effect_count=(
                    1 if contract.scenario_id == duplicate_scenario else 0
                ),
                data_loss_count=0,
                unauthorized_write_count=0,
                duration_ms=1_000,
            )
        )
    return RecoveryObservationSet(
        manifest_sha256=_canonical_sha256(manifest),
        execution_status=RecoveryExecutionStatus.EXECUTED,
        source_commit="a" * 40,
        environment="synthetic-unit-fixture",
        observations=tuple(observations),
        limitations=("Synthetic unit fixture; not checked release recovery evidence.",),
    )


def test_checked_recovery_report_recomputes_without_exercise_claims() -> None:
    report = build_recovery_report(
        load_recovery_manifest(MANIFEST_PATH),
        load_recovery_observations(OBSERVATIONS_PATH),
        root=ROOT,
    )

    assert report == load_recovery_report(REPORT_PATH)
    assert report.execution_status is RecoveryExecutionStatus.NOT_EXECUTED
    assert report.observed_scenario_count == 0
    assert report.expected_scenario_count == 12
    assert all(metric.value is None for metric in report.metrics.values())
    assert all(alert.status is RecoveryAlertStatus.UNKNOWN for alert in report.alerts)
    assert report.recovery_gate_passed is False
    assert report.release_ready is False


def test_complete_exercises_compute_recovery_and_safety_metrics(tmp_path: Path) -> None:
    report = build_recovery_report(
        load_recovery_manifest(MANIFEST_PATH),
        _executed_observations(tmp_path),
        root=tmp_path,
    )

    assert report.observed_scenario_count == report.expected_scenario_count == 12
    assert all(metric.value == 1 for metric in report.metrics.values())
    assert all(alert.status is RecoveryAlertStatus.CLEAR for alert in report.alerts)
    assert report.recovery_gate_passed is True
    assert report.release_ready is False
    assert "previous_image_release_artifact_not_verified" not in report.blockers
    assert report.blockers == ("remote_ci_not_verified",)


def test_duplicate_side_effect_fires_recovery_alert(tmp_path: Path) -> None:
    report = build_recovery_report(
        load_recovery_manifest(MANIFEST_PATH),
        _executed_observations(tmp_path, duplicate_scenario="worker-interruption-resume"),
        root=tmp_path,
    )

    metric = report.metrics["zero_duplicate_side_effect_rate"]
    assert metric.value == pytest.approx(11 / 12, abs=1e-6)
    assert metric.gate_passed is False
    alert = next(item for item in report.alerts if item.metric_name == metric.metric_name)
    assert alert.status is RecoveryAlertStatus.FIRING
    assert "zero_duplicate_side_effect_rate_failed" in report.blockers


def test_executed_recovery_requires_every_scenario_and_runtime_binding(tmp_path: Path) -> None:
    observations = _executed_observations(tmp_path)
    incomplete = RecoveryObservationSet.model_validate(
        {**observations.model_dump(), "observations": observations.observations[:-1]}
    )
    with pytest.raises(ValueError, match="cover every frozen scenario"):
        build_recovery_report(
            load_recovery_manifest(MANIFEST_PATH),
            incomplete,
            root=tmp_path,
        )

    selected = observations.observations[2]
    unbound = RecoveryObservationSet.model_validate(
        {
            **observations.model_dump(),
            "observations": (
                *observations.observations[:2],
                selected.model_copy(update={"run_id": None}),
                *observations.observations[3:],
            ),
        }
    )
    with pytest.raises(ValueError, match="requires Run and Workspace binding"):
        build_recovery_report(
            load_recovery_manifest(MANIFEST_PATH),
            unbound,
            root=tmp_path,
        )


def test_recovery_artifact_hash_and_manifest_drift_fail_closed(tmp_path: Path) -> None:
    manifest = load_recovery_manifest(MANIFEST_PATH)
    observations = _executed_observations(tmp_path)
    artifact = tmp_path / observations.observations[0].evidence_path
    artifact.write_text("changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum changed"):
        build_recovery_report(manifest, observations, root=tmp_path)

    checked = load_recovery_observations(OBSERVATIONS_PATH)
    drifted = checked.model_copy(update={"manifest_sha256": "0" * 64})
    with pytest.raises(ValueError, match="do not match the manifest"):
        build_recovery_report(manifest, drifted, root=ROOT)


def test_unexecuted_recovery_cannot_claim_environment_identity() -> None:
    current = load_recovery_observations(OBSERVATIONS_PATH)
    with pytest.raises(ValueError, match="cannot claim exercises or identity"):
        RecoveryObservationSet.model_validate(
            {**current.model_dump(), "environment": "local-compose"}
        )


def test_recovery_cli_writes_checked_report_markdown_and_schema(tmp_path: Path) -> None:
    json_output = tmp_path / "report.json"
    markdown_output = tmp_path / "report.md"
    schema_output = tmp_path / "schema.json"

    assert (
        main(
            [
                "--root",
                str(ROOT),
                "--manifest",
                str(MANIFEST_PATH),
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
    assert markdown_output.read_text(encoding="utf-8").startswith("# SEC release recovery evidence")
    assert json.loads(schema_output.read_text(encoding="utf-8")) == json.loads(
        SCHEMA_PATH.read_text(encoding="utf-8")
    )
