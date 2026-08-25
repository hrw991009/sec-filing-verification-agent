"""Domain contracts for authorized Dense Knowledge retrieval."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Final
from uuid import UUID, uuid5

from industry_platform.modules.financial_verification.domain import FinancialScope

KNOWLEDGE_SEARCH_SCHEMA_VERSION: Final = 1
KNOWLEDGE_SEARCH_TOOL_VERSION: Final = "v1"
KNOWLEDGE_SEARCH_TOP_K: Final = 5
KNOWLEDGE_EVIDENCE_NAMESPACE: Final = UUID("da98a83d-d350-471d-89ab-f83224fa01e5")

_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_ACCESSION_PATTERN = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")


class KnowledgeSearchStatus(StrEnum):
    OK = "ok"
    NOT_READY = "not_ready"
    NO_RESULT = "no_result"
    PARTIAL_INDEX = "partial_index"
    DEPENDENCY_FAILED = "dependency_failed"
    PERMISSION_DENIED = "permission_denied"
    AMBIGUOUS_FILER = "ambiguous_filer"
    PERIOD_MISMATCH = "period_mismatch"


@dataclass(frozen=True, slots=True)
class SecFixtureFact:
    key: str
    value: str
    unit: str
    scale: int
    period_start: date
    period_end: date
    section: str
    source_page: int
    anchor: str


@dataclass(frozen=True, slots=True)
class SecFilingFixture:
    dataset_version: str
    cik: str
    accession: str
    form: str
    report_period: date
    filed_at: datetime
    accepted_at: datetime
    primary_document: str
    canonical_url: str
    fixture_path: str
    content_sha256: str
    license_or_terms: str
    facts: tuple[SecFixtureFact, ...]

    def __post_init__(self) -> None:
        if (
            not _ACCESSION_PATTERN.fullmatch(self.accession)
            or not _SHA256_PATTERN.fullmatch(self.content_sha256)
            or not self.cik.isdigit()
            or len(self.cik) != 10
            or self.form not in {"10-K", "10-Q"}
            or self.filed_at.tzinfo is None
            or self.accepted_at.tzinfo is None
            or not self.facts
        ):
            raise ValueError("SEC fixture identity is invalid")
        if len({fact.key for fact in self.facts}) != len(self.facts):
            raise ValueError("SEC fixture fact keys must be unique")

    def matches(self, scope: FinancialScope) -> bool:
        return (
            self.cik == scope.cik
            and self.accession == scope.accession
            and self.form == scope.form.value
        )

    def scope_status(self, scope: FinancialScope) -> KnowledgeSearchStatus:
        if not self.matches(scope):
            return KnowledgeSearchStatus.AMBIGUOUS_FILER
        if self.report_period != scope.report_period or any(
            fact.unit != scope.unit or fact.scale != scope.scale for fact in self.facts
        ):
            return KnowledgeSearchStatus.PERIOD_MISMATCH
        if self.accepted_at > scope.as_of:
            return KnowledgeSearchStatus.NO_RESULT
        return KnowledgeSearchStatus.OK


@dataclass(frozen=True, slots=True)
class DenseCandidate:
    chunk_id: UUID
    document_version_id: UUID
    score: float

    def __post_init__(self) -> None:
        if self.chunk_id.int == 0 or self.document_version_id.int == 0:
            raise ValueError("Dense candidate identity is invalid")
        if not 0 <= self.score <= 1:
            raise ValueError("Dense candidate score is invalid")


@dataclass(frozen=True, slots=True)
class KnowledgeSearchHit:
    evidence_ref: UUID
    knowledge_base_id: UUID
    document_id: UUID
    document_version_id: UUID
    chunk_id: UUID
    title: str
    excerpt: str = field(repr=False)
    score: float
    page_number: int
    section: str
    content_sha256: str
    parser_version: str
    chunker_version: str
    index_version: str
    fixture: SecFilingFixture


@dataclass(frozen=True, slots=True)
class KnowledgeSearchResult:
    status: KnowledgeSearchStatus
    hits: tuple[KnowledgeSearchHit, ...] = ()
    error_code: str | None = None

    def __post_init__(self) -> None:
        hits = tuple(self.hits)
        if (self.status is KnowledgeSearchStatus.OK) != bool(hits):
            raise ValueError("Knowledge search status and hits are inconsistent")
        if len(hits) > KNOWLEDGE_SEARCH_TOP_K:
            raise ValueError("Knowledge search result exceeds Top-K")
        if len({hit.chunk_id for hit in hits}) != len(hits):
            raise ValueError("Knowledge search hits must be unique")
        object.__setattr__(self, "hits", hits)


@dataclass(frozen=True, slots=True)
class KnowledgeContextSource:
    """Evidence-ready Knowledge candidate considered by the shared Context path."""

    evidence_ref: UUID
    excerpt: str = field(repr=False)
    content_sha256: str
    source_version: str

    def __post_init__(self) -> None:
        if self.evidence_ref.int == 0 or not _SHA256_PATTERN.fullmatch(self.content_sha256):
            raise ValueError("Knowledge Context source is invalid")


def knowledge_evidence_ref(
    *,
    workspace_id: UUID,
    accession: str,
    document_version_id: UUID,
    chunk_id: UUID,
    content_sha256: str,
) -> UUID:
    if workspace_id.int == 0 or not _ACCESSION_PATTERN.fullmatch(accession):
        raise ValueError("Knowledge Evidence identity is invalid")
    if document_version_id.int == 0 or chunk_id.int == 0:
        raise ValueError("Knowledge Evidence resource identity is invalid")
    if not _SHA256_PATTERN.fullmatch(content_sha256):
        raise ValueError("Knowledge Evidence content hash is invalid")
    return uuid5(
        KNOWLEDGE_EVIDENCE_NAMESPACE,
        f"{workspace_id}:{accession}:{document_version_id}:{chunk_id}:{content_sha256}",
    )


def canonical_content_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
