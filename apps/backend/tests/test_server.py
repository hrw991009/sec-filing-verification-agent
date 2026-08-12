"""Tests for platform-aware API server startup."""

import asyncio
import sys
from unittest.mock import Mock

import pytest
import uvicorn
from uvicorn import Config

from industry_platform.server import (
    APPLICATION_IMPORT,
    SELECTOR_LOOP_FACTORY_IMPORT,
    create_selector_event_loop,
    main,
    select_event_loop_factory,
)


def test_windows_selects_the_psycopg_compatible_loop() -> None:
    assert select_event_loop_factory("win32") == SELECTOR_LOOP_FACTORY_IMPORT


@pytest.mark.parametrize("platform_name", ["linux", "darwin"])
def test_non_windows_platforms_keep_uvicorn_auto_selection(
    platform_name: str,
) -> None:
    assert select_event_loop_factory(platform_name) == "auto"


def test_selector_factory_creates_a_selector_event_loop() -> None:
    event_loop = create_selector_event_loop()

    try:
        assert isinstance(event_loop, asyncio.SelectorEventLoop)
    finally:
        event_loop.close()


def test_uvicorn_can_resolve_the_project_loop_factory() -> None:
    config = Config(
        APPLICATION_IMPORT,
        loop=SELECTOR_LOOP_FACTORY_IMPORT,
        factory=True,
    )

    assert config.factory is True
    assert config.get_loop_factory() is create_selector_event_loop


def test_server_starts_uvicorn_with_the_application_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = Mock()
    monkeypatch.setattr(uvicorn, "run", run)

    main()

    run.assert_called_once_with(
        APPLICATION_IMPORT,
        host="127.0.0.1",
        port=8000,
        loop=select_event_loop_factory(sys.platform),
        factory=True,
    )
