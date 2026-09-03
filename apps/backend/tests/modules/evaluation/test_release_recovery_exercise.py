from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest

from industry_platform.modules.evaluation import release_recovery_exercise as exercise_module
from industry_platform.modules.evaluation.release_recovery_exercise import (
    ExerciseBinding,
    RecoveryExerciseFailure,
    execute,
    probe,
)

RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
WORKSPACE_ID = UUID("22222222-2222-4222-8222-222222222222")


def _binding(tmp_path: Path, scenario_id: str = "redis-outage-recovery") -> ExerciseBinding:
    return ExerciseBinding(
        scenario_id=scenario_id,
        environment="disposable:release-recovery-test",
        state_directory=tmp_path / scenario_id,
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
    )


def test_binding_rejects_unknown_scenarios_and_partial_runtime_identity(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not registered"):
        _binding(tmp_path, "unknown")
    with pytest.raises(ValueError, match="must be paired"):
        ExerciseBinding(
            scenario_id="fresh-migration",
            environment="disposable:test",
            state_directory=tmp_path,
            run_id=RUN_ID,
            workspace_id=None,
        )


def test_outage_execution_restores_the_service_before_running_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(exercise_module, "_settings", lambda: object())
    monkeypatch.setattr(
        exercise_module,
        "_compose",
        lambda service, action: events.append(f"compose:{service}:{action}"),
    )
    monkeypatch.setattr(
        exercise_module,
        "_wait_service",
        lambda service, *, available, timeout=120.0: events.append(
            f"wait:{service}:{available}:{timeout}"
        ),
    )
    monkeypatch.setattr(
        exercise_module,
        "_pytest",
        lambda scenario_id: events.append(f"pytest:{scenario_id}"),
    )

    result = execute(_binding(tmp_path))

    assert result["ok"] is True
    assert events == [
        "compose:redis:stop",
        "wait:redis:False:60",
        "compose:redis:start",
        "wait:redis:True:120.0",
        "pytest:redis-outage-recovery",
    ]
    stored = json.loads(
        (tmp_path / "redis-outage-recovery" / "exercise-result.json").read_text(encoding="utf-8")
    )
    assert stored["details"] == {"fault_observed": True, "service_recovered": True}


def test_outage_execution_restarts_service_when_fault_probe_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(exercise_module, "_settings", lambda: object())
    monkeypatch.setattr(
        exercise_module,
        "_compose",
        lambda service, action: events.append(f"{service}:{action}"),
    )

    def fail_wait(service: str, *, available: bool, timeout: float = 120.0) -> None:
        del service, timeout
        if not available:
            raise RecoveryExerciseFailure("fault was not observed")

    monkeypatch.setattr(exercise_module, "_wait_service", fail_wait)

    with pytest.raises(RecoveryExerciseFailure, match="not observed"):
        execute(_binding(tmp_path))

    assert events == ["redis:stop", "redis:start"]


def test_probe_requires_execution_and_compares_durable_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _binding(tmp_path, "worker-interruption-resume")
    state = {
        "database_sha256": "a" * 64,
        "runtime_binding_required": True,
        "run_exists": True,
        "run_id": str(RUN_ID),
        "workspace_id": str(WORKSPACE_ID),
        "scenario_id": binding.scenario_id,
    }
    monkeypatch.setattr(exercise_module, "_settings", lambda: object())
    monkeypatch.setattr(exercise_module, "_business_state", lambda settings, item: state)
    monkeypatch.setattr(exercise_module, "_integrity_counts", lambda settings, item: (0, 0))

    start = probe(binding, phase="start")
    assert start.checks == {}
    with pytest.raises(FileNotFoundError):
        probe(binding, phase="final")

    binding.state_directory.mkdir(parents=True, exist_ok=True)
    (binding.state_directory / "exercise-result.json").write_text(
        json.dumps({"ok": True}), encoding="utf-8"
    )
    final = probe(binding, phase="final")

    assert final.checks == {
        "exercise_completed": True,
        "durable_state_preserved": True,
        "runtime_binding_visible": True,
    }
    assert final.data_loss_count == 0


def test_probe_fails_durable_state_check_when_business_hash_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _binding(tmp_path, "worker-interruption-resume")
    state = {
        "database_sha256": "a" * 64,
        "runtime_binding_required": True,
        "run_exists": True,
        "workspace_id": str(WORKSPACE_ID),
    }
    monkeypatch.setattr(exercise_module, "_settings", lambda: object())
    monkeypatch.setattr(exercise_module, "_business_state", lambda settings, item: dict(state))
    monkeypatch.setattr(exercise_module, "_integrity_counts", lambda settings, item: (0, 0))

    probe(binding, phase="start")
    binding.state_directory.joinpath("exercise-result.json").write_text(
        json.dumps({"ok": True}), encoding="utf-8"
    )
    state["database_sha256"] = "b" * 64

    final = probe(binding, phase="final")

    assert final.data_loss_count == 1
    assert final.checks["durable_state_preserved"] is False


def test_previous_image_requires_an_immutable_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PREVIOUS_IMAGE_DIGEST", raising=False)

    with pytest.raises(RecoveryExerciseFailure, match="missing or is not immutable"):
        exercise_module._previous_image()
