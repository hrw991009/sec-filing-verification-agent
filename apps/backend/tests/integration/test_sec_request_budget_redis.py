"""Prove the SEC request budget is atomic across concurrent Redis clients."""

import asyncio
import os
from pathlib import Path
from time import monotonic
from typing import cast
from uuid import uuid4

import pytest

from industry_platform.core.config import Settings
from industry_platform.core.redis_client import create_redis_client
from industry_platform.modules.disclosures.adapters.sec_edgar import (
    RedisSecRequestBudget,
    SecRedisClient,
)
from industry_platform.server import create_selector_event_loop

ENV_FILE_PATH = Path(__file__).resolve().parents[4] / ".env"
REDIS_TESTS_REQUIRED = "REDIS_TESTS_REQUIRED"


def test_real_redis_serializes_concurrent_sec_requests_below_the_configured_limit() -> None:
    if os.getenv(REDIS_TESTS_REQUIRED) != "1":
        pytest.skip(f"Set {REDIS_TESTS_REQUIRED}=1 to run Redis integration tests")

    async def exercise() -> None:
        settings = Settings(_env_file=ENV_FILE_PATH)
        clients = [create_redis_client(settings), create_redis_client(settings)]
        namespace = f"test:sec-rate:{uuid4().hex}"
        budgets = [
            RedisSecRequestBudget(
                cast(SecRedisClient, client),
                requests_per_second=3,
                key_namespace=namespace,
            )
            for client in clients
        ]
        started_at = monotonic()

        async def acquire(index: int) -> float:
            await budgets[index % len(budgets)].acquire()
            return monotonic() - started_at

        try:
            completions = sorted(await asyncio.gather(*(acquire(index) for index in range(5))))
            assert completions[2] < 0.5
            assert completions[3] >= 0.8
            assert all(completion < 2.5 for completion in completions)
        finally:
            await clients[0].delete(namespace)
            await asyncio.gather(*(client.aclose() for client in clients))

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())
