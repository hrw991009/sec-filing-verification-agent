"""Composition of the production Direct Answer Runtime around an injected egress client."""

from dataclasses import dataclass, field

import httpx2

from industry_platform.adapters.openai_compatible import (
    OpenAICompatibleModelProvider,
    OpenAICompatibleModelRoute,
    OpenAICompatibleProviderConfig,
)
from industry_platform.core.config import Settings
from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.agent_runtime.context_compiler import (
    ContextCompilerV0,
    Utf8UpperBoundTokenCounter,
)
from industry_platform.modules.agent_runtime.execution import (
    DirectAnswerRunExecutionService,
    DirectAnswerRunExecutionUseCase,
)
from industry_platform.modules.agent_runtime.execution_persistence import (
    SqlAlchemyDirectAnswerRunLoader,
)
from industry_platform.modules.agent_runtime.persistence import (
    SqlAlchemyAgentEventCommitter,
    SqlAlchemyAgentRunControl,
    SqlAlchemyContextManifestStore,
)
from industry_platform.modules.agent_runtime.runtime import DirectAnswerRuntime
from industry_platform.modules.agent_runtime.runtime_contracts import DirectAnswerRuntimePolicy

UNCONFIGURED_AGENT_MODEL = "openai-compatible/unconfigured"


@dataclass(frozen=True, slots=True)
class DirectAnswerRuntimeResources:
    """Worker-owned Runtime resources; the injected HTTP client owns its lifecycle."""

    execution_service: DirectAnswerRunExecutionUseCase = field(repr=False)
    model: str
    provider_configured: bool


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
    runtime = DirectAnswerRuntime(
        context_compiler=ContextCompilerV0(token_counter=Utf8UpperBoundTokenCounter()),
        context_manifest_store=SqlAlchemyContextManifestStore(session_factory),
        model_provider=OpenAICompatibleModelProvider(
            client=provider_http_client,
            config=provider_config,
        ),
        event_committer=SqlAlchemyAgentEventCommitter(session_factory),
        cancellation_probe=SqlAlchemyAgentRunControl(session_factory),
    )
    execution_service = DirectAnswerRunExecutionService(
        loader=SqlAlchemyDirectAnswerRunLoader(session_factory, policy),
        runtime=runtime,
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
            ),
        ),
        request_timeout_seconds=settings.agent_model_request_timeout_seconds,
    )
