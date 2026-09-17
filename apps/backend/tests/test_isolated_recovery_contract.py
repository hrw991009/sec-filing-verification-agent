"""Isolation and fail-closed evidence contracts for the opt-in container exercise."""

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture
def exercise_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    path = Path(__file__).resolve().parents[3] / "infra/recovery/exercise.py"
    spec = importlib.util.spec_from_file_location("isolated_recovery_exercise", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    return module


def test_rejects_unscoped_evidence_and_project_names(
    exercise_module: ModuleType, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="inside"):
        exercise_module.Exercise(tmp_path / "outside")
    with pytest.raises(ValueError, match="identity"):
        exercise_module.Exercise(tmp_path / ".data/isolated-recovery/development")


def test_index_identity_hash_ignores_sql_row_order_but_detects_changed_identity(
    exercise_module: ModuleType,
) -> None:
    records = [{"id": "b", "chunk_id": "second"}, {"id": "a", "chunk_id": "first"}]
    digest = exercise_module.index_identity_digest(records)
    assert exercise_module.index_identity_digest(list(reversed(records))) == digest
    assert exercise_module.index_identity_digest([*records, {"id": "c"}]) != digest
    assert (
        exercise_module.index_identity_digest([{"id": "b", "chunk_id": "changed"}, records[1]])
        != digest
    )


def test_latest_summary_requires_all_checks_and_retains_failed_attempts(
    exercise_module: ModuleType,
) -> None:
    names = exercise_module.REQUIRED_CHECKS
    scenarios: list[dict[str, object]] = [{"name": names[0], "passed": False}]
    report = {
        "source_commit": "test",
        "source_patch_sha256": "test",
        "application_image_id": "test",
        "scenarios": scenarios,
    }
    assert exercise_module.verification_summary(report)["checks_passed"] is False
    scenarios.extend({"name": name, "passed": True} for name in names)
    summary = exercise_module.verification_summary(report)
    assert summary["checks_passed"] is True
    assert summary["single_failure_free_batch"] is False
    assert len(summary["historical_failed_attempts"]) == 1
    assert summary["release_accepted"] is False
    scenarios.append({"name": names[-1], "passed": False})
    assert exercise_module.verification_summary(report)["checks_passed"] is False


def test_refuses_to_mutate_container_from_another_project(
    exercise_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exercise = exercise_module.Exercise(tmp_path / ".data/isolated-recovery/iip-recovery-a1")
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(exercise, "compose", lambda *args: b"container-id")

    def command(*args: str) -> bytes:
        calls.append(args)
        return json.dumps(
            [
                {
                    "Config": {
                        "Labels": {
                            "com.docker.compose.project": "development",
                            "com.docker.compose.service": "worker",
                        }
                    }
                }
            ]
        ).encode()

    monkeypatch.setattr(exercise_module, "command", command)
    with pytest.raises(ValueError, match="not owned"):
        exercise.fault("worker", "kill")
    assert calls == [("docker", "inspect", "container-id")]
    exercise.client.close()


def test_failure_overrides_previous_success_without_claiming_release_acceptance(
    exercise_module: ModuleType, tmp_path: Path
) -> None:
    exercise = exercise_module.Exercise(tmp_path / ".data/isolated-recovery/iip-recovery-a1")
    exercise.directory.mkdir(parents=True)
    exercise.scenario("first", lambda: {"verified": True})

    def failure() -> None:
        raise RuntimeError("observed recovery failure")

    with pytest.raises(RuntimeError, match="observed"):
        exercise.scenario("second", failure)
    report = json.loads((exercise.directory / "report.json").read_text())
    assert report["checks_passed"] is False
    assert report["release_accepted"] is False
    assert len(report["scenarios"]) == 2
    assert report["scenarios"][1]["passed"] is False
    exercise.client.close()
