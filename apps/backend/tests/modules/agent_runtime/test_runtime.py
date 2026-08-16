"""End-to-end component tests for the single concrete Direct Answer Runtime."""

import asyncio
from collections.abc import AsyncGenerator, Callable
from dataclasses import FrozenInstanceError, dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from industry_platform.modules.agent_runtime.context import ContextManifest, TrustedRuntimeContext
from industry_platform.modules.agent_runtime.context_compiler import ContextCompilerV0
from industry_platform.modules.agent_runtime.domain import (
    AGENT_RUNTIME_SCHEMA_VERSION,
    AgentRun,
    AgentRunStatus,
    AgentRunType,
    RunBudget,
    RunStopReason,
)
from industry_platform.modules.agent_runtime.events import (
    TERMINAL_AGENT_EVENT_TYPES,
    AgentEvent,
    AgentEventType,
)
from industry_platform.modules.agent_runtime.final_output import (
    FINAL_MARKDOWN_CONTRACT_VERSION,
    DirectAnswerFinalOutput,
)
from industry_platform.modules.agent_runtime.model import (
    ModelFinishReason,
    ModelRequest,
    ModelResponse,
    ModelStreamCompleted,
    ModelStreamDelta,
    ModelStreamItem,
    ModelUsage,
)
from industry_platform.modules.agent_runtime.ports import ContextManifestStoreError
from industry_platform.modules.agent_runtime.provider_errors import (
    ModelProviderError,
    ModelProviderErrorCode,
)
from industry_platform.modules.agent_runtime.runtime import DirectAnswerRuntime
from industry_platform.modules.agent_runtime.runtime_contracts import (
    DIRECT_ANSWER_RUNTIME_VERSION,
    DirectAnswerRunCommand,
    DirectAnswerRuntimePolicy,
)
from industry_platform.modules.agent_runtime.state import RunState
from industry_platform.modules.identity.domain import (
    AuthenticatedPrincipal,
    AuthenticatedWorkspace,
    NormalizedEmail,
    TraceId,
)
from industry_platform.modules.workspaces.domain import WorkspaceAction, WorkspaceScope

RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
STREAM_ID = UUID("22222222-2222-4222-8222-222222222222")
WORKSPACE_ID = UUID("33333333-3333-4333-8333-333333333333")
USER_ID = UUID("44444444-4444-4444-8444-444444444444")
SESSION_ID = UUID("55555555-5555-4555-8555-555555555555")
MODEL_STEP_ID = UUID("66666666-6666-4666-8666-666666666666")
FINAL_STEP_ID = UUID("77777777-7777-4777-8777-777777777777")
MANIFEST_ID = UUID("88888888-8888-4888-8888-888888888888")
NOW = datetime(2026, 8, 13, 13, 0, tzinfo=UTC)


class WordTokenCounter:
    version = "word-counter-v1"

    def count(self, *, model: str, messages: tuple[object, ...]) -> int:
        del model
        return 2 + len(messages) * 5


class RecordingManifestStore:
    def __init__(self, timeline: list[str], *, fail: bool = False) -> None:
        self.timeline = timeline
        self.fail = fail
        self.manifests: list[ContextManifest] = []

    async def save(self, manifest: ContextManifest) -> None:
        self.timeline.append("manifest")
        if self.fail:
            raise ContextManifestStoreError("simulated persistence boundary failure")
        self.manifests.append(manifest)


class RecordingEventCommitter:
    def __init__(self) -> None:
        self.events: list[AgentEvent] = []

    async def append(self, event: AgentEvent) -> None:
        if self.events and event.sequence != self.events[-1].sequence + 1:
            raise AssertionError("test committer received a sequence gap")
        self.events.append(event)


class ScriptedCancellationProbe:
    def __init__(self, decisions: tuple[bool, ...] = ()) -> None:
        self._decisions = decisions
        self.calls = 0

    async def is_cancel_requested(self, *, run_id: UUID, workspace_id: UUID) -> bool:
        assert run_id == RUN_ID
        assert workspace_id == WORKSPACE_ID
        index = self.calls
        self.calls += 1
        return self._decisions[index] if index < len(self._decisions) else False


class ScriptedProvider:
    def __init__(
        self,
        timeline: list[str],
        *,
        items: tuple[ModelStreamItem, ...] = (),
        error: ModelProviderError | None = None,
    ) -> None:
        self.timeline = timeline
        self.items = items
        self.error = error
        self.requests: list[ModelRequest] = []
        self.closed = False

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelStreamItem]:
        self.timeline.append("provider")
        self.requests.append(request)
        try:
            for item in self.items:
                yield item
            if self.error is not None:
                raise self.error
        finally:
            self.closed = True

    async def complete(self, request: ModelRequest) -> ModelResponse:
        raise AssertionError("Direct Answer Runtime must use the streaming Provider boundary")


class HungProvider(ScriptedProvider):
    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelStreamItem]:
        self.timeline.append("provider")
        self.requests.append(request)
        try:
            blocker = asyncio.Event()
            await blocker.wait()
            yield ModelStreamDelta(
                schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
                sequence=1,
                text="unreachable",
            )
        finally:
            self.closed = True


@dataclass
class IncrementingClock:
    value: datetime = NOW + timedelta(seconds=1)

    def __call__(self) -> datetime:
        current = self.value
        self.value += timedelta(milliseconds=10)
        return current


def budget(
    *,
    max_total_tokens: int = 512,
    max_cost: int = 10_000,
    deadline: datetime = NOW + timedelta(minutes=5),
) -> RunBudget:
    return RunBudget(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        max_steps=2,
        max_total_tokens=max_total_tokens,
        max_cost_micro_usd=max_cost,
        deadline=deadline,
    )


def policy() -> DirectAnswerRuntimePolicy:
    return DirectAnswerRuntimePolicy(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        profile_version="direct-answer-v0",
        prompt_version="direct-answer-prompt-v0",
        context_compiler_version="context-v0",
        output_contract_version="final-markdown-v1",
        model="openai-compatible/test-model",
        max_input_tokens=128,
        max_output_tokens=64,
        system_instructions="Answer the current question directly.",
    )


def command(*, selected_budget: RunBudget | None = None) -> DirectAnswerRunCommand:
    run_budget = selected_budget or budget()
    run = AgentRun(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        run_id=RUN_ID,
        event_stream_id=STREAM_ID,
        workspace_id=WORKSPACE_ID,
        user_id=USER_ID,
        run_type=AgentRunType.DIRECT_ANSWER,
        runtime_version=DIRECT_ANSWER_RUNTIME_VERSION,
        harness_version="direct-answer-v0",
        budget=run_budget,
        trace_id=TraceId("trace-runtime-v0"),
        status=AgentRunStatus.QUEUED,
        state_revision=0,
        created_at=NOW,
        started_at=None,
        terminal_at=None,
        stop_reason=None,
    )
    state = RunState(
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
    )
    return DirectAnswerRunCommand(
        run=run,
        state=state,
        policy=policy(),
        model_step_id=MODEL_STEP_ID,
        final_step_id=FINAL_STEP_ID,
        manifest_id=MANIFEST_ID,
        user_question="What does the Runtime do?",
    )


def runtime_context(*, selected_budget: RunBudget | None = None) -> TrustedRuntimeContext:
    run_budget = selected_budget or budget()
    return TrustedRuntimeContext(
        principal=AuthenticatedPrincipal(
            user_id=USER_ID,
            session_id=SESSION_ID,
            email=NormalizedEmail("runtime-test@example.com"),
            workspaces=(
                AuthenticatedWorkspace(
                    workspace_id=WORKSPACE_ID,
                    name="Runtime Test Workspace",
                    role="member",
                ),
            ),
        ),
        workspace_scope=WorkspaceScope(
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            role="member",
        ),
        capabilities=frozenset({WorkspaceAction.VIEW}),
        budget=run_budget,
        secret_references=("provider/runtime-test-key",),
    )


def successful_items(
    *,
    finish_reason: ModelFinishReason = ModelFinishReason.STOP,
    cost_micro_usd: int = 12,
) -> tuple[ModelStreamItem, ...]:
    response = ModelResponse(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        model="openai-compatible/test-model",
        finish_reason=finish_reason,
        usage=ModelUsage(
            input_tokens=20,
            output_tokens=5,
            cached_input_tokens=0,
            cost_micro_usd=cost_micro_usd,
            pricing_version="test-pricing-v1",
        ),
        output_text="A direct answer.",
        provider_request_id="request-runtime-1",
    )
    return (
        ModelStreamDelta(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            sequence=1,
            text="A direct ",
        ),
        ModelStreamDelta(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            sequence=2,
            text="answer.",
        ),
        ModelStreamCompleted(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            sequence=3,
            response=response,
        ),
    )


def final_output() -> DirectAnswerFinalOutput:
    terminal = successful_items()[-1]
    assert isinstance(terminal, ModelStreamCompleted)
    return DirectAnswerFinalOutput.from_response(
        contract_version=FINAL_MARKDOWN_CONTRACT_VERSION,
        run_id=RUN_ID,
        step_id=FINAL_STEP_ID,
        workspace_id=WORKSPACE_ID,
        response=terminal.response,
    )


def test_final_contract_is_markdown_not_an_artifact_and_hides_content() -> None:
    output = final_output()
    payload = output.to_event_payload()

    assert payload["format"] == "markdown"
    assert payload["stop_reason"] == "final"
    assert "input_tokens" not in payload
    assert "cost_micro_usd" not in payload
    assert "artifact" not in payload
    assert output.content_markdown not in repr(output)
    with pytest.raises(FrozenInstanceError):
        output.__setattr__("content_markdown", "changed")
    with pytest.raises(ValueError, match="contract version"):
        replace(output, contract_version="final-v2")
    with pytest.raises(ValueError, match="Markdown content"):
        replace(output, content_markdown="   ")


def build_runtime(
    provider: ScriptedProvider,
    timeline: list[str],
    *,
    cancellation: ScriptedCancellationProbe | None = None,
    manifest_failure: bool = False,
    clock: Callable[[], datetime] | None = None,
) -> tuple[DirectAnswerRuntime, RecordingManifestStore, RecordingEventCommitter]:
    manifests = RecordingManifestStore(timeline, fail=manifest_failure)
    committer = RecordingEventCommitter()
    return (
        DirectAnswerRuntime(
            context_compiler=ContextCompilerV0(token_counter=WordTokenCounter()),
            context_manifest_store=manifests,
            model_provider=provider,
            event_committer=committer,
            cancellation_probe=cancellation or ScriptedCancellationProbe(),
            clock=clock or IncrementingClock(),
        ),
        manifests,
        committer,
    )


@pytest.mark.asyncio
async def test_success_uses_one_provider_call_and_commits_before_yielding() -> None:
    timeline: list[str] = []
    provider = ScriptedProvider(timeline, items=successful_items())
    runtime, manifests, committer = build_runtime(provider, timeline)

    events = [item async for item in runtime.run(command(), runtime_context())]
    types = [event.event_type for event in events]

    assert timeline == ["manifest", "provider"]
    assert len(provider.requests) == 1
    assert len(manifests.manifests) == 1
    manifest = manifests.manifests[0]
    assert (
        sum(source.estimated_token_count for source in manifest.sources)
        == manifest.budget.estimated_input_tokens
    )
    assert events == committer.events
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert types == [
        AgentEventType.RUN_QUEUED,
        AgentEventType.RUN_STARTED,
        AgentEventType.STEP_STARTED,
        AgentEventType.MODEL_STARTED,
        AgentEventType.MODEL_DELTA,
        AgentEventType.MODEL_DELTA,
        AgentEventType.MODEL_COMPLETED,
        AgentEventType.STEP_COMPLETED,
        AgentEventType.STEP_STARTED,
        AgentEventType.STEP_COMPLETED,
        AgentEventType.RUN_COMPLETED,
    ]
    model_step_completed = events[7]
    assert model_step_completed.payload["cached_input_tokens"] == 0
    assert events[-2].payload["content_markdown"] == "A direct answer."
    assert events[-1].payload["stop_reason"] == RunStopReason.FINAL.value
    assert "provider/runtime-test-key" not in repr(events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "reason"),
    [
        (ModelProviderErrorCode.NOT_CONFIGURED, RunStopReason.PROVIDER_ERROR),
        (ModelProviderErrorCode.TIMEOUT, RunStopReason.PROVIDER_TIMEOUT),
        (ModelProviderErrorCode.RATE_LIMITED, RunStopReason.PROVIDER_RATE_LIMITED),
        (ModelProviderErrorCode.INVALID_RESPONSE, RunStopReason.INVALID_PROVIDER_RESPONSE),
        (
            ModelProviderErrorCode.INCOMPLETE_RESPONSE,
            RunStopReason.INCOMPLETE_PROVIDER_RESPONSE,
        ),
    ],
)
async def test_provider_failure_is_never_retried_or_disguised_as_success(
    code: ModelProviderErrorCode,
    reason: RunStopReason,
) -> None:
    timeline: list[str] = []
    provider = ScriptedProvider(timeline, error=ModelProviderError(code))
    runtime, _, _ = build_runtime(provider, timeline)

    events = [item async for item in runtime.run(command(), runtime_context())]

    assert len(provider.requests) == 1
    assert AgentEventType.MODEL_COMPLETED not in {event.event_type for event in events}
    assert events[-2].event_type is AgentEventType.STEP_FAILED
    assert events[-2].payload["error_code"] == code.value
    assert events[-1].event_type is AgentEventType.RUN_FAILED
    assert events[-1].payload["stop_reason"] == reason.value


@pytest.mark.asyncio
async def test_stream_ending_after_a_delta_is_an_incomplete_response() -> None:
    timeline: list[str] = []
    provider = ScriptedProvider(
        timeline,
        items=(
            ModelStreamDelta(
                schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
                sequence=1,
                text="unfinished",
            ),
        ),
    )
    runtime, _, _ = build_runtime(provider, timeline)

    events = [item async for item in runtime.run(command(), runtime_context())]

    assert events[-2].payload["error_code"] == "incomplete_provider_response"
    assert events[-1].payload["stop_reason"] == "incomplete_provider_response"


@pytest.mark.asyncio
async def test_explicit_cancel_before_start_never_calls_provider() -> None:
    timeline: list[str] = []
    provider = ScriptedProvider(timeline, items=successful_items())
    runtime, _, _ = build_runtime(
        provider,
        timeline,
        cancellation=ScriptedCancellationProbe((True,)),
    )

    events = [item async for item in runtime.run(command(), runtime_context())]

    assert provider.requests == []
    assert [event.event_type for event in events] == [
        AgentEventType.RUN_QUEUED,
        AgentEventType.RUN_CANCELLED,
    ]


@pytest.mark.asyncio
async def test_cancel_after_one_delta_closes_stream_and_keeps_one_terminal() -> None:
    timeline: list[str] = []
    provider = ScriptedProvider(timeline, items=successful_items())
    cancellation = ScriptedCancellationProbe((False, False, True))
    runtime, _, _ = build_runtime(provider, timeline, cancellation=cancellation)

    events = [item async for item in runtime.run(command(), runtime_context())]

    assert provider.closed is True
    assert [event.event_type for event in events].count(AgentEventType.MODEL_DELTA) == 1
    assert events[-1].payload["cancelled_step_status"] == "cancelled"
    assert events[-1].event_type is AgentEventType.RUN_CANCELLED
    assert AgentEventType.RUN_COMPLETED not in {event.event_type for event in events}


@pytest.mark.asyncio
async def test_cancel_before_first_delta_interrupts_and_closes_hung_provider() -> None:
    timeline: list[str] = []
    provider = HungProvider(timeline)
    cancellation = ScriptedCancellationProbe((False, False, True))
    runtime, _, _ = build_runtime(provider, timeline, cancellation=cancellation)

    async def collect_events() -> list[AgentEvent]:
        return [event async for event in runtime.run(command(), runtime_context())]

    events = await asyncio.wait_for(collect_events(), timeout=1)

    assert provider.closed is True
    assert events[-1].event_type is AgentEventType.RUN_CANCELLED
    assert sum(event.event_type in TERMINAL_AGENT_EVENT_TYPES for event in events) == 1


@pytest.mark.asyncio
async def test_deadline_interrupts_hung_provider_and_commits_one_failed_terminal() -> None:
    selected_budget = budget(deadline=NOW + timedelta(milliseconds=1_055))
    timeline: list[str] = []
    provider = HungProvider(timeline)
    runtime, _, _ = build_runtime(provider, timeline, clock=IncrementingClock())

    async def collect_events() -> list[AgentEvent]:
        return [
            event
            async for event in runtime.run(
                command(selected_budget=selected_budget),
                runtime_context(selected_budget=selected_budget),
            )
        ]

    events = await asyncio.wait_for(collect_events(), timeout=1)

    assert provider.closed is True
    assert events[-2].event_type is AgentEventType.STEP_FAILED
    assert events[-2].payload["error_code"] == RunStopReason.DEADLINE_EXCEEDED.value
    assert events[-1].event_type is AgentEventType.RUN_FAILED
    assert events[-1].payload["stop_reason"] == RunStopReason.DEADLINE_EXCEEDED.value
    assert sum(event.event_type in TERMINAL_AGENT_EVENT_TYPES for event in events) == 1


@pytest.mark.asyncio
async def test_length_finish_and_cost_overrun_do_not_create_final_output() -> None:
    timeline: list[str] = []
    length_provider = ScriptedProvider(
        timeline,
        items=successful_items(finish_reason=ModelFinishReason.LENGTH),
    )
    length_runtime, _, _ = build_runtime(length_provider, timeline)
    length_events = [item async for item in length_runtime.run(command(), runtime_context())]
    assert length_events[-1].payload["stop_reason"] == "incomplete_provider_response"
    assert "content_markdown" not in length_events[-2].payload

    tiny_budget = budget(max_cost=5)
    cost_provider = ScriptedProvider([], items=successful_items(cost_micro_usd=12))
    cost_runtime, _, _ = build_runtime(cost_provider, [])
    cost_events = [
        item
        async for item in cost_runtime.run(
            command(selected_budget=tiny_budget),
            runtime_context(selected_budget=tiny_budget),
        )
    ]
    assert cost_events[-1].payload["stop_reason"] == "cost_budget_exceeded"
    assert cost_events[-1].event_type is AgentEventType.RUN_FAILED


@pytest.mark.asyncio
async def test_context_budget_failure_happens_before_manifest_and_provider() -> None:
    selected_budget = budget(max_total_tokens=10)
    timeline: list[str] = []
    provider = ScriptedProvider(timeline, items=successful_items())
    runtime, manifests, _ = build_runtime(provider, timeline)

    events = [
        item
        async for item in runtime.run(
            command(selected_budget=selected_budget),
            runtime_context(selected_budget=selected_budget),
        )
    ]

    assert provider.requests == []
    assert manifests.manifests == []
    assert events[-1].event_type is AgentEventType.RUN_FAILED
    assert events[-1].payload["stop_reason"] == "token_budget_exceeded"


@pytest.mark.asyncio
async def test_expired_deadline_finishes_without_starting_provider() -> None:
    selected_budget = budget(deadline=NOW + timedelta(milliseconds=500))
    timeline: list[str] = []
    provider = ScriptedProvider(timeline, items=successful_items())
    runtime, _, _ = build_runtime(provider, timeline)

    events = [
        item
        async for item in runtime.run(
            command(selected_budget=selected_budget),
            runtime_context(selected_budget=selected_budget),
        )
    ]

    assert provider.requests == []
    assert [event.event_type for event in events] == [
        AgentEventType.RUN_QUEUED,
        AgentEventType.RUN_FAILED,
    ]
    assert events[-1].payload["stop_reason"] == "deadline_exceeded"


@pytest.mark.asyncio
async def test_deadline_crossed_during_context_preflight_never_calls_provider() -> None:
    selected_budget = budget(deadline=NOW + timedelta(milliseconds=1_025))
    timeline: list[str] = []
    provider = ScriptedProvider(timeline, items=successful_items())
    runtime, manifests, _ = build_runtime(provider, timeline)

    events = [
        item
        async for item in runtime.run(
            command(selected_budget=selected_budget),
            runtime_context(selected_budget=selected_budget),
        )
    ]

    assert provider.requests == []
    assert manifests.manifests == []
    assert events[-2].payload["error_code"] == "deadline_exceeded"
    assert events[-1].payload["stop_reason"] == "deadline_exceeded"


@pytest.mark.asyncio
async def test_manifest_failure_is_sanitized_and_provider_is_not_called() -> None:
    timeline: list[str] = []
    provider = ScriptedProvider(timeline, items=successful_items())
    runtime, _, _ = build_runtime(provider, timeline, manifest_failure=True)

    events = [item async for item in runtime.run(command(), runtime_context())]

    assert provider.requests == []
    assert events[-2].payload["error_code"] == "context_manifest_error"
    assert events[-1].payload["stop_reason"] == "runtime_error"
    assert "simulated persistence" not in repr(events)
