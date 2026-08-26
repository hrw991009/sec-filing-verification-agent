"""Versioned Knowledge search Tool over Dense candidates and PostgreSQL truth."""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from industry_platform.modules.agent_runtime.context import TrustedRuntimeContext
from industry_platform.modules.agent_runtime.domain import AGENT_RUNTIME_SCHEMA_VERSION
from industry_platform.modules.financial_verification.schemas import FinancialScopePayload
from industry_platform.modules.retrieval.domain import (
    KNOWLEDGE_SEARCH_TOOL_VERSION,
    KnowledgeContextSource,
    KnowledgeSearchStatus,
)
from industry_platform.modules.retrieval.service import KnowledgeSearchService
from industry_platform.modules.tools.domain import (
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

KNOWLEDGE_SEARCH_TOOL_NAME = "knowledge_search"
KNOWLEDGE_SEC_SOURCE_TYPE = "knowledge_sec_filing_chunk"


class KnowledgeSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1, max_length=1_000)


class KnowledgeSearchItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_ref: UUID
    knowledge_base_id: UUID
    document_id: UUID
    document_version_id: UUID
    chunk_id: UUID
    title: str
    excerpt: str
    score: float = Field(ge=0, le=1)
    page_number: int = Field(ge=1)
    section: str
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    parser_version: str
    chunker_version: str
    index_version: str
    dataset_version: str
    cik: str
    accession: str
    form: str
    report_period: date
    filed_at: datetime
    accepted_at: datetime
    primary_document: str
    canonical_url: str


class KnowledgeSearchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: KnowledgeSearchStatus
    financial_scope: FinancialScopePayload
    items: list[KnowledgeSearchItem]
    error_code: str | None = None


def knowledge_search_definition() -> ToolDefinition:
    return ToolDefinition(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        name=KNOWLEDGE_SEARCH_TOOL_NAME,
        version=KNOWLEDGE_SEARCH_TOOL_VERSION,
        description=(
            "Search the server-pinned SEC filing scope in authorized ready Knowledge Bases."
        ),
        input_schema_version="knowledge-search-input-v1",
        output_schema_version="knowledge-search-output-v1",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["query"],
            "properties": {"query": {"type": "string"}},
        },
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["status", "financial_scope", "items", "error_code"],
            "properties": {
                "status": {"type": "string"},
                "financial_scope": {"type": "object"},
                "items": {"type": "array"},
                "error_code": {"type": ["string", "null"]},
            },
        },
        capability=WorkspaceAction.RUN_TOOL,
        timeout_ms=30_000,
        max_result_bytes=300_000,
        max_cost_micro_usd=1,
        cost_class=ToolCostClass.LOW,
        side_effect_class=ToolSideEffectClass.READ_ONLY,
        retry_classification=ToolRetryClassification.SAFE_READ_ONLY,
        approval_policy=ToolApprovalPolicy.AUTO_ALLOW,
        policy_version="knowledge-sec-fixture-read-v1",
    )


class KnowledgeSearchTool(PydanticToolAdapter[KnowledgeSearchInput, KnowledgeSearchOutput]):
    def __init__(self, service: KnowledgeSearchService) -> None:
        super().__init__(
            definition=knowledge_search_definition(),
            input_model=KnowledgeSearchInput,
            output_model=KnowledgeSearchOutput,
        )
        self._service = service

    async def invoke(
        self,
        value: KnowledgeSearchInput,
        runtime_context: TrustedRuntimeContext,
        *,
        idempotency_key: str | None,
    ) -> tuple[KnowledgeSearchOutput, int]:
        if idempotency_key is not None:
            raise ToolExecutionError("tool_idempotency_key_unexpected")
        financial_scope = runtime_context.financial_scope
        if financial_scope is None or not runtime_context.knowledge_base_ids:
            raise ToolExecutionError("financial_scope_not_configured")
        result = await self._service.search(
            runtime_context.workspace_scope,
            knowledge_base_ids=runtime_context.knowledge_base_ids,
            financial_scope=financial_scope,
            query=value.query,
        )
        return (
            KnowledgeSearchOutput(
                status=result.status,
                financial_scope=FinancialScopePayload.from_domain(financial_scope),
                items=[
                    KnowledgeSearchItem(
                        evidence_ref=hit.evidence_ref,
                        knowledge_base_id=hit.knowledge_base_id,
                        document_id=hit.document_id,
                        document_version_id=hit.document_version_id,
                        chunk_id=hit.chunk_id,
                        title=hit.title,
                        excerpt=hit.excerpt,
                        score=hit.score,
                        page_number=hit.page_number,
                        section=hit.section,
                        content_sha256=hit.content_sha256,
                        parser_version=hit.parser_version,
                        chunker_version=hit.chunker_version,
                        index_version=hit.index_version,
                        dataset_version=hit.fixture.dataset_version,
                        cik=hit.fixture.cik,
                        accession=hit.fixture.accession,
                        form=hit.fixture.form,
                        report_period=hit.fixture.report_period,
                        filed_at=hit.fixture.filed_at,
                        accepted_at=hit.fixture.accepted_at,
                        primary_document=hit.fixture.primary_document,
                        canonical_url=hit.fixture.canonical_url,
                    )
                    for hit in result.hits
                ],
                error_code=result.error_code,
            ),
            0,
        )

    def normalize(
        self,
        value: KnowledgeSearchOutput,
        runtime_context: TrustedRuntimeContext,
        *,
        call_id: UUID,
        run_id: UUID,
        observed_at: datetime,
    ) -> ToolObservation:
        context_sources = tuple(
            KnowledgeContextSource(
                evidence_ref=item.evidence_ref,
                excerpt=item.excerpt,
                content_sha256=item.content_sha256,
                source_version=item.dataset_version,
            )
            for item in value.items
        )
        if value.items:
            lines = [
                (
                    f"[S{index}] evidence_ref={item.evidence_ref} "
                    f"accession={item.accession} section={item.section} "
                    f"page={item.page_number} score={item.score:.6f}\n{source.excerpt}"
                )
                for index, (item, source) in enumerate(
                    zip(value.items, context_sources, strict=True),
                    start=1,
                )
            ]
            model_text = "\n\n".join(lines)
        else:
            model_text = f"knowledge_search status={value.status.value}"
            if value.error_code is not None:
                model_text += f" error_code={value.error_code}"
        content_sha256 = hashlib.sha256(model_text.encode("utf-8")).hexdigest()
        return ToolObservation(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            observation_id=uuid5(NAMESPACE_URL, f"{call_id}:knowledge-search:v1"),
            call_id=call_id,
            run_id=run_id,
            workspace_id=runtime_context.workspace_scope.workspace_id,
            tool=ToolReference(self.definition.name, self.definition.version),
            normalizer_version=TOOL_OBSERVATION_NORMALIZER_VERSION,
            model_text=model_text,
            sources=tuple(
                ToolSource(
                    source_type=KNOWLEDGE_SEC_SOURCE_TYPE,
                    source_version=source.source_version,
                    locator=(
                        "fixture://sec-filings/"
                        f"{item.accession}/{item.document_version_id}/{item.chunk_id}"
                    ),
                    observed_at=observed_at,
                    content_sha256=source.content_sha256,
                )
                for item, source in zip(value.items, context_sources, strict=True)
            ),
            observed_at=observed_at,
            content_sha256=content_sha256,
        )
