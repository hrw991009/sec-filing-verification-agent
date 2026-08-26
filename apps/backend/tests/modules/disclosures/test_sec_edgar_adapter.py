"""Contract tests for the bounded official SEC EDGAR Adapter."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx2
import pytest

from industry_platform.modules.disclosures.adapters.sec_edgar import (
    CachedSecResponse,
    LiveSecEdgarAdapter,
    RedisSecRequestBudget,
)
from industry_platform.modules.disclosures.domain import (
    SEC_COMPANY_TICKERS_URL,
    SEC_MAX_CATALOG_RESPONSE_BYTES,
    SecSourceError,
    SecSourceErrorCode,
)

NOW = datetime(2026, 8, 26, 4, 0, tzinfo=UTC)
USER_AGENT = "IndustryIntelligencePlatform/0.1 edgar-ops@example.test"


def catalog_body() -> bytes:
    return json.dumps(
        {
            "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
            "1": {"cik_str": 1652044, "ticker": "GOOG", "title": "Alphabet Inc."},
            "2": {"cik_str": 1652044, "ticker": "GOOGL", "title": "Alphabet Inc."},
        },
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass(slots=True)
class CountingBudget:
    calls: int = 0

    async def acquire(self) -> None:
        self.calls += 1


@dataclass(slots=True)
class MemoryCache:
    value: CachedSecResponse | None = None
    puts: int = 0

    async def get(self) -> CachedSecResponse | None:
        return self.value

    async def put(self, value: CachedSecResponse) -> None:
        self.value = value
        self.puts += 1


@pytest.mark.asyncio
async def test_live_adapter_uses_exact_official_url_identity_headers_and_fresh_cache() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            headers={"content-type": "application/json", "etag": '"catalog-v1"'},
            content=catalog_body(),
        )

    budget = CountingBudget()
    cache = MemoryCache()
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        trust_env=False,
    ) as client:
        adapter = LiveSecEdgarAdapter(
            client,
            budget,
            cache,
            user_agent=USER_AGENT,
            clock=lambda: NOW,
        )
        first = await adapter.fetch_filer_catalog()
        second = await adapter.fetch_filer_catalog()

    assert len(requests) == 1
    assert str(requests[0].url) == SEC_COMPANY_TICKERS_URL
    assert requests[0].headers["user-agent"] == USER_AGENT
    assert requests[0].headers["accept"] == "application/json"
    assert budget.calls == 1
    assert cache.puts == 1
    assert first == second
    assert first.filers[1].cik == "0001652044"
    assert [alias.display_value for alias in first.filers[1].aliases] == [
        "Alphabet Inc.",
        "GOOG",
        "GOOGL",
    ]


@pytest.mark.asyncio
async def test_stale_cache_is_revalidated_without_replacing_identical_bytes() -> None:
    now = [NOW]
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx2.Response(
                200,
                headers={"content-type": "application/json", "etag": '"catalog-v1"'},
                content=catalog_body(),
            )
        assert request.headers["if-none-match"] == '"catalog-v1"'
        return httpx2.Response(304, headers={"etag": '"catalog-v1"'})

    cache = MemoryCache()
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        trust_env=False,
    ) as client:
        adapter = LiveSecEdgarAdapter(
            client,
            CountingBudget(),
            cache,
            user_agent=USER_AGENT,
            clock=lambda: now[0],
            cache_ttl_seconds=60,
        )
        first = await adapter.fetch_filer_catalog()
        now[0] += timedelta(seconds=61)
        second = await adapter.fetch_filer_catalog()

    assert len(requests) == 2
    assert first.content_sha256 == second.content_sha256
    assert second.retrieved_at == NOW + timedelta(seconds=61)


@pytest.mark.asyncio
async def test_429_retries_with_bounded_delay_and_never_becomes_no_result() -> None:
    responses = [
        httpx2.Response(429, headers={"retry-after": "0"}),
        httpx2.Response(
            200,
            headers={"content-type": "application/json"},
            content=catalog_body(),
        ),
    ]
    delays: list[float] = []
    budget = CountingBudget()

    async def sleep(delay: float) -> None:
        delays.append(delay)

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _request: responses.pop(0)),
        trust_env=False,
    ) as client:
        result = await LiveSecEdgarAdapter(
            client,
            budget,
            MemoryCache(),
            user_agent=USER_AGENT,
            clock=lambda: NOW,
            sleep=sleep,
        ).fetch_filer_catalog()

    assert result.filers
    assert budget.calls == 2
    assert delays == [0.0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        (
            httpx2.Response(302, headers={"location": "https://example.com"}),
            SecSourceErrorCode.REDIRECT_REJECTED,
        ),
        (
            httpx2.Response(200, headers={"content-type": "text/html"}, content=b"{}"),
            SecSourceErrorCode.CONTENT_TYPE_INVALID,
        ),
        (
            httpx2.Response(
                200,
                headers={
                    "content-type": "application/json",
                    "content-length": str(SEC_MAX_CATALOG_RESPONSE_BYTES + 1),
                },
            ),
            SecSourceErrorCode.RESPONSE_TOO_LARGE,
        ),
        (
            httpx2.Response(
                200,
                headers={"content-type": "application/json"},
                content=b'{"0":{"cik_str":320193,"ticker":"AAPL","title":"Apple"},"0":{}}',
            ),
            SecSourceErrorCode.RESPONSE_INVALID,
        ),
    ],
)
async def test_untrusted_response_shapes_fail_with_stable_codes(
    response: httpx2.Response,
    expected_code: SecSourceErrorCode,
) -> None:
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _request: response),
        trust_env=False,
    ) as client:
        adapter = LiveSecEdgarAdapter(
            client,
            CountingBudget(),
            MemoryCache(),
            user_agent=USER_AGENT,
            clock=lambda: NOW,
        )
        with pytest.raises(SecSourceError) as caught:
            await adapter.fetch_filer_catalog()

    assert caught.value.code is expected_code


@dataclass(slots=True)
class ScriptedRedis:
    results: list[object]
    calls: int = 0

    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> object:
        assert "ZREMRANGEBYSCORE" in script
        assert numkeys == 1
        assert keys_and_args[1] == "8"
        self.calls += 1
        return self.results.pop(0)

    async def get(self, name: str) -> str | None:
        del name
        return None

    async def set(self, name: str, value: str, *, ex: int) -> bool | None:
        del name, value, ex
        return True


@pytest.mark.asyncio
async def test_redis_budget_waits_on_one_shared_sliding_window() -> None:
    redis = ScriptedRedis(results=[[0, 5_000], [1, 0]])
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    await RedisSecRequestBudget(redis, sleep=sleep).acquire()

    assert redis.calls == 2
    assert delays == [0.005]
