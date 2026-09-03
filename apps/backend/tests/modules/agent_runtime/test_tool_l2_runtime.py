"""Bounded-loop, budget, and no-progress tests for the formal L2 Runtime."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest

from industry_platform.adapters.openai_compatible_schema import (
    InvalidProviderResponse,
    validate_structured_output,
    validate_supported_schema,
)
from industry_platform.modules.agent_harness.direct_answer import HarnessTrustedIdentity
from industry_platform.modules.agent_harness.profiles import ToolL2Profile
from industry_platform.modules.agent_harness.runner import HarnessRunner
from industry_platform.modules.agent_harness.scenarios import load_scenario_dataset
from industry_platform.modules.agent_harness.tool_fakes import (
    FAKE_DATABASE_TOOL_NAME,
    FAKE_DATABASE_TOOL_VERSION,
    FAKE_LOOKUP_TOOL_NAME,
    FAKE_LOOKUP_TOOL_VERSION,
    FakeDatabaseLookupTool,
    FakeIndustryLookupTool,
    FakeLookupRecord,
    fake_lookup_definition,
)
from industry_platform.modules.agent_harness.tool_use import (
    ToolL2HarnessExecutionIdentity,
    ToolL2ScenarioMaterializer,
)
from industry_platform.modules.agent_runtime.context import (
    ContextManifest,
    TrustedRuntimeContext,
)
from industry_platform.modules.agent_runtime.context_compiler import (
    ContextCompilerV0,
    ContextCompilerV1,
)
from industry_platform.modules.agent_runtime.domain import (
    AGENT_RUNTIME_SCHEMA_VERSION,
    AgentRun,
    AgentRunStatus,
    AgentRunType,
    RunBudget,
    RunStopReason,
)
from industry_platform.modules.agent_runtime.events import AgentEvent, AgentEventType
from industry_platform.modules.agent_runtime.model import (
    ModelFinishReason,
    ModelRequest,
    ModelResponse,
    ModelStreamItem,
    ModelUsage,
)
from industry_platform.modules.agent_runtime.ports import (
    CancellationProbe,
    ModelProvider,
    ToolExecutor,
)
from industry_platform.modules.agent_runtime.runtime import DirectAnswerRuntime
from industry_platform.modules.agent_runtime.state import RunState
from industry_platform.modules.agent_runtime.tool_runtime import (
    ToolL2Runtime,
    UnifiedAgentRuntime,
)
from industry_platform.modules.agent_runtime.tool_runtime_contracts import (
    TOOL_L2_RUNTIME_VERSION,
    ToolL2RunCommand,
    ToolL2RuntimePolicy,
    ToolLoopFinalDecision,
    decode_tool_loop_decision,
    tool_loop_decision_response_schema,
)
from industry_platform.modules.identity.domain import (
    AuthenticatedPrincipal,
    AuthenticatedWorkspace,
    NormalizedEmail,
    TraceId,
)
from industry_platform.modules.tools.domain import (
    ToolAction,
    ToolCall,
    ToolExecutionResult,
    ToolReference,
)
from industry_platform.modules.tools.registry import (
    RegistryToolExecutor,
    ToolExecutionError,
    ToolRegistry,
)
from industry_platform.modules.workspaces.domain import WorkspaceAction, WorkspaceScope

NOW = datetime(2026, 8, 16, 10, 0, tzinfo=UTC)
RUN_ID = UUID("90000000-0000-4000-8000-000000000001")
STREAM_ID = UUID("90000000-0000-4000-8000-000000000002")
WORKSPACE_ID = UUID("90000000-0000-4000-8000-000000000003")
USER_ID = UUID("90000000-0000-4000-8000-000000000004")
SESSION_ID = UUID("90000000-0000-4000-8000-000000000005")
REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
DATASET_PATH = REPOSITORY_ROOT / "evals" / "scenarios" / "day3-l2-v1.json"


def stable_id(name: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"industry-platform:tool-l2:{name}")


class FixedTokenCounter:
    version = "fixed-token-counter-v1"

    def count(self, *, model: str, messages: tuple[object, ...]) -> int:
        del model
        return len(messages) * 10


class RecordingManifestStore:
    def __init__(self) -> None:
        self.manifests: list[ContextManifest] = []

    async def save(self, manifest: ContextManifest) -> None:
        self.manifests.append(manifest)


class RecordingCommitter:
    def __init__(self) -> None:
        self.events: list[AgentEvent] = []
        self.batches: list[tuple[AgentEvent, ...]] = []

    async def append(self, event: AgentEvent) -> None:
        self.events.append(event)

    async def append_batch(self, events: tuple[AgentEvent, ...]) -> None:
        self.batches.append(events)
        self.events.extend(events)


class NeverCancelled:
    async def is_cancel_requested(self, *, run_id: UUID, workspace_id: UUID) -> bool:
        del run_id, workspace_id
        return False


class CancelAfterFirstTool:
    def __init__(self, committer: RecordingCommitter) -> None:
        self._committer = committer

    async def is_cancel_requested(self, *, run_id: UUID, workspace_id: UUID) -> bool:
        del run_id, workspace_id
        return any(
            event.event_type is AgentEventType.TOOL_COMPLETED for event in self._committer.events
        )


class QueueModelProvider:
    def __init__(self, responses: tuple[ModelResponse, ...]) -> None:
        self._responses = list(responses)
        self.requests: list[ModelRequest] = []

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        raise AssertionError(f"L2 must not stream structured decisions: {request.model}")

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("Unexpected L2 model call")
        return self._responses.pop(0)

    @property
    def remaining(self) -> int:
        return len(self._responses)


@dataclass
class IncrementingClock:
    value: datetime = NOW
    increment: timedelta = timedelta(milliseconds=1)

    def __call__(self) -> datetime:
        current = self.value
        self.value += self.increment
        return current


@dataclass
class ToolCompletionSignal:
    completed: bool = False


class DeadlineAfterToolCompletionClock:
    def __init__(self, signal: ToolCompletionSignal, deadline: datetime) -> None:
        self._signal = signal
        self._deadline = deadline
        self._current = NOW

    def __call__(self) -> datetime:
        if self._signal.completed:
            return self._deadline
        current = self._current
        self._current += timedelta(milliseconds=1)
        return current


class FailSecondToolExecutor:
    def __init__(
        self,
        delegate: ToolExecutor[ToolCall, TrustedRuntimeContext, ToolExecutionResult],
    ) -> None:
        self._delegate = delegate
        self.calls = 0

    async def execute(
        self,
        call: ToolCall,
        runtime_context: TrustedRuntimeContext,
    ) -> ToolExecutionResult:
        self.calls += 1
        if self.calls == 2:
            raise ToolExecutionError("tool_timeout")
        return await self._delegate.execute(call, runtime_context)


class SignalAfterToolExecutor:
    def __init__(
        self,
        delegate: ToolExecutor[ToolCall, TrustedRuntimeContext, ToolExecutionResult],
        signal: ToolCompletionSignal,
    ) -> None:
        self._delegate = delegate
        self._signal = signal

    async def execute(
        self,
        call: ToolCall,
        runtime_context: TrustedRuntimeContext,
    ) -> ToolExecutionResult:
        result = await self._delegate.execute(call, runtime_context)
        self._signal.completed = True
        return result


def model_response(
    output_text: str,
    *,
    request_id: str,
    input_tokens: int = 10,
    output_tokens: int = 5,
    cost_micro_usd: int = 20,
) -> ModelResponse:
    return ModelResponse(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        model="openai-compatible/fake-model",
        finish_reason=ModelFinishReason.STOP,
        usage=ModelUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=0,
            cost_micro_usd=cost_micro_usd,
            pricing_version="fake-pricing-v1",
        ),
        output_text=output_text,
        provider_request_id=request_id,
    )


def action_decision(
    query: str,
    *,
    name: str = FAKE_LOOKUP_TOOL_NAME,
    version: str = FAKE_LOOKUP_TOOL_VERSION,
) -> str:
    return (
        '{"decision":{"schema_version":1,"kind":"tool_call",'
        f'"name":"{name}","version":"{version}",'
        f'"arguments":{{"query":"{query}"}}}}}}'
    )


def final_decision(markdown: str = "## Bounded result\n\nTwo observations were used.") -> str:
    escaped = markdown.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'{{"decision":{{"schema_version":1,"kind":"final","content_markdown":"{escaped}"}}}}'


def budget(
    *,
    max_steps: int = 6,
    max_total_tokens: int = 5_000,
    max_cost_micro_usd: int = 10_000,
    deadline: datetime = NOW + timedelta(minutes=10),
) -> RunBudget:
    return RunBudget(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        max_steps=max_steps,
        max_total_tokens=max_total_tokens,
        max_cost_micro_usd=max_cost_micro_usd,
        deadline=deadline,
    )


def policy() -> ToolL2RuntimePolicy:
    return ToolL2RuntimePolicy(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        profile_version="tool-l2-v1",
        prompt_version="tool-l2-prompt-v1",
        context_compiler_version="context-v1",
        output_contract_version="final-markdown-v1",
        toolset_version="fake-industry-toolset-v1",
        model="openai-compatible/fake-model",
        max_input_tokens=2_048,
        max_decision_output_tokens=256,
        max_tool_calls=2,
        system_instructions="Use the configured Tool only when another result is required.",
        available_tools=(ToolReference(FAKE_LOOKUP_TOOL_NAME, FAKE_LOOKUP_TOOL_VERSION),),
    )


def command(*, selected_budget: RunBudget | None = None) -> ToolL2RunCommand:
    run_budget = selected_budget or budget()
    run = AgentRun(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        run_id=RUN_ID,
        event_stream_id=STREAM_ID,
        workspace_id=WORKSPACE_ID,
        user_id=USER_ID,
        run_type=AgentRunType.TOOL_LOOP,
        runtime_version=TOOL_L2_RUNTIME_VERSION,
        harness_version="harness-v1",
        budget=run_budget,
        trace_id=TraceId("trace-tool-l2"),
        status=AgentRunStatus.QUEUED,
        state_revision=0,
        created_at=NOW,
        started_at=None,
        terminal_at=None,
        stop_reason=None,
    )
    return ToolL2RunCommand(
        run=run,
        state=RunState(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            run_id=RUN_ID,
            workspace_id=WORKSPACE_ID,
            revision=0,
            status=AgentRunStatus.QUEUED,
            step_count=0,
            event_count=1,
            input_tokens_used=0,
            output_tokens_used=0,
            cost_micro_usd=0,
            updated_at=NOW,
        ),
        policy=policy(),
        decision_model_step_ids=tuple(stable_id(f"decision-step-{index}") for index in range(3)),
        tool_step_ids=tuple(stable_id(f"tool-step-{index}") for index in range(2)),
        decision_manifest_ids=tuple(stable_id(f"manifest-{index}") for index in range(3)),
        tool_call_ids=tuple(stable_id(f"call-{index}") for index in range(2)),
        approval_request_ids=tuple(stable_id(f"approval-{index}") for index in range(2)),
        final_step_id=stable_id("final-step"),
        user_question="Compare steel and copper market changes.",
        side_effect_idempotency_keys=(None, None),
    )


def runtime_context(selected_budget: RunBudget) -> TrustedRuntimeContext:
    return TrustedRuntimeContext(
        principal=AuthenticatedPrincipal(
            user_id=USER_ID,
            session_id=SESSION_ID,
            email=NormalizedEmail("tool-l2@example.test"),
            workspaces=(
                AuthenticatedWorkspace(
                    workspace_id=WORKSPACE_ID,
                    name="Tool L2 Workspace",
                    role="member",
                ),
            ),
        ),
        workspace_scope=WorkspaceScope(
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            role="member",
        ),
        capabilities=frozenset({WorkspaceAction.VIEW, WorkspaceAction.RUN_TOOL}),
        budget=selected_budget,
        secret_references=("provider/tool-l2-key",),
    )


def build_runtime(
    provider: ModelProvider,
    *,
    records: dict[str, FakeLookupRecord] | None = None,
    cancellation_probe: CancellationProbe | None = None,
    committer: RecordingCommitter | None = None,
    clock: Callable[[], datetime] | None = None,
    fail_second_tool: bool = False,
    completion_signal: ToolCompletionSignal | None = None,
) -> tuple[
    UnifiedAgentRuntime,
    FakeIndustryLookupTool,
    RecordingManifestStore,
    RecordingCommitter,
]:
    selected_clock = clock or IncrementingClock()
    tool = FakeIndustryLookupTool(
        records
        or {
            "steel": FakeLookupRecord(
                text="Steel demand rose 3%.",
                locator="fixture://industry/steel/2026-08",
                source_version="fixture-2026-08-v1",
            ),
            "copper": FakeLookupRecord(
                text="Copper inventories fell 2%.",
                locator="fixture://industry/copper/2026-08",
                source_version="fixture-2026-08-v1",
            ),
        }
    )
    registry = ToolRegistry((tool,))
    manifests = RecordingManifestStore()
    selected_committer = committer or RecordingCommitter()
    cancellation = cancellation_probe or NeverCancelled()
    registry_executor = RegistryToolExecutor(registry, clock=selected_clock)
    executor: ToolExecutor[ToolCall, TrustedRuntimeContext, ToolExecutionResult] = (
        FailSecondToolExecutor(registry_executor) if fail_second_tool else registry_executor
    )
    if completion_signal is not None:
        executor = SignalAfterToolExecutor(executor, completion_signal)
    l2_runtime = ToolL2Runtime(
        context_compiler=ContextCompilerV1(token_counter=FixedTokenCounter()),
        context_manifest_store=manifests,
        model_provider=provider,
        tool_registry=registry,
        tool_executor=executor,
        event_committer=selected_committer,
        cancellation_probe=cancellation,
        clock=selected_clock,
    )
    direct_runtime = DirectAnswerRuntime(
        context_compiler=ContextCompilerV0(token_counter=FixedTokenCounter()),
        context_manifest_store=manifests,
        model_provider=provider,
        event_committer=selected_committer,
        cancellation_probe=cancellation,
        clock=selected_clock,
    )
    return (
        UnifiedAgentRuntime(
            direct_answer_runtime=direct_runtime,
            tool_l2_runtime=l2_runtime,
        ),
        tool,
        manifests,
        selected_committer,
    )


async def execute(
    runtime: UnifiedAgentRuntime,
    selected_budget: RunBudget,
) -> list[AgentEvent]:
    return [
        event
        async for event in runtime.run(
            command(selected_budget=selected_budget),
            runtime_context(selected_budget),
        )
    ]


def assert_terminal(
    events: list[AgentEvent],
    *,
    event_type: AgentEventType,
    reason: RunStopReason,
) -> None:
    terminals = [
        event
        for event in events
        if event.event_type
        in {
            AgentEventType.RUN_COMPLETED,
            AgentEventType.RUN_FAILED,
            AgentEventType.RUN_CANCELLED,
        }
    ]
    assert terminals == [events[-1]]
    assert events[-1].event_type is event_type
    assert events[-1].payload["stop_reason"] == reason.value


def test_l2_decision_schema_and_decoder_accept_only_one_strict_branch() -> None:
    schema = tool_loop_decision_response_schema(fake_lookup_definition())
    action = action_decision("steel")
    final = final_decision()

    validate_supported_schema(schema)
    validate_structured_output(action, schema)
    validate_structured_output(final, schema)
    assert isinstance(decode_tool_loop_decision(action), ToolAction)
    assert isinstance(decode_tool_loop_decision(final), ToolLoopFinalDecision)

    with pytest.raises(InvalidProviderResponse):
        validate_structured_output(
            '{"decision":{"schema_version":1,"kind":"final",'
            '"content_markdown":"done","arguments":{}}}',
            schema,
        )
    with pytest.raises(ValueError, match="decision"):
        decode_tool_loop_decision('{"decision":{"schema_version":1,"kind":"unknown"}}')


@pytest.mark.asyncio
async def test_l2_completes_two_tool_rounds_in_the_unified_runtime() -> None:
    provider = QueueModelProvider(
        (
            model_response(action_decision("steel"), request_id="decision-1"),
            model_response(action_decision("copper"), request_id="decision-2"),
            model_response(final_decision(), request_id="decision-3"),
        )
    )
    selected_budget = budget()
    runtime, tool, manifests, committer = build_runtime(provider)

    events = await execute(runtime, selected_budget)

    assert events == committer.events
    assert_terminal(events, event_type=AgentEventType.RUN_COMPLETED, reason=RunStopReason.FINAL)
    assert [value.query for value in tool.invocations] == ["steel", "copper"]
    assert provider.remaining == 0
    assert len(provider.requests) == 3
    assert [len(manifest.sources) for manifest in manifests.manifests] == [5, 6, 7]
    assert [event.event_type for event in events].count(AgentEventType.TOOL_COMPLETED) == 2
    assert events[-1].payload["state_revision"] == 12
    for request in provider.requests:
        assert request.response_schema is not None
        validate_supported_schema(request.response_schema)
        assert '"input_schema_version":' in request.messages[0].content
        assert '"input_schema":' not in request.messages[0].content
    assert "provider/tool-l2-key" not in repr(events)
    assert "provider/tool-l2-key" not in repr(provider.requests)


@pytest.mark.asyncio
async def test_l2_selects_two_different_tools_from_one_exact_allowlist() -> None:
    provider = QueueModelProvider(
        (
            model_response(action_decision("steel"), request_id="multi-decision-1"),
            model_response(
                action_decision(
                    "revenue",
                    name=FAKE_DATABASE_TOOL_NAME,
                    version=FAKE_DATABASE_TOOL_VERSION,
                ),
                request_id="multi-decision-2",
            ),
            model_response(final_decision(), request_id="multi-decision-3"),
        )
    )
    industry_tool = FakeIndustryLookupTool(
        {
            "steel": FakeLookupRecord(
                text="Steel demand rose 3%.",
                locator="fixture://industry/steel/2026-08",
                source_version="fixture-2026-08-v1",
            )
        }
    )
    database_tool = FakeDatabaseLookupTool(
        {
            "revenue": FakeLookupRecord(
                text='{"artifact_id":"table-1","row_count":4}',
                locator="fixture://database/artifacts/table-1",
                source_version="fixture-database-v1",
            )
        }
    )
    registry = ToolRegistry((industry_tool, database_tool))
    manifests = RecordingManifestStore()
    committer = RecordingCommitter()
    selected_clock = IncrementingClock()
    runtime = UnifiedAgentRuntime(
        direct_answer_runtime=DirectAnswerRuntime(
            context_compiler=ContextCompilerV0(token_counter=FixedTokenCounter()),
            context_manifest_store=manifests,
            model_provider=provider,
            event_committer=committer,
            cancellation_probe=NeverCancelled(),
            clock=selected_clock,
        ),
        tool_l2_runtime=ToolL2Runtime(
            context_compiler=ContextCompilerV1(token_counter=FixedTokenCounter()),
            context_manifest_store=manifests,
            model_provider=provider,
            tool_registry=registry,
            tool_executor=RegistryToolExecutor(registry, clock=selected_clock),
            event_committer=committer,
            cancellation_probe=NeverCancelled(),
            clock=selected_clock,
        ),
    )
    selected_budget = budget()
    selected_policy = replace(
        policy(),
        toolset_version="fake-multitool-v1",
        available_tools=(industry_tool.definition.reference, database_tool.definition.reference),
    )
    selected_command = replace(
        command(selected_budget=selected_budget),
        policy=selected_policy,
    )

    events = [
        event
        async for event in runtime.run(
            selected_command,
            runtime_context(selected_budget),
        )
    ]

    assert_terminal(events, event_type=AgentEventType.RUN_COMPLETED, reason=RunStopReason.FINAL)
    assert [item.query for item in industry_tool.invocations] == ["steel"]
    assert [item.query for item in database_tool.invocations] == ["revenue"]
    assert [
        event.payload["requested_tool_name"]
        for event in events
        if event.event_type is AgentEventType.TOOL_REQUESTED
    ] == [FAKE_LOOKUP_TOOL_NAME, FAKE_DATABASE_TOOL_NAME]
    assert all(request.response_schema is not None for request in provider.requests)


@pytest.mark.asyncio
async def test_l2_stops_repeated_action_before_a_second_execution() -> None:
    provider = QueueModelProvider(
        (
            model_response(action_decision("steel"), request_id="decision-1"),
            model_response(action_decision("steel"), request_id="decision-2"),
        )
    )
    selected_budget = budget()
    runtime, tool, _manifests, _committer = build_runtime(provider)

    events = await execute(runtime, selected_budget)

    assert_terminal(events, event_type=AgentEventType.RUN_FAILED, reason=RunStopReason.NO_PROGRESS)
    assert [value.query for value in tool.invocations] == ["steel"]
    assert [event.event_type for event in events].count(AgentEventType.TOOL_REQUESTED) == 2
    assert [event.event_type for event in events].count(AgentEventType.TOOL_STARTED) == 1
    assert events[-1].payload["loop_guard"] == "tool_action_repeated"


@pytest.mark.asyncio
async def test_l2_stops_after_a_duplicate_normalized_observation() -> None:
    duplicate_records = {
        "steel": FakeLookupRecord(
            text="Same bounded result.",
            locator="fixture://industry/steel/2026-08",
            source_version="fixture-2026-08-v1",
        ),
        "alias": FakeLookupRecord(
            text="Same bounded result.",
            locator="fixture://industry/alias/2026-08",
            source_version="fixture-2026-08-v1",
        ),
    }
    provider = QueueModelProvider(
        (
            model_response(action_decision("steel"), request_id="decision-1"),
            model_response(action_decision("alias"), request_id="decision-2"),
        )
    )
    selected_budget = budget()
    runtime, tool, _manifests, committer = build_runtime(provider, records=duplicate_records)

    events = await execute(runtime, selected_budget)

    assert_terminal(events, event_type=AgentEventType.RUN_FAILED, reason=RunStopReason.NO_PROGRESS)
    assert [value.query for value in tool.invocations] == ["steel", "alias"]
    assert events[-1].payload["loop_guard"] == "duplicate_observation"
    assert tuple(event.event_type for event in committer.batches[-1]) == (
        AgentEventType.TOOL_COMPLETED,
        AgentEventType.STEP_COMPLETED,
        AgentEventType.RUN_FAILED,
    )


@pytest.mark.asyncio
async def test_l2_reserves_room_for_a_future_decision_and_final_step() -> None:
    provider = QueueModelProvider(
        (
            model_response(action_decision("steel"), request_id="decision-1"),
            model_response(action_decision("copper"), request_id="decision-2"),
        )
    )
    selected_budget = budget(max_steps=5)
    runtime, tool, _manifests, _committer = build_runtime(provider)

    events = await execute(runtime, selected_budget)

    assert_terminal(events, event_type=AgentEventType.RUN_FAILED, reason=RunStopReason.MAX_STEPS)
    assert [value.query for value in tool.invocations] == ["steel"]
    assert events[-1].payload["max_steps_preflight_rejected"] is True
    assert events[-1].payload["loop_guard"] == "run_step_budget"


@pytest.mark.asyncio
async def test_l2_accumulates_token_and_cost_budgets_across_rounds() -> None:
    token_provider = QueueModelProvider(
        (
            model_response(action_decision("steel"), request_id="token-1"),
            model_response(
                action_decision("copper"),
                request_id="token-2",
                input_tokens=843,
                output_tokens=42,
            ),
        )
    )
    token_budget = budget(max_total_tokens=900)
    token_runtime, token_tool, _manifests, _committer = build_runtime(token_provider)
    token_events = await execute(token_runtime, token_budget)
    assert_terminal(
        token_events,
        event_type=AgentEventType.RUN_FAILED,
        reason=RunStopReason.TOKEN_BUDGET_EXCEEDED,
    )
    assert [value.query for value in token_tool.invocations] == ["steel"]

    cost_provider = QueueModelProvider(
        (
            model_response(
                action_decision("steel"),
                request_id="cost-1",
                cost_micro_usd=1,
            ),
            model_response(
                action_decision("copper"),
                request_id="cost-2",
                cost_micro_usd=1_001,
            ),
        )
    )
    cost_budget = budget(max_cost_micro_usd=1_002)
    cost_runtime, cost_tool, _manifests, _committer = build_runtime(cost_provider)
    cost_events = await execute(cost_runtime, cost_budget)
    assert_terminal(
        cost_events,
        event_type=AgentEventType.RUN_FAILED,
        reason=RunStopReason.COST_BUDGET_EXCEEDED,
    )
    assert [value.query for value in cost_tool.invocations] == ["steel"]


@pytest.mark.asyncio
async def test_l2_honors_cancellation_between_tool_rounds() -> None:
    provider = QueueModelProvider(
        (
            model_response(action_decision("steel"), request_id="decision-1"),
            model_response(final_decision(), request_id="must-not-run"),
        )
    )
    selected_budget = budget()
    committer = RecordingCommitter()
    runtime, tool, _manifests, _committer = build_runtime(
        provider,
        committer=committer,
        cancellation_probe=CancelAfterFirstTool(committer),
    )

    events = await execute(runtime, selected_budget)

    assert_terminal(
        events,
        event_type=AgentEventType.RUN_CANCELLED,
        reason=RunStopReason.CANCELLED,
    )
    assert [value.query for value in tool.invocations] == ["steel"]
    assert provider.remaining == 1


@pytest.mark.asyncio
async def test_l2_honors_deadline_after_an_authoritative_tool_result() -> None:
    provider = QueueModelProvider(
        (
            model_response(action_decision("steel"), request_id="decision-1"),
            model_response(final_decision(), request_id="must-not-run"),
        )
    )
    deadline = NOW + timedelta(seconds=30)
    selected_budget = budget(deadline=deadline)
    committer = RecordingCommitter()
    completion_signal = ToolCompletionSignal()
    runtime, tool, _manifests, _committer = build_runtime(
        provider,
        committer=committer,
        clock=DeadlineAfterToolCompletionClock(completion_signal, deadline),
        completion_signal=completion_signal,
    )

    events = await execute(runtime, selected_budget)

    assert_terminal(
        events,
        event_type=AgentEventType.RUN_FAILED,
        reason=RunStopReason.DEADLINE_EXCEEDED,
    )
    assert [value.query for value in tool.invocations] == ["steel"]
    assert [event.event_type for event in events].count(AgentEventType.TOOL_COMPLETED) == 1
    assert provider.remaining == 1


@pytest.mark.asyncio
async def test_l2_second_tool_timeout_is_a_stable_tool_error() -> None:
    provider = QueueModelProvider(
        (
            model_response(action_decision("steel"), request_id="decision-1"),
            model_response(action_decision("copper"), request_id="decision-2"),
        )
    )
    selected_budget = budget()
    runtime, tool, _manifests, _committer = build_runtime(
        provider,
        fail_second_tool=True,
    )

    events = await execute(runtime, selected_budget)

    assert_terminal(events, event_type=AgentEventType.RUN_FAILED, reason=RunStopReason.TOOL_ERROR)
    assert [value.query for value in tool.invocations] == ["steel"]
    failed = next(event for event in events if event.event_type is AgentEventType.TOOL_FAILED)
    assert failed.payload["error_code"] == "tool_timeout"


@pytest.mark.asyncio
async def test_l2_second_tool_failure_has_one_stable_terminal() -> None:
    provider = QueueModelProvider(
        (
            model_response(action_decision("steel"), request_id="decision-1"),
            model_response(action_decision("missing"), request_id="decision-2"),
        )
    )
    selected_budget = budget()
    runtime, tool, _manifests, _committer = build_runtime(provider)

    events = await execute(runtime, selected_budget)

    assert_terminal(events, event_type=AgentEventType.RUN_FAILED, reason=RunStopReason.TOOL_ERROR)
    assert [value.query for value in tool.invocations] == ["steel", "missing"]
    failed = next(event for event in events if event.event_type is AgentEventType.TOOL_FAILED)
    assert failed.payload["error_code"] == "tool_fixture_not_found"


def test_l2_command_rejects_an_unbounded_or_reused_identity_pool() -> None:
    with pytest.raises(ValueError, match="length"):
        replace(command(), tool_call_ids=(stable_id("only-one"),))
    with pytest.raises(ValueError, match="distinct"):
        replace(
            command(),
            decision_model_step_ids=(
                stable_id("same"),
                stable_id("same"),
                stable_id("different"),
            ),
        )


@dataclass(frozen=True)
class HarnessCaseFixture:
    responses: tuple[ModelResponse, ...]
    expected_tool_invocations: int
    expected_remaining_responses: int = 0
    records: dict[str, FakeLookupRecord] | None = None
    cancel_after_first_tool: bool = False
    deadline_after_first_tool: bool = False
    fail_second_tool: bool = False


@pytest.mark.asyncio
async def test_all_versioned_l2_fault_scenarios_run_through_the_unified_runtime() -> None:
    dataset = load_scenario_dataset(DATASET_PATH)
    fixtures = {
        "day3-l2-two-round-final": HarnessCaseFixture(
            responses=(
                model_response(action_decision("steel"), request_id="harness-success-1"),
                model_response(action_decision("copper"), request_id="harness-success-2"),
                model_response(final_decision(), request_id="harness-success-3"),
            ),
            expected_tool_invocations=2,
        ),
        "day3-l2-repeated-action": HarnessCaseFixture(
            responses=(
                model_response(action_decision("steel"), request_id="harness-repeat-1"),
                model_response(action_decision("steel"), request_id="harness-repeat-2"),
            ),
            expected_tool_invocations=1,
        ),
        "day3-l2-duplicate-observation": HarnessCaseFixture(
            responses=(
                model_response(action_decision("steel"), request_id="harness-duplicate-1"),
                model_response(action_decision("alias"), request_id="harness-duplicate-2"),
            ),
            expected_tool_invocations=2,
            records={
                "steel": FakeLookupRecord(
                    text="Same bounded result.",
                    locator="fixture://industry/steel/2026-08",
                    source_version="fixture-2026-08-v1",
                ),
                "alias": FakeLookupRecord(
                    text="Same bounded result.",
                    locator="fixture://industry/alias/2026-08",
                    source_version="fixture-2026-08-v1",
                ),
            },
        ),
        "day3-l2-max-steps": HarnessCaseFixture(
            responses=(
                model_response(action_decision("steel"), request_id="harness-max-1"),
                model_response(action_decision("copper"), request_id="harness-max-2"),
            ),
            expected_tool_invocations=1,
        ),
        "day3-l2-token-budget": HarnessCaseFixture(
            responses=(
                model_response(action_decision("steel"), request_id="harness-token-1"),
                model_response(
                    action_decision("copper"),
                    request_id="harness-token-2",
                    input_tokens=843,
                    output_tokens=42,
                ),
            ),
            expected_tool_invocations=1,
        ),
        "day3-l2-cost-budget": HarnessCaseFixture(
            responses=(
                model_response(
                    action_decision("steel"),
                    request_id="harness-cost-1",
                    cost_micro_usd=1,
                ),
                model_response(
                    action_decision("copper"),
                    request_id="harness-cost-2",
                    cost_micro_usd=1_001,
                ),
            ),
            expected_tool_invocations=1,
        ),
        "day3-l2-cancelled": HarnessCaseFixture(
            responses=(
                model_response(action_decision("steel"), request_id="harness-cancel-1"),
                model_response(final_decision(), request_id="harness-cancel-unused"),
            ),
            expected_tool_invocations=1,
            expected_remaining_responses=1,
            cancel_after_first_tool=True,
        ),
        "day3-l2-deadline": HarnessCaseFixture(
            responses=(
                model_response(action_decision("steel"), request_id="harness-deadline-1"),
                model_response(final_decision(), request_id="harness-deadline-unused"),
            ),
            expected_tool_invocations=1,
            expected_remaining_responses=1,
            deadline_after_first_tool=True,
        ),
        "day3-l2-tool-timeout": HarnessCaseFixture(
            responses=(
                model_response(action_decision("steel"), request_id="harness-timeout-1"),
                model_response(action_decision("copper"), request_id="harness-timeout-2"),
            ),
            expected_tool_invocations=1,
            fail_second_tool=True,
        ),
        "day3-l2-tool-failure": HarnessCaseFixture(
            responses=(
                model_response(action_decision("steel"), request_id="harness-failure-1"),
                model_response(action_decision("missing"), request_id="harness-failure-2"),
            ),
            expected_tool_invocations=2,
        ),
    }
    assert {case.case_id for case in dataset.cases} == set(fixtures)

    profile = ToolL2Profile(
        schema_version=1,
        profile_name="tool-l2",
        profile_version="v1",
        prompt_version="tool-l2-prompt-v1",
        context_compiler_version="context-v1",
        output_contract_version="final-markdown-v1",
        toolset_version="fake-industry-toolset-v1",
        model="openai-compatible/fake-model",
        max_input_tokens=2_048,
        max_decision_output_tokens=256,
        max_tool_calls=2,
        system_instructions="Use the configured Tool only when another result is required.",
        available_tools=(ToolReference(FAKE_LOOKUP_TOOL_NAME, FAKE_LOOKUP_TOOL_VERSION),),
    )
    principal = runtime_context(budget()).principal
    assert isinstance(principal, AuthenticatedPrincipal)
    executed: set[str] = set()

    for selected in dataset.cases:
        fixture = fixtures[selected.case_id]
        provider = QueueModelProvider(fixture.responses)
        committer = RecordingCommitter()
        cancellation: CancellationProbe = (
            CancelAfterFirstTool(committer) if fixture.cancel_after_first_tool else NeverCancelled()
        )
        completion_signal = ToolCompletionSignal()
        deadline = NOW + timedelta(seconds=selected.scenario.budget.timeout_seconds)
        selected_clock: Callable[[], datetime] = (
            DeadlineAfterToolCompletionClock(completion_signal, deadline)
            if fixture.deadline_after_first_tool
            else IncrementingClock()
        )
        runtime, tool, _manifests, _committer = build_runtime(
            provider,
            records=fixture.records,
            cancellation_probe=cancellation,
            committer=committer,
            clock=selected_clock,
            fail_second_tool=fixture.fail_second_tool,
            completion_signal=(completion_signal if fixture.deadline_after_first_tool else None),
        )
        prefix = selected.case_id
        materializer = ToolL2ScenarioMaterializer(
            profile=profile,
            execution=ToolL2HarnessExecutionIdentity(
                run_id=stable_id(f"{prefix}-run"),
                stream_id=stable_id(f"{prefix}-stream"),
                decision_model_step_ids=tuple(
                    stable_id(f"{prefix}-decision-{index}") for index in range(3)
                ),
                tool_step_ids=tuple(stable_id(f"{prefix}-tool-{index}") for index in range(2)),
                decision_manifest_ids=tuple(
                    stable_id(f"{prefix}-manifest-{index}") for index in range(3)
                ),
                tool_call_ids=tuple(stable_id(f"{prefix}-call-{index}") for index in range(2)),
                approval_request_ids=tuple(
                    stable_id(f"{prefix}-approval-{index}") for index in range(2)
                ),
                final_step_id=stable_id(f"{prefix}-final"),
                trace_id=TraceId(f"trace-{prefix}"),
                created_at=NOW,
            ),
            identity=HarnessTrustedIdentity(
                principal=principal,
                workspace_scope=WorkspaceScope(
                    workspace_id=WORKSPACE_ID,
                    user_id=USER_ID,
                    role="member",
                ),
                capabilities=frozenset({WorkspaceAction.VIEW, WorkspaceAction.RUN_TOOL}),
                secret_references=("provider/tool-l2-key",),
            ),
            model_version="fake-model-v1",
            harness_version="harness-v1",
        )

        result = await HarnessRunner(runtime=runtime, materializer=materializer).run_case(selected)

        assert isinstance(runtime, UnifiedAgentRuntime)
        assert result.events[-1].payload["stop_reason"] == selected.expected_stop_reason.value
        assert len(tool.invocations) == fixture.expected_tool_invocations
        assert provider.remaining == fixture.expected_remaining_responses
        executed.add(result.case_id)

    assert executed == set(fixtures)
