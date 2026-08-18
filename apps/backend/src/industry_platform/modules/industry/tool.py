"""Real multi-domain industry search Tool over the bounded Provider registry."""

import hashlib
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from industry_platform.modules.agent_runtime.context import TrustedRuntimeContext
from industry_platform.modules.agent_runtime.domain import AGENT_RUNTIME_SCHEMA_VERSION
from industry_platform.modules.industry.domain import (
    INDUSTRY_PRESETS_BY_CODE,
    IndustryProviderError,
    ProviderQuery,
    SourceKind,
)
from industry_platform.modules.industry.ports import ProviderRegistryPort
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

INDUSTRY_WEB_SEARCH_TOOL_NAME = "industry.web_search"
INDUSTRY_WEB_SEARCH_TOOL_VERSION = "v1"


class IndustryWebSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    industry_code: str = Field(pattern=r"^(smart_transport|fintech|healthcare|energy_power)$")
    source_kind: str = Field(pattern=r"^(news|policy|tender|stock)$")
    query: str = Field(min_length=1, max_length=120, pattern=r"^[\w .-]+$")
    limit: int = Field(ge=1, le=10)


class IndustryWebSearchResultItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str
    summary: str
    locator: str
    source_version: str
    content_sha256: str


class IndustryWebSearchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: list[IndustryWebSearchResultItem]


def industry_web_search_definition() -> ToolDefinition:
    return ToolDefinition(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        name=INDUSTRY_WEB_SEARCH_TOOL_NAME,
        version=INDUSTRY_WEB_SEARCH_TOOL_VERSION,
        description=(
            "Search one allowlisted real industry source and return bounded attributed results."
        ),
        input_schema_version="industry-web-search-input-v1",
        output_schema_version="industry-web-search-output-v1",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["industry_code", "source_kind", "query", "limit"],
            "properties": {
                "industry_code": {
                    "type": "string",
                    "enum": ["smart_transport", "fintech", "healthcare", "energy_power"],
                },
                "source_kind": {
                    "type": "string",
                    "enum": ["news", "policy", "tender", "stock"],
                },
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["items"],
            "properties": {"items": {"type": "array"}},
        },
        capability=WorkspaceAction.RUN_TOOL,
        timeout_ms=30_000,
        max_result_bytes=200_000,
        max_cost_micro_usd=1,
        cost_class=ToolCostClass.LOW,
        side_effect_class=ToolSideEffectClass.READ_ONLY,
        retry_classification=ToolRetryClassification.SAFE_READ_ONLY,
        approval_policy=ToolApprovalPolicy.AUTO_ALLOW,
        policy_version="industry-public-read-policy-v1",
    )


class IndustryWebSearchTool(PydanticToolAdapter[IndustryWebSearchInput, IndustryWebSearchOutput]):
    def __init__(self, providers: ProviderRegistryPort) -> None:
        super().__init__(
            definition=industry_web_search_definition(),
            input_model=IndustryWebSearchInput,
            output_model=IndustryWebSearchOutput,
        )
        self._providers = providers

    async def invoke(
        self,
        value: IndustryWebSearchInput,
        runtime_context: TrustedRuntimeContext,
        *,
        idempotency_key: str | None,
    ) -> tuple[IndustryWebSearchOutput, int]:
        del runtime_context
        if idempotency_key is not None:
            raise ToolExecutionError("tool_idempotency_key_unexpected")
        industry = INDUSTRY_PRESETS_BY_CODE.get(value.industry_code)
        if industry is None:
            raise ToolExecutionError("industry_not_found")
        try:
            kind = SourceKind(value.source_kind)
            page = await self._providers.provider(kind).fetch(
                ProviderQuery(
                    industry=industry,
                    query=value.query,
                    limit=value.limit,
                )
            )
        except IndustryProviderError as error:
            raise ToolExecutionError(error.code.value) from None
        if not page.items:
            raise ToolExecutionError("provider_no_results")
        return (
            IndustryWebSearchOutput(
                items=[
                    IndustryWebSearchResultItem(
                        title=item.title,
                        summary=item.summary,
                        locator=item.locator,
                        source_version=page.definition.version,
                        content_sha256=item.content_sha256,
                    )
                    for item in page.items
                ]
            ),
            0,
        )

    def normalize(
        self,
        value: IndustryWebSearchOutput,
        runtime_context: TrustedRuntimeContext,
        *,
        call_id: UUID,
        run_id: UUID,
        observed_at: datetime,
    ) -> ToolObservation:
        lines = [
            f"[S{index}] {item.title} — {item.summary}"
            for index, item in enumerate(value.items, start=1)
        ]
        model_text = "\n".join(lines)
        content_sha256 = hashlib.sha256(model_text.encode("utf-8")).hexdigest()
        return ToolObservation(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            observation_id=uuid5(NAMESPACE_URL, f"{call_id}:industry-web-search:v1"),
            call_id=call_id,
            run_id=run_id,
            workspace_id=runtime_context.workspace_scope.workspace_id,
            tool=ToolReference(self.definition.name, self.definition.version),
            normalizer_version=TOOL_OBSERVATION_NORMALIZER_VERSION,
            model_text=model_text,
            sources=tuple(
                ToolSource(
                    source_type="industry_public_source",
                    source_version=item.source_version,
                    locator=item.locator,
                    observed_at=observed_at,
                    content_sha256=item.content_sha256,
                )
                for item in value.items
            ),
            observed_at=observed_at,
            content_sha256=content_sha256,
        )
