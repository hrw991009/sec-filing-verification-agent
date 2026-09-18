"""Isolation and fail-closed evidence contracts for the opt-in container exercise."""

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

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
    assert (
        exercise_module.verification_summary(report, require_rollback=True)["checks_passed"]
        is False
    )
    scenarios.append({"name": "previous-immutable-image-rollback", "passed": True})
    rollback_summary = exercise_module.verification_summary(report, require_rollback=True)
    assert rollback_summary["checks_passed"] is True
    assert rollback_summary["logical_check_count"] == 12
    assert rollback_summary["release_accepted"] is False
    scenarios.append({"name": "previous-immutable-image-rollback", "passed": False})
    assert (
        exercise_module.verification_summary(report, require_rollback=True)["checks_passed"]
        is False
    )
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


def test_prepare_rejects_wrong_source_before_creating_environment(
    exercise_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / ".data/isolated-recovery/iip-recovery-a1"
    exercise = exercise_module.Exercise(directory)

    def command(*args: str) -> bytes:
        if args[0] == "git":
            return b"expected-commit\n"
        return json.dumps(
            [{"Config": {"Labels": {"org.opencontainers.image.revision": "old"}}}]
        ).encode()

    monkeypatch.setattr(exercise_module, "command", command)
    with pytest.raises(ValueError, match="revision mismatch"):
        exercise.prepare("image", "expected-commit")
    assert not directory.exists()
    exercise.client.close()


@pytest.fixture
def rollback_module(exercise_module: ModuleType, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    import sys

    monkeypatch.setitem(sys.modules, "exercise", exercise_module)
    path = Path(__file__).resolve().parents[3] / "infra/recovery/rollback.py"
    spec = importlib.util.spec_from_file_location("isolated_image_rollback", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("invalid", ["latest", "repo:v1", "sha256:abcd"])
def test_rollback_requires_immutable_distinct_images(
    rollback_module: ModuleType, invalid: str
) -> None:
    current = "sha256:" + "a" * 64
    config = {"services": {name: {"image": current} for name in rollback_module.ACTORS}}
    with pytest.raises(ValueError, match="immutable"):
        rollback_module.validate_images(config, current, invalid)
    with pytest.raises(ValueError, match="distinct"):
        rollback_module.validate_images(config, current, current)


def test_rollback_rejects_mixed_actor_images(rollback_module: ModuleType) -> None:
    current, previous = "sha256:" + "a" * 64, "sha256:" + "b" * 64
    config = {"services": {name: {"image": current} for name in rollback_module.ACTORS}}
    rollback_module.validate_images(config, current, previous)
    config["services"]["worker"]["image"] = previous
    with pytest.raises(ValueError, match="share"):
        rollback_module.validate_images(config, current, previous)


@pytest.mark.parametrize("resolvable", [True, False])
def test_rollback_smoke_requires_resolvable_citations(
    rollback_module: ModuleType, resolvable: bool
) -> None:
    class FakeExercise:
        workspace = "workspace"

        def wait_api(self) -> None:
            pass

        def authenticate(self) -> None:
            pass

        def rows(self, query: str, args: tuple[str, ...]) -> list[dict[str, str]]:
            return [{"id": "record"}]

        def api(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
            if path.endswith("/filings"):
                return {"filings": [{"accession": "0000320193-23-000106"}]}
            if path.endswith("/resolve"):
                return {"resolvable": resolvable}
            if path.endswith("/monitors"):
                return {"monitors": []}
            if path.endswith("/cases"):
                return {"cases": []}
            return {"id": "record"}

    if resolvable:
        result = rollback_module.smoke(FakeExercise())
        assert result["resolved_evidence_ids"] == ["record"]
        assert result["monitor_count"] == 0
    else:
        with pytest.raises(AssertionError, match="cannot be resolved"):
            rollback_module.smoke(FakeExercise())


def test_rollback_restores_baseline_configuration_when_old_image_smoke_fails(
    rollback_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import MagicMock

    from industry_platform.core import config as settings_module
    from industry_platform.modules.evaluation import release_recovery_exercise as recovery_module

    current, previous = "sha256:" + "a" * 64, "sha256:" + "b" * 64
    exercise = MagicMock()
    exercise.directory = tmp_path
    exercise.compose_file = tmp_path / "compose.json"
    original_config = json.dumps(
        {"services": {name: {"image": current} for name in rollback_module.ACTORS}}
    ).encode()
    exercise.compose_file.write_bytes(original_config)
    original_env = b"POSTGRES_DB=iip_recovery\nPRIVATE=test-secret\n"
    (tmp_path / "runtime.env").write_bytes(original_env)
    exercise.env = {"POSTGRES_DB": "iip_recovery", "PRIVATE": "test-secret"}
    exercise.report = {"application_image_id": current}
    exercise.rows.return_value = [{"version_num": "schema"}]
    monkeypatch.setattr(rollback_module, "image_actors", MagicMock(return_value={}))
    monkeypatch.setattr(settings_module, "Settings", MagicMock())
    monkeypatch.setattr(recovery_module, "_database_digest", MagicMock(return_value="digest"))
    monkeypatch.setattr(
        rollback_module,
        "smoke",
        MagicMock(side_effect=[{"valid": True}, RuntimeError("old image unhealthy")]),
    )

    def command(*args: str, **kwargs: Any) -> bytes:
        return b"schema (head)" if args[-1] == "heads" else b"dump"

    monkeypatch.setattr(rollback_module, "command", command)
    with pytest.raises(RuntimeError, match="old image unhealthy"):
        rollback_module.rollback(exercise, previous)
    assert exercise.compose_file.read_bytes() == original_config
    assert (tmp_path / "runtime.env").read_bytes() == original_env
    assert exercise.env["POSTGRES_DB"] == "iip_recovery"
    snapshot = json.loads((tmp_path / "rollback-snapshot.json").read_text())
    assert snapshot["source_unchanged"] is True
    assert "test-secret" not in json.dumps(snapshot)
    exercise.compose.assert_called_with("up", "-d", "--force-recreate", *rollback_module.ACTORS)
