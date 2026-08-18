"""Application boundary that executes one persisted Direct Answer Run."""

import asyncio
import logging
from dataclasses import dataclass, field, replace
from typing import Protocol
from uuid import UUID

from industry_platform.modules.agent_runtime.context import TrustedRuntimeContext
from industry_platform.modules.agent_runtime.domain import (
    AgentRunStatus,
    RunStopReason,
    require_non_nil_uuid,
    validate_stop_reason,
)
from industry_platform.modules.agent_runtime.events import (
    TERMINAL_AGENT_EVENT_TYPES,
    AgentEvent,
    AgentEventType,
    validate_event_stream,
)
from industry_platform.modules.agent_runtime.ports import AgentRuntime
from industry_platform.modules.agent_runtime.runtime_contracts import DirectAnswerRunCommand
from industry_platform.modules.agent_runtime.tool_runtime_contracts import ToolL2RunCommand

type ProductionAgentRunCommand = DirectAnswerRunCommand | ToolL2RunCommand

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DirectAnswerExecutionInput:
    """Trusted Runtime command and authorization context loaded from durable facts."""

    command: ProductionAgentRunCommand = field(repr=False)
    runtime_context: TrustedRuntimeContext = field(repr=False)


@dataclass(frozen=True, slots=True)
class DirectAnswerExecutionResult:
    """Small non-sensitive terminal result safe to store on the execution Job."""

    run_id: UUID
    status: AgentRunStatus
    stop_reason: RunStopReason
    terminal_event_sequence: int

    def __post_init__(self) -> None:
        require_non_nil_uuid(self.run_id, field_name="Executed Agent Run ID")
        if self.status not in {
            AgentRunStatus.COMPLETED,
            AgentRunStatus.FAILED,
            AgentRunStatus.CANCELLED,
        }:
            raise ValueError("Agent execution result must be terminal")
        validate_stop_reason(self.status, self.stop_reason)
        if isinstance(self.terminal_event_sequence, bool) or self.terminal_event_sequence < 1:
            raise ValueError("Agent execution terminal sequence is invalid")


class DirectAnswerRunLoader(Protocol):
    """Load trusted Runtime inputs without placing them in the Job payload."""

    async def load(self, run_id: UUID) -> DirectAnswerExecutionInput: ...


class AgentRunTerminalizer(Protocol):
    """Idempotently close a Run that cannot continue through the Day 2 Runtime."""

    async def settle_unrecoverable(self, run_id: UUID, *, error_code: str) -> bool: ...


class AgentRunOrphanReconciler(Protocol):
    """Close Runs whose durable Job can no longer execute their Day 2 work."""

    async def reconcile_orphans(self, *, batch_size: int) -> int: ...


class DirectAnswerRunExecutionUseCase(Protocol):
    """Execute one persisted Run through the project's only Agent Runtime."""

    async def execute_run(self, run_id: UUID) -> DirectAnswerExecutionResult: ...


@dataclass(frozen=True, slots=True)
class DirectAnswerRunExecutionService:
    """Load one Run, consume the unified Runtime, and return its terminal fact."""

    loader: DirectAnswerRunLoader = field(repr=False)
    runtime: AgentRuntime[ProductionAgentRunCommand, TrustedRuntimeContext] = field(repr=False)
    terminalizer: AgentRunTerminalizer | None = field(default=None, repr=False)

    async def execute_run(self, run_id: UUID) -> DirectAnswerExecutionResult:
        require_non_nil_uuid(run_id, field_name="Agent Run execution ID")
        try:
            execution = await self.loader.load(run_id)
            run = execution.command.run
            if run.run_id != run_id:
                raise ValueError("Loaded Agent Run does not match the requested ID")

            events = tuple(
                [
                    event
                    async for event in self.runtime.run(
                        execution.command,
                        execution.runtime_context,
                    )
                ]
            )
            if not events or events[-1].event_type not in TERMINAL_AGENT_EVENT_TYPES:
                raise ValueError("Agent Runtime ended without one terminal Event")

            terminal = events[-1]
            status = _status_for_terminal_event(terminal.event_type)
            stop_reason = _stop_reason(terminal)
            started = next(
                (
                    event.occurred_at
                    for event in events
                    if event.event_type is AgentEventType.RUN_STARTED
                ),
                None,
            )
            terminal_run = replace(
                run,
                status=status,
                started_at=started,
                terminal_at=terminal.occurred_at,
                stop_reason=stop_reason,
            )
            validate_event_stream(events, terminal_run)
        except asyncio.CancelledError:
            await self._settle_unrecoverable(run_id, error_code="execution_cancelled")
            raise
        except Exception:
            await self._settle_unrecoverable(run_id, error_code="execution_failed")
            raise

        input_tokens, output_tokens, cost_micro_usd = _event_usage(events)
        duration_ms = max(
            0,
            int((terminal.occurred_at - (started or run.created_at)).total_seconds() * 1_000),
        )
        logger.info(
            "agent_run_terminal run_id=%s job_id=%s trace_id=%s status=%s "
            "stop_reason=%s duration_ms=%d input_tokens=%d output_tokens=%d "
            "cost_micro_usd=%d",
            run_id,
            run.job_id or "none",
            run.trace_id,
            status.value,
            stop_reason.value,
            duration_ms,
            input_tokens,
            output_tokens,
            cost_micro_usd,
        )
        return DirectAnswerExecutionResult(
            run_id=run_id,
            status=status,
            stop_reason=stop_reason,
            terminal_event_sequence=terminal.sequence,
        )

    async def _settle_unrecoverable(self, run_id: UUID, *, error_code: str) -> None:
        if self.terminalizer is None:
            return
        try:
            await asyncio.shield(
                self.terminalizer.settle_unrecoverable(run_id, error_code=error_code)
            )
        except Exception:
            logger.exception(
                "agent_run_terminalization_failed run_id=%s error_code=%s",
                run_id,
                error_code,
            )


def _status_for_terminal_event(event_type: AgentEventType) -> AgentRunStatus:
    try:
        return {
            AgentEventType.RUN_COMPLETED: AgentRunStatus.COMPLETED,
            AgentEventType.RUN_FAILED: AgentRunStatus.FAILED,
            AgentEventType.RUN_CANCELLED: AgentRunStatus.CANCELLED,
        }[event_type]
    except KeyError:
        raise ValueError("Agent Runtime terminal Event type is invalid") from None


def _stop_reason(event: AgentEvent) -> RunStopReason:
    value = event.payload.get("stop_reason")
    if not isinstance(value, str):
        raise ValueError("Agent Runtime terminal Event has no stop reason")
    try:
        return RunStopReason(value)
    except ValueError:
        raise ValueError("Agent Runtime terminal Event stop reason is invalid") from None


def _event_usage(events: tuple[AgentEvent, ...]) -> tuple[int, int, int]:
    totals = [0, 0, 0]
    keys = ("input_tokens", "output_tokens", "cost_micro_usd")
    for event in events:
        if event.event_type not in {
            AgentEventType.STEP_COMPLETED,
            AgentEventType.STEP_FAILED,
        }:
            continue
        for index, key in enumerate(keys):
            value = event.payload.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                totals[index] += value
    return totals[0], totals[1], totals[2]
