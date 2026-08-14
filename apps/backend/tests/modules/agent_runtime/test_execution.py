"""Tests for the application boundary that invokes the unified Agent Runtime."""

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest

from industry_platform.modules.agent_runtime.context import TrustedRuntimeContext
from industry_platform.modules.agent_runtime.domain import (
    AGENT_RUNTIME_SCHEMA_VERSION,
    AgentRun,
    AgentRunStatus,
    AgentRunType,
    RunBudget,
    RunStopReason,
)
from industry_platform.modules.agent_runtime.events import AgentEvent, AgentEventType
from industry_platform.modules.agent_runtime.execution import (
    DirectAnswerExecutionInput,
    DirectAnswerRunExecutionService,
)
from industry_platform.modules.agent_runtime.runtime_contracts import DirectAnswerRunCommand
from industry_platform.modules.identity.domain import TraceId

RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
STREAM_ID = UUID("22222222-2222-4222-8222-222222222222")
WORKSPACE_ID = UUID("33333333-3333-4333-8333-333333333333")
USER_ID = UUID("44444444-4444-4444-8444-444444444444")
NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)


@dataclass(frozen=True)
class CommandStub:
    run: AgentRun


class RecordingLoader:
    def __init__(self, execution: DirectAnswerExecutionInput) -> None:
        self.execution = execution
        self.run_ids: list[UUID] = []

    async def load(self, run_id: UUID) -> DirectAnswerExecutionInput:
        self.run_ids.append(run_id)
        return self.execution


class RecordingRuntime:
    def __init__(self, events: tuple[AgentEvent, ...]) -> None:
        self.events = events
        self.calls: list[tuple[DirectAnswerRunCommand, TrustedRuntimeContext]] = []

    async def run(
        self,
        command: DirectAnswerRunCommand,
        runtime_context: TrustedRuntimeContext,
    ) -> AsyncGenerator[AgentEvent]:
        self.calls.append((command, runtime_context))
        for event in self.events:
            yield event


def queued_run() -> AgentRun:
    return AgentRun(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        run_id=RUN_ID,
        event_stream_id=STREAM_ID,
        workspace_id=WORKSPACE_ID,
        user_id=USER_ID,
        run_type=AgentRunType.DIRECT_ANSWER,
        runtime_version="direct-answer-runtime-v0",
        harness_version="harness-v0",
        budget=RunBudget(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            max_steps=2,
            max_total_tokens=512,
            max_cost_micro_usd=10_000,
            deadline=NOW + timedelta(minutes=5),
        ),
        trace_id=TraceId("execution-service-trace"),
        status=AgentRunStatus.QUEUED,
        state_revision=0,
        created_at=NOW,
        started_at=None,
        terminal_at=None,
        stop_reason=None,
    )


def event(sequence: int, event_type: AgentEventType) -> AgentEvent:
    return AgentEvent(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        stream_id=STREAM_ID,
        run_id=RUN_ID,
        workspace_id=WORKSPACE_ID,
        sequence=sequence,
        occurred_at=NOW + timedelta(seconds=sequence - 1),
        trace_id=TraceId("execution-service-trace"),
        event_type=event_type,
        payload=(
            {"stop_reason": RunStopReason.RUNTIME_ERROR.value}
            if event_type is AgentEventType.RUN_FAILED
            else {}
        ),
    )


def execution_input(run: AgentRun) -> DirectAnswerExecutionInput:
    return DirectAnswerExecutionInput(
        command=cast(DirectAnswerRunCommand, CommandStub(run)),
        runtime_context=cast(TrustedRuntimeContext, object()),
    )


@pytest.mark.asyncio
async def test_service_loads_once_and_consumes_the_single_runtime_to_terminal() -> None:
    loader = RecordingLoader(execution_input(queued_run()))
    runtime = RecordingRuntime(
        (
            event(1, AgentEventType.RUN_QUEUED),
            event(2, AgentEventType.RUN_FAILED),
        )
    )
    service = DirectAnswerRunExecutionService(loader=loader, runtime=runtime)

    result = await service.execute_run(RUN_ID)

    assert loader.run_ids == [RUN_ID]
    assert len(runtime.calls) == 1
    assert result.run_id == RUN_ID
    assert result.status is AgentRunStatus.FAILED
    assert result.stop_reason is RunStopReason.RUNTIME_ERROR
    assert result.terminal_event_sequence == 2


@pytest.mark.asyncio
async def test_service_rejects_a_runtime_that_ends_without_a_terminal_event() -> None:
    service = DirectAnswerRunExecutionService(
        loader=RecordingLoader(execution_input(queued_run())),
        runtime=RecordingRuntime((event(1, AgentEventType.RUN_QUEUED),)),
    )

    with pytest.raises(ValueError, match="without one terminal Event"):
        await service.execute_run(RUN_ID)
