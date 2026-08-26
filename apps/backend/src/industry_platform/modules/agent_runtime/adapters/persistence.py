"""SQLAlchemy adapters for committed Events, manifests, replay, and cancellation."""

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import and_, func, literal, or_, select
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from industry_platform.core.database import AsyncSessionFactory, safe_sqlstate
from industry_platform.modules.agent_runtime.context import ContextManifest
from industry_platform.modules.agent_runtime.delivery import (
    AgentRunDeliveryUnavailableError,
    AgentRunStreamDescriptor,
)
from industry_platform.modules.agent_runtime.domain import (
    TERMINAL_RUN_STATUSES,
    AgentRunStatus,
    AgentRunType,
    AgentStepKind,
    AgentStepStatus,
    RunStopReason,
    require_utc,
)
from industry_platform.modules.agent_runtime.events import AgentEvent, AgentEventType
from industry_platform.modules.agent_runtime.models import (
    AgentEventRecord,
    AgentRunRecord,
    AgentStepRecord,
    ContextManifestRecord,
)
from industry_platform.modules.agent_runtime.ports import ContextManifestStoreError
from industry_platform.modules.agent_runtime.streaming import (
    DEFAULT_COMMITTED_EVENT_WINDOW,
    MAX_COMMITTED_EVENT_WINDOW,
    CommittedEventWindow,
    StreamSnapshot,
)
from industry_platform.modules.conversations.models import Message, MessageRole, MessageStatus
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.jobs.domain import TERMINAL_JOB_STATUSES, JobEventType, JobStatus
from industry_platform.modules.jobs.models import Job, JobEvent
from industry_platform.modules.tools.domain import ToolObservation, ToolReference, ToolSource
from industry_platform.modules.tools.models import ToolCallRecord, ToolRunRecord


class AgentEventPersistenceError(RuntimeError):
    """Sanitized failure to append or project a committed Agent Event."""

    def __init__(self, *, sqlstate: str | None = None) -> None:
        super().__init__("Agent Event persistence failed")
        self.sqlstate = sqlstate


@dataclass(frozen=True, slots=True)
class SqlAlchemyContextManifestStore:
    session_factory: AsyncSessionFactory

    async def save(self, manifest: ContextManifest) -> None:
        try:
            async with self.session_factory.begin() as session:
                existing = await session.scalar(
                    select(ContextManifestRecord).where(
                        ContextManifestRecord.step_id == manifest.step_id,
                        ContextManifestRecord.workspace_id == manifest.workspace_id,
                    )
                )
                values = _manifest_values(manifest)
                if existing is not None:
                    if _manifest_record_values(existing) != values:
                        raise ContextManifestStoreError(
                            "A different Context manifest already exists for this Step"
                        )
                    return
                session.add(ContextManifestRecord(**values))
        except ContextManifestStoreError:
            raise
        except SQLAlchemyError as error:
            raise ContextManifestStoreError(
                f"Context manifest persistence failed ({safe_sqlstate(error) or 'database'})"
            ) from None


@dataclass(frozen=True, slots=True)
class SqlAlchemyAgentEventCommitter:
    """Append one Event and update its Run/Step projection in the same transaction."""

    session_factory: AsyncSessionFactory

    async def append(self, event: AgentEvent) -> None:
        await self.append_batch((event,))

    async def append_batch(self, events: tuple[AgentEvent, ...]) -> None:
        if not events:
            raise ValueError("Agent Event persistence batch cannot be empty")
        first = events[0]
        if any(
            event.run_id != first.run_id
            or event.workspace_id != first.workspace_id
            or event.stream_id != first.stream_id
            or event.trace_id != first.trace_id
            or event.schema_version != first.schema_version
            for event in events[1:]
        ):
            raise ValueError("Agent Event persistence batch must belong to one Run")
        try:
            async with self.session_factory.begin() as session:
                run = await session.scalar(
                    select(AgentRunRecord)
                    .where(
                        AgentRunRecord.id == first.run_id,
                        AgentRunRecord.workspace_id == first.workspace_id,
                    )
                    .with_for_update()
                )
                if run is None:
                    raise AgentEventPersistenceError()
                for index, event in enumerate(events):
                    await _append_locked_agent_event(session, run, event)
                    if index + 1 < len(events):
                        # Later projections in the same transaction may lock rows
                        # created by an earlier Event in this atomic batch.
                        await session.flush()
        except AgentEventPersistenceError:
            raise
        except (TypeError, ValueError):
            raise AgentEventPersistenceError() from None
        except SQLAlchemyError as error:
            raise AgentEventPersistenceError(sqlstate=safe_sqlstate(error)) from None

    @staticmethod
    async def _project(session: object, run: AgentRunRecord, event: AgentEvent) -> None:
        # Runtime Ports stay SQLAlchemy-free; only this concrete adapter sees AsyncSession.
        if not isinstance(session, AsyncSession):
            raise AgentEventPersistenceError()
        payload = event.payload
        if event.event_type is AgentEventType.RUN_STARTED:
            run.status = AgentRunStatus.RUNNING
            run.started_at = event.occurred_at
            run.state_revision = _optional_int(payload, "state_revision") or 1
            await _project_research_lifecycle(session, run, event)
            return
        if event.event_type is AgentEventType.RUN_PAUSED:
            revision = _required_int(payload, "state_revision")
            if run.status is not AgentRunStatus.RUNNING or revision <= run.state_revision:
                raise AgentEventPersistenceError()
            run.status = AgentRunStatus.PAUSED
            run.state_revision = revision
            await _project_research_lifecycle(session, run, event)
            return
        if event.event_type is AgentEventType.RUN_RESUMED:
            revision = _required_int(payload, "state_revision")
            if (
                run.status
                not in {
                    AgentRunStatus.PAUSED,
                    AgentRunStatus.RUNNING,
                }
                or revision <= run.state_revision
            ):
                raise AgentEventPersistenceError()
            run.status = AgentRunStatus.RUNNING
            run.state_revision = revision
            await _project_research_lifecycle(session, run, event)
            return
        if event.event_type in {
            AgentEventType.CHECKPOINT_SAVED,
            AgentEventType.APPROVAL_REQUESTED,
            AgentEventType.APPROVAL_DECIDED,
        }:
            return
        if event.event_type in {
            AgentEventType.RESEARCH_NODE_STARTED,
            AgentEventType.RESEARCH_NODE_COMPLETED,
            AgentEventType.RESEARCH_NODE_FAILED,
        }:
            node_revision = _required_int(payload, "state_revision")
            if node_revision <= run.state_revision:
                raise AgentEventPersistenceError()
            run.state_revision = node_revision
            await _project_research_lifecycle(session, run, event)
            return
        if event.event_type is AgentEventType.STEP_STARTED:
            step_id = _required_uuid(payload, "step_id")
            step_sequence = _required_int(payload, "step_sequence")
            step_kind = AgentStepKind(_required_str(payload, "step_kind"))
            session.add(
                AgentStepRecord(
                    id=step_id,
                    workspace_id=event.workspace_id,
                    run_id=event.run_id,
                    sequence=step_sequence,
                    kind=step_kind,
                    status=AgentStepStatus.RUNNING,
                    last_event_sequence=event.sequence,
                    started_at=event.occurred_at,
                    completed_at=None,
                    input_tokens=0,
                    output_tokens=0,
                    cost_micro_usd=0,
                    error_code=None,
                )
            )
            run.step_count = max(run.step_count, step_sequence)
            return
        if event.event_type in {AgentEventType.STEP_COMPLETED, AgentEventType.STEP_FAILED}:
            step = await _locked_step(
                session,
                run_id=event.run_id,
                workspace_id=event.workspace_id,
                step_id=_required_uuid(payload, "step_id"),
            )
            step_cost_micro_usd = _optional_int(payload, "cost_micro_usd") or 0
            if step.kind is AgentStepKind.TOOL:
                await _validate_tool_step_terminal_projection(
                    session,
                    event,
                    step=step,
                    cost_micro_usd=step_cost_micro_usd,
                )
            step.status = (
                AgentStepStatus.COMPLETED
                if event.event_type is AgentEventType.STEP_COMPLETED
                else AgentStepStatus.FAILED
            )
            step.completed_at = event.occurred_at
            step.last_event_sequence = event.sequence
            step.input_tokens = _optional_int(payload, "input_tokens") or 0
            step.output_tokens = _optional_int(payload, "output_tokens") or 0
            step.cost_micro_usd = step_cost_micro_usd
            step.error_code = (
                _required_str(payload, "error_code")
                if event.event_type is AgentEventType.STEP_FAILED
                else None
            )
            if step.kind is AgentStepKind.MODEL:
                run.input_tokens_used += step.input_tokens
                run.output_tokens_used += step.output_tokens
                run.cached_input_tokens_used += _optional_int(payload, "cached_input_tokens") or 0
            run.cost_micro_usd += step.cost_micro_usd
            return
        if event.event_type is AgentEventType.TOOL_REQUESTED:
            await _project_tool_requested(session, run, event)
            return
        if event.event_type in {
            AgentEventType.TOOL_APPROVAL_REQUIRED,
            AgentEventType.TOOL_DENIED,
        }:
            await _project_tool_rejected(session, event)
            return
        if event.event_type is AgentEventType.TOOL_STARTED:
            await _project_tool_started(session, event)
            return
        if event.event_type is AgentEventType.TOOL_COMPLETED:
            await _project_tool_completed(session, event)
            return
        if event.event_type in {
            AgentEventType.TOOL_FAILED,
            AgentEventType.TOOL_CANCELLED,
        }:
            await _project_tool_settled_without_result(session, event)
            return
        if event.event_type in {
            AgentEventType.RUN_COMPLETED,
            AgentEventType.RUN_FAILED,
            AgentEventType.RUN_CANCELLED,
        }:
            projected_revision = _optional_int(payload, "state_revision")
            if projected_revision is not None:
                if projected_revision <= run.state_revision:
                    raise AgentEventPersistenceError()
                run.state_revision = projected_revision
            run.status = {
                AgentEventType.RUN_COMPLETED: AgentRunStatus.COMPLETED,
                AgentEventType.RUN_FAILED: AgentRunStatus.FAILED,
                AgentEventType.RUN_CANCELLED: AgentRunStatus.CANCELLED,
            }[event.event_type]
            run.stop_reason = RunStopReason(_required_str(payload, "stop_reason"))
            run.terminal_at = event.occurred_at
            await _project_research_lifecycle(session, run, event)
            await _settle_interrupted_tool_facts(session, event)
            if event.event_type is AgentEventType.RUN_CANCELLED:
                await _settle_cancelled_step(session, run, event)
            if event.event_type is AgentEventType.RUN_COMPLETED:
                await _persist_final_message(session, run, event.occurred_at)
            else:
                await _persist_partial_message(session, run, event)


async def _project_research_lifecycle(
    session: object,
    run: AgentRunRecord,
    event: AgentEvent,
) -> None:
    """Update only the Research domain extension; Agent events remain the execution truth."""

    if run.run_type is not AgentRunType.RESEARCH:
        return
    if not isinstance(session, AsyncSession):
        raise AgentEventPersistenceError()
    from industry_platform.modules.research.domain import (
        ResearchApprovalStatus,
        ResearchNode,
        ResearchRunStatus,
    )
    from industry_platform.modules.research.models import ResearchRunRecord

    research = await session.scalar(
        select(ResearchRunRecord)
        .where(
            ResearchRunRecord.agent_run_id == run.id,
            ResearchRunRecord.workspace_id == run.workspace_id,
            ResearchRunRecord.owner_user_id == run.user_id,
        )
        .with_for_update()
    )
    if research is None:
        raise AgentEventPersistenceError()
    if event.event_type is AgentEventType.RUN_STARTED:
        research.status = ResearchRunStatus.ACTIVE
    elif event.event_type is AgentEventType.RUN_PAUSED:
        research.status = ResearchRunStatus.PAUSED
        state = dict(research.state)
        state.update(status=AgentRunStatus.PAUSED.value, approval_status="pending")
        research.state = state
    elif event.event_type is AgentEventType.RUN_RESUMED:
        research.status = ResearchRunStatus.ACTIVE
        state = dict(research.state)
        resume_kind = _required_str(event.payload, "resume_kind")
        state.update(
            status=AgentRunStatus.RUNNING.value,
            approval_status=(
                ResearchApprovalStatus.ALLOWED.value
                if resume_kind == "approval"
                else state.get("approval_status", "not_required")
            ),
            stop_reason=None,
        )
        research.state = state
    elif event.event_type in {
        AgentEventType.RESEARCH_NODE_STARTED,
        AgentEventType.RESEARCH_NODE_COMPLETED,
        AgentEventType.RESEARCH_NODE_FAILED,
    }:
        research.current_node = ResearchNode(_required_str(event.payload, "node"))
        if event.event_type is AgentEventType.RESEARCH_NODE_FAILED:
            research.error_summary = _required_str(event.payload, "error_code")
    elif event.event_type in {
        AgentEventType.RUN_COMPLETED,
        AgentEventType.RUN_FAILED,
        AgentEventType.RUN_CANCELLED,
    }:
        research.status = {
            AgentEventType.RUN_COMPLETED: ResearchRunStatus.COMPLETED,
            AgentEventType.RUN_FAILED: ResearchRunStatus.FAILED,
            AgentEventType.RUN_CANCELLED: ResearchRunStatus.CANCELLED,
        }[event.event_type]
        state = dict(research.state)
        terminal_error = (
            run.stop_reason.value
            if event.event_type is AgentEventType.RUN_FAILED and run.stop_reason is not None
            else state.get("error_summary")
        )
        state.update(
            status=run.status.value,
            step_count=run.step_count,
            input_tokens_used=run.input_tokens_used,
            output_tokens_used=run.output_tokens_used,
            cost_micro_usd=run.cost_micro_usd,
            stop_reason=run.stop_reason.value if run.stop_reason is not None else None,
            cancel_requested=event.event_type is AgentEventType.RUN_CANCELLED,
            approval_status=(
                "required"
                if run.stop_reason is RunStopReason.APPROVAL_REQUIRED
                else (
                    "denied"
                    if run.stop_reason is RunStopReason.APPROVAL_DENIED
                    else (
                        "timed_out"
                        if run.stop_reason is RunStopReason.APPROVAL_TIMED_OUT
                        else "not_required"
                    )
                )
            ),
            error_summary=terminal_error,
        )
        research.state = state
        if event.event_type is AgentEventType.RUN_FAILED:
            research.error_summary = str(terminal_error)
    else:
        raise AgentEventPersistenceError()
    research.revision += 1
    research.updated_at = event.occurred_at


async def _locked_tool_facts(
    session: AsyncSession,
    event: AgentEvent,
) -> tuple[ToolCallRecord, ToolRunRecord]:
    call_id = _required_uuid(event.payload, "call_id")
    call = await session.scalar(
        select(ToolCallRecord)
        .where(
            ToolCallRecord.id == call_id,
            ToolCallRecord.run_id == event.run_id,
            ToolCallRecord.workspace_id == event.workspace_id,
        )
        .with_for_update()
    )
    audit = await session.scalar(
        select(ToolRunRecord)
        .where(
            ToolRunRecord.id == call_id,
            ToolRunRecord.run_id == event.run_id,
            ToolRunRecord.workspace_id == event.workspace_id,
        )
        .with_for_update()
    )
    if call is None or audit is None:
        raise AgentEventPersistenceError()
    return call, audit


async def _validate_tool_step_terminal_projection(
    session: AsyncSession,
    event: AgentEvent,
    *,
    step: AgentStepRecord,
    cost_micro_usd: int,
) -> None:
    """Require one Tool Step settlement to match its already-projected Tool facts."""

    call_id = _required_uuid(event.payload, "call_id")
    call = await session.scalar(
        select(ToolCallRecord)
        .where(
            ToolCallRecord.id == call_id,
            ToolCallRecord.execution_step_id == step.id,
            ToolCallRecord.run_id == event.run_id,
            ToolCallRecord.workspace_id == event.workspace_id,
        )
        .with_for_update()
    )
    audit = await session.scalar(
        select(ToolRunRecord)
        .where(
            ToolRunRecord.id == call_id,
            ToolRunRecord.run_id == event.run_id,
            ToolRunRecord.workspace_id == event.workspace_id,
        )
        .with_for_update()
    )
    expected_status = "completed" if event.event_type is AgentEventType.STEP_COMPLETED else "failed"
    expected_error = (
        None if expected_status == "completed" else _required_str(event.payload, "error_code")
    )
    if (
        call is None
        or audit is None
        or call.status != expected_status
        or audit.status != expected_status
        or call.cost_micro_usd != cost_micro_usd
        or audit.cost_micro_usd != cost_micro_usd
        or call.error_code != expected_error
        or audit.error_code != expected_error
    ):
        raise AgentEventPersistenceError()
    if expected_status == "completed" and _required_str(event.payload, "step_kind") != "tool":
        raise AgentEventPersistenceError()


async def _validate_cancelled_tool_step_projection(
    session: AsyncSession,
    event: AgentEvent,
    *,
    step: AgentStepRecord,
) -> None:
    call = await session.scalar(
        select(ToolCallRecord)
        .where(
            ToolCallRecord.execution_step_id == step.id,
            ToolCallRecord.run_id == event.run_id,
            ToolCallRecord.workspace_id == event.workspace_id,
        )
        .with_for_update()
    )
    if call is None:
        raise AgentEventPersistenceError()
    audit = await session.scalar(
        select(ToolRunRecord)
        .where(
            ToolRunRecord.id == call.id,
            ToolRunRecord.run_id == event.run_id,
            ToolRunRecord.workspace_id == event.workspace_id,
        )
        .with_for_update()
    )
    if (
        audit is None
        or call.status != "cancelled"
        or audit.status != "cancelled"
        or call.cost_micro_usd != 0
        or audit.cost_micro_usd != 0
        or call.error_code is not None
        or audit.error_code is not None
    ):
        raise AgentEventPersistenceError()


async def _project_tool_requested(
    session: AsyncSession,
    run: AgentRunRecord,
    event: AgentEvent,
) -> None:
    payload = event.payload
    call_id = _required_uuid(payload, "call_id")
    requested_by_step_id = _required_uuid(payload, "requested_by_step_id")
    requesting_step = await _locked_step(
        session,
        run_id=event.run_id,
        workspace_id=event.workspace_id,
        step_id=requested_by_step_id,
    )
    if requesting_step.kind is not AgentStepKind.MODEL:
        raise AgentEventPersistenceError()
    actor_user_id = _required_uuid(payload, "actor_user_id")
    trace_id = _required_str(payload, "trace_id")
    if actor_user_id != run.user_id or trace_id != run.trace_id:
        raise AgentEventPersistenceError()
    digest = _required_sha256_bytes(payload, "sanitized_arguments_sha256")
    summary = _required_json_object(payload, "sanitized_input_summary")
    common: dict[str, object] = {
        "id": call_id,
        "workspace_id": event.workspace_id,
        "run_id": event.run_id,
        "schema_version": event.schema_version,
        "requested_tool_name": _required_str(payload, "requested_tool_name"),
        "requested_tool_version": _required_str(payload, "requested_tool_version"),
        "toolset_version": _required_str(payload, "toolset_version"),
        "policy_version": _required_str(payload, "policy_version"),
        "status": "requested",
        "cost_micro_usd": 0,
        "created_at": event.occurred_at,
        "updated_at": event.occurred_at,
    }
    session.add(
        ToolCallRecord(
            **common,
            requested_by_step_id=requested_by_step_id,
            execution_step_id=None,
            sanitized_arguments_hash=digest,
        )
    )
    session.add(
        ToolRunRecord(
            **common,
            actor_user_id=actor_user_id,
            actor_role=_required_str(payload, "actor_role"),
            trace_id=trace_id,
            sanitizer_version=_required_str(payload, "sanitizer_version"),
            sanitized_input_summary=summary,
            source_summary=[],
        )
    )


def _apply_tool_definition_snapshot(
    call: ToolCallRecord,
    audit: ToolRunRecord,
    payload: Mapping[str, object],
) -> None:
    resolved_name = payload.get("resolved_tool_name")
    if resolved_name is None:
        return
    values: dict[str, object] = {
        "resolved_tool_name": _required_str(payload, "resolved_tool_name"),
        "tool_version": _required_str(payload, "tool_version"),
        "input_schema_version": _required_str(payload, "input_schema_version"),
        "output_schema_version": _required_str(payload, "output_schema_version"),
        "required_capability": _required_str(payload, "required_capability"),
        "cost_class": _required_str(payload, "cost_class"),
        "side_effect_class": _required_str(payload, "side_effect_class"),
        "approval_policy": _required_str(payload, "approval_policy"),
        "retry_classification": _required_str(payload, "retry_classification"),
        "policy_version": _required_str(payload, "policy_version"),
        "timeout_ms": _required_int(payload, "timeout_ms"),
        "max_result_bytes": _required_int(payload, "max_result_bytes"),
        "max_cost_micro_usd": _required_int(payload, "max_cost_micro_usd"),
    }
    for record in (call, audit):
        for key, value in values.items():
            setattr(record, key, value)


async def _project_tool_rejected(session: AsyncSession, event: AgentEvent) -> None:
    call, audit = await _locked_tool_facts(session, event)
    if call.status != "requested" or audit.status != "requested":
        raise AgentEventPersistenceError()
    _apply_tool_definition_snapshot(call, audit, event.payload)
    decision = _required_str(event.payload, "policy_decision")
    reason = _required_str(event.payload, "policy_reason_code")
    status = (
        "approval_required"
        if event.event_type is AgentEventType.TOOL_APPROVAL_REQUIRED
        else "denied"
    )
    if (status == "approval_required") != (decision == "approval_required"):
        raise AgentEventPersistenceError()
    if status == "denied" and decision != "deny":
        raise AgentEventPersistenceError()
    for record in (call, audit):
        record.status = status
        record.policy_decision = decision
        record.policy_reason_code = reason
        record.terminal_at = event.occurred_at
        record.updated_at = event.occurred_at
        record.error_code = None if status == "approval_required" else reason


async def _project_tool_started(session: AsyncSession, event: AgentEvent) -> None:
    call, audit = await _locked_tool_facts(session, event)
    if call.status != "requested" or audit.status != "requested":
        raise AgentEventPersistenceError()
    execution_step_id = _required_uuid(event.payload, "execution_step_id")
    execution_step = await _locked_step(
        session,
        run_id=event.run_id,
        workspace_id=event.workspace_id,
        step_id=execution_step_id,
    )
    if execution_step.kind is not AgentStepKind.TOOL:
        raise AgentEventPersistenceError()
    _apply_tool_definition_snapshot(call, audit, event.payload)
    decision = _required_str(event.payload, "policy_decision")
    reason = _required_str(event.payload, "policy_reason_code")
    if decision != "allow":
        raise AgentEventPersistenceError()
    digest = _required_sha256_bytes(event.payload, "sanitized_arguments_sha256")
    if digest != call.sanitized_arguments_hash:
        raise AgentEventPersistenceError()
    idempotency_hash = _optional_sha256_bytes(event.payload, "idempotency_key_sha256")
    call.execution_step_id = execution_step_id
    call.idempotency_key_hash = idempotency_hash
    call.started_at = event.occurred_at
    for record in (call, audit):
        record.status = "running"
        record.policy_decision = decision
        record.policy_reason_code = reason
        record.updated_at = event.occurred_at


async def _project_tool_completed(session: AsyncSession, event: AgentEvent) -> None:
    call, audit = await _locked_tool_facts(session, event)
    if call.status != "running" or audit.status != "running":
        raise AgentEventPersistenceError()
    if call.execution_step_id != _required_uuid(event.payload, "execution_step_id"):
        raise AgentEventPersistenceError()
    projection = _validated_tool_observation_projection(call, event)
    duration_ms = _required_int(event.payload, "duration_ms")
    cost_micro_usd = _required_int(event.payload, "cost_micro_usd")
    call.status = "completed"
    call.observation_schema_version = projection.schema_version
    call.observation = projection.envelope
    call.observation_content_sha256 = projection.content_sha256
    call.observation_envelope_sha256 = projection.envelope_sha256
    call.cost_micro_usd = cost_micro_usd
    call.terminal_at = event.occurred_at
    call.updated_at = event.occurred_at
    audit.status = "completed"
    audit.sanitized_output_summary = projection.output_summary
    audit.source_summary = projection.source_summary
    audit.duration_ms = duration_ms
    audit.cost_micro_usd = cost_micro_usd
    audit.terminal_at = event.occurred_at
    audit.updated_at = event.occurred_at


@dataclass(frozen=True, slots=True)
class _ValidatedToolObservationProjection:
    schema_version: int
    envelope: dict[str, object]
    content_sha256: str
    envelope_sha256: str
    output_summary: dict[str, object]
    source_summary: list[dict[str, object]]


def _validated_tool_observation_projection(
    call: ToolCallRecord,
    event: AgentEvent,
) -> _ValidatedToolObservationProjection:
    payload = event.payload
    envelope = _required_json_object(payload, "observation")
    expected_envelope_fields = {
        "schema_version",
        "observation_id",
        "call_id",
        "tool_name",
        "tool_version",
        "normalizer_version",
        "model_text",
        "content_sha256",
        "observed_at",
        "sources",
    }
    if set(envelope) != expected_envelope_fields:
        raise AgentEventPersistenceError()

    source_values = _required_json_object_list(envelope, "sources")
    sources: list[ToolSource] = []
    expected_source_fields = {
        "source_type",
        "source_version",
        "locator",
        "observed_at",
        "content_sha256",
    }
    for source in source_values:
        if set(source) != expected_source_fields:
            raise AgentEventPersistenceError()
        try:
            sources.append(
                ToolSource(
                    source_type=_required_str(source, "source_type"),
                    source_version=_required_str(source, "source_version"),
                    locator=_required_str(source, "locator"),
                    observed_at=_required_iso_datetime(source, "observed_at"),
                    content_sha256=_required_sha256_hex(source, "content_sha256"),
                )
            )
        except (TypeError, ValueError):
            raise AgentEventPersistenceError() from None

    try:
        observation = ToolObservation(
            schema_version=_required_int(envelope, "schema_version"),
            observation_id=_required_uuid(envelope, "observation_id"),
            call_id=_required_uuid(envelope, "call_id"),
            run_id=event.run_id,
            workspace_id=event.workspace_id,
            tool=ToolReference(
                name=_required_str(envelope, "tool_name"),
                version=_required_str(envelope, "tool_version"),
            ),
            normalizer_version=_required_str(envelope, "normalizer_version"),
            model_text=_required_str(envelope, "model_text"),
            sources=tuple(sources),
            observed_at=_required_iso_datetime(envelope, "observed_at"),
            content_sha256=_required_sha256_hex(envelope, "content_sha256"),
        )
    except (TypeError, ValueError):
        raise AgentEventPersistenceError() from None

    if (
        call.id != observation.call_id
        or call.run_id != event.run_id
        or call.workspace_id != event.workspace_id
        or call.resolved_tool_name is None
        or call.tool_version is None
        or observation.tool.name != call.resolved_tool_name
        or observation.tool.version != call.tool_version
        or call.started_at is None
        or observation.observed_at < call.started_at
        or observation.observed_at > event.occurred_at
    ):
        raise AgentEventPersistenceError()

    canonical_envelope = _plain_json_mapping(observation.to_persistence_payload())
    if canonical_envelope != envelope:
        raise AgentEventPersistenceError()
    declared_schema_version = _required_int(payload, "observation_schema_version")
    declared_observation_id = _required_uuid(payload, "observation_id")
    declared_content_sha256 = _required_sha256_hex(payload, "observation_content_sha256")
    declared_envelope_sha256 = _required_sha256_hex(payload, "observation_envelope_sha256")
    if (
        declared_schema_version != observation.schema_version
        or declared_observation_id != observation.observation_id
        or declared_content_sha256 != observation.content_sha256
        or declared_envelope_sha256 != observation.model_visible_envelope_sha256
    ):
        raise AgentEventPersistenceError()

    source_summary = _required_json_object_list(payload, "source_summary")
    canonical_sources = _required_json_object_list(canonical_envelope, "sources")
    output_summary = _required_json_object(payload, "sanitized_output_summary")
    canonical_output_summary = _plain_json_mapping(observation.sanitized_output_summary)
    if source_summary != canonical_sources or output_summary != canonical_output_summary:
        raise AgentEventPersistenceError()

    return _ValidatedToolObservationProjection(
        schema_version=observation.schema_version,
        envelope=canonical_envelope,
        content_sha256=observation.content_sha256,
        envelope_sha256=observation.model_visible_envelope_sha256,
        output_summary=canonical_output_summary,
        source_summary=canonical_sources,
    )


async def _project_tool_settled_without_result(
    session: AsyncSession,
    event: AgentEvent,
) -> None:
    call, audit = await _locked_tool_facts(session, event)
    if call.status != "running" or audit.status != "running" or call.started_at is None:
        raise AgentEventPersistenceError()
    if call.execution_step_id != _required_uuid(event.payload, "execution_step_id"):
        raise AgentEventPersistenceError()
    cancelled = event.event_type is AgentEventType.TOOL_CANCELLED
    status = "cancelled" if cancelled else "failed"
    error_code = None if cancelled else _required_str(event.payload, "error_code")
    cost_micro_usd = 0 if cancelled else _required_int(event.payload, "cost_micro_usd")
    duration_ms = max(
        0,
        int((event.occurred_at - call.started_at).total_seconds() * 1_000),
    )
    for record in (call, audit):
        record.status = status
        record.cost_micro_usd = cost_micro_usd
        record.terminal_at = event.occurred_at
        record.updated_at = event.occurred_at
        record.error_code = error_code
    audit.duration_ms = duration_ms


async def _settle_interrupted_tool_facts(
    session: AsyncSession,
    event: AgentEvent,
) -> None:
    calls = tuple(
        await session.scalars(
            select(ToolCallRecord)
            .where(
                ToolCallRecord.run_id == event.run_id,
                ToolCallRecord.workspace_id == event.workspace_id,
                ToolCallRecord.status.in_(("requested", "running")),
            )
            .with_for_update()
        )
    )
    for call in calls:
        audit = await session.scalar(
            select(ToolRunRecord)
            .where(
                ToolRunRecord.id == call.id,
                ToolRunRecord.run_id == event.run_id,
                ToolRunRecord.workspace_id == event.workspace_id,
            )
            .with_for_update()
        )
        if audit is None:
            raise AgentEventPersistenceError()
        _settle_interrupted_tool_fact(call, audit, event)


def _settle_interrupted_tool_fact(
    call: ToolCallRecord,
    audit: ToolRunRecord,
    event: AgentEvent,
) -> None:
    if audit.status != call.status or call.status not in {"requested", "running"}:
        raise AgentEventPersistenceError()
    execution_started = call.status == "running"
    if execution_started != (call.started_at is not None):
        raise AgentEventPersistenceError()
    if audit.side_effect_class != call.side_effect_class:
        raise AgentEventPersistenceError()
    outcome_unknown = execution_started and call.side_effect_class != "read_only"
    cancelled = event.event_type is AgentEventType.RUN_CANCELLED and not outcome_unknown
    status = "cancelled" if cancelled else "failed"
    error_code = (
        "tool_outcome_unknown"
        if outcome_unknown
        else (None if cancelled else "runtime_interrupted")
    )
    started_at = call.started_at
    for record in (call, audit):
        record.status = status
        record.terminal_at = event.occurred_at
        record.updated_at = event.occurred_at
        record.error_code = error_code
    if started_at is not None:
        audit.duration_ms = max(
            0,
            int((event.occurred_at - started_at).total_seconds() * 1_000),
        )


@dataclass(frozen=True, slots=True)
class SqlAlchemyAgentRunTerminalizer:
    """Close non-resumable Day 2 Runs after execution or Job infrastructure failure."""

    session_factory: AsyncSessionFactory

    async def settle_unrecoverable(self, run_id: UUID, *, error_code: str) -> bool:
        if run_id.int == 0 or not _valid_error_code(error_code):
            raise ValueError("Agent Run terminalization input is invalid")
        try:
            async with self.session_factory.begin() as session:
                run = await session.scalar(
                    select(AgentRunRecord).where(AgentRunRecord.id == run_id).with_for_update()
                )
                if run is None:
                    return False
                if run.status in TERMINAL_RUN_STATUSES:
                    return True
                database_now = await _database_now(session)
                await _terminalize_unrecoverable_run(
                    session,
                    run,
                    occurred_at=max(
                        database_now,
                        run.updated_at,
                        run.cancel_requested_at or run.updated_at,
                    ),
                    error_code=error_code,
                    cancelled=run.cancel_requested_at is not None,
                )
                return True
        except AgentEventPersistenceError:
            raise
        except (TypeError, ValueError):
            raise AgentEventPersistenceError() from None
        except SQLAlchemyError as error:
            raise AgentEventPersistenceError(sqlstate=safe_sqlstate(error)) from None

    async def reconcile_orphans(self, *, batch_size: int) -> int:
        if isinstance(batch_size, bool) or not 1 <= batch_size <= 1_000:
            raise ValueError("Agent Run reconciliation batch size is invalid")
        stranded_after_lease = (
            JobStatus.PENDING,
            JobStatus.DISPATCHED,
            JobStatus.RETRY_WAIT,
        )
        try:
            async with self.session_factory.begin() as session:
                rows = tuple(
                    (
                        await session.execute(
                            select(AgentRunRecord, Job)
                            .join(Job, Job.id == AgentRunRecord.job_id)
                            .where(
                                AgentRunRecord.status.not_in(tuple(TERMINAL_RUN_STATUSES)),
                                or_(
                                    Job.status.in_(tuple(TERMINAL_JOB_STATUSES)),
                                    and_(
                                        AgentRunRecord.status == AgentRunStatus.RUNNING,
                                        Job.status.in_(stranded_after_lease),
                                    ),
                                ),
                            )
                            .order_by(AgentRunRecord.updated_at, AgentRunRecord.id)
                            .limit(batch_size)
                            .with_for_update(of=(AgentRunRecord, Job), skip_locked=True)
                        )
                    ).all()
                )
                if not rows:
                    return 0
                database_now = await _database_now(session)
                for run, job in rows:
                    cancelled = (
                        run.cancel_requested_at is not None
                        or job.cancel_requested_at is not None
                        or job.status is JobStatus.CANCELLED
                    )
                    occurred_at = max(
                        database_now,
                        run.updated_at,
                        job.updated_at,
                        run.cancel_requested_at or run.updated_at,
                        job.cancel_requested_at or job.updated_at,
                    )
                    if job.status not in TERMINAL_JOB_STATUSES:
                        _terminalize_stranded_job(
                            session,
                            job,
                            occurred_at=occurred_at,
                            error_code="job_execution_abandoned",
                            cancelled=cancelled,
                        )
                    await _terminalize_unrecoverable_run(
                        session,
                        run,
                        occurred_at=occurred_at,
                        error_code="job_execution_abandoned",
                        cancelled=cancelled,
                    )
                return len(rows)
        except AgentEventPersistenceError:
            raise
        except (TypeError, ValueError):
            raise AgentEventPersistenceError() from None
        except SQLAlchemyError as error:
            raise AgentEventPersistenceError(sqlstate=safe_sqlstate(error)) from None


@dataclass(frozen=True, slots=True)
class SqlAlchemyAgentRunControl:
    """Persist cancellation and immediately settle work that never acquired a Worker."""

    session_factory: AsyncSessionFactory

    async def request_cancel(
        self, *, run_id: UUID, workspace_id: UUID, requested_at: datetime
    ) -> bool:
        require_utc(requested_at, field_name="Cancellation request time")
        try:
            async with self.session_factory.begin() as session:
                run = await session.scalar(
                    select(AgentRunRecord)
                    .where(
                        AgentRunRecord.id == run_id,
                        AgentRunRecord.workspace_id == workspace_id,
                    )
                    .with_for_update()
                )
                if run is None:
                    return False
                if run.status in {
                    AgentRunStatus.COMPLETED,
                    AgentRunStatus.FAILED,
                    AgentRunStatus.CANCELLED,
                }:
                    return True
                run.cancel_requested_at = run.cancel_requested_at or requested_at
                job = await session.scalar(
                    select(Job).where(Job.id == run.job_id).with_for_update()
                )
                if job is None or job.workspace_id != workspace_id:
                    raise AgentRunDeliveryUnavailableError()
                job.cancel_requested_at = job.cancel_requested_at or requested_at
                if job.status not in TERMINAL_JOB_STATUSES:
                    if job.status is JobStatus.RUNNING:
                        return True
                    terminal_at = max(
                        run.cancel_requested_at,
                        run.updated_at,
                        job.updated_at,
                    )
                    _cancel_unstarted_job(session, job, terminal_at=terminal_at)
                elif job.status is not JobStatus.CANCELLED:
                    raise AgentRunDeliveryUnavailableError()

                if run.status is AgentRunStatus.QUEUED:
                    terminal_at = max(
                        run.cancel_requested_at,
                        run.updated_at,
                        job.updated_at,
                    )
                    await _append_locked_agent_event(
                        session,
                        run,
                        AgentEvent(
                            schema_version=run.schema_version,
                            stream_id=run.event_stream_id,
                            run_id=run.id,
                            workspace_id=run.workspace_id,
                            sequence=run.event_count + 1,
                            occurred_at=terminal_at,
                            trace_id=TraceId(run.trace_id),
                            event_type=AgentEventType.RUN_CANCELLED,
                            payload={
                                "stop_reason": RunStopReason.CANCELLED.value,
                                "state_revision": run.state_revision + 1,
                            },
                        ),
                    )
                return True
        except AgentRunDeliveryUnavailableError:
            raise
        except (TypeError, ValueError):
            raise AgentRunDeliveryUnavailableError() from None
        except SQLAlchemyError as error:
            raise AgentRunDeliveryUnavailableError(sqlstate=safe_sqlstate(error)) from None

    async def is_cancel_requested(self, *, run_id: UUID, workspace_id: UUID) -> bool:
        try:
            async with self.session_factory() as session:
                value = await session.scalar(
                    select(AgentRunRecord.cancel_requested_at).where(
                        AgentRunRecord.id == run_id,
                        AgentRunRecord.workspace_id == workspace_id,
                    )
                )
                return value is not None
        except SQLAlchemyError as error:
            raise AgentEventPersistenceError(sqlstate=safe_sqlstate(error)) from None


@dataclass(frozen=True, slots=True)
class SqlAlchemyCommittedEventSource:
    """Replay only PostgreSQL-committed Events; never call Runtime or Provider."""

    session_factory: AsyncSessionFactory
    window_size: int = DEFAULT_COMMITTED_EVENT_WINDOW

    def __post_init__(self) -> None:
        if (
            isinstance(self.window_size, bool)
            or not 1 <= self.window_size <= MAX_COMMITTED_EVENT_WINDOW
        ):
            raise ValueError("Committed Event window size is invalid")

    async def find_run(
        self, *, run_id: UUID, workspace_id: UUID
    ) -> AgentRunStreamDescriptor | None:
        try:
            async with self.session_factory() as session:
                run = await session.scalar(
                    select(AgentRunRecord).where(
                        AgentRunRecord.id == run_id,
                        AgentRunRecord.workspace_id == workspace_id,
                    )
                )
                if run is None:
                    return None
                return AgentRunStreamDescriptor(
                    run_id=run.id,
                    workspace_id=run.workspace_id,
                    user_id=run.user_id,
                    stream_id=run.event_stream_id,
                    trace_id=TraceId(run.trace_id),
                    status=run.status,
                    latest_committed_sequence=run.event_count,
                )
        except (TypeError, ValueError):
            raise AgentRunDeliveryUnavailableError() from None
        except SQLAlchemyError as error:
            raise AgentRunDeliveryUnavailableError(sqlstate=safe_sqlstate(error)) from None

    async def load_window(self, *, stream_id: UUID, workspace_id: UUID) -> CommittedEventWindow:
        try:
            async with self.session_factory() as session:
                run = await session.scalar(
                    select(AgentRunRecord).where(
                        AgentRunRecord.event_stream_id == stream_id,
                        AgentRunRecord.workspace_id == workspace_id,
                    )
                )
                if run is None:
                    raise AgentRunDeliveryUnavailableError()
                descending_records = tuple(
                    await session.scalars(
                        select(AgentEventRecord)
                        .where(
                            AgentEventRecord.stream_id == stream_id,
                            AgentEventRecord.workspace_id == workspace_id,
                            AgentEventRecord.sequence <= run.event_count,
                        )
                        .order_by(AgentEventRecord.sequence.desc())
                        .limit(self.window_size)
                    )
                )
                records = tuple(reversed(descending_records))
                if not records or records[-1].sequence != run.event_count:
                    raise AgentRunDeliveryUnavailableError()
                snapshot = (
                    await _load_authoritative_stream_snapshot(
                        session,
                        run=run,
                        aligned_event=records[-1],
                    )
                    if records[0].sequence > 1
                    else None
                )
            events = tuple(_to_domain_event(record) for record in records)
            return CommittedEventWindow(
                stream_id=stream_id,
                workspace_id=workspace_id,
                earliest_available_sequence=events[0].sequence,
                latest_committed_sequence=run.event_count,
                events=events,
                snapshot=snapshot,
            )
        except AgentRunDeliveryUnavailableError:
            raise
        except (TypeError, ValueError):
            raise AgentRunDeliveryUnavailableError() from None
        except SQLAlchemyError as error:
            raise AgentRunDeliveryUnavailableError(sqlstate=safe_sqlstate(error)) from None

    async def load_events_after(
        self,
        *,
        run_id: UUID,
        stream_id: UUID,
        workspace_id: UUID,
        after_sequence: int,
        limit: int,
    ) -> tuple[AgentEvent, ...]:
        if isinstance(after_sequence, bool) or after_sequence < 0:
            raise ValueError("Committed Event cursor is invalid")
        if isinstance(limit, bool) or not 1 <= limit <= 10_000:
            raise ValueError("Committed Event batch limit is invalid")
        try:
            async with self.session_factory() as session:
                records = tuple(
                    await session.scalars(
                        select(AgentEventRecord)
                        .where(
                            AgentEventRecord.run_id == run_id,
                            AgentEventRecord.stream_id == stream_id,
                            AgentEventRecord.workspace_id == workspace_id,
                            AgentEventRecord.sequence > after_sequence,
                        )
                        .order_by(AgentEventRecord.sequence)
                        .limit(limit)
                    )
                )
            return tuple(_to_domain_event(record) for record in records)
        except (TypeError, ValueError):
            raise AgentRunDeliveryUnavailableError() from None
        except SQLAlchemyError as error:
            raise AgentRunDeliveryUnavailableError(sqlstate=safe_sqlstate(error)) from None


async def _load_authoritative_stream_snapshot(
    session: AsyncSession,
    *,
    run: AgentRunRecord,
    aligned_event: AgentEventRecord,
) -> StreamSnapshot:
    """Build current client state in PostgreSQL without loading every Event row."""

    content_markdown = await session.scalar(
        select(
            func.string_agg(
                AgentEventRecord.payload["delta"].astext,
                aggregate_order_by(literal(""), AgentEventRecord.sequence),
            )
        ).where(
            AgentEventRecord.run_id == run.id,
            AgentEventRecord.workspace_id == run.workspace_id,
            AgentEventRecord.sequence <= run.event_count,
            AgentEventRecord.event_type == AgentEventType.MODEL_DELTA,
        )
    )
    if content_markdown is None:
        content_markdown = ""
    if not isinstance(content_markdown, str):
        raise AgentRunDeliveryUnavailableError()
    return StreamSnapshot(
        schema_version=run.schema_version,
        stream_id=run.event_stream_id,
        workspace_id=run.workspace_id,
        trace_id=TraceId(run.trace_id),
        last_sequence=run.event_count,
        occurred_at=aligned_event.occurred_at,
        payload={
            "run_id": str(run.id),
            "status": run.status.value,
            "stop_reason": run.stop_reason.value if run.stop_reason is not None else None,
            "terminal": run.status in TERMINAL_RUN_STATUSES,
            "content_markdown": content_markdown,
            "input_tokens": run.input_tokens_used,
            "output_tokens": run.output_tokens_used,
            "cached_input_tokens": run.cached_input_tokens_used,
            "cost_micro_usd": run.cost_micro_usd,
        },
    )


async def _database_now(session: AsyncSession) -> datetime:
    value = await session.scalar(select(func.clock_timestamp()))
    if not isinstance(value, datetime):
        raise AgentEventPersistenceError()
    require_utc(value, field_name="Agent Run terminal time")
    return value


async def _terminalize_unrecoverable_run(
    session: AsyncSession,
    run: AgentRunRecord,
    *,
    occurred_at: datetime,
    error_code: str,
    cancelled: bool,
) -> None:
    """Append one terminal Event and settle any interrupted Step under the Run lock."""

    running_steps = tuple(
        await session.scalars(
            select(AgentStepRecord)
            .where(
                AgentStepRecord.run_id == run.id,
                AgentStepRecord.workspace_id == run.workspace_id,
                AgentStepRecord.status == AgentStepStatus.RUNNING,
            )
            .order_by(AgentStepRecord.sequence)
            .with_for_update()
        )
    )
    terminal_sequence = run.event_count + 1
    for step in running_steps:
        step.status = AgentStepStatus.CANCELLED if cancelled else AgentStepStatus.FAILED
        step.completed_at = occurred_at
        step.last_event_sequence = terminal_sequence
        step.error_code = None if cancelled else error_code

    event_type = AgentEventType.RUN_CANCELLED if cancelled else AgentEventType.RUN_FAILED
    stop_reason = RunStopReason.CANCELLED if cancelled else RunStopReason.RUNTIME_ERROR
    await _append_locked_agent_event(
        session,
        run,
        AgentEvent(
            schema_version=run.schema_version,
            stream_id=run.event_stream_id,
            run_id=run.id,
            workspace_id=run.workspace_id,
            sequence=terminal_sequence,
            occurred_at=occurred_at,
            trace_id=TraceId(run.trace_id),
            event_type=event_type,
            payload={
                "stop_reason": stop_reason.value,
                "error_code": error_code,
                "settled_step_ids": [str(step.id) for step in running_steps],
                "state_revision": run.state_revision + 1,
            },
        ),
    )


def _valid_error_code(value: str) -> bool:
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789._-"
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 100
        and value[0] in allowed[:36]
        and all(character in allowed for character in value)
    )


async def _append_locked_agent_event(
    session: AsyncSession,
    run: AgentRunRecord,
    event: AgentEvent,
) -> None:
    """Append and project one Event while the caller holds the Run row lock."""

    if (
        run.id != event.run_id
        or run.workspace_id != event.workspace_id
        or run.event_stream_id != event.stream_id
        or run.trace_id != str(event.trace_id)
        or run.schema_version != event.schema_version
    ):
        raise AgentEventPersistenceError()

    existing = await session.scalar(
        select(AgentEventRecord).where(
            AgentEventRecord.stream_id == event.stream_id,
            AgentEventRecord.sequence == event.sequence,
        )
    )
    if existing is not None:
        if not _same_event(existing, event):
            raise AgentEventPersistenceError()
        return
    if run.status in TERMINAL_RUN_STATUSES:
        raise AgentEventPersistenceError()
    if event.sequence != run.event_count + 1 or event.occurred_at < run.updated_at:
        raise AgentEventPersistenceError()

    session.add(
        AgentEventRecord(
            workspace_id=event.workspace_id,
            run_id=event.run_id,
            stream_id=event.stream_id,
            sequence=event.sequence,
            occurred_at=event.occurred_at,
            trace_id=str(event.trace_id),
            schema_version=event.schema_version,
            event_type=event.event_type,
            payload=_plain_json_mapping(event.payload),
        )
    )
    await SqlAlchemyAgentEventCommitter._project(session, run, event)
    run.event_count = event.sequence
    run.updated_at = event.occurred_at


def _cancel_unstarted_job(
    session: AsyncSession,
    job: Job,
    *,
    terminal_at: datetime,
) -> None:
    """Settle a Job that never acquired a Worker lease in the current transaction."""

    job.status = JobStatus.CANCELLED
    job.terminal_at = terminal_at
    job.stage_name = JobStatus.CANCELLED.value
    job.stage_sequence += 1
    job.last_error_code = None
    job.updated_at = terminal_at
    session.add(
        JobEvent(
            id=uuid4(),
            job_id=job.id,
            event_type=JobEventType.CANCELLED,
            generation=job.generation,
            dispatch_generation=job.dispatch_generation,
            fencing_token=job.fencing_token,
            event_sequence=job.stage_sequence,
            occurred_at=terminal_at,
            details={"source": "agent_run_cancel"},
        )
    )


def _terminalize_stranded_job(
    session: AsyncSession,
    job: Job,
    *,
    occurred_at: datetime,
    error_code: str,
    cancelled: bool,
) -> None:
    """Settle a non-terminal Job whose Day 2 Run cannot be resumed."""

    outcome = JobStatus.CANCELLED if cancelled else JobStatus.FAILED
    job.status = outcome
    job.terminal_at = occurred_at
    job.stage_name = outcome.value
    job.stage_sequence += 1
    job.last_error_code = None if cancelled else error_code
    job.lease_owner = None
    job.lease_token = None
    job.lease_expires_at = None
    job.heartbeat_at = None
    job.result = None
    job.updated_at = occurred_at
    session.add(
        JobEvent(
            id=uuid4(),
            job_id=job.id,
            event_type=JobEventType(outcome.value),
            generation=job.generation,
            dispatch_generation=job.dispatch_generation,
            fencing_token=job.fencing_token,
            event_sequence=job.stage_sequence,
            occurred_at=occurred_at,
            details={
                "source": "agent_run_reconciler",
                **({} if cancelled else {"error_code": error_code}),
            },
        )
    )


async def _locked_step(
    session: object, *, run_id: UUID, workspace_id: UUID, step_id: UUID
) -> AgentStepRecord:
    from sqlalchemy.ext.asyncio import AsyncSession

    if not isinstance(session, AsyncSession):
        raise AgentEventPersistenceError()
    step = await session.scalar(
        select(AgentStepRecord)
        .where(
            AgentStepRecord.id == step_id,
            AgentStepRecord.run_id == run_id,
            AgentStepRecord.workspace_id == workspace_id,
        )
        .with_for_update()
    )
    if step is None:
        raise AgentEventPersistenceError()
    return step


async def _settle_cancelled_step(session: object, run: AgentRunRecord, event: AgentEvent) -> None:
    value = event.payload.get("cancelled_step_id")
    if not isinstance(value, str):
        return
    step = await _locked_step(
        session,
        run_id=event.run_id,
        workspace_id=event.workspace_id,
        step_id=UUID(value),
    )
    if step.kind is AgentStepKind.TOOL:
        if not isinstance(session, AsyncSession):
            raise AgentEventPersistenceError()
        await _validate_cancelled_tool_step_projection(session, event, step=step)
    step.status = AgentStepStatus.CANCELLED
    step.completed_at = event.occurred_at
    step.last_event_sequence = event.sequence
    step.input_tokens = _optional_int(event.payload, "input_tokens") or 0
    step.output_tokens = _optional_int(event.payload, "output_tokens") or 0
    step.cost_micro_usd = _optional_int(event.payload, "cost_micro_usd") or 0
    if step.kind is AgentStepKind.MODEL:
        run.input_tokens_used += step.input_tokens
        run.output_tokens_used += step.output_tokens
        run.cached_input_tokens_used += _optional_int(event.payload, "cached_input_tokens") or 0
    run.cost_micro_usd += step.cost_micro_usd
    run.step_count = max(run.step_count, step.sequence)


async def _persist_final_message(
    session: object, run: AgentRunRecord, completed_at: datetime
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    if not isinstance(session, AsyncSession):
        raise AgentEventPersistenceError()
    records = tuple(
        await session.scalars(
            select(AgentEventRecord)
            .where(
                AgentEventRecord.run_id == run.id,
                AgentEventRecord.workspace_id == run.workspace_id,
                AgentEventRecord.event_type == AgentEventType.STEP_COMPLETED,
            )
            .order_by(AgentEventRecord.sequence.desc())
        )
    )
    final_payload = next(
        (
            record.payload
            for record in records
            if record.payload.get("step_kind") == AgentStepKind.FINAL.value
        ),
        None,
    )
    if final_payload is None:
        raise AgentEventPersistenceError()
    content = final_payload.get("content_markdown")
    if not isinstance(content, str) or not content.strip():
        raise AgentEventPersistenceError()
    session.add(
        Message(
            workspace_id=run.workspace_id,
            turn_id=run.turn_id,
            agent_run_id=run.id,
            created_by_user_id=None,
            role=MessageRole.ASSISTANT,
            status=MessageStatus.FINAL,
            content_markdown=content,
            created_at=completed_at,
            updated_at=completed_at,
        )
    )


async def _persist_partial_message(
    session: object,
    run: AgentRunRecord,
    terminal_event: AgentEvent,
) -> None:
    """Preserve committed Provider text when a Run fails or is cancelled."""

    if not isinstance(session, AsyncSession):
        raise AgentEventPersistenceError()
    content = await session.scalar(
        select(
            func.string_agg(
                AgentEventRecord.payload["delta"].astext,
                aggregate_order_by(literal(""), AgentEventRecord.sequence),
            )
        ).where(
            AgentEventRecord.run_id == run.id,
            AgentEventRecord.workspace_id == run.workspace_id,
            AgentEventRecord.sequence < terminal_event.sequence,
            AgentEventRecord.event_type == AgentEventType.MODEL_DELTA,
        )
    )
    if content is None or (isinstance(content, str) and not content.strip()):
        return
    if not isinstance(content, str):
        raise AgentEventPersistenceError()

    existing = tuple(
        await session.scalars(
            select(Message)
            .where(
                Message.agent_run_id == run.id,
                Message.workspace_id == run.workspace_id,
                Message.role == MessageRole.ASSISTANT,
            )
            .order_by(Message.created_at, Message.id)
            .with_for_update()
        )
    )
    if existing:
        if (
            len(existing) == 1
            and existing[0].status is MessageStatus.PARTIAL
            and existing[0].content_markdown == content
        ):
            return
        raise AgentEventPersistenceError()

    session.add(
        Message(
            workspace_id=run.workspace_id,
            turn_id=run.turn_id,
            agent_run_id=run.id,
            created_by_user_id=None,
            role=MessageRole.ASSISTANT,
            status=MessageStatus.PARTIAL,
            content_markdown=content,
            created_at=terminal_event.occurred_at,
            updated_at=terminal_event.occurred_at,
        )
    )


def _manifest_values(manifest: ContextManifest) -> dict[str, object]:
    return {
        "id": manifest.manifest_id,
        "workspace_id": manifest.workspace_id,
        "run_id": manifest.run_id,
        "step_id": manifest.step_id,
        "schema_version": manifest.schema_version,
        "compiler_version": manifest.compiler_version,
        "prompt_version": manifest.prompt_version,
        "runtime_projection_version": manifest.runtime_projection_version,
        "token_counter_version": manifest.token_counter_version,
        "budget": _plain_json_mapping(asdict(manifest.budget)),
        "sources": [_plain_json_mapping(asdict(source)) for source in manifest.sources],
        "created_at": manifest.created_at,
    }


def _manifest_record_values(record: ContextManifestRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "workspace_id": record.workspace_id,
        "run_id": record.run_id,
        "step_id": record.step_id,
        "schema_version": record.schema_version,
        "compiler_version": record.compiler_version,
        "prompt_version": record.prompt_version,
        "runtime_projection_version": record.runtime_projection_version,
        "token_counter_version": record.token_counter_version,
        "budget": record.budget,
        "sources": record.sources,
        "created_at": record.created_at,
    }


def _plain_json_mapping(value: Mapping[str, object]) -> dict[str, object]:
    return {str(key): _plain_json_value(item) for key, item in value.items()}


def _plain_json_value(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return _plain_json_mapping(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_plain_json_value(item) for item in value]
    if isinstance(value, UUID):
        return str(value)
    return value


def _same_event(record: AgentEventRecord, event: AgentEvent) -> bool:
    return (
        record.workspace_id == event.workspace_id
        and record.run_id == event.run_id
        and record.stream_id == event.stream_id
        and record.sequence == event.sequence
        and record.occurred_at == event.occurred_at
        and record.trace_id == str(event.trace_id)
        and record.schema_version == event.schema_version
        and record.event_type == event.event_type
        and record.payload == _plain_json_mapping(event.payload)
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


def _required_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise AgentEventPersistenceError()
    return value


def _required_uuid(payload: Mapping[str, object], key: str) -> UUID:
    try:
        return UUID(_required_str(payload, key))
    except ValueError:
        raise AgentEventPersistenceError() from None


def _required_int(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AgentEventPersistenceError()
    return value


def _optional_int(payload: Mapping[str, object], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AgentEventPersistenceError()
    return value


def _required_json_object(
    payload: Mapping[str, object],
    key: str,
) -> dict[str, object]:
    value = payload.get(key)
    if not isinstance(value, Mapping) or not all(isinstance(item, str) for item in value):
        raise AgentEventPersistenceError()
    return _plain_json_mapping(value)


def _required_json_object_list(
    payload: Mapping[str, object],
    key: str,
) -> list[dict[str, object]]:
    value = payload.get(key)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise AgentEventPersistenceError()
    result: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping) or not all(isinstance(name, str) for name in item):
            raise AgentEventPersistenceError()
        result.append(_plain_json_mapping(item))
    return result


def _required_iso_datetime(payload: Mapping[str, object], key: str) -> datetime:
    try:
        return datetime.fromisoformat(_required_str(payload, key))
    except ValueError:
        raise AgentEventPersistenceError() from None


def _required_sha256_hex(payload: Mapping[str, object], key: str) -> str:
    value = _required_str(payload, key)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise AgentEventPersistenceError()
    return value


def _required_sha256_bytes(payload: Mapping[str, object], key: str) -> bytes:
    value = _required_sha256_hex(payload, key)
    try:
        decoded = bytes.fromhex(value)
    except ValueError:
        raise AgentEventPersistenceError() from None
    if len(decoded) != 32:
        raise AgentEventPersistenceError()
    return decoded


def _optional_sha256_bytes(payload: Mapping[str, object], key: str) -> bytes | None:
    value = payload.get(key)
    if value is None:
        return None
    return _required_sha256_bytes(payload, key)
