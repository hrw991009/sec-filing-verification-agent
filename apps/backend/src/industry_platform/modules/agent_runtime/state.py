"""Typed Agent state and optimistic transition invariants."""

from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID

from industry_platform.modules.agent_runtime.domain import (
    TERMINAL_RUN_STATUSES,
    AgentRun,
    AgentRunStatus,
    RunBudget,
    RunStopReason,
    require_current_schema_version,
    require_non_nil_uuid,
    require_utc,
    validate_stop_reason,
)

_ALLOWED_STATUS_TRANSITIONS: Final = {
    AgentRunStatus.QUEUED: frozenset(
        {
            AgentRunStatus.QUEUED,
            AgentRunStatus.RUNNING,
            AgentRunStatus.FAILED,
            AgentRunStatus.CANCELLED,
        }
    ),
    AgentRunStatus.RUNNING: frozenset(
        {
            AgentRunStatus.RUNNING,
            AgentRunStatus.PAUSED,
            AgentRunStatus.COMPLETED,
            AgentRunStatus.FAILED,
            AgentRunStatus.CANCELLED,
        }
    ),
    AgentRunStatus.PAUSED: frozenset(
        {
            AgentRunStatus.PAUSED,
            AgentRunStatus.RUNNING,
            AgentRunStatus.FAILED,
            AgentRunStatus.CANCELLED,
        }
    ),
}


def _require_non_negative(value: int, *, field_name: str) -> None:
    if isinstance(value, bool) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")


def _snapshot_artifact_ids(values: tuple[UUID, ...]) -> tuple[UUID, ...]:
    snapshot = tuple(values)
    if len(snapshot) != len(set(snapshot)):
        raise ValueError("Run State cannot contain duplicate Artifact references")
    for artifact_id in snapshot:
        require_non_nil_uuid(artifact_id, field_name="Run State Artifact ID")
    return snapshot


@dataclass(frozen=True, slots=True)
class RunState:
    """Minimum recoverable projection for a Direct Answer run."""

    schema_version: int
    run_id: UUID
    workspace_id: UUID
    revision: int
    status: AgentRunStatus
    step_count: int
    event_count: int
    input_tokens_used: int
    output_tokens_used: int
    cost_micro_usd: int
    updated_at: datetime
    artifact_ids: tuple[UUID, ...] = ()
    stop_reason: RunStopReason | None = None
    token_budget_preflight_rejected: bool = False

    def __post_init__(self) -> None:
        require_current_schema_version(self.schema_version)
        require_non_nil_uuid(self.run_id, field_name="Run State run ID")
        require_non_nil_uuid(self.workspace_id, field_name="Run State workspace ID")
        for value, name in (
            (self.revision, "Run State revision"),
            (self.step_count, "Run State step count"),
            (self.event_count, "Run State event count"),
            (self.input_tokens_used, "Run State input tokens"),
            (self.output_tokens_used, "Run State output tokens"),
            (self.cost_micro_usd, "Run State cost"),
        ):
            _require_non_negative(value, field_name=name)
        if self.step_count > self.event_count:
            raise ValueError("Run State cannot contain more Steps than Events")
        require_utc(self.updated_at, field_name="Run State update time")
        object.__setattr__(self, "artifact_ids", _snapshot_artifact_ids(self.artifact_ids))
        validate_stop_reason(self.status, self.stop_reason)
        if not isinstance(self.token_budget_preflight_rejected, bool):
            raise ValueError("Run State token preflight flag is invalid")
        if self.token_budget_preflight_rejected and (
            self.status is not AgentRunStatus.FAILED
            or self.stop_reason is not RunStopReason.TOKEN_BUDGET_EXCEEDED
        ):
            raise ValueError("Only a failed token preflight may set its rejection flag")

    @property
    def total_tokens_used(self) -> int:
        """Return the stable aggregate used by the token budget."""

        return self.input_tokens_used + self.output_tokens_used


def exhausted_budget_reason(
    state: RunState,
    budget: RunBudget,
) -> RunStopReason | None:
    """Return the first deterministic hard ceiling reached by State."""

    if state.updated_at >= budget.deadline:
        return RunStopReason.DEADLINE_EXCEEDED
    if state.step_count >= budget.max_steps:
        return RunStopReason.MAX_STEPS
    if state.total_tokens_used >= budget.max_total_tokens:
        return RunStopReason.TOKEN_BUDGET_EXCEEDED
    if state.cost_micro_usd >= budget.max_cost_micro_usd:
        return RunStopReason.COST_BUDGET_EXCEEDED
    return None


def validate_run_state(run: AgentRun, state: RunState) -> None:
    """Ensure persisted Run metadata and its recoverable State agree."""

    if state.run_id != run.run_id or state.workspace_id != run.workspace_id:
        raise ValueError("Run State belongs to another run or workspace")
    if state.revision != run.state_revision:
        raise ValueError("Run and State revisions must match")
    if state.status is not run.status or state.stop_reason is not run.stop_reason:
        raise ValueError("Run and State terminal metadata must match")
    if state.updated_at < run.created_at:
        raise ValueError("Run State cannot predate its Run")
    if state.step_count > run.budget.max_steps:
        raise ValueError("Run State step count exceeds its hard budget")

    exhausted = exhausted_budget_reason(state, run.budget)
    if run.status not in TERMINAL_RUN_STATUSES and exhausted is not None:
        raise ValueError("A budget-exhausted State must be terminalized")
    if run.stop_reason is RunStopReason.MAX_STEPS and (state.step_count < run.budget.max_steps):
        raise ValueError("Max-step stop reason requires an exhausted step budget")
    if run.stop_reason is RunStopReason.TOKEN_BUDGET_EXCEEDED and (
        state.total_tokens_used < run.budget.max_total_tokens
        and not state.token_budget_preflight_rejected
    ):
        raise ValueError("Token stop reason requires exhaustion or a recorded preflight rejection")
    if run.stop_reason is RunStopReason.COST_BUDGET_EXCEEDED and (
        state.cost_micro_usd < run.budget.max_cost_micro_usd
    ):
        raise ValueError("Cost stop reason requires an exhausted cost budget")
    if run.stop_reason is RunStopReason.DEADLINE_EXCEEDED and (
        state.updated_at < run.budget.deadline
    ):
        raise ValueError("Deadline stop reason requires an expired deadline")


def validate_state_transition(
    previous: RunState,
    successor: RunState,
    *,
    expected_revision: int,
) -> None:
    """Freeze the CAS and append-only rules a CheckpointStore must enforce."""

    _require_non_negative(expected_revision, field_name="Expected State revision")
    if previous.revision != expected_revision:
        raise ValueError("Expected State revision is stale")
    if successor.revision != expected_revision + 1:
        raise ValueError("Successor State revision must increase by exactly one")
    if successor.run_id != previous.run_id or successor.workspace_id != previous.workspace_id:
        raise ValueError("A State transition cannot change Run or Workspace identity")
    if previous.status in TERMINAL_RUN_STATUSES:
        raise ValueError("A terminal Run State cannot transition again")
    if successor.status not in _ALLOWED_STATUS_TRANSITIONS[previous.status]:
        raise ValueError("Run State status transition is invalid")
    if successor.updated_at < previous.updated_at:
        raise ValueError("Run State update time cannot move backwards")
    if successor.event_count <= previous.event_count:
        raise ValueError("Each State revision must append at least one Event")
    for before, after, name in (
        (previous.step_count, successor.step_count, "step count"),
        (previous.input_tokens_used, successor.input_tokens_used, "input tokens"),
        (previous.output_tokens_used, successor.output_tokens_used, "output tokens"),
        (previous.cost_micro_usd, successor.cost_micro_usd, "cost"),
    ):
        if after < before:
            raise ValueError(f"Run State {name} cannot decrease")
    if successor.artifact_ids[: len(previous.artifact_ids)] != previous.artifact_ids:
        raise ValueError("Run State Artifact references are append-only")
    if previous.token_budget_preflight_rejected and not successor.token_budget_preflight_rejected:
        raise ValueError("Run State token preflight rejection cannot be removed")
