"""Frozen and live SEC EDGAR filer-catalog adapters."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Final, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

import httpx2
from redis.exceptions import RedisError

from industry_platform.modules.disclosures.domain import (
    SEC_COMPANY_TICKERS_SOURCE_KIND,
    SEC_COMPANY_TICKERS_URL,
    SEC_DEFAULT_REQUESTS_PER_SECOND,
    SEC_MAX_CATALOG_RESPONSE_BYTES,
    SEC_MAX_SUBMISSIONS_RESPONSE_BYTES,
    SEC_SUBMISSIONS_URL_PREFIX,
    FilingSelectionScope,
    SecAliasKind,
    SecFiler,
    SecFilerAlias,
    SecFilerCatalogSnapshot,
    SecSourceError,
    SecSourceErrorCode,
    SecSubmissionSet,
    catalog_source_version,
    normalize_cik,
    normalize_filer_name,
    normalize_ticker,
    sha256_hex,
)

_CACHE_KEY: Final = "iip:sec:company-tickers:v1"
_RATE_KEY: Final = "iip:sec:request-budget:v1"
_CACHE_RETENTION_SECONDS: Final = 7 * 24 * 60 * 60
_RATE_ACQUIRE_TIMEOUT_SECONDS: Final = 5.0
_CACHE_KEY_PATTERN: Final = re.compile(r"^[a-z0-9:._-]{1,240}$")
_SUBMISSIONS_PATH_PATTERN: Final = re.compile(
    r"^/submissions/CIK[0-9]{10}(?:-submissions-[0-9]{3})?\.json$"
)
_RATE_LIMIT_SCRIPT: Final = """
local now_parts = redis.call('TIME')
local now_us = tonumber(now_parts[1]) * 1000000 + tonumber(now_parts[2])
local window_start = now_us - 1000000
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', window_start)
local count = redis.call('ZCARD', KEYS[1])
if count < tonumber(ARGV[1]) then
  redis.call('ZADD', KEYS[1], now_us, ARGV[2])
  redis.call('PEXPIRE', KEYS[1], 2000)
  return {1, 0}
end
local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
local wait_us = 1000000
if oldest[2] ~= nil then
  wait_us = math.max(1000, 1000000 - (now_us - tonumber(oldest[2])))
end
return {0, wait_us}
"""

type Sleep = Callable[[float], Awaitable[None]]


class SecRequestBudget(Protocol):
    async def acquire(self) -> None: ...


class SecResponseCache(Protocol):
    async def get(self) -> CachedSecResponse | None: ...

    async def put(self, value: CachedSecResponse) -> None: ...


class SecRedisClient(Protocol):
    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> object: ...

    async def get(self, name: str) -> str | None: ...

    async def set(self, name: str, value: str, *, ex: int) -> bool | None: ...


@dataclass(frozen=True, slots=True)
class CachedSecResponse:
    body: bytes
    retrieved_at: datetime
    fresh_until: datetime
    source_available_at: datetime | None = None
    etag: str | None = None
    last_modified: str | None = None

    def __post_init__(self) -> None:
        if not self.body or len(self.body) > SEC_MAX_SUBMISSIONS_RESPONSE_BYTES:
            raise ValueError("Cached SEC response body is invalid")
        for value in (self.retrieved_at, self.fresh_until):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("Cached SEC response timestamp is invalid")
        if self.fresh_until <= self.retrieved_at:
            raise ValueError("Cached SEC response freshness is invalid")
        source_available_at = self.source_available_at or self.retrieved_at
        if (
            source_available_at.tzinfo is None
            or source_available_at.utcoffset() is None
            or source_available_at > self.retrieved_at
        ):
            raise ValueError("Cached SEC response availability is invalid")
        object.__setattr__(self, "source_available_at", source_available_at)


class RedisSecRequestBudget:
    """One Redis-time sliding window shared by API and Worker processes."""

    def __init__(
        self,
        redis: SecRedisClient,
        *,
        requests_per_second: int = SEC_DEFAULT_REQUESTS_PER_SECOND,
        sleep: Sleep = asyncio.sleep,
        key_namespace: str = _RATE_KEY,
    ) -> None:
        if not 1 <= requests_per_second <= 9:
            raise ValueError("SEC request rate must remain below the official limit")
        if not re.fullmatch(r"[a-z0-9:._-]{1,200}", key_namespace):
            raise ValueError("SEC request budget namespace is invalid")
        self._redis = redis
        self._requests_per_second = requests_per_second
        self._sleep = sleep
        self._key_namespace = key_namespace

    async def acquire(self) -> None:
        try:
            async with asyncio.timeout(_RATE_ACQUIRE_TIMEOUT_SECONDS):
                while True:
                    raw = await self._redis.eval(
                        _RATE_LIMIT_SCRIPT,
                        1,
                        self._key_namespace,
                        str(self._requests_per_second),
                        uuid4().hex,
                    )
                    if (
                        not isinstance(raw, (list, tuple))
                        or len(raw) != 2
                        or isinstance(raw[0], bool)
                        or not isinstance(raw[0], int)
                        or isinstance(raw[1], bool)
                        or not isinstance(raw[1], int)
                    ):
                        raise SecSourceError(
                            SecSourceErrorCode.RATE_LIMIT_UNAVAILABLE,
                            retryable=True,
                        )
                    if raw[0] == 1:
                        return
                    await self._sleep(min(max(raw[1] / 1_000_000, 0.001), 1.0))
        except SecSourceError:
            raise
        except (TimeoutError, RedisError):
            raise SecSourceError(
                SecSourceErrorCode.RATE_LIMIT_UNAVAILABLE,
                retryable=True,
            ) from None


class RedisSecResponseCache:
    """Retain one bounded raw response and validators for conditional requests."""

    def __init__(self, redis: SecRedisClient, *, cache_key: str = _CACHE_KEY) -> None:
        if _CACHE_KEY_PATTERN.fullmatch(cache_key) is None:
            raise ValueError("SEC response cache key is invalid")
        self._redis = redis
        self._cache_key = cache_key

    async def get(self) -> CachedSecResponse | None:
        try:
            raw = await self._redis.get(self._cache_key)
            if raw is None:
                return None
            document = json.loads(raw)
            if not isinstance(document, dict):
                raise ValueError
            body_text = document.get("body_b64")
            retrieved_at = document.get("retrieved_at")
            fresh_until = document.get("fresh_until")
            if (
                not isinstance(body_text, str)
                or not isinstance(retrieved_at, str)
                or not isinstance(fresh_until, str)
            ):
                raise ValueError
            etag = document.get("etag")
            last_modified = document.get("last_modified")
            if etag is not None and not isinstance(etag, str):
                raise ValueError
            if last_modified is not None and not isinstance(last_modified, str):
                raise ValueError
            return CachedSecResponse(
                body=base64.b64decode(body_text, validate=True),
                retrieved_at=_parse_cached_datetime(retrieved_at),
                fresh_until=_parse_cached_datetime(fresh_until),
                source_available_at=(
                    _parse_cached_datetime(document["source_available_at"])
                    if isinstance(document.get("source_available_at"), str)
                    else None
                ),
                etag=etag,
                last_modified=last_modified,
            )
        except (RedisError, ValueError, TypeError, json.JSONDecodeError, binascii.Error):
            raise SecSourceError(
                SecSourceErrorCode.CACHE_UNAVAILABLE,
                retryable=True,
            ) from None

    async def put(self, value: CachedSecResponse) -> None:
        document = json.dumps(
            {
                "body_b64": base64.b64encode(value.body).decode("ascii"),
                "retrieved_at": value.retrieved_at.isoformat(),
                "fresh_until": value.fresh_until.isoformat(),
                "source_available_at": value.source_available_at.isoformat()
                if value.source_available_at is not None
                else None,
                "etag": value.etag,
                "last_modified": value.last_modified,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        try:
            stored = await self._redis.set(
                self._cache_key,
                document,
                ex=_CACHE_RETENTION_SECONDS,
            )
            if stored is not True:
                raise SecSourceError(
                    SecSourceErrorCode.CACHE_UNAVAILABLE,
                    retryable=True,
                )
        except SecSourceError:
            raise
        except RedisError:
            raise SecSourceError(
                SecSourceErrorCode.CACHE_UNAVAILABLE,
                retryable=True,
            ) from None


class OfficialSecJsonClient:
    """One allowlisted, bounded JSON client shared by official SEC adapters."""

    def __init__(
        self,
        client: httpx2.AsyncClient,
        budget: SecRequestBudget,
        *,
        user_agent: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Sleep = asyncio.sleep,
        timeout_seconds: float = 20.0,
        maximum_attempts: int = 3,
    ) -> None:
        if (
            not user_agent.strip()
            or "@" not in user_agent
            or any(character in user_agent for character in "\r\n")
        ):
            raise ValueError("SEC User-Agent must identify the application and contact email")
        if not 0 < timeout_seconds <= 60 or not 1 <= maximum_attempts <= 5:
            raise ValueError("SEC request policy is invalid")
        self._client = client
        self._budget = budget
        self._user_agent = user_agent
        self._clock = clock
        self._sleep = sleep
        self._timeout_seconds = timeout_seconds
        self._maximum_attempts = maximum_attempts

    async def fetch(
        self,
        url: str,
        cache: SecResponseCache,
        *,
        cache_ttl_seconds: int,
        maximum_bytes: int,
    ) -> CachedSecResponse:
        _validate_official_json_url(url)
        if not 60 <= cache_ttl_seconds <= 86_400:
            raise ValueError("SEC cache TTL is invalid")
        if not 1 <= maximum_bytes <= SEC_MAX_SUBMISSIONS_RESPONSE_BYTES:
            raise ValueError("SEC response budget is invalid")
        now = _utc_now(self._clock)
        cached = await cache.get()
        if cached is not None and len(cached.body) > maximum_bytes:
            raise SecSourceError(SecSourceErrorCode.RESPONSE_TOO_LARGE, retryable=False)
        if cached is not None and cached.fresh_until > now:
            return cached

        last_error: SecSourceError | None = None
        for attempt in range(self._maximum_attempts):
            try:
                response = await self._request(
                    url,
                    cached,
                    cache_ttl_seconds=cache_ttl_seconds,
                    maximum_bytes=maximum_bytes,
                )
                await cache.put(response)
                return response
            except SecSourceError as error:
                last_error = error
                if not error.retryable or attempt + 1 >= self._maximum_attempts:
                    raise
                delay = error.retry_after_seconds
                if delay is None:
                    delay = min(0.25 * (2**attempt), 2.0)
                await self._sleep(delay)
        if last_error is None:
            raise AssertionError("SEC retry loop terminated without an outcome")
        raise last_error

    async def _request(
        self,
        url: str,
        cached: CachedSecResponse | None,
        *,
        cache_ttl_seconds: int,
        maximum_bytes: int,
    ) -> CachedSecResponse:
        await self._budget.acquire()
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "User-Agent": self._user_agent,
        }
        if cached is not None:
            if cached.etag is not None:
                headers["If-None-Match"] = cached.etag
            if cached.last_modified is not None:
                headers["If-Modified-Since"] = cached.last_modified
        try:
            async with asyncio.timeout(self._timeout_seconds):
                async with self._client.stream(
                    "GET",
                    url,
                    headers=headers,
                    follow_redirects=False,
                    timeout=self._timeout_seconds,
                ) as response:
                    now = _utc_now(self._clock)
                    if response.status_code == 304:
                        if cached is None:
                            raise SecSourceError(
                                SecSourceErrorCode.RESPONSE_INVALID,
                                retryable=False,
                            )
                        return CachedSecResponse(
                            body=cached.body,
                            retrieved_at=now,
                            fresh_until=now + timedelta(seconds=cache_ttl_seconds),
                            source_available_at=cached.source_available_at,
                            etag=response.headers.get("etag") or cached.etag,
                            last_modified=(
                                response.headers.get("last-modified") or cached.last_modified
                            ),
                        )
                    if 300 <= response.status_code < 400:
                        raise SecSourceError(
                            SecSourceErrorCode.REDIRECT_REJECTED,
                            retryable=False,
                        )
                    if response.status_code == 429:
                        raise SecSourceError(
                            SecSourceErrorCode.RATE_LIMITED,
                            retryable=True,
                            retry_after_seconds=_retry_after(response.headers.get("retry-after")),
                        )
                    if response.status_code >= 500:
                        raise SecSourceError(
                            SecSourceErrorCode.UPSTREAM_ERROR,
                            retryable=True,
                        )
                    if response.status_code >= 400:
                        raise SecSourceError(
                            SecSourceErrorCode.RESPONSE_INVALID,
                            retryable=False,
                        )
                    content_type = response.headers.get("content-type", "").partition(";")[0]
                    if content_type.strip().lower() != "application/json":
                        raise SecSourceError(
                            SecSourceErrorCode.CONTENT_TYPE_INVALID,
                            retryable=False,
                        )
                    content_length = response.headers.get("content-length")
                    if content_length is not None:
                        try:
                            declared_length = int(content_length)
                        except ValueError:
                            raise SecSourceError(
                                SecSourceErrorCode.RESPONSE_INVALID,
                                retryable=False,
                            ) from None
                        if not 0 <= declared_length <= maximum_bytes:
                            raise SecSourceError(
                                SecSourceErrorCode.RESPONSE_TOO_LARGE,
                                retryable=False,
                            )
                    chunks: list[bytes] = []
                    observed_bytes = 0
                    async for chunk in response.aiter_bytes():
                        observed_bytes += len(chunk)
                        if observed_bytes > maximum_bytes:
                            raise SecSourceError(
                                SecSourceErrorCode.RESPONSE_TOO_LARGE,
                                retryable=False,
                            )
                        chunks.append(chunk)
                    body = b"".join(chunks)
                    if not body:
                        raise SecSourceError(
                            SecSourceErrorCode.RESPONSE_INVALID,
                            retryable=False,
                        )
                    last_modified = response.headers.get("last-modified")
                    return CachedSecResponse(
                        body=body,
                        retrieved_at=now,
                        fresh_until=now + timedelta(seconds=cache_ttl_seconds),
                        source_available_at=_source_available_at(last_modified, retrieved_at=now),
                        etag=response.headers.get("etag"),
                        last_modified=last_modified,
                    )
        except SecSourceError:
            raise
        except (TimeoutError, httpx2.TimeoutException):
            raise SecSourceError(SecSourceErrorCode.TIMEOUT, retryable=True) from None
        except (httpx2.RequestError, httpx2.InvalidURL):
            raise SecSourceError(SecSourceErrorCode.UPSTREAM_ERROR, retryable=True) from None


class FrozenSecEdgarAdapter:
    """Return one already-validated source snapshot for deterministic replay."""

    def __init__(self, snapshot: SecFilerCatalogSnapshot) -> None:
        self._snapshot = snapshot

    async def fetch_filer_catalog(self) -> SecFilerCatalogSnapshot:
        return self._snapshot


class UnavailableSecEdgarAdapter:
    """Keep missing User-Agent configuration explicit and fail closed."""

    async def fetch_filer_catalog(self) -> SecFilerCatalogSnapshot:
        raise SecSourceError(SecSourceErrorCode.NOT_CONFIGURED, retryable=False)

    async def fetch_submission_set(self, scope: FilingSelectionScope) -> SecSubmissionSet:
        del scope
        raise SecSourceError(SecSourceErrorCode.NOT_CONFIGURED, retryable=False)


class LiveSecEdgarAdapter:
    """Bounded official company-ticker reader with shared budget and cache."""

    def __init__(
        self,
        client: httpx2.AsyncClient,
        budget: SecRequestBudget,
        cache: SecResponseCache,
        *,
        user_agent: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Sleep = asyncio.sleep,
        cache_ttl_seconds: int = 3_600,
        timeout_seconds: float = 20.0,
        maximum_attempts: int = 3,
    ) -> None:
        self._json_client = OfficialSecJsonClient(
            client,
            budget,
            user_agent=user_agent,
            clock=clock,
            sleep=sleep,
            timeout_seconds=timeout_seconds,
            maximum_attempts=maximum_attempts,
        )
        self._cache = cache
        self._cache_ttl_seconds = cache_ttl_seconds

    async def fetch_filer_catalog(self) -> SecFilerCatalogSnapshot:
        response = await self._json_client.fetch(
            SEC_COMPANY_TICKERS_URL,
            self._cache,
            cache_ttl_seconds=self._cache_ttl_seconds,
            maximum_bytes=SEC_MAX_CATALOG_RESPONSE_BYTES,
        )
        return _parse_catalog(response.body, retrieved_at=response.retrieved_at)


def _parse_catalog(body: bytes, *, retrieved_at: datetime) -> SecFilerCatalogSnapshot:
    try:
        document = json.loads(
            body.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_json_object,
        )
        if not isinstance(document, dict):
            raise ValueError
        content_sha256 = sha256_hex(body)
        source_version = catalog_source_version(content_sha256)
        grouped: dict[str, tuple[str, set[str]]] = {}
        for key, raw_item in document.items():
            if not isinstance(key, str) or not key.isdigit() or not isinstance(raw_item, dict):
                raise ValueError
            cik = normalize_cik(_required_int(raw_item.get("cik_str")))
            title = _required_text(raw_item.get("title"), maximum=500)
            ticker = normalize_ticker(_required_text(raw_item.get("ticker"), maximum=20))
            existing = grouped.get(cik)
            if existing is None:
                grouped[cik] = (title, {ticker})
            else:
                existing_title, tickers = existing
                if normalize_filer_name(existing_title) != normalize_filer_name(title):
                    raise ValueError
                tickers.add(ticker)
        if not grouped:
            raise ValueError
        filers = tuple(
            _filer_from_group(
                cik,
                title,
                tickers,
                source_version=source_version,
                content_sha256=content_sha256,
                observed_at=retrieved_at,
            )
            for cik, (title, tickers) in sorted(grouped.items())
        )
        return SecFilerCatalogSnapshot(
            source_kind=SEC_COMPANY_TICKERS_SOURCE_KIND,
            source_version=source_version,
            source_url=SEC_COMPANY_TICKERS_URL,
            content_sha256=content_sha256,
            retrieved_at=retrieved_at,
            filers=filers,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise SecSourceError(SecSourceErrorCode.RESPONSE_INVALID, retryable=False) from None


def _filer_from_group(
    cik: str,
    title: str,
    tickers: set[str],
    *,
    source_version: str,
    content_sha256: str,
    observed_at: datetime,
) -> SecFiler:
    aliases = [
        SecFilerAlias(
            kind=SecAliasKind.NAME,
            display_value=title,
            normalized_value=normalize_filer_name(title),
            source_kind=SEC_COMPANY_TICKERS_SOURCE_KIND,
            source_version=source_version,
            source_url=SEC_COMPANY_TICKERS_URL,
            content_sha256=content_sha256,
            observed_at=observed_at,
        )
    ]
    aliases.extend(
        SecFilerAlias(
            kind=SecAliasKind.TICKER,
            display_value=ticker,
            normalized_value=ticker,
            source_kind=SEC_COMPANY_TICKERS_SOURCE_KIND,
            source_version=source_version,
            source_url=SEC_COMPANY_TICKERS_URL,
            content_sha256=content_sha256,
            observed_at=observed_at,
        )
        for ticker in sorted(tickers)
    )
    return SecFiler(
        cik=cik,
        canonical_name=title,
        normalized_name=normalize_filer_name(title),
        aliases=tuple(aliases),
        source_kind=SEC_COMPANY_TICKERS_SOURCE_KIND,
        source_version=source_version,
        source_url=SEC_COMPANY_TICKERS_URL,
        content_sha256=content_sha256,
        observed_at=observed_at,
    )


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _required_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("SEC integer field is invalid")
    return value


def _required_text(value: object, *, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError("SEC text field is invalid")
    return " ".join(value.split())


def _retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if 0 <= seconds <= 60 else None


def _utc_now(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("SEC Adapter clock must return an aware datetime")
    return value.astimezone(UTC)


def _parse_cached_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Cached SEC response timestamp is invalid")
    return parsed.astimezone(UTC)


def _validate_official_json_url(url: str) -> None:
    parsed = urlsplit(url)
    allowed = (
        parsed.scheme == "https"
        and parsed.username is None
        and parsed.password is None
        and parsed.port is None
        and not parsed.query
        and not parsed.fragment
        and (
            (parsed.hostname == "www.sec.gov" and url == SEC_COMPANY_TICKERS_URL)
            or (
                parsed.hostname == "data.sec.gov"
                and url.startswith(SEC_SUBMISSIONS_URL_PREFIX)
                and _SUBMISSIONS_PATH_PATTERN.fullmatch(parsed.path) is not None
            )
        )
    )
    if not allowed:
        raise SecSourceError(SecSourceErrorCode.REDIRECT_REJECTED, retryable=False)


def _source_available_at(value: str | None, *, retrieved_at: datetime) -> datetime:
    if value is None:
        return retrieved_at
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        raise SecSourceError(SecSourceErrorCode.RESPONSE_INVALID, retryable=False) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SecSourceError(SecSourceErrorCode.RESPONSE_INVALID, retryable=False)
    normalized = parsed.astimezone(UTC)
    if normalized > retrieved_at:
        raise SecSourceError(SecSourceErrorCode.RESPONSE_INVALID, retryable=False)
    return normalized
