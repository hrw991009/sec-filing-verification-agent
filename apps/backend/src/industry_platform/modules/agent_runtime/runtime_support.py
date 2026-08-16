"""Shared event, clock, cancellation, and terminal-transition mechanics for Runtime."""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from industry_platform.modules.agent_runtime.domain import (
    AGENT_RUNTIME_SCHEMA_VERSION,
    AgentRun,
    AgentRunStatus,
    AgentStep,
    AgentStepStatus,
    RunStopReason,
    require_utc,
    validate_step_sequence,
)
from industry_platform.modules.agent_runtime.events import (
    AgentEvent,
    AgentEventType,
    validate_event_stream,
)
from industry_platform.modules.agent_runtime.model import ModelUsage
from industry_platform.modules.agent_runtime.ports import (
    AgentEventCommitter,
    CancellationProbe,
)
from industry_platform.modules.agent_runtime.state import (
    RunState,
    validate_run_state,
    validate_state_transition,
)


class RuntimeDeadlineExceeded(RuntimeError):
    """Internal control signal that never leaves the Runtime boundary."""


def utc_now() -> datetime:
    return datetime.now(UTC)


class RuntimeTransitionSupport:
    """Keep generic state/event mechanics out of the Direct Answer decision flow."""

    def __init__(
        self,
        *,
        event_committer: AgentEventCommitter,
        cancellation_probe: CancellationProbe,
        clock: Callable[[], datetime],
    ) -> None:
        self._event_committer = event_committer
        self._cancellation_probe = cancellation_probe
        self._clock = clock

    async def _commit(self, events: list[AgentEvent], event: AgentEvent) -> None:
        await self._event_committer.append(event)
        events.append(event)

    async def _commit_batch(
        self,
        events: list[AgentEvent],
        batch: tuple[AgentEvent, ...],
    ) -> None:
        if not batch:
            raise ValueError("Runtime Event batch cannot be empty")
        await self._event_committer.append_batch(batch)
        events.extend(batch)

    def _time(self, *, not_before: datetime) -> datetime:
        value = self._clock()
        require_utc(value, field_name="Runtime clock value")
        if value < not_before:
            raise ValueError("Runtime clock cannot move backwards")
        return value

    @staticmethod
    def _event(
        run: AgentRun,
        events: list[AgentEvent],
        *,
        event_type: AgentEventType,
        occurred_at: datetime,
        payload: dict[str, object] | None = None,
    ) -> AgentEvent:
        return AgentEvent(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            stream_id=run.event_stream_id,
            run_id=run.run_id,
            workspace_id=run.workspace_id,
            sequence=len(events) + 1,
            occurred_at=occurred_at,
            trace_id=run.trace_id,
            event_type=event_type,
            payload={} if payload is None else payload,
        )

    async def _cancel_requested(self, run: AgentRun) -> bool:
        return await self._cancellation_probe.is_cancel_requested(
            run_id=run.run_id,
            workspace_id=run.workspace_id,
        )

    def _before_deadline(self, run: AgentRun, *, not_before: datetime) -> datetime:
        value = self._time(not_before=not_before)
        if value >= run.budget.deadline:
            raise RuntimeDeadlineExceeded
        return value

    @staticmethod
    def _settled_step(
        running_step: AgentStep,
        *,
        status: AgentStepStatus,
        revision: int,
        completed_at: datetime,
        usage: ModelUsage | None = None,
        error_code: str | None = None,
        output_summary: dict[str, object] | None = None,
    ) -> AgentStep:
        selected_usage = usage or ModelUsage(0, 0, 0, 0)
        latency_ms = max(
            0,
            int((completed_at - running_step.started_at).total_seconds() * 1_000),
        )
        return replace(
            running_step,
            status=status,
            state_revision=revision,
            completed_at=completed_at,
            output_summary={} if output_summary is None else output_summary,
            input_tokens=selected_usage.input_tokens,
            output_tokens=selected_usage.output_tokens,
            cost_micro_usd=selected_usage.cost_micro_usd,
            latency_ms=latency_ms,
            error_code=error_code,
        )

    def _terminal_event(
        self,
        *,
        run: AgentRun,
        state: RunState,
        events: list[AgentEvent],
        steps: tuple[AgentStep, ...],
        status: AgentRunStatus,
        stop_reason: RunStopReason,
        occurred_at: datetime,
        step_count: int | None = None,
        usage: ModelUsage | None = None,
        token_budget_preflight_rejected: bool = False,
        cost_budget_preflight_rejected: bool = False,
        terminal_details: dict[str, object] | None = None,
    ) -> AgentEvent:
        revision = state.revision + 1
        terminal_state = replace(
            state,
            revision=revision,
            status=status,
            step_count=state.step_count if step_count is None else step_count,
            event_count=len(events) + 1,
            input_tokens_used=(
                state.input_tokens_used
                if usage is None
                else state.input_tokens_used + usage.input_tokens
            ),
            output_tokens_used=(
                state.output_tokens_used
                if usage is None
                else state.output_tokens_used + usage.output_tokens
            ),
            cost_micro_usd=(
                state.cost_micro_usd
                if usage is None
                else state.cost_micro_usd + usage.cost_micro_usd
            ),
            updated_at=occurred_at,
            stop_reason=stop_reason,
            token_budget_preflight_rejected=token_budget_preflight_rejected,
            cost_budget_preflight_rejected=cost_budget_preflight_rejected,
        )
        terminal_run = replace(
            run,
            status=status,
            state_revision=revision,
            terminal_at=occurred_at,
            stop_reason=stop_reason,
        )
        event_type = {
            AgentRunStatus.COMPLETED: AgentEventType.RUN_COMPLETED,
            AgentRunStatus.FAILED: AgentEventType.RUN_FAILED,
            AgentRunStatus.CANCELLED: AgentEventType.RUN_CANCELLED,
        }[status]
        terminal_event = self._event(
            terminal_run,
            events,
            event_type=event_type,
            occurred_at=occurred_at,
            payload={
                "stop_reason": stop_reason.value,
                "state_revision": revision,
                **(
                    {}
                    if usage is None
                    else {
                        "input_tokens": usage.input_tokens,
                        "output_tokens": usage.output_tokens,
                        "cached_input_tokens": usage.cached_input_tokens,
                        "cost_micro_usd": usage.cost_micro_usd,
                    }
                ),
                **(
                    {"cost_budget_preflight_rejected": True}
                    if cost_budget_preflight_rejected
                    else {}
                ),
                **({} if terminal_details is None else terminal_details),
            },
        )
        validate_state_transition(state, terminal_state, expected_revision=state.revision)
        validate_run_state(terminal_run, terminal_state)
        validate_step_sequence(steps, terminal_run)
        validate_event_stream((*events, terminal_event), terminal_run)
        return terminal_event
