"""Tests for production Direct Answer Runtime composition."""

from types import SimpleNamespace
from typing import cast

import httpx2
import pytest
from pydantic import SecretStr

from industry_platform.core.config import AgentModelRouteSettings, Settings
from industry_platform.core.database import create_database_engine, create_database_session_factory
from industry_platform.modules.agent_runtime.adapters.execution import (
    SqlAlchemyDirectAnswerRunLoader,
)
from industry_platform.modules.agent_runtime.context import (
    CONTEXT_COMPILER_V1,
    FINANCIAL_CONTEXT_COMPILER_V1,
)
from industry_platform.modules.agent_runtime.context_compiler import (
    FinancialContextCompilerV1,
)
from industry_platform.modules.agent_runtime.execution import (
    DirectAnswerRunExecutionService,
    DirectAnswerRunExecutionUseCase,
)
from industry_platform.modules.agent_runtime.resources import (
    UNCONFIGURED_AGENT_MODEL,
    create_direct_answer_runtime_resources,
)
from industry_platform.modules.agent_runtime.tool_runtime import (
    ToolL2Runtime,
    UnifiedAgentRuntime,
)
from industry_platform.modules.conversations.domain import TurnSearchMode
from industry_platform.modules.industry.tool import industry_web_search_definition
from industry_platform.modules.retrieval.tool import knowledge_search_definition
from industry_platform.modules.tools.registry import RegisteredToolAdapter


def accepts_execution_service(
    value: DirectAnswerRunExecutionUseCase,
) -> DirectAnswerRunExecutionUseCase:
    return value


@pytest.mark.asyncio
async def test_resources_keep_missing_provider_explicit_and_use_the_unified_runtime(
    test_settings: Settings,
) -> None:
    engine = create_database_engine(test_settings)
    client = httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _request: httpx2.Response(500)),
        trust_env=False,
        follow_redirects=False,
    )
    try:
        resources = create_direct_answer_runtime_resources(
            test_settings,
            create_database_session_factory(engine),
            client,
        )

        assert accepts_execution_service(resources.execution_service)
        assert resources.model == UNCONFIGURED_AGENT_MODEL
        assert resources.provider_configured is False
        assert "provider-secret" not in repr(resources)
    finally:
        await client.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_resources_use_the_configured_canonical_model(test_settings: Settings) -> None:
    configured = test_settings.model_copy(
        update={
            "agent_model_provider_base_url": "https://api.example.com/v1",
            "agent_model_provider_api_key": SecretStr("provider-test-key"),
            "agent_model_route": AgentModelRouteSettings(
                model="openai-compatible/example-model",
                upstream_model="example-model-2026-08-14",
                response_models=("example-model-2026-08-14",),
                pricing_version="example-pricing-v1",
                input_micro_usd_per_million=1_000_000,
                cached_input_micro_usd_per_million=100_000,
                output_micro_usd_per_million=2_000_000,
            ),
        }
    )
    engine = create_database_engine(configured)
    client = httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _request: httpx2.Response(500)),
        trust_env=False,
        follow_redirects=False,
    )
    try:
        resources = create_direct_answer_runtime_resources(
            configured,
            create_database_session_factory(engine),
            client,
        )

        assert resources.model == "openai-compatible/example-model"
        assert resources.provider_configured is True
        assert "provider-test-key" not in repr(resources)
    finally:
        await client.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_resources_select_financial_context_only_for_the_local_tool_surface(
    test_settings: Settings,
) -> None:
    engine = create_database_engine(test_settings)
    client = httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _request: httpx2.Response(500)),
        trust_env=False,
        follow_redirects=False,
    )
    web_adapter = cast(
        RegisteredToolAdapter,
        SimpleNamespace(definition=industry_web_search_definition()),
    )
    local_adapter = cast(
        RegisteredToolAdapter,
        SimpleNamespace(definition=knowledge_search_definition()),
    )
    try:
        resources = create_direct_answer_runtime_resources(
            test_settings,
            create_database_session_factory(engine),
            client,
            tool_adapters=(web_adapter, local_adapter),
            tool_surfaces={
                TurnSearchMode.WEB: (web_adapter.definition.reference,),
                TurnSearchMode.LOCAL: (local_adapter.definition.reference,),
            },
        )

        assert isinstance(resources.execution_service, DirectAnswerRunExecutionService)
        loader = resources.execution_service.loader
        assert isinstance(loader, SqlAlchemyDirectAnswerRunLoader)
        assert loader.tool_policies is not None
        assert (
            loader.tool_policies[TurnSearchMode.WEB].context_compiler_version == CONTEXT_COMPILER_V1
        )
        assert (
            loader.tool_policies[TurnSearchMode.LOCAL].context_compiler_version
            == FINANCIAL_CONTEXT_COMPILER_V1
        )
        runtime = resources.execution_service.runtime
        assert isinstance(runtime, UnifiedAgentRuntime)
        tool_runtime = runtime._tool_l2_runtime
        assert isinstance(tool_runtime, ToolL2Runtime)
        assert isinstance(tool_runtime._context_compiler, FinancialContextCompilerV1)
    finally:
        await client.aclose()
        await engine.dispose()
