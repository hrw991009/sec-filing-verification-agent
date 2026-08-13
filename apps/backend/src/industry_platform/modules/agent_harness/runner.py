"""Harness orchestration that delegates execution to the injected AgentRuntime."""

from dataclasses import dataclass, field
from typing import Protocol

from industry_platform.modules.agent_harness.scenarios import EvalCase, Scenario
from industry_platform.modules.agent_runtime.domain import RunStopReason
from industry_platform.modules.agent_runtime.events import (
    TERMINAL_AGENT_EVENT_TYPES,
    AgentEvent,
    AgentEventType,
)
from industry_platform.modules.agent_runtime.ports import AgentRuntime


class HarnessExecutionError(RuntimeError):
    """Raised when the unified Runtime violates its minimum execution contract."""


@dataclass(frozen=True, slots=True)
class MaterializedScenario[CommandT, RuntimeContextT]:
    """Trusted command and context produced outside the serialized Scenario."""

    command: CommandT = field(repr=False)
    runtime_context: RuntimeContextT = field(repr=False)


class ScenarioMaterializer[CommandT, RuntimeContextT](Protocol):
    """Build trusted Runtime input from references using the composition root."""

    def materialize(
        self,
        scenario: Scenario,
    ) -> MaterializedScenario[CommandT, RuntimeContextT]:
        """Resolve a Scenario without trusting it as authorization context."""

        ...


@dataclass(frozen=True, slots=True)
class HarnessRunResult:
    """Unscored formal Events returned by the same Runtime used in production."""

    case_id: str
    case_version: str
    events: tuple[AgentEvent, ...]


def _expected_terminal_event(stop_reason: RunStopReason) -> AgentEventType:
    if stop_reason is RunStopReason.FINAL:
        return AgentEventType.RUN_COMPLETED
    if stop_reason is RunStopReason.CANCELLED:
        return AgentEventType.RUN_CANCELLED
    return AgentEventType.RUN_FAILED


def _validate_event_skeleton(events: tuple[AgentEvent, ...], case: EvalCase) -> None:
    if not events:
        raise HarnessExecutionError("Agent Runtime returned no Events")
    first = events[0]
    if first.event_type is not AgentEventType.RUN_QUEUED:
        raise HarnessExecutionError("Agent Runtime Event stream must begin with queued")

    terminal_events: list[AgentEvent] = []
    previous_time = first.occurred_at
    for expected_sequence, event in enumerate(events, start=1):
        if (
            event.stream_id != first.stream_id
            or event.run_id != first.run_id
            or event.workspace_id != first.workspace_id
            or event.trace_id != first.trace_id
        ):
            raise HarnessExecutionError("Agent Runtime Events must share one execution identity")
        if event.sequence != expected_sequence:
            raise HarnessExecutionError("Agent Runtime Event sequence must be contiguous")
        if event.occurred_at < previous_time:
            raise HarnessExecutionError("Agent Runtime Event time cannot move backwards")
        if terminal_events:
            raise HarnessExecutionError("No Agent Runtime Event may follow a terminal Event")
        if event.event_type in TERMINAL_AGENT_EVENT_TYPES:
            terminal_events.append(event)
        previous_time = event.occurred_at

    if len(terminal_events) != 1 or terminal_events[0] is not events[-1]:
        raise HarnessExecutionError("Agent Runtime must return exactly one final terminal Event")
    terminal = terminal_events[0]
    if terminal.event_type is not _expected_terminal_event(case.expected_stop_reason):
        raise HarnessExecutionError("Agent Runtime terminal Event contradicts expected stop reason")
    if terminal.payload.get("stop_reason") != case.expected_stop_reason.value:
        raise HarnessExecutionError("Agent Runtime terminal payload omits the expected stop reason")


class HarnessRunner[CommandT, RuntimeContextT]:
    """Call one injected Runtime once; never implement a model/tool loop here."""

    def __init__(
        self,
        *,
        runtime: AgentRuntime[CommandT, RuntimeContextT],
        materializer: ScenarioMaterializer[CommandT, RuntimeContextT],
    ) -> None:
        self._runtime = runtime
        self._materializer = materializer

    async def run_case(self, case: EvalCase) -> HarnessRunResult:
        """Materialize trusted inputs and preserve the Runtime Event stream exactly."""

        materialized = self._materializer.materialize(case.scenario)
        streamed_events = [
            event
            async for event in self._runtime.run(
                materialized.command,
                materialized.runtime_context,
            )
        ]
        events = tuple(streamed_events)
        _validate_event_skeleton(events, case)
        return HarnessRunResult(
            case_id=case.case_id,
            case_version=case.case_version,
            events=events,
        )
