"""Persist the formal L1 Tool trajectory and audit projections atomically in PostgreSQL."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from industry_platform.core.database import create_database_engine, create_database_session_factory
from industry_platform.modules.agent_harness.tool_fakes import (
    FAKE_LOOKUP_TOOL_NAME,
    FAKE_LOOKUP_TOOL_VERSION,
    FakeIndustryLookupTool,
    FakeLookupInput,
    FakeLookupOutput,
    FakeLookupRecord,
)
from industry_platform.modules.agent_runtime.adapters import persistence as persistence_adapter
from industry_platform.modules.agent_runtime.adapters.persistence import (
    AgentEventPersistenceError,
    SqlAlchemyAgentEventCommitter,
    SqlAlchemyAgentRunControl,
    SqlAlchemyContextManifestStore,
)
from industry_platform.modules.agent_runtime.adapters.trace_query import (
    AgentTraceDataError,
    SqlAlchemyAgentTraceQuery,
)
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
from industry_platform.modules.agent_runtime.events import AgentEvent, AgentEventType
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
from industry_platform.modules.agent_runtime.tool_runtime import ToolL1Runtime
from industry_platform.modules.agent_runtime.tool_runtime_contracts import (
    TOOL_L1_RUNTIME_VERSION,
    ToolL1RunCommand,
    ToolL1RuntimePolicy,
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
        raise AssertionError("L1 must not stream the structured Action response")

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("Fake Model script exhausted")
        return self.responses.pop(0)


@dataclass(slots=True)
class IncrementingClock:
    value: datetime

    def __call__(self) -> datetime:
        current = self.value
        self.value += timedelta(milliseconds=10)
        return current


@dataclass(slots=True)
class TamperingToolTerminalCommitter:
    """Corrupt one Tool Step settlement to prove its whole batch rolls back."""

    delegate: SqlAlchemyAgentEventCommitter
    tampered: bool = False

    async def append(self, event: AgentEvent) -> None:
        await self.delegate.append(event)

    async def append_batch(self, events: tuple[AgentEvent, ...]) -> None:
        selected_events = events
        if not self.tampered and any(
            event.event_type is AgentEventType.TOOL_COMPLETED for event in events
        ):
            self.tampered = True
            tampered_events: list[AgentEvent] = []
            for event in events:
                if event.event_type is AgentEventType.STEP_COMPLETED:
                    cost_micro_usd = event.payload.get("cost_micro_usd")
                    if isinstance(cost_micro_usd, bool) or not isinstance(cost_micro_usd, int):
                        raise AssertionError("Tool Step cost fixture is invalid")
                    event = replace(
                        event,
                        payload={
                            **dict(event.payload),
                            "cost_micro_usd": cost_micro_usd + 1,
                        },
                    )
                tampered_events.append(event)
            selected_events = tuple(tampered_events)
        await self.delegate.append_batch(selected_events)


class CostedFakeIndustryLookupTool(FakeIndustryLookupTool):
    """Keep the deterministic fixture while proving non-zero Tool cost projection."""

    async def invoke(
        self,
        value: FakeLookupInput,
        runtime_context: TrustedRuntimeContext,
        *,
        idempotency_key: str | None,
    ) -> tuple[FakeLookupOutput, int]:
        output, _cost = await super().invoke(
            value,
            runtime_context,
            idempotency_key=idempotency_key,
        )
        return output, 17


def _response(text: str, *, request_id: str) -> ModelResponse:
    return ModelResponse(
        schema_version=1,
        model="openai-compatible/fake-tool-l1",
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


@pytest.mark.parametrize("tamper_tool_step_cost", [False, True], ids=["success", "tampered"])
def test_l1_success_persists_tool_call_run_observation_and_safe_trace(
    migrated_postgres_probe: PostgresProbe,
    tamper_tool_step_cost: bool,
) -> None:
    async def exercise() -> None:
        settings = migrated_postgres_probe.settings
        engine = create_database_engine(settings)
        session_factory = create_database_session_factory(engine)
        workspace_id = uuid4()
        user_id = uuid4()
        other_user_id = uuid4()
        session_id = uuid4()
        accepted_at = datetime.now(UTC)
        budget = RunBudget(
            schema_version=1,
            max_steps=4,
            max_total_tokens=20_000,
            max_cost_micro_usd=100_000,
            deadline=accepted_at + timedelta(minutes=5),
        )
        try:
            async with session_factory.begin() as session:
                session.add_all(
                    (
                        User(
                            id=user_id,
                            email=f"tool-l1-{user_id}@example.test",
                            password_hash=str(user_id),
                            status=UserStatus.ACTIVE,
                            password_changed_at=accepted_at,
                        ),
                        User(
                            id=other_user_id,
                            email=f"tool-l1-other-{other_user_id}@example.test",
                            password_hash=str(other_user_id),
                            status=UserStatus.ACTIVE,
                            password_changed_at=accepted_at,
                        ),
                        Workspace(
                            id=workspace_id,
                            name="Tool L1 Persistence",
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
                    trace_id=TraceId("trace-tool-l1-postgres"),
                    budget=budget,
                    runtime_version=TOOL_L1_RUNTIME_VERSION,
                    harness_version="harness-v1",
                    idempotency_key=f"tool-l1-{user_id}",
                    question="What changed in the steel market?",
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
                    "runtime_version": TOOL_L1_RUNTIME_VERSION,
                    "harness_version": "harness-v1",
                }

            run = AgentRun(
                schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
                run_id=receipt.run_id,
                event_stream_id=stream_id,
                workspace_id=workspace_id,
                user_id=user_id,
                run_type=AgentRunType.TOOL_LOOP,
                runtime_version=TOOL_L1_RUNTIME_VERSION,
                harness_version="harness-v1",
                budget=budget,
                trace_id=TraceId("trace-tool-l1-postgres"),
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
            ids = tuple(uuid4() for _ in range(8))
            command = ToolL1RunCommand(
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
                policy=ToolL1RuntimePolicy(
                    schema_version=1,
                    profile_version="tool-l1-v1",
                    prompt_version="tool-l1-prompt-v1",
                    context_compiler_version="context-v1",
                    output_contract_version="final-markdown-v1",
                    toolset_version="fake-industry-toolset-v1",
                    model="openai-compatible/fake-tool-l1",
                    max_input_tokens=8_192,
                    max_action_output_tokens=128,
                    max_final_output_tokens=128,
                    system_instructions="Use the configured Tool exactly once.",
                    available_tools=(
                        ToolReference(FAKE_LOOKUP_TOOL_NAME, FAKE_LOOKUP_TOOL_VERSION),
                    ),
                ),
                action_model_step_id=ids[0],
                tool_step_id=ids[1],
                answer_model_step_id=ids[2],
                final_step_id=ids[3],
                action_manifest_id=ids[4],
                answer_manifest_id=ids[5],
                tool_call_id=ids[6],
                approval_request_id=ids[7],
                user_question="What changed in the steel market?",
            )
            runtime_context = TrustedRuntimeContext(
                principal=AuthenticatedPrincipal(
                    user_id=user_id,
                    session_id=session_id,
                    email=NormalizedEmail(f"tool-l1-{user_id}@example.test"),
                    workspaces=(
                        AuthenticatedWorkspace(
                            workspace_id=workspace_id,
                            name="Tool L1 Persistence",
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
                secret_references=("provider/postgres-tool-l1",),
            )
            provider = CompleteModelProvider(
                responses=[
                    _response(
                        '{"schema_version":1,"kind":"tool_call",'
                        '"name":"fake.industry_lookup","version":"v1",'
                        '"arguments":{"query":"steel"}}',
                        request_id="action-postgres",
                    ),
                    _response("Steel demand rose 3%.", request_id="answer-postgres"),
                ]
            )
            tool = CostedFakeIndustryLookupTool(
                {
                    "steel": FakeLookupRecord(
                        text="Steel demand rose 3%.",
                        locator="fixture://postgres/steel",
                        source_version="fixture-postgres-v1",
                    )
                }
            )
            registry = ToolRegistry((tool,))
            clock = IncrementingClock(accepted_at + timedelta(seconds=1))
            event_committer = SqlAlchemyAgentEventCommitter(session_factory)
            selected_committer = (
                TamperingToolTerminalCommitter(event_committer)
                if tamper_tool_step_cost
                else event_committer
            )
            runtime = ToolL1Runtime(
                context_compiler=ContextCompilerV1(token_counter=Utf8UpperBoundTokenCounter()),
                context_manifest_store=SqlAlchemyContextManifestStore(session_factory),
                model_provider=provider,
                tool_registry=registry,
                tool_executor=RegistryToolExecutor(registry, clock=clock),
                event_committer=selected_committer,
                cancellation_probe=SqlAlchemyAgentRunControl(session_factory),
                clock=clock,
            )

            if tamper_tool_step_cost:
                with pytest.raises(AgentEventPersistenceError):
                    _events = [event async for event in runtime.run(command, runtime_context)]
                async with session_factory() as session:
                    call = await session.get(ToolCallRecord, ids[6])
                    audit = await session.get(ToolRunRecord, ids[6])
                    persisted_run = await session.get(AgentRunRecord, run.run_id)
                    stored_events = tuple(
                        await session.scalars(
                            select(AgentEventRecord)
                            .where(AgentEventRecord.run_id == run.run_id)
                            .order_by(AgentEventRecord.sequence)
                        )
                    )
                assert call is not None
                assert audit is not None
                assert persisted_run is not None
                assert call.status == audit.status == "running"
                assert call.cost_micro_usd == audit.cost_micro_usd == 0
                assert persisted_run.cost_micro_usd == 20
                assert AgentEventType.TOOL_COMPLETED not in {
                    event.event_type for event in stored_events
                }
                assert not any(
                    event.event_type is AgentEventType.STEP_COMPLETED
                    and event.payload.get("step_kind") == "tool"
                    for event in stored_events
                )
                return

            events = [event async for event in runtime.run(command, runtime_context)]
            assert events[-1].event_type is AgentEventType.RUN_COMPLETED, [
                (event.event_type.value, dict(event.payload)) for event in events
            ]

            async with session_factory() as session:
                call = await session.get(ToolCallRecord, ids[6])
                audit = await session.get(ToolRunRecord, ids[6])
                persisted_run = await session.get(AgentRunRecord, run.run_id)
                steps = tuple(
                    await session.scalars(
                        select(AgentStepRecord)
                        .where(AgentStepRecord.run_id == run.run_id)
                        .order_by(AgentStepRecord.sequence)
                    )
                )
                manifests = tuple(
                    await session.scalars(
                        select(ContextManifestRecord).where(
                            ContextManifestRecord.run_id == run.run_id
                        )
                    )
                )
                stored_events = tuple(
                    await session.scalars(
                        select(AgentEventRecord)
                        .where(AgentEventRecord.run_id == run.run_id)
                        .order_by(AgentEventRecord.sequence)
                    )
                )

            assert call is not None
            assert audit is not None
            assert persisted_run is not None
            assert persisted_run.state_revision == events[-1].payload["state_revision"]
            assert persisted_run.state_revision > 1
            assert call.status == "completed"
            assert call.requested_by_step_id == ids[0]
            assert call.execution_step_id == ids[1]
            assert call.observation is not None
            assert call.observation["model_text"] == "Steel demand rose 3%."
            assert call.observation_envelope_sha256 is not None
            assert call.retry_classification == "never"
            assert call.cost_micro_usd == 17
            assert audit.status == "completed"
            assert audit.retry_classification == "never"
            assert audit.cost_micro_usd == 17
            assert persisted_run.cost_micro_usd == 57
            assert audit.sanitized_input_summary == {
                "argument_count": 1,
                "canonical_bytes": len(b'{"query":"steel"}'),
            }
            assert "steel" not in repr(audit.sanitized_input_summary)
            assert len(steps) == 4
            assert steps[1].cost_micro_usd == 17
            assert len(manifests) == 2
            assert len(stored_events) == len(events)
            assert "provider/postgres-tool-l1" not in repr(
                [(event.event_type, event.payload) for event in stored_events]
            )

            trace = await SqlAlchemyAgentTraceQuery(session_factory).get(
                scope=WorkspaceScope(
                    workspace_id=workspace_id,
                    user_id=user_id,
                    role="owner",
                ),
                run_id=run.run_id,
            )
            assert trace.run.state_revision == persisted_run.state_revision
            requested_trace = next(
                event for event in trace.events if event.event_type is AgentEventType.TOOL_REQUESTED
            )
            started_trace = next(
                event for event in trace.events if event.event_type is AgentEventType.TOOL_STARTED
            )
            assert "sanitized_arguments_sha256" not in requested_trace.details
            assert "sanitized_arguments_sha256" not in started_trace.details
            tool_trace = next(
                event for event in trace.events if event.event_type is AgentEventType.TOOL_COMPLETED
            )
            observation_source = trace.context_manifests[1].sources[-1]
            assert tool_trace.details["observation_id"] == observation_source.source_id
            assert (
                tool_trace.details["observation_envelope_sha256"]
                == observation_source.source_sha256
            )

            original_sources = [dict(source) for source in manifests[1].sources]
            tampered_sources = [
                {
                    **source,
                    **(
                        {"source_sha256": "0" * 64}
                        if source.get("source_kind") == "tool_observation"
                        else {}
                    ),
                }
                for source in original_sources
            ]
            async with session_factory.begin() as session:
                await session.execute(
                    update(ContextManifestRecord)
                    .where(ContextManifestRecord.id == manifests[1].id)
                    .values(sources=tampered_sources)
                )
            with pytest.raises(AgentTraceDataError):
                await SqlAlchemyAgentTraceQuery(session_factory).get(
                    scope=WorkspaceScope(
                        workspace_id=workspace_id,
                        user_id=user_id,
                        role="owner",
                    ),
                    run_id=run.run_id,
                )
            async with session_factory.begin() as session:
                await session.execute(
                    update(ContextManifestRecord)
                    .where(ContextManifestRecord.id == manifests[1].id)
                    .values(sources=original_sources)
                )

            async with session_factory() as session:
                with pytest.raises(IntegrityError) as exc_info:
                    await session.execute(
                        update(ToolCallRecord)
                        .where(ToolCallRecord.id == ids[6])
                        .values(observation_content_sha256=None)
                    )
                await session.rollback()
            assert (
                getattr(getattr(exc_info.value.orig, "diag", None), "constraint_name", None)
                == "ck_tool_calls_observation_fields_paired_and_bounded"
            )

            async with session_factory() as session:
                with pytest.raises(IntegrityError) as exc_info:
                    await session.execute(
                        update(ToolCallRecord)
                        .where(ToolCallRecord.id == ids[6])
                        .values(policy_decision=None, policy_reason_code=None)
                    )
                await session.rollback()
            assert (
                getattr(getattr(exc_info.value.orig, "diag", None), "constraint_name", None)
                == "ck_tool_calls_lifecycle_consistent"
            )

            async with session_factory() as session:
                with pytest.raises(IntegrityError) as exc_info:
                    await session.execute(
                        update(ToolCallRecord)
                        .where(ToolCallRecord.id == ids[6])
                        .values(side_effect_class="idempotent_write")
                    )
                await session.rollback()
            assert (
                getattr(getattr(exc_info.value.orig, "diag", None), "constraint_name", None)
                == "ck_tool_calls_allowed_write_requires_idempotency"
            )

            async with session_factory() as session:
                with pytest.raises(IntegrityError) as exc_info:
                    await session.execute(
                        update(ToolRunRecord)
                        .where(ToolRunRecord.id == ids[6])
                        .values(actor_user_id=other_user_id)
                    )
                await session.rollback()
            assert (
                getattr(getattr(exc_info.value.orig, "diag", None), "constraint_name", None)
                == "fk_tool_runs_actor_run_workspace"
            )

            async with session_factory() as session:
                with pytest.raises(IntegrityError) as exc_info:
                    await session.execute(
                        delete(AgentStepRecord).where(AgentStepRecord.id == ids[0])
                    )
                await session.rollback()
            assert (
                getattr(getattr(exc_info.value.orig, "diag", None), "constraint_name", None)
                == "fk_tool_calls_request_step_run_workspace"
            )

            interrupted_call_id = uuid4()
            interrupted_at = events[-1].occurred_at + timedelta(seconds=1)
            common = {
                "id": interrupted_call_id,
                "workspace_id": workspace_id,
                "run_id": run.run_id,
                "schema_version": 1,
                "requested_tool_name": FAKE_LOOKUP_TOOL_NAME,
                "requested_tool_version": FAKE_LOOKUP_TOOL_VERSION,
                "toolset_version": "fake-industry-toolset-v1",
                "policy_version": "tool-policy-unresolved-v1",
                "status": "requested",
                "cost_micro_usd": 0,
                "created_at": events[-1].occurred_at,
                "updated_at": events[-1].occurred_at,
            }
            async with session_factory.begin() as session:
                session.add(
                    ToolCallRecord(
                        **common,
                        requested_by_step_id=ids[2],
                        execution_step_id=None,
                        sanitized_arguments_hash=b"a" * 32,
                    )
                )

            async with session_factory() as session:
                invalid_audit = ToolRunRecord(
                    **common,
                    actor_user_id=user_id,
                    actor_role="owner",
                    trace_id=str(run.trace_id),
                    sanitizer_version="tool-arguments-structural-v1",
                    sanitized_input_summary={"argument_count": 1, "canonical_bytes": 17},
                    source_summary=[],
                )
                invalid_audit.status = "running"
                session.add(invalid_audit)
                with pytest.raises(IntegrityError) as exc_info:
                    await session.commit()
                await session.rollback()
            assert (
                getattr(getattr(exc_info.value.orig, "diag", None), "constraint_name", None)
                == "ck_tool_runs_lifecycle_consistent"
            )

            async with session_factory.begin() as session:
                session.add(
                    ToolRunRecord(
                        **common,
                        actor_user_id=user_id,
                        actor_role="owner",
                        trace_id=str(run.trace_id),
                        sanitizer_version="tool-arguments-structural-v1",
                        sanitized_input_summary={"argument_count": 1, "canonical_bytes": 17},
                        source_summary=[],
                    )
                )
                await session.flush()
                await persistence_adapter._settle_interrupted_tool_facts(
                    session,
                    AgentEvent(
                        schema_version=1,
                        stream_id=run.event_stream_id,
                        run_id=run.run_id,
                        workspace_id=workspace_id,
                        sequence=len(events) + 1,
                        occurred_at=interrupted_at,
                        trace_id=run.trace_id,
                        event_type=AgentEventType.RUN_FAILED,
                        payload={"stop_reason": "runtime_error"},
                    ),
                )

            async with session_factory() as session:
                interrupted_call = await session.get(ToolCallRecord, interrupted_call_id)
                interrupted_audit = await session.get(ToolRunRecord, interrupted_call_id)
            assert interrupted_call is not None
            assert interrupted_audit is not None
            assert interrupted_call.status == interrupted_audit.status == "failed"
            assert interrupted_call.execution_step_id is None
            assert interrupted_call.policy_decision is None
            assert interrupted_audit.duration_ms is None
            assert interrupted_call.error_code == "runtime_interrupted"
        finally:
            await engine.dispose()

    asyncio.run(exercise(), loop_factory=create_selector_event_loop)
