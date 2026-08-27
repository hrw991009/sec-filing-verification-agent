"""Typed SEC filer resolver Tool over the shared Tool Registry contract."""

import hashlib
import json
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from industry_platform.modules.agent_runtime.context import TrustedRuntimeContext
from industry_platform.modules.agent_runtime.domain import AGENT_RUNTIME_SCHEMA_VERSION
from industry_platform.modules.disclosures.domain import (
    SEC_COMPANY_TICKERS_URL,
    SecDisclosurePersistenceError,
    SecFilerMatchKind,
    SecFilerResolutionStatus,
    SecFilingContentError,
    SecFilingContentStatus,
    SecSourceError,
)
from industry_platform.modules.disclosures.filing_content_service import SecFilingContentService
from industry_platform.modules.disclosures.schemas import SecFilingSelectionResponse
from industry_platform.modules.disclosures.service import (
    SecFilerResolutionService,
    SecFilingSelectionService,
)
from industry_platform.modules.tools.domain import (
    MAX_TOOL_SOURCES,
    TOOL_OBSERVATION_NORMALIZER_VERSION,
    ToolApprovalPolicy,
    ToolCostClass,
    ToolDefinition,
    ToolObservation,
    ToolReference,
    ToolRetryClassification,
    ToolSideEffectClass,
    ToolSource,
)
from industry_platform.modules.tools.registry import PydanticToolAdapter, ToolExecutionError
from industry_platform.modules.workspaces.domain import WorkspaceAction

SEC_RESOLVE_FILER_TOOL_NAME = "sec.resolve_filer"
SEC_RESOLVE_FILER_TOOL_VERSION = "v1"


class SecResolveFilerInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=5, ge=1, le=10)


class SecResolveFilerCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cik: str
    canonical_name: str
    tickers: list[str]
    matched_by: SecFilerMatchKind
    matched_value: str
    confidence: float
    source_version: str
    source_observed_at: datetime
    alias_valid_from: datetime | None
    alias_valid_to: datetime | None


class SecResolveFilerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: SecFilerResolutionStatus
    query: str
    normalized_query: str
    candidates: list[SecResolveFilerCandidate]
    catalog_source_version: str
    catalog_source_url: str
    catalog_content_sha256: str
    catalog_retrieved_at: datetime
    error_code: str | None


def sec_resolve_filer_definition() -> ToolDefinition:
    return ToolDefinition(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        name=SEC_RESOLVE_FILER_TOOL_NAME,
        version=SEC_RESOLVE_FILER_TOOL_VERSION,
        description=(
            "Resolve a company name, ticker, or CIK to attributed official SEC filer candidates."
        ),
        input_schema_version="sec-resolve-filer-input-v1",
        output_schema_version="sec-resolve-filer-output-v1",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["query", "limit"],
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": [
                "status",
                "query",
                "normalized_query",
                "candidates",
                "catalog_source_version",
                "catalog_source_url",
                "catalog_content_sha256",
                "catalog_retrieved_at",
                "error_code",
            ],
            "properties": {
                "status": {"type": "string"},
                "query": {"type": "string"},
                "normalized_query": {"type": "string"},
                "candidates": {"type": "array"},
                "catalog_source_version": {"type": "string"},
                "catalog_source_url": {"type": "string"},
                "catalog_content_sha256": {"type": "string"},
                "catalog_retrieved_at": {"type": "string"},
                "error_code": {"type": ["string", "null"]},
            },
        },
        capability=WorkspaceAction.RUN_TOOL,
        timeout_ms=30_000,
        max_result_bytes=100_000,
        max_cost_micro_usd=1,
        cost_class=ToolCostClass.LOW,
        side_effect_class=ToolSideEffectClass.READ_ONLY,
        retry_classification=ToolRetryClassification.SAFE_READ_ONLY,
        approval_policy=ToolApprovalPolicy.AUTO_ALLOW,
        policy_version="sec-public-discovery-read-v1",
    )


class SecResolveFilerTool(PydanticToolAdapter[SecResolveFilerInput, SecResolveFilerOutput]):
    def __init__(self, service: SecFilerResolutionService) -> None:
        super().__init__(
            definition=sec_resolve_filer_definition(),
            input_model=SecResolveFilerInput,
            output_model=SecResolveFilerOutput,
        )
        self._service = service

    async def invoke(
        self,
        value: SecResolveFilerInput,
        runtime_context: TrustedRuntimeContext,
        *,
        idempotency_key: str | None,
    ) -> tuple[SecResolveFilerOutput, int]:
        if idempotency_key is not None:
            raise ToolExecutionError("tool_idempotency_key_unexpected")
        try:
            resolution = await self._service.resolve(
                runtime_context.workspace_scope,
                query=value.query,
                limit=value.limit,
            )
        except SecSourceError as error:
            raise ToolExecutionError(error.code.value) from None
        except SecDisclosurePersistenceError:
            raise ToolExecutionError("sec_catalog_unavailable") from None
        error_code = (
            "filer_not_found"
            if resolution.status is SecFilerResolutionStatus.NO_RESULT
            else "ambiguous_filer"
            if resolution.status is SecFilerResolutionStatus.AMBIGUOUS
            else None
        )
        return (
            SecResolveFilerOutput(
                status=resolution.status,
                query=resolution.query,
                normalized_query=resolution.normalized_query,
                candidates=[
                    SecResolveFilerCandidate(
                        cik=item.cik,
                        canonical_name=item.canonical_name,
                        tickers=list(item.tickers),
                        matched_by=item.matched_by,
                        matched_value=item.matched_value,
                        confidence=item.confidence,
                        source_version=item.source_version,
                        source_observed_at=item.source_observed_at,
                        alias_valid_from=item.alias_valid_from,
                        alias_valid_to=item.alias_valid_to,
                    )
                    for item in resolution.candidates
                ],
                catalog_source_version=resolution.catalog_source_version,
                catalog_source_url=SEC_COMPANY_TICKERS_URL,
                catalog_content_sha256=resolution.catalog_content_sha256,
                catalog_retrieved_at=resolution.catalog_retrieved_at,
                error_code=error_code,
            ),
            0,
        )

    def normalize(
        self,
        value: SecResolveFilerOutput,
        runtime_context: TrustedRuntimeContext,
        *,
        call_id: UUID,
        run_id: UUID,
        observed_at: datetime,
    ) -> ToolObservation:
        model_text = json.dumps(
            value.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        content_sha256 = hashlib.sha256(model_text.encode("utf-8")).hexdigest()
        return ToolObservation(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            observation_id=uuid5(NAMESPACE_URL, f"{call_id}:sec-resolve-filer:v1"),
            call_id=call_id,
            run_id=run_id,
            workspace_id=runtime_context.workspace_scope.workspace_id,
            tool=ToolReference(self.definition.name, self.definition.version),
            normalizer_version=TOOL_OBSERVATION_NORMALIZER_VERSION,
            model_text=model_text,
            sources=(
                ToolSource(
                    source_type="sec_filer_catalog",
                    source_version=value.catalog_source_version,
                    locator=value.catalog_source_url,
                    observed_at=value.catalog_retrieved_at,
                    content_sha256=value.catalog_content_sha256,
                ),
            ),
            observed_at=observed_at,
            content_sha256=content_sha256,
        )


SEC_LIST_FILINGS_TOOL_NAME = "sec.list_filings"
SEC_LIST_FILINGS_TOOL_VERSION = "v1"


class SecListFilingsInput(BaseModel):
    """The model supplies no trust boundary; scope comes from Runtime Context."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SecListFilingsOutput(SecFilingSelectionResponse):
    model_config = ConfigDict(extra="forbid", frozen=True)


def sec_list_filings_definition() -> ToolDefinition:
    return ToolDefinition(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        name=SEC_LIST_FILINGS_TOOL_NAME,
        version=SEC_LIST_FILINGS_TOOL_VERSION,
        description=(
            "List official SEC filings within the server-verified point-in-time filing scope."
        ),
        input_schema_version="sec-list-filings-input-v1",
        output_schema_version="sec-list-filings-output-v1",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": [],
            "properties": {},
        },
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": [
                "status",
                "scope",
                "filings",
                "coverage_version",
                "sources",
                "error_code",
            ],
            "properties": {
                "status": {"type": "string"},
                "scope": {"type": "object"},
                "filings": {"type": "array"},
                "coverage_version": {"type": "string"},
                "sources": {"type": "array"},
                "error_code": {"type": ["string", "null"]},
            },
        },
        capability=WorkspaceAction.RUN_TOOL,
        timeout_ms=60_000,
        max_result_bytes=250_000,
        max_cost_micro_usd=1,
        cost_class=ToolCostClass.LOW,
        side_effect_class=ToolSideEffectClass.READ_ONLY,
        retry_classification=ToolRetryClassification.SAFE_READ_ONLY,
        approval_policy=ToolApprovalPolicy.AUTO_ALLOW,
        policy_version="sec-point-in-time-read-v1",
    )


class SecListFilingsTool(PydanticToolAdapter[SecListFilingsInput, SecListFilingsOutput]):
    def __init__(self, service: SecFilingSelectionService) -> None:
        super().__init__(
            definition=sec_list_filings_definition(),
            input_model=SecListFilingsInput,
            output_model=SecListFilingsOutput,
        )
        self._service = service

    async def invoke(
        self,
        value: SecListFilingsInput,
        runtime_context: TrustedRuntimeContext,
        *,
        idempotency_key: str | None,
    ) -> tuple[SecListFilingsOutput, int]:
        del value
        if idempotency_key is not None:
            raise ToolExecutionError("tool_idempotency_key_unexpected")
        selection_scope = runtime_context.filing_selection_scope
        if selection_scope is None:
            raise ToolExecutionError("filing_selection_scope_not_configured")
        try:
            selection = await self._service.select(
                runtime_context.workspace_scope,
                selection_scope=selection_scope,
            )
        except SecSourceError as error:
            raise ToolExecutionError(error.code.value) from None
        except SecDisclosurePersistenceError:
            raise ToolExecutionError("sec_filing_catalog_unavailable") from None
        if len(selection.sources) > MAX_TOOL_SOURCES:
            raise ToolExecutionError("sec_source_manifest_limit_exceeded")
        return SecListFilingsOutput.from_domain(selection), 0

    def normalize(
        self,
        value: SecListFilingsOutput,
        runtime_context: TrustedRuntimeContext,
        *,
        call_id: UUID,
        run_id: UUID,
        observed_at: datetime,
    ) -> ToolObservation:
        model_text = json.dumps(
            value.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        content_sha256 = hashlib.sha256(model_text.encode("utf-8")).hexdigest()
        return ToolObservation(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            observation_id=uuid5(NAMESPACE_URL, f"{call_id}:sec-list-filings:v1"),
            call_id=call_id,
            run_id=run_id,
            workspace_id=runtime_context.workspace_scope.workspace_id,
            tool=ToolReference(self.definition.name, self.definition.version),
            normalizer_version=TOOL_OBSERVATION_NORMALIZER_VERSION,
            model_text=model_text,
            sources=tuple(
                ToolSource(
                    source_type="sec_submissions",
                    source_version=source.source_version,
                    locator=source.source_url,
                    observed_at=source.retrieved_at,
                    content_sha256=source.content_sha256,
                )
                for source in value.sources
            ),
            observed_at=observed_at,
            content_sha256=content_sha256,
        )


SEC_SEARCH_FILING_TOOL_NAME = "sec.search_filing"
SEC_SEARCH_FILING_TOOL_VERSION = "v1"
SEC_READ_FILING_SECTION_TOOL_NAME = "sec.read_filing_section"
SEC_READ_FILING_SECTION_TOOL_VERSION = "v1"


class SecSearchFilingInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1, max_length=2_000)


class SecFilingContentHitOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: UUID
    document_version_id: UUID
    snapshot_id: UUID
    accession: str
    title: str
    excerpt: str
    score: float = Field(ge=0, le=1)
    section: str
    page_number: int = Field(ge=1)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_url: str
    source_version: str


class SecSearchFilingOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: SecFilingContentStatus
    accession: str
    retrieval_profile_version: str
    hits: list[SecFilingContentHitOutput]
    error_code: str | None


def sec_search_filing_definition() -> ToolDefinition:
    return ToolDefinition(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        name=SEC_SEARCH_FILING_TOOL_NAME,
        version=SEC_SEARCH_FILING_TOOL_VERSION,
        description="Dense search within one imported, server-locked SEC filing snapshot.",
        input_schema_version="sec-search-filing-input-v1",
        output_schema_version="sec-search-filing-output-v1",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["query"],
            "properties": {"query": {"type": "string"}},
        },
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": [
                "status",
                "accession",
                "retrieval_profile_version",
                "hits",
                "error_code",
            ],
            "properties": {
                "status": {"type": "string"},
                "accession": {"type": "string"},
                "retrieval_profile_version": {"type": "string"},
                "hits": {"type": "array"},
                "error_code": {"type": ["string", "null"]},
            },
        },
        capability=WorkspaceAction.RUN_TOOL,
        timeout_ms=30_000,
        max_result_bytes=250_000,
        max_cost_micro_usd=1,
        cost_class=ToolCostClass.LOW,
        side_effect_class=ToolSideEffectClass.READ_ONLY,
        retry_classification=ToolRetryClassification.SAFE_READ_ONLY,
        approval_policy=ToolApprovalPolicy.AUTO_ALLOW,
        policy_version="sec-imported-filing-read-v1",
    )


class SecSearchFilingTool(PydanticToolAdapter[SecSearchFilingInput, SecSearchFilingOutput]):
    def __init__(self, service: SecFilingContentService) -> None:
        super().__init__(
            definition=sec_search_filing_definition(),
            input_model=SecSearchFilingInput,
            output_model=SecSearchFilingOutput,
        )
        self._service = service

    async def invoke(
        self,
        value: SecSearchFilingInput,
        runtime_context: TrustedRuntimeContext,
        *,
        idempotency_key: str | None,
    ) -> tuple[SecSearchFilingOutput, int]:
        if idempotency_key is not None:
            raise ToolExecutionError("tool_idempotency_key_unexpected")
        financial_scope = runtime_context.financial_scope
        if financial_scope is None or not runtime_context.knowledge_base_ids:
            raise ToolExecutionError("financial_scope_not_configured")
        try:
            result = await self._service.search(
                runtime_context.workspace_scope,
                knowledge_base_ids=runtime_context.knowledge_base_ids,
                financial_scope=financial_scope,
                query=value.query,
            )
        except SecFilingContentError as error:
            raise ToolExecutionError(error.code.value) from None
        except SecDisclosurePersistenceError:
            raise ToolExecutionError("sec_filing_content_unavailable") from None
        return (
            SecSearchFilingOutput(
                status=result.status,
                accession=result.accession,
                retrieval_profile_version=result.retrieval_profile_version,
                hits=[
                    SecFilingContentHitOutput(
                        chunk_id=hit.chunk_id,
                        document_version_id=hit.document_version_id,
                        snapshot_id=hit.snapshot_id,
                        accession=hit.accession,
                        title=hit.title,
                        excerpt=hit.excerpt,
                        score=hit.score,
                        section=hit.section,
                        page_number=hit.page_number,
                        content_sha256=hit.content_sha256,
                        source_content_sha256=hit.source_content_sha256,
                        source_url=hit.source_url,
                        source_version=hit.source_version,
                    )
                    for hit in result.hits
                ],
                error_code=result.error_code,
            ),
            0,
        )

    def normalize(
        self,
        value: SecSearchFilingOutput,
        runtime_context: TrustedRuntimeContext,
        *,
        call_id: UUID,
        run_id: UUID,
        observed_at: datetime,
    ) -> ToolObservation:
        model_text = json.dumps(
            value.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        content_sha256 = hashlib.sha256(model_text.encode("utf-8")).hexdigest()
        unique_sources = {(hit.snapshot_id, hit.source_version): hit for hit in value.hits}
        return ToolObservation(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            observation_id=uuid5(NAMESPACE_URL, f"{call_id}:sec-search-filing:v1"),
            call_id=call_id,
            run_id=run_id,
            workspace_id=runtime_context.workspace_scope.workspace_id,
            tool=ToolReference(self.definition.name, self.definition.version),
            normalizer_version=TOOL_OBSERVATION_NORMALIZER_VERSION,
            model_text=model_text,
            sources=tuple(
                ToolSource(
                    source_type="sec_filing_snapshot",
                    source_version=hit.source_version,
                    locator=hit.source_url,
                    observed_at=observed_at,
                    content_sha256=hit.source_content_sha256,
                )
                for hit in unique_sources.values()
            ),
            observed_at=observed_at,
            content_sha256=content_sha256,
        )


class SecReadFilingSectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_version_id: str = Field(
        pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$"
    )
    chunk_id: str = Field(
        pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$"
    )


class SecReadFilingSectionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    import_id: UUID
    snapshot_id: UUID
    accession: str
    document_version_id: UUID
    chunk_id: UUID
    title: str
    section: str
    text: str
    page_number: int = Field(ge=1)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_url: str
    source_version: str


def sec_read_filing_section_definition() -> ToolDefinition:
    return ToolDefinition(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        name=SEC_READ_FILING_SECTION_TOOL_NAME,
        version=SEC_READ_FILING_SECTION_TOOL_VERSION,
        description="Read one authorized chunk from the server-locked SEC filing snapshot.",
        input_schema_version="sec-read-filing-section-input-v1",
        output_schema_version="sec-read-filing-section-output-v1",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["document_version_id", "chunk_id"],
            "properties": {
                "document_version_id": {"type": "string"},
                "chunk_id": {"type": "string"},
            },
        },
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": [
                "import_id",
                "snapshot_id",
                "accession",
                "document_version_id",
                "chunk_id",
                "title",
                "section",
                "text",
                "page_number",
                "content_sha256",
                "source_content_sha256",
                "source_url",
                "source_version",
            ],
            "properties": {
                "import_id": {"type": "string"},
                "snapshot_id": {"type": "string"},
                "accession": {"type": "string"},
                "document_version_id": {"type": "string"},
                "chunk_id": {"type": "string"},
                "title": {"type": "string"},
                "section": {"type": "string"},
                "text": {"type": "string"},
                "page_number": {"type": "integer"},
                "content_sha256": {"type": "string"},
                "source_content_sha256": {"type": "string"},
                "source_url": {"type": "string"},
                "source_version": {"type": "string"},
            },
        },
        capability=WorkspaceAction.RUN_TOOL,
        timeout_ms=10_000,
        max_result_bytes=100_000,
        max_cost_micro_usd=1,
        cost_class=ToolCostClass.LOW,
        side_effect_class=ToolSideEffectClass.READ_ONLY,
        retry_classification=ToolRetryClassification.SAFE_READ_ONLY,
        approval_policy=ToolApprovalPolicy.AUTO_ALLOW,
        policy_version="sec-imported-filing-read-v1",
    )


class SecReadFilingSectionTool(
    PydanticToolAdapter[SecReadFilingSectionInput, SecReadFilingSectionOutput]
):
    def __init__(self, service: SecFilingContentService) -> None:
        super().__init__(
            definition=sec_read_filing_section_definition(),
            input_model=SecReadFilingSectionInput,
            output_model=SecReadFilingSectionOutput,
        )
        self._service = service

    async def invoke(
        self,
        value: SecReadFilingSectionInput,
        runtime_context: TrustedRuntimeContext,
        *,
        idempotency_key: str | None,
    ) -> tuple[SecReadFilingSectionOutput, int]:
        if idempotency_key is not None:
            raise ToolExecutionError("tool_idempotency_key_unexpected")
        financial_scope = runtime_context.financial_scope
        if financial_scope is None or not runtime_context.knowledge_base_ids:
            raise ToolExecutionError("financial_scope_not_configured")
        try:
            section = await self._service.read_section(
                runtime_context.workspace_scope,
                knowledge_base_ids=runtime_context.knowledge_base_ids,
                financial_scope=financial_scope,
                document_version_id=UUID(value.document_version_id),
                chunk_id=UUID(value.chunk_id),
            )
        except SecFilingContentError as error:
            raise ToolExecutionError(error.code.value) from None
        except SecDisclosurePersistenceError:
            raise ToolExecutionError("sec_filing_content_unavailable") from None
        return (
            SecReadFilingSectionOutput(
                import_id=section.import_id,
                snapshot_id=section.snapshot_id,
                accession=section.accession,
                document_version_id=section.document_version_id,
                chunk_id=section.chunk_id,
                title=section.title,
                section=section.section,
                text=section.text,
                page_number=section.page_number,
                content_sha256=section.content_sha256,
                source_content_sha256=section.source_content_sha256,
                source_url=section.source_url,
                source_version=section.source_version,
            ),
            0,
        )

    def normalize(
        self,
        value: SecReadFilingSectionOutput,
        runtime_context: TrustedRuntimeContext,
        *,
        call_id: UUID,
        run_id: UUID,
        observed_at: datetime,
    ) -> ToolObservation:
        model_text = json.dumps(
            value.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        content_sha256 = hashlib.sha256(model_text.encode("utf-8")).hexdigest()
        return ToolObservation(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            observation_id=uuid5(NAMESPACE_URL, f"{call_id}:sec-read-filing-section:v1"),
            call_id=call_id,
            run_id=run_id,
            workspace_id=runtime_context.workspace_scope.workspace_id,
            tool=ToolReference(self.definition.name, self.definition.version),
            normalizer_version=TOOL_OBSERVATION_NORMALIZER_VERSION,
            model_text=model_text,
            sources=(
                ToolSource(
                    source_type="sec_filing_snapshot",
                    source_version=value.source_version,
                    locator=value.source_url,
                    observed_at=observed_at,
                    content_sha256=value.source_content_sha256,
                ),
            ),
            observed_at=observed_at,
            content_sha256=content_sha256,
        )
