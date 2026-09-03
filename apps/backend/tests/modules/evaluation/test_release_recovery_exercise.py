from __future__ import annotations

import json
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from uuid import UUID

import pytest

from industry_platform.core.config import Settings
from industry_platform.modules.evaluation import release_recovery_exercise as exercise_module
from industry_platform.modules.evaluation.release_recovery_exercise import (
    ExerciseBinding,
    RecoveryExerciseFailure,
    execute,
    probe,
)

RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
WORKSPACE_ID = UUID("22222222-2222-4222-8222-222222222222")


class _ScriptedCursor:
    def __init__(
        self,
        *,
        fetchall_results: list[list[tuple[object, ...]]] | None = None,
        fetchone_results: list[tuple[object, ...] | None] | None = None,
    ) -> None:
        self.fetchall_results = list(fetchall_results or [])
        self.fetchone_results = list(fetchone_results or [])

    def __enter__(self) -> _ScriptedCursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, *args: object) -> None:
        return None

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.fetchall_results.pop(0)

    def fetchone(self) -> tuple[object, ...] | None:
        return self.fetchone_results.pop(0)


class _FakeConnection:
    def __init__(self, cursor: _ScriptedCursor) -> None:
        self._cursor = cursor
        self.autocommit = False
        self.executed: list[object] = []

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def cursor(self) -> _ScriptedCursor:
        return self._cursor

    def execute(self, statement: object) -> None:
        self.executed.append(statement)


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
    with pytest.raises(ValueError, match="environment is invalid"):
        ExerciseBinding(
            scenario_id="fresh-migration",
            environment="not allowed",
            state_directory=tmp_path,
            run_id=None,
            workspace_id=None,
        )
    with pytest.raises(ValueError, match="nil UUIDs"):
        ExerciseBinding(
            scenario_id="fresh-migration",
            environment="disposable:test",
            state_directory=tmp_path,
            run_id=UUID(int=0),
            workspace_id=UUID(int=0),
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


def test_json_and_command_helpers_are_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_path = tmp_path / "state.json"
    exercise_module._write_json(state_path, {"value": 1})
    assert exercise_module._read_json(state_path) == {"value": 1}
    assert len(exercise_module._json_sha256({"value": 1})) == 64
    state_path.write_text("[]", encoding="utf-8")
    with pytest.raises(RecoveryExerciseFailure, match="state is invalid"):
        exercise_module._read_json(state_path)

    seen: list[tuple[tuple[str, ...], bytes | None]] = []

    def run(args: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        seen.append((args, kwargs["input"]))  # type: ignore[arg-type]
        return subprocess.CompletedProcess(args, 0, b"ok", b"")

    monkeypatch.setattr(subprocess, "run", run)
    completed = exercise_module._run(("fixed-command", "arg"), input_bytes=b"input")
    assert completed.stdout == b"ok"
    assert seen == [(("fixed-command", "arg"), b"input")]

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 2, b"", b"failed"),
    )
    with pytest.raises(RecoveryExerciseFailure, match="exit code 2: fixed-command"):
        exercise_module._run(("fixed-command",))


@pytest.mark.parametrize("action", ["stop", "start"])
def test_compose_builds_only_registered_fixed_commands(
    action: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[str, ...], int]] = []
    monkeypatch.setattr(
        exercise_module,
        "_run",
        lambda command, timeout: calls.append((tuple(command), timeout)),
    )
    exercise_module._compose("milvus", action)  # type: ignore[arg-type]
    command, timeout = calls[0]
    assert command[:7] == (
        "docker",
        "compose",
        "--env-file",
        ".env",
        "-f",
        "infra/compose/compose.yaml",
        "--profile",
    )
    assert command[-2:] == (("stop", "milvus") if action == "stop" else ("--wait", "milvus"))
    assert timeout == 300


def test_pytest_runner_rejects_unknown_and_skipped_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(RecoveryExerciseFailure, match="no fixed pytest reference"):
        exercise_module._pytest("unknown")
    monkeypatch.setattr(
        exercise_module,
        "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, b"1 skipped", b""),
    )
    with pytest.raises(RecoveryExerciseFailure, match="skipped required evidence"):
        exercise_module._pytest("fresh-migration")
    monkeypatch.setattr(
        exercise_module,
        "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, b"1 passed", b""),
    )
    exercise_module._pytest("fresh-migration")


def test_service_probes_and_waits_for_expected_state(monkeypatch: pytest.MonkeyPatch) -> None:
    class SocketContext:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: SocketContext())
    assert exercise_module._service_available("redis") is True
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError()),
    )
    assert exercise_module._service_available("redis") is False

    class HttpResponse:
        status = 204

        def __enter__(self) -> HttpResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: HttpResponse())
    assert exercise_module._service_available("minio") is True
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError()),
    )
    assert exercise_module._service_available("minio") is False

    responses = iter((False, True))
    monkeypatch.setattr(exercise_module, "_service_available", lambda service: next(responses))
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    exercise_module._wait_service("redis", available=True, timeout=5)

    monkeypatch.setattr(exercise_module, "_service_available", lambda service: False)
    moments = iter((0.0, 0.0, 1.0))
    monkeypatch.setattr(time, "monotonic", lambda: next(moments))
    with pytest.raises(RecoveryExerciseFailure, match="did not become available"):
        exercise_module._wait_service("redis", available=True, timeout=1)


def test_database_state_hashes_business_rows_and_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = _ScriptedCursor(
        fetchall_results=[
            [("agent_runs",), ("alembic_version",)],
            [("row-b",), ("row-a",)],
        ],
        fetchone_results=[(2,), ("revision-1",)],
    )
    monkeypatch.setattr(
        exercise_module, "_connect", lambda settings, database=None: _FakeConnection(cursor)
    )

    state = exercise_module._database_state(object())  # type: ignore[arg-type]

    assert state["agent_runs_count"] == 2
    assert state["alembic_revision"] == "revision-1"
    assert len(str(state["database_sha256"])) == 64


def test_database_state_rejects_a_disappearing_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = _ScriptedCursor(
        fetchall_results=[[("agent_runs",)]],
        fetchone_results=[None],
    )
    monkeypatch.setattr(
        exercise_module, "_connect", lambda settings, database=None: _FakeConnection(cursor)
    )
    with pytest.raises(RecoveryExerciseFailure, match="count disappeared"):
        exercise_module._database_state(object())  # type: ignore[arg-type]


def test_run_and_business_state_preserve_runtime_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unbound = ExerciseBinding(
        scenario_id="fresh-migration",
        environment="disposable:test",
        state_directory=tmp_path,
        run_id=None,
        workspace_id=None,
    )
    assert exercise_module._run_state(object(), unbound) == {  # type: ignore[arg-type]
        "runtime_binding_required": False
    }

    missing_cursor = _ScriptedCursor(fetchone_results=[None])
    monkeypatch.setattr(
        exercise_module,
        "_connect",
        lambda settings, database=None: _FakeConnection(missing_cursor),
    )
    missing = exercise_module._run_state(object(), _binding(tmp_path))  # type: ignore[arg-type]
    assert missing["run_exists"] is False

    present_cursor = _ScriptedCursor(fetchone_results=[("completed", 4, 2, WORKSPACE_ID)])
    monkeypatch.setattr(
        exercise_module,
        "_connect",
        lambda settings, database=None: _FakeConnection(present_cursor),
    )
    present = exercise_module._run_state(object(), _binding(tmp_path))  # type: ignore[arg-type]
    assert present["run_exists"] is True
    assert present["run_event_count"] == 4
    assert present["workspace_id"] == str(WORKSPACE_ID)

    monkeypatch.setattr(
        exercise_module,
        "_database_state",
        lambda settings: {"kept": 1, "discarded_float": 1.5},
    )
    monkeypatch.setattr(
        exercise_module,
        "_run_state",
        lambda settings, binding: {"run_exists": True, "discarded_none": None},
    )
    business = exercise_module._business_state(object(), _binding(tmp_path))  # type: ignore[arg-type]
    assert business == {
        "kept": 1,
        "run_exists": True,
        "scenario_id": "redis-outage-recovery",
    }


def test_integrity_counts_sum_duplicates_and_reject_missing_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = _ScriptedCursor(fetchone_results=[(2,), (3,), (4,)])
    monkeypatch.setattr(
        exercise_module, "_connect", lambda settings, database=None: _FakeConnection(cursor)
    )
    assert exercise_module._integrity_counts(
        object(),  # type: ignore[arg-type]
        _binding(tmp_path),
    ) == (5, 4)

    missing_cursor = _ScriptedCursor(fetchone_results=[(0,), None, (0,)])
    monkeypatch.setattr(
        exercise_module,
        "_connect",
        lambda settings, database=None: _FakeConnection(missing_cursor),
    )
    with pytest.raises(RecoveryExerciseFailure, match="query returned no result"):
        exercise_module._integrity_counts(object(), _binding(tmp_path))  # type: ignore[arg-type]


def test_backup_restore_verifies_digest_and_always_drops_disposable_database(
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connections: list[_FakeConnection] = []
    commands: list[tuple[str, ...]] = []

    def connect(settings: object, database: str | None = None) -> _FakeConnection:
        connection = _FakeConnection(_ScriptedCursor())
        connections.append(connection)
        return connection

    def run(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        commands.append(tuple(command))
        stdout = b"database-dump" if "pg_dump" in command else b""
        return subprocess.CompletedProcess(command, 0, stdout, b"")

    monkeypatch.setattr(exercise_module, "_connect", connect)
    monkeypatch.setattr(exercise_module, "_run", run)
    monkeypatch.setattr(time, "time_ns", lambda: 12345)
    monkeypatch.setattr(exercise_module, "_database_digest", lambda settings, database: "a" * 64)

    result = exercise_module._backup_restore(test_settings)

    assert result == {
        "backup_bytes": len(b"database-dump"),
        "source_sha256": "a" * 64,
        "restored_sha256": "a" * 64,
    }
    assert any("pg_dump" in command for command in commands)
    assert any("pg_restore" in command for command in commands)
    assert connections[0].autocommit is True
    assert connections[-1].autocommit is True
    assert connections[-1].executed

    digests = iter(("a" * 64, "b" * 64))
    monkeypatch.setattr(
        exercise_module, "_database_digest", lambda settings, database: next(digests)
    )
    with pytest.raises(RecoveryExerciseFailure, match="digest differs"):
        exercise_module._backup_restore(test_settings)
    assert connections[-1].executed


def test_previous_image_requires_valid_inspection_and_smoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = f"registry.example/app@sha256:{'a' * 64}"
    monkeypatch.setenv("PREVIOUS_IMAGE_DIGEST", digest)
    outputs = iter(
        (
            subprocess.CompletedProcess((), 0, b"[{}]", b""),
            subprocess.CompletedProcess((), 0, b"previous-image-runtime-ok\n", b""),
        )
    )
    monkeypatch.setattr(exercise_module, "_run", lambda *args, **kwargs: next(outputs))
    assert exercise_module._previous_image() == {
        "image_digest": digest,
        "runtime_import": True,
    }

    monkeypatch.setattr(
        exercise_module,
        "_run",
        lambda *args, **kwargs: subprocess.CompletedProcess((), 0, b"{}", b""),
    )
    with pytest.raises(RecoveryExerciseFailure, match="invalid identity"):
        exercise_module._previous_image()

    outputs = iter(
        (
            subprocess.CompletedProcess((), 0, b"[{}]", b""),
            subprocess.CompletedProcess((), 0, b"wrong", b""),
        )
    )
    monkeypatch.setattr(exercise_module, "_run", lambda *args, **kwargs: next(outputs))
    with pytest.raises(RecoveryExerciseFailure, match="runtime smoke did not pass"):
        exercise_module._previous_image()


@pytest.mark.parametrize(
    ("scenario_id", "details"),
    [
        ("postgres-backup-restore", {"backup": True}),
        ("previous-image-rollback", {"image": True}),
        ("fresh-migration", {"verification_passed": True}),
    ],
)
def test_execute_routes_non_outage_scenarios(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario_id: str,
    details: dict[str, bool],
) -> None:
    monkeypatch.setattr(exercise_module, "_settings", lambda: object())
    monkeypatch.setattr(exercise_module, "_backup_restore", lambda settings: {"backup": True})
    monkeypatch.setattr(exercise_module, "_previous_image", lambda: {"image": True})
    monkeypatch.setattr(exercise_module, "_pytest", lambda scenario: None)
    result = execute(_binding(tmp_path, scenario_id))
    assert result["details"] == details


def test_main_enforces_state_root_and_reports_execution_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    common = [
        "--scenario",
        "fresh-migration",
        "--environment",
        "disposable:test",
        "--state-directory",
        ".data/evals/recovery",
    ]
    monkeypatch.setattr(exercise_module, "execute", lambda binding: {"ok": True})
    assert exercise_module.main([*common, "execute"]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True}

    monkeypatch.setattr(
        exercise_module,
        "execute",
        lambda binding: (_ for _ in ()).throw(RecoveryExerciseFailure("failed")),
    )
    assert exercise_module.main([*common, "execute"]) == 1
    error = json.loads(capsys.readouterr().err)
    assert error["ok"] is False
    assert error["error"] == "RecoveryExerciseFailure"

    with pytest.raises(SystemExit, match="must be inside"):
        exercise_module.main(
            [
                "--scenario",
                "fresh-migration",
                "--environment",
                "disposable:test",
                "--state-directory",
                str(tmp_path.parent / "outside"),
                "execute",
            ]
        )
