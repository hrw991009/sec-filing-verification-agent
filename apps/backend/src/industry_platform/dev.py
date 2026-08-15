"""Local supervisor for the backend's independently deployable processes."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Final, Protocol, TextIO, cast

from industry_platform.core.config import Settings, get_settings
from industry_platform.core.database import check_database_connection, create_database_engine
from industry_platform.core.health import DependencyStatus, assess_readiness
from industry_platform.core.redis_client import check_redis_connection, create_redis_client
from industry_platform.modules.files.ports import FileObjectStoreError
from industry_platform.modules.files.resources import create_private_file_object_store
from industry_platform.server import create_selector_event_loop

DEPENDENCY_START_COMMAND: Final = (
    "docker compose --env-file '.env' -f infra/compose/compose.yaml "
    "up -d --wait postgres redis minio"
)
OBJECT_STORAGE_INIT_COMMAND: Final = (
    "docker compose --env-file '.env' -f infra/compose/compose.yaml run --rm --no-deps minio-init"
)
MIGRATION_UPGRADE_COMMAND: Final = (
    "uv run --env-file '.env' --locked --package industry-platform-backend "
    "alembic -c apps/backend/alembic.ini upgrade head"
)
DEFAULT_ALEMBIC_CONFIG: Final = Path("apps/backend/alembic.ini")
POLL_INTERVAL_SECONDS: Final = 0.25
NATURAL_SHUTDOWN_GRACE_SECONDS: Final = 2.0
TERMINATE_GRACE_SECONDS: Final = 5.0
POSIX_SIGTERM: Final = 15
POSIX_SIGKILL: Final = 9


@dataclass(frozen=True, slots=True)
class BackendProcessSpec:
    """One required long-running process in the local backend stack."""

    name: str
    module: str
    arguments: tuple[str, ...] = ()


BACKEND_PROCESS_SPECS: Final = (
    BackendProcessSpec("api", "industry_platform.server"),
    BackendProcessSpec("outbox-dispatcher", "industry_platform.workers.dispatcher"),
    BackendProcessSpec(
        "celery-worker",
        "industry_platform.workers.celery_app",
        ("--loglevel=INFO",),
    ),
    BackendProcessSpec("job-reconciler", "industry_platform.workers.reconciler"),
    BackendProcessSpec(
        "celery-beat",
        "industry_platform.workers.beat",
        ("--loglevel=INFO",),
    ),
)


class ManagedProcess(Protocol):
    """Narrow subprocess surface used by the supervisor and its tests."""

    pid: int

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


class FileStorageProbe(Protocol):
    """Narrow MinIO readiness surface used by the development preflight."""

    async def bucket_exists(self, *, bucket: str) -> bool: ...


type ProcessLauncher = Callable[[BackendProcessSpec], ManagedProcess]
type ProcessShutdown = Callable[[Sequence[ManagedProcess], str], None]
type DependencyChecker = Callable[[Settings], tuple[str, ...]]
type MigrationChecker = Callable[[], bool]
type Supervisor = Callable[[Sequence[BackendProcessSpec], str], int]
type FileStorageProbeFactory = Callable[[Settings], FileStorageProbe | None]


def _write_line(message: str, *, stream: TextIO | None = None) -> None:
    destination = stream or sys.stderr
    destination.write(f"{message}\n")
    destination.flush()


def backend_process_command(spec: BackendProcessSpec) -> tuple[str, ...]:
    """Build one child command without recursively invoking uv or a shell."""

    return (sys.executable, "-m", spec.module, *spec.arguments)


def start_backend_process(
    spec: BackendProcessSpec,
    *,
    platform_name: str | None = None,
) -> subprocess.Popen[bytes]:
    """Start one child with inherited environment and unbuffered terminal logs."""

    _write_line(f"[backend-dev] starting {spec.name}")
    # The executable is sys.executable and every module/argument comes from the
    # fixed BACKEND_PROCESS_SPECS registry; no user-controlled shell is involved.
    return subprocess.Popen(  # noqa: S603
        backend_process_command(spec),
        start_new_session=(platform_name or sys.platform) != "win32",
    )


def _wait_for_processes(processes: Sequence[ManagedProcess], timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while any(process.poll() is None for process in processes) and time.monotonic() < deadline:
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))


def _force_kill_windows_tree(process: ManagedProcess) -> None:
    completed: subprocess.CompletedProcess[bytes] | None = None
    with suppress(KeyboardInterrupt, OSError):
        # taskkill is a fixed Windows system command and pid is an integer from Popen.
        completed = subprocess.run(  # noqa: S603
            ("taskkill", "/PID", str(process.pid), "/T", "/F"),  # noqa: S607
            check=False,
            stderr=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
        )

    if (completed is None or completed.returncode != 0) and process.poll() is None:
        _write_line(
            f"[backend-dev] process-tree cleanup failed for pid {process.pid}; "
            "killing the tracked process"
        )
        with suppress(KeyboardInterrupt, OSError):
            process.kill()


def _signal_posix_process_group(process: ManagedProcess, signal_number: int) -> None:
    """Signal a child session as one tree, with a direct-process fallback."""

    killpg_value = getattr(os, "killpg", None)
    with suppress(KeyboardInterrupt, OSError):
        if killpg_value is None:
            raise OSError("process-group signalling is unavailable")
        killpg = cast(Callable[[int, int], None], killpg_value)
        killpg(process.pid, signal_number)
        return

    if process.poll() is None:
        operation = process.kill if signal_number == POSIX_SIGKILL else process.terminate
        with suppress(KeyboardInterrupt, OSError):
            operation()


def shutdown_backend_processes(
    processes: Sequence[ManagedProcess],
    platform_name: str,
    *,
    natural_grace_seconds: float = NATURAL_SHUTDOWN_GRACE_SECONDS,
    terminate_grace_seconds: float = TERMINATE_GRACE_SECONDS,
) -> None:
    """Stop every child, using a Windows process-tree fallback when required."""

    ordered = tuple(reversed(processes))
    # A second Ctrl+C means "stop now", but it must not skip the remaining children.
    with suppress(KeyboardInterrupt):
        _wait_for_processes(ordered, natural_grace_seconds)

    if platform_name == "win32":
        for process in ordered:
            _force_kill_windows_tree(process)
        return

    for process in ordered:
        _signal_posix_process_group(process, POSIX_SIGTERM)

    with suppress(KeyboardInterrupt):
        _wait_for_processes(ordered, terminate_grace_seconds)
    for process in ordered:
        # A prefork child can outlive its leader, so always check the whole group.
        _signal_posix_process_group(process, POSIX_SIGKILL)


def supervise_backend_processes(
    specs: Sequence[BackendProcessSpec],
    platform_name: str,
    *,
    launcher: ProcessLauncher = start_backend_process,
    shutdown: ProcessShutdown = shutdown_backend_processes,
    sleeper: Callable[[float], None] = time.sleep,
) -> int:
    """Run every required child and fail the group when one exits unexpectedly."""

    processes: list[ManagedProcess] = []
    active_name = "backend process"
    try:
        for spec in specs:
            active_name = spec.name
            processes.append(launcher(spec))

        while True:
            for spec, process in zip(specs, processes, strict=True):
                return_code = process.poll()
                if return_code is not None:
                    _write_line(
                        f"[backend-dev] {spec.name} exited unexpectedly with code {return_code}",
                    )
                    return return_code if return_code != 0 else 1
            sleeper(POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        _write_line("\n[backend-dev] stopping backend processes")
        return 130
    except OSError as error:
        detail = error.strerror or type(error).__name__
        _write_line(f"[backend-dev] could not start {active_name}: {detail}")
        return 1
    finally:
        shutdown(processes, platform_name)


async def unavailable_required_dependencies(settings: Settings) -> tuple[str, ...]:
    """Return public names for unavailable required dependencies without secrets."""

    engine = create_database_engine(settings)
    redis_client = create_redis_client(settings)
    try:
        report = await assess_readiness(
            postgres_check=partial(check_database_connection, engine),
            redis_check=partial(check_redis_connection, redis_client),
            timeout_seconds=settings.health_check_timeout_seconds,
        )
    finally:
        await redis_client.aclose()
        await engine.dispose()

    unavailable: list[str] = []
    if report.postgres is DependencyStatus.FAILED:
        unavailable.append("PostgreSQL")
    if report.redis is DependencyStatus.FAILED:
        unavailable.append("Redis")
    if not await configured_file_storage_is_available(settings):
        unavailable.append("MinIO private bucket")
    return tuple(unavailable)


async def configured_file_storage_is_available(
    settings: Settings,
    *,
    store_factory: FileStorageProbeFactory = create_private_file_object_store,
) -> bool:
    """Check that attachment storage is configured, reachable, and initialized."""

    bucket = settings.minio_bucket
    if bucket is None:
        return False
    store = store_factory(settings)
    if store is None:
        return False
    try:
        async with asyncio.timeout(settings.health_check_timeout_seconds):
            return await store.bucket_exists(bucket=bucket)
    except (FileObjectStoreError, TimeoutError):
        return False


def check_required_dependencies(settings: Settings) -> tuple[str, ...]:
    """Run the same PostgreSQL and Redis probes used by API readiness."""

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        return runner.run(unavailable_required_dependencies(settings))


def migration_check_command(config_path: Path = DEFAULT_ALEMBIC_CONFIG) -> tuple[str, ...]:
    """Build the read-only Alembic head check used before child startup."""

    return (
        sys.executable,
        "-m",
        "alembic",
        "-c",
        str(config_path),
        "current",
        "--check-heads",
    )


def migrations_are_current(config_path: Path = DEFAULT_ALEMBIC_CONFIG) -> bool:
    """Return whether the configured database is already at every code head."""

    if not config_path.is_file():
        _write_line(
            "[backend-dev] Alembic config was not found; run this command from the repository root"
        )
        return False
    # sys.executable and the repository-owned Alembic config are passed directly,
    # without a shell or user-provided command string.
    completed = subprocess.run(  # noqa: S603
        migration_check_command(config_path),
        check=False,
    )
    return completed.returncode == 0


def run_backend_dev(
    *,
    settings: Settings | None = None,
    dependency_checker: DependencyChecker = check_required_dependencies,
    migration_checker: MigrationChecker = migrations_are_current,
    supervisor: Supervisor = supervise_backend_processes,
    platform_name: str | None = None,
) -> int:
    """Validate local prerequisites, then supervise the complete backend stack."""

    resolved_settings = settings or get_settings()
    unavailable = dependency_checker(resolved_settings)
    if unavailable:
        names = ", ".join(unavailable)
        _write_line(f"[backend-dev] required dependencies are unavailable: {names}")
        _write_line(f"[backend-dev] start them with: {DEPENDENCY_START_COMMAND}")
        _write_line(f"[backend-dev] initialize object storage with: {OBJECT_STORAGE_INIT_COMMAND}")
        return 2

    if not migration_checker():
        _write_line("[backend-dev] the database is not at the current Alembic head")
        _write_line(f"[backend-dev] upgrade it with: {MIGRATION_UPGRADE_COMMAND}")
        return 2

    return supervisor(BACKEND_PROCESS_SPECS, platform_name or sys.platform)


def main() -> None:
    """Console-script entry point for the complete local backend stack."""

    raise SystemExit(run_backend_dev())


if __name__ == "__main__":
    main()
