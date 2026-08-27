"""HTTP schemas for SEC filer discovery."""

from __future__ import annotations

from datetime import date, datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from industry_platform.modules.disclosures.domain import (
    FilingSelectionScope,
    SecAmendmentPolicy,
    SecAmendmentRelationStatus,
    SecFilerCandidate,
    SecFilerMatchKind,
    SecFilerResolution,
    SecFilerResolutionStatus,
    SecFilingCandidate,
    SecFilingContentStatus,
    SecFilingForm,
    SecFilingImportStatus,
    SecFilingSearchHit,
    SecFilingSearchResult,
    SecFilingSection,
    SecFilingSelection,
    SecFilingSelectionStatus,
    SecSubmissionSourceKind,
    SecSubmissionSourceReference,
    SecWorkspaceFilingImport,
    normalize_cik,
)


class SecFilerCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cik: str = Field(pattern=r"^[0-9]{10}$")
    canonical_name: str
    tickers: list[str]
    matched_by: SecFilerMatchKind
    matched_value: str
    confidence: float = Field(ge=0, le=1)
    source_version: str
    source_url: str
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_observed_at: datetime
    alias_valid_from: datetime | None
    alias_valid_to: datetime | None

    @classmethod
    def from_domain(cls, value: SecFilerCandidate) -> Self:
        return cls(
            cik=value.cik,
            canonical_name=value.canonical_name,
            tickers=list(value.tickers),
            matched_by=value.matched_by,
            matched_value=value.matched_value,
            confidence=value.confidence,
            source_version=value.source_version,
            source_url=value.source_url,
            content_sha256=value.content_sha256,
            source_observed_at=value.source_observed_at,
            alias_valid_from=value.alias_valid_from,
            alias_valid_to=value.alias_valid_to,
        )


class SecFilerResolutionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: SecFilerResolutionStatus
    query: str
    normalized_query: str
    candidates: list[SecFilerCandidateResponse]
    catalog_source_version: str
    catalog_content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    catalog_retrieved_at: datetime

    @classmethod
    def from_domain(cls, value: SecFilerResolution) -> Self:
        return cls(
            status=value.status,
            query=value.query,
            normalized_query=value.normalized_query,
            candidates=[SecFilerCandidateResponse.from_domain(item) for item in value.candidates],
            catalog_source_version=value.catalog_source_version,
            catalog_content_sha256=value.catalog_content_sha256,
            catalog_retrieved_at=value.catalog_retrieved_at,
        )


class FilingSelectionScopeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    cik: str = Field(pattern=r"^[0-9]{10}$")
    allowed_forms: list[SecFilingForm]
    report_period_start: date
    report_period_end: date
    as_of: datetime
    amendment_policy: SecAmendmentPolicy

    @classmethod
    def from_domain(cls, value: FilingSelectionScope) -> Self:
        return cls(
            schema_version=value.schema_version,
            cik=value.cik,
            allowed_forms=list(value.allowed_forms),
            report_period_start=value.report_period_start,
            report_period_end=value.report_period_end,
            as_of=value.as_of,
            amendment_policy=value.amendment_policy,
        )


class FilingSelectionQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cik: str = Field(min_length=1, max_length=10, pattern=r"^[0-9]{1,10}$")
    forms: list[SecFilingForm] = Field(min_length=1, max_length=4)
    report_period_start: date
    report_period_end: date
    as_of: datetime
    amendment_policy: SecAmendmentPolicy = SecAmendmentPolicy.AS_FILED

    @model_validator(mode="after")
    def validate_domain_scope(self) -> Self:
        self.to_domain()
        return self

    def to_domain(self) -> FilingSelectionScope:
        return FilingSelectionScope(
            cik=normalize_cik(self.cik),
            allowed_forms=tuple(sorted(set(self.forms), key=lambda item: item.value)),
            report_period_start=self.report_period_start,
            report_period_end=self.report_period_end,
            as_of=self.as_of,
            amendment_policy=self.amendment_policy,
        )


class SecFilingCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cik: str = Field(pattern=r"^[0-9]{10}$")
    accession: str = Field(pattern=r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
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
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_available_at: datetime

    @classmethod
    def from_domain(cls, value: SecFilingCandidate) -> Self:
        return cls(
            cik=value.cik,
            accession=value.accession,
            form=value.form,
            report_date=value.report_date,
            filed_date=value.filed_date,
            accepted_at=value.accepted_at,
            public_available_at=value.public_available_at,
            primary_document=value.primary_document,
            amendment_relation_status=value.amendment_relation_status,
            base_accession=value.base_accession,
            source_version=value.source_version,
            source_url=value.source_url,
            content_sha256=value.content_sha256,
            source_available_at=value.source_available_at,
        )


class SecSubmissionSourceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_kind: SecSubmissionSourceKind
    source_version: str
    source_url: str
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_available_at: datetime
    retrieved_at: datetime

    @classmethod
    def from_domain(cls, value: SecSubmissionSourceReference) -> Self:
        return cls(
            source_kind=value.source_kind,
            source_version=value.source_version,
            source_url=value.source_url,
            content_sha256=value.content_sha256,
            source_available_at=value.source_available_at,
            retrieved_at=value.retrieved_at,
        )


class SecFilingSelectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: SecFilingSelectionStatus
    scope: FilingSelectionScopeResponse
    filings: list[SecFilingCandidateResponse]
    coverage_version: str
    sources: list[SecSubmissionSourceResponse]
    error_code: str | None

    @classmethod
    def from_domain(cls, value: SecFilingSelection) -> Self:
        return cls(
            status=value.status,
            scope=FilingSelectionScopeResponse.from_domain(value.scope),
            filings=[SecFilingCandidateResponse.from_domain(item) for item in value.filings],
            coverage_version=value.coverage_version,
            sources=[SecSubmissionSourceResponse.from_domain(item) for item in value.sources],
            error_code=value.error_code,
        )


class SecFilingImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    knowledge_base_id: UUID
    as_of: datetime


class SecWorkspaceFilingImportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    workspace_id: UUID
    filing_id: UUID
    accession: str = Field(pattern=r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
    knowledge_base_id: UUID
    primary_snapshot_id: UUID
    complete_submission_snapshot_id: UUID
    file_id: UUID
    document_id: UUID
    document_version_id: UUID
    ingestion_job_id: UUID
    status: SecFilingImportStatus
    error_code: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, value: SecWorkspaceFilingImport) -> Self:
        return cls(
            id=value.id,
            workspace_id=value.workspace_id,
            filing_id=value.filing_id,
            accession=value.accession,
            knowledge_base_id=value.knowledge_base_id,
            primary_snapshot_id=value.primary_snapshot_id,
            complete_submission_snapshot_id=value.complete_submission_snapshot_id,
            file_id=value.file_id,
            document_id=value.document_id,
            document_version_id=value.document_version_id,
            ingestion_job_id=value.ingestion_job_id,
            status=value.status,
            error_code=value.error_code,
            created_at=value.created_at,
            updated_at=value.updated_at,
        )


class SecFilingImportCollectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    imports: list[SecWorkspaceFilingImportResponse]


class SecFilingSearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    knowledge_base_id: UUID
    as_of: datetime
    query: str = Field(min_length=1, max_length=2_000)


class SecFilingSearchHitResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: UUID
    document_version_id: UUID
    snapshot_id: UUID
    accession: str
    title: str
    excerpt: str
    score: float
    section: str
    page_number: int
    content_sha256: str
    source_content_sha256: str
    source_url: str
    source_version: str

    @classmethod
    def from_domain(cls, value: SecFilingSearchHit) -> Self:
        return cls(
            chunk_id=value.chunk_id,
            document_version_id=value.document_version_id,
            snapshot_id=value.snapshot_id,
            accession=value.accession,
            title=value.title,
            excerpt=value.excerpt,
            score=value.score,
            section=value.section,
            page_number=value.page_number,
            content_sha256=value.content_sha256,
            source_content_sha256=value.source_content_sha256,
            source_url=value.source_url,
            source_version=value.source_version,
        )


class SecFilingSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: SecFilingContentStatus
    accession: str
    retrieval_profile_version: str
    hits: list[SecFilingSearchHitResponse]
    error_code: str | None

    @classmethod
    def from_domain(cls, value: SecFilingSearchResult) -> Self:
        return cls(
            status=value.status,
            accession=value.accession,
            retrieval_profile_version=value.retrieval_profile_version,
            hits=[SecFilingSearchHitResponse.from_domain(hit) for hit in value.hits],
            error_code=value.error_code,
        )


class SecFilingSectionQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    knowledge_base_id: UUID
    document_version_id: UUID
    as_of: datetime


class SecFilingSectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    import_id: UUID
    snapshot_id: UUID
    accession: str
    document_version_id: UUID
    chunk_id: UUID
    title: str
    section: str
    text: str
    page_number: int
    content_sha256: str
    source_content_sha256: str
    source_url: str
    source_version: str

    @classmethod
    def from_domain(cls, value: SecFilingSection) -> Self:
        return cls(
            import_id=value.import_id,
            snapshot_id=value.snapshot_id,
            accession=value.accession,
            document_version_id=value.document_version_id,
            chunk_id=value.chunk_id,
            title=value.title,
            section=value.section,
            text=value.text,
            page_number=value.page_number,
            content_sha256=value.content_sha256,
            source_content_sha256=value.source_content_sha256,
            source_url=value.source_url,
            source_version=value.source_version,
        )
