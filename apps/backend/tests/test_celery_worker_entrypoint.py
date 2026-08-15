"""CLI contracts for the standalone Celery worker entry point."""

from __future__ import annotations

import sys
from collections.abc import Sequence

import pytest

import industry_platform.workers.celery_app as celery_module
from industry_platform.core.config import Settings
from industry_platform.workers.celery_app import build_worker_cli_args


@pytest.mark.parametrize(
    "arguments",
    [
        ("-P", "prefork", "-c", "4"),
        ("-Pprefork", "-c4"),
        ("--pool", "prefork", "--concurrency", "4"),
        ("--pool=prefork", "--concurrency=4"),
    ],
)
def test_windows_worker_preserves_explicit_pool_and_concurrency(
    arguments: tuple[str, ...],
) -> None:
    assert build_worker_cli_args(arguments, "win32") == [
        "worker",
        "--pool=solo",
        "--concurrency=1",
        *arguments,
    ]


def test_windows_worker_prepends_safe_defaults_before_user_options() -> None:
    assert build_worker_cli_args(("--loglevel=INFO",), "win32") == [
        "worker",
        "--pool=solo",
        "--concurrency=1",
        "--loglevel=INFO",
    ]
    assert build_worker_cli_args(("--pool=threads",), "win32") == [
        "worker",
        "--pool=solo",
        "--concurrency=1",
        "--pool=threads",
    ]
    assert build_worker_cli_args(("--concurrency=4",), "win32") == [
        "worker",
        "--pool=solo",
        "--concurrency=1",
        "--concurrency=4",
    ]


def test_windows_worker_does_not_mistake_option_values_for_short_options() -> None:
    arguments = ("--logfile", "-Process.log", "--hostname", "-consumer@%h")
    assert build_worker_cli_args(arguments, "win32") == [
        "worker",
        "--pool=solo",
        "--concurrency=1",
        *arguments,
    ]


@pytest.mark.parametrize("platform_name", ["linux", "darwin"])
def test_non_windows_worker_forwards_arguments_unchanged(platform_name: str) -> None:
    arguments = ("--loglevel=INFO", "--autoscale=4,1")
    assert build_worker_cli_args(arguments, platform_name) == ["worker", *arguments]


class FakeCeleryApp:
    def __init__(self) -> None:
        self.worker_arguments: list[str] | None = None
        self.closed = False

    def worker_main(self, arguments: Sequence[str]) -> None:
        self.worker_arguments = list(arguments)

    def close(self) -> None:
        self.closed = True


def test_main_uses_platform_cli_defaults_and_closes_app(
    monkeypatch: pytest.MonkeyPatch,
    test_settings: Settings,
) -> None:
    app = FakeCeleryApp()
    monkeypatch.setattr(celery_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(celery_module, "create_worker_celery_app", lambda _settings: app)
    monkeypatch.setattr(sys, "argv", ["worker", "--loglevel=INFO"])
    monkeypatch.setattr(sys, "platform", "win32")

    celery_module.main()

    assert app.worker_arguments == [
        "worker",
        "--pool=solo",
        "--concurrency=1",
        "--loglevel=INFO",
    ]
    assert app.closed is True
