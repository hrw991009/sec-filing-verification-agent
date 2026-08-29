"""Typed Evidence locators, normalization decisions, Claims, and graph projections."""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final
from urllib.parse import urlsplit
from uuid import UUID

from industry_platform.modules.agent_runtime.domain import require_non_nil_uuid, require_utc
from industry_platform.modules.identity.domain import TraceId

EVIDENCE_SCHEMA_VERSION: Final = 1
EVIDENCE_NORMALIZER_VERSION: Final = "evidence-normalizer-v1"
MAX_EVIDENCE_EXCERPT_LENGTH: Final = 10_000
MAX_EVIDENCE_TITLE_LENGTH: Final = 1_000
MAX_CLAIM_STATEMENT_LENGTH: Final = 4_000
MAX_CLAIM_RELATIONS: Final = 32

_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,127}$")
_TABLE_PATTERN = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")
_COLUMN_PATTERN = re.compile(r"^[a-z_][a-z0-9_]*$")


class EvidenceKind(StrEnum):
    WEB_SNAPSHOT = "web_snapshot"
    SQL_RESULT = "sql_result"
    NEWS = "news"
    POLICY = "policy"
    BIDDING = "bidding"
    STOCK = "stock"
    FILING = "filing"
    CALCULATION = "calculation"


class EvidenceStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    TOMBSTONED = "tombstoned"
    UNAVAILABLE = "unavailable"


class EvidenceLocatorType(StrEnum):
    INDUSTRY_SOURCE_V1 = "industry_source_v1"
    SQL_RESULT_V1 = "sql_result_v1"
    SEC_FILING_CHUNK_V1 = "sec_filing_chunk_v1"
    SEC_FILING_TEXT_V1 = "sec_filing_text_v1"
    SEC_XBRL_FACT_V1 = "sec_xbrl_fact_v1"
    FINANCIAL_CALCULATION_V1 = "financial_calculation_v1"


class EvidenceDecision(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class EvidenceDecisionReason(StrEnum):
    ACCEPTED = "accepted"
    OBSERVATION_NOT_FOUND = "observation_not_found"
    OBSERVATION_INVALID = "observation_invalid"
    UNSUPPORTED_SOURCE = "unsupported_source"
    LOCATOR_INVALID = "locator_invalid"
    SOURCE_VERSION_MISSING = "source_version_missing"
    SOURCE_SNAPSHOT_MISSING = "source_snapshot_missing"
    SOURCE_HASH_MISMATCH = "source_hash_mismatch"
    LICENSE_NOT_ALLOWED = "license_not_allowed"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    CONTENT_TOO_LARGE = "content_too_large"
    RESOURCE_UNAUTHORIZED = "resource_unauthorized"


class ClaimEvidenceRelation(StrEnum):
    SUPPORTS = "supports"
    REFUTES = "refutes"
    CONTEXT = "context"


class ClaimVerificationStatus(StrEnum):
    SUPPORTED = "supported"
    REFUTED = "refuted"
    UNCERTAIN = "uncertain"
    CONFLICTED = "conflicted"


class RelationStatus(StrEnum):
    ACTIVE = "active"
    INVALIDATED = "invalidated"


class GraphNodeType(StrEnum):
    CLAIM = "claim"
    EVIDENCE = "evidence"
    ENTITY = "entity"


class EvidenceError(Exception):
    """Base class for sanitized Evidence failures."""


class EvidenceNotFoundError(EvidenceError):
    pass


class ResearchRunNotFoundError(EvidenceError):
    pass


class ClaimNotFoundError(EvidenceError):
    pass


class EvidenceConflictError(EvidenceError):
    pass


class EvidenceRequestRejectedError(EvidenceError):
    pass


class EvidencePersistenceError(EvidenceError):
    def __init__(self, *, sqlstate: str | None = None) -> None:
        super().__init__("Evidence persistence is unavailable")
        self.sqlstate = sqlstate


def _bounded_text(value: str, *, field_name: str, maximum: int) -> str:
    normalized = "\n".join(line.rstrip() for line in value.strip().splitlines()).strip()
    if not normalized or len(normalized) > maximum or "\x00" in normalized:
        raise ValueError(f"{field_name} is invalid")
    return normalized


def _sha256(value: str, *, field_name: str) -> str:
    if not _SHA256_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} is invalid")
    return value


def canonical_fingerprint(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        dict(payload), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class IndustrySourceLocatorV1:
    source_item_id: UUID
    source_kind: str
    provider: str
    source_version: str
    content_sha256: str
    locator_type: EvidenceLocatorType = EvidenceLocatorType.INDUSTRY_SOURCE_V1
    schema_version: int = EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.source_item_id, field_name="Industry source item ID")
        if self.source_kind not in {"news", "policy", "tender", "stock"}:
            raise ValueError("Industry source kind is invalid")
        for value, field_name in (
            (self.provider, "Industry source provider"),
            (self.source_version, "Industry source version"),
        ):
            if not _REFERENCE_PATTERN.fullmatch(value):
                raise ValueError(f"{field_name} is invalid")
        _sha256(self.content_sha256, field_name="Industry source hash")
        if self.schema_version != EVIDENCE_SCHEMA_VERSION:
            raise ValueError("Industry locator schema version is unsupported")

    def to_mapping(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "schema_version": self.schema_version,
                "locator_type": self.locator_type.value,
                "source_item_id": str(self.source_item_id),
                "source_kind": self.source_kind,
                "provider": self.provider,
                "source_version": self.source_version,
                "content_sha256": self.content_sha256,
            }
        )


@dataclass(frozen=True, slots=True)
class SqlResultLocatorV1:
    query_run_id: UUID
    connection_id: UUID
    schema_snapshot_id: UUID
    schema_snapshot_sha256: str
    tables: tuple[str, ...]
    columns: tuple[str, ...]
    row_start: int
    row_end: int
    locator_type: EvidenceLocatorType = EvidenceLocatorType.SQL_RESULT_V1
    schema_version: int = EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for identifier, field_name in (
            (self.query_run_id, "SQL locator Query Run ID"),
            (self.connection_id, "SQL locator connection ID"),
            (self.schema_snapshot_id, "SQL locator Schema Snapshot ID"),
        ):
            require_non_nil_uuid(identifier, field_name=field_name)
        _sha256(self.schema_snapshot_sha256, field_name="SQL locator schema hash")
        tables = tuple(self.tables)
        columns = tuple(self.columns)
        if (
            not tables
            or len(tables) > 16
            or len(tables) != len(set(tables))
            or any(_TABLE_PATTERN.fullmatch(item) is None for item in tables)
        ):
            raise ValueError("SQL locator tables are invalid")
        if (
            not columns
            or len(columns) > 64
            or len(columns) != len(set(columns))
            or any(_COLUMN_PATTERN.fullmatch(item) is None for item in columns)
        ):
            raise ValueError("SQL locator columns are invalid")
        if (
            isinstance(self.row_start, bool)
            or isinstance(self.row_end, bool)
            or self.row_start < 0
            or self.row_end < self.row_start
            or self.row_end > 200
        ):
            raise ValueError("SQL locator row range is invalid")
        if self.schema_version != EVIDENCE_SCHEMA_VERSION:
            raise ValueError("SQL locator schema version is unsupported")
        object.__setattr__(self, "tables", tables)
        object.__setattr__(self, "columns", columns)

    def to_mapping(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "schema_version": self.schema_version,
                "locator_type": self.locator_type.value,
                "query_run_id": str(self.query_run_id),
                "connection_id": str(self.connection_id),
                "schema_snapshot_id": str(self.schema_snapshot_id),
                "schema_snapshot_sha256": self.schema_snapshot_sha256,
                "tables": list(self.tables),
                "columns": list(self.columns),
                "row_start": self.row_start,
                "row_end": self.row_end,
            }
        )


@dataclass(frozen=True, slots=True)
class SecFilingChunkLocatorV1:
    cik: str
    accession: str
    form: str
    report_period: str
    filed_at: str
    accepted_at: str
    primary_document: str
    canonical_url: str
    dataset_version: str
    fixture_sha256: str
    knowledge_base_id: UUID
    document_id: UUID
    document_version_id: UUID
    chunk_id: UUID
    section: str
    page_number: int
    content_sha256: str
    parser_version: str
    chunker_version: str
    index_version: str
    locator_type: EvidenceLocatorType = EvidenceLocatorType.SEC_FILING_CHUNK_V1
    schema_version: int = EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for identifier, field_name in (
            (self.knowledge_base_id, "Filing locator Knowledge Base ID"),
            (self.document_id, "Filing locator Document ID"),
            (self.document_version_id, "Filing locator Document Version ID"),
            (self.chunk_id, "Filing locator Chunk ID"),
        ):
            require_non_nil_uuid(identifier, field_name=field_name)
        if not re.fullmatch(r"[0-9]{10}", self.cik) or not re.fullmatch(
            r"[0-9]{10}-[0-9]{2}-[0-9]{6}", self.accession
        ):
            raise ValueError("Filing locator identity is invalid")
        if self.form not in {"10-K", "10-Q"}:
            raise ValueError("Filing locator form is invalid")
        for value, field_name in (
            (self.report_period, "Filing locator report period"),
            (self.filed_at, "Filing locator filed time"),
            (self.accepted_at, "Filing locator accepted time"),
            (self.primary_document, "Filing locator primary document"),
            (self.dataset_version, "Filing locator dataset version"),
            (self.parser_version, "Filing locator parser version"),
            (self.chunker_version, "Filing locator chunker version"),
            (self.index_version, "Filing locator index version"),
        ):
            if not _REFERENCE_PATTERN.fullmatch(value):
                raise ValueError(f"{field_name} is invalid")
        parsed_url = urlsplit(self.canonical_url)
        if parsed_url.scheme != "https" or parsed_url.hostname != "www.sec.gov":
            raise ValueError("Filing locator canonical URL is invalid")
        _bounded_text(self.section, field_name="Filing locator section", maximum=1_000)
        if isinstance(self.page_number, bool) or self.page_number < 1:
            raise ValueError("Filing locator page is invalid")
        _sha256(self.fixture_sha256, field_name="Filing locator fixture hash")
        _sha256(self.content_sha256, field_name="Filing locator content hash")
        if self.schema_version != EVIDENCE_SCHEMA_VERSION:
            raise ValueError("Filing locator schema version is unsupported")

    def to_mapping(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "schema_version": self.schema_version,
                "locator_type": self.locator_type.value,
                "cik": self.cik,
                "accession": self.accession,
                "form": self.form,
                "report_period": self.report_period,
                "filed_at": self.filed_at,
                "accepted_at": self.accepted_at,
                "primary_document": self.primary_document,
                "canonical_url": self.canonical_url,
                "dataset_version": self.dataset_version,
                "fixture_sha256": self.fixture_sha256,
                "knowledge_base_id": str(self.knowledge_base_id),
                "document_id": str(self.document_id),
                "document_version_id": str(self.document_version_id),
                "chunk_id": str(self.chunk_id),
                "section": self.section,
                "page_number": self.page_number,
                "content_sha256": self.content_sha256,
                "parser_version": self.parser_version,
                "chunker_version": self.chunker_version,
                "index_version": self.index_version,
            }
        )


@dataclass(frozen=True, slots=True)
class SecFilingTextLocatorV1:
    cik: str
    accession: str
    form: str
    report_period: str
    as_of: str
    filed_at: str
    accepted_at: str
    canonical_url: str
    snapshot_id: UUID
    source_version: str
    source_content_sha256: str
    knowledge_base_id: UUID
    document_id: UUID
    document_version_id: UUID
    chunk_id: UUID
    section: str
    page_number: int
    content_sha256: str
    parser_version: str
    chunker_version: str
    index_version: str
    retrieval_profile_version: str
    retrieval_channels: tuple[str, ...]
    locator_type: EvidenceLocatorType = EvidenceLocatorType.SEC_FILING_TEXT_V1
    schema_version: int = EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for identifier, field_name in (
            (self.snapshot_id, "SEC filing snapshot ID"),
            (self.knowledge_base_id, "SEC filing Knowledge Base ID"),
            (self.document_id, "SEC filing Document ID"),
            (self.document_version_id, "SEC filing Document Version ID"),
            (self.chunk_id, "SEC filing Chunk ID"),
        ):
            require_non_nil_uuid(identifier, field_name=field_name)
        if (
            not re.fullmatch(r"[0-9]{10}", self.cik)
            or self.cik == "0000000000"
            or not re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", self.accession)
            or self.form not in {"10-K", "10-K/A", "10-Q", "10-Q/A"}
        ):
            raise ValueError("SEC filing locator identity is invalid")
        try:
            date.fromisoformat(self.report_period)
            as_of = datetime.fromisoformat(self.as_of)
            filed_at = datetime.fromisoformat(self.filed_at)
            accepted_at = datetime.fromisoformat(self.accepted_at)
            for time_value in (as_of, filed_at, accepted_at):
                require_utc(time_value, field_name="SEC filing locator time")
        except ValueError:
            raise ValueError("SEC filing locator time is invalid") from None
        if accepted_at > as_of or filed_at > accepted_at:
            raise ValueError("SEC filing locator cutoff is invalid")
        parsed_url = urlsplit(self.canonical_url)
        if parsed_url.scheme != "https" or parsed_url.hostname != "www.sec.gov":
            raise ValueError("SEC filing locator URL is invalid")
        for reference_value, field_name in (
            (self.source_version, "SEC filing source version"),
            (self.parser_version, "SEC filing parser version"),
            (self.chunker_version, "SEC filing chunker version"),
            (self.index_version, "SEC filing index version"),
            (self.retrieval_profile_version, "SEC filing retrieval profile"),
        ):
            if not _REFERENCE_PATTERN.fullmatch(reference_value):
                raise ValueError(f"{field_name} is invalid")
        if self.retrieval_profile_version not in {"dense-v1", "hybrid-v1", "direct-read-v1"}:
            raise ValueError("SEC filing retrieval profile is invalid")
        channels = tuple(self.retrieval_channels)
        if (
            len(channels) != len(set(channels))
            or any(channel not in {"dense", "lexical"} for channel in channels)
            or (not channels and self.retrieval_profile_version != "direct-read-v1")
        ):
            raise ValueError("SEC filing retrieval channels are invalid")
        _bounded_text(self.section, field_name="SEC filing section", maximum=1_000)
        if isinstance(self.page_number, bool) or self.page_number < 1:
            raise ValueError("SEC filing page is invalid")
        _sha256(self.source_content_sha256, field_name="SEC filing source hash")
        _sha256(self.content_sha256, field_name="SEC filing content hash")
        if self.schema_version != EVIDENCE_SCHEMA_VERSION:
            raise ValueError("SEC filing locator schema version is unsupported")
        object.__setattr__(self, "retrieval_channels", channels)

    def to_mapping(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "schema_version": self.schema_version,
                "locator_type": self.locator_type.value,
                "cik": self.cik,
                "accession": self.accession,
                "form": self.form,
                "report_period": self.report_period,
                "as_of": self.as_of,
                "filed_at": self.filed_at,
                "accepted_at": self.accepted_at,
                "canonical_url": self.canonical_url,
                "snapshot_id": str(self.snapshot_id),
                "source_version": self.source_version,
                "source_content_sha256": self.source_content_sha256,
                "knowledge_base_id": str(self.knowledge_base_id),
                "document_id": str(self.document_id),
                "document_version_id": str(self.document_version_id),
                "chunk_id": str(self.chunk_id),
                "section": self.section,
                "page_number": self.page_number,
                "content_sha256": self.content_sha256,
                "parser_version": self.parser_version,
                "chunker_version": self.chunker_version,
                "index_version": self.index_version,
                "retrieval_profile_version": self.retrieval_profile_version,
                "retrieval_channels": list(self.retrieval_channels),
            }
        )


@dataclass(frozen=True, slots=True)
class SecXbrlFactLocatorV1:
    cik: str
    accession: str
    form: str
    report_period: str
    as_of: str
    fact_id: UUID
    filing_id: UUID
    source_id: UUID
    source_snapshot_id: UUID | None
    source_kind: str
    taxonomy: str
    concept: str
    unit: str | None
    period_kind: str
    instant: str | None
    start_date: str | None
    end_date: str | None
    context_id: str | None
    dimensions: Mapping[str, str]
    decimals: str | None
    scale: int | None
    source_url: str
    source_version: str
    source_content_sha256: str
    content_sha256: str
    source_available_at: str
    retrieved_at: str
    locator_type: EvidenceLocatorType = EvidenceLocatorType.SEC_XBRL_FACT_V1
    schema_version: int = EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for identifier, field_name in (
            (self.fact_id, "SEC XBRL fact ID"),
            (self.filing_id, "SEC XBRL filing ID"),
            (self.source_id, "SEC XBRL source ID"),
        ):
            require_non_nil_uuid(identifier, field_name=field_name)
        if self.source_snapshot_id is not None:
            require_non_nil_uuid(self.source_snapshot_id, field_name="SEC XBRL snapshot ID")
        if (
            not re.fullmatch(r"[0-9]{10}", self.cik)
            or self.cik == "0000000000"
            or not re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", self.accession)
            or self.form not in {"10-K", "10-K/A", "10-Q", "10-Q/A"}
            or self.source_kind not in {"companyfacts_aggregate", "raw_inline", "raw_instance"}
            or self.period_kind not in {"instant", "duration", "forever"}
        ):
            raise ValueError("SEC XBRL locator identity is invalid")
        try:
            date.fromisoformat(self.report_period)
            as_of = datetime.fromisoformat(self.as_of)
            source_available_at = datetime.fromisoformat(self.source_available_at)
            retrieved_at = datetime.fromisoformat(self.retrieved_at)
            for time_value in (as_of, source_available_at, retrieved_at):
                require_utc(time_value, field_name="SEC XBRL locator time")
            for period_value in (self.instant, self.start_date, self.end_date):
                if period_value is not None:
                    date.fromisoformat(period_value)
        except ValueError:
            raise ValueError("SEC XBRL locator time is invalid") from None
        if source_available_at > as_of or source_available_at > retrieved_at:
            raise ValueError("SEC XBRL locator cutoff is invalid")
        valid_period = (
            (
                self.period_kind == "instant"
                and self.instant is not None
                and self.start_date is None
                and self.end_date is None
            )
            or (
                self.period_kind == "duration"
                and self.instant is None
                and self.start_date is not None
                and self.end_date is not None
            )
            or (
                self.period_kind == "forever"
                and self.instant is None
                and self.start_date is None
                and self.end_date is None
            )
        )
        if not valid_period:
            raise ValueError("SEC XBRL period is invalid")
        if (
            self.period_kind == "duration"
            and self.start_date is not None
            and self.end_date is not None
            and date.fromisoformat(self.start_date) > date.fromisoformat(self.end_date)
        ):
            raise ValueError("SEC XBRL period is invalid")
        if (
            self.source_kind == "companyfacts_aggregate" and self.source_snapshot_id is not None
        ) or (
            self.source_kind != "companyfacts_aggregate"
            and (self.source_snapshot_id is None or self.context_id is None)
        ):
            raise ValueError("SEC XBRL source boundary is invalid")
        for reference_value, field_name in (
            (self.source_kind, "SEC XBRL source kind"),
            (self.taxonomy, "SEC XBRL taxonomy"),
            (self.concept, "SEC XBRL concept"),
            (self.source_version, "SEC XBRL source version"),
        ):
            if not _REFERENCE_PATTERN.fullmatch(reference_value):
                raise ValueError(f"{field_name} is invalid")
        dimensions = dict(self.dimensions)
        if len(dimensions) > 32 or any(
            not _REFERENCE_PATTERN.fullmatch(axis) or not _REFERENCE_PATTERN.fullmatch(member)
            for axis, member in dimensions.items()
        ):
            raise ValueError("SEC XBRL dimensions are invalid")
        parsed_url = urlsplit(self.source_url)
        if parsed_url.scheme != "https" or parsed_url.hostname not in {
            "www.sec.gov",
            "data.sec.gov",
        }:
            raise ValueError("SEC XBRL source URL is invalid")
        if (
            self.source_kind == "companyfacts_aggregate" and parsed_url.hostname != "data.sec.gov"
        ) or (
            self.source_kind != "companyfacts_aggregate" and parsed_url.hostname != "www.sec.gov"
        ):
            raise ValueError("SEC XBRL source URL boundary is invalid")
        if isinstance(self.scale, bool) or (self.scale is not None and not -12 <= self.scale <= 12):
            raise ValueError("SEC XBRL scale is invalid")
        _sha256(self.source_content_sha256, field_name="SEC XBRL source hash")
        _sha256(self.content_sha256, field_name="SEC XBRL fact hash")
        if self.schema_version != EVIDENCE_SCHEMA_VERSION:
            raise ValueError("SEC XBRL locator schema version is unsupported")
        object.__setattr__(self, "dimensions", MappingProxyType(dimensions))

    def to_mapping(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "schema_version": self.schema_version,
                "locator_type": self.locator_type.value,
                "cik": self.cik,
                "accession": self.accession,
                "form": self.form,
                "report_period": self.report_period,
                "as_of": self.as_of,
                "fact_id": str(self.fact_id),
                "filing_id": str(self.filing_id),
                "source_id": str(self.source_id),
                "source_snapshot_id": (
                    None if self.source_snapshot_id is None else str(self.source_snapshot_id)
                ),
                "source_kind": self.source_kind,
                "taxonomy": self.taxonomy,
                "concept": self.concept,
                "unit": self.unit,
                "period_kind": self.period_kind,
                "instant": self.instant,
                "start_date": self.start_date,
                "end_date": self.end_date,
                "context_id": self.context_id,
                "dimensions": dict(self.dimensions),
                "decimals": self.decimals,
                "scale": self.scale,
                "source_url": self.source_url,
                "source_version": self.source_version,
                "source_content_sha256": self.source_content_sha256,
                "content_sha256": self.content_sha256,
                "source_available_at": self.source_available_at,
                "retrieved_at": self.retrieved_at,
            }
        )


@dataclass(frozen=True, slots=True)
class FinancialCalculationLocatorV1:
    financial_scope: Mapping[str, object]
    operator: str
    operand_values: tuple[str, ...]
    input_evidence_refs: tuple[UUID, ...]
    decimal_places: int
    rounding_mode: str
    formula: str
    result: str
    unit: str
    scale: int
    observation_sha256: str
    reconciliation_status: str | None = None
    reconciliation_version: str | None = None
    locator_type: EvidenceLocatorType = EvidenceLocatorType.FINANCIAL_CALCULATION_V1
    schema_version: int = EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        from industry_platform.modules.financial_verification.domain import FinancialScope

        scope = dict(self.financial_scope)
        FinancialScope.from_mapping(scope)
        values = tuple(self.operand_values)
        references = tuple(self.input_evidence_refs)
        if not 2 <= len(values) <= 8 or len(values) != len(references):
            raise ValueError("Calculation locator inputs are invalid")
        for reference in references:
            require_non_nil_uuid(reference, field_name="Calculation input Evidence ref")
        for value, field_name in (
            (self.operator, "Calculation operator"),
            (self.rounding_mode, "Calculation rounding mode"),
            (self.unit, "Calculation unit"),
        ):
            if not _REFERENCE_PATTERN.fullmatch(value):
                raise ValueError(f"{field_name} is invalid")
        _bounded_text(self.formula, field_name="Calculation formula", maximum=4_000)
        _bounded_text(self.result, field_name="Calculation result", maximum=200)
        if isinstance(self.decimal_places, bool) or not 0 <= self.decimal_places <= 12:
            raise ValueError("Calculation decimal places are invalid")
        if isinstance(self.scale, bool) or not -12 <= self.scale <= 12:
            raise ValueError("Calculation scale is invalid")
        _sha256(self.observation_sha256, field_name="Calculation observation hash")
        if (self.reconciliation_status is None) != (self.reconciliation_version is None):
            raise ValueError("Calculation reconciliation identity is incomplete")
        if self.reconciliation_status is not None and (
            self.reconciliation_status != "consistent"
            or self.reconciliation_version != "financial-reconciliation-v1"
        ):
            raise ValueError("Calculation reconciliation identity is invalid")
        if self.schema_version != EVIDENCE_SCHEMA_VERSION:
            raise ValueError("Calculation locator schema version is unsupported")
        object.__setattr__(self, "financial_scope", MappingProxyType(scope))
        object.__setattr__(self, "operand_values", values)
        object.__setattr__(self, "input_evidence_refs", references)

    def to_mapping(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "schema_version": self.schema_version,
                "locator_type": self.locator_type.value,
                "financial_scope": dict(self.financial_scope),
                "operator": self.operator,
                "operand_values": list(self.operand_values),
                "input_evidence_refs": [str(item) for item in self.input_evidence_refs],
                "decimal_places": self.decimal_places,
                "rounding_mode": self.rounding_mode,
                "formula": self.formula,
                "result": self.result,
                "unit": self.unit,
                "scale": self.scale,
                "observation_sha256": self.observation_sha256,
                "reconciliation_status": self.reconciliation_status,
                "reconciliation_version": self.reconciliation_version,
            }
        )


type EvidenceLocator = (
    IndustrySourceLocatorV1
    | SqlResultLocatorV1
    | SecFilingChunkLocatorV1
    | SecFilingTextLocatorV1
    | SecXbrlFactLocatorV1
    | FinancialCalculationLocatorV1
)


def parse_evidence_locator(value: Mapping[str, object]) -> EvidenceLocator:
    document = dict(value)
    try:
        locator_type_value = document.get("locator_type")
        if not isinstance(locator_type_value, str):
            raise ValueError
        locator_type = EvidenceLocatorType(locator_type_value)
        schema_version = document.get("schema_version")
        if schema_version != EVIDENCE_SCHEMA_VERSION:
            raise ValueError
        if locator_type is EvidenceLocatorType.INDUSTRY_SOURCE_V1:
            if set(document) != {
                "schema_version",
                "locator_type",
                "source_item_id",
                "source_kind",
                "provider",
                "source_version",
                "content_sha256",
            }:
                raise ValueError
            return IndustrySourceLocatorV1(
                source_item_id=UUID(str(document["source_item_id"])),
                source_kind=str(document["source_kind"]),
                provider=str(document["provider"]),
                source_version=str(document["source_version"]),
                content_sha256=str(document["content_sha256"]),
            )
        if locator_type is EvidenceLocatorType.SQL_RESULT_V1:
            if set(document) != {
                "schema_version",
                "locator_type",
                "query_run_id",
                "connection_id",
                "schema_snapshot_id",
                "schema_snapshot_sha256",
                "tables",
                "columns",
                "row_start",
                "row_end",
            }:
                raise ValueError
            tables = document["tables"]
            columns = document["columns"]
            row_start = document["row_start"]
            row_end = document["row_end"]
            if (
                not isinstance(tables, list)
                or not isinstance(columns, list)
                or isinstance(row_start, bool)
                or not isinstance(row_start, int)
                or isinstance(row_end, bool)
                or not isinstance(row_end, int)
            ):
                raise ValueError
            return SqlResultLocatorV1(
                query_run_id=UUID(str(document["query_run_id"])),
                connection_id=UUID(str(document["connection_id"])),
                schema_snapshot_id=UUID(str(document["schema_snapshot_id"])),
                schema_snapshot_sha256=str(document["schema_snapshot_sha256"]),
                tables=tuple(str(item) for item in tables),
                columns=tuple(str(item) for item in columns),
                row_start=row_start,
                row_end=row_end,
            )
        if locator_type is EvidenceLocatorType.SEC_FILING_CHUNK_V1:
            expected = {
                "schema_version",
                "locator_type",
                "cik",
                "accession",
                "form",
                "report_period",
                "filed_at",
                "accepted_at",
                "primary_document",
                "canonical_url",
                "dataset_version",
                "fixture_sha256",
                "knowledge_base_id",
                "document_id",
                "document_version_id",
                "chunk_id",
                "section",
                "page_number",
                "content_sha256",
                "parser_version",
                "chunker_version",
                "index_version",
            }
            page_number = document["page_number"]
            if (
                set(document) != expected
                or isinstance(page_number, bool)
                or not isinstance(page_number, int)
            ):
                raise ValueError
            return SecFilingChunkLocatorV1(
                cik=str(document["cik"]),
                accession=str(document["accession"]),
                form=str(document["form"]),
                report_period=str(document["report_period"]),
                filed_at=str(document["filed_at"]),
                accepted_at=str(document["accepted_at"]),
                primary_document=str(document["primary_document"]),
                canonical_url=str(document["canonical_url"]),
                dataset_version=str(document["dataset_version"]),
                fixture_sha256=str(document["fixture_sha256"]),
                knowledge_base_id=UUID(str(document["knowledge_base_id"])),
                document_id=UUID(str(document["document_id"])),
                document_version_id=UUID(str(document["document_version_id"])),
                chunk_id=UUID(str(document["chunk_id"])),
                section=str(document["section"]),
                page_number=page_number,
                content_sha256=str(document["content_sha256"]),
                parser_version=str(document["parser_version"]),
                chunker_version=str(document["chunker_version"]),
                index_version=str(document["index_version"]),
            )
        if locator_type is EvidenceLocatorType.SEC_FILING_TEXT_V1:
            expected = {
                "schema_version",
                "locator_type",
                "cik",
                "accession",
                "form",
                "report_period",
                "as_of",
                "filed_at",
                "accepted_at",
                "canonical_url",
                "snapshot_id",
                "source_version",
                "source_content_sha256",
                "knowledge_base_id",
                "document_id",
                "document_version_id",
                "chunk_id",
                "section",
                "page_number",
                "content_sha256",
                "parser_version",
                "chunker_version",
                "index_version",
                "retrieval_profile_version",
                "retrieval_channels",
            }
            page_number = document["page_number"]
            channels = document["retrieval_channels"]
            if (
                set(document) != expected
                or isinstance(page_number, bool)
                or not isinstance(page_number, int)
                or not isinstance(channels, list)
            ):
                raise ValueError
            return SecFilingTextLocatorV1(
                cik=str(document["cik"]),
                accession=str(document["accession"]),
                form=str(document["form"]),
                report_period=str(document["report_period"]),
                as_of=str(document["as_of"]),
                filed_at=str(document["filed_at"]),
                accepted_at=str(document["accepted_at"]),
                canonical_url=str(document["canonical_url"]),
                snapshot_id=UUID(str(document["snapshot_id"])),
                source_version=str(document["source_version"]),
                source_content_sha256=str(document["source_content_sha256"]),
                knowledge_base_id=UUID(str(document["knowledge_base_id"])),
                document_id=UUID(str(document["document_id"])),
                document_version_id=UUID(str(document["document_version_id"])),
                chunk_id=UUID(str(document["chunk_id"])),
                section=str(document["section"]),
                page_number=page_number,
                content_sha256=str(document["content_sha256"]),
                parser_version=str(document["parser_version"]),
                chunker_version=str(document["chunker_version"]),
                index_version=str(document["index_version"]),
                retrieval_profile_version=str(document["retrieval_profile_version"]),
                retrieval_channels=tuple(str(item) for item in channels),
            )
        if locator_type is EvidenceLocatorType.SEC_XBRL_FACT_V1:
            expected = {
                "schema_version",
                "locator_type",
                "cik",
                "accession",
                "form",
                "report_period",
                "as_of",
                "fact_id",
                "filing_id",
                "source_id",
                "source_snapshot_id",
                "source_kind",
                "taxonomy",
                "concept",
                "unit",
                "period_kind",
                "instant",
                "start_date",
                "end_date",
                "context_id",
                "dimensions",
                "decimals",
                "scale",
                "source_url",
                "source_version",
                "source_content_sha256",
                "content_sha256",
                "source_available_at",
                "retrieved_at",
            }
            dimensions = document["dimensions"]
            scale = document["scale"]
            if (
                set(document) != expected
                or not isinstance(dimensions, dict)
                or isinstance(scale, bool)
                or (scale is not None and not isinstance(scale, int))
            ):
                raise ValueError
            source_snapshot_id = document["source_snapshot_id"]
            return SecXbrlFactLocatorV1(
                cik=str(document["cik"]),
                accession=str(document["accession"]),
                form=str(document["form"]),
                report_period=str(document["report_period"]),
                as_of=str(document["as_of"]),
                fact_id=UUID(str(document["fact_id"])),
                filing_id=UUID(str(document["filing_id"])),
                source_id=UUID(str(document["source_id"])),
                source_snapshot_id=(
                    None if source_snapshot_id is None else UUID(str(source_snapshot_id))
                ),
                source_kind=str(document["source_kind"]),
                taxonomy=str(document["taxonomy"]),
                concept=str(document["concept"]),
                unit=None if document["unit"] is None else str(document["unit"]),
                period_kind=str(document["period_kind"]),
                instant=None if document["instant"] is None else str(document["instant"]),
                start_date=(
                    None if document["start_date"] is None else str(document["start_date"])
                ),
                end_date=None if document["end_date"] is None else str(document["end_date"]),
                context_id=(
                    None if document["context_id"] is None else str(document["context_id"])
                ),
                dimensions={str(key): str(value) for key, value in dimensions.items()},
                decimals=(None if document["decimals"] is None else str(document["decimals"])),
                scale=scale,
                source_url=str(document["source_url"]),
                source_version=str(document["source_version"]),
                source_content_sha256=str(document["source_content_sha256"]),
                content_sha256=str(document["content_sha256"]),
                source_available_at=str(document["source_available_at"]),
                retrieved_at=str(document["retrieved_at"]),
            )
        if locator_type is not EvidenceLocatorType.FINANCIAL_CALCULATION_V1:
            raise ValueError
        expected = {
            "schema_version",
            "locator_type",
            "financial_scope",
            "operator",
            "operand_values",
            "input_evidence_refs",
            "decimal_places",
            "rounding_mode",
            "formula",
            "result",
            "unit",
            "scale",
            "observation_sha256",
        }
        extended = expected | {"reconciliation_status", "reconciliation_version"}
        if frozenset(document) not in {frozenset(expected), frozenset(extended)}:
            raise ValueError
        scope = document["financial_scope"]
        values = document["operand_values"]
        references = document["input_evidence_refs"]
        decimal_places = document["decimal_places"]
        scale = document["scale"]
        if (
            not isinstance(scope, dict)
            or not isinstance(values, list)
            or not isinstance(references, list)
            or isinstance(decimal_places, bool)
            or not isinstance(decimal_places, int)
            or isinstance(scale, bool)
            or not isinstance(scale, int)
        ):
            raise ValueError
        return FinancialCalculationLocatorV1(
            financial_scope=scope,
            operator=str(document["operator"]),
            operand_values=tuple(str(item) for item in values),
            input_evidence_refs=tuple(UUID(str(item)) for item in references),
            decimal_places=decimal_places,
            rounding_mode=str(document["rounding_mode"]),
            formula=str(document["formula"]),
            result=str(document["result"]),
            unit=str(document["unit"]),
            scale=scale,
            observation_sha256=str(document["observation_sha256"]),
            reconciliation_status=(
                None
                if document.get("reconciliation_status") is None
                else str(document["reconciliation_status"])
            ),
            reconciliation_version=(
                None
                if document.get("reconciliation_version") is None
                else str(document["reconciliation_version"])
            ),
        )
    except (KeyError, TypeError, ValueError):
        raise ValueError("Evidence locator is invalid") from None


@dataclass(frozen=True, slots=True)
class AuthorizationSnapshot:
    workspace_id: UUID
    actor_user_id: UUID
    role: str
    action: str
    captured_at: datetime

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.workspace_id, field_name="Evidence authorization Workspace ID")
        require_non_nil_uuid(self.actor_user_id, field_name="Evidence authorization actor ID")
        if not _REFERENCE_PATTERN.fullmatch(self.role) or not _REFERENCE_PATTERN.fullmatch(
            self.action
        ):
            raise ValueError("Evidence authorization snapshot is invalid")
        require_utc(self.captured_at, field_name="Evidence authorization capture time")

    def to_mapping(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "workspace_id": str(self.workspace_id),
                "actor_user_id": str(self.actor_user_id),
                "role": self.role,
                "action": self.action,
                "captured_at": self.captured_at.isoformat(),
            }
        )


@dataclass(frozen=True, slots=True)
class Evidence:
    evidence_id: UUID
    workspace_id: UUID
    kind: EvidenceKind
    title: str
    canonical_url: str | None
    locator: EvidenceLocator
    excerpt: str | None = field(repr=False)
    content_sha256: str
    source_published_at: datetime | None
    retrieved_at: datetime
    license_or_terms: str
    status: EvidenceStatus
    revision: int
    invalidated_at: datetime | None
    invalidation_reason: str | None
    origin_run_id: UUID | None
    origin_step_id: UUID | None
    origin_tool_call_id: UUID | None
    origin_observation_id: UUID
    origin_source_ordinal: int
    normalizer_version: str
    authorization_snapshot: AuthorizationSnapshot
    source_resource_version: str
    created_at: datetime
    updated_at: datetime
    origin_case_id: UUID | None = None

    def __post_init__(self) -> None:
        for identifier, field_name in (
            (self.evidence_id, "Evidence ID"),
            (self.workspace_id, "Evidence Workspace ID"),
            (self.origin_observation_id, "Evidence origin Observation ID"),
        ):
            require_non_nil_uuid(identifier, field_name=field_name)
        agent_origin = (self.origin_run_id, self.origin_step_id, self.origin_tool_call_id)
        if (self.origin_case_id is None) != all(value is not None for value in agent_origin):
            raise ValueError("Evidence origin is incomplete")
        if self.origin_case_id is not None:
            require_non_nil_uuid(self.origin_case_id, field_name="Evidence origin Case ID")
        for origin_identifier, origin_field_name in zip(
            agent_origin,
            ("Evidence origin Run ID", "Evidence origin Step ID", "Evidence origin Tool Call ID"),
            strict=True,
        ):
            if origin_identifier is not None:
                require_non_nil_uuid(origin_identifier, field_name=origin_field_name)
        _bounded_text(self.title, field_name="Evidence title", maximum=MAX_EVIDENCE_TITLE_LENGTH)
        if self.canonical_url is not None:
            parsed = urlsplit(self.canonical_url)
            if parsed.scheme != "https" or parsed.hostname is None or parsed.username is not None:
                raise ValueError("Evidence canonical URL is invalid")
        if self.excerpt is not None:
            _bounded_text(
                self.excerpt,
                field_name="Evidence excerpt",
                maximum=MAX_EVIDENCE_EXCERPT_LENGTH,
            )
        _sha256(self.content_sha256, field_name="Evidence content hash")
        _bounded_text(self.license_or_terms, field_name="Evidence terms", maximum=1_000)
        for value, field_name in (
            (self.normalizer_version, "Evidence normalizer version"),
            (self.source_resource_version, "Evidence source resource version"),
        ):
            if not _REFERENCE_PATTERN.fullmatch(value):
                raise ValueError(f"{field_name} is invalid")
        for timestamp, field_name in (
            (self.retrieved_at, "Evidence retrieval time"),
            (self.created_at, "Evidence creation time"),
            (self.updated_at, "Evidence update time"),
        ):
            require_utc(timestamp, field_name=field_name)
        if self.source_published_at is not None:
            require_utc(self.source_published_at, field_name="Evidence publication time")
        if isinstance(self.revision, bool) or self.revision < 1:
            raise ValueError("Evidence revision is invalid")
        if (
            isinstance(self.origin_source_ordinal, bool)
            or not 1 <= self.origin_source_ordinal <= 16
        ):
            raise ValueError("Evidence origin source ordinal is invalid")
        if (
            self.authorization_snapshot.workspace_id != self.workspace_id
            or self.authorization_snapshot.action != "evidence.normalize"
        ):
            raise ValueError("Evidence authorization snapshot is inconsistent")
        active = self.status is EvidenceStatus.ACTIVE
        if active and (
            self.excerpt is None
            or self.invalidated_at is not None
            or self.invalidation_reason is not None
        ):
            raise ValueError("Evidence lifecycle is inconsistent")
        if not active and (
            self.excerpt is not None
            or self.invalidated_at is None
            or self.invalidation_reason is None
        ):
            raise ValueError("Evidence lifecycle is inconsistent")
        if self.invalidated_at is not None and self.invalidation_reason is not None:
            require_utc(self.invalidated_at, field_name="Evidence invalidation time")
            _bounded_text(
                self.invalidation_reason,
                field_name="Evidence invalidation reason",
                maximum=200,
            )


@dataclass(frozen=True, slots=True)
class NormalizeObservation:
    tool_call_id: UUID
    observation_id: UUID
    trace_id: TraceId

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.tool_call_id, field_name="Evidence Tool Call ID")
        require_non_nil_uuid(self.observation_id, field_name="Evidence Observation ID")


@dataclass(frozen=True, slots=True)
class EvidenceNormalizationItem:
    source_ordinal: int
    decision: EvidenceDecision
    reason: EvidenceDecisionReason
    evidence: Evidence | None

    def __post_init__(self) -> None:
        if isinstance(self.source_ordinal, bool) or not 1 <= self.source_ordinal <= 16:
            raise ValueError("Evidence source ordinal is invalid")
        if (self.decision is EvidenceDecision.ACCEPTED) != (self.evidence is not None):
            raise ValueError("Evidence normalization result is inconsistent")
        if (
            self.decision is EvidenceDecision.ACCEPTED
            and self.reason is not EvidenceDecisionReason.ACCEPTED
        ):
            raise ValueError("Accepted Evidence requires an accepted reason")


@dataclass(frozen=True, slots=True)
class EvidenceNormalizationResult:
    observation_id: UUID
    tool_call_id: UUID
    normalizer_version: str
    items: tuple[EvidenceNormalizationItem, ...]

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.observation_id, field_name="Evidence result Observation ID")
        require_non_nil_uuid(self.tool_call_id, field_name="Evidence result Tool Call ID")
        if self.normalizer_version != EVIDENCE_NORMALIZER_VERSION:
            raise ValueError("Evidence normalizer version is unsupported")
        items = tuple(self.items)
        if tuple(item.source_ordinal for item in items) != tuple(range(1, len(items) + 1)):
            raise ValueError("Evidence normalization items are invalid")
        object.__setattr__(self, "items", items)


@dataclass(frozen=True, slots=True)
class ClaimEvidenceInput:
    evidence_id: UUID
    relation: ClaimEvidenceRelation

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.evidence_id, field_name="Claim Evidence ID")


@dataclass(frozen=True, slots=True)
class CreateClaim:
    research_run_id: UUID
    statement: str
    confidence: float
    relations: tuple[ClaimEvidenceInput, ...]
    origin_run_id: UUID
    origin_step_id: UUID
    trace_id: TraceId
    claim_id: UUID | None = None

    def __post_init__(self) -> None:
        for identifier, field_name in (
            (self.research_run_id, "Claim Research Run ID"),
            (self.origin_run_id, "Claim origin Run ID"),
            (self.origin_step_id, "Claim origin Step ID"),
        ):
            require_non_nil_uuid(identifier, field_name=field_name)
        if self.claim_id is not None:
            require_non_nil_uuid(self.claim_id, field_name="Claim ID")
        object.__setattr__(
            self,
            "statement",
            _bounded_text(
                self.statement,
                field_name="Claim statement",
                maximum=MAX_CLAIM_STATEMENT_LENGTH,
            ),
        )
        if isinstance(self.confidence, bool) or not 0 <= self.confidence <= 1:
            raise ValueError("Claim confidence is invalid")
        relations = tuple(self.relations)
        if len(relations) > MAX_CLAIM_RELATIONS or len(
            {relation.evidence_id for relation in relations}
        ) != len(relations):
            raise ValueError("Claim Evidence relations are invalid")
        object.__setattr__(self, "relations", relations)


def claim_verification_status(
    relations: tuple[ClaimEvidenceInput, ...],
) -> ClaimVerificationStatus:
    kinds = {item.relation for item in relations}
    has_support = ClaimEvidenceRelation.SUPPORTS in kinds
    has_refute = ClaimEvidenceRelation.REFUTES in kinds
    if has_support and has_refute:
        return ClaimVerificationStatus.CONFLICTED
    if has_support:
        return ClaimVerificationStatus.SUPPORTED
    if has_refute:
        return ClaimVerificationStatus.REFUTED
    return ClaimVerificationStatus.UNCERTAIN


def claim_coverage(relations: tuple[ClaimEvidenceInput, ...]) -> float:
    if not relations:
        return 0
    decisive = sum(
        item.relation in {ClaimEvidenceRelation.SUPPORTS, ClaimEvidenceRelation.REFUTES}
        for item in relations
    )
    return round(decisive / len(relations), 6)


@dataclass(frozen=True, slots=True)
class ClaimEvidenceLink:
    evidence: Evidence
    relation: ClaimEvidenceRelation
    relation_version: int
    status: RelationStatus
    ordinal: int
    origin_run_id: UUID
    origin_step_id: UUID


@dataclass(frozen=True, slots=True)
class ResearchClaim:
    claim_id: UUID
    workspace_id: UUID
    research_run_id: UUID
    statement: str
    confidence: float
    verification_status: ClaimVerificationStatus
    coverage: float
    conflict: bool
    revision: int
    relations: tuple[ClaimEvidenceLink, ...]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class InvalidateEvidence:
    evidence_id: UUID
    expected_revision: int
    status: EvidenceStatus
    reason: str
    trace_id: TraceId

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.evidence_id, field_name="Evidence ID")
        if isinstance(self.expected_revision, bool) or self.expected_revision < 1:
            raise ValueError("Evidence expected revision is invalid")
        if self.status is EvidenceStatus.ACTIVE:
            raise ValueError("Evidence invalidation status is invalid")
        object.__setattr__(
            self,
            "reason",
            _bounded_text(
                self.reason,
                field_name="Evidence invalidation reason",
                maximum=200,
            ),
        )


@dataclass(frozen=True, slots=True)
class GraphNode:
    node_id: UUID
    node_type: GraphNodeType
    resource_id: UUID
    label: str
    status: RelationStatus


@dataclass(frozen=True, slots=True)
class GraphEdge:
    edge_id: UUID
    source_node_id: UUID
    target_node_id: UUID
    relation: ClaimEvidenceRelation
    status: RelationStatus


@dataclass(frozen=True, slots=True)
class ClaimGraph:
    research_run_id: UUID
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
