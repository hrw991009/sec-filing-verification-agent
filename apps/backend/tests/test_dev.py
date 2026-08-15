"""Contracts for the local multi-process backend supervisor."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from industry_platform.core.config import Settings
from industry_platform.dev import (
    BACKEND_PROCESS_SPECS,
    BackendProcessSpec,
    ManagedProcess,
    backend_process_command,
    configured_file_storage_is_available,
    migration_check_command,
    run_backend_dev,
    shutdown_backend_processes,
    start_backend_process,
    supervise_backend_processes,
)


class FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.return_code: int | None = None
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.return_code

    def terminate(self) -> None:
        self.terminated = True
        self.return_code = -15

    def kill(self) -> None:
        self.killed = True
        self.return_code = -9


def test_backend_processes_are_fixed_independent_python_modules() -> None:
    assert [spec.name for spec in BACKEND_PROCESS_SPECS] == [
        "api",
        "outbox-dispatcher",
        "celery-worker",
        "job-reconciler",
        "celery-beat",
    ]
    assert backend_process_command(BACKEND_PROCESS_SPECS[0]) == (
        sys.executable,
        "-m",
        "industry_platform.server",
    )
    assert BACKEND_PROCESS_SPECS[2].arguments == ("--loglevel=INFO",)


def test_migration_check_is_read_only_and_requires_all_heads() -> None:
    assert migration_check_command(Path("custom.ini")) == (
        sys.executable,
        "-m",
        "alembic",
        "-c",
        "custom.ini",
        "current",
        "--check-heads",
    )


def test_process_start_uses_a_new_session_only_on_posix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launches: list[tuple[tuple[str, ...], bool]] = []
    processes = [FakeProcess(31), FakeProcess(32)]

    def popen(
        command: Sequence[str],
        *,
        start_new_session: bool,
    ) -> FakeProcess:
        launches.append((tuple(command), start_new_session))
        return processes[len(launches) - 1]

    monkeypatch.setattr("industry_platform.dev.subprocess.Popen", popen)
    start_backend_process(BACKEND_PROCESS_SPECS[0], platform_name="linux")
    start_backend_process(BACKEND_PROCESS_SPECS[0], platform_name="win32")
    assert [new_session for _command, new_session in launches] == [True, False]


def test_dependency_and_migration_preflight_fail_before_spawning(
    test_settings: Settings,
) -> None:
    calls: list[str] = []

    def record_migration() -> bool:
        calls.append("migration")
        return True

    def record_supervisor(
        _specs: Sequence[BackendProcessSpec],
        _platform: str,
    ) -> int:
        calls.append("supervisor")
        return 0

    result = run_backend_dev(
        settings=test_settings,
        dependency_checker=lambda _settings: ("Redis",),
        migration_checker=record_migration,
        supervisor=record_supervisor,
    )
    assert result == 2
    assert calls == []

    result = run_backend_dev(
        settings=test_settings,
        dependency_checker=lambda _settings: (),
        migration_checker=lambda: False,
        supervisor=record_supervisor,
    )
    assert result == 2
    assert calls == []


def test_successful_preflight_passes_all_services_to_supervisor(
    test_settings: Settings,
) -> None:
    received: list[tuple[Sequence[BackendProcessSpec], str]] = []

    def record_supervisor(specs: Sequence[BackendProcessSpec], platform: str) -> int:
        received.append((specs, platform))
        return 7

    result = run_backend_dev(
        settings=test_settings,
        dependency_checker=lambda _settings: (),
        migration_checker=lambda: True,
        supervisor=record_supervisor,
        platform_name="test-platform",
    )
    assert result == 7
    assert received == [(BACKEND_PROCESS_SPECS, "test-platform")]


def test_object_storage_preflight_requires_the_configured_private_bucket(
    test_settings: Settings,
) -> None:
    factory_called = False

    def unexpected_factory(_settings: Settings) -> None:
        nonlocal factory_called
        factory_called = True

    assert (
        asyncio.run(
            configured_file_storage_is_available(
                test_settings,
                store_factory=unexpected_factory,
            )
        )
        is False
    )
    assert factory_called is False

    requested_buckets: list[str] = []

    class AvailableStore:
        async def bucket_exists(self, *, bucket: str) -> bool:
            requested_buckets.append(bucket)
            return True

    configured_settings = test_settings.model_copy(update={"minio_bucket": "private-test-bucket"})
    assert (
        asyncio.run(
            configured_file_storage_is_available(
                configured_settings,
                store_factory=lambda _settings: AvailableStore(),
            )
        )
        is True
    )
    assert requested_buckets == ["private-test-bucket"]


def test_child_exit_or_keyboard_interrupt_stops_the_whole_group() -> None:
    processes = [FakeProcess(index) for index in range(1, 6)]
    launched: list[BackendProcessSpec] = []
    shutdown_calls: list[tuple[tuple[int, ...], str]] = []

    def launcher(spec: BackendProcessSpec) -> FakeProcess:
        launched.append(spec)
        return processes[len(launched) - 1]

    def exit_one(_seconds: float) -> None:
        processes[2].return_code = 9

    def record_shutdown(children: Sequence[ManagedProcess], platform: str) -> None:
        shutdown_calls.append((tuple(child.pid for child in children), platform))

    result = supervise_backend_processes(
        BACKEND_PROCESS_SPECS,
        "test-platform",
        launcher=launcher,
        shutdown=record_shutdown,
        sleeper=exit_one,
    )
    assert result == 9
    assert launched == list(BACKEND_PROCESS_SPECS)
    assert shutdown_calls == [((1, 2, 3, 4, 5), "test-platform")]

    shutdown_calls.clear()
    launched.clear()
    for process in processes:
        process.return_code = None

    def interrupt(_seconds: float) -> None:
        raise KeyboardInterrupt

    result = supervise_backend_processes(
        BACKEND_PROCESS_SPECS,
        "test-platform",
        launcher=launcher,
        shutdown=record_shutdown,
        sleeper=interrupt,
    )
    assert result == 130
    assert shutdown_calls[-1][1] == "test-platform"


def test_spawn_failure_cleans_already_started_children() -> None:
    first = FakeProcess(1)
    shutdown_calls: list[tuple[int, ...]] = []

    def launcher(spec: BackendProcessSpec) -> FakeProcess:
        if spec.name != "api":
            raise OSError("spawn unavailable")
        return first

    def record_shutdown(children: Sequence[ManagedProcess], _platform: str) -> None:
        shutdown_calls.append(tuple(child.pid for child in children))

    result = supervise_backend_processes(
        BACKEND_PROCESS_SPECS,
        "test-platform",
        launcher=launcher,
        shutdown=record_shutdown,
    )
    assert result == 1
    assert shutdown_calls == [(1,)]


def test_shutdown_uses_graceful_posix_stop_and_windows_tree_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posix_process = FakeProcess(10)
    posix_signals: list[tuple[int, int]] = []

    def killpg(pid: int, signal_number: int) -> None:
        posix_signals.append((pid, signal_number))
        if signal_number == 15:
            posix_process.return_code = -15
        else:
            raise ProcessLookupError

    monkeypatch.setattr(os, "killpg", killpg, raising=False)
    shutdown_backend_processes(
        (posix_process,),
        "linux",
        natural_grace_seconds=0,
        terminate_grace_seconds=0,
    )
    assert posix_signals == [(10, 15), (10, 9)]
    assert posix_process.terminated is False
    assert posix_process.killed is False

    windows_process = FakeProcess(11)
    commands: list[tuple[str, ...]] = []

    def run(
        command: Sequence[str],
        *,
        check: bool,
        stderr: int,
        stdout: int,
    ) -> subprocess.CompletedProcess[bytes]:
        del check, stderr, stdout
        commands.append(tuple(command))
        windows_process.return_code = 1
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("industry_platform.dev.subprocess.run", run)
    shutdown_backend_processes(
        (windows_process,),
        "win32",
        natural_grace_seconds=0,
        terminate_grace_seconds=0,
    )
    assert commands == [("taskkill", "/PID", "11", "/T", "/F")]
    assert windows_process.terminated is False

    failed_tree_process = FakeProcess(12)

    def failed_run(
        command: Sequence[str],
        *,
        check: bool,
        stderr: int,
        stdout: int,
    ) -> subprocess.CompletedProcess[bytes]:
        del check, stderr, stdout
        commands.append(tuple(command))
        return subprocess.CompletedProcess(command, 1)

    monkeypatch.setattr("industry_platform.dev.subprocess.run", failed_run)
    shutdown_backend_processes(
        (failed_tree_process,),
        "win32",
        natural_grace_seconds=0,
        terminate_grace_seconds=0,
    )
    assert failed_tree_process.killed is True
    assert commands[-1] == ("taskkill", "/PID", "12", "/T", "/F")


def test_repeated_keyboard_interrupt_does_not_abort_remaining_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class InterruptingProcess(FakeProcess):
        def terminate(self) -> None:
            self.terminated = True
            raise KeyboardInterrupt

    processes = (InterruptingProcess(21), InterruptingProcess(22))

    def interrupted_wait(
        _processes: Sequence[ManagedProcess],
        _timeout_seconds: float,
    ) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("industry_platform.dev._wait_for_processes", interrupted_wait)

    def interrupted_killpg(_pid: int, _signal_number: int) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "killpg", interrupted_killpg, raising=False)
    shutdown_backend_processes(
        processes,
        "linux",
        natural_grace_seconds=0,
        terminate_grace_seconds=0,
    )
    assert all(process.terminated for process in processes)
    assert all(process.killed for process in processes)
