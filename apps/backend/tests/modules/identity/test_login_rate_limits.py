"""Tests for privacy-preserving Redis login attempt windows."""

import re
from typing import cast
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretBytes
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from industry_platform.modules.identity.adapters.login_rate_limits import (
    RedisLoginAttemptRateLimiter,
)
from industry_platform.modules.identity.domain import (
    LoginRateLimitConfigurationError,
    LoginRateLimitExceededError,
    LoginRateLimitUnavailableError,
)

RATE_KEY_VALUE = b"l" * 32
HEX_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_DEFAULT_REDIS_RESULT = object()


def redis_double(result: object = _DEFAULT_REDIS_RESULT) -> tuple[Redis, AsyncMock]:
    """Return a typed Redis double with one controlled Lua result."""

    client = AsyncMock(spec=Redis)
    client.eval = AsyncMock(return_value=[1, 0] if result is _DEFAULT_REDIS_RESULT else result)
    return cast(Redis, client), client


def limiter(client: Redis, *, namespace: str = "test:login-rate") -> RedisLoginAttemptRateLimiter:
    """Build a small deterministic limiter for unit tests."""

    return RedisLoginAttemptRateLimiter(
        client,
        hmac_key=SecretBytes(RATE_KEY_VALUE),
        ip_max_attempts=20,
        ip_window_seconds=300,
        account_max_attempts=5,
        account_window_seconds=300,
        key_namespace=namespace,
    )


def evaluated_keys(client: AsyncMock, call_index: int = -1) -> tuple[str, str]:
    """Read only the two Redis keys passed to the Lua boundary."""

    arguments = client.eval.await_args_list[call_index].args
    return cast(str, arguments[2]), cast(str, arguments[3])


@pytest.mark.asyncio
async def test_attempt_uses_independent_hmac_keys_without_storing_pii() -> None:
    redis_client, client_mock = redis_double()
    rate_limiter = limiter(redis_client)

    await rate_limiter.acquire(
        source_ip="203.0.113.42",
        raw_email=" Learner@Example.COM ",
    )

    ip_key, account_key = evaluated_keys(client_mock)
    rendered_keys = f"{ip_key} {account_key}"
    assert "203.0.113.42" not in rendered_keys
    assert "learner@example.com" not in rendered_keys.lower()
    assert "{login-rate-limit}" in ip_key
    assert "{login-rate-limit}" in account_key
    assert ":ip:" in ip_key
    assert ":account:" in account_key
    assert ip_key != account_key
    assert HEX_DIGEST_PATTERN.fullmatch(ip_key.rsplit(":", 1)[1])
    assert HEX_DIGEST_PATTERN.fullmatch(account_key.rsplit(":", 1)[1])


@pytest.mark.asyncio
async def test_canonical_equivalents_share_the_same_buckets() -> None:
    redis_client, client_mock = redis_double()
    rate_limiter = limiter(redis_client)

    await rate_limiter.acquire(
        source_ip="192.0.2.8",
        raw_email="Learner@Example.COM",
    )
    first_ip_key, first_account_key = evaluated_keys(client_mock)
    await rate_limiter.acquire(
        source_ip="::ffff:192.0.2.8",
        raw_email=" learner@example.com ",
    )
    second_ip_key, second_account_key = evaluated_keys(client_mock)

    assert first_ip_key == second_ip_key
    assert first_account_key == second_account_key


@pytest.mark.asyncio
async def test_invalid_accounts_share_one_non_bypassable_account_bucket() -> None:
    redis_client, client_mock = redis_double()
    rate_limiter = limiter(redis_client)

    await rate_limiter.acquire(source_ip="192.0.2.10", raw_email="not-an-email")
    _, first_account_key = evaluated_keys(client_mock)
    await rate_limiter.acquire(source_ip="192.0.2.11", raw_email="still invalid")
    _, second_account_key = evaluated_keys(client_mock)

    assert first_account_key == second_account_key


@pytest.mark.asyncio
async def test_limiter_returns_only_a_generic_retry_delay() -> None:
    redis_client, _client_mock = redis_double([0, 17])

    with pytest.raises(LoginRateLimitExceededError) as exc_info:
        await limiter(redis_client).acquire(
            source_ip="198.51.100.4",
            raw_email="learner@example.com",
        )

    assert exc_info.value.retry_after_seconds == 17
    assert "198.51.100.4" not in str(exc_info.value)
    assert "learner@example.com" not in str(exc_info.value)


@pytest.mark.parametrize(
    "invalid_result",
    [None, [], [1], [1, 0, 0], [True, 0], [1, 2], [0, 0], "1,0"],
)
@pytest.mark.asyncio
async def test_malformed_lua_results_fail_closed(invalid_result: object) -> None:
    redis_client, _client_mock = redis_double(invalid_result)

    with pytest.raises(LoginRateLimitUnavailableError):
        await limiter(redis_client).acquire(
            source_ip="198.51.100.7",
            raw_email="learner@example.com",
        )


@pytest.mark.asyncio
async def test_redis_and_source_failures_are_sanitized_and_fail_closed() -> None:
    sensitive_detail = "redis password and endpoint must not escape"
    redis_client, client_mock = redis_double()
    client_mock.eval.side_effect = RedisConnectionError(sensitive_detail)

    with pytest.raises(LoginRateLimitUnavailableError) as redis_exc:
        await limiter(redis_client).acquire(
            source_ip="198.51.100.9",
            raw_email="learner@example.com",
        )

    with pytest.raises(LoginRateLimitUnavailableError) as source_exc:
        await limiter(redis_client).acquire(
            source_ip="not-an-ip-address",
            raw_email="learner@example.com",
        )

    assert sensitive_detail not in str(redis_exc.value)
    assert "not-an-ip-address" not in str(source_exc.value)


@pytest.mark.parametrize(
    ("key_value", "ip_attempts", "namespace"),
    [
        (b"short", 20, "test:login-rate"),
        (RATE_KEY_VALUE, 0, "test:login-rate"),
        (RATE_KEY_VALUE, 20, "invalid namespace with spaces"),
    ],
)
def test_constructor_rejects_unsafe_configuration(
    key_value: bytes,
    ip_attempts: int,
    namespace: str,
) -> None:
    redis_client, _client_mock = redis_double()

    with pytest.raises(LoginRateLimitConfigurationError) as exc_info:
        RedisLoginAttemptRateLimiter(
            redis_client,
            hmac_key=SecretBytes(key_value),
            ip_max_attempts=ip_attempts,
            ip_window_seconds=300,
            account_max_attempts=5,
            account_window_seconds=300,
            key_namespace=namespace,
        )

    assert key_value.hex() not in str(exc_info.value)
