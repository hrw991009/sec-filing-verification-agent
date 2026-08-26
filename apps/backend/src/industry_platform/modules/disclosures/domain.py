"""Domain contracts for SEC filer identity discovery."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from types import MappingProxyType
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
SEC_MAX_SUBMISSIONS_RESPONSE_BYTES: Final = 15_000_000
SEC_MAX_SUBMISSION_FILINGS: Final = 20_000
SEC_MAX_SUPPLEMENTAL_FILES: Final = 100
SEC_MAX_FILING_CANDIDATES: Final = 100
FILING_SELECTION_SCOPE_SCHEMA_VERSION: Final = 1
SEC_VISIBILITY_POLICY_VERSION: Final = "sec-acceptance-source-v1"
SEC_SUBMISSIONS_CURRENT_SOURCE_KIND: Final = "submissions_current"
SEC_SUBMISSIONS_SUPPLEMENTAL_SOURCE_KIND: Final = "submissions_supplemental"
SEC_SUBMISSIONS_URL_PREFIX: Final = "https://data.sec.gov/submissions/"

_CIK_PATTERN = re.compile(r"^[0-9]{10}$")
_TICKER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,19}$")
_SOURCE_VERSION_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_ACCESSION_PATTERN = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
_PRIMARY_DOCUMENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
_SUPPLEMENTAL_NAME_PATTERN = re.compile(
    r"^CIK(?P<cik>[0-9]{10})-submissions-(?P<ordinal>[0-9]{3})\.json$"
)


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


class SecFilingForm(StrEnum):
    TEN_K = "10-K"
    TEN_K_AMENDMENT = "10-K/A"
    TEN_Q = "10-Q"
    TEN_Q_AMENDMENT = "10-Q/A"


class SecAmendmentPolicy(StrEnum):
    AS_FILED = "as_filed"
    LATEST_KNOWN_BY_AS_OF = "latest_amendment_known_by_as_of"


class SecSubmissionSourceKind(StrEnum):
    CURRENT = SEC_SUBMISSIONS_CURRENT_SOURCE_KIND
    SUPPLEMENTAL = SEC_SUBMISSIONS_SUPPLEMENTAL_SOURCE_KIND


class SecFilingSelectionStatus(StrEnum):
    OK = "ok"
    NO_RESULT = "no_result"
    INCOMPLETE = "incomplete"


class SecAmendmentRelationStatus(StrEnum):
    NOT_AMENDMENT = "not_amendment"
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"


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
    COVERAGE_INCOMPLETE = "sec_coverage_incomplete"
    SNAPSHOT_STORE_UNAVAILABLE = "sec_snapshot_store_unavailable"


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


@dataclass(frozen=True, slots=True)
class FilingSelectionScope:
    cik: str
    allowed_forms: tuple[SecFilingForm, ...]
    report_period_start: date
    report_period_end: date
    as_of: datetime
    amendment_policy: SecAmendmentPolicy
    schema_version: int = FILING_SELECTION_SCOPE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != FILING_SELECTION_SCOPE_SCHEMA_VERSION:
            raise ValueError("Filing Selection Scope schema version is unsupported")
        if not _CIK_PATTERN.fullmatch(self.cik):
            raise ValueError("Filing Selection Scope CIK is invalid")
        forms = tuple(self.allowed_forms)
        if (
            not forms
            or len(forms) != len(set(forms))
            or any(not isinstance(form, SecFilingForm) for form in forms)
            or forms != tuple(sorted(forms, key=lambda item: item.value))
        ):
            raise ValueError("Filing Selection Scope forms are invalid")
        if self.report_period_end < self.report_period_start:
            raise ValueError("Filing Selection Scope report period is invalid")
        if (self.report_period_end - self.report_period_start).days > 3_660:
            raise ValueError("Filing Selection Scope report period is too broad")
        require_utc(self.as_of, field_name="Filing Selection Scope as_of")
        if not isinstance(self.amendment_policy, SecAmendmentPolicy):
            raise ValueError("Filing Selection Scope amendment policy is invalid")
        object.__setattr__(self, "allowed_forms", forms)

    def to_mapping(self) -> MappingProxyType[str, object]:
        return MappingProxyType(
            {
                "schema_version": self.schema_version,
                "cik": self.cik,
                "allowed_forms": [form.value for form in self.allowed_forms],
                "report_period_start": self.report_period_start.isoformat(),
                "report_period_end": self.report_period_end.isoformat(),
                "as_of": self.as_of.isoformat(),
                "amendment_policy": self.amendment_policy.value,
            }
        )

    @classmethod
    def from_mapping(cls, value: dict[str, object]) -> FilingSelectionScope:
        expected = {
            "schema_version",
            "cik",
            "allowed_forms",
            "report_period_start",
            "report_period_end",
            "as_of",
            "amendment_policy",
        }
        if set(value) != expected:
            raise ValueError("Filing Selection Scope fields are invalid")
        schema_version = value["schema_version"]
        raw_forms = value["allowed_forms"]
        if (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or not isinstance(raw_forms, list)
            or any(not isinstance(form, str) for form in raw_forms)
            or not isinstance(value["cik"], str)
            or not isinstance(value["report_period_start"], str)
            or not isinstance(value["report_period_end"], str)
            or not isinstance(value["as_of"], str)
            or not isinstance(value["amendment_policy"], str)
        ):
            raise ValueError("Filing Selection Scope values are invalid")
        try:
            return cls(
                schema_version=schema_version,
                cik=str(value["cik"]),
                allowed_forms=tuple(
                    sorted(
                        (SecFilingForm(form) for form in raw_forms),
                        key=lambda item: item.value,
                    )
                ),
                report_period_start=date.fromisoformat(str(value["report_period_start"])),
                report_period_end=date.fromisoformat(str(value["report_period_end"])),
                as_of=datetime.fromisoformat(str(value["as_of"])),
                amendment_policy=SecAmendmentPolicy(str(value["amendment_policy"])),
            )
        except (TypeError, ValueError):
            raise ValueError("Filing Selection Scope is invalid") from None


@dataclass(frozen=True, slots=True)
class SecSupplementalDescriptor:
    name: str
    filing_from: date
    filing_to: date
    filing_count: int

    def __post_init__(self) -> None:
        if _SUPPLEMENTAL_NAME_PATTERN.fullmatch(self.name) is None:
            raise ValueError("SEC supplemental source name is invalid")
        if self.filing_to < self.filing_from:
            raise ValueError("SEC supplemental filing range is invalid")
        if isinstance(self.filing_count, bool) or not 0 < self.filing_count <= 100_000:
            raise ValueError("SEC supplemental filing count is invalid")

    @property
    def source_url(self) -> str:
        return f"{SEC_SUBMISSIONS_URL_PREFIX}{self.name}"


@dataclass(frozen=True, slots=True)
class SecFilingObservation:
    cik: str
    accession: str
    form: SecFilingForm
    report_date: date
    filed_date: date
    accepted_at: datetime
    primary_document: str

    def __post_init__(self) -> None:
        if not _CIK_PATTERN.fullmatch(self.cik):
            raise ValueError("SEC filing CIK is invalid")
        if not _ACCESSION_PATTERN.fullmatch(self.accession):
            raise ValueError("SEC filing accession is invalid")
        if not isinstance(self.form, SecFilingForm):
            raise ValueError("SEC filing form is invalid")
        if self.filed_date < self.report_date:
            raise ValueError("SEC filing dates are invalid")
        require_utc(self.accepted_at, field_name="SEC filing accepted_at")
        if self.accepted_at.date() < self.filed_date:
            raise ValueError("SEC filing acceptance time is invalid")
        if _PRIMARY_DOCUMENT_PATTERN.fullmatch(self.primary_document) is None:
            raise ValueError("SEC filing primary document is invalid")


@dataclass(frozen=True, slots=True)
class SecSubmissionSourceSnapshot:
    cik: str
    source_kind: SecSubmissionSourceKind
    source_name: str
    source_url: str
    source_version: str
    content_sha256: str
    retrieved_at: datetime
    source_available_at: datetime
    body: bytes
    filings: tuple[SecFilingObservation, ...]
    filing_from: date | None = None
    filing_to: date | None = None
    descriptors: tuple[SecSupplementalDescriptor, ...] = ()

    def __post_init__(self) -> None:
        if not _CIK_PATTERN.fullmatch(self.cik):
            raise ValueError("SEC submission source CIK is invalid")
        expected_current_name = f"CIK{self.cik}.json"
        if self.source_kind is SecSubmissionSourceKind.CURRENT:
            if (
                self.source_name != expected_current_name
                or self.filing_from is not None
                or self.filing_to is not None
                or self.descriptors != tuple(sorted(self.descriptors, key=lambda item: item.name))
            ):
                raise ValueError("SEC current submission source is invalid")
        else:
            match = _SUPPLEMENTAL_NAME_PATTERN.fullmatch(self.source_name)
            if match is None or match.group("cik") != self.cik or self.descriptors:
                raise ValueError("SEC supplemental submission source is invalid")
            if self.filing_from is None or self.filing_to is None:
                raise ValueError("SEC supplemental source coverage is missing")
        if self.source_url != f"{SEC_SUBMISSIONS_URL_PREFIX}{self.source_name}":
            raise ValueError("SEC submission source URL is invalid")
        if not _SOURCE_VERSION_PATTERN.fullmatch(self.source_version):
            raise ValueError("SEC submission source version is invalid")
        if not _SHA256_PATTERN.fullmatch(self.content_sha256):
            raise ValueError("SEC submission source hash is invalid")
        snapshot = bytes(self.body)
        if (
            not snapshot
            or len(snapshot) > SEC_MAX_SUBMISSIONS_RESPONSE_BYTES
            or sha256_hex(snapshot) != self.content_sha256
        ):
            raise ValueError("SEC submission source body is invalid")
        require_utc(self.retrieved_at, field_name="SEC submission retrieved_at")
        require_utc(self.source_available_at, field_name="SEC submission available_at")
        if self.source_available_at > self.retrieved_at:
            raise ValueError("SEC submission source availability is invalid")
        if (self.filing_from is None) != (self.filing_to is None):
            raise ValueError("SEC submission source coverage is invalid")
        if (
            self.filing_from is not None
            and self.filing_to is not None
            and self.filing_to < self.filing_from
        ):
            raise ValueError("SEC submission source coverage is invalid")
        filings = tuple(self.filings)
        if len(filings) > SEC_MAX_SUBMISSION_FILINGS:
            raise ValueError("SEC submission filing count is invalid")
        if len({filing.accession for filing in filings}) != len(filings):
            raise ValueError("SEC submission source contains duplicate accessions")
        if any(filing.cik != self.cik for filing in filings):
            raise ValueError("SEC submission filing CIK is inconsistent")
        if any(filing.accepted_at > self.source_available_at for filing in filings):
            raise ValueError("SEC submission filing is newer than its source version")
        descriptors = tuple(self.descriptors)
        if (
            len(descriptors) > SEC_MAX_SUPPLEMENTAL_FILES
            or len({descriptor.name for descriptor in descriptors}) != len(descriptors)
            or any(_supplemental_cik(descriptor.name) != self.cik for descriptor in descriptors)
        ):
            raise ValueError("SEC supplemental descriptors are invalid")
        object.__setattr__(self, "body", snapshot)
        object.__setattr__(self, "filings", filings)
        object.__setattr__(self, "descriptors", descriptors)


@dataclass(frozen=True, slots=True)
class SecSubmissionSet:
    current: SecSubmissionSourceSnapshot
    supplementals: tuple[SecSubmissionSourceSnapshot, ...]
    required_supplemental_names: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.current.source_kind is not SecSubmissionSourceKind.CURRENT:
            raise ValueError("SEC submission set current source is invalid")
        supplementals = tuple(self.supplementals)
        if any(
            source.source_kind is not SecSubmissionSourceKind.SUPPLEMENTAL
            or source.cik != self.current.cik
            for source in supplementals
        ):
            raise ValueError("SEC submission set supplemental source is invalid")
        required = tuple(sorted(set(self.required_supplemental_names)))
        actual = tuple(sorted(source.source_name for source in supplementals))
        if required != actual or required != tuple(self.required_supplemental_names):
            raise ValueError("SEC submission set coverage is incomplete")
        observations: dict[str, SecFilingObservation] = {}
        for source in (self.current, *supplementals):
            for filing in source.filings:
                existing = observations.get(filing.accession)
                if existing is not None and existing != filing:
                    raise ValueError("SEC submission sources conflict on one accession")
                observations[filing.accession] = filing
        object.__setattr__(self, "supplementals", supplementals)
        object.__setattr__(self, "required_supplemental_names", required)

    @property
    def sources(self) -> tuple[SecSubmissionSourceSnapshot, ...]:
        return (self.current, *self.supplementals)

    @property
    def filings(self) -> tuple[SecFilingObservation, ...]:
        by_accession: dict[str, SecFilingObservation] = {}
        for source in self.sources:
            for filing in source.filings:
                by_accession[filing.accession] = filing
        return tuple(sorted(by_accession.values(), key=lambda item: item.accession))


@dataclass(frozen=True, slots=True)
class SecFilingCandidate:
    cik: str
    accession: str
    form: SecFilingForm
    report_date: date
    filed_date: date
    accepted_at: datetime
    public_available_at: datetime
    primary_document: str
    amendment_relation_status: SecAmendmentRelationStatus
    base_accession: str | None
    source_version: str
    source_url: str
    content_sha256: str
    source_available_at: datetime

    def __post_init__(self) -> None:
        SecFilingObservation(
            cik=self.cik,
            accession=self.accession,
            form=self.form,
            report_date=self.report_date,
            filed_date=self.filed_date,
            accepted_at=self.accepted_at,
            primary_document=self.primary_document,
        )
        require_utc(self.public_available_at, field_name="SEC filing public_available_at")
        require_utc(self.source_available_at, field_name="SEC filing source_available_at")
        if self.public_available_at != self.accepted_at:
            raise ValueError("SEC filing visibility policy is invalid")
        if self.source_available_at < self.accepted_at:
            raise ValueError("SEC filing source predates the filing")
        if self.amendment_relation_status is SecAmendmentRelationStatus.RESOLVED:
            if self.base_accession is None or not _ACCESSION_PATTERN.fullmatch(self.base_accession):
                raise ValueError("SEC filing base accession is invalid")
        elif self.base_accession is not None:
            raise ValueError("SEC filing base accession is unexpected")
        if not _SOURCE_VERSION_PATTERN.fullmatch(self.source_version):
            raise ValueError("SEC filing source version is invalid")
        if not self.source_url.startswith(SEC_SUBMISSIONS_URL_PREFIX):
            raise ValueError("SEC filing source URL is invalid")
        if not _SHA256_PATTERN.fullmatch(self.content_sha256):
            raise ValueError("SEC filing source hash is invalid")


@dataclass(frozen=True, slots=True)
class SecSubmissionSourceReference:
    source_kind: SecSubmissionSourceKind
    source_version: str
    source_url: str
    content_sha256: str
    source_available_at: datetime
    retrieved_at: datetime

    def __post_init__(self) -> None:
        if not _SOURCE_VERSION_PATTERN.fullmatch(self.source_version):
            raise ValueError("SEC source reference version is invalid")
        if not self.source_url.startswith(SEC_SUBMISSIONS_URL_PREFIX):
            raise ValueError("SEC source reference URL is invalid")
        if not _SHA256_PATTERN.fullmatch(self.content_sha256):
            raise ValueError("SEC source reference hash is invalid")
        require_utc(self.source_available_at, field_name="SEC source reference available_at")
        require_utc(self.retrieved_at, field_name="SEC source reference retrieved_at")
        if self.source_available_at > self.retrieved_at:
            raise ValueError("SEC source reference availability is invalid")


@dataclass(frozen=True, slots=True)
class SecFilingSelection:
    status: SecFilingSelectionStatus
    scope: FilingSelectionScope
    filings: tuple[SecFilingCandidate, ...]
    coverage_version: str
    sources: tuple[SecSubmissionSourceReference, ...]
    error_code: str | None = None

    def __post_init__(self) -> None:
        filings = tuple(self.filings)
        if len(filings) > SEC_MAX_FILING_CANDIDATES:
            raise ValueError("SEC filing selection size is invalid")
        if self.status is SecFilingSelectionStatus.OK and not filings:
            raise ValueError("SEC filing selection requires candidates")
        if self.status is SecFilingSelectionStatus.NO_RESULT and filings:
            raise ValueError("SEC no-result filing selection cannot contain candidates")
        if self.status is SecFilingSelectionStatus.INCOMPLETE and self.error_code is None:
            raise ValueError("SEC incomplete filing selection requires an error")
        if self.status is SecFilingSelectionStatus.INCOMPLETE and filings:
            raise ValueError("SEC incomplete filing selection cannot contain candidates")
        if self.status is not SecFilingSelectionStatus.INCOMPLETE and self.error_code is not None:
            raise ValueError("SEC filing selection error is unexpected")
        if not _SOURCE_VERSION_PATTERN.fullmatch(self.coverage_version):
            raise ValueError("SEC filing coverage version is invalid")
        sources = tuple(self.sources)
        if not sources or len({source.source_url for source in sources}) != len(sources):
            raise ValueError("SEC filing coverage sources are invalid")
        object.__setattr__(self, "filings", filings)
        object.__setattr__(self, "sources", sources)


@dataclass(frozen=True, slots=True)
class SecFilingDataset:
    coverage_version: str
    scope: FilingSelectionScope
    filings: tuple[SecFilingCandidate, ...]
    sources: tuple[SecSubmissionSourceReference, ...]

    def __post_init__(self) -> None:
        if not _SOURCE_VERSION_PATTERN.fullmatch(self.coverage_version):
            raise ValueError("SEC filing dataset coverage version is invalid")
        filings = tuple(self.filings)
        if (
            len(filings) > SEC_MAX_SUBMISSION_FILINGS
            or len({filing.accession for filing in filings}) != len(filings)
            or any(filing.cik != self.scope.cik for filing in filings)
        ):
            raise ValueError("SEC filing dataset is invalid")
        sources = tuple(self.sources)
        if not sources or len({source.source_url for source in sources}) != len(sources):
            raise ValueError("SEC filing dataset sources are invalid")
        object.__setattr__(self, "filings", filings)
        object.__setattr__(self, "sources", sources)


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


def sec_submissions_current_url(cik: str) -> str:
    if not _CIK_PATTERN.fullmatch(cik):
        raise ValueError("SEC submissions CIK is invalid")
    return f"{SEC_SUBMISSIONS_URL_PREFIX}CIK{cik}.json"


def sec_submissions_source_version(
    source_kind: SecSubmissionSourceKind,
    content_sha256: str,
) -> str:
    if not _SHA256_PATTERN.fullmatch(content_sha256):
        raise ValueError("SEC submissions hash is invalid")
    suffix = "current" if source_kind is SecSubmissionSourceKind.CURRENT else "supplemental"
    return f"sec-submissions-{suffix}-{content_sha256[:24]}"


def sec_submission_object_key(source: SecSubmissionSourceSnapshot) -> str:
    kind = "current" if source.source_kind is SecSubmissionSourceKind.CURRENT else "history"
    return (
        f"sec/submissions/{source.cik}/{kind}/"
        f"{source.content_sha256[:2]}/{source.content_sha256}.json"
    )


def required_supplemental_descriptors(
    descriptors: tuple[SecSupplementalDescriptor, ...],
    scope: FilingSelectionScope,
) -> tuple[SecSupplementalDescriptor, ...]:
    """Select every filing-date partition that can contain an in-scope report."""

    filing_window_start = scope.report_period_start
    filing_window_end = scope.as_of.date()
    if filing_window_end < filing_window_start:
        return ()
    return tuple(
        descriptor
        for descriptor in descriptors
        if descriptor.filing_from <= filing_window_end
        and descriptor.filing_to >= filing_window_start
    )


def _supplemental_cik(name: str) -> str:
    match = _SUPPLEMENTAL_NAME_PATTERN.fullmatch(name)
    if match is None:
        raise ValueError("SEC supplemental source name is invalid")
    return match.group("cik")


def base_form(form: SecFilingForm) -> SecFilingForm:
    if form is SecFilingForm.TEN_K_AMENDMENT:
        return SecFilingForm.TEN_K
    if form is SecFilingForm.TEN_Q_AMENDMENT:
        return SecFilingForm.TEN_Q
    return form


def is_amendment(form: SecFilingForm) -> bool:
    return form in {
        SecFilingForm.TEN_K_AMENDMENT,
        SecFilingForm.TEN_Q_AMENDMENT,
    }


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
