"""Composition roots for production Agent execution and HTTP delivery."""

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

import httpx2
from fastapi import Request

from industry_platform.adapters.openai_compatible import (
    OpenAICompatibleModelProvider,
    OpenAICompatibleModelRoute,
    OpenAICompatibleProviderConfig,
)
from industry_platform.core.config import Settings
from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.agent_runtime.adapters.execution import (
    SqlAlchemyDirectAnswerRunLoader,
)
from industry_platform.modules.agent_runtime.adapters.persistence import (
    SqlAlchemyAgentEventCommitter,
    SqlAlchemyAgentRunControl,
    SqlAlchemyAgentRunTerminalizer,
    SqlAlchemyCommittedEventSource,
    SqlAlchemyContextManifestStore,
)
from industry_platform.modules.agent_runtime.adapters.trace_query import (
    SqlAlchemyAgentTraceQuery,
)
from industry_platform.modules.agent_runtime.context_compiler import (
    ContextCompilerV0,
    Utf8UpperBoundTokenCounter,
)
from industry_platform.modules.agent_runtime.delivery import (
    AgentRunDeliveryService,
    AgentRunDeliveryUseCase,
)
from industry_platform.modules.agent_runtime.execution import (
    DirectAnswerRunExecutionService,
    DirectAnswerRunExecutionUseCase,
)
from industry_platform.modules.agent_runtime.runtime import DirectAnswerRuntime
from industry_platform.modules.agent_runtime.runtime_contracts import DirectAnswerRuntimePolicy
from industry_platform.modules.agent_runtime.tool_runtime import UnifiedAgentRuntime
from industry_platform.modules.agent_runtime.trace import AgentTrace
from industry_platform.modules.files.resources import create_private_file_object_store
from industry_platform.modules.workspaces.domain import WorkspaceScope

UNCONFIGURED_AGENT_MODEL = "openai-compatible/unconfigured"


@dataclass(frozen=True, slots=True)
class DirectAnswerRuntimeResources:
    """Worker-owned Runtime resources; the injected HTTP client owns its lifecycle."""

    execution_service: DirectAnswerRunExecutionUseCase = field(repr=False)
    model: str
    provider_configured: bool


@dataclass(frozen=True, slots=True)
class AgentRunDeliveryResources:
    """API-owned committed Event reader and cooperative cancellation service."""

    service: AgentRunDeliveryUseCase


class AgentTraceQuery(Protocol):
    """Workspace-scoped safe Trace read boundary used by HTTP delivery."""

    async def get(self, *, scope: WorkspaceScope, run_id: UUID) -> AgentTrace: ...


@dataclass(frozen=True, slots=True)
class AgentTraceResources:
    """API-owned read-only Agent Trace query."""

    query: AgentTraceQuery


def create_agent_run_delivery_resources(
    session_factory: AsyncSessionFactory,
) -> AgentRunDeliveryResources:
    return AgentRunDeliveryResources(
        service=AgentRunDeliveryService(
            event_reader=SqlAlchemyCommittedEventSource(session_factory),
            cancellation_controller=SqlAlchemyAgentRunControl(session_factory),
        )
    )


def get_agent_run_delivery_resources(request: Request) -> AgentRunDeliveryResources:
    resources = getattr(request.app.state, "agent_run_delivery_resources", None)
    if not isinstance(resources, AgentRunDeliveryResources):
        raise RuntimeError("Application lifespan has not initialized Agent delivery resources")
    return resources


def create_agent_trace_resources(
    session_factory: AsyncSessionFactory,
) -> AgentTraceResources:
    return AgentTraceResources(query=SqlAlchemyAgentTraceQuery(session_factory))


def get_agent_trace_resources(request: Request) -> AgentTraceResources:
    resources = getattr(request.app.state, "agent_trace_resources", None)
    if not isinstance(resources, AgentTraceResources):
        raise RuntimeError("Application lifespan has not initialized Agent Trace resources")
    return resources


def create_direct_answer_runtime_resources(
    settings: Settings,
    session_factory: AsyncSessionFactory,
    provider_http_client: httpx2.AsyncClient,
) -> DirectAnswerRuntimeResources:
    """Build the same Runtime used by Harness while replacing only external adapters."""

    provider_config = _provider_config(settings)
    model = (
        UNCONFIGURED_AGENT_MODEL
        if settings.agent_model_route is None
        else settings.agent_model_route.model
    )
    policy = DirectAnswerRuntimePolicy(
        schema_version=1,
        profile_version="direct-answer-v0",
        prompt_version="direct-answer-prompt-v0",
        context_compiler_version="context-v0",
        output_contract_version="final-markdown-v1",
        model=model,
        max_input_tokens=2_048,
        max_output_tokens=512,
        system_instructions=(
            "Answer the current user question directly with concise, safe Markdown. "
            "Do not claim to have searched the web or private knowledge sources."
        ),
    )
    runtime = UnifiedAgentRuntime(
        direct_answer_runtime=DirectAnswerRuntime(
            context_compiler=ContextCompilerV0(token_counter=Utf8UpperBoundTokenCounter()),
            context_manifest_store=SqlAlchemyContextManifestStore(session_factory),
            model_provider=OpenAICompatibleModelProvider(
                client=provider_http_client,
                config=provider_config,
            ),
            event_committer=SqlAlchemyAgentEventCommitter(session_factory),
            cancellation_probe=SqlAlchemyAgentRunControl(session_factory),
        )
    )
    execution_service = DirectAnswerRunExecutionService(
        loader=SqlAlchemyDirectAnswerRunLoader(
            session_factory,
            policy,
            attachment_object_reader=create_private_file_object_store(settings),
        ),
        runtime=runtime,
        terminalizer=SqlAlchemyAgentRunTerminalizer(session_factory),
    )
    return DirectAnswerRuntimeResources(
        execution_service=execution_service,
        model=model,
        provider_configured=provider_config is not None,
    )


def _provider_config(settings: Settings) -> OpenAICompatibleProviderConfig | None:
    route = settings.agent_model_route
    base_url = settings.agent_model_provider_base_url
    api_key = settings.agent_model_provider_api_key
    if route is None or base_url is None or api_key is None:
        return None
    return OpenAICompatibleProviderConfig(
        base_url=base_url,
        api_key=api_key,
        models=(
            OpenAICompatibleModelRoute(
                model=route.model,
                upstream_model=route.upstream_model,
                response_models=route.response_models,
                pricing_version=route.pricing_version,
                input_micro_usd_per_million=route.input_micro_usd_per_million,
                cached_input_micro_usd_per_million=(route.cached_input_micro_usd_per_million),
                output_micro_usd_per_million=route.output_micro_usd_per_million,
                supports_image_input=route.supports_image_input,
            ),
        ),
        request_timeout_seconds=settings.agent_model_request_timeout_seconds,
    )
