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
    SecSourceError,
)
from industry_platform.modules.disclosures.service import SecFilerResolutionService
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
