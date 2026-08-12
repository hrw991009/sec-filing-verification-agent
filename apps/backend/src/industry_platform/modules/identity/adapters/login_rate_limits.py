"""Privacy-preserving Redis sliding windows for login attempts."""

import hmac
import re
from collections.abc import Awaitable
from ipaddress import IPv6Address, ip_address
from secrets import token_hex
from typing import cast

from pydantic import SecretBytes
from redis.asyncio import Redis
from redis.exceptions import RedisError

from industry_platform.modules.identity.domain import (
    InvalidEmailAddressError,
    LoginRateLimitConfigurationError,
    LoginRateLimitExceededError,
    LoginRateLimitUnavailableError,
)
from industry_platform.modules.identity.emails import normalize_email_address

RATE_LIMIT_KEY_BYTES = 32
MAX_ATTEMPTS = 1_000
MAX_WINDOW_SECONDS = 86_400

_NAMESPACE_PATTERN = re.compile(r"^[A-Za-z0-9:._-]{1,100}$")
_IP_DIGEST_DOMAIN = b"iip.identity.login-rate-limit.ip.v1\x00"
_ACCOUNT_DIGEST_DOMAIN = b"iip.identity.login-rate-limit.account.v1\x00"
_INVALID_ACCOUNT_VALUE = b"invalid-account"

_SLIDING_WINDOW_SCRIPT = """
local now_parts = redis.call('TIME')
local now_ms = (tonumber(now_parts[1]) * 1000) + math.floor(tonumber(now_parts[2]) / 1000)

local ip_limit = tonumber(ARGV[1])
local ip_window_ms = tonumber(ARGV[2])
local account_limit = tonumber(ARGV[3])
local account_window_ms = tonumber(ARGV[4])

redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now_ms - ip_window_ms)
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now_ms - account_window_ms)

local ip_count = redis.call('ZCARD', KEYS[1])
local account_count = redis.call('ZCARD', KEYS[2])
local retry_after_seconds = 0

local function update_retry_after(key, window_ms)
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    if #oldest == 2 then
        local remaining_ms = tonumber(oldest[2]) + window_ms - now_ms
        retry_after_seconds = math.max(
            retry_after_seconds,
            math.max(1, math.ceil(remaining_ms / 1000))
        )
    end
end

if ip_count >= ip_limit then
    update_retry_after(KEYS[1], ip_window_ms)
end

if account_count >= account_limit then
    update_retry_after(KEYS[2], account_window_ms)
end

if retry_after_seconds > 0 then
    return {0, retry_after_seconds}
end

local member = tostring(now_ms) .. ':' .. ARGV[5]
redis.call('ZADD', KEYS[1], now_ms, member)
redis.call('ZADD', KEYS[2], now_ms, member)
redis.call('PEXPIRE', KEYS[1], ip_window_ms)
redis.call('PEXPIRE', KEYS[2], account_window_ms)

return {1, 0}
"""


class RedisLoginAttemptRateLimiter:
    """Atomically enforce independent per-IP and per-account attempt limits."""

    def __init__(
        self,
        client: Redis,
        *,
        hmac_key: SecretBytes,
        ip_max_attempts: int,
        ip_window_seconds: int,
        account_max_attempts: int,
        account_window_seconds: int,
        key_namespace: str = "iip:login-rate-limit:v1",
    ) -> None:
        key_value = hmac_key.get_secret_value()
        attempt_values = (ip_max_attempts, account_max_attempts)
        window_values = (ip_window_seconds, account_window_seconds)

        if (
            len(key_value) != RATE_LIMIT_KEY_BYTES
            or any(
                isinstance(value, bool) or not 1 <= value <= MAX_ATTEMPTS
                for value in attempt_values
            )
            or any(
                isinstance(value, bool) or not 1 <= value <= MAX_WINDOW_SECONDS
                for value in window_values
            )
            or not _NAMESPACE_PATTERN.fullmatch(key_namespace)
        ):
            raise LoginRateLimitConfigurationError

        self._client = client
        self._hmac_key = key_value
        self._ip_max_attempts = ip_max_attempts
        self._ip_window_milliseconds = ip_window_seconds * 1_000
        self._account_max_attempts = account_max_attempts
        self._account_window_milliseconds = account_window_seconds * 1_000
        self._key_namespace = key_namespace

    async def acquire(self, *, source_ip: str, raw_email: str) -> None:
        """Consume one attempt in both windows, or fail without credential work."""

        ip_key = self._key_for_ip(source_ip)
        account_key = self._key_for_account(raw_email)

        try:
            result = await cast(
                Awaitable[object],
                self._client.eval(
                    _SLIDING_WINDOW_SCRIPT,
                    2,
                    ip_key,
                    account_key,
                    str(self._ip_max_attempts),
                    str(self._ip_window_milliseconds),
                    str(self._account_max_attempts),
                    str(self._account_window_milliseconds),
                    token_hex(16),
                ),
            )
        except RedisError:
            raise LoginRateLimitUnavailableError from None

        if (
            not isinstance(result, list)
            or len(result) != 2
            or any(not isinstance(value, int) or isinstance(value, bool) for value in result)
        ):
            raise LoginRateLimitUnavailableError

        allowed, retry_after_seconds = result

        if allowed == 1 and retry_after_seconds == 0:
            return

        if allowed == 0 and retry_after_seconds >= 1:
            raise LoginRateLimitExceededError(
                retry_after_seconds=retry_after_seconds,
            )

        raise LoginRateLimitUnavailableError

    def _key_for_ip(self, source_ip: str) -> str:
        try:
            address = ip_address(source_ip)
        except ValueError:
            raise LoginRateLimitUnavailableError from None

        address_bytes = (
            address.ipv4_mapped.packed
            if isinstance(address, IPv6Address) and address.ipv4_mapped is not None
            else address.packed
        )

        digest = self._digest(_IP_DIGEST_DOMAIN, address_bytes)
        return f"{self._key_namespace}:{{login-rate-limit}}:ip:{digest}"

    def _key_for_account(self, raw_email: str) -> str:
        try:
            account_value = str(normalize_email_address(raw_email)).encode("ascii")
        except InvalidEmailAddressError:
            account_value = _INVALID_ACCOUNT_VALUE

        digest = self._digest(_ACCOUNT_DIGEST_DOMAIN, account_value)
        return f"{self._key_namespace}:{{login-rate-limit}}:account:{digest}"

    def _digest(self, domain: bytes, value: bytes) -> str:
        return hmac.digest(self._hmac_key, domain + value, "sha256").hex()
