"""Bounded real-source Adapters and the fixed Provider registry."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, time
from typing import Final, cast

import httpx2
from pydantic import SecretStr

from industry_platform.modules.industry.domain import (
    MAX_SOURCE_RESPONSE_BYTES,
    IndustryProviderError,
    ProviderCode,
    ProviderErrorCode,
    ProviderItem,
    ProviderPage,
    ProviderQuery,
    ProviderReadiness,
    ProviderStatus,
    SourceKind,
    parse_market_price,
    parse_utc_date,
    provider_for_kind,
    unique_provider_items,
)
from industry_platform.modules.industry.ports import IndustrySourceProvider

_WORLD_BANK_URL: Final = "https://search.worldbank.org/api/v2/news"
_FEDERAL_REGISTER_URL: Final = "https://www.federalregister.gov/api/v1/documents.json"
_TED_URL: Final = "https://api.ted.europa.eu/v3/notices/search"
_ALPHA_VANTAGE_URL: Final = "https://www.alphavantage.co/query"
_TED_QUERY_PATTERN = re.compile(r"^[\w .-]{1,120}$", re.UNICODE)
_SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")
_USER_AGENT: Final = "IndustryIntelligencePlatform/0.1"


class _InvalidProviderResponse(RuntimeError):
    pass


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidProviderResponse
        result[key] = value
    return result


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise _InvalidProviderResponse
    return cast(dict[str, object], value)


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise _InvalidProviderResponse
    return value


def _string(value: object, *, maximum: int = 10_000) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 and character not in {"\n", "\t", "\r"} for character in value)
    ):
        raise _InvalidProviderResponse
    return " ".join(value.split())


def _optional_string(value: object, *, maximum: int = 10_000) -> str | None:
    if value is None or value == "":
        return None
    return _string(value, maximum=maximum)


def _integer(value: object, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _InvalidProviderResponse
    return value


def _cdata(value: object, *, maximum: int) -> str:
    document = _mapping(value)
    return _string(document.get("cdata!"), maximum=maximum)


def _parse_datetime(value: object) -> datetime:
    text = _string(value, maximum=64)
    date_with_offset = re.fullmatch(r"(\d{4}-\d{2}-\d{2})([+-]\d{2}:\d{2})", text)
    if date_with_offset is not None:
        text = f"{date_with_offset.group(1)}T{time.min.isoformat()}{date_with_offset.group(2)}"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise _InvalidProviderResponse from None
    if parsed.tzinfo is None:
        raise _InvalidProviderResponse
    return parsed.astimezone(UTC)


class BoundedJsonClient:
    """Read one decompressed JSON response with strict status/content/byte limits."""

    def __init__(
        self,
        client: httpx2.AsyncClient,
        *,
        maximum_bytes: int = MAX_SOURCE_RESPONSE_BYTES,
        timeout_seconds: float = 30.0,
    ) -> None:
        if maximum_bytes < 1 or not 0 < timeout_seconds <= 60:
            raise ValueError("Bounded Provider client limits are invalid")
        self._client = client
        self._maximum_bytes = maximum_bytes
        self._timeout_seconds = timeout_seconds

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str | int | bool] | None = None,
        json_body: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                async with self._client.stream(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers={"Accept": "application/json", "User-Agent": _USER_AGENT},
                    follow_redirects=False,
                    timeout=self._timeout_seconds,
                ) as response:
                    if 300 <= response.status_code < 400:
                        raise IndustryProviderError(
                            ProviderErrorCode.REDIRECT_REJECTED,
                            retryable=False,
                        )
                    if response.status_code == 429:
                        raise IndustryProviderError(
                            ProviderErrorCode.RATE_LIMITED,
                            retryable=True,
                        )
                    if response.status_code >= 500:
                        raise IndustryProviderError(
                            ProviderErrorCode.UPSTREAM_ERROR,
                            retryable=True,
                        )
                    if response.status_code >= 400:
                        raise IndustryProviderError(
                            ProviderErrorCode.RESPONSE_INVALID,
                            retryable=False,
                        )
                    content_type = response.headers.get("content-type", "").partition(";")[0]
                    if content_type.strip().lower() != "application/json":
                        raise IndustryProviderError(
                            ProviderErrorCode.CONTENT_TYPE_INVALID,
                            retryable=False,
                        )
                    content_length = response.headers.get("content-length")
                    if content_length is not None:
                        try:
                            declared_length = int(content_length)
                        except ValueError:
                            raise IndustryProviderError(
                                ProviderErrorCode.RESPONSE_INVALID,
                                retryable=False,
                            ) from None
                        if declared_length < 0 or declared_length > self._maximum_bytes:
                            raise IndustryProviderError(
                                ProviderErrorCode.RESPONSE_TOO_LARGE,
                                retryable=False,
                            )
                    chunks: list[bytes] = []
                    observed = 0
                    async for chunk in response.aiter_bytes():
                        observed += len(chunk)
                        if observed > self._maximum_bytes:
                            raise IndustryProviderError(
                                ProviderErrorCode.RESPONSE_TOO_LARGE,
                                retryable=False,
                            )
                        chunks.append(chunk)
                    try:
                        decoded = json.loads(
                            b"".join(chunks).decode("utf-8", errors="strict"),
                            object_pairs_hook=_unique_json_object,
                        )
                    except (UnicodeDecodeError, json.JSONDecodeError, _InvalidProviderResponse):
                        raise IndustryProviderError(
                            ProviderErrorCode.RESPONSE_INVALID,
                            retryable=False,
                        ) from None
                    try:
                        return _mapping(decoded)
                    except _InvalidProviderResponse:
                        raise IndustryProviderError(
                            ProviderErrorCode.RESPONSE_INVALID,
                            retryable=False,
                        ) from None
        except IndustryProviderError:
            raise
        except (TimeoutError, httpx2.TimeoutException):
            raise IndustryProviderError(ProviderErrorCode.TIMEOUT, retryable=True) from None
        except (httpx2.RequestError, httpx2.InvalidURL):
            raise IndustryProviderError(
                ProviderErrorCode.UPSTREAM_ERROR,
                retryable=True,
            ) from None


class _ReadyProvider:
    kind: SourceKind

    @property
    def status(self) -> ProviderStatus:
        definition = provider_for_kind(self.kind)
        return ProviderStatus(
            provider=definition.provider,
            kind=definition.kind,
            readiness=ProviderReadiness.READY,
            reason_code=None,
        )


class WorldBankNewsProvider:
    """Official World Bank news/search endpoint, normalized to news facts."""

    kind = SourceKind.NEWS

    def __init__(
        self,
        client: BoundedJsonClient,
        *,
        terms_approved: bool,
        clock: Callable[[], datetime],
    ) -> None:
        self._client = client
        self._terms_approved = terms_approved
        self._clock = clock

    @property
    def status(self) -> ProviderStatus:
        definition = provider_for_kind(self.kind)
        return ProviderStatus(
            provider=definition.provider,
            kind=definition.kind,
            readiness=(
                ProviderReadiness.READY
                if self._terms_approved
                else ProviderReadiness.TERMS_APPROVAL_REQUIRED
            ),
            reason_code=(
                None if self._terms_approved else ProviderErrorCode.TERMS_APPROVAL_REQUIRED
            ),
        )

    async def fetch(self, query: ProviderQuery) -> ProviderPage:
        if not self._terms_approved:
            raise IndustryProviderError(
                ProviderErrorCode.TERMS_APPROVAL_REQUIRED,
                retryable=False,
            )
        offset = _cursor_offset(query.cursor)
        document = await self._client.request(
            "GET",
            _WORLD_BANK_URL,
            params={
                "format": "json",
                "qterm": query.query,
                "rows": query.limit,
                "os": offset,
                "apilang": "en",
            },
        )
        try:
            total = _integer(document.get("total"))
            documents = _mapping(document.get("documents"))
            items: list[ProviderItem] = []
            for external_key, raw in documents.items():
                if external_key == "facets" or len(items) >= query.limit:
                    continue
                item = _mapping(raw)
                title = _cdata(item.get("title"), maximum=1_000)
                summary_value = item.get("descr") or item.get("content_1000")
                summary = _cdata(summary_value, maximum=10_000)
                launch = _string(item.get("lnchdt"), maximum=64)
                try:
                    published_at = datetime.strptime(launch, "%Y/%m/%d %H:%M:%S").replace(
                        tzinfo=UTC
                    )
                except ValueError:
                    raise _InvalidProviderResponse from None
                items.append(
                    ProviderItem(
                        kind=self.kind,
                        provider=ProviderCode.WORLD_BANK_NEWS,
                        external_id=_string(item.get("id") or external_key, maximum=256),
                        title=title,
                        summary=summary,
                        locator=_string(item.get("url"), maximum=2_048),
                        published_at=published_at,
                        metadata={
                            "category": _optional_string(item.get("displayconttype"), maximum=100)
                            or "News",
                            "publication_domain": _optional_string(
                                item.get("publication_domain"), maximum=500
                            ),
                        },
                    )
                )
            next_offset = offset + len(items)
            next_cursor = str(next_offset) if next_offset < total and items else None
            return ProviderPage(
                definition=provider_for_kind(self.kind),
                items=unique_provider_items(items),
                next_cursor=next_cursor,
                fetched_at=_utc_clock(self._clock),
            )
        except (_InvalidProviderResponse, TypeError, ValueError):
            raise IndustryProviderError(
                ProviderErrorCode.RESPONSE_INVALID,
                retryable=False,
            ) from None


class FederalRegisterPolicyProvider(_ReadyProvider):
    """Official Federal Register API normalized to policy facts."""

    kind = SourceKind.POLICY

    def __init__(self, client: BoundedJsonClient, *, clock: Callable[[], datetime]) -> None:
        self._client = client
        self._clock = clock

    async def fetch(self, query: ProviderQuery) -> ProviderPage:
        page_number = _cursor_page(query.cursor)
        document = await self._client.request(
            "GET",
            _FEDERAL_REGISTER_URL,
            params={
                "per_page": query.limit,
                "page": page_number,
                "order": "newest",
                "conditions[term]": query.query,
            },
        )
        try:
            total_pages = _integer(document.get("total_pages"), minimum=0)
            items: list[ProviderItem] = []
            for raw in _list(document.get("results")):
                item = _mapping(raw)
                title = _string(item.get("title"), maximum=1_000)
                agencies = [
                    _string(_mapping(value).get("name"), maximum=200)
                    for value in _list(item.get("agencies"))
                ]
                items.append(
                    ProviderItem(
                        kind=self.kind,
                        provider=ProviderCode.FEDERAL_REGISTER,
                        external_id=_string(item.get("document_number"), maximum=256),
                        title=title,
                        summary=_optional_string(item.get("abstract"), maximum=10_000) or title,
                        locator=_string(item.get("html_url"), maximum=2_048),
                        published_at=parse_utc_date(
                            _string(item.get("publication_date"), maximum=10),
                            field_name="Federal Register publication date",
                        ),
                        metadata={
                            "agency": ", ".join(agencies) if agencies else "Unknown",
                            "document_number": _string(item.get("document_number"), maximum=100),
                            "document_type": _string(item.get("type"), maximum=100),
                            "jurisdiction": "United States",
                        },
                    )
                )
            return ProviderPage(
                definition=provider_for_kind(self.kind),
                items=unique_provider_items(items),
                next_cursor=str(page_number + 1) if page_number < total_pages and items else None,
                fetched_at=_utc_clock(self._clock),
            )
        except (_InvalidProviderResponse, TypeError, ValueError):
            raise IndustryProviderError(
                ProviderErrorCode.RESPONSE_INVALID,
                retryable=False,
            ) from None


class TedTenderProvider(_ReadyProvider):
    """Anonymous TED v3 Search API normalized to tender facts."""

    kind = SourceKind.TENDER

    def __init__(self, client: BoundedJsonClient, *, clock: Callable[[], datetime]) -> None:
        self._client = client
        self._clock = clock

    async def fetch(self, query: ProviderQuery) -> ProviderPage:
        search_term = query.query[:120]
        if not _TED_QUERY_PATTERN.fullmatch(search_term):
            raise IndustryProviderError(ProviderErrorCode.RESPONSE_INVALID, retryable=False)
        page_number = _cursor_page(query.cursor)
        document = await self._client.request(
            "POST",
            _TED_URL,
            json_body={
                "query": f'notice-title ~ "{search_term}"',
                "fields": [
                    "publication-number",
                    "notice-title",
                    "publication-date",
                    "buyer-country",
                    "notice-type",
                ],
                "page": page_number,
                "limit": query.limit,
                "scope": "ALL",
                "checkQuerySyntax": False,
                "paginationMode": "PAGE_NUMBER",
                "onlyLatestVersions": True,
            },
        )
        try:
            total = _integer(document.get("totalNoticeCount"))
            items: list[ProviderItem] = []
            for raw in _list(document.get("notices")):
                item = _mapping(raw)
                publication_number = _string(item.get("publication-number"), maximum=100)
                titles = _mapping(item.get("notice-title"))
                title_value = titles.get("eng")
                if title_value is None:
                    title_value = next(iter(titles.values()), None)
                title = _string(title_value, maximum=1_000)
                links = _mapping(item.get("links"))
                html_links = _mapping(links.get("html"))
                locator_value = html_links.get("ENG")
                if locator_value is None:
                    locator_value = next(iter(html_links.values()), None)
                buyer_country = item.get("buyer-country")
                if isinstance(buyer_country, list):
                    region = ",".join(_string(value, maximum=10) for value in buyer_country)
                else:
                    region = _string(buyer_country, maximum=100)
                notice_type = _string(item.get("notice-type"), maximum=100)
                items.append(
                    ProviderItem(
                        kind=self.kind,
                        provider=ProviderCode.TED,
                        external_id=publication_number,
                        title=title,
                        summary=f"{notice_type} procurement notice for region {region}.",
                        locator=_string(locator_value, maximum=2_048),
                        published_at=_parse_datetime(item.get("publication-date")),
                        metadata={"notice_type": notice_type, "region": region},
                    )
                )
            consumed = (page_number - 1) * query.limit + len(items)
            return ProviderPage(
                definition=provider_for_kind(self.kind),
                items=unique_provider_items(items),
                next_cursor=str(page_number + 1) if consumed < total and items else None,
                fetched_at=_utc_clock(self._clock),
            )
        except (_InvalidProviderResponse, TypeError, ValueError):
            raise IndustryProviderError(
                ProviderErrorCode.RESPONSE_INVALID,
                retryable=False,
            ) from None


class AlphaVantageStockProvider:
    """Configured Alpha Vantage Global Quote Adapter; never invents market data."""

    kind = SourceKind.STOCK

    def __init__(
        self,
        client: BoundedJsonClient,
        *,
        api_key: SecretStr | None,
        terms_approved: bool,
        clock: Callable[[], datetime],
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._terms_approved = terms_approved
        self._clock = clock

    @property
    def status(self) -> ProviderStatus:
        definition = provider_for_kind(self.kind)
        if self._api_key is None:
            readiness = ProviderReadiness.NOT_CONFIGURED
            reason_code = ProviderErrorCode.NOT_CONFIGURED
        elif not self._terms_approved:
            readiness = ProviderReadiness.TERMS_APPROVAL_REQUIRED
            reason_code = ProviderErrorCode.TERMS_APPROVAL_REQUIRED
        else:
            readiness = ProviderReadiness.READY
            reason_code = None
        return ProviderStatus(
            provider=definition.provider,
            kind=definition.kind,
            readiness=readiness,
            reason_code=reason_code,
        )

    async def fetch(self, query: ProviderQuery) -> ProviderPage:
        api_key = self._api_key
        symbol = query.query.upper()
        if api_key is None:
            raise IndustryProviderError(ProviderErrorCode.NOT_CONFIGURED, retryable=False)
        if not self._terms_approved:
            raise IndustryProviderError(
                ProviderErrorCode.TERMS_APPROVAL_REQUIRED,
                retryable=False,
            )
        if not _SYMBOL_PATTERN.fullmatch(symbol):
            raise IndustryProviderError(ProviderErrorCode.RESPONSE_INVALID, retryable=False)
        document = await self._client.request(
            "GET",
            _ALPHA_VANTAGE_URL,
            params={
                "function": "GLOBAL_QUOTE",
                "symbol": symbol,
                "apikey": api_key.get_secret_value(),
            },
        )
        try:
            if "Note" in document or "Information" in document:
                raise IndustryProviderError(ProviderErrorCode.RATE_LIMITED, retryable=True)
            quote_document = _mapping(document.get("Global Quote"))
            returned_symbol = _string(quote_document.get("01. symbol"), maximum=16).upper()
            if returned_symbol != symbol:
                raise _InvalidProviderResponse
            price = parse_market_price(_string(quote_document.get("05. price"), maximum=64))
            trading_day = _string(quote_document.get("07. latest trading day"), maximum=10)
            published_at = parse_utc_date(trading_day, field_name="Market trading day")
            title = f"{symbol} market quote"
            summary = f"{symbol} closed at {price} USD on {trading_day}."
            item = ProviderItem(
                kind=self.kind,
                provider=ProviderCode.ALPHA_VANTAGE,
                external_id=f"{symbol}:{trading_day}",
                title=title,
                summary=summary,
                locator="https://www.alphavantage.co/query",
                published_at=published_at,
                metadata={
                    "currency": "USD",
                    "observed_at": published_at.isoformat(),
                    "price": price,
                    "symbol": symbol,
                },
            )
            return ProviderPage(
                definition=provider_for_kind(self.kind),
                items=(item,),
                next_cursor=None,
                fetched_at=_utc_clock(self._clock),
            )
        except IndustryProviderError:
            raise
        except (_InvalidProviderResponse, TypeError, ValueError):
            raise IndustryProviderError(
                ProviderErrorCode.RESPONSE_INVALID,
                retryable=False,
            ) from None


class FixedIndustryProviderRegistry:
    """Exact four-domain allowlist; no dynamic imports or mock fallback."""

    def __init__(self, providers: Sequence[IndustrySourceProvider]) -> None:
        selected = tuple(providers)
        by_kind = {provider.status.kind: provider for provider in selected}
        if set(by_kind) != set(SourceKind) or len(by_kind) != len(selected):
            raise ValueError("Industry Provider Registry requires exactly one Provider per kind")
        self._by_kind = by_kind

    def provider(self, kind: SourceKind) -> IndustrySourceProvider:
        return self._by_kind[kind]

    def statuses(self) -> tuple[ProviderStatus, ...]:
        return tuple(self._by_kind[kind].status for kind in SourceKind)


def create_provider_registry(
    client: httpx2.AsyncClient,
    *,
    world_bank_news_terms_approved: bool,
    alpha_vantage_api_key: SecretStr | None,
    alpha_vantage_terms_approved: bool,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> FixedIndustryProviderRegistry:
    bounded = BoundedJsonClient(client)
    return FixedIndustryProviderRegistry(
        (
            WorldBankNewsProvider(
                bounded,
                terms_approved=world_bank_news_terms_approved,
                clock=clock,
            ),
            FederalRegisterPolicyProvider(bounded, clock=clock),
            TedTenderProvider(bounded, clock=clock),
            AlphaVantageStockProvider(
                bounded,
                api_key=alpha_vantage_api_key,
                terms_approved=alpha_vantage_terms_approved,
                clock=clock,
            ),
        )
    )


def _cursor_offset(value: str | None) -> int:
    if value is None:
        return 0
    try:
        result = int(value)
    except ValueError:
        raise IndustryProviderError(ProviderErrorCode.RESPONSE_INVALID, retryable=False) from None
    if result < 0 or result > 100_000:
        raise IndustryProviderError(ProviderErrorCode.RESPONSE_INVALID, retryable=False)
    return result


def _cursor_page(value: str | None) -> int:
    if value is None:
        return 1
    result = _cursor_offset(value)
    if result < 1:
        raise IndustryProviderError(ProviderErrorCode.RESPONSE_INVALID, retryable=False)
    return result


def _utc_clock(clock: Callable[[], datetime]) -> datetime:
    observed = clock()
    if observed.tzinfo is None or observed.utcoffset() != UTC.utcoffset(observed):
        raise ValueError("Provider clock must return UTC")
    return observed
