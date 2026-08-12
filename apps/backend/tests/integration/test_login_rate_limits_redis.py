"""Exercise login attempt windows against the real Redis service."""

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from industry_platform.core.config import Settings
from industry_platform.core.redis_client import create_redis_client
from industry_platform.modules.identity.adapters.login_rate_limits import (
    RedisLoginAttemptRateLimiter,
)
from industry_platform.modules.identity.domain import LoginRateLimitExceededError
from industry_platform.server import create_selector_event_loop

ENV_FILE_PATH = Path(__file__).resolve().parents[4] / ".env"
REDIS_TESTS_REQUIRED = "REDIS_TESTS_REQUIRED"


def _limiter(
    client: Redis,
    settings: Settings,
    *,
    namespace: str,
    ip_max_attempts: int,
    account_max_attempts: int,
) -> RedisLoginAttemptRateLimiter:
    return RedisLoginAttemptRateLimiter(
        client,
        hmac_key=settings.login_rate_limit_hmac_key,
        ip_max_attempts=ip_max_attempts,
        ip_window_seconds=60,
        account_max_attempts=account_max_attempts,
        account_window_seconds=60,
        key_namespace=namespace,
    )


async def _delete_namespace(client: Redis, namespace: str) -> None:
    keys: list[str] = []

    async for key in client.scan_iter(match=f"{namespace}:*"):
        if not isinstance(key, str):
            raise AssertionError("Redis test client must decode keys as text")
        keys.append(key)

    if keys:
        await client.delete(*keys)


def test_real_redis_atomically_enforces_both_privacy_preserving_buckets() -> None:
    """Prove concurrency, independent dimensions, TTL, and PII-free keys."""

    if os.getenv(REDIS_TESTS_REQUIRED) != "1":
        pytest.skip(f"Set {REDIS_TESTS_REQUIRED}=1 to run Redis integration tests")

    async def exercise() -> None:
        settings = Settings(_env_file=ENV_FILE_PATH)
        client = create_redis_client(settings)
        namespaces = [f"test:login-rate:{uuid4().hex}" for _ in range(3)]

        try:
            assert await client.ping() is True

            account_limiter = _limiter(
                client,
                settings,
                namespace=namespaces[0],
                ip_max_attempts=20,
                account_max_attempts=3,
            )

            async def account_attempt(last_octet: int) -> bool:
                try:
                    await account_limiter.acquire(
                        source_ip=f"192.0.2.{last_octet}",
                        raw_email=" Target@Example.COM ",
                    )
                except LoginRateLimitExceededError:
                    return False
                return True

            account_outcomes = await asyncio.gather(
                *(account_attempt(last_octet) for last_octet in range(1, 5))
            )
            assert sum(account_outcomes) == 3

            ip_limiter = _limiter(
                client,
                settings,
                namespace=namespaces[1],
                ip_max_attempts=3,
                account_max_attempts=20,
            )

            async def ip_attempt(account_number: int) -> bool:
                try:
                    await ip_limiter.acquire(
                        source_ip="198.51.100.20",
                        raw_email=f"person-{account_number}@example.com",
                    )
                except LoginRateLimitExceededError:
                    return False
                return True

            ip_outcomes = await asyncio.gather(
                *(ip_attempt(account_number) for account_number in range(4))
            )
            assert sum(ip_outcomes) == 3

            concurrent_limiter = _limiter(
                client,
                settings,
                namespace=namespaces[2],
                ip_max_attempts=5,
                account_max_attempts=5,
            )

            async def concurrent_attempt() -> bool:
                try:
                    await concurrent_limiter.acquire(
                        source_ip="203.0.113.30",
                        raw_email="concurrent@example.com",
                    )
                except LoginRateLimitExceededError:
                    return False
                return True

            outcomes = await asyncio.gather(*(concurrent_attempt() for _ in range(12)))
            assert sum(outcomes) == 5

            concurrent_keys: list[str] = []
            async for key in client.scan_iter(match=f"{namespaces[2]}:*"):
                if not isinstance(key, str):
                    raise AssertionError("Redis test client must decode keys as text")
                concurrent_keys.append(key)

            assert len(concurrent_keys) == 2
            assert all("203.0.113.30" not in key for key in concurrent_keys)
            assert all("concurrent@example.com" not in key for key in concurrent_keys)

            for key in concurrent_keys:
                remaining_milliseconds = await client.pttl(key)
                assert 0 < remaining_milliseconds <= 60_000
        finally:
            for namespace in namespaces:
                await _delete_namespace(client, namespace)
            await client.aclose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())
