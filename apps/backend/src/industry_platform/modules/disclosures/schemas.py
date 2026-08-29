"""HTTP schemas for SEC filer discovery."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from industry_platform.modules.disclosures.diff import (
    SEC_MAX_DIFF_FACT_CHANGES,
    SecFilingChangeKind,
    SecFilingComparisonIdentity,
    SecFilingDiffRelationship,
    SecFilingDiffResult,
    SecFilingDiffStatus,
)
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
    SecFilingRetrievalTrace,
    SecFilingSearchHit,
    SecFilingSearchResult,
    SecFilingSection,
    SecFilingSelection,
    SecFilingSelectionStatus,
    SecSubmissionSourceKind,
    SecSubmissionSourceReference,
    SecWorkspaceFilingImport,
    SecXbrlFact,
    SecXbrlFactQuery,
    SecXbrlFactResult,
    SecXbrlPeriod,
    SecXbrlPeriodKind,
    SecXbrlSourceKind,
    SecXbrlSyncResult,
    normalize_cik,
    sec_xbrl_fact_content_sha256,
)
from industry_platform.modules.disclosures.monitor import SecMonitorStatus
from industry_platform.modules.disclosures.subscription import (
    SecDisclosureCaseView,
    SecMonitorView,
)
from industry_platform.modules.financial_verification.domain import (
    FinancialForm,
    FinancialScope,
)
from industry_platform.modules.research.domain import ResearchApprovalOutcome
from industry_platform.modules.research.schemas import ResearchApprovalResponse


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
    retrieval_channels: list[str]
    dense_rank: int | None
    lexical_rank: int | None
    rrf_score: float | None
    rerank_score: float | None
    index_version: str

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
            retrieval_channels=list(value.retrieval_channels),
            dense_rank=value.dense_rank,
            lexical_rank=value.lexical_rank,
            rrf_score=value.rrf_score,
            rerank_score=value.rerank_score,
            index_version=value.index_version,
        )


class SecFilingRetrievalTraceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_version: str
    dense_candidate_count: int
    lexical_candidate_count: int
    fused_candidate_count: int
    rrf_k: int | None
    reranker_version: str | None
    query_rewrite_version: str | None
    dense_candidate_limit: int | None
    lexical_candidate_limit: int | None
    final_limit: int | None
    diversity_policy_version: str | None
    as_of: datetime | None
    active_source_versions: list[str]
    index_versions: list[str]

    @classmethod
    def from_domain(cls, value: SecFilingRetrievalTrace) -> Self:
        return cls(
            profile_version=value.profile_version,
            dense_candidate_count=value.dense_candidate_count,
            lexical_candidate_count=value.lexical_candidate_count,
            fused_candidate_count=value.fused_candidate_count,
            rrf_k=value.rrf_k,
            reranker_version=value.reranker_version,
            query_rewrite_version=value.query_rewrite_version,
            dense_candidate_limit=value.dense_candidate_limit,
            lexical_candidate_limit=value.lexical_candidate_limit,
            final_limit=value.final_limit,
            diversity_policy_version=value.diversity_policy_version,
            as_of=value.as_of,
            active_source_versions=list(value.active_source_versions),
            index_versions=list(value.index_versions),
        )


class SecFilingSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: SecFilingContentStatus
    accession: str
    retrieval_profile_version: str
    hits: list[SecFilingSearchHitResponse]
    error_code: str | None
    retrieval_trace: SecFilingRetrievalTraceResponse | None

    @classmethod
    def from_domain(cls, value: SecFilingSearchResult) -> Self:
        return cls(
            status=value.status,
            accession=value.accession,
            retrieval_profile_version=value.retrieval_profile_version,
            hits=[SecFilingSearchHitResponse.from_domain(hit) for hit in value.hits],
            error_code=value.error_code,
            retrieval_trace=(
                None
                if value.retrieval_trace is None
                else SecFilingRetrievalTraceResponse.from_domain(value.retrieval_trace)
            ),
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


class SecXbrlSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    knowledge_base_id: UUID


class SecXbrlSyncResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    accession: str = Field(pattern=r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
    source_count: int = Field(ge=1)
    context_count: int = Field(ge=0)
    fact_count: int = Field(ge=1)
    source_versions: list[str]

    @classmethod
    def from_domain(cls, value: SecXbrlSyncResult) -> Self:
        return cls(
            accession=value.accession,
            source_count=value.source_count,
            context_count=value.context_count,
            fact_count=value.fact_count,
            source_versions=list(value.source_versions),
        )


class SecXbrlPeriodResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: SecXbrlPeriodKind
    instant: date | None
    start_date: date | None
    end_date: date | None

    @classmethod
    def from_domain(cls, value: SecXbrlPeriod) -> Self:
        return cls(
            kind=value.kind,
            instant=value.instant,
            start_date=value.start_date,
            end_date=value.end_date,
        )


class SecAggregateXbrlFactLocatorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_kind: Literal[SecXbrlSourceKind.COMPANYFACTS_AGGREGATE]
    endpoint_snapshot_id: UUID
    accession: str
    taxonomy: str
    concept: str
    unit: str | None
    period: SecXbrlPeriodResponse
    ordinal: int


class SecRawXbrlFactLocatorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_kind: Literal[SecXbrlSourceKind.RAW_INLINE, SecXbrlSourceKind.RAW_INSTANCE]
    filing_snapshot_id: UUID
    accession: str
    taxonomy: str
    concept: str
    context_id: str
    ordinal: int


type SecXbrlFactLocatorResponse = Annotated[
    SecAggregateXbrlFactLocatorResponse | SecRawXbrlFactLocatorResponse,
    Field(discriminator="source_kind"),
]


class SecXbrlFactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    filing_id: UUID
    source_id: UUID
    source_snapshot_id: UUID | None
    source_kind: SecXbrlSourceKind
    cik: str
    accession: str
    taxonomy: str
    concept: str
    value: str
    unit: str | None
    period: SecXbrlPeriodResponse
    filed_date: date
    form: SecFilingForm
    context_id: str | None
    dimensions: dict[str, str]
    decimals: str | None
    scale: int | None
    format: str | None
    is_custom: bool
    locator: SecXbrlFactLocatorResponse
    source_url: str
    source_version: str
    source_content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_available_at: datetime
    retrieved_at: datetime
    unavailable_fields: list[str]

    @classmethod
    def from_domain(cls, value: SecXbrlFact) -> Self:
        period = SecXbrlPeriodResponse.from_domain(value.period)
        if value.source_kind is SecXbrlSourceKind.COMPANYFACTS_AGGREGATE:
            locator: SecXbrlFactLocatorResponse = SecAggregateXbrlFactLocatorResponse(
                source_kind=SecXbrlSourceKind.COMPANYFACTS_AGGREGATE,
                endpoint_snapshot_id=value.source_id,
                accession=value.accession,
                taxonomy=value.taxonomy,
                concept=value.concept,
                unit=value.unit,
                period=period,
                ordinal=value.ordinal,
            )
        else:
            if value.source_snapshot_id is None or value.context_id is None:
                raise AssertionError("Validated raw XBRL fact lost its locator")
            locator = SecRawXbrlFactLocatorResponse(
                source_kind=value.source_kind,
                filing_snapshot_id=value.source_snapshot_id,
                accession=value.accession,
                taxonomy=value.taxonomy,
                concept=value.concept,
                context_id=value.context_id,
                ordinal=value.ordinal,
            )
        return cls(
            id=value.id,
            filing_id=value.filing_id,
            source_id=value.source_id,
            source_snapshot_id=value.source_snapshot_id,
            source_kind=value.source_kind,
            cik=value.cik,
            accession=value.accession,
            taxonomy=value.taxonomy,
            concept=value.concept,
            value=value.value,
            unit=value.unit,
            period=period,
            filed_date=value.filed_date,
            form=value.form,
            context_id=value.context_id,
            dimensions=dict(value.dimensions),
            decimals=value.decimals,
            scale=value.scale,
            format=value.format,
            is_custom=value.is_custom,
            locator=locator,
            source_url=value.source_url,
            source_version=value.source_version,
            source_content_sha256=value.source_content_sha256,
            content_sha256=sec_xbrl_fact_content_sha256(value),
            source_available_at=value.source_available_at,
            retrieved_at=value.retrieved_at,
            unavailable_fields=list(value.unavailable_fields),
        )


class SecXbrlFactQueryParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    knowledge_base_id: UUID
    as_of: datetime
    taxonomy: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9._-]{0,255}$")
    concept: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9._-]{0,255}$")
    unit: str | None = Field(default=None, min_length=1, max_length=255)
    period_kind: SecXbrlPeriodKind | None = None
    source_kinds: list[SecXbrlSourceKind] = Field(default_factory=lambda: list(SecXbrlSourceKind))
    limit: int = Field(default=100, ge=1, le=100)

    def to_domain(self) -> SecXbrlFactQuery:
        return SecXbrlFactQuery(
            taxonomy=self.taxonomy,
            concept=self.concept,
            unit=self.unit,
            period_kind=self.period_kind,
            source_kinds=tuple(self.source_kinds),
            limit=self.limit,
        )


class SecXbrlFactCollectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: SecFilingContentStatus
    accession: str
    facts: list[SecXbrlFactResponse]
    error_code: str | None

    @classmethod
    def from_domain(cls, value: SecXbrlFactResult) -> Self:
        return cls(
            status=value.status,
            accession=value.accession,
            facts=[SecXbrlFactResponse.from_domain(fact) for fact in value.facts],
            error_code=value.error_code,
        )


class SecFilingDiffQueryParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    knowledge_base_id: UUID
    comparison_accession: str = Field(pattern=r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
    cik: str = Field(pattern=r"^[0-9]{10}$")
    form: FinancialForm
    report_period: date
    as_of: datetime
    unit: str = Field(default="USD", pattern=r"^[A-Z][A-Z0-9_/-]{0,15}$")
    scale: int = Field(default=0, ge=-12, le=12)
    section_query: str = Field(min_length=1, max_length=500)
    taxonomy: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9._-]{0,255}$")
    concept: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9._-]{0,255}$")
    fact_limit: int = Field(default=10, ge=1, le=SEC_MAX_DIFF_FACT_CHANGES)

    def to_financial_scope(self, accession: str) -> FinancialScope:
        return FinancialScope(
            cik=self.cik,
            accession=accession,
            form=self.form,
            report_period=self.report_period,
            as_of=self.as_of,
            unit=self.unit,
            scale=self.scale,
        )


class SecFilingComparisonIdentityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    import_id: UUID
    knowledge_base_id: UUID
    cik: str
    accession: str
    form: SecFilingForm
    report_date: date
    filed_date: date
    public_available_at: datetime
    amendment_relation_status: SecAmendmentRelationStatus
    base_accession: str | None

    @classmethod
    def from_domain(cls, value: SecFilingComparisonIdentity) -> Self:
        return cls(
            import_id=value.import_id,
            knowledge_base_id=value.knowledge_base_id,
            cik=value.cik,
            accession=value.accession,
            form=value.form,
            report_date=value.report_date,
            filed_date=value.filed_date,
            public_available_at=value.public_available_at,
            amendment_relation_status=value.amendment_relation_status,
            base_accession=value.base_accession,
        )


class SecFilingFactChangeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    taxonomy: str
    concept: str
    unit: str | None
    period_kind: str
    period_bucket: str
    dimensions: dict[str, str]
    is_custom: bool
    change_kind: SecFilingChangeKind
    baseline: SecXbrlFactResponse | None
    target: SecXbrlFactResponse | None


class SecFilingSectionChangeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    section: str
    change_kind: SecFilingChangeKind
    baseline: SecFilingSearchHitResponse
    target: SecFilingSearchHitResponse


class SecFilingDiffResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: SecFilingDiffStatus
    requested_accession: str
    comparison_accession: str
    relationship: SecFilingDiffRelationship | None
    baseline: SecFilingComparisonIdentityResponse | None
    target: SecFilingComparisonIdentityResponse | None
    fact_changes: list[SecFilingFactChangeResponse]
    section_change: SecFilingSectionChangeResponse | None
    unchanged_fact_count: int
    baseline_retrieval_trace: SecFilingRetrievalTraceResponse | None
    target_retrieval_trace: SecFilingRetrievalTraceResponse | None
    error_code: str | None
    version: str

    @classmethod
    def from_domain(cls, value: SecFilingDiffResult) -> Self:
        return cls(
            status=value.status,
            requested_accession=value.requested_accession,
            comparison_accession=value.comparison_accession,
            relationship=value.relationship,
            baseline=(
                None
                if value.baseline is None
                else SecFilingComparisonIdentityResponse.from_domain(value.baseline)
            ),
            target=(
                None
                if value.target is None
                else SecFilingComparisonIdentityResponse.from_domain(value.target)
            ),
            fact_changes=[
                SecFilingFactChangeResponse(
                    taxonomy=change.taxonomy,
                    concept=change.concept,
                    unit=change.unit,
                    period_kind=change.period_kind,
                    period_bucket=change.period_bucket,
                    dimensions=dict(change.dimensions),
                    is_custom=change.is_custom,
                    change_kind=change.change_kind,
                    baseline=(
                        None
                        if change.baseline is None
                        else SecXbrlFactResponse.from_domain(change.baseline)
                    ),
                    target=(
                        None
                        if change.target is None
                        else SecXbrlFactResponse.from_domain(change.target)
                    ),
                )
                for change in value.fact_changes
            ],
            section_change=(
                None
                if value.section_change is None
                else SecFilingSectionChangeResponse(
                    section=value.section_change.section,
                    change_kind=value.section_change.change_kind,
                    baseline=SecFilingSearchHitResponse.from_domain(value.section_change.baseline),
                    target=SecFilingSearchHitResponse.from_domain(value.section_change.target),
                )
            ),
            unchanged_fact_count=value.unchanged_fact_count,
            baseline_retrieval_trace=(
                None
                if value.baseline_retrieval_trace is None
                else SecFilingRetrievalTraceResponse.from_domain(value.baseline_retrieval_trace)
            ),
            target_retrieval_trace=(
                None
                if value.target_retrieval_trace is None
                else SecFilingRetrievalTraceResponse.from_domain(value.target_retrieval_trace)
            ),
            error_code=value.error_code,
            version=value.version,
        )


class SecMonitorRuleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: UUID
    kind: str
    rule_version: str
    section_query: str
    taxonomy: str | None
    concept: str | None
    unit: str | None
    threshold: str | None
    comparator: str | None


class SecMonitorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    monitor_id: UUID
    workspace_id: UUID
    owner_user_id: UUID
    cik: str
    canonical_name: str
    knowledge_base_id: UUID
    schedule_id: UUID
    cron_expression: str
    timezone_name: str
    allowed_forms: list[str]
    rules: list[SecMonitorRuleResponse]
    status: SecMonitorStatus
    revision: int
    watermark_revision: int
    watermark_coverage_version: str
    watermark_accepted_at: datetime | None
    watermark_accession: str | None
    created_from_approval_id: UUID | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, value: SecMonitorView) -> Self:
        return cls(
            monitor_id=value.monitor_id,
            workspace_id=value.workspace_id,
            owner_user_id=value.owner_user_id,
            cik=value.cik,
            canonical_name=value.canonical_name,
            knowledge_base_id=value.knowledge_base_id,
            schedule_id=value.schedule_id,
            cron_expression=value.cron_expression,
            timezone_name=value.timezone_name,
            allowed_forms=list(value.allowed_forms),
            rules=[
                SecMonitorRuleResponse(
                    rule_id=rule.rule_id,
                    kind=rule.kind.value,
                    rule_version=rule.rule_version,
                    section_query=rule.section_query,
                    taxonomy=rule.taxonomy,
                    concept=rule.concept,
                    unit=rule.unit,
                    threshold=rule.threshold,
                    comparator=rule.comparator,
                )
                for rule in value.rules
            ],
            status=value.status,
            revision=value.revision,
            watermark_revision=value.watermark_revision,
            watermark_coverage_version=value.watermark_coverage_version,
            watermark_accepted_at=value.watermark_accepted_at,
            watermark_accession=value.watermark_accession,
            created_from_approval_id=value.created_from_approval_id,
            created_at=value.created_at,
            updated_at=value.updated_at,
        )


class SecMonitorCollectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    monitors: list[SecMonitorResponse]


class DecideSecMonitorSubscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    approval_request_id: UUID
    checkpoint_revision: int = Field(ge=0)
    outcome: ResearchApprovalOutcome


class ChangeSecMonitorStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_revision: int = Field(ge=1)


class SecMonitorSubscriptionDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    approval: ResearchApprovalResponse
    monitor: SecMonitorResponse | None
    resume_job_id: UUID | None
    created: bool


class SecCaseEvidenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    side: str
    evidence_id: UUID


class SecDisclosureCaseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: UUID
    monitor_id: UUID
    monitor_run_id: UUID
    rule_id: UUID
    trigger_kind: str
    source_coverage_version: str
    baseline_accession: str
    target_accession: str
    diff_version: str
    diff_payload: dict[str, object]
    diff_sha256: str
    verification_status: str
    notification_status: str
    evidence: list[SecCaseEvidenceResponse]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, value: SecDisclosureCaseView) -> Self:
        return cls(
            case_id=value.case_id,
            monitor_id=value.monitor_id,
            monitor_run_id=value.monitor_run_id,
            rule_id=value.rule_id,
            trigger_kind=value.trigger_kind,
            source_coverage_version=value.source_coverage_version,
            baseline_accession=value.baseline_accession,
            target_accession=value.target_accession,
            diff_version=value.diff_version,
            diff_payload=value.diff_payload,
            diff_sha256=value.diff_sha256,
            verification_status=value.verification_status,
            notification_status=value.notification_status,
            evidence=[
                SecCaseEvidenceResponse(side=item.side, evidence_id=item.evidence_id)
                for item in value.evidence
            ],
            created_at=value.created_at,
            updated_at=value.updated_at,
        )


class SecDisclosureCaseCollectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cases: list[SecDisclosureCaseResponse]
