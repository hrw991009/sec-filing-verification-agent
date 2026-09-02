"""Domain contracts for SEC filer identity discovery."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Final
from uuid import UUID

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
SEC_ARCHIVE_URL_PREFIX: Final = "https://www.sec.gov/Archives/edgar/data/"
SEC_MAX_ARCHIVE_DOCUMENT_BYTES: Final = 50 * 1_024 * 1_024
SEC_MAX_ARCHIVE_ATTACHMENTS: Final = 12
SEC_MAX_ARCHIVE_TOTAL_BYTES: Final = 128 * 1_024 * 1_024
SEC_FILING_CONTENT_ADAPTER_VERSION: Final = "sec-archive-v1"
SEC_DENSE_RETRIEVAL_PROFILE_VERSION: Final = "dense-v1"
SEC_HYBRID_RETRIEVAL_PROFILE_VERSION: Final = "hybrid-v1"
SEC_IDENTITY_RERANKER_VERSION: Final = "identity-reranker-v1"
SEC_COMPANYFACTS_URL_PREFIX: Final = "https://data.sec.gov/api/xbrl/companyfacts/"
SEC_MAX_XBRL_RESPONSE_BYTES: Final = 50 * 1_024 * 1_024
SEC_MAX_XBRL_CONTEXTS: Final = 100_000
SEC_MAX_XBRL_FACTS: Final = 200_000
SEC_MAX_XBRL_FACT_VALUE_CHARACTERS: Final = 20_000
SEC_XBRL_ADAPTER_VERSION: Final = "sec-xbrl-v1"

_CIK_PATTERN = re.compile(r"^[0-9]{10}$")
_TICKER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,19}$")
_SOURCE_VERSION_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_ACCESSION_PATTERN = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
_PRIMARY_DOCUMENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
_XBRL_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9._-]{0,255}$")
_XBRL_QNAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9._-]{0,127}:[A-Za-z_][A-Za-z0-9._-]{0,255}$")
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


class SecFilingDocumentKind(StrEnum):
    COMPLETE_SUBMISSION = "complete_submission"
    PRIMARY_DOCUMENT = "primary_document"
    XBRL_INSTANCE = "xbrl_instance"
    XBRL_ATTACHMENT = "xbrl_attachment"


class SecFilingSnapshotStatus(StrEnum):
    ACTIVE = "active"
    QUARANTINED = "quarantined"


class SecFilingImportStatus(StrEnum):
    QUEUED = "queued"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SecFilingContentStatus(StrEnum):
    OK = "ok"
    NOT_READY = "not_ready"
    NO_RESULT = "no_result"
    DEPENDENCY_FAILED = "dependency_failed"
    PERMISSION_DENIED = "permission_denied"


class SecXbrlSourceKind(StrEnum):
    COMPANYFACTS_AGGREGATE = "companyfacts_aggregate"
    RAW_INLINE = "raw_inline"
    RAW_INSTANCE = "raw_instance"


class SecXbrlPeriodKind(StrEnum):
    INSTANT = "instant"
    DURATION = "duration"
    FOREVER = "forever"


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
    BULK_WATERMARK_INVALID = "sec_bulk_watermark_invalid"
    BULK_ARCHIVE_PARTIAL = "sec_bulk_archive_partial"
    BULK_ARCHIVE_INVALID = "sec_bulk_archive_invalid"
    BULK_ENTRY_MISSING = "sec_bulk_entry_missing"
    COVERAGE_INCOMPLETE = "sec_coverage_incomplete"
    SNAPSHOT_STORE_UNAVAILABLE = "sec_snapshot_store_unavailable"
    FILING_NOT_FOUND = "sec_filing_not_found"
    SNAPSHOT_ANOMALY = "sec_snapshot_anomaly"
    SNAPSHOT_NOT_VISIBLE = "sec_snapshot_not_visible"
    IMPORT_NOT_READY = "sec_import_not_ready"


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


class SecFilingContentError(RuntimeError):
    """Stable business failure while synchronizing or reading locked filing content."""

    def __init__(self, code: SecSourceErrorCode) -> None:
        super().__init__("SEC filing content operation failed")
        self.code = code


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
        # EDGAR can assign the next filing date to an evening acceptance.
        if self.accepted_at.date() not in {
            self.filed_date,
            self.filed_date - timedelta(days=1),
        }:
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


@dataclass(frozen=True, slots=True)
class SecCanonicalFiling:
    id: UUID
    cik: str
    accession: str
    form: SecFilingForm
    report_date: date
    filed_date: date
    accepted_at: datetime
    public_available_at: datetime
    primary_document: str
    source_available_at: datetime

    def __post_init__(self) -> None:
        if self.id.int == 0:
            raise ValueError("SEC canonical filing ID is invalid")
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
        if (
            self.public_available_at != self.accepted_at
            or self.source_available_at < self.accepted_at
        ):
            raise ValueError("SEC canonical filing visibility is invalid")


@dataclass(frozen=True, slots=True)
class SecFilingDocumentSnapshot:
    kind: SecFilingDocumentKind
    cik: str
    accession: str
    filename: str
    source_url: str
    source_version: str
    content_type: str
    content_sha256: str
    byte_size: int
    retrieved_at: datetime
    source_available_at: datetime
    body: bytes = field(repr=False)
    adapter_version: str = SEC_FILING_CONTENT_ADAPTER_VERSION

    def __post_init__(self) -> None:
        if not _CIK_PATTERN.fullmatch(self.cik) or not _ACCESSION_PATTERN.fullmatch(self.accession):
            raise ValueError("SEC filing document identity is invalid")
        if _PRIMARY_DOCUMENT_PATTERN.fullmatch(self.filename) is None:
            raise ValueError("SEC filing document filename is invalid")
        expected_url = (
            sec_complete_submission_url(self.cik, self.accession)
            if self.kind is SecFilingDocumentKind.COMPLETE_SUBMISSION
            else sec_filing_document_url(self.cik, self.accession, self.filename)
        )
        if self.source_url != expected_url:
            raise ValueError("SEC filing document URL is invalid")
        if not _SOURCE_VERSION_PATTERN.fullmatch(self.source_version):
            raise ValueError("SEC filing document version is invalid")
        if self.content_type not in {
            "text/plain",
            "text/html",
            "application/xhtml+xml",
            "application/xml",
            "text/xml",
        }:
            raise ValueError("SEC filing document content type is invalid")
        snapshot = bytes(self.body)
        if (
            isinstance(self.byte_size, bool)
            or self.byte_size != len(snapshot)
            or not 1 <= self.byte_size <= SEC_MAX_ARCHIVE_DOCUMENT_BYTES
            or not _SHA256_PATTERN.fullmatch(self.content_sha256)
            or sha256_hex(snapshot) != self.content_sha256
        ):
            raise ValueError("SEC filing document bytes are invalid")
        require_utc(self.retrieved_at, field_name="SEC filing document retrieved_at")
        require_utc(self.source_available_at, field_name="SEC filing document available_at")
        if self.source_available_at > self.retrieved_at or not _SOURCE_VERSION_PATTERN.fullmatch(
            self.adapter_version
        ):
            raise ValueError("SEC filing document provenance is invalid")
        object.__setattr__(self, "body", snapshot)


@dataclass(frozen=True, slots=True)
class SecFilingArchive:
    filing: SecCanonicalFiling
    documents: tuple[SecFilingDocumentSnapshot, ...]

    def __post_init__(self) -> None:
        documents = tuple(self.documents)
        required = {
            SecFilingDocumentKind.COMPLETE_SUBMISSION,
            SecFilingDocumentKind.PRIMARY_DOCUMENT,
        }
        identities = {(document.kind, document.filename) for document in documents}
        attachment_count = sum(
            document.kind
            in {SecFilingDocumentKind.XBRL_INSTANCE, SecFilingDocumentKind.XBRL_ATTACHMENT}
            for document in documents
        )
        if (
            not required.issubset(document.kind for document in documents)
            or len(identities) != len(documents)
            or sum(
                document.kind is SecFilingDocumentKind.COMPLETE_SUBMISSION for document in documents
            )
            != 1
            or sum(
                document.kind is SecFilingDocumentKind.PRIMARY_DOCUMENT for document in documents
            )
            != 1
            or sum(document.kind is SecFilingDocumentKind.XBRL_INSTANCE for document in documents)
            > 1
            or attachment_count > SEC_MAX_ARCHIVE_ATTACHMENTS
            or sum(document.byte_size for document in documents) > SEC_MAX_ARCHIVE_TOTAL_BYTES
            or any(
                document.cik != self.filing.cik
                or document.accession != self.filing.accession
                or document.source_available_at < self.filing.accepted_at
                for document in documents
            )
        ):
            raise ValueError("SEC filing archive is incomplete")
        object.__setattr__(
            self,
            "documents",
            tuple(sorted(documents, key=lambda item: (item.kind.value, item.filename))),
        )

    def document(self, kind: SecFilingDocumentKind) -> SecFilingDocumentSnapshot:
        return next(document for document in self.documents if document.kind is kind)


@dataclass(frozen=True, slots=True)
class SecFilingSnapshotReference:
    document_id: UUID
    snapshot_id: UUID
    filing_id: UUID
    kind: SecFilingDocumentKind
    filename: str
    source_url: str
    source_version: str
    content_type: str
    content_sha256: str
    byte_size: int
    retrieved_at: datetime
    source_available_at: datetime
    status: SecFilingSnapshotStatus
    object_bucket: str = field(repr=False)
    object_key: str = field(repr=False)
    anomaly_code: str | None = None

    def __post_init__(self) -> None:
        if any(
            identifier.int == 0
            for identifier in (self.document_id, self.snapshot_id, self.filing_id)
        ):
            raise ValueError("SEC filing snapshot reference identity is invalid")
        if not _SOURCE_VERSION_PATTERN.fullmatch(
            self.source_version
        ) or not _SHA256_PATTERN.fullmatch(self.content_sha256):
            raise ValueError("SEC filing snapshot reference provenance is invalid")
        if not self.object_bucket.strip() or not self.object_key.strip():
            raise ValueError("SEC filing snapshot object reference is invalid")
        if (self.status is SecFilingSnapshotStatus.QUARANTINED) != (self.anomaly_code is not None):
            raise ValueError("SEC filing snapshot anomaly state is invalid")
        require_utc(self.retrieved_at, field_name="SEC filing snapshot retrieved_at")
        require_utc(self.source_available_at, field_name="SEC filing snapshot available_at")


@dataclass(frozen=True, slots=True)
class SecWorkspaceFilingImport:
    id: UUID
    workspace_id: UUID
    filing_id: UUID
    accession: str
    knowledge_base_id: UUID
    primary_snapshot_id: UUID
    complete_submission_snapshot_id: UUID
    file_id: UUID
    document_id: UUID
    document_version_id: UUID
    ingestion_job_id: UUID
    status: SecFilingImportStatus
    created_at: datetime
    updated_at: datetime
    error_code: str | None = None

    def __post_init__(self) -> None:
        identifiers = (
            self.id,
            self.workspace_id,
            self.filing_id,
            self.knowledge_base_id,
            self.primary_snapshot_id,
            self.complete_submission_snapshot_id,
            self.file_id,
            self.document_id,
            self.document_version_id,
            self.ingestion_job_id,
        )
        if any(
            identifier.int == 0 for identifier in identifiers
        ) or not _ACCESSION_PATTERN.fullmatch(self.accession):
            raise ValueError("SEC Workspace import identity is invalid")
        require_utc(self.created_at, field_name="SEC Workspace import created_at")
        require_utc(self.updated_at, field_name="SEC Workspace import updated_at")
        if (self.status is SecFilingImportStatus.FAILED) != (self.error_code is not None):
            raise ValueError("SEC Workspace import failure state is invalid")


@dataclass(frozen=True, slots=True)
class SecFilingRetrievalTrace:
    profile_version: str
    dense_candidate_count: int
    lexical_candidate_count: int
    fused_candidate_count: int
    rrf_k: int | None
    reranker_version: str | None
    query_rewrite_version: str | None = None
    dense_candidate_limit: int | None = None
    lexical_candidate_limit: int | None = None
    final_limit: int | None = None
    diversity_policy_version: str | None = None
    as_of: datetime | None = None
    active_source_versions: tuple[str, ...] = ()
    index_versions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        counts = (
            self.dense_candidate_count,
            self.lexical_candidate_count,
            self.fused_candidate_count,
        )
        if any(isinstance(value, bool) or not 0 <= value <= 100 for value in counts):
            raise ValueError("SEC retrieval trace counts are invalid")
        if self.profile_version == SEC_DENSE_RETRIEVAL_PROFILE_VERSION:
            if (
                self.lexical_candidate_count
                or self.rrf_k is not None
                or self.reranker_version is not None
            ):
                raise ValueError("SEC Dense retrieval trace is invalid")
        elif self.profile_version == SEC_HYBRID_RETRIEVAL_PROFILE_VERSION:
            if (
                self.rrf_k is None
                or not 1 <= self.rrf_k <= 10_000
                or self.reranker_version is None
                or not _SOURCE_VERSION_PATTERN.fullmatch(self.reranker_version)
                or self.query_rewrite_version is None
                or not _SOURCE_VERSION_PATTERN.fullmatch(self.query_rewrite_version)
                or self.dense_candidate_limit is None
                or self.lexical_candidate_limit is None
                or self.final_limit is None
                or not 1 <= self.final_limit <= self.dense_candidate_limit <= 100
                or not 1 <= self.final_limit <= self.lexical_candidate_limit <= 100
                or self.diversity_policy_version is None
                or not _SOURCE_VERSION_PATTERN.fullmatch(self.diversity_policy_version)
                or self.as_of is None
            ):
                raise ValueError("SEC Hybrid retrieval trace is invalid")
            require_utc(self.as_of, field_name="SEC retrieval cutoff")
        else:
            raise ValueError("SEC retrieval profile is invalid")
        source_versions = tuple(self.active_source_versions)
        index_versions = tuple(self.index_versions)
        if (
            len(source_versions) != len(set(source_versions))
            or len(index_versions) != len(set(index_versions))
            or any(not _SOURCE_VERSION_PATTERN.fullmatch(value) for value in source_versions)
            or any(not _SOURCE_VERSION_PATTERN.fullmatch(value) for value in index_versions)
        ):
            raise ValueError("SEC retrieval version identity is invalid")
        object.__setattr__(self, "active_source_versions", source_versions)
        object.__setattr__(self, "index_versions", index_versions)


@dataclass(frozen=True, slots=True)
class SecFilingSearchHit:
    chunk_id: UUID
    document_version_id: UUID
    snapshot_id: UUID
    accession: str
    title: str
    excerpt: str = field(repr=False)
    score: float
    section: str
    page_number: int
    content_sha256: str
    source_content_sha256: str
    source_url: str
    source_version: str
    retrieval_channels: tuple[str, ...] = ("dense",)
    dense_rank: int | None = None
    lexical_rank: int | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None
    index_version: str = "knowledge-index-v1"

    def __post_init__(self) -> None:
        if any(
            identifier.int == 0
            for identifier in (self.chunk_id, self.document_version_id, self.snapshot_id)
        ):
            raise ValueError("SEC filing search identity is invalid")
        if not _ACCESSION_PATTERN.fullmatch(self.accession) or not 0 <= self.score <= 1:
            raise ValueError("SEC filing search hit is invalid")
        if not self.excerpt.strip() or not self.title.strip() or not self.section.strip():
            raise ValueError("SEC filing search text is invalid")
        if (
            self.page_number < 1
            or not _SHA256_PATTERN.fullmatch(self.content_sha256)
            or not _SHA256_PATTERN.fullmatch(self.source_content_sha256)
        ):
            raise ValueError("SEC filing search provenance is invalid")
        channels = tuple(self.retrieval_channels)
        if (
            not channels
            or len(channels) != len(set(channels))
            or any(channel not in {"dense", "lexical"} for channel in channels)
        ):
            raise ValueError("SEC filing retrieval channels are invalid")
        for rank in (self.dense_rank, self.lexical_rank):
            if rank is not None and (isinstance(rank, bool) or rank < 1):
                raise ValueError("SEC filing retrieval rank is invalid")
        for score in (self.rrf_score, self.rerank_score):
            if score is not None and not 0 <= score <= 1:
                raise ValueError("SEC filing retrieval score is invalid")
        if not _SOURCE_VERSION_PATTERN.fullmatch(self.index_version):
            raise ValueError("SEC filing index version is invalid")
        object.__setattr__(self, "retrieval_channels", channels)


@dataclass(frozen=True, slots=True)
class SecFilingSearchResult:
    status: SecFilingContentStatus
    accession: str
    retrieval_profile_version: str = SEC_DENSE_RETRIEVAL_PROFILE_VERSION
    hits: tuple[SecFilingSearchHit, ...] = ()
    error_code: str | None = None
    retrieval_trace: SecFilingRetrievalTrace | None = None

    def __post_init__(self) -> None:
        if not _ACCESSION_PATTERN.fullmatch(self.accession):
            raise ValueError("SEC filing search accession is invalid")
        hits = tuple(self.hits)
        if (self.status is SecFilingContentStatus.OK) != bool(hits):
            raise ValueError("SEC filing search status is inconsistent")
        if len(hits) > 5 or len({hit.chunk_id for hit in hits}) != len(hits):
            raise ValueError("SEC filing search hits are invalid")
        if (self.status is SecFilingContentStatus.DEPENDENCY_FAILED) != (
            self.error_code is not None
        ):
            raise ValueError("SEC filing search error state is invalid")
        if self.retrieval_profile_version == SEC_HYBRID_RETRIEVAL_PROFILE_VERSION and (
            self.retrieval_trace is None
            or self.retrieval_trace.profile_version != self.retrieval_profile_version
        ):
            raise ValueError("SEC Hybrid search trace is missing")
        if self.retrieval_trace is not None and (
            self.retrieval_trace.profile_version != self.retrieval_profile_version
        ):
            raise ValueError("SEC filing search trace profile is inconsistent")
        object.__setattr__(self, "hits", hits)


@dataclass(frozen=True, slots=True)
class SecFilingContentPreparation:
    status: SecFilingContentStatus
    accession: str
    import_record: SecWorkspaceFilingImport | None = None

    def __post_init__(self) -> None:
        if not _ACCESSION_PATTERN.fullmatch(self.accession):
            raise ValueError("SEC filing preparation accession is invalid")
        if (self.status is SecFilingContentStatus.OK) != (self.import_record is not None):
            raise ValueError("SEC filing preparation status is inconsistent")
        if self.import_record is not None and self.import_record.accession != self.accession:
            raise ValueError("SEC filing preparation import is inconsistent")


@dataclass(frozen=True, slots=True)
class SecFilingSection:
    import_id: UUID
    snapshot_id: UUID
    accession: str
    document_version_id: UUID
    chunk_id: UUID
    title: str
    section: str
    text: str = field(repr=False)
    page_number: int
    content_sha256: str
    source_content_sha256: str
    source_url: str
    source_version: str

    def __post_init__(self) -> None:
        if any(
            identifier.int == 0
            for identifier in (
                self.import_id,
                self.snapshot_id,
                self.document_version_id,
                self.chunk_id,
            )
        ):
            raise ValueError("SEC filing section identity is invalid")
        if not _ACCESSION_PATTERN.fullmatch(self.accession) or not self.text.strip():
            raise ValueError("SEC filing section is invalid")
        if (
            self.page_number < 1
            or not _SHA256_PATTERN.fullmatch(self.content_sha256)
            or not _SHA256_PATTERN.fullmatch(self.source_content_sha256)
        ):
            raise ValueError("SEC filing section provenance is invalid")


@dataclass(frozen=True, slots=True)
class SecXbrlPeriod:
    kind: SecXbrlPeriodKind
    instant: date | None = None
    start_date: date | None = None
    end_date: date | None = None

    def __post_init__(self) -> None:
        if self.kind is SecXbrlPeriodKind.INSTANT:
            valid = self.instant is not None and self.start_date is None and self.end_date is None
        elif self.kind is SecXbrlPeriodKind.DURATION:
            valid = (
                self.instant is None
                and self.start_date is not None
                and self.end_date is not None
                and self.end_date >= self.start_date
            )
        else:
            valid = self.instant is None and self.start_date is None and self.end_date is None
        if not valid:
            raise ValueError("SEC XBRL period is invalid")

    @property
    def key(self) -> str:
        if self.kind is SecXbrlPeriodKind.INSTANT:
            if self.instant is None:
                raise AssertionError("Validated instant period lost its date")
            return f"instant:{self.instant.isoformat()}"
        if self.kind is SecXbrlPeriodKind.DURATION:
            if self.start_date is None or self.end_date is None:
                raise AssertionError("Validated duration period lost its dates")
            return f"duration:{self.start_date.isoformat()}:{self.end_date.isoformat()}"
        return "forever"


@dataclass(frozen=True, slots=True)
class SecXbrlSourceSnapshot:
    source_kind: SecXbrlSourceKind
    cik: str
    source_url: str
    source_version: str
    content_type: str
    content_sha256: str
    byte_size: int
    retrieved_at: datetime
    source_available_at: datetime
    body: bytes = field(repr=False)
    filing_snapshot_id: UUID | None = None
    adapter_version: str = SEC_XBRL_ADAPTER_VERSION

    def __post_init__(self) -> None:
        body = bytes(self.body)
        if not _CIK_PATTERN.fullmatch(self.cik):
            raise ValueError("SEC XBRL source CIK is invalid")
        aggregate = self.source_kind is SecXbrlSourceKind.COMPANYFACTS_AGGREGATE
        if aggregate:
            expected_url = sec_companyfacts_url(self.cik)
            valid_type = self.content_type == "application/json"
        else:
            expected_url = self.source_url
            valid_type = self.source_url.startswith(
                SEC_ARCHIVE_URL_PREFIX
            ) and self.content_type in {
                "text/html",
                "application/xhtml+xml",
                "application/xml",
                "text/xml",
            }
        if self.source_url != expected_url or not valid_type:
            raise ValueError("SEC XBRL source boundary is invalid")
        if aggregate == (self.filing_snapshot_id is not None):
            raise ValueError("SEC XBRL source snapshot link is invalid")
        if (
            not _SOURCE_VERSION_PATTERN.fullmatch(self.source_version)
            or not _SOURCE_VERSION_PATTERN.fullmatch(self.adapter_version)
            or not _SHA256_PATTERN.fullmatch(self.content_sha256)
            or self.byte_size != len(body)
            or not 1 <= self.byte_size <= SEC_MAX_XBRL_RESPONSE_BYTES
            or sha256_hex(body) != self.content_sha256
        ):
            raise ValueError("SEC XBRL source bytes are invalid")
        require_utc(self.retrieved_at, field_name="SEC XBRL source retrieved_at")
        require_utc(self.source_available_at, field_name="SEC XBRL source available_at")
        if self.source_available_at > self.retrieved_at:
            raise ValueError("SEC XBRL source availability is invalid")
        object.__setattr__(self, "body", body)


@dataclass(frozen=True, slots=True)
class SecXbrlContextData:
    source_version: str
    context_id: str
    entity_identifier: str
    period: SecXbrlPeriod
    dimensions: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.source_version, field_name="SEC XBRL context source", maximum=128)
        _require_text(self.context_id, field_name="SEC XBRL context ID", maximum=255)
        _require_text(self.entity_identifier, field_name="SEC XBRL entity", maximum=255)
        dimensions = tuple(sorted(self.dimensions))
        if len(dimensions) > 64 or len({name for name, _value in dimensions}) != len(dimensions):
            raise ValueError("SEC XBRL context dimensions are invalid")
        for name, value in dimensions:
            _require_xbrl_qname(name, field_name="SEC XBRL dimension")
            _require_text(value, field_name="SEC XBRL dimension value", maximum=2_000)
        object.__setattr__(self, "dimensions", dimensions)


@dataclass(frozen=True, slots=True)
class SecXbrlFactData:
    source_version: str
    locator_key: str
    taxonomy: str
    concept: str
    value: str = field(repr=False)
    period: SecXbrlPeriod
    filed_date: date
    form: SecFilingForm
    ordinal: int
    unit: str | None = None
    context_id: str | None = None
    dimensions: tuple[tuple[str, str], ...] = ()
    decimals: str | None = None
    scale: int | None = None
    format: str | None = None
    is_custom: bool = False

    def __post_init__(self) -> None:
        _require_text(self.source_version, field_name="SEC XBRL fact source", maximum=128)
        _require_text(self.locator_key, field_name="SEC XBRL fact locator", maximum=512)
        _require_xbrl_name(self.taxonomy, field_name="SEC XBRL taxonomy")
        _require_xbrl_name(self.concept, field_name="SEC XBRL concept")
        _require_text(
            self.value,
            field_name="SEC XBRL fact value",
            maximum=SEC_MAX_XBRL_FACT_VALUE_CHARACTERS,
        )
        if isinstance(self.ordinal, bool) or not 0 <= self.ordinal < SEC_MAX_XBRL_FACTS:
            raise ValueError("SEC XBRL fact ordinal is invalid")
        if self.unit is not None:
            _require_text(self.unit, field_name="SEC XBRL unit", maximum=255)
        if self.context_id is not None:
            _require_text(self.context_id, field_name="SEC XBRL context ID", maximum=255)
        if self.decimals is not None:
            _require_text(self.decimals, field_name="SEC XBRL decimals", maximum=32)
        if self.scale is not None and not -100 <= self.scale <= 100:
            raise ValueError("SEC XBRL scale is invalid")
        if self.format is not None:
            _require_text(self.format, field_name="SEC XBRL format", maximum=255)
        dimensions = tuple(sorted(self.dimensions))
        if len(dimensions) > 64 or len({name for name, _value in dimensions}) != len(dimensions):
            raise ValueError("SEC XBRL fact dimensions are invalid")
        for name, value in dimensions:
            _require_xbrl_qname(name, field_name="SEC XBRL dimension")
            _require_text(value, field_name="SEC XBRL dimension value", maximum=2_000)
        object.__setattr__(self, "dimensions", dimensions)


@dataclass(frozen=True, slots=True)
class SecXbrlSourceBatch:
    source: SecXbrlSourceSnapshot
    contexts: tuple[SecXbrlContextData, ...]
    facts: tuple[SecXbrlFactData, ...]

    def __post_init__(self) -> None:
        contexts = tuple(self.contexts)
        facts = tuple(self.facts)
        if (
            len(contexts) > SEC_MAX_XBRL_CONTEXTS
            or len(facts) > SEC_MAX_XBRL_FACTS
            or len({item.context_id for item in contexts}) != len(contexts)
            or len({item.locator_key for item in facts}) != len(facts)
            or any(item.source_version != self.source.source_version for item in contexts)
            or any(item.source_version != self.source.source_version for item in facts)
        ):
            raise ValueError("SEC XBRL source batch is invalid")
        context_ids = {item.context_id for item in contexts}
        aggregate = self.source.source_kind is SecXbrlSourceKind.COMPANYFACTS_AGGREGATE
        if any(
            (aggregate and fact.context_id is not None)
            or (not aggregate and fact.context_id not in context_ids)
            for fact in facts
        ):
            raise ValueError("SEC XBRL fact context is invalid")
        object.__setattr__(self, "contexts", contexts)
        object.__setattr__(self, "facts", facts)


@dataclass(frozen=True, slots=True)
class SecXbrlDataset:
    filing: SecCanonicalFiling
    batches: tuple[SecXbrlSourceBatch, ...]

    def __post_init__(self) -> None:
        batches = tuple(self.batches)
        if (
            not batches
            or not any(batch.facts for batch in batches)
            or len({batch.source.source_version for batch in batches}) != len(batches)
            or any(batch.source.cik != self.filing.cik for batch in batches)
        ):
            raise ValueError("SEC XBRL dataset is invalid")
        object.__setattr__(self, "batches", batches)


@dataclass(frozen=True, slots=True)
class SecXbrlSyncPreparation:
    filing: SecCanonicalFiling
    import_record: SecWorkspaceFilingImport
    raw_sources: tuple[SecFilingSnapshotReference, ...]

    def __post_init__(self) -> None:
        sources = tuple(self.raw_sources)
        if (
            self.import_record.filing_id != self.filing.id
            or self.import_record.accession != self.filing.accession
            or not sources
            or any(
                source.filing_id != self.filing.id
                or source.status is not SecFilingSnapshotStatus.ACTIVE
                or source.kind
                not in {
                    SecFilingDocumentKind.PRIMARY_DOCUMENT,
                    SecFilingDocumentKind.XBRL_INSTANCE,
                }
                for source in sources
            )
        ):
            raise ValueError("SEC XBRL sync preparation is invalid")
        object.__setattr__(self, "raw_sources", sources)


@dataclass(frozen=True, slots=True)
class SecXbrlSyncResult:
    accession: str
    source_count: int
    context_count: int
    fact_count: int
    source_versions: tuple[str, ...]

    def __post_init__(self) -> None:
        versions = tuple(self.source_versions)
        if (
            not _ACCESSION_PATTERN.fullmatch(self.accession)
            or not 1 <= self.source_count == len(versions)
            or self.context_count < 0
            or self.fact_count < 1
            or len(set(versions)) != len(versions)
        ):
            raise ValueError("SEC XBRL sync result is invalid")
        object.__setattr__(self, "source_versions", versions)


@dataclass(frozen=True, slots=True)
class SecXbrlFactQuery:
    taxonomy: str | None = None
    concept: str | None = None
    unit: str | None = None
    period_kind: SecXbrlPeriodKind | None = None
    source_kinds: tuple[SecXbrlSourceKind, ...] = tuple(SecXbrlSourceKind)
    limit: int = 20

    def __post_init__(self) -> None:
        if self.taxonomy is not None:
            _require_xbrl_name(self.taxonomy, field_name="SEC XBRL taxonomy")
        if self.concept is not None:
            _require_xbrl_name(self.concept, field_name="SEC XBRL concept")
        if self.unit is not None:
            _require_text(self.unit, field_name="SEC XBRL unit", maximum=255)
        kinds = tuple(dict.fromkeys(self.source_kinds))
        if not kinds or not 1 <= self.limit <= 100:
            raise ValueError("SEC XBRL fact query is invalid")
        object.__setattr__(self, "source_kinds", kinds)


@dataclass(frozen=True, slots=True)
class SecXbrlFact:
    id: UUID
    filing_id: UUID
    source_id: UUID
    source_snapshot_id: UUID | None
    source_kind: SecXbrlSourceKind
    cik: str
    accession: str
    taxonomy: str
    concept: str
    value: str = field(repr=False)
    unit: str | None
    period: SecXbrlPeriod
    filed_date: date
    form: SecFilingForm
    context_id: str | None
    dimensions: tuple[tuple[str, str], ...]
    decimals: str | None
    scale: int | None
    format: str | None
    is_custom: bool
    ordinal: int
    locator_key: str
    source_url: str
    source_version: str
    source_content_sha256: str
    source_available_at: datetime
    retrieved_at: datetime
    unavailable_fields: tuple[str, ...]

    def __post_init__(self) -> None:
        if any(identifier.int == 0 for identifier in (self.id, self.filing_id, self.source_id)):
            raise ValueError("SEC XBRL fact identity is invalid")
        aggregate = self.source_kind is SecXbrlSourceKind.COMPANYFACTS_AGGREGATE
        if aggregate == (self.source_snapshot_id is not None) or aggregate == (
            self.context_id is not None
        ):
            raise ValueError("SEC XBRL fact source locator is invalid")
        SecXbrlFactData(
            source_version=self.source_version,
            locator_key=self.locator_key,
            taxonomy=self.taxonomy,
            concept=self.concept,
            value=self.value,
            unit=self.unit,
            period=self.period,
            filed_date=self.filed_date,
            form=self.form,
            context_id=self.context_id,
            dimensions=self.dimensions,
            decimals=self.decimals,
            scale=self.scale,
            format=self.format,
            is_custom=self.is_custom,
            ordinal=self.ordinal,
        )
        if not _CIK_PATTERN.fullmatch(self.cik) or not _ACCESSION_PATTERN.fullmatch(self.accession):
            raise ValueError("SEC XBRL fact filing identity is invalid")
        if not _SHA256_PATTERN.fullmatch(self.source_content_sha256):
            raise ValueError("SEC XBRL fact source hash is invalid")
        require_utc(self.source_available_at, field_name="SEC XBRL fact available_at")
        require_utc(self.retrieved_at, field_name="SEC XBRL fact retrieved_at")
        unavailable = tuple(self.unavailable_fields)
        if aggregate and unavailable != ("context_id", "decimals", "dimensions", "scale"):
            raise ValueError("SEC aggregate XBRL unavailable fields are invalid")
        if not aggregate and unavailable:
            raise ValueError("SEC raw XBRL unavailable fields are invalid")
        object.__setattr__(self, "dimensions", tuple(sorted(self.dimensions)))
        object.__setattr__(self, "unavailable_fields", unavailable)


def sec_xbrl_fact_content_sha256(fact: SecXbrlFact) -> str:
    """Hash the stable fact payload independently from its containing source bytes."""

    payload = {
        "accession": fact.accession,
        "cik": fact.cik,
        "concept": fact.concept,
        "context_id": fact.context_id,
        "decimals": fact.decimals,
        "dimensions": list(fact.dimensions),
        "fact_id": str(fact.id),
        "filed_date": fact.filed_date.isoformat(),
        "filing_id": str(fact.filing_id),
        "form": fact.form.value,
        "format": fact.format,
        "is_custom": fact.is_custom,
        "locator_key": fact.locator_key,
        "ordinal": fact.ordinal,
        "period": {
            "end_date": None if fact.period.end_date is None else fact.period.end_date.isoformat(),
            "instant": None if fact.period.instant is None else fact.period.instant.isoformat(),
            "kind": fact.period.kind.value,
            "start_date": (
                None if fact.period.start_date is None else fact.period.start_date.isoformat()
            ),
        },
        "scale": fact.scale,
        "source_available_at": fact.source_available_at.isoformat(),
        "source_content_sha256": fact.source_content_sha256,
        "source_id": str(fact.source_id),
        "source_kind": fact.source_kind.value,
        "source_snapshot_id": (
            None if fact.source_snapshot_id is None else str(fact.source_snapshot_id)
        ),
        "source_url": fact.source_url,
        "source_version": fact.source_version,
        "taxonomy": fact.taxonomy,
        "unit": fact.unit,
        "value": fact.value,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class SecXbrlFactResult:
    status: SecFilingContentStatus
    accession: str
    facts: tuple[SecXbrlFact, ...] = ()
    error_code: str | None = None

    def __post_init__(self) -> None:
        facts = tuple(self.facts)
        if not _ACCESSION_PATTERN.fullmatch(self.accession):
            raise ValueError("SEC XBRL result accession is invalid")
        if (self.status is SecFilingContentStatus.OK) != bool(facts):
            raise ValueError("SEC XBRL result status is inconsistent")
        if len(facts) > 100 or any(fact.accession != self.accession for fact in facts):
            raise ValueError("SEC XBRL result facts are invalid")
        if (self.status is SecFilingContentStatus.DEPENDENCY_FAILED) != (
            self.error_code is not None
        ):
            raise ValueError("SEC XBRL result error state is invalid")
        object.__setattr__(self, "facts", facts)


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


def sec_companyfacts_url(cik: str) -> str:
    if not _CIK_PATTERN.fullmatch(cik):
        raise ValueError("SEC companyfacts CIK is invalid")
    return f"{SEC_COMPANYFACTS_URL_PREFIX}CIK{cik}.json"


def sec_archive_folder_url(cik: str, accession: str) -> str:
    normalized = normalize_cik(cik)
    if _ACCESSION_PATTERN.fullmatch(accession) is None:
        raise ValueError("SEC filing accession is invalid")
    return f"{SEC_ARCHIVE_URL_PREFIX}{int(normalized)}/{accession.replace('-', '')}/"


def sec_complete_submission_url(cik: str, accession: str) -> str:
    return f"{sec_archive_folder_url(cik, accession)}{accession}.txt"


def sec_primary_document_url(cik: str, accession: str, filename: str) -> str:
    return sec_filing_document_url(cik, accession, filename)


def sec_filing_document_url(cik: str, accession: str, filename: str) -> str:
    if _PRIMARY_DOCUMENT_PATTERN.fullmatch(filename) is None:
        raise ValueError("SEC filing document is invalid")
    return f"{sec_archive_folder_url(cik, accession)}{filename}"


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


def sec_filing_snapshot_object_key(source: SecFilingDocumentSnapshot) -> str:
    suffix = source.filename.rpartition(".")[2].lower() or "bin"
    return (
        f"sec/filings/{source.cik}/{source.accession.replace('-', '')}/"
        f"{source.kind.value}/{source.content_sha256[:2]}/{source.content_sha256}.{suffix}"
    )


def sec_xbrl_object_key(source: SecXbrlSourceSnapshot) -> str:
    if source.source_kind is not SecXbrlSourceKind.COMPANYFACTS_AGGREGATE:
        raise ValueError("Only aggregate XBRL responses own a separate object")
    return (
        f"sec/xbrl/companyfacts/{source.cik}/"
        f"{source.content_sha256[:2]}/{source.content_sha256}.json"
    )


def sec_xbrl_source_version(
    source_kind: SecXbrlSourceKind,
    content_sha256: str,
) -> str:
    if not _SHA256_PATTERN.fullmatch(content_sha256):
        raise ValueError("SEC XBRL source hash is invalid")
    kind = {
        SecXbrlSourceKind.COMPANYFACTS_AGGREGATE: "companyfacts",
        SecXbrlSourceKind.RAW_INLINE: "inline",
        SecXbrlSourceKind.RAW_INSTANCE: "instance",
    }[source_kind]
    return f"sec-xbrl-{kind}-{content_sha256[:24]}"


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


def _require_xbrl_name(value: str, *, field_name: str) -> None:
    if not value.isascii() or _XBRL_NAME_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} is invalid")


def _require_xbrl_qname(value: str, *, field_name: str) -> None:
    if not value.isascii() or _XBRL_QNAME_PATTERN.fullmatch(value) is None:
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
