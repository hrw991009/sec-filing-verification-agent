"""Typed Evidence locators, normalization decisions, Claims, and graph projections."""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
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


class EvidenceStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    TOMBSTONED = "tombstoned"
    UNAVAILABLE = "unavailable"


class EvidenceLocatorType(StrEnum):
    INDUSTRY_SOURCE_V1 = "industry_source_v1"
    SQL_RESULT_V1 = "sql_result_v1"


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


type EvidenceLocator = IndustrySourceLocatorV1 | SqlResultLocatorV1


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
    origin_run_id: UUID
    origin_step_id: UUID
    origin_tool_call_id: UUID
    origin_observation_id: UUID
    origin_source_ordinal: int
    normalizer_version: str
    authorization_snapshot: AuthorizationSnapshot
    source_resource_version: str
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        for identifier, field_name in (
            (self.evidence_id, "Evidence ID"),
            (self.workspace_id, "Evidence Workspace ID"),
            (self.origin_run_id, "Evidence origin Run ID"),
            (self.origin_step_id, "Evidence origin Step ID"),
            (self.origin_tool_call_id, "Evidence origin Tool Call ID"),
            (self.origin_observation_id, "Evidence origin Observation ID"),
        ):
            require_non_nil_uuid(identifier, field_name=field_name)
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
        if not items or tuple(item.source_ordinal for item in items) != tuple(
            range(1, len(items) + 1)
        ):
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

    def __post_init__(self) -> None:
        for identifier, field_name in (
            (self.research_run_id, "Claim Research Run ID"),
            (self.origin_run_id, "Claim origin Run ID"),
            (self.origin_step_id, "Claim origin Step ID"),
        ):
            require_non_nil_uuid(identifier, field_name=field_name)
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
