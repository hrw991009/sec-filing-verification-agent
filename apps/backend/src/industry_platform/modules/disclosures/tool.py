"""Typed SEC filer resolver Tool over the shared Tool Registry contract."""

import hashlib
import json
from datetime import datetime
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

from industry_platform.modules.agent_runtime.context import TrustedRuntimeContext
from industry_platform.modules.agent_runtime.domain import AGENT_RUNTIME_SCHEMA_VERSION
from industry_platform.modules.disclosures.diff import (
    SEC_MAX_DIFF_TOOL_FACT_CHANGES,
    SecFilingDiffService,
)
from industry_platform.modules.disclosures.domain import (
    SEC_COMPANY_TICKERS_URL,
    SecDisclosurePersistenceError,
    SecFilerMatchKind,
    SecFilerResolutionStatus,
    SecFilingContentError,
    SecFilingContentStatus,
    SecFilingRetrievalTrace,
    SecSourceError,
    SecXbrlFactQuery,
    SecXbrlPeriodKind,
    SecXbrlSourceKind,
)
from industry_platform.modules.disclosures.filing_content_service import SecFilingContentService
from industry_platform.modules.disclosures.monitor import (
    SEC_MONITOR_RULE_SET_VERSION,
    SecMonitorRule,
    SecMonitorRuleKind,
)
from industry_platform.modules.disclosures.schemas import (
    SecFilingDiffResponse,
    SecFilingSelectionResponse,
    SecXbrlFactResponse,
)
from industry_platform.modules.disclosures.service import (
    SecFilerResolutionService,
    SecFilingSelectionService,
)
from industry_platform.modules.disclosures.xbrl_service import SecXbrlService
from industry_platform.modules.financial_verification.domain import sec_xbrl_evidence_ref
from industry_platform.modules.financial_verification.schemas import FinancialScopePayload
from industry_platform.modules.jobs.domain import ExecutionScope, ScheduleDefinition
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


SEC_MONITOR_SUBSCRIBE_TOOL_NAME = "sec.monitor.subscribe"
SEC_MONITOR_SUBSCRIBE_TOOL_VERSION = "v1"
_MONITOR_VALIDATION_WORKSPACE_ID = UUID("00000000-0000-4000-8000-000000000001")


class SecMonitorSubscribeRuleInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[
        "new_filing",
        "amendment",
        "fact_absolute_change",
        "section_change",
    ]
    section_query: str = Field(min_length=1, max_length=500)
    taxonomy: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z_][A-Za-z0-9._-]{0,127}$",
    )
    concept: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z_][A-Za-z0-9._-]{0,255}$",
    )
    unit: str | None = Field(default=None, min_length=1, max_length=255)
    threshold: str | None = Field(default=None, min_length=1, max_length=200)
    comparator: Literal["absolute_delta_gte"] | None = None

    @model_validator(mode="after")
    def validate_rule(self) -> "SecMonitorSubscribeRuleInput":
        SecMonitorRule(
            rule_id=uuid5(NAMESPACE_URL, f"monitor-rule-validation:{self.model_dump_json()}"),
            kind=SecMonitorRuleKind(self.kind),
            rule_version=SEC_MONITOR_RULE_SET_VERSION,
            section_query=self.section_query,
            taxonomy=self.taxonomy,
            concept=self.concept,
            unit=self.unit,
            threshold=self.threshold,
            comparator=self.comparator,
        )
        return self


class SecMonitorSubscribeInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cik: str = Field(pattern=r"^[0-9]{10}$")
    knowledge_base_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    )
    allowed_forms: list[Literal["10-K", "10-K/A", "10-Q", "10-Q/A"]] = Field(
        min_length=1,
        max_length=4,
    )
    cron_expression: str = Field(min_length=1, max_length=120)
    timezone_name: str = Field(min_length=1, max_length=64)
    rules: list[SecMonitorSubscribeRuleInput] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def validate_subscription(self) -> "SecMonitorSubscribeInput":
        if self.cik == "0000000000" or len(set(self.allowed_forms)) != len(self.allowed_forms):
            raise ValueError("SEC Monitor subscription scope is invalid")
        ScheduleDefinition(
            scope=ExecutionScope(workspace_id=_MONITOR_VALIDATION_WORKSPACE_ID),
            name="sec-monitor-validation",
            task_name="industry_platform.disclosures.monitor.execute",
            cron_expression=self.cron_expression,
            timezone_name=self.timezone_name,
            payload={"schema_version": 1, "monitor_id": str(_MONITOR_VALIDATION_WORKSPACE_ID)},
        )
        return self


class SecMonitorSubscribeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["approval_required"]
    approval_request_id: UUID


def sec_monitor_subscribe_definition() -> ToolDefinition:
    return ToolDefinition(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        name=SEC_MONITOR_SUBSCRIBE_TOOL_NAME,
        version=SEC_MONITOR_SUBSCRIBE_TOOL_VERSION,
        description="Request a durable, human-approved SEC filing Monitor subscription.",
        input_schema_version="sec-monitor-subscribe-input-v1",
        output_schema_version="sec-monitor-subscribe-output-v1",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": [
                "cik",
                "knowledge_base_id",
                "allowed_forms",
                "cron_expression",
                "timezone_name",
                "rules",
            ],
            "properties": {
                "cik": {"type": "string", "pattern": r"^[0-9]{10}$"},
                "knowledge_base_id": {
                    "type": "string",
                    "pattern": (
                        r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
                        r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
                    ),
                },
                "allowed_forms": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["10-K", "10-K/A", "10-Q", "10-Q/A"]},
                    "minItems": 1,
                    "maxItems": 4,
                },
                "cron_expression": {"type": "string", "minLength": 1, "maxLength": 120},
                "timezone_name": {"type": "string", "minLength": 1, "maxLength": 64},
                "rules": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 16,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "kind",
                            "section_query",
                            "taxonomy",
                            "concept",
                            "unit",
                            "threshold",
                            "comparator",
                        ],
                        "properties": {
                            "kind": {
                                "type": "string",
                                "enum": [
                                    "new_filing",
                                    "amendment",
                                    "fact_absolute_change",
                                    "section_change",
                                ],
                            },
                            "section_query": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 500,
                            },
                            "taxonomy": {
                                "type": ["string", "null"],
                                "pattern": r"^[A-Za-z_][A-Za-z0-9._-]{0,127}$",
                            },
                            "concept": {
                                "type": ["string", "null"],
                                "pattern": r"^[A-Za-z_][A-Za-z0-9._-]{0,255}$",
                            },
                            "unit": {"type": ["string", "null"]},
                            "threshold": {"type": ["string", "null"]},
                            "comparator": {
                                "type": ["string", "null"],
                                "enum": ["absolute_delta_gte", None],
                            },
                        },
                    },
                },
            },
        },
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["status", "approval_request_id"],
            "properties": {
                "status": {"type": "string"},
                "approval_request_id": {"type": "string"},
            },
        },
        capability=WorkspaceAction.RUN_RESEARCH,
        timeout_ms=5_000,
        max_result_bytes=10_000,
        max_cost_micro_usd=1,
        cost_class=ToolCostClass.LOW,
        side_effect_class=ToolSideEffectClass.IDEMPOTENT_WRITE,
        retry_classification=ToolRetryClassification.IDEMPOTENT_WRITE,
        approval_policy=ToolApprovalPolicy.REQUIRE_APPROVAL,
        policy_version="sec-monitor-subscription-hitl-v1",
    )


class SecMonitorSubscribeTool(
    PydanticToolAdapter[SecMonitorSubscribeInput, SecMonitorSubscribeOutput]
):
    def __init__(self) -> None:
        super().__init__(
            definition=sec_monitor_subscribe_definition(),
            input_model=SecMonitorSubscribeInput,
            output_model=SecMonitorSubscribeOutput,
        )

    async def invoke(
        self,
        value: SecMonitorSubscribeInput,
        runtime_context: TrustedRuntimeContext,
        *,
        idempotency_key: str | None,
    ) -> tuple[SecMonitorSubscribeOutput, int]:
        del value, runtime_context, idempotency_key
        raise ToolExecutionError("monitor_approval_bypass_rejected")

    def normalize(
        self,
        value: SecMonitorSubscribeOutput,
        runtime_context: TrustedRuntimeContext,
        *,
        call_id: UUID,
        run_id: UUID,
        observed_at: datetime,
    ) -> ToolObservation:
        del value, runtime_context, call_id, run_id, observed_at
        raise ToolExecutionError("monitor_approval_bypass_rejected")


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


class SecFilingTableCellOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    table_index: int = Field(ge=1)
    row_index: int = Field(ge=1)
    column_index: int = Field(ge=1)
    row_span: int = Field(ge=1)
    column_span: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=4_000)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


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
    retrieval_channels: list[str]
    dense_rank: int | None
    lexical_rank: int | None
    rrf_score: float | None = Field(default=None, ge=0, le=1)
    rerank_score: float | None = Field(default=None, ge=0, le=1)
    index_version: str
    table_cells: list[SecFilingTableCellOutput]


class SecFilingRetrievalTraceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_version: str
    dense_candidate_count: int = Field(ge=0, le=100)
    lexical_candidate_count: int = Field(ge=0, le=100)
    fused_candidate_count: int = Field(ge=0, le=100)
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
    def from_domain(cls, value: SecFilingRetrievalTrace) -> "SecFilingRetrievalTraceOutput":
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


class SecSearchFilingOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: SecFilingContentStatus
    accession: str
    retrieval_profile_version: str
    hits: list[SecFilingContentHitOutput]
    error_code: str | None
    retrieval_trace: SecFilingRetrievalTraceOutput | None = None
    financial_scope: FinancialScopePayload | None = None


def sec_search_filing_definition() -> ToolDefinition:
    return ToolDefinition(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        name=SEC_SEARCH_FILING_TOOL_NAME,
        version=SEC_SEARCH_FILING_TOOL_VERSION,
        description="Versioned search within one imported, server-locked SEC filing snapshot.",
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
                "retrieval_trace",
                "financial_scope",
            ],
            "properties": {
                "status": {"type": "string"},
                "accession": {"type": "string"},
                "retrieval_profile_version": {"type": "string"},
                "hits": {"type": "array"},
                "error_code": {"type": ["string", "null"]},
                "retrieval_trace": {"type": ["object", "null"]},
                "financial_scope": {"type": ["object", "null"]},
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
                        retrieval_channels=list(hit.retrieval_channels),
                        dense_rank=hit.dense_rank,
                        lexical_rank=hit.lexical_rank,
                        rrf_score=hit.rrf_score,
                        rerank_score=hit.rerank_score,
                        index_version=hit.index_version,
                        table_cells=[
                            SecFilingTableCellOutput(
                                table_index=cell.table_index,
                                row_index=cell.row_index,
                                column_index=cell.column_index,
                                row_span=cell.row_span,
                                column_span=cell.column_span,
                                text=cell.text,
                                content_sha256=cell.content_sha256,
                            )
                            for cell in hit.table_cells
                        ],
                    )
                    for hit in result.hits
                ],
                error_code=result.error_code,
                retrieval_trace=(
                    None
                    if result.retrieval_trace is None
                    else SecFilingRetrievalTraceOutput.from_domain(result.retrieval_trace)
                ),
                financial_scope=FinancialScopePayload.from_domain(financial_scope),
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
                    source_type="sec_filing_text",
                    source_version=hit.source_version,
                    locator=f"sec://filing-chunks/{hit.chunk_id}",
                    observed_at=observed_at,
                    content_sha256=hit.content_sha256,
                )
                for hit in value.hits
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
    table_cells: list[SecFilingTableCellOutput]
    financial_scope: FinancialScopePayload | None = None


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
                "table_cells",
                "financial_scope",
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
                "table_cells": {"type": "array"},
                "financial_scope": {"type": ["object", "null"]},
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
                table_cells=[
                    SecFilingTableCellOutput(
                        table_index=cell.table_index,
                        row_index=cell.row_index,
                        column_index=cell.column_index,
                        row_span=cell.row_span,
                        column_span=cell.column_span,
                        text=cell.text,
                        content_sha256=cell.content_sha256,
                    )
                    for cell in section.table_cells
                ],
                financial_scope=FinancialScopePayload.from_domain(financial_scope),
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
                    source_type="sec_filing_text",
                    source_version=value.source_version,
                    locator=f"sec://filing-chunks/{value.chunk_id}",
                    observed_at=observed_at,
                    content_sha256=value.content_sha256,
                ),
            ),
            observed_at=observed_at,
            content_sha256=content_sha256,
        )


SEC_GET_XBRL_FACTS_TOOL_NAME = "sec.get_xbrl_facts"
SEC_GET_XBRL_FACTS_TOOL_VERSION = "v1"


class SecGetXbrlFactsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    taxonomy: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z_][A-Za-z0-9._-]{0,255}$",
    )
    concept: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z_][A-Za-z0-9._-]{0,255}$",
    )
    unit: str | None = Field(default=None, min_length=1, max_length=255)
    period_kind: Literal["instant", "duration", "forever"] | None = None
    source_kinds: list[Literal["companyfacts_aggregate", "raw_inline", "raw_instance"]] = Field(
        default_factory=lambda: [kind.value for kind in SecXbrlSourceKind],
        min_length=1,
        max_length=3,
    )
    limit: int = Field(default=16, ge=1, le=16)

    def to_domain(self) -> SecXbrlFactQuery:
        return SecXbrlFactQuery(
            taxonomy=self.taxonomy,
            concept=self.concept,
            unit=self.unit,
            period_kind=(None if self.period_kind is None else SecXbrlPeriodKind(self.period_kind)),
            source_kinds=tuple(SecXbrlSourceKind(kind) for kind in self.source_kinds),
            limit=self.limit,
        )


class SecXbrlFactToolResponse(SecXbrlFactResponse):
    evidence_ref: UUID | None = None


class SecGetXbrlFactsOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: SecFilingContentStatus
    accession: str
    facts: list[SecXbrlFactToolResponse]
    error_code: str | None
    financial_scope: FinancialScopePayload | None = None
    knowledge_base_ids: list[UUID] = Field(default_factory=list)


def sec_get_xbrl_facts_definition() -> ToolDefinition:
    return ToolDefinition(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        name=SEC_GET_XBRL_FACTS_TOOL_NAME,
        version=SEC_GET_XBRL_FACTS_TOOL_VERSION,
        description=(
            "Read source-typed aggregate or raw XBRL facts from one imported, "
            "server-locked SEC accession."
        ),
        input_schema_version="sec-get-xbrl-facts-input-v1",
        output_schema_version="sec-get-xbrl-facts-output-v1",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "taxonomy": {"type": ["string", "null"]},
                "concept": {"type": ["string", "null"]},
                "unit": {"type": ["string", "null"]},
                "period_kind": {
                    "type": ["string", "null"],
                    "enum": ["instant", "duration", "forever", None],
                },
                "source_kinds": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [kind.value for kind in SecXbrlSourceKind],
                    },
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 16},
            },
        },
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": [
                "status",
                "accession",
                "facts",
                "error_code",
                "financial_scope",
                "knowledge_base_ids",
            ],
            "properties": {
                "status": {"type": "string"},
                "accession": {"type": "string"},
                "facts": {"type": "array"},
                "error_code": {"type": ["string", "null"]},
                "financial_scope": {"type": ["object", "null"]},
                "knowledge_base_ids": {"type": "array", "items": {"type": "string"}},
            },
        },
        capability=WorkspaceAction.RUN_TOOL,
        timeout_ms=10_000,
        max_result_bytes=250_000,
        max_cost_micro_usd=1,
        cost_class=ToolCostClass.LOW,
        side_effect_class=ToolSideEffectClass.READ_ONLY,
        retry_classification=ToolRetryClassification.SAFE_READ_ONLY,
        approval_policy=ToolApprovalPolicy.AUTO_ALLOW,
        policy_version="sec-imported-xbrl-read-v1",
    )


class SecGetXbrlFactsTool(PydanticToolAdapter[SecGetXbrlFactsInput, SecGetXbrlFactsOutput]):
    def __init__(self, service: SecXbrlService) -> None:
        super().__init__(
            definition=sec_get_xbrl_facts_definition(),
            input_model=SecGetXbrlFactsInput,
            output_model=SecGetXbrlFactsOutput,
        )
        self._service = service

    async def invoke(
        self,
        value: SecGetXbrlFactsInput,
        runtime_context: TrustedRuntimeContext,
        *,
        idempotency_key: str | None,
    ) -> tuple[SecGetXbrlFactsOutput, int]:
        if idempotency_key is not None:
            raise ToolExecutionError("tool_idempotency_key_unexpected")
        financial_scope = runtime_context.financial_scope
        if financial_scope is None or not runtime_context.knowledge_base_ids:
            raise ToolExecutionError("financial_scope_not_configured")
        try:
            result = await self._service.get_facts(
                runtime_context.workspace_scope,
                knowledge_base_ids=runtime_context.knowledge_base_ids,
                financial_scope=financial_scope,
                query=value.to_domain(),
            )
        except SecFilingContentError as error:
            raise ToolExecutionError(error.code.value) from None
        except SecDisclosurePersistenceError:
            raise ToolExecutionError("sec_xbrl_facts_unavailable") from None
        return (
            SecGetXbrlFactsOutput(
                status=result.status,
                accession=result.accession,
                facts=[
                    SecXbrlFactToolResponse(
                        **SecXbrlFactResponse.from_domain(fact).model_dump(),
                        evidence_ref=sec_xbrl_evidence_ref(
                            workspace_id=runtime_context.workspace_scope.workspace_id,
                            fact_id=fact.id,
                            as_of=financial_scope.as_of,
                            authorization_role=runtime_context.workspace_scope.role,
                        ),
                    )
                    for fact in result.facts
                ],
                error_code=result.error_code,
                financial_scope=FinancialScopePayload.from_domain(financial_scope),
                knowledge_base_ids=list(runtime_context.knowledge_base_ids),
            ),
            0,
        )

    def normalize(
        self,
        value: SecGetXbrlFactsOutput,
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
        if len(value.facts) > MAX_TOOL_SOURCES:
            raise ValueError("SEC XBRL Tool source count exceeds Observation contract")
        return ToolObservation(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            observation_id=uuid5(NAMESPACE_URL, f"{call_id}:sec-get-xbrl-facts:v1"),
            call_id=call_id,
            run_id=run_id,
            workspace_id=runtime_context.workspace_scope.workspace_id,
            tool=ToolReference(self.definition.name, self.definition.version),
            normalizer_version=TOOL_OBSERVATION_NORMALIZER_VERSION,
            model_text=model_text,
            sources=tuple(
                ToolSource(
                    source_type="sec_xbrl_fact",
                    source_version=fact.source_version,
                    locator=f"sec://xbrl-facts/{fact.id}",
                    observed_at=fact.retrieved_at,
                    content_sha256=fact.content_sha256,
                )
                for fact in value.facts
            ),
            observed_at=observed_at,
            content_sha256=content_sha256,
        )


SEC_DIFF_FILINGS_TOOL_NAME = "sec.diff_filings"
SEC_DIFF_FILINGS_TOOL_VERSION = "v1"


class SecDiffFilingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    comparison_accession: str = Field(pattern=r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
    section_query: str = Field(min_length=1, max_length=500)
    taxonomy: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z_][A-Za-z0-9._-]{0,255}$",
    )
    concept: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z_][A-Za-z0-9._-]{0,255}$",
    )
    fact_limit: int = Field(default=SEC_MAX_DIFF_TOOL_FACT_CHANGES, ge=1, le=6)


class SecDiffFactEvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fact_id: UUID
    evidence_ref: UUID


class SecDiffFilingsOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    result: SecFilingDiffResponse
    fact_evidence_refs: list[SecDiffFactEvidenceRef]
    financial_scope: FinancialScopePayload
    knowledge_base_ids: list[UUID]


def sec_diff_filings_definition() -> ToolDefinition:
    return ToolDefinition(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        name=SEC_DIFF_FILINGS_TOOL_NAME,
        version=SEC_DIFF_FILINGS_TOOL_VERSION,
        description=(
            "Compare a base filing with its resolved amendment, or two adjacent comparable "
            "periods, using authorized XBRL facts and one matched filing section. The Tool "
            "does not calculate numeric deltas; pass returned fact Evidence refs to "
            "finance.calculate."
        ),
        input_schema_version="sec-diff-filings-input-v1",
        output_schema_version="sec-diff-filings-output-v1",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": [
                "comparison_accession",
                "section_query",
                "taxonomy",
                "concept",
                "fact_limit",
            ],
            "properties": {
                "comparison_accession": {"type": "string"},
                "section_query": {"type": "string"},
                "taxonomy": {"type": ["string", "null"]},
                "concept": {"type": ["string", "null"]},
                "fact_limit": {"type": "integer", "minimum": 1, "maximum": 6},
            },
        },
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": [
                "result",
                "fact_evidence_refs",
                "financial_scope",
                "knowledge_base_ids",
            ],
            "properties": {
                "result": {"type": "object"},
                "fact_evidence_refs": {"type": "array"},
                "financial_scope": {"type": "object"},
                "knowledge_base_ids": {"type": "array", "items": {"type": "string"}},
            },
        },
        capability=WorkspaceAction.RUN_TOOL,
        timeout_ms=45_000,
        max_result_bytes=500_000,
        max_cost_micro_usd=1,
        cost_class=ToolCostClass.LOW,
        side_effect_class=ToolSideEffectClass.READ_ONLY,
        retry_classification=ToolRetryClassification.SAFE_READ_ONLY,
        approval_policy=ToolApprovalPolicy.AUTO_ALLOW,
        policy_version="sec-imported-filing-diff-v1",
    )


class SecDiffFilingsTool(PydanticToolAdapter[SecDiffFilingsInput, SecDiffFilingsOutput]):
    def __init__(self, service: SecFilingDiffService) -> None:
        super().__init__(
            definition=sec_diff_filings_definition(),
            input_model=SecDiffFilingsInput,
            output_model=SecDiffFilingsOutput,
        )
        self._service = service

    async def invoke(
        self,
        value: SecDiffFilingsInput,
        runtime_context: TrustedRuntimeContext,
        *,
        idempotency_key: str | None,
    ) -> tuple[SecDiffFilingsOutput, int]:
        if idempotency_key is not None:
            raise ToolExecutionError("tool_idempotency_key_unexpected")
        financial_scope = runtime_context.financial_scope
        if financial_scope is None or not runtime_context.knowledge_base_ids:
            raise ToolExecutionError("financial_scope_not_configured")
        try:
            result = await self._service.compare(
                runtime_context.workspace_scope,
                knowledge_base_ids=runtime_context.knowledge_base_ids,
                financial_scope=financial_scope,
                comparison_accession=value.comparison_accession,
                section_query=value.section_query,
                taxonomy=value.taxonomy,
                concept=value.concept,
                fact_limit=value.fact_limit,
            )
        except SecFilingContentError as error:
            raise ToolExecutionError(error.code.value) from None
        except SecDisclosurePersistenceError:
            raise ToolExecutionError("sec_filing_diff_unavailable") from None
        fact_ids = tuple(
            dict.fromkeys(
                fact.id
                for change in result.fact_changes
                for fact in (change.baseline, change.target)
                if fact is not None
            )
        )
        return (
            SecDiffFilingsOutput(
                result=SecFilingDiffResponse.from_domain(result),
                fact_evidence_refs=[
                    SecDiffFactEvidenceRef(
                        fact_id=fact_id,
                        evidence_ref=sec_xbrl_evidence_ref(
                            workspace_id=runtime_context.workspace_scope.workspace_id,
                            fact_id=fact_id,
                            as_of=financial_scope.as_of,
                            authorization_role=runtime_context.workspace_scope.role,
                        ),
                    )
                    for fact_id in fact_ids
                ],
                financial_scope=FinancialScopePayload.from_domain(financial_scope),
                knowledge_base_ids=list(runtime_context.knowledge_base_ids),
            ),
            0,
        )

    def normalize(
        self,
        value: SecDiffFilingsOutput,
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
        sources: list[ToolSource] = []
        seen: set[str] = set()
        for change in value.result.fact_changes:
            for fact in (change.baseline, change.target):
                if fact is None:
                    continue
                locator = f"sec://xbrl-facts/{fact.id}"
                if locator in seen:
                    continue
                seen.add(locator)
                sources.append(
                    ToolSource(
                        source_type="sec_xbrl_fact",
                        source_version=fact.source_version,
                        locator=locator,
                        observed_at=fact.retrieved_at,
                        content_sha256=fact.content_sha256,
                    )
                )
        section = value.result.section_change
        if section is not None:
            for hit in (section.baseline, section.target):
                locator = f"sec://filing-chunks/{hit.chunk_id}"
                if locator in seen:
                    continue
                seen.add(locator)
                sources.append(
                    ToolSource(
                        source_type="sec_filing_text",
                        source_version=hit.source_version,
                        locator=locator,
                        observed_at=observed_at,
                        content_sha256=hit.content_sha256,
                    )
                )
        if len(sources) > MAX_TOOL_SOURCES:
            raise ValueError("SEC filing diff source count exceeds Observation contract")
        return ToolObservation(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            observation_id=uuid5(NAMESPACE_URL, f"{call_id}:sec-diff-filings:v1"),
            call_id=call_id,
            run_id=run_id,
            workspace_id=runtime_context.workspace_scope.workspace_id,
            tool=ToolReference(self.definition.name, self.definition.version),
            normalizer_version=TOOL_OBSERVATION_NORMALIZER_VERSION,
            model_text=model_text,
            sources=tuple(sources),
            observed_at=observed_at,
            content_sha256=content_sha256,
        )
