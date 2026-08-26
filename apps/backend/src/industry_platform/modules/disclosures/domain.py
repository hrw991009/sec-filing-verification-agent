"""Domain contracts for SEC filer identity discovery."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final

from industry_platform.modules.agent_runtime.domain import require_utc

SEC_COMPANY_TICKERS_URL: Final = "https://www.sec.gov/files/company_tickers.json"
SEC_COMPANY_TICKERS_SOURCE_KIND: Final = "company_tickers"
SEC_API_CIK_BATCH_LIMIT: Final = 100
SEC_DEFAULT_REQUESTS_PER_SECOND: Final = 8
SEC_MAX_REQUESTS_PER_SECOND: Final = 9
SEC_MAX_FILER_CANDIDATES: Final = 10
SEC_MAX_CATALOG_FILERS: Final = 20_000
SEC_MAX_CATALOG_RESPONSE_BYTES: Final = 5_000_000

_CIK_PATTERN = re.compile(r"^[0-9]{10}$")
_TICKER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,19}$")
_SOURCE_VERSION_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class SecAliasKind(StrEnum):
    NAME = "name"
    TICKER = "ticker"


class SecFilerMatchKind(StrEnum):
    CIK = "cik"
    TICKER = "ticker"
    NAME_EXACT = "name_exact"
    NAME_PREFIX = "name_prefix"
    NAME_CONTAINS = "name_contains"


class SecFilerResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    NO_RESULT = "no_result"


class SecFetchMode(StrEnum):
    API = "api"
    BULK = "bulk"


class SecSourceErrorCode(StrEnum):
    NOT_CONFIGURED = "sec_source_not_configured"
    RATE_LIMIT_UNAVAILABLE = "sec_rate_limit_unavailable"
    CACHE_UNAVAILABLE = "sec_cache_unavailable"
    RATE_LIMITED = "sec_rate_limited"
    TIMEOUT = "sec_timeout"
    UPSTREAM_ERROR = "sec_upstream_error"
    REDIRECT_REJECTED = "sec_redirect_rejected"
    CONTENT_TYPE_INVALID = "sec_content_type_invalid"
    RESPONSE_TOO_LARGE = "sec_response_too_large"
    RESPONSE_INVALID = "sec_response_invalid"


class SecSourceError(RuntimeError):
    """Sanitized failure from the official SEC source boundary."""

    def __init__(
        self,
        code: SecSourceErrorCode,
        *,
        retryable: bool,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__("SEC source request failed")
        if retry_after_seconds is not None and not 0 <= retry_after_seconds <= 60:
            raise ValueError("SEC retry delay is invalid")
        self.code = code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


class SecDisclosurePersistenceError(RuntimeError):
    """Sanitized failure while updating or reading the canonical filer catalog."""

    def __init__(self, *, sqlstate: str | None = None) -> None:
        super().__init__("SEC disclosure persistence failed")
        self.sqlstate = sqlstate


@dataclass(frozen=True, slots=True)
class SecFilerAlias:
    kind: SecAliasKind
    display_value: str
    normalized_value: str
    source_kind: str
    source_version: str
    source_url: str
    content_sha256: str
    observed_at: datetime
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    def __post_init__(self) -> None:
        _require_text(self.display_value, field_name="SEC alias", maximum=500)
        _require_text(self.normalized_value, field_name="SEC normalized alias", maximum=500)
        _require_source_identity(
            self.source_kind,
            self.source_version,
            self.source_url,
            self.content_sha256,
        )
        require_utc(self.observed_at, field_name="SEC alias observed_at")
        if self.valid_from is not None:
            require_utc(self.valid_from, field_name="SEC alias valid_from")
        if self.valid_to is not None:
            require_utc(self.valid_to, field_name="SEC alias valid_to")
        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_to <= self.valid_from
        ):
            raise ValueError("SEC alias validity interval is invalid")


@dataclass(frozen=True, slots=True)
class SecFiler:
    cik: str
    canonical_name: str
    normalized_name: str
    aliases: tuple[SecFilerAlias, ...]
    source_kind: str
    source_version: str
    source_url: str
    content_sha256: str
    observed_at: datetime

    def __post_init__(self) -> None:
        if not _CIK_PATTERN.fullmatch(self.cik):
            raise ValueError("SEC filer CIK is invalid")
        _require_text(self.canonical_name, field_name="SEC filer name", maximum=500)
        _require_text(self.normalized_name, field_name="SEC normalized name", maximum=500)
        if not self.aliases:
            raise ValueError("SEC filer requires at least one alias")
        keys = {
            (alias.kind, alias.normalized_value, alias.source_version) for alias in self.aliases
        }
        if len(keys) != len(self.aliases):
            raise ValueError("SEC filer aliases must be unique")
        _require_source_identity(
            self.source_kind,
            self.source_version,
            self.source_url,
            self.content_sha256,
        )
        require_utc(self.observed_at, field_name="SEC filer observed_at")


@dataclass(frozen=True, slots=True)
class SecFilerCatalogSnapshot:
    source_kind: str
    source_version: str
    source_url: str
    content_sha256: str
    retrieved_at: datetime
    filers: tuple[SecFiler, ...]

    def __post_init__(self) -> None:
        _require_source_identity(
            self.source_kind,
            self.source_version,
            self.source_url,
            self.content_sha256,
        )
        require_utc(self.retrieved_at, field_name="SEC catalog retrieved_at")
        if not 1 <= len(self.filers) <= SEC_MAX_CATALOG_FILERS:
            raise ValueError("SEC filer catalog size is invalid")
        if len({filer.cik for filer in self.filers}) != len(self.filers):
            raise ValueError("SEC filer catalog contains duplicate CIKs")
        if any(
            filer.source_version != self.source_version
            or filer.source_url != self.source_url
            or filer.content_sha256 != self.content_sha256
            for filer in self.filers
        ):
            raise ValueError("SEC filer catalog source identity is inconsistent")


@dataclass(frozen=True, slots=True)
class SecFilerCandidate:
    cik: str
    canonical_name: str
    tickers: tuple[str, ...]
    matched_by: SecFilerMatchKind
    matched_value: str
    confidence: float
    source_version: str
    source_url: str
    content_sha256: str
    source_observed_at: datetime
    alias_valid_from: datetime | None = None
    alias_valid_to: datetime | None = None

    def __post_init__(self) -> None:
        if not _CIK_PATTERN.fullmatch(self.cik):
            raise ValueError("SEC candidate CIK is invalid")
        if not 0 <= self.confidence <= 1:
            raise ValueError("SEC candidate confidence is invalid")
        if tuple(sorted(set(self.tickers))) != self.tickers:
            raise ValueError("SEC candidate tickers must be unique and sorted")
        _require_source_identity(
            SEC_COMPANY_TICKERS_SOURCE_KIND,
            self.source_version,
            self.source_url,
            self.content_sha256,
        )
        require_utc(self.source_observed_at, field_name="SEC candidate observed_at")


@dataclass(frozen=True, slots=True)
class SecFilerResolution:
    status: SecFilerResolutionStatus
    query: str
    normalized_query: str
    candidates: tuple[SecFilerCandidate, ...]
    catalog_source_version: str
    catalog_content_sha256: str
    catalog_retrieved_at: datetime

    def __post_init__(self) -> None:
        _require_text(self.query, field_name="SEC filer query", maximum=200)
        _require_text(self.normalized_query, field_name="SEC normalized query", maximum=200)
        if len(self.candidates) > SEC_MAX_FILER_CANDIDATES:
            raise ValueError("SEC filer candidate count is invalid")
        if self.status is SecFilerResolutionStatus.NO_RESULT and self.candidates:
            raise ValueError("SEC no-result response cannot contain candidates")
        if self.status is SecFilerResolutionStatus.RESOLVED and len(self.candidates) != 1:
            raise ValueError("SEC resolved response requires exactly one candidate")
        if self.status is SecFilerResolutionStatus.AMBIGUOUS and not self.candidates:
            raise ValueError("SEC ambiguous response requires candidates")
        if not _SOURCE_VERSION_PATTERN.fullmatch(self.catalog_source_version):
            raise ValueError("SEC catalog version is invalid")
        if not _SHA256_PATTERN.fullmatch(self.catalog_content_sha256):
            raise ValueError("SEC catalog hash is invalid")
        require_utc(self.catalog_retrieved_at, field_name="SEC catalog retrieved_at")


def normalize_cik(value: str | int) -> str:
    if isinstance(value, bool):
        raise ValueError("CIK is invalid")
    text = str(value).strip()
    if not text.isascii() or not text.isdigit() or not 1 <= len(text) <= 10:
        raise ValueError("CIK is invalid")
    normalized = text.zfill(10)
    if normalized == "0000000000":
        raise ValueError("CIK is invalid")
    return normalized


def normalize_ticker(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().upper()
    if not _TICKER_PATTERN.fullmatch(normalized):
        raise ValueError("Ticker is invalid")
    return normalized


def normalize_filer_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = " ".join(
        "".join(character if character.isalnum() else " " for character in normalized).split()
    )
    if not 1 <= len(normalized) <= 500:
        raise ValueError("Filer name is invalid")
    return normalized


def normalize_filer_query(value: str) -> tuple[str, str | None, str | None]:
    query = " ".join(unicodedata.normalize("NFKC", value).strip().split())
    if not 1 <= len(query) <= 200 or any(ord(character) < 32 for character in query):
        raise ValueError("SEC filer query is invalid")
    cik = None
    if query.isascii() and query.isdigit():
        cik = normalize_cik(query)
    ticker = None
    with suppress(ValueError):
        ticker = normalize_ticker(query)
    return normalize_filer_name(query), cik, ticker


def plan_sec_cik_fetch(
    ciks: tuple[str, ...],
    *,
    full_refresh: bool = False,
) -> SecFetchMode:
    normalized = tuple(dict.fromkeys(normalize_cik(cik) for cik in ciks))
    if not normalized and not full_refresh:
        raise ValueError("SEC CIK request is empty")
    return (
        SecFetchMode.BULK
        if full_refresh or len(normalized) >= SEC_API_CIK_BATCH_LIMIT
        else SecFetchMode.API
    )


def catalog_source_version(content_sha256: str) -> str:
    if not _SHA256_PATTERN.fullmatch(content_sha256):
        raise ValueError("SEC catalog hash is invalid")
    return f"sec-company-tickers-{content_sha256[:24]}"


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_text(value: str, *, field_name: str, maximum: int) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError(f"{field_name} is invalid")


def _require_source_identity(
    source_kind: str,
    source_version: str,
    source_url: str,
    content_sha256: str,
) -> None:
    if source_kind != SEC_COMPANY_TICKERS_SOURCE_KIND:
        raise ValueError("SEC source kind is invalid")
    if not _SOURCE_VERSION_PATTERN.fullmatch(source_version):
        raise ValueError("SEC source version is invalid")
    if source_url != SEC_COMPANY_TICKERS_URL:
        raise ValueError("SEC source URL is invalid")
    if not _SHA256_PATTERN.fullmatch(content_sha256):
        raise ValueError("SEC source hash is invalid")
