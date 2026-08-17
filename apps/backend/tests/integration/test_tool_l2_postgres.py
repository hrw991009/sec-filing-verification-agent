"""Persist a two-round L2 Tool trajectory and its safe Trace in PostgreSQL."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from industry_platform.core.database import create_database_engine, create_database_session_factory
from industry_platform.modules.agent_harness.tool_fakes import (
    FAKE_LOOKUP_TOOL_NAME,
    FAKE_LOOKUP_TOOL_VERSION,
    FakeIndustryLookupTool,
    FakeLookupRecord,
)
from industry_platform.modules.agent_runtime.adapters.persistence import (
    SqlAlchemyAgentEventCommitter,
    SqlAlchemyAgentRunControl,
    SqlAlchemyContextManifestStore,
)
from industry_platform.modules.agent_runtime.adapters.trace_query import SqlAlchemyAgentTraceQuery
from industry_platform.modules.agent_runtime.context import TrustedRuntimeContext
from industry_platform.modules.agent_runtime.context_compiler import (
    ContextCompilerV1,
    Utf8UpperBoundTokenCounter,
)
from industry_platform.modules.agent_runtime.domain import (
    AGENT_RUNTIME_SCHEMA_VERSION,
    AgentRun,
    AgentRunStatus,
    AgentRunType,
    RunBudget,
)
from industry_platform.modules.agent_runtime.events import AgentEventType
from industry_platform.modules.agent_runtime.model import (
    ModelFinishReason,
    ModelRequest,
    ModelResponse,
    ModelStreamItem,
    ModelUsage,
)
from industry_platform.modules.agent_runtime.models import (
    AgentEventRecord,
    AgentRunRecord,
    AgentStepRecord,
    ContextManifestRecord,
)
from industry_platform.modules.agent_runtime.state import RunState
from industry_platform.modules.agent_runtime.tool_runtime import ToolL2Runtime
from industry_platform.modules.agent_runtime.tool_runtime_contracts import (
    TOOL_L2_RUNTIME_VERSION,
    ToolL2RunCommand,
    ToolL2RuntimePolicy,
)
from industry_platform.modules.conversations.adapters.sqlalchemy import (
    SqlAlchemyDirectAnswerTurnTransactionFactory,
)
from industry_platform.modules.conversations.domain import StartDirectAnswerTurn
from industry_platform.modules.conversations.service import ConversationApplicationService
from industry_platform.modules.identity.domain import (
    AuthenticatedPrincipal,
    AuthenticatedWorkspace,
    NormalizedEmail,
    TraceId,
)
from industry_platform.modules.identity.models import (
    User,
    UserStatus,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
    WorkspaceStatus,
)
from industry_platform.modules.tools.domain import ToolReference
from industry_platform.modules.tools.models import ToolCallRecord, ToolRunRecord
from industry_platform.modules.tools.registry import RegistryToolExecutor, ToolRegistry
from industry_platform.modules.workspaces.domain import WorkspaceAction, WorkspaceScope
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe


@dataclass(slots=True)
class CompleteModelProvider:
    responses: list[ModelResponse]
    requests: list[ModelRequest] = field(default_factory=list)

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        raise AssertionError(f"L2 must not stream structured decisions: {request.model}")

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("Fake L2 Model script exhausted")
        return self.responses.pop(0)


@dataclass(slots=True)
class IncrementingClock:
    value: datetime

    def __call__(self) -> datetime:
        current = self.value
        self.value += timedelta(milliseconds=10)
        return current


def response(text: str, *, request_id: str) -> ModelResponse:
    return ModelResponse(
        schema_version=1,
        model="openai-compatible/fake-tool-l2",
        finish_reason=ModelFinishReason.STOP,
        usage=ModelUsage(
            input_tokens=10,
            output_tokens=5,
            cached_input_tokens=0,
            cost_micro_usd=20,
            pricing_version="fake-pricing-v1",
        ),
        output_text=text,
        provider_request_id=request_id,
    )


def action(query: str) -> str:
    return (
        '{"decision":{"schema_version":1,"kind":"tool_call",'
        f'"name":"{FAKE_LOOKUP_TOOL_NAME}","version":"{FAKE_LOOKUP_TOOL_VERSION}",'
        f'"arguments":{{"query":"{query}"}}}}}}'
    )


def final() -> str:
    return (
        '{"decision":{"schema_version":1,"kind":"final",'
        '"content_markdown":"## Market comparison\\n\\nSteel and copper both changed."}}'
    )


def test_l2_two_rounds_persist_distinct_tool_facts_and_trace(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        settings = migrated_postgres_probe.settings
        engine = create_database_engine(settings)
        session_factory = create_database_session_factory(engine)
        workspace_id = uuid4()
        user_id = uuid4()
        session_id = uuid4()
        accepted_at = datetime.now(UTC)
        budget = RunBudget(
            schema_version=1,
            max_steps=6,
            max_total_tokens=30_000,
            max_cost_micro_usd=100_000,
            deadline=accepted_at + timedelta(minutes=5),
        )
        try:
            async with session_factory.begin() as session:
                session.add_all(
                    (
                        User(
                            id=user_id,
                            email=f"tool-l2-{user_id}@example.test",
                            password_hash=str(user_id),
                            status=UserStatus.ACTIVE,
                            password_changed_at=accepted_at,
                        ),
                        Workspace(
                            id=workspace_id,
                            name="Tool L2 Persistence",
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
                    trace_id=TraceId("trace-tool-l2-postgres"),
                    budget=budget,
                    runtime_version=TOOL_L2_RUNTIME_VERSION,
                    harness_version="harness-v1",
                    idempotency_key=f"tool-l2-{user_id}",
                    question="Compare steel and copper market changes.",
                )
            )
            async with session_factory.begin() as session:
                record = await session.get(AgentRunRecord, receipt.run_id)
                queued = await session.scalar(
                    select(AgentEventRecord).where(
                        AgentEventRecord.run_id == receipt.run_id,
                        AgentEventRecord.sequence == 1,
                    )
                )
                assert record is not None
                assert queued is not None
                record.run_type = AgentRunType.TOOL_LOOP
                stream_id = record.event_stream_id
                queued.payload = {
                    "run_type": AgentRunType.TOOL_LOOP.value,
                    "runtime_version": TOOL_L2_RUNTIME_VERSION,
                    "harness_version": "harness-v1",
                    "loop_level": "l2",
                    "tool_call_limit": 2,
                }

            run = AgentRun(
                schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
                run_id=receipt.run_id,
                event_stream_id=stream_id,
                workspace_id=workspace_id,
                user_id=user_id,
                run_type=AgentRunType.TOOL_LOOP,
                runtime_version=TOOL_L2_RUNTIME_VERSION,
                harness_version="harness-v1",
                budget=budget,
                trace_id=TraceId("trace-tool-l2-postgres"),
                status=AgentRunStatus.QUEUED,
                state_revision=0,
                created_at=accepted_at,
                started_at=None,
                terminal_at=None,
                stop_reason=None,
                thread_id=receipt.conversation_id,
                turn_id=receipt.turn_id,
                job_id=receipt.job_id,
            )
            ids = tuple(uuid4() for _ in range(13))
            command = ToolL2RunCommand(
                run=run,
                state=RunState(
                    schema_version=1,
                    run_id=run.run_id,
                    workspace_id=workspace_id,
                    revision=0,
                    status=AgentRunStatus.QUEUED,
                    step_count=0,
                    event_count=1,
                    input_tokens_used=0,
                    output_tokens_used=0,
                    cost_micro_usd=0,
                    updated_at=accepted_at,
                ),
                policy=ToolL2RuntimePolicy(
                    schema_version=1,
                    profile_version="tool-l2-v1",
                    prompt_version="tool-l2-prompt-v1",
                    context_compiler_version="context-v1",
                    output_contract_version="final-markdown-v1",
                    toolset_version="fake-industry-toolset-v1",
                    model="openai-compatible/fake-tool-l2",
                    max_input_tokens=12_000,
                    max_decision_output_tokens=256,
                    max_tool_calls=2,
                    system_instructions="Use bounded Tool rounds, then answer.",
                    available_tools=(
                        ToolReference(FAKE_LOOKUP_TOOL_NAME, FAKE_LOOKUP_TOOL_VERSION),
                    ),
                ),
                decision_model_step_ids=(ids[0], ids[1], ids[2]),
                tool_step_ids=(ids[3], ids[4]),
                decision_manifest_ids=(ids[5], ids[6], ids[7]),
                tool_call_ids=(ids[8], ids[9]),
                approval_request_ids=(ids[10], ids[11]),
                final_step_id=ids[12],
                user_question="Compare steel and copper market changes.",
                side_effect_idempotency_keys=(None, None),
            )
            runtime_context = TrustedRuntimeContext(
                principal=AuthenticatedPrincipal(
                    user_id=user_id,
                    session_id=session_id,
                    email=NormalizedEmail(f"tool-l2-{user_id}@example.test"),
                    workspaces=(
                        AuthenticatedWorkspace(
                            workspace_id=workspace_id,
                            name="Tool L2 Persistence",
                            role="owner",
                        ),
                    ),
                ),
                workspace_scope=WorkspaceScope(
                    workspace_id=workspace_id,
                    user_id=user_id,
                    role="owner",
                ),
                capabilities=frozenset({WorkspaceAction.VIEW, WorkspaceAction.RUN_TOOL}),
                budget=budget,
                secret_references=("provider/postgres-tool-l2",),
            )
            provider = CompleteModelProvider(
                responses=[
                    response(action("steel"), request_id="l2-postgres-1"),
                    response(action("copper"), request_id="l2-postgres-2"),
                    response(final(), request_id="l2-postgres-3"),
                ]
            )
            tool = FakeIndustryLookupTool(
                {
                    "steel": FakeLookupRecord(
                        text="Steel demand rose 3%.",
                        locator="fixture://postgres/steel",
                        source_version="fixture-postgres-v1",
                    ),
                    "copper": FakeLookupRecord(
                        text="Copper inventories fell 2%.",
                        locator="fixture://postgres/copper",
                        source_version="fixture-postgres-v1",
                    ),
                }
            )
            registry = ToolRegistry((tool,))
            clock = IncrementingClock(accepted_at + timedelta(seconds=1))
            runtime = ToolL2Runtime(
                context_compiler=ContextCompilerV1(token_counter=Utf8UpperBoundTokenCounter()),
                context_manifest_store=SqlAlchemyContextManifestStore(session_factory),
                model_provider=provider,
                tool_registry=registry,
                tool_executor=RegistryToolExecutor(registry, clock=clock),
                event_committer=SqlAlchemyAgentEventCommitter(session_factory),
                cancellation_probe=SqlAlchemyAgentRunControl(session_factory),
                clock=clock,
            )

            events = [event async for event in runtime.run(command, runtime_context)]
            assert events[-1].event_type is AgentEventType.RUN_COMPLETED

            async with session_factory() as session:
                persisted_run = await session.get(AgentRunRecord, run.run_id)
                calls = tuple(
                    await session.scalars(
                        select(ToolCallRecord)
                        .where(ToolCallRecord.run_id == run.run_id)
                        .order_by(ToolCallRecord.created_at, ToolCallRecord.id)
                    )
                )
                audits = tuple(
                    await session.scalars(
                        select(ToolRunRecord)
                        .where(ToolRunRecord.run_id == run.run_id)
                        .order_by(ToolRunRecord.created_at, ToolRunRecord.id)
                    )
                )
                steps = tuple(
                    await session.scalars(
                        select(AgentStepRecord)
                        .where(AgentStepRecord.run_id == run.run_id)
                        .order_by(AgentStepRecord.sequence)
                    )
                )
                manifests = tuple(
                    await session.scalars(
                        select(ContextManifestRecord)
                        .where(ContextManifestRecord.run_id == run.run_id)
                        .order_by(ContextManifestRecord.created_at, ContextManifestRecord.id)
                    )
                )

            assert persisted_run is not None
            assert persisted_run.state_revision == events[-1].payload["state_revision"]
            assert persisted_run.step_count == 6
            assert persisted_run.cost_micro_usd == 60
            assert {call.id for call in calls} == {ids[8], ids[9]}
            assert all(call.status == "completed" for call in calls)
            assert all(audit.status == "completed" for audit in audits)
            assert len(steps) == 6
            assert len(manifests) == 3
            assert [
                sum(source.get("source_kind") == "tool_observation" for source in manifest.sources)
                for manifest in manifests
            ] == [0, 1, 2]
            assert "provider/postgres-tool-l2" not in repr(events)

            trace = await SqlAlchemyAgentTraceQuery(session_factory).get(
                scope=WorkspaceScope(
                    workspace_id=workspace_id,
                    user_id=user_id,
                    role="owner",
                ),
                run_id=run.run_id,
            )
            assert trace.run.state_revision == persisted_run.state_revision
            assert trace.run.step_count == 6
            assert (
                len(
                    [
                        event
                        for event in trace.events
                        if event.event_type is AgentEventType.TOOL_COMPLETED
                    ]
                )
                == 2
            )
            assert trace.events[0].details["loop_level"] == "l2"
            assert trace.events[0].details["tool_call_limit"] == 2
        finally:
            await engine.dispose()

    asyncio.run(exercise(), loop_factory=create_selector_event_loop)
