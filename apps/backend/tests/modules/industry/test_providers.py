"""Real Provider contracts over deterministic official-response snapshots."""

import json
from collections.abc import Callable
from datetime import UTC, datetime

import httpx2
import pytest
from pydantic import SecretStr

from industry_platform.modules.industry.domain import (
    ENERGY_POWER_INDUSTRY_ID,
    INDUSTRY_PRESETS_BY_ID,
    IndustryProviderError,
    ProviderCode,
    ProviderErrorCode,
    ProviderQuery,
    ProviderReadiness,
    SourceKind,
)
from industry_platform.modules.industry.providers import (
    AlphaVantageStockProvider,
    BoundedJsonClient,
    FederalRegisterPolicyProvider,
    TedTenderProvider,
    WorldBankNewsProvider,
    create_provider_registry,
)

NOW = datetime(2026, 8, 17, 2, 30, tzinfo=UTC)
INDUSTRY = INDUSTRY_PRESETS_BY_ID[ENERGY_POWER_INDUSTRY_ID]


def _client(handler: Callable[[httpx2.Request], httpx2.Response]) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    )


def _json_response(document: object, *, status: int = 200) -> httpx2.Response:
    return httpx2.Response(
        status,
        headers={"Content-Type": "application/json"},
        content=json.dumps(document).encode(),
    )


@pytest.mark.asyncio
async def test_world_bank_news_contract_uses_fixed_endpoint_and_normalizes_https() -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return _json_response(
            {
                "rows": 1,
                "os": 0,
                "page": 1,
                "total": 2,
                "documents": {
                    "snapshot-key": {
                        "id": "world-bank-news-1",
                        "url": "http://www.worldbank.org/en/topic/energy/publication/example",
                        "descr": {"cdata!": "Public energy access summary."},
                        "title": {"cdata!": "Energy Access Redefined"},
                        "lnchdt": "2026/8/17 3:00:00",
                        "displayconttype": "Feature Story",
                        "publication_domain": "topic|energy",
                    },
                    "facets": {},
                },
            }
        )

    client = _client(respond)
    try:
        blocked = WorldBankNewsProvider(
            BoundedJsonClient(client),
            terms_approved=False,
            clock=lambda: NOW,
        )
        assert blocked.status.readiness is ProviderReadiness.TERMS_APPROVAL_REQUIRED
        with pytest.raises(IndustryProviderError) as blocked_error:
            await blocked.fetch(ProviderQuery(INDUSTRY, "energy", limit=1))
        assert blocked_error.value.code is ProviderErrorCode.TERMS_APPROVAL_REQUIRED
        assert requests == []

        provider = WorldBankNewsProvider(
            BoundedJsonClient(client),
            terms_approved=True,
            clock=lambda: NOW,
        )
        page = await provider.fetch(ProviderQuery(INDUSTRY, "energy", limit=1))
    finally:
        await client.aclose()

    assert requests[0].url.host == "search.worldbank.org"
    assert requests[0].url.path == "/api/v2/news"
    assert requests[0].url.params["qterm"] == "energy"
    assert page.next_cursor == "1"
    assert page.items[0].locator.startswith("https://www.worldbank.org/")
    assert page.items[0].metadata["category"] == "Feature Story"


@pytest.mark.asyncio
async def test_federal_register_policy_contract_preserves_agency_and_document_number() -> None:
    def respond(request: httpx2.Request) -> httpx2.Response:
        assert request.url.host == "www.federalregister.gov"
        assert request.url.params["conditions[term]"] == "energy"
        return _json_response(
            {
                "count": 1,
                "total_pages": 1,
                "results": [
                    {
                        "title": "Energy conservation standard",
                        "type": "Proposed Rule",
                        "abstract": "The agency proposes a bounded energy standard.",
                        "document_number": "2026-16774",
                        "html_url": (
                            "https://www.federalregister.gov/documents/2026/08/17/"
                            "2026-16774/energy-conservation-standard"
                        ),
                        "publication_date": "2026-08-17",
                        "agencies": [{"name": "Department of Energy"}],
                    }
                ],
            }
        )

    client = _client(respond)
    try:
        page = await FederalRegisterPolicyProvider(
            BoundedJsonClient(client), clock=lambda: NOW
        ).fetch(ProviderQuery(INDUSTRY, "energy", limit=1))
    finally:
        await client.aclose()

    assert page.items[0].metadata == {
        "agency": "Department of Energy",
        "document_number": "2026-16774",
        "document_type": "Proposed Rule",
        "jurisdiction": "United States",
    }


@pytest.mark.asyncio
async def test_federal_register_accepts_an_empty_first_page() -> None:
    client = _client(lambda _request: _json_response({"count": 0, "total_pages": 0, "results": []}))
    try:
        page = await FederalRegisterPolicyProvider(
            BoundedJsonClient(client), clock=lambda: NOW
        ).fetch(ProviderQuery(INDUSTRY, "no matching policy", limit=1))
    finally:
        await client.aclose()

    assert page.items == ()
    assert page.next_cursor is None


@pytest.mark.asyncio
async def test_ted_contract_uses_expert_search_without_syntax_only_mode() -> None:
    def respond(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        assert body["query"] == 'notice-title ~ "energy"'
        assert body["checkQuerySyntax"] is False
        assert body["paginationMode"] == "PAGE_NUMBER"
        return _json_response(
            {
                "notices": [
                    {
                        "notice-type": "cn-standard",
                        "publication-number": "284363-2026",
                        "publication-date": "2026-08-17+02:00",
                        "links": {
                            "html": {
                                "ENG": ("https://ted.europa.eu/en/notice/-/detail/284363-2026")
                            }
                        },
                        "notice-title": {"eng": "Energy sector support programme"},
                        "buyer-country": "DEU",
                    }
                ],
                "totalNoticeCount": 1,
                "iterationNextToken": None,
                "timedOut": False,
            }
        )

    client = _client(respond)
    try:
        page = await TedTenderProvider(BoundedJsonClient(client), clock=lambda: NOW).fetch(
            ProviderQuery(INDUSTRY, "energy", limit=1)
        )
    finally:
        await client.aclose()

    assert page.items[0].external_id == "284363-2026"
    assert page.items[0].metadata["notice_type"] == "cn-standard"
    assert page.items[0].metadata["region"] == "DEU"


@pytest.mark.asyncio
async def test_alpha_vantage_is_explicitly_unconfigured_and_demo_snapshot_is_real_shape() -> None:
    calls = 0

    def respond(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        assert request.url.params["apikey"] == "demo"
        return _json_response(
            {
                "Global Quote": {
                    "01. symbol": "IBM",
                    "02. open": "232.0000",
                    "03. high": "235.0000",
                    "04. low": "231.0000",
                    "05. price": "234.3200",
                    "06. volume": "1000",
                    "07. latest trading day": "2026-08-14",
                    "08. previous close": "232.1000",
                    "09. change": "2.2200",
                    "10. change percent": "0.9565%",
                }
            }
        )

    client = _client(respond)
    try:
        unconfigured = AlphaVantageStockProvider(
            BoundedJsonClient(client),
            api_key=None,
            terms_approved=False,
            clock=lambda: NOW,
        )
        assert unconfigured.status.readiness is ProviderReadiness.NOT_CONFIGURED
        with pytest.raises(IndustryProviderError) as captured:
            await unconfigured.fetch(ProviderQuery(INDUSTRY, "IBM"))
        assert captured.value.code is ProviderErrorCode.NOT_CONFIGURED
        assert calls == 0

        terms_blocked = AlphaVantageStockProvider(
            BoundedJsonClient(client),
            api_key=SecretStr("demo"),
            terms_approved=False,
            clock=lambda: NOW,
        )
        assert terms_blocked.status.readiness is ProviderReadiness.TERMS_APPROVAL_REQUIRED
        with pytest.raises(IndustryProviderError) as terms_error:
            await terms_blocked.fetch(ProviderQuery(INDUSTRY, "IBM"))
        assert terms_error.value.code is ProviderErrorCode.TERMS_APPROVAL_REQUIRED
        assert calls == 0

        configured = AlphaVantageStockProvider(
            BoundedJsonClient(client),
            api_key=SecretStr("demo"),
            terms_approved=True,
            clock=lambda: NOW,
        )
        page = await configured.fetch(ProviderQuery(INDUSTRY, "IBM"))
    finally:
        await client.aclose()

    assert page.items[0].metadata["price"] == "234.32"
    assert page.items[0].external_id == "IBM:2026-08-14"
    assert "demo" not in repr(configured)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            httpx2.Response(
                302,
                headers={"Location": "http://169.254.169.254/latest"},
            ),
            ProviderErrorCode.REDIRECT_REJECTED,
        ),
        (
            httpx2.Response(200, headers={"Content-Type": "text/html"}, content=b"{}"),
            ProviderErrorCode.CONTENT_TYPE_INVALID,
        ),
        (
            httpx2.Response(
                200,
                headers={"Content-Type": "application/json"},
                content=b'{"a":1,"a":2}',
            ),
            ProviderErrorCode.RESPONSE_INVALID,
        ),
        (_json_response({}, status=429), ProviderErrorCode.RATE_LIMITED),
    ],
)
async def test_bounded_client_rejects_redirect_content_and_ambiguous_json(
    response: httpx2.Response,
    expected: ProviderErrorCode,
) -> None:
    client = _client(lambda _request: response)
    try:
        with pytest.raises(IndustryProviderError) as captured:
            await BoundedJsonClient(client).request("GET", "https://api.example.com/data")
    finally:
        await client.aclose()
    assert captured.value.code is expected


@pytest.mark.asyncio
async def test_bounded_client_counts_decompressed_bytes_and_stops_at_limit() -> None:
    client = _client(
        lambda _request: httpx2.Response(
            200,
            headers={"Content-Type": "application/json"},
            content=b'{"payload":"' + b"x" * 200 + b'"}',
        )
    )
    try:
        with pytest.raises(IndustryProviderError) as captured:
            await BoundedJsonClient(client, maximum_bytes=64).request(
                "GET", "https://api.example.com/data"
            )
    finally:
        await client.aclose()
    assert captured.value.code is ProviderErrorCode.RESPONSE_TOO_LARGE


@pytest.mark.asyncio
async def test_bounded_client_classifies_connect_failure_as_upstream_error() -> None:
    def fail(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection failed", request=request)

    client = _client(fail)
    try:
        with pytest.raises(IndustryProviderError) as captured:
            await BoundedJsonClient(client).request("GET", "https://api.example.com/data")
    finally:
        await client.aclose()

    assert captured.value.code is ProviderErrorCode.UPSTREAM_ERROR
    assert captured.value.retryable is True


@pytest.mark.asyncio
async def test_registry_has_one_real_adapter_per_domain_without_mock_fallback() -> None:
    client = _client(lambda _request: _json_response({}))
    try:
        registry = create_provider_registry(
            client,
            world_bank_news_terms_approved=False,
            alpha_vantage_api_key=None,
            alpha_vantage_terms_approved=False,
            clock=lambda: NOW,
        )
        assert tuple(status.kind for status in registry.statuses()) == tuple(SourceKind)
        assert tuple(status.provider for status in registry.statuses()) == (
            ProviderCode.WORLD_BANK_NEWS,
            ProviderCode.FEDERAL_REGISTER,
            ProviderCode.TED,
            ProviderCode.ALPHA_VANTAGE,
        )
        statuses = registry.statuses()
        assert statuses[0].reason_code is ProviderErrorCode.TERMS_APPROVAL_REQUIRED
        assert statuses[-1].reason_code is ProviderErrorCode.NOT_CONFIGURED
    finally:
        await client.aclose()
