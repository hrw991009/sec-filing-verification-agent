"""Canonical ASGI server entry point."""

import asyncio
import sys
from typing import Final

import uvicorn

APPLICATION_IMPORT: Final = "industry_platform.main:app"
SELECTOR_LOOP_FACTORY_IMPORT: Final = "industry_platform.server:create_selector_event_loop"


def create_selector_event_loop() -> asyncio.AbstractEventLoop:
    """Create an event loop compatible with psycopg on Windows."""

    return asyncio.SelectorEventLoop()


def select_event_loop_factory(platform_name: str) -> str:
    """Select a compatible Uvicorn event-loop factory for one platform."""

    if platform_name == "win32":
        return SELECTOR_LOOP_FACTORY_IMPORT

    return "auto"


def main() -> None:
    """Run the API through the project's supported server configuration."""

    uvicorn.run(
        APPLICATION_IMPORT,
        host="127.0.0.1",
        port=8000,
        loop=select_event_loop_factory(sys.platform),
    )


if __name__ == "__main__":
    main()
