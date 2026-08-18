"""Technology-independent contracts for industry context and source collection."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from types import MappingProxyType
from typing import Final
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.jobs.domain import ScheduleMisfirePolicy, require_utc
from industry_platform.modules.workspaces.domain import WorkspaceScope

INDUSTRY_COLLECTION_TASK_NAME: Final = "industry.sources.collect.v1"
INDUSTRY_COLLECTION_QUEUE_NAME: Final = "industry-collection"
INDUSTRY_SOURCE_SCHEMA_VERSION: Final = 1
MAX_SOURCE_ITEMS_PER_PAGE: Final = 20
MAX_SOURCE_RESPONSE_BYTES: Final = 1_000_000

SMART_TRANSPORT_INDUSTRY_ID: Final = UUID("5ae94c40-4441-5e6f-b4cb-0679e8a92f9e")
FINTECH_INDUSTRY_ID: Final = UUID("56edef5d-ee4d-5978-8069-f89bd391ac20")
HEALTHCARE_INDUSTRY_ID: Final = UUID("5ecae69a-b8d1-54a3-9fe4-e0a6a3c86cbe")
ENERGY_POWER_INDUSTRY_ID: Final = UUID("a985dc08-83d8-5efb-b84e-034ffd453e38")

WORLD_BANK_NEWS_SOURCE_ID: Final = UUID("3dadb35b-658d-5fa9-a88c-108f159e9b4c")
FEDERAL_REGISTER_SOURCE_ID: Final = UUID("e410cbbf-b09d-5daa-ab9c-4a7f98c160d1")
TED_SOURCE_ID: Final = UUID("8ae93bf1-af49-5ff7-95ca-68325e14935c")
ALPHA_VANTAGE_SOURCE_ID: Final = UUID("f55cc967-812e-5934-b734-b9446c1346d9")

_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_PROVIDER_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,99}$")
_EXTERNAL_ID_PATTERN = re.compile(r"^[^\x00-\x1f\x7f]{1,256}$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")
_PUBLIC_LOCATOR_HOSTS: Final = frozenset(
    {
        "www.worldbank.org",
        "documents.worldbank.org",
        "www.federalregister.gov",
        "ted.europa.eu",
        "www.alphavantage.co",
    }
)


class SourceKind(StrEnum):
    """Stable product domains backed by independent Provider contracts."""

    NEWS = "news"
    POLICY = "policy"
    TENDER = "tender"
    STOCK = "stock"


class ProviderCode(StrEnum):
    """Exact allowlisted external integrations."""

    WORLD_BANK_NEWS = "world_bank_news"
    FEDERAL_REGISTER = "federal_register"
    TED = "ted"
    ALPHA_VANTAGE = "alpha_vantage"


class ProviderReadiness(StrEnum):
    READY = "ready"
    NOT_CONFIGURED = "not_configured"
    TERMS_APPROVAL_REQUIRED = "terms_approval_required"


class ProviderErrorCode(StrEnum):
    """Sanitized external failure vocabulary safe for persistence and APIs."""

    NOT_CONFIGURED = "provider_not_configured"
    TERMS_APPROVAL_REQUIRED = "provider_terms_approval_required"
    RATE_LIMITED = "provider_rate_limited"
    TIMEOUT = "provider_timeout"
    REDIRECT_REJECTED = "provider_redirect_rejected"
    RESPONSE_TOO_LARGE = "provider_response_too_large"
    CONTENT_TYPE_INVALID = "provider_content_type_invalid"
    RESPONSE_INVALID = "provider_response_invalid"
    UPSTREAM_ERROR = "provider_upstream_error"


class CollectionRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class CollectionTriggerKind(StrEnum):
    SCHEDULED = "scheduled"
    MANUAL = "manual"


class SourceItemDisposition(StrEnum):
    INSERTED = "inserted"
    DUPLICATE_EXTERNAL_ID = "duplicate_external_id"
    DUPLICATE_CONTENT = "duplicate_content"


class IndustryNotFoundError(RuntimeError):
    """The selected preset does not exist."""


class IndustryPersistenceError(RuntimeError):
    """Persistence failed without exposing database detail."""

    def __init__(self, *, sqlstate: str | None = None) -> None:
        super().__init__("Industry persistence failed")
        self.sqlstate = sqlstate


class IndustryCollectionNotFoundError(RuntimeError):
    """A collection schedule/run is not visible in the selected Workspace."""


class IndustryProviderError(RuntimeError):
    """One stable Provider failure with explicit retry eligibility."""

    def __init__(self, code: ProviderErrorCode, *, retryable: bool) -> None:
        super().__init__("Industry Provider request failed")
        self.code = code
        self.retryable = retryable


def _require_text(value: str, *, field_name: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 and character not in {"\n", "\t"} for character in value)
        or any(ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{field_name} is invalid")
    return value


def canonical_public_locator(value: str) -> str:
    """Return one credential-free HTTPS locator from the source allowlist."""

    _require_text(value, field_name="Source locator", maximum=2_048)
    try:
        parsed = urlsplit(value)
        has_userinfo = parsed.username is not None or parsed.password is not None
        port = parsed.port
    except ValueError:
        raise ValueError("Source locator is invalid") from None
    hostname = parsed.hostname.casefold() if parsed.hostname is not None else None
    if (
        parsed.scheme not in {"http", "https"}
        or hostname not in _PUBLIC_LOCATOR_HOSTS
        or has_userinfo
        or port not in {None, 443, 80}
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/")
        or "\\" in parsed.path
    ):
        raise ValueError("Source locator is invalid")
    # World Bank's API still returns historical http links; its public pages support HTTPS.
    canonical = urlunsplit(("https", hostname, parsed.path, "", ""))
    if len(canonical) > 2_048:
        raise ValueError("Source locator is invalid")
    return canonical


def canonical_content_sha256(value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        dict(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _snapshot_metadata(value: Mapping[str, object]) -> Mapping[str, object]:
    try:
        encoded = json.dumps(
            dict(value),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        decoded = json.loads(encoded)
    except (TypeError, ValueError):
        raise ValueError("Source item metadata is invalid") from None
    if not isinstance(decoded, dict) or len(encoded.encode("utf-8")) > 16_384:
        raise ValueError("Source item metadata is invalid")
    return MappingProxyType(decoded)


@dataclass(frozen=True, slots=True)
class IndustryPreset:
    industry_id: UUID
    code: str
    name: str
    default_query: str
    default_symbol: str

    def __post_init__(self) -> None:
        if self.industry_id.int == 0 or not _CODE_PATTERN.fullmatch(self.code):
            raise ValueError("Industry preset identity is invalid")
        _require_text(self.name, field_name="Industry name", maximum=100)
        _require_text(self.default_query, field_name="Industry query", maximum=200)
        if not _SYMBOL_PATTERN.fullmatch(self.default_symbol):
            raise ValueError("Industry symbol is invalid")


INDUSTRY_PRESETS: Final = (
    IndustryPreset(
        SMART_TRANSPORT_INDUSTRY_ID,
        "smart_transport",
        "智慧交通",
        "smart transport mobility autonomous vehicles",
        "TSLA",
    ),
    IndustryPreset(
        FINTECH_INDUSTRY_ID,
        "fintech",
        "金融科技",
        "financial technology digital payments fintech",
        "PYPL",
    ),
    IndustryPreset(
        HEALTHCARE_INDUSTRY_ID,
        "healthcare",
        "医疗健康",
        "healthcare medical health technology",
        "UNH",
    ),
    IndustryPreset(
        ENERGY_POWER_INDUSTRY_ID,
        "energy_power",
        "能源电力",
        "energy power electricity renewable",
        "XOM",
    ),
)
INDUSTRY_PRESETS_BY_ID: Final = MappingProxyType(
    {preset.industry_id: preset for preset in INDUSTRY_PRESETS}
)
INDUSTRY_PRESETS_BY_CODE: Final = MappingProxyType(
    {preset.code: preset for preset in INDUSTRY_PRESETS}
)


@dataclass(frozen=True, slots=True)
class IndustryPreference:
    workspace_id: UUID
    user_id: UUID
    industry: IndustryPreset
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.workspace_id.int == 0 or self.user_id.int == 0:
            raise ValueError("Industry preference scope is invalid")
        require_utc(self.updated_at, field_name="Industry preference update time")


@dataclass(frozen=True, slots=True)
class SourceProviderDefinition:
    source_id: UUID
    provider: ProviderCode
    kind: SourceKind
    version: str
    display_name: str
    usage_constraints: str
    requires_secret: bool

    def __post_init__(self) -> None:
        if self.source_id.int == 0 or not _PROVIDER_PATTERN.fullmatch(self.version):
            raise ValueError("Source Provider definition is invalid")
        _require_text(self.display_name, field_name="Provider display name", maximum=100)
        _require_text(self.usage_constraints, field_name="Usage constraints", maximum=1_000)


SOURCE_DEFINITIONS: Final = (
    SourceProviderDefinition(
        WORLD_BANK_NEWS_SOURCE_ID,
        ProviderCode.WORLD_BANK_NEWS,
        SourceKind.NEWS,
        "api-v2-2026-08",
        "World Bank News",
        "Public World Bank news metadata; preserve attribution and original link.",
        False,
    ),
    SourceProviderDefinition(
        FEDERAL_REGISTER_SOURCE_ID,
        ProviderCode.FEDERAL_REGISTER,
        SourceKind.POLICY,
        "api-v1-2026-08",
        "Federal Register",
        "United States government public metadata; preserve agency and document link.",
        False,
    ),
    SourceProviderDefinition(
        TED_SOURCE_ID,
        ProviderCode.TED,
        SourceKind.TENDER,
        "api-v3-2026-08",
        "Tenders Electronic Daily",
        "EU public procurement metadata; preserve publication number and TED link.",
        False,
    ),
    SourceProviderDefinition(
        ALPHA_VANTAGE_SOURCE_ID,
        ProviderCode.ALPHA_VANTAGE,
        SourceKind.STOCK,
        "global-quote-v1",
        "Alpha Vantage",
        "Market data is informational and may be delayed; preserve source and observation time.",
        True,
    ),
)
SOURCE_DEFINITIONS_BY_PROVIDER: Final = MappingProxyType(
    {definition.provider: definition for definition in SOURCE_DEFINITIONS}
)
SOURCE_DEFINITIONS_BY_KIND: Final = MappingProxyType(
    {definition.kind: definition for definition in SOURCE_DEFINITIONS}
)


@dataclass(frozen=True, slots=True)
class ProviderQuery:
    industry: IndustryPreset
    query: str
    limit: int = 10
    cursor: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.query, field_name="Provider query", maximum=200)
        if isinstance(self.limit, bool) or not 1 <= self.limit <= MAX_SOURCE_ITEMS_PER_PAGE:
            raise ValueError("Provider query limit is invalid")
        if self.cursor is not None:
            _require_text(self.cursor, field_name="Provider cursor", maximum=512)


@dataclass(frozen=True, slots=True)
class ProviderItem:
    kind: SourceKind
    provider: ProviderCode
    external_id: str
    title: str
    summary: str
    locator: str
    published_at: datetime
    metadata: Mapping[str, object] = field(repr=False)

    def __post_init__(self) -> None:
        if not _EXTERNAL_ID_PATTERN.fullmatch(self.external_id):
            raise ValueError("Source external ID is invalid")
        _require_text(self.title, field_name="Source title", maximum=1_000)
        _require_text(self.summary, field_name="Source summary", maximum=10_000)
        object.__setattr__(self, "locator", canonical_public_locator(self.locator))
        require_utc(self.published_at, field_name="Source publication time")
        object.__setattr__(self, "metadata", _snapshot_metadata(self.metadata))

    @property
    def content_sha256(self) -> str:
        return canonical_content_sha256(
            {
                "external_id": self.external_id,
                "kind": self.kind.value,
                "locator": self.locator,
                "metadata": dict(self.metadata),
                "provider": self.provider.value,
                "published_at": self.published_at.isoformat(),
                "summary": self.summary,
                "title": self.title,
            }
        )


@dataclass(frozen=True, slots=True)
class ProviderPage:
    definition: SourceProviderDefinition
    items: tuple[ProviderItem, ...]
    next_cursor: str | None
    fetched_at: datetime

    def __post_init__(self) -> None:
        items = tuple(self.items)
        if len(items) > MAX_SOURCE_ITEMS_PER_PAGE or any(
            item.kind is not self.definition.kind or item.provider is not self.definition.provider
            for item in items
        ):
            raise ValueError("Provider page is invalid")
        if self.next_cursor is not None:
            _require_text(self.next_cursor, field_name="Provider cursor", maximum=512)
        require_utc(self.fetched_at, field_name="Provider fetch time")
        object.__setattr__(self, "items", items)


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    provider: ProviderCode
    kind: SourceKind
    readiness: ProviderReadiness
    reason_code: ProviderErrorCode | None

    def __post_init__(self) -> None:
        if (self.readiness is ProviderReadiness.READY) == (self.reason_code is not None):
            raise ValueError("Provider status is inconsistent")


@dataclass(frozen=True, slots=True)
class CollectionRunRequest:
    collection_run_id: UUID
    job_id: UUID
    workspace_id: UUID
    industry_id: UUID
    kind: SourceKind
    query: str
    trace_id: TraceId

    def __post_init__(self) -> None:
        if any(
            identifier.int == 0
            for identifier in (
                self.collection_run_id,
                self.job_id,
                self.workspace_id,
                self.industry_id,
            )
        ):
            raise ValueError("Collection Run request identity is invalid")
        _require_text(self.query, field_name="Collection query", maximum=200)


@dataclass(frozen=True, slots=True)
class CollectionResult:
    collection_run_id: UUID
    provider: ProviderCode
    fetched_count: int
    inserted_count: int
    duplicate_count: int
    next_cursor: str | None

    def __post_init__(self) -> None:
        if (
            self.collection_run_id.int == 0
            or min(self.fetched_count, self.inserted_count, self.duplicate_count) < 0
            or self.inserted_count + self.duplicate_count != self.fetched_count
        ):
            raise ValueError("Collection result is invalid")


@dataclass(frozen=True, slots=True)
class CollectionScheduleRequest:
    scope: WorkspaceScope
    industry_id: UUID
    kind: SourceKind
    cron_expression: str
    timezone_name: str
    misfire_policy: ScheduleMisfirePolicy
    catch_up_window_seconds: int
    max_catch_up: int


@dataclass(frozen=True, slots=True)
class SourceItemSummary:
    source_item_id: UUID
    industry_id: UUID
    kind: SourceKind
    provider: ProviderCode
    external_id: str
    title: str
    summary: str
    locator: str
    published_at: datetime
    collected_at: datetime
    content_sha256: str
    metadata: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.source_item_id.int == 0 or self.industry_id.int == 0:
            raise ValueError("Source item identity is invalid")
        canonical_public_locator(self.locator)
        require_utc(self.published_at, field_name="Source publication time")
        require_utc(self.collected_at, field_name="Source collection time")
        if not _SHA256_PATTERN.fullmatch(self.content_sha256):
            raise ValueError("Source item hash is invalid")
        object.__setattr__(self, "metadata", _snapshot_metadata(self.metadata))


@dataclass(frozen=True, slots=True)
class CollectionStatusSummary:
    collection_run_id: UUID
    industry_id: UUID
    kind: SourceKind
    provider: ProviderCode
    status: CollectionRunStatus
    scheduled_for: datetime | None
    started_at: datetime | None
    terminal_at: datetime | None
    last_error_code: str | None
    fetched_count: int
    inserted_count: int
    duplicate_count: int


@dataclass(frozen=True, slots=True)
class CollectionScheduleSummary:
    schedule_id: UUID
    industry_id: UUID
    kind: SourceKind
    cron_expression: str
    timezone_name: str
    next_due_at: datetime | None
    last_fired_at: datetime | None
    enabled: bool
    misfire_policy: ScheduleMisfirePolicy
    misfire_error_code: str | None


def parse_utc_date(value: str, *, field_name: str) -> datetime:
    """Parse one date-only Provider field at UTC midnight."""

    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} is invalid") from None
    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=UTC)


def parse_market_price(value: str) -> str:
    """Normalize a positive decimal without introducing binary-float drift."""

    try:
        price = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Market price is invalid") from None
    if not price.is_finite() or price <= 0 or price > Decimal("1000000000"):
        raise ValueError("Market price is invalid")
    return format(price.normalize(), "f")


def provider_for_kind(kind: SourceKind) -> SourceProviderDefinition:
    return SOURCE_DEFINITIONS_BY_KIND[kind]


def require_industry(industry_id: UUID) -> IndustryPreset:
    industry = INDUSTRY_PRESETS_BY_ID.get(industry_id)
    if industry is None:
        raise IndustryNotFoundError
    return industry


def search_industries(query: str | None) -> tuple[IndustryPreset, ...]:
    if query is None:
        return INDUSTRY_PRESETS
    normalized = query.strip().casefold()
    if not normalized or len(normalized) > 100:
        raise ValueError("Industry search query is invalid")
    return tuple(
        preset
        for preset in INDUSTRY_PRESETS
        if normalized in preset.name.casefold()
        or normalized in preset.code.casefold()
        or normalized in preset.default_query.casefold()
    )


def unique_provider_items(values: Sequence[ProviderItem]) -> tuple[ProviderItem, ...]:
    """Reject ambiguous duplicate facts inside one external response."""

    items = tuple(values)
    identities = {(item.provider, item.external_id) for item in items}
    if len(identities) != len(items):
        raise ValueError("Provider response contains duplicate external IDs")
    return items
