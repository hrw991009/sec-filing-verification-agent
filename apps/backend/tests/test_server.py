"""Tests for platform-aware API server startup."""

import asyncio

import pytest
from uvicorn import Config

from industry_platform.server import (
    APPLICATION_IMPORT,
    SELECTOR_LOOP_FACTORY_IMPORT,
    create_selector_event_loop,
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
    )

    assert config.get_loop_factory() is create_selector_event_loop
