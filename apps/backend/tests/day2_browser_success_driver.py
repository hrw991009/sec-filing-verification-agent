"""Execute one browser-created Direct Answer Job through the formal runtime stack."""

import argparse
import asyncio
import hashlib
import json
import sys
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from uuid import UUID

from browser_driver_support import (
    BrowserSuccessDriverError,
    claim_target_delivery,
    non_nil_uuid,
    require_pending_target,
)
from sqlalchemy import select

from industry_platform.core.config import Settings, get_settings
from industry_platform.core.database import (
    AsyncSessionFactory,
    create_database_engine,
    create_database_session_factory,
)
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
    Utf8UpperBoundTokenCounter,
)
from industry_platform.modules.agent_runtime.domain import AgentRunStatus, RunStopReason
from industry_platform.modules.agent_runtime.events import (
    TERMINAL_AGENT_EVENT_TYPES,
    AgentEventType,
)
from industry_platform.modules.agent_runtime.execution import DirectAnswerRunExecutionService
from industry_platform.modules.agent_runtime.model import (
    ModelFinishReason,
    ModelRequest,
    ModelResponse,
    ModelStreamCompleted,
    ModelStreamDelta,
    ModelStreamItem,
    ModelUsage,
)
from industry_platform.modules.agent_runtime.models import AgentEventRecord, AgentRunRecord
from industry_platform.modules.agent_runtime.runtime import DirectAnswerRuntime
from industry_platform.modules.agent_runtime.runtime_contracts import DirectAnswerRuntimePolicy
from industry_platform.modules.agent_runtime.tool_runtime import UnifiedAgentRuntime
from industry_platform.modules.conversations.domain import DIRECT_ANSWER_TASK_NAME
from industry_platform.modules.conversations.models import Message, MessageRole, MessageStatus
from industry_platform.modules.jobs.domain import (
    JobStatus,
)
from industry_platform.modules.jobs.models import Job
from industry_platform.modules.jobs.resources import create_job_resources
from industry_platform.server import create_selector_event_loop
from industry_platform.workers.runtime import (
    DirectAnswerJobHandler,
    FixedJobHandlerRegistry,
    JobExecutionDisposition,
    JobExecutionRuntime,
)

ANSWER_PREFIX = "Day 2 浏览器流式片段已到达。Run: "
ANSWER_SUFFIX = "; 第二段完成, 最终回答已持久化。"
MODEL_DELTA_DELAY_SECONDS = 1.25


@dataclass(slots=True)
class BrowserSuccessModelProvider:
    """Deterministic test Provider scoped to exactly one browser-created Run."""

    target_run_id: UUID
    requests: list[ModelRequest] = field(default_factory=list)

    @property
    def answer(self) -> str:
        return f"{self.first_delta}{ANSWER_SUFFIX}"

    @property
    def first_delta(self) -> str:
        return f"{ANSWER_PREFIX}{self.target_run_id}"

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelStreamItem]:
        if request.run_id != self.target_run_id:
            raise BrowserSuccessDriverError("The test Provider received an unexpected Run")
        if self.requests:
            raise BrowserSuccessDriverError("The test Provider was called more than once")
        self.requests.append(request)
        response = ModelResponse(
            schema_version=1,
            model=request.model,
            finish_reason=ModelFinishReason.STOP,
            usage=ModelUsage(
                input_tokens=23,
                output_tokens=11,
                cached_input_tokens=0,
                cost_micro_usd=37,
                pricing_version="e2e-fake-pricing-v1",
            ),
            output_text=self.answer,
            provider_request_id=f"e2e-{self.target_run_id.hex}",
        )
        yield ModelStreamDelta(schema_version=1, sequence=1, text=self.first_delta)
        await asyncio.sleep(MODEL_DELTA_DELAY_SECONDS)
        yield ModelStreamDelta(schema_version=1, sequence=2, text=ANSWER_SUFFIX)
        yield ModelStreamCompleted(schema_version=1, sequence=3, response=response)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        raise BrowserSuccessDriverError(
            f"Direct Answer must stream the requested model, not complete {request.model}"
        )


async def _verify_terminal_facts(
    session_factory: AsyncSessionFactory,
    *,
    run_id: UUID,
    job_id: UUID,
    expected_answer: str,
) -> None:
    async with session_factory() as session:
        run = await session.get(AgentRunRecord, run_id)
        job = await session.get(Job, job_id)
        assistant_messages = tuple(
            await session.scalars(
                select(Message)
                .where(
                    Message.agent_run_id == run_id,
                    Message.role == MessageRole.ASSISTANT,
                )
                .order_by(Message.created_at, Message.id)
            )
        )
        events = tuple(
            await session.scalars(
                select(AgentEventRecord)
                .where(AgentEventRecord.run_id == run_id)
                .order_by(AgentEventRecord.sequence)
            )
        )
    if run is None or job is None:
        raise BrowserSuccessDriverError("The completed Run or Job disappeared")
    if (
        run.status is not AgentRunStatus.COMPLETED
        or run.stop_reason is not RunStopReason.FINAL
        or job.status is not JobStatus.SUCCEEDED
    ):
        raise BrowserSuccessDriverError(
            "The formal execution did not reach successful terminal facts"
        )
    if len(assistant_messages) != 1 or (
        assistant_messages[0].status is not MessageStatus.FINAL
        or assistant_messages[0].content_markdown != expected_answer
    ):
        raise BrowserSuccessDriverError("The final assistant Message is missing or inconsistent")
    if (
        not events
        or events[-1].event_type is not AgentEventType.RUN_COMPLETED
        or sum(event.event_type in TERMINAL_AGENT_EVENT_TYPES for event in events) != 1
    ):
        raise BrowserSuccessDriverError(
            "The Run does not have one committed completed terminal Event"
        )


async def execute_browser_run(
    settings: Settings, *, run_id: UUID, job_id: UUID
) -> dict[str, object]:
    """Claim, execute and verify exactly one browser-created durable Agent Job."""

    engine = create_database_engine(settings)
    try:
        session_factory = create_database_session_factory(engine)
        outbox_id = await require_pending_target(
            session_factory,
            run_id=run_id,
            job_id=job_id,
        )
        delivery = await claim_target_delivery(
            settings,
            session_factory,
            job_id=job_id,
            outbox_id=outbox_id,
        )

        policy = DirectAnswerRuntimePolicy(
            schema_version=1,
            profile_version="direct-answer-v0",
            prompt_version="direct-answer-prompt-v0",
            context_compiler_version="context-v0",
            output_contract_version="final-markdown-v1",
            model="openai-compatible/e2e-test-provider",
            max_input_tokens=2_048,
            max_output_tokens=512,
            system_instructions="Answer directly with concise, safe Markdown.",
        )
        provider = BrowserSuccessModelProvider(run_id)
        control = SqlAlchemyAgentRunControl(session_factory)
        runtime = DirectAnswerRuntime(
            context_compiler=ContextCompilerV0(token_counter=Utf8UpperBoundTokenCounter()),
            context_manifest_store=SqlAlchemyContextManifestStore(session_factory),
            model_provider=provider,
            event_committer=SqlAlchemyAgentEventCommitter(session_factory),
            cancellation_probe=control,
        )
        execution = DirectAnswerRunExecutionService(
            loader=SqlAlchemyDirectAnswerRunLoader(session_factory, policy),
            runtime=UnifiedAgentRuntime(direct_answer_runtime=runtime),
            terminalizer=SqlAlchemyAgentRunTerminalizer(session_factory),
        )
        worker = JobExecutionRuntime(
            jobs=create_job_resources(settings, session_factory).application_service,
            handlers=FixedJobHandlerRegistry(
                {DIRECT_ANSWER_TASK_NAME: DirectAnswerJobHandler(execution)}
            ),
            worker_id=f"e2e-browser-worker-{job_id.hex}",
            heartbeat_seconds=settings.job_heartbeat_seconds,
        )
        disposition = await worker.execute(delivery.message)
        if disposition is not JobExecutionDisposition.SUCCEEDED:
            raise BrowserSuccessDriverError(
                f"The formal Job runtime returned {disposition.value} instead of succeeded"
            )
        if len(provider.requests) != 1:
            raise BrowserSuccessDriverError("The Provider call count was not exactly one")
        await _verify_terminal_facts(
            session_factory,
            run_id=run_id,
            job_id=job_id,
            expected_answer=provider.answer,
        )
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
            execute_browser_run(
                get_settings(),
                run_id=arguments.run_id,
                job_id=arguments.job_id,
            )
        )
    sys.stdout.write(f"{json.dumps(result, sort_keys=True)}\n")


if __name__ == "__main__":
    main()
