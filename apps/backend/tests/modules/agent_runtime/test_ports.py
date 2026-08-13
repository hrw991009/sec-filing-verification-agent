"""Structural and behavioral tests for Agent Runtime-owned Ports."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from industry_platform.modules.agent_runtime.checkpoints import (
    CheckpointEnvelope,
    LoadCheckpointRequest,
    SaveCheckpointCommand,
)
from industry_platform.modules.agent_runtime.context import (
    CompiledContext,
    ContextCompilationInput,
    ContextManifest,
)
from industry_platform.modules.agent_runtime.domain import (
    AGENT_RUNTIME_SCHEMA_VERSION,
    AgentRunStatus,
)
from industry_platform.modules.agent_runtime.events import AgentEvent, AgentEventType
from industry_platform.modules.agent_runtime.model import (
    ModelFinishReason,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelRole,
    ModelStreamCompleted,
    ModelStreamItem,
    ModelUsage,
)
from industry_platform.modules.agent_runtime.ports import (
    AgentRuntime,
    CheckpointStore,
    ContextCompiler,
    ContextManifestStore,
    ContextTokenCounter,
    ModelProvider,
    ToolExecutor,
    TrajectoryRecorder,
)
from industry_platform.modules.identity.domain import TraceId

RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
STEP_ID = UUID("22222222-2222-4222-8222-222222222222")
STREAM_ID = UUID("33333333-3333-4333-8333-333333333333")
WORKSPACE_ID = UUID("44444444-4444-4444-8444-444444444444")
TRACE_ID = TraceId("trace-day2-ports")
NOW = datetime(2026, 8, 13, 6, 0, tzinfo=UTC)


def model_request() -> ModelRequest:
    return ModelRequest(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        run_id=RUN_ID,
        step_id=STEP_ID,
        workspace_id=WORKSPACE_ID,
        model="openai-compatible/test-model",
        messages=(ModelMessage(role=ModelRole.USER, content="Hello"),),
        max_output_tokens=32,
        deadline=NOW + timedelta(seconds=10),
    )


def model_response() -> ModelResponse:
    return ModelResponse(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        model="openai-compatible/test-model",
        finish_reason=ModelFinishReason.STOP,
        usage=ModelUsage(
            input_tokens=1,
            output_tokens=1,
            cached_input_tokens=0,
            cost_micro_usd=1,
        ),
        output_text="Hello",
    )


def queued_event() -> AgentEvent:
    return AgentEvent(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        stream_id=STREAM_ID,
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        sequence=1,
        occurred_at=NOW,
        trace_id=TRACE_ID,
        event_type=AgentEventType.RUN_QUEUED,
    )


class RecordingModelProvider:
    def __init__(self, response: ModelResponse) -> None:
        self.response = response
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamItem]:
        self.requests.append(request)
        yield ModelStreamCompleted(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            sequence=1,
            response=self.response,
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return self.response


class RecordingTrajectoryRecorder:
    def __init__(self) -> None:
        self.events: list[AgentEvent] = []

    async def record(self, event: AgentEvent) -> None:
        self.events.append(event)


class UppercaseToolExecutor:
    async def execute(self, call: str, runtime_context: UUID) -> str:
        return f"{runtime_context}:{call.upper()}"


class RecordingRuntime:
    def __init__(self, event: AgentEvent) -> None:
        self.event = event
        self.calls: list[tuple[str, UUID]] = []

    async def run(self, command: str, runtime_context: UUID) -> AsyncIterator[AgentEvent]:
        self.calls.append((command, runtime_context))
        yield self.event


class FixedContextTokenCounter:
    version = "fixed-counter-v1"

    def count(self, *, model: str, messages: tuple[ModelMessage, ...]) -> int:
        del model
        return len(messages)


class ContextCompilerStub:
    def compile(self, compilation: ContextCompilationInput) -> CompiledContext:
        raise NotImplementedError


class ContextManifestStoreStub:
    async def save(self, manifest: ContextManifest) -> None:
        raise NotImplementedError


def accept_model_provider(provider: ModelProvider) -> ModelProvider:
    return provider


def accept_trajectory_recorder(recorder: TrajectoryRecorder) -> TrajectoryRecorder:
    return recorder


def accept_checkpoint_store(store: CheckpointStore) -> CheckpointStore:
    return store


def accept_tool_executor(
    executor: ToolExecutor[str, UUID, str],
) -> ToolExecutor[str, UUID, str]:
    return executor


def accept_runtime(
    runtime: AgentRuntime[str, UUID],
) -> AgentRuntime[str, UUID]:
    return runtime


def accept_context_token_counter(counter: ContextTokenCounter) -> ContextTokenCounter:
    return counter


def accept_context_compiler(compiler: ContextCompiler) -> ContextCompiler:
    return compiler


def accept_context_manifest_store(store: ContextManifestStore) -> ContextManifestStore:
    return store


@pytest.mark.asyncio
async def test_model_provider_stream_is_directly_async_iterable() -> None:
    fake = RecordingModelProvider(model_response())
    provider = accept_model_provider(fake)

    streamed = [item async for item in provider.stream(model_request())]
    completed = await provider.complete(model_request())

    assert len(streamed) == 1
    assert completed.output_text == "Hello"
    assert len(fake.requests) == 2


@pytest.mark.asyncio
async def test_recorder_tool_executor_and_runtime_are_structural_ports() -> None:
    recorder_fake = RecordingTrajectoryRecorder()
    recorder = accept_trajectory_recorder(recorder_fake)
    event = queued_event()
    await recorder.record(event)

    tool = accept_tool_executor(UppercaseToolExecutor())
    assert await tool.execute("inspect", WORKSPACE_ID) == f"{WORKSPACE_ID}:INSPECT"

    runtime_fake = RecordingRuntime(event)
    runtime = accept_runtime(runtime_fake)
    events = [item async for item in runtime.run("answer", WORKSPACE_ID)]

    assert recorder_fake.events == [event]
    assert events == [event]
    assert runtime_fake.calls == [("answer", WORKSPACE_ID)]


def test_checkpoint_store_port_remains_a_persistence_boundary() -> None:
    class StoreStub:
        async def save(self, command: SaveCheckpointCommand) -> CheckpointEnvelope:
            raise NotImplementedError

        async def load(self, request: LoadCheckpointRequest) -> CheckpointEnvelope:
            raise NotImplementedError

    assert accept_checkpoint_store(StoreStub()) is not None
    assert AgentRunStatus.QUEUED.value == "queued"


def test_context_ports_keep_counting_compilation_and_persistence_separate() -> None:
    counter = accept_context_token_counter(FixedContextTokenCounter())

    assert (
        counter.count(
            model="openai-compatible/test-model",
            messages=(ModelMessage(role=ModelRole.USER, content="hello"),),
        )
        == 1
    )
    assert accept_context_compiler(ContextCompilerStub()) is not None
    assert accept_context_manifest_store(ContextManifestStoreStub()) is not None
