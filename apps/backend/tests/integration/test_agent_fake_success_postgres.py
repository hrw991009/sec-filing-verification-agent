"""Prove the durable application path succeeds through the one Direct Answer Runtime."""

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from industry_platform.core.database import (
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
    SqlAlchemyCommittedEventSource,
    SqlAlchemyContextManifestStore,
)
from industry_platform.modules.agent_runtime.context_compiler import (
    ContextCompilerV0,
    Utf8UpperBoundTokenCounter,
)
from industry_platform.modules.agent_runtime.domain import (
    AgentRunStatus,
    RunBudget,
    RunStopReason,
)
from industry_platform.modules.agent_runtime.events import AgentEventType
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
from industry_platform.modules.agent_runtime.models import (
    AgentEventRecord,
    AgentRunRecord,
    ContextManifestRecord,
)
from industry_platform.modules.agent_runtime.runtime import DirectAnswerRuntime
from industry_platform.modules.agent_runtime.runtime_contracts import DirectAnswerRuntimePolicy
from industry_platform.modules.agent_runtime.streaming import load_committed_replay
from industry_platform.modules.agent_runtime.tool_runtime import UnifiedAgentRuntime
from industry_platform.modules.conversations.adapters.sqlalchemy import (
    SqlAlchemyDirectAnswerTurnTransactionFactory,
)
from industry_platform.modules.conversations.domain import (
    DIRECT_ANSWER_TASK_NAME,
    StartDirectAnswerTurn,
)
from industry_platform.modules.conversations.models import Message, MessageRole, MessageStatus
from industry_platform.modules.conversations.service import ConversationApplicationService
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.identity.models import (
    User,
    UserStatus,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
    WorkspaceStatus,
)
from industry_platform.modules.jobs.adapters.sqlalchemy import SqlAlchemyOutboxTransactionFactory
from industry_platform.modules.jobs.domain import ClaimedJobDispatch, JobStatus
from industry_platform.modules.jobs.models import Job
from industry_platform.modules.jobs.resources import create_job_resources
from industry_platform.server import create_selector_event_loop
from industry_platform.workers.dispatcher import OutboxDispatcher
from industry_platform.workers.runtime import (
    DirectAnswerJobHandler,
    FixedJobHandlerRegistry,
    JobExecutionDisposition,
    JobExecutionRuntime,
)

from .postgres import PostgresProbe

ANSWER = "The durable Direct Answer path completed through the shared Runtime."


@dataclass(slots=True)
class CapturingPublisher:
    dispatches: list[ClaimedJobDispatch] = field(default_factory=list)

    async def publish(self, dispatch: ClaimedJobDispatch) -> None:
        self.dispatches.append(dispatch)


@dataclass(slots=True)
class SuccessfulModelProvider:
    requests: list[ModelRequest] = field(default_factory=list)

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelStreamItem]:
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
                pricing_version="fake-pricing-v1",
            ),
            output_text=ANSWER,
            provider_request_id="fake-request-day2-success",
        )
        yield ModelStreamDelta(schema_version=1, sequence=1, text=ANSWER)
        yield ModelStreamCompleted(schema_version=1, sequence=2, response=response)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        raise AssertionError(f"Direct Answer must stream model {request.model}")


@dataclass(slots=True)
class IncrementingClock:
    value: datetime

    def __call__(self) -> datetime:
        current = self.value
        self.value += timedelta(milliseconds=10)
        return current


def test_success_uses_the_durable_job_runtime_and_replays_without_model_reexecution(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        settings = migrated_postgres_probe.settings
        engine = create_database_engine(settings)
        session_factory = create_database_session_factory(engine)
        workspace_id = uuid4()
        user_id = uuid4()
        accepted_at = datetime.now(UTC)
        question = "Prove the successful Day 2 application path."
        try:
            async with session_factory.begin() as session:
                session.add_all(
                    (
                        User(
                            id=user_id,
                            email=f"fake-success-{user_id}@example.test",
                            password_hash=str(user_id),
                            status=UserStatus.ACTIVE,
                            password_changed_at=accepted_at,
                        ),
                        Workspace(
                            id=workspace_id,
                            name="Fake Success Workspace",
                            created_by_user_id=user_id,
                            status=WorkspaceStatus.ACTIVE,
                        ),
                        WorkspaceMembership(
                            id=uuid4(),
                            workspace_id=workspace_id,
                            user_id=user_id,
                            role=WorkspaceRole.OWNER,
                        ),
                    )
                )

            receipt = await ConversationApplicationService(
                transaction_factory=SqlAlchemyDirectAnswerTurnTransactionFactory(session_factory),
                clock=lambda: accepted_at,
            ).start_direct_answer(
                StartDirectAnswerTurn(
                    workspace_id=workspace_id,
                    user_id=user_id,
                    trace_id=TraceId("fake-success-full-chain"),
                    budget=RunBudget(
                        schema_version=1,
                        max_steps=2,
                        max_total_tokens=4_096,
                        max_cost_micro_usd=250_000,
                        deadline=accepted_at + timedelta(minutes=5),
                    ),
                    runtime_version="direct-answer-runtime-v0",
                    harness_version="harness-v0",
                    idempotency_key=f"fake-success-{user_id}",
                    question=question,
                )
            )

            publisher = CapturingPublisher()
            dispatch_result = await OutboxDispatcher(
                transaction_factory=SqlAlchemyOutboxTransactionFactory(session_factory),
                publisher=publisher,
                dispatcher_id="fake-success-dispatcher",
                batch_size=1,
                claim_seconds=60,
            ).dispatch_once()
            assert dispatch_result.published == 1
            assert len(publisher.dispatches) == 1

            policy = DirectAnswerRuntimePolicy(
                schema_version=1,
                profile_version="direct-answer-v0",
                prompt_version="direct-answer-prompt-v0",
                context_compiler_version="context-v0",
                output_contract_version="final-markdown-v1",
                model="openai-compatible/fake-success",
                max_input_tokens=2_048,
                max_output_tokens=512,
                system_instructions="Answer directly with concise, safe Markdown.",
            )
            provider = SuccessfulModelProvider()
            control = SqlAlchemyAgentRunControl(session_factory)
            runtime = DirectAnswerRuntime(
                context_compiler=ContextCompilerV0(token_counter=Utf8UpperBoundTokenCounter()),
                context_manifest_store=SqlAlchemyContextManifestStore(session_factory),
                model_provider=provider,
                event_committer=SqlAlchemyAgentEventCommitter(session_factory),
                cancellation_probe=control,
                clock=IncrementingClock(accepted_at + timedelta(seconds=1)),
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
                worker_id="fake-success-worker",
                heartbeat_seconds=1,
            )
            disposition = await worker.execute(publisher.dispatches[0].message)
            assert disposition is JobExecutionDisposition.SUCCEEDED

            async with session_factory() as session:
                run = await session.get(AgentRunRecord, receipt.run_id)
                job = await session.get(Job, receipt.job_id)
                events = tuple(
                    await session.scalars(
                        select(AgentEventRecord)
                        .where(AgentEventRecord.run_id == receipt.run_id)
                        .order_by(AgentEventRecord.sequence)
                    )
                )
                manifests = tuple(
                    await session.scalars(
                        select(ContextManifestRecord).where(
                            ContextManifestRecord.run_id == receipt.run_id
                        )
                    )
                )
                messages = tuple(
                    await session.scalars(
                        select(Message)
                        .where(Message.agent_run_id == receipt.run_id)
                        .order_by(Message.created_at, Message.id)
                    )
                )

            assert run is not None
            assert job is not None
            assert run.status is AgentRunStatus.COMPLETED
            assert run.stop_reason is RunStopReason.FINAL
            assert run.input_tokens_used == 23
            assert run.output_tokens_used == 11
            assert run.cost_micro_usd == 37
            assert job.status is JobStatus.SUCCEEDED
            assert len(manifests) == 1
            assert [
                (message.role, message.status, message.content_markdown) for message in messages
            ] == [
                (MessageRole.USER, MessageStatus.COMMITTED, question),
                (MessageRole.ASSISTANT, MessageStatus.FINAL, ANSWER),
            ]
            assert events[-1].event_type is AgentEventType.RUN_COMPLETED
            assert sum(event.event_type is AgentEventType.RUN_COMPLETED for event in events) == 1
            assert len(provider.requests) == 1
            assert provider.requests[0].run_id == receipt.run_id

            replay = await load_committed_replay(
                SqlAlchemyCommittedEventSource(session_factory),
                stream_id=run.event_stream_id,
                workspace_id=workspace_id,
                last_event_id="4",
            )
            assert replay.snapshot is None
            assert replay.events
            assert replay.events[-1].event_type is AgentEventType.RUN_COMPLETED
            assert len(provider.requests) == 1
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())
