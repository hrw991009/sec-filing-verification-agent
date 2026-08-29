"""SQLAlchemy query adapter for the safe Agent Learning Workbench read model."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from industry_platform.core.database import AsyncSessionFactory, safe_sqlstate
from industry_platform.modules.agent_runtime.context import (
    ContextBudgetSnapshot,
    ContextDecisionReason,
    ContextManifest,
    ContextSourceKind,
    ContextSourceManifestEntry,
)
from industry_platform.modules.agent_runtime.domain import (
    AgentRun,
    RunBudget,
)
from industry_platform.modules.agent_runtime.events import (
    AgentEvent,
    AgentEventType,
    validate_event_stream,
)
from industry_platform.modules.agent_runtime.model import ModelRole
from industry_platform.modules.agent_runtime.models import (
    AgentEventRecord,
    AgentRunRecord,
    AgentStepRecord,
    ContextManifestRecord,
)
from industry_platform.modules.agent_runtime.trace import (
    TRACE_VIEW_SCHEMA_VERSION,
    AgentTrace,
    TraceEvent,
    TraceRun,
    TraceStep,
    TraceUsage,
)
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.workspaces.domain import WorkspaceScope


class AgentTraceNotFoundError(LookupError):
    """Hide whether a Run exists outside the caller's trusted Workspace scope."""

    def __init__(self) -> None:
        super().__init__("Agent Trace was not found")


class AgentTraceDataError(RuntimeError):
    """Report invalid persisted Trace facts without echoing their contents."""

    def __init__(self) -> None:
        super().__init__("Persisted Agent Trace facts are inconsistent")


class AgentTraceQueryError(RuntimeError):
    """Sanitized database failure while reading a Trace."""

    def __init__(self, *, sqlstate: str | None = None) -> None:
        super().__init__("Agent Trace query failed")
        self.sqlstate = sqlstate


_SAFE_EVENT_FIELDS: Mapping[AgentEventType, tuple[str, ...]] = {
    AgentEventType.RUN_QUEUED: (
        "run_type",
        "runtime_version",
        "harness_version",
        "loop_level",
        "graph_version",
        "tool_call_limit",
    ),
    AgentEventType.RUN_STARTED: ("state_revision",),
    AgentEventType.RUN_PAUSED: (
        "state_revision",
        "checkpoint_revision",
        "approval_request_id",
        "pause_reason",
    ),
    AgentEventType.RUN_RESUMED: ("state_revision", "checkpoint_revision", "resume_node"),
    AgentEventType.RUN_COMPLETED: ("stop_reason", "research_node"),
    AgentEventType.RUN_FAILED: ("stop_reason", "loop_guard", "research_node", "error_code"),
    AgentEventType.RUN_CANCELLED: ("stop_reason", "cancelled_step_id", "research_node"),
    AgentEventType.STEP_STARTED: ("step_id", "step_sequence", "step_kind"),
    AgentEventType.STEP_COMPLETED: (
        "step_id",
        "step_kind",
        "input_tokens",
        "output_tokens",
        "cost_micro_usd",
        "contract_version",
        "format",
        "finish_reason",
    ),
    AgentEventType.STEP_FAILED: ("step_id", "error_code"),
    AgentEventType.MODEL_STARTED: ("step_id", "model", "context_manifest_id"),
    AgentEventType.MODEL_DELTA: ("step_id", "model_sequence"),
    AgentEventType.MODEL_COMPLETED: (
        "step_id",
        "model",
        "finish_reason",
        "input_tokens",
        "output_tokens",
        "cached_input_tokens",
        "cost_micro_usd",
        "pricing_version",
        "provider_request_id",
    ),
    AgentEventType.TOOL_REQUESTED: (
        "call_id",
        "requested_by_step_id",
        "requested_tool_name",
        "requested_tool_version",
        "toolset_version",
        "policy_version",
        "sanitizer_version",
    ),
    AgentEventType.TOOL_APPROVAL_REQUIRED: (
        "call_id",
        "approval_request_id",
        "policy_decision",
        "policy_reason_code",
        "error_code",
        "resolved_tool_name",
        "tool_version",
    ),
    AgentEventType.TOOL_DENIED: (
        "call_id",
        "policy_decision",
        "policy_reason_code",
        "error_code",
        "resolved_tool_name",
        "tool_version",
    ),
    AgentEventType.TOOL_STARTED: (
        "call_id",
        "execution_step_id",
        "resolved_tool_name",
        "tool_version",
        "input_schema_version",
        "output_schema_version",
        "required_capability",
        "timeout_ms",
        "max_result_bytes",
        "max_cost_micro_usd",
        "cost_class",
        "side_effect_class",
        "approval_policy",
        "policy_version",
        "policy_decision",
        "policy_reason_code",
    ),
    AgentEventType.TOOL_COMPLETED: (
        "call_id",
        "execution_step_id",
        "duration_ms",
        "cost_micro_usd",
        "observation_schema_version",
        "observation_id",
        "observation_content_sha256",
        "observation_envelope_sha256",
    ),
    AgentEventType.TOOL_FAILED: ("call_id", "execution_step_id", "error_code"),
    AgentEventType.TOOL_CANCELLED: ("call_id", "execution_step_id"),
    AgentEventType.ARTIFACT_CREATED: ("artifact_id", "artifact_kind", "version"),
    AgentEventType.CHECKPOINT_SAVED: ("checkpoint_id", "revision"),
    AgentEventType.APPROVAL_REQUESTED: (
        "approval_request_id",
        "checkpoint_revision",
        "reason",
        "expires_at",
    ),
    AgentEventType.APPROVAL_DECIDED: (
        "approval_request_id",
        "checkpoint_revision",
        "outcome",
    ),
    AgentEventType.RESEARCH_NODE_STARTED: (
        "node",
        "graph_version",
        "research_state_schema_version",
        "state_revision",
    ),
    AgentEventType.RESEARCH_NODE_COMPLETED: (
        "node",
        "graph_version",
        "research_state_schema_version",
        "state_revision",
    ),
    AgentEventType.RESEARCH_NODE_FAILED: (
        "node",
        "error_code",
        "graph_version",
        "state_revision",
    ),
    AgentEventType.VERIFICATION_COMPLETED: (
        "report_id",
        "revision",
        "verification_status",
        "coverage",
        "issue_count",
        "checker_version",
    ),
}

if frozenset(_SAFE_EVENT_FIELDS) != frozenset(AgentEventType):
    raise RuntimeError("Every Agent Event type requires an explicit safe Trace projection")


@dataclass(frozen=True, slots=True)
class SqlAlchemyAgentTraceQuery:
    """Read a Run timeline from formal tables without exposing prompt or answer text."""

    session_factory: AsyncSessionFactory

    async def get(self, *, scope: WorkspaceScope, run_id: UUID) -> AgentTrace:
        try:
            async with self.session_factory() as session:
                await session.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                )
                run = await session.scalar(
                    select(AgentRunRecord).where(
                        AgentRunRecord.id == run_id,
                        AgentRunRecord.workspace_id == scope.workspace_id,
                    )
                )
                if run is None:
                    raise AgentTraceNotFoundError
                steps = tuple(
                    await session.scalars(
                        select(AgentStepRecord)
                        .where(
                            AgentStepRecord.run_id == run_id,
                            AgentStepRecord.workspace_id == scope.workspace_id,
                        )
                        .order_by(AgentStepRecord.sequence)
                    )
                )
                manifests = tuple(
                    await session.scalars(
                        select(ContextManifestRecord)
                        .where(
                            ContextManifestRecord.run_id == run_id,
                            ContextManifestRecord.workspace_id == scope.workspace_id,
                        )
                        .order_by(ContextManifestRecord.created_at, ContextManifestRecord.id)
                    )
                )
                events = tuple(
                    await session.scalars(
                        select(AgentEventRecord)
                        .where(
                            AgentEventRecord.run_id == run_id,
                            AgentEventRecord.workspace_id == scope.workspace_id,
                        )
                        .order_by(AgentEventRecord.sequence)
                    )
                )
        except AgentTraceNotFoundError:
            raise
        except SQLAlchemyError as error:
            raise AgentTraceQueryError(sqlstate=safe_sqlstate(error)) from None

        try:
            return _build_trace(run, steps=steps, manifests=manifests, events=events)
        except (TypeError, ValueError):
            raise AgentTraceDataError from None


def _build_trace(
    record: AgentRunRecord,
    *,
    steps: Sequence[AgentStepRecord],
    manifests: Sequence[ContextManifestRecord],
    events: Sequence[AgentEventRecord],
) -> AgentTrace:
    domain_run = _to_domain_run(record)
    domain_events = tuple(_to_domain_event(event) for event in events)
    validate_event_stream(domain_events, domain_run)
    return AgentTrace(
        schema_version=TRACE_VIEW_SCHEMA_VERSION,
        run=_to_trace_run(record),
        steps=tuple(_to_trace_step(step) for step in steps),
        context_manifests=tuple(_to_context_manifest(manifest) for manifest in manifests),
        events=tuple(_to_trace_event(event) for event in events),
    )


def _to_domain_run(record: AgentRunRecord) -> AgentRun:
    return AgentRun(
        schema_version=record.schema_version,
        run_id=record.id,
        event_stream_id=record.event_stream_id,
        workspace_id=record.workspace_id,
        user_id=record.user_id,
        run_type=record.run_type,
        runtime_version=record.runtime_version,
        harness_version=record.harness_version,
        budget=RunBudget(
            schema_version=record.schema_version,
            max_steps=record.max_steps,
            max_total_tokens=record.max_total_tokens,
            max_cost_micro_usd=record.max_cost_micro_usd,
            deadline=record.deadline,
        ),
        trace_id=TraceId(record.trace_id),
        status=record.status,
        state_revision=record.state_revision,
        created_at=record.created_at,
        started_at=record.started_at,
        terminal_at=record.terminal_at,
        stop_reason=record.stop_reason,
        thread_id=record.conversation_id,
        turn_id=record.turn_id,
        job_id=record.job_id,
    )


def _to_trace_run(record: AgentRunRecord) -> TraceRun:
    return TraceRun(
        schema_version=record.schema_version,
        run_id=record.id,
        workspace_id=record.workspace_id,
        conversation_id=record.conversation_id,
        turn_id=record.turn_id,
        event_stream_id=record.event_stream_id,
        trace_id=TraceId(record.trace_id),
        run_type=record.run_type,
        status=record.status,
        stop_reason=record.stop_reason,
        runtime_version=record.runtime_version,
        harness_version=record.harness_version,
        state_revision=record.state_revision,
        max_steps=record.max_steps,
        max_total_tokens=record.max_total_tokens,
        max_cost_micro_usd=record.max_cost_micro_usd,
        deadline=record.deadline,
        event_count=record.event_count,
        step_count=record.step_count,
        usage=TraceUsage(
            input_tokens=record.input_tokens_used,
            output_tokens=record.output_tokens_used,
            cached_input_tokens=record.cached_input_tokens_used,
            cost_micro_usd=record.cost_micro_usd,
        ),
        created_at=record.created_at,
        started_at=record.started_at,
        terminal_at=record.terminal_at,
    )


def _to_trace_step(record: AgentStepRecord) -> TraceStep:
    return TraceStep(
        step_id=record.id,
        sequence=record.sequence,
        kind=record.kind,
        status=record.status,
        last_event_sequence=record.last_event_sequence,
        started_at=record.started_at,
        completed_at=record.completed_at,
        usage=TraceUsage(
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            cached_input_tokens=0,
            cost_micro_usd=record.cost_micro_usd,
        ),
        error_code=record.error_code,
    )


def _to_domain_event(record: AgentEventRecord) -> AgentEvent:
    return AgentEvent(
        schema_version=record.schema_version,
        stream_id=record.stream_id,
        run_id=record.run_id,
        workspace_id=record.workspace_id,
        sequence=record.sequence,
        occurred_at=record.occurred_at,
        trace_id=TraceId(record.trace_id),
        event_type=record.event_type,
        payload=record.payload,
    )


def _to_trace_event(record: AgentEventRecord) -> TraceEvent:
    details: dict[str, str | int] = {}
    for field_name in _SAFE_EVENT_FIELDS[record.event_type]:
        value = record.payload.get(field_name)
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            continue
        details[field_name] = value
    if record.event_type is AgentEventType.MODEL_DELTA:
        delta = record.payload.get("delta")
        if isinstance(delta, str):
            details["delta_character_count"] = len(delta)
    return TraceEvent(
        schema_version=record.schema_version,
        sequence=record.sequence,
        occurred_at=record.occurred_at,
        event_type=record.event_type,
        details=details,
    )


def _to_context_manifest(record: ContextManifestRecord) -> ContextManifest:
    budget = record.budget
    sources = record.sources
    return ContextManifest(
        schema_version=record.schema_version,
        manifest_id=record.id,
        workspace_id=record.workspace_id,
        run_id=record.run_id,
        step_id=record.step_id,
        compiler_version=record.compiler_version,
        prompt_version=record.prompt_version,
        runtime_projection_version=record.runtime_projection_version,
        token_counter_version=record.token_counter_version,
        created_at=record.created_at,
        budget=ContextBudgetSnapshot(
            run_max_total_tokens=_required_int(budget, "run_max_total_tokens"),
            tokens_used_before_step=_required_int(budget, "tokens_used_before_step"),
            max_input_tokens=_required_int(budget, "max_input_tokens"),
            estimated_input_tokens=_required_int(budget, "estimated_input_tokens"),
            allowed_output_tokens=_required_int(budget, "allowed_output_tokens"),
            unreserved_run_tokens=_required_int(budget, "unreserved_run_tokens"),
        ),
        sources=tuple(_to_context_source(source) for source in sources),
    )


def _to_context_source(value: object) -> ContextSourceManifestEntry:
    if not isinstance(value, Mapping):
        raise ValueError("Context source is invalid")
    message_role_value = value.get("message_role")
    message_role = (
        None if message_role_value is None else ModelRole(_required_str(value, "message_role"))
    )
    return ContextSourceManifestEntry(
        ordinal=_required_int(value, "ordinal"),
        source_kind=ContextSourceKind(_required_str(value, "source_kind")),
        source_id=_required_str(value, "source_id"),
        source_version=_required_str(value, "source_version"),
        included=_required_bool(value, "included"),
        decision_reason=ContextDecisionReason(_required_str(value, "decision_reason")),
        estimated_token_count=_required_int(value, "estimated_token_count"),
        message_role=message_role,
        source_sha256=(
            None if value.get("source_sha256") is None else _required_str(value, "source_sha256")
        ),
        source_revision_id=(
            None
            if value.get("source_revision_id") is None
            else UUID(_required_str(value, "source_revision_id"))
        ),
        source_scope=(
            None if value.get("source_scope") is None else _required_str(value, "source_scope")
        ),
        relevance_score=(
            None
            if value.get("relevance_score") is None
            else _required_float(value, "relevance_score")
        ),
        feedback_score=(
            None if value.get("feedback_score") is None else _required_int(value, "feedback_score")
        ),
        source_identity=(
            None
            if value.get("source_identity") is None
            else _required_mapping(value, "source_identity")
        ),
    )


def _required_int(value: Mapping[str, object], key: str) -> int:
    item = value.get(key)
    if isinstance(item, bool) or not isinstance(item, int):
        raise ValueError("Trace integer field is invalid")
    return item


def _required_str(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise ValueError("Trace string field is invalid")
    return item


def _required_bool(value: Mapping[str, object], key: str) -> bool:
    item = value.get(key)
    if not isinstance(item, bool):
        raise ValueError("Trace boolean field is invalid")
    return item


def _required_float(value: Mapping[str, object], key: str) -> float:
    item = value.get(key)
    if isinstance(item, bool) or not isinstance(item, int | float):
        raise ValueError("Trace numeric field is invalid")
    return float(item)


def _required_mapping(value: Mapping[str, object], key: str) -> dict[str, object]:
    item = value.get(key)
    if not isinstance(item, Mapping):
        raise ValueError("Trace mapping field is invalid")
    return {str(item_key): item_value for item_key, item_value in item.items()}
