"""Run the controlled SEC browser journey against real local processes."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import ExitStack
from pathlib import Path
from typing import IO

ROOT = Path(__file__).resolve().parents[3]
RUNTIME_DIR = ROOT / "test-results" / "sec-real-runtime"
PROVIDER_ORIGIN = "http://127.0.0.1:18081"
PROVIDER_KEY = "sec-browser-controlled-key"


def _environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            "AGENT_MODEL_CONTROLLED_LOOPBACK": "true",
            "AGENT_MODEL_PROVIDER_API_KEY": PROVIDER_KEY,
            "AGENT_MODEL_PROVIDER_BASE_URL": f"{PROVIDER_ORIGIN}/v1",
            "AGENT_MODEL_ROUTE_JSON": json.dumps(
                {
                    "model": "openai-compatible/sec-browser",
                    "upstream_model": "sec-browser-model",
                    "response_models": ["sec-browser-model"],
                    "pricing_version": "sec-browser-pricing-v1",
                    "input_micro_usd_per_million": 1,
                    "cached_input_micro_usd_per_million": 1,
                    "output_micro_usd_per_million": 1,
                    "supports_image_input": False,
                },
                separators=(",", ":"),
            ),
            "APP_ENVIRONMENT": "test",
            "ELASTICSEARCH_ENDPOINT": environment.get(
                "ELASTICSEARCH_ENDPOINT", "http://127.0.0.1:19200"
            ),
            "MILVUS_ENDPOINT": environment.get("MILVUS_ENDPOINT", "http://127.0.0.1:19530"),
            "SEC_CONTROLLED_SOURCE_MANIFEST_PATH": (
                "evals/fixtures/sec/sec-browser-v1/manifest.json"
            ),
            "SEC_REAL_BROWSER_E2E": "true",
        }
    )
    return environment


def _wait_for_provider(process: subprocess.Popen[bytes], *, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Controlled Provider exited before becoming healthy")
        try:
            with urllib.request.urlopen(f"{PROVIDER_ORIGIN}/health", timeout=1) as response:  # noqa: S310
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.2)
    raise RuntimeError("Controlled Provider did not become healthy")


def _provider_state() -> dict[str, object]:
    with urllib.request.urlopen(f"{PROVIDER_ORIGIN}/state", timeout=3) as response:  # noqa: S310
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("Controlled Provider returned an invalid state")
    return value


def _start(
    command: list[str],
    *,
    environment: dict[str, str],
    output: IO[bytes],
) -> subprocess.Popen[bytes]:
    creationflags = 0
    start_new_session = True
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        start_new_session = False
    return subprocess.Popen(  # noqa: S603
        command,
        creationflags=creationflags,
        cwd=ROOT,
        env=environment,
        stderr=subprocess.STDOUT,
        stdout=output,
        start_new_session=start_new_session,
    )


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        process.terminate()
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if sys.platform == "win32":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def _require_provider_decisions(state: dict[str, object]) -> None:
    decisions = state.get("decisions")
    if not isinstance(decisions, dict):
        raise RuntimeError("Controlled Provider state has no decision counters")
    missing = [
        kind
        for kind in ("filing_search", "monitor_subscription", "final")
        if not isinstance(decisions.get(kind), int) or decisions[kind] < 1
    ]
    if missing:
        raise RuntimeError(f"Controlled Provider decisions are incomplete: {', '.join(missing)}")


def main() -> None:
    environment = _environment()
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "apps/backend/alembic.ini", "upgrade", "head"],
        check=True,
        cwd=ROOT,
        env=environment,
    )
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        raise RuntimeError("pnpm is unavailable")

    processes: list[subprocess.Popen[bytes]] = []
    try:
        with ExitStack() as stack:
            provider_log = stack.enter_context((RUNTIME_DIR / "provider.log").open("wb"))
            dispatcher_log = stack.enter_context((RUNTIME_DIR / "dispatcher.log").open("wb"))
            worker_log = stack.enter_context((RUNTIME_DIR / "worker.log").open("wb"))
            provider = _start(
                [sys.executable, "apps/backend/tests/sec_browser_provider.py"],
                environment=environment,
                output=provider_log,
            )
            processes.append(provider)
            _wait_for_provider(provider)
            processes.append(
                _start(
                    [sys.executable, "-m", "industry_platform.workers.dispatcher"],
                    environment=environment,
                    output=dispatcher_log,
                )
            )
            processes.append(
                _start(
                    [sys.executable, "-m", "industry_platform.workers.celery_app"],
                    environment=environment,
                    output=worker_log,
                )
            )
            time.sleep(2)
            stopped = [process.pid for process in processes if process.poll() is not None]
            if stopped:
                raise RuntimeError(f"SEC browser runtime process exited early: {stopped}")
            completed = subprocess.run(  # noqa: S603
                [pnpm, "exec", "playwright", "test", "--project=sec-real-journey"],
                cwd=ROOT,
                env=environment,
                check=False,
            )
            playwright_output = ROOT / "test-results" / "playwright"
            if playwright_output.exists():
                shutil.copytree(
                    playwright_output,
                    RUNTIME_DIR / "playwright",
                    dirs_exist_ok=True,
                )
            state = _provider_state()
            (RUNTIME_DIR / "provider-state.json").write_text(
                json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            source_manifest = ROOT / environment["SEC_CONTROLLED_SOURCE_MANIFEST_PATH"]
            runtime_manifest = {
                "schema_version": 1,
                "source_mode": "controlled_derivative",
                "source_manifest": source_manifest.relative_to(ROOT).as_posix(),
                "source_manifest_sha256": hashlib.sha256(source_manifest.read_bytes()).hexdigest(),
                "api_interception": False,
                "dependencies": [
                    "postgresql",
                    "redis",
                    "minio",
                    "milvus",
                    "elasticsearch",
                ],
                "processes": [
                    "api",
                    "web",
                    "outbox_dispatcher",
                    "celery_worker",
                    "controlled_http_provider",
                ],
                "provider_state": state,
            }
            (RUNTIME_DIR / "runtime-manifest.json").write_text(
                json.dumps(runtime_manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            if completed.returncode != 0:
                raise SystemExit(completed.returncode)
            _require_provider_decisions(state)
    finally:
        for process in reversed(processes):
            _stop(process)


if __name__ == "__main__":
    main()
