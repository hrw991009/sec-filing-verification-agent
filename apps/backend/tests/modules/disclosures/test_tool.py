"""Typed Tool Registry tests for sec.resolve_filer@v1."""

from datetime import timedelta
from uuid import UUID

import pytest

from industry_platform.modules.agent_runtime.context import (
    BackgroundRunPrincipal,
    TrustedRuntimeContext,
)
from industry_platform.modules.agent_runtime.domain import RunBudget
from industry_platform.modules.disclosures.adapters.sec_edgar import (
    FrozenSecEdgarAdapter,
    UnavailableSecEdgarAdapter,
)
from industry_platform.modules.disclosures.domain import FilingSelectionScope, SecAmendmentPolicy
from industry_platform.modules.disclosures.service import SecFilerResolutionService
from industry_platform.modules.disclosures.tool import SecListFilingsTool, SecResolveFilerTool
from industry_platform.modules.identity.domain import AuthenticatedWorkspace
from industry_platform.modules.tools.domain import ToolAction, ToolCall, ToolReference
from industry_platform.modules.tools.registry import (
    RegistryToolExecutor,
    ToolExecutionError,
    ToolPreparationError,
    ToolRegistry,
    ToolRequestAudit,
)
from industry_platform.modules.workspaces.domain import WorkspaceAction, WorkspaceScope

from .support import NOW, InMemoryFilerCatalogRepository, catalog_snapshot
from .test_filing_selection_service import selection_scope
from .test_filing_selection_service import service as filing_service

WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
USER_ID = UUID("22222222-2222-4222-8222-222222222222")
RUN_ID = UUID("33333333-3333-4333-8333-333333333333")
STEP_ID = UUID("44444444-4444-4444-8444-444444444444")
CALL_ID = UUID("55555555-5555-4555-8555-555555555555")


def runtime_context(
    filing_scope: FilingSelectionScope | None = None,
) -> TrustedRuntimeContext:
    return TrustedRuntimeContext(
        principal=BackgroundRunPrincipal(
            user_id=USER_ID,
            workspaces=(AuthenticatedWorkspace(WORKSPACE_ID, "SEC Research", "member"),),
        ),
        workspace_scope=WorkspaceScope(WORKSPACE_ID, USER_ID, "member"),
        capabilities=frozenset({WorkspaceAction.VIEW, WorkspaceAction.RUN_TOOL}),
        budget=RunBudget(
            schema_version=1,
            max_steps=8,
            max_total_tokens=4_000,
            max_cost_micro_usd=100_000,
            deadline=NOW + timedelta(minutes=5),
        ),
        filing_selection_scope=filing_scope,
    )


def registry(*, configured: bool = True) -> ToolRegistry:
    source = (
        FrozenSecEdgarAdapter(catalog_snapshot()) if configured else UnavailableSecEdgarAdapter()
    )
    service = SecFilerResolutionService(
        repository=InMemoryFilerCatalogRepository(),
        source=source,
    )
    return ToolRegistry((SecResolveFilerTool(service),))


def prepare(tool_registry: ToolRegistry, action: ToolAction) -> ToolCall:
    definition = tool_registry.definition(ToolReference(action.name, action.version))
    assert definition is not None
    return tool_registry.prepare(
        ToolRequestAudit(call_id=CALL_ID, action=action),
        allowed_tools=(definition.reference,),
        run_id=RUN_ID,
        requested_by_step_id=STEP_ID,
        runtime_context=runtime_context(),
        requested_at=NOW,
    )


@pytest.mark.asyncio
async def test_tool_returns_attributed_ambiguous_candidates_through_shared_executor() -> None:
    tool_registry = registry()
    action = ToolAction(1, "sec.resolve_filer", "v1", {"query": "Apple", "limit": 5})

    result = await RegistryToolExecutor(tool_registry, clock=lambda: NOW).execute(
        prepare(tool_registry, action),
        runtime_context(),
    )

    assert '"status":"ambiguous"' in result.observation.model_text
    assert '"error_code":"ambiguous_filer"' in result.observation.model_text
    assert result.observation.sources[0].source_type == "sec_filer_catalog"
    assert result.observation.sources[0].locator == (
        "https://www.sec.gov/files/company_tickers.json"
    )


def test_model_cannot_supply_host_url_or_request_policy() -> None:
    tool_registry = registry()
    action = ToolAction(
        1,
        "sec.resolve_filer",
        "v1",
        {
            "query": "AAPL",
            "limit": 5,
            "host": "example.com",
        },
    )

    with pytest.raises(ToolPreparationError) as caught:
        prepare(tool_registry, action)

    assert caught.value.code == "tool_arguments_invalid"


@pytest.mark.asyncio
async def test_missing_live_source_is_not_reported_as_no_result() -> None:
    tool_registry = registry(configured=False)
    action = ToolAction(1, "sec.resolve_filer", "v1", {"query": "AAPL", "limit": 5})

    with pytest.raises(ToolExecutionError) as caught:
        await RegistryToolExecutor(tool_registry, clock=lambda: NOW).execute(
            prepare(tool_registry, action),
            runtime_context(),
        )

    assert caught.value.code == "sec_source_not_configured"


def list_filings_registry() -> ToolRegistry:
    scope = selection_scope(policy=SecAmendmentPolicy.LATEST_KNOWN_BY_AS_OF)
    application, _, _ = filing_service(scope)
    return ToolRegistry((SecListFilingsTool(application),))


def prepare_list_filings(tool_registry: ToolRegistry, action: ToolAction) -> ToolCall:
    definition = tool_registry.definition(ToolReference(action.name, action.version))
    assert definition is not None
    scope = selection_scope(policy=SecAmendmentPolicy.LATEST_KNOWN_BY_AS_OF)
    return tool_registry.prepare(
        ToolRequestAudit(call_id=CALL_ID, action=action),
        allowed_tools=(definition.reference,),
        run_id=RUN_ID,
        requested_by_step_id=STEP_ID,
        runtime_context=runtime_context(scope),
        requested_at=NOW,
    )


@pytest.mark.asyncio
async def test_list_filings_uses_only_trusted_scope_and_emits_all_source_lineage() -> None:
    tool_registry = list_filings_registry()
    action = ToolAction(1, "sec.list_filings", "v1", {})
    scope = selection_scope(policy=SecAmendmentPolicy.LATEST_KNOWN_BY_AS_OF)

    result = await RegistryToolExecutor(tool_registry, clock=lambda: NOW).execute(
        prepare_list_filings(tool_registry, action),
        runtime_context(scope),
    )

    assert '"accession":"0000320193-24-000002"' in result.observation.model_text
    assert result.observation.sources[0].source_type == "sec_submissions"
    assert result.observation.sources[0].locator.startswith("https://data.sec.gov/submissions/")


def test_model_cannot_override_list_filings_scope() -> None:
    tool_registry = list_filings_registry()
    action = ToolAction(
        1,
        "sec.list_filings",
        "v1",
        {"cik": "0000789019"},
    )

    with pytest.raises(ToolPreparationError) as caught:
        prepare_list_filings(tool_registry, action)

    assert caught.value.code == "tool_arguments_invalid"


@pytest.mark.asyncio
async def test_list_filings_requires_server_verified_scope() -> None:
    tool_registry = list_filings_registry()
    action = ToolAction(1, "sec.list_filings", "v1", {})
    prepared = prepare_list_filings(tool_registry, action)

    with pytest.raises(ToolExecutionError) as caught:
        await RegistryToolExecutor(tool_registry, clock=lambda: NOW).execute(
            prepared,
            runtime_context(),
        )

    assert caught.value.code == "filing_selection_scope_not_configured"
