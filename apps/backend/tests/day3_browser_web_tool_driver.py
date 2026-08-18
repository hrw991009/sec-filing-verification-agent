"""Execute one browser-created Web Tool Run through the formal Job and L2 Runtime stack."""

import argparse
import asyncio
import hashlib
import json
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from browser_driver_support import (
    BrowserSuccessDriverError,
    claim_target_delivery,
    non_nil_uuid,
    require_pending_target,
)
from sqlalchemy import select

from industry_platform.core.config import Settings, get_settings
from industry_platform.core.database import create_database_engine, create_database_session_factory
from industry_platform.modules.agent_runtime.adapters.execution import (
    SqlAlchemyDirectAnswerRunLoader,
)
from industry_platform.modules.agent_runtime.adapters.persistence import (
    SqlAlchemyAgentEventCommitter,
    SqlAlchemyAgentRunControl,
    SqlAlchemyAgentRunTerminalizer,
    SqlAlchemyContextManifestStore,
)
from industry_platform.modules.agent_runtime.context_compiler import (
    ContextCompilerV0,
    ContextCompilerV1,
    Utf8UpperBoundTokenCounter,
)
from industry_platform.modules.agent_runtime.execution import DirectAnswerRunExecutionService
from industry_platform.modules.agent_runtime.model import (
    ModelFinishReason,
    ModelRequest,
    ModelResponse,
    ModelStreamItem,
    ModelUsage,
)
from industry_platform.modules.agent_runtime.runtime import DirectAnswerRuntime
from industry_platform.modules.agent_runtime.runtime_contracts import DirectAnswerRuntimePolicy
from industry_platform.modules.agent_runtime.tool_runtime import ToolL2Runtime, UnifiedAgentRuntime
from industry_platform.modules.agent_runtime.tool_runtime_contracts import ToolL2RuntimePolicy
from industry_platform.modules.conversations.domain import DIRECT_ANSWER_TASK_NAME
from industry_platform.modules.conversations.models import Message, MessageRole, MessageStatus
from industry_platform.modules.industry.domain import (
    SMART_TRANSPORT_INDUSTRY_ID,
    ProviderCode,
    ProviderItem,
    ProviderPage,
    ProviderQuery,
    ProviderReadiness,
    ProviderStatus,
    SourceKind,
    provider_for_kind,
)
from industry_platform.modules.industry.tool import IndustryWebSearchTool
from industry_platform.modules.jobs.domain import JobStatus
from industry_platform.modules.jobs.models import Job
from industry_platform.modules.jobs.resources import create_job_resources
from industry_platform.modules.tools.models import ToolCallRecord, ToolRunRecord
from industry_platform.modules.tools.registry import RegistryToolExecutor, ToolRegistry
from industry_platform.server import create_selector_event_loop
from industry_platform.workers.runtime import (
    DirectAnswerJobHandler,
    FixedJobHandlerRegistry,
    JobExecutionDisposition,
    JobExecutionRuntime,
)

ANSWER_PREFIX = "Day 3 Web Tool 已完成。Run: "
ANSWER_SUFFIX = "; 公共来源结果已引用 [S1]。"


@dataclass(frozen=True, slots=True)
class BrowserNewsProvider:
    now: datetime

    @property
    def status(self) -> ProviderStatus:
        return ProviderStatus(
            ProviderCode.WORLD_BANK_NEWS,
            SourceKind.NEWS,
            ProviderReadiness.READY,
            None,
        )

    async def fetch(self, query: ProviderQuery) -> ProviderPage:
        if query.industry.industry_id != SMART_TRANSPORT_INDUSTRY_ID:
            raise BrowserSuccessDriverError("The Web Tool received an unexpected industry")
        return ProviderPage(
            definition=provider_for_kind(SourceKind.NEWS),
            items=(
                ProviderItem(
                    kind=SourceKind.NEWS,
                    provider=ProviderCode.WORLD_BANK_NEWS,
                    external_id="day3-browser-web-1",
                    title="Public transport transition",
                    summary="A frozen official-source contract fixture.",
                    locator="https://www.worldbank.org/en/news/transport-transition",
                    published_at=self.now,
                    metadata={"category": "Feature Story"},
                ),
            ),
            next_cursor=None,
            fetched_at=self.now,
        )


@dataclass(frozen=True, slots=True)
class BrowserProviderRegistry:
    adapter: BrowserNewsProvider

    def provider(self, kind: SourceKind) -> BrowserNewsProvider:
        if kind is not SourceKind.NEWS:
            raise BrowserSuccessDriverError("The Web Tool selected an unexpected source kind")
        return self.adapter

    def statuses(self) -> tuple[ProviderStatus, ...]:
        return (self.adapter.status,)


@dataclass(slots=True)
class BrowserToolModelProvider:
    run_id: UUID
    requests: list[ModelRequest] = field(default_factory=list)

    @property
    def answer(self) -> str:
        return f"{ANSWER_PREFIX}{self.run_id}{ANSWER_SUFFIX}"

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        raise BrowserSuccessDriverError(f"Web L2 must not stream {request.model}")

    async def complete(self, request: ModelRequest) -> ModelResponse:
        if request.run_id != self.run_id or len(self.requests) >= 2:
            raise BrowserSuccessDriverError("The Web model received an unexpected request")
        self.requests.append(request)
        if len(self.requests) == 1:
            output = (
                '{"decision":{"schema_version":1,"kind":"tool_call",'
                '"name":"industry.web_search","version":"v1","arguments":{'
                '"industry_code":"smart_transport","source_kind":"news",'
                '"query":"transport policy","limit":2}}}'
            )
        else:
            output = json.dumps(
                {
                    "decision": {
                        "schema_version": 1,
                        "kind": "final",
                        "content_markdown": self.answer,
                    }
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        return ModelResponse(
            schema_version=1,
            model=request.model,
            finish_reason=ModelFinishReason.STOP,
            usage=ModelUsage(
                input_tokens=20,
                output_tokens=10,
                cached_input_tokens=0,
                cost_micro_usd=40,
                pricing_version="e2e-tool-pricing-v1",
            ),
            output_text=output,
            provider_request_id=f"e2e-tool-{self.run_id.hex}-{len(self.requests)}",
        )


async def execute_browser_web_run(
    settings: Settings, *, run_id: UUID, job_id: UUID
) -> dict[str, object]:
    engine = create_database_engine(settings)
    try:
        session_factory = create_database_session_factory(engine)
        outbox_id = await require_pending_target(session_factory, run_id=run_id, job_id=job_id)
        delivery = await claim_target_delivery(
            settings,
            session_factory,
            job_id=job_id,
            outbox_id=outbox_id,
        )
        tool = IndustryWebSearchTool(
            BrowserProviderRegistry(BrowserNewsProvider(datetime.now(UTC)))
        )
        tool_policy = ToolL2RuntimePolicy(
            schema_version=1,
            profile_version="conversation-web-l2-v1",
            prompt_version="conversation-web-l2-prompt-v1",
            context_compiler_version="context-v1",
            output_contract_version="final-markdown-v1",
            toolset_version="conversation-web-toolset-v1",
            model="openai-compatible/e2e-tool-provider",
            max_input_tokens=4_096,
            max_decision_output_tokens=768,
            max_tool_calls=2,
            system_instructions="Use only the exact public-source Tool catalog.",
            available_tools=(tool.definition.reference,),
        )
        direct_policy = DirectAnswerRuntimePolicy(
            schema_version=1,
            profile_version="direct-answer-v0",
            prompt_version="direct-answer-prompt-v0",
            context_compiler_version="context-v0",
            output_contract_version="final-markdown-v1",
            model="openai-compatible/e2e-tool-provider",
            max_input_tokens=2_048,
            max_output_tokens=512,
            system_instructions="Answer directly without Tool claims.",
        )
        provider = BrowserToolModelProvider(run_id)
        committer = SqlAlchemyAgentEventCommitter(session_factory)
        manifests = SqlAlchemyContextManifestStore(session_factory)
        control = SqlAlchemyAgentRunControl(session_factory)
        registry = ToolRegistry((tool,))
        runtime = UnifiedAgentRuntime(
            direct_answer_runtime=DirectAnswerRuntime(
                context_compiler=ContextCompilerV0(token_counter=Utf8UpperBoundTokenCounter()),
                context_manifest_store=manifests,
                model_provider=provider,
                event_committer=committer,
                cancellation_probe=control,
            ),
            tool_l2_runtime=ToolL2Runtime(
                context_compiler=ContextCompilerV1(token_counter=Utf8UpperBoundTokenCounter()),
                context_manifest_store=manifests,
                model_provider=provider,
                tool_registry=registry,
                tool_executor=RegistryToolExecutor(registry),
                event_committer=committer,
                cancellation_probe=control,
            ),
        )
        execution = DirectAnswerRunExecutionService(
            loader=SqlAlchemyDirectAnswerRunLoader(
                session_factory,
                direct_policy,
                tool_policy=tool_policy,
            ),
            runtime=runtime,
            terminalizer=SqlAlchemyAgentRunTerminalizer(session_factory),
        )
        disposition = await JobExecutionRuntime(
            jobs=create_job_resources(settings, session_factory).application_service,
            handlers=FixedJobHandlerRegistry(
                {DIRECT_ANSWER_TASK_NAME: DirectAnswerJobHandler(execution)}
            ),
            worker_id=f"e2e-browser-tool-worker-{job_id.hex}",
            heartbeat_seconds=settings.job_heartbeat_seconds,
        ).execute(delivery.message)

        async with session_factory() as session:
            run_status = await session.scalar(select(Job.status).where(Job.id == job_id))
            assistant = await session.scalar(
                select(Message).where(
                    Message.agent_run_id == run_id,
                    Message.role == MessageRole.ASSISTANT,
                    Message.status == MessageStatus.FINAL,
                )
            )
            call = await session.scalar(
                select(ToolCallRecord).where(ToolCallRecord.run_id == run_id)
            )
            audit = await session.scalar(
                select(ToolRunRecord).where(ToolRunRecord.run_id == run_id)
            )
        if (
            disposition is not JobExecutionDisposition.SUCCEEDED
            or run_status is not JobStatus.SUCCEEDED
            or assistant is None
            or assistant.content_markdown != provider.answer
            or call is None
            or call.resolved_tool_name != "industry.web_search"
            or audit is None
            or audit.status != "completed"
            or len(provider.requests) != 2
        ):
            raise BrowserSuccessDriverError("The Web Tool terminal facts are inconsistent")
        return {
            "schema_version": 1,
            "run_id": str(run_id),
            "job_id": str(job_id),
            "disposition": disposition.value,
            "provider_calls": len(provider.requests),
            "answer_sha256": hashlib.sha256(provider.answer.encode()).hexdigest(),
        }
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, type=non_nil_uuid)
    parser.add_argument("--job-id", required=True, type=non_nil_uuid)
    arguments = parser.parse_args()
    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        result = runner.run(
            execute_browser_web_run(
                get_settings(),
                run_id=arguments.run_id,
                job_id=arguments.job_id,
            )
        )
    sys.stdout.write(f"{json.dumps(result, sort_keys=True)}\n")


if __name__ == "__main__":
    main()
