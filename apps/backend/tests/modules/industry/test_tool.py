"""Industry Web Tool execution and normalized Observation contracts."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from industry_platform.modules.agent_runtime.context import (
    BackgroundRunPrincipal,
    TrustedRuntimeContext,
)
from industry_platform.modules.agent_runtime.domain import RunBudget
from industry_platform.modules.identity.domain import AuthenticatedWorkspace
from industry_platform.modules.industry.domain import (
    ENERGY_POWER_INDUSTRY_ID,
    INDUSTRY_PRESETS_BY_ID,
    IndustryProviderError,
    ProviderCode,
    ProviderErrorCode,
    ProviderItem,
    ProviderPage,
    ProviderQuery,
    ProviderReadiness,
    ProviderStatus,
    SourceKind,
    provider_for_kind,
)
from industry_platform.modules.industry.tool import IndustryWebSearchTool
from industry_platform.modules.tools.domain import ToolAction, ToolCall
from industry_platform.modules.tools.registry import (
    RegistryToolExecutor,
    ToolExecutionError,
    ToolRegistry,
    ToolRequestAudit,
)
from industry_platform.modules.workspaces.domain import WorkspaceAction, WorkspaceScope

CALL_ID = UUID("11111111-1111-4111-8111-111111111111")
RUN_ID = UUID("22222222-2222-4222-8222-222222222222")
STEP_ID = UUID("33333333-3333-4333-8333-333333333333")
WORKSPACE_ID = UUID("44444444-4444-4444-8444-444444444444")
USER_ID = UUID("55555555-5555-4555-8555-555555555555")
NOW = datetime(2026, 8, 17, 3, 0, tzinfo=UTC)


def _runtime_context() -> TrustedRuntimeContext:
    return TrustedRuntimeContext(
        principal=BackgroundRunPrincipal(
            user_id=USER_ID,
            workspaces=(
                AuthenticatedWorkspace(
                    workspace_id=WORKSPACE_ID,
                    name="Industry Tool",
                    role="member",
                ),
            ),
        ),
        workspace_scope=WorkspaceScope(WORKSPACE_ID, USER_ID, "member"),
        capabilities=frozenset({WorkspaceAction.VIEW, WorkspaceAction.RUN_TOOL}),
        budget=RunBudget(
            schema_version=1,
            max_steps=4,
            max_total_tokens=4_000,
            max_cost_micro_usd=10,
            deadline=NOW + timedelta(minutes=5),
        ),
    )


@dataclass(frozen=True, slots=True)
class OnePageProvider:
    failure: IndustryProviderError | None = None

    @property
    def status(self) -> ProviderStatus:
        return ProviderStatus(
            ProviderCode.WORLD_BANK_NEWS,
            SourceKind.NEWS,
            ProviderReadiness.READY,
            None,
        )

    async def fetch(self, query: ProviderQuery) -> ProviderPage:
        if self.failure is not None:
            raise self.failure
        assert query.industry.industry_id == ENERGY_POWER_INDUSTRY_ID
        assert query.limit == 2
        return ProviderPage(
            definition=provider_for_kind(SourceKind.NEWS),
            items=(
                ProviderItem(
                    kind=SourceKind.NEWS,
                    provider=ProviderCode.WORLD_BANK_NEWS,
                    external_id="news-tool-1",
                    title="Energy transition update",
                    summary="A bounded normalized source summary.",
                    locator="https://www.worldbank.org/en/news/energy-update",
                    published_at=NOW,
                    metadata={"category": "Feature Story"},
                ),
            ),
            next_cursor=None,
            fetched_at=NOW,
        )


@dataclass(frozen=True, slots=True)
class OneProviderRegistry:
    provider_adapter: OnePageProvider

    def provider(self, kind: SourceKind) -> OnePageProvider:
        if kind is not SourceKind.NEWS:
            raise AssertionError
        return self.provider_adapter

    def statuses(self) -> tuple[ProviderStatus, ...]:
        return (self.provider_adapter.status,)


def _prepared_call(
    tool: IndustryWebSearchTool,
    context: TrustedRuntimeContext,
) -> tuple[ToolRegistry, ToolCall]:
    registry = ToolRegistry((tool,))
    call = registry.prepare(
        ToolRequestAudit(
            call_id=CALL_ID,
            action=ToolAction(
                schema_version=1,
                name=tool.definition.name,
                version=tool.definition.version,
                arguments={
                    "industry_code": INDUSTRY_PRESETS_BY_ID[ENERGY_POWER_INDUSTRY_ID].code,
                    "source_kind": "news",
                    "query": "energy",
                    "limit": 2,
                },
            ),
        ),
        allowed_tools=(tool.definition.reference,),
        run_id=RUN_ID,
        requested_by_step_id=STEP_ID,
        runtime_context=context,
        requested_at=NOW,
        idempotency_key=None,
    )
    return registry, call


@pytest.mark.asyncio
async def test_real_industry_adapter_executes_through_registry_and_emits_citations() -> None:
    context = _runtime_context()
    tool = IndustryWebSearchTool(OneProviderRegistry(OnePageProvider()))
    registry, call = _prepared_call(tool, context)

    result = await RegistryToolExecutor(registry, clock=lambda: NOW).execute(call, context)

    assert result.actual_cost_micro_usd == 0
    assert result.observation.model_text == (
        "[S1] Energy transition update — A bounded normalized source summary."
    )
    assert result.observation.workspace_id == WORKSPACE_ID
    assert result.observation.call_id == CALL_ID
    assert result.observation.run_id == RUN_ID
    assert len(result.observation.sources) == 1
    source = result.observation.sources[0]
    assert source.locator == "https://www.worldbank.org/en/news/energy-update"
    assert source.source_version == provider_for_kind(SourceKind.NEWS).version
    assert len(source.content_sha256) == 64
    assert source.content_sha256 != result.observation.content_sha256


@pytest.mark.asyncio
async def test_provider_failure_is_mapped_to_a_stable_tool_error_without_detail() -> None:
    context = _runtime_context()
    failure = IndustryProviderError(ProviderErrorCode.RATE_LIMITED, retryable=True)
    tool = IndustryWebSearchTool(OneProviderRegistry(OnePageProvider(failure)))
    registry, call = _prepared_call(tool, context)

    with pytest.raises(ToolExecutionError) as exc_info:
        await RegistryToolExecutor(registry, clock=lambda: NOW).execute(call, context)

    assert exc_info.value.code == ProviderErrorCode.RATE_LIMITED.value
    assert "rate" not in str(exc_info.value).casefold()
