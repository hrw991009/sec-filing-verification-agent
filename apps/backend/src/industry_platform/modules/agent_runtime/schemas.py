"""Strict HTTP projections for the safe Agent Trace read model."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from industry_platform.modules.agent_runtime.context import (
    ContextDecisionReason,
    ContextSourceKind,
)
from industry_platform.modules.agent_runtime.domain import (
    AgentRunStatus,
    AgentRunType,
    AgentStepKind,
    AgentStepStatus,
    RunStopReason,
)
from industry_platform.modules.agent_runtime.events import AgentEventType
from industry_platform.modules.agent_runtime.model import ModelRole
from industry_platform.modules.agent_runtime.trace import AgentTrace, TraceUsage


class StrictAgentTraceModel(BaseModel):
    """Reject undocumented fields at every level of the Trace response."""

    model_config = ConfigDict(extra="forbid", strict=True)


class TraceUsageResponse(StrictAgentTraceModel):
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cost_micro_usd: int


class TraceRunResponse(StrictAgentTraceModel):
    schema_version: int
    run_id: UUID
    workspace_id: UUID
    conversation_id: UUID
    turn_id: UUID
    event_stream_id: UUID
    trace_id: str
    run_type: AgentRunType
    status: AgentRunStatus
    stop_reason: RunStopReason | None
    runtime_version: str
    harness_version: str
    state_revision: int
    max_steps: int
    max_total_tokens: int
    max_cost_micro_usd: int
    deadline: datetime
    event_count: int
    step_count: int
    usage: TraceUsageResponse
    created_at: datetime
    started_at: datetime | None
    terminal_at: datetime | None


class TraceStepResponse(StrictAgentTraceModel):
    step_id: UUID
    sequence: int
    kind: AgentStepKind
    status: AgentStepStatus
    last_event_sequence: int
    started_at: datetime
    completed_at: datetime | None
    usage: TraceUsageResponse
    error_code: str | None


class TraceEventResponse(StrictAgentTraceModel):
    schema_version: int
    sequence: int
    occurred_at: datetime
    event_type: AgentEventType
    details: dict[str, str | int]


class ContextSourceResponse(StrictAgentTraceModel):
    ordinal: int
    source_kind: ContextSourceKind
    source_id: str
    source_version: str
    included: bool
    decision_reason: ContextDecisionReason
    estimated_token_count: int
    message_role: ModelRole | None
    source_sha256: str | None
    source_revision_id: UUID | None
    source_scope: str | None
    relevance_score: float | None
    feedback_score: int | None
    source_identity: dict[str, object] | None


class ContextBudgetResponse(StrictAgentTraceModel):
    run_max_total_tokens: int
    tokens_used_before_step: int
    max_input_tokens: int
    estimated_input_tokens: int
    allowed_output_tokens: int
    unreserved_run_tokens: int


class ContextManifestResponse(StrictAgentTraceModel):
    schema_version: int
    manifest_id: UUID
    workspace_id: UUID
    run_id: UUID
    step_id: UUID
    compiler_version: str
    prompt_version: str
    runtime_projection_version: str
    token_counter_version: str
    created_at: datetime
    budget: ContextBudgetResponse
    sources: list[ContextSourceResponse]


class AgentTraceResponse(StrictAgentTraceModel):
    schema_version: int
    run: TraceRunResponse
    steps: list[TraceStepResponse]
    context_manifests: list[ContextManifestResponse]
    events: list[TraceEventResponse]


def _usage_response(usage: TraceUsage) -> TraceUsageResponse:
    return TraceUsageResponse(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        cost_micro_usd=usage.cost_micro_usd,
    )


def agent_trace_response(trace: AgentTrace) -> AgentTraceResponse:
    """Copy only fields already approved by the safe AgentTrace projection."""

    return AgentTraceResponse(
        schema_version=trace.schema_version,
        run=TraceRunResponse(
            schema_version=trace.run.schema_version,
            run_id=trace.run.run_id,
            workspace_id=trace.run.workspace_id,
            conversation_id=trace.run.conversation_id,
            turn_id=trace.run.turn_id,
            event_stream_id=trace.run.event_stream_id,
            trace_id=str(trace.run.trace_id),
            run_type=trace.run.run_type,
            status=trace.run.status,
            stop_reason=trace.run.stop_reason,
            runtime_version=trace.run.runtime_version,
            harness_version=trace.run.harness_version,
            state_revision=trace.run.state_revision,
            max_steps=trace.run.max_steps,
            max_total_tokens=trace.run.max_total_tokens,
            max_cost_micro_usd=trace.run.max_cost_micro_usd,
            deadline=trace.run.deadline,
            event_count=trace.run.event_count,
            step_count=trace.run.step_count,
            usage=_usage_response(trace.run.usage),
            created_at=trace.run.created_at,
            started_at=trace.run.started_at,
            terminal_at=trace.run.terminal_at,
        ),
        steps=[
            TraceStepResponse(
                step_id=step.step_id,
                sequence=step.sequence,
                kind=step.kind,
                status=step.status,
                last_event_sequence=step.last_event_sequence,
                started_at=step.started_at,
                completed_at=step.completed_at,
                usage=_usage_response(step.usage),
                error_code=step.error_code,
            )
            for step in trace.steps
        ],
        context_manifests=[
            ContextManifestResponse(
                schema_version=manifest.schema_version,
                manifest_id=manifest.manifest_id,
                workspace_id=manifest.workspace_id,
                run_id=manifest.run_id,
                step_id=manifest.step_id,
                compiler_version=manifest.compiler_version,
                prompt_version=manifest.prompt_version,
                runtime_projection_version=manifest.runtime_projection_version,
                token_counter_version=manifest.token_counter_version,
                created_at=manifest.created_at,
                budget=ContextBudgetResponse(
                    run_max_total_tokens=manifest.budget.run_max_total_tokens,
                    tokens_used_before_step=manifest.budget.tokens_used_before_step,
                    max_input_tokens=manifest.budget.max_input_tokens,
                    estimated_input_tokens=manifest.budget.estimated_input_tokens,
                    allowed_output_tokens=manifest.budget.allowed_output_tokens,
                    unreserved_run_tokens=manifest.budget.unreserved_run_tokens,
                ),
                sources=[
                    ContextSourceResponse(
                        ordinal=source.ordinal,
                        source_kind=source.source_kind,
                        source_id=source.source_id,
                        source_version=source.source_version,
                        included=source.included,
                        decision_reason=source.decision_reason,
                        estimated_token_count=source.estimated_token_count,
                        message_role=source.message_role,
                        source_sha256=source.source_sha256,
                        source_revision_id=source.source_revision_id,
                        source_scope=source.source_scope,
                        relevance_score=source.relevance_score,
                        feedback_score=source.feedback_score,
                        source_identity=(
                            None if source.source_identity is None else dict(source.source_identity)
                        ),
                    )
                    for source in manifest.sources
                ],
            )
            for manifest in trace.context_manifests
        ],
        events=[
            TraceEventResponse(
                schema_version=event.schema_version,
                sequence=event.sequence,
                occurred_at=event.occurred_at,
                event_type=event.event_type,
                details=dict(event.details),
            )
            for event in trace.events
        ],
    )
