"""SQLAlchemy adapters for committed Events, manifests, replay, and cancellation."""

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from industry_platform.core.database import AsyncSessionFactory, safe_sqlstate
from industry_platform.modules.agent_runtime.context import ContextManifest
from industry_platform.modules.agent_runtime.delivery import (
    AgentRunDeliveryUnavailableError,
    AgentRunStreamDescriptor,
)
from industry_platform.modules.agent_runtime.domain import (
    AgentRunStatus,
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
from industry_platform.modules.agent_runtime.streaming import CommittedEventWindow
from industry_platform.modules.conversations.models import Message, MessageRole, MessageStatus
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.jobs.domain import TERMINAL_JOB_STATUSES, JobEventType, JobStatus
from industry_platform.modules.jobs.models import Job, JobEvent


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
        try:
            async with self.session_factory.begin() as session:
                run = await session.scalar(
                    select(AgentRunRecord)
                    .where(
                        AgentRunRecord.id == event.run_id,
                        AgentRunRecord.workspace_id == event.workspace_id,
                    )
                    .with_for_update()
                )
                if run is None:
                    raise AgentEventPersistenceError()
                await _append_locked_agent_event(session, run, event)
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
            step.status = (
                AgentStepStatus.COMPLETED
                if event.event_type is AgentEventType.STEP_COMPLETED
                else AgentStepStatus.FAILED
            )
            step.completed_at = event.occurred_at
            step.last_event_sequence = event.sequence
            step.input_tokens = _optional_int(payload, "input_tokens") or 0
            step.output_tokens = _optional_int(payload, "output_tokens") or 0
            step.cost_micro_usd = _optional_int(payload, "cost_micro_usd") or 0
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
        if event.event_type in {
            AgentEventType.RUN_COMPLETED,
            AgentEventType.RUN_FAILED,
            AgentEventType.RUN_CANCELLED,
        }:
            run.status = {
                AgentEventType.RUN_COMPLETED: AgentRunStatus.COMPLETED,
                AgentEventType.RUN_FAILED: AgentRunStatus.FAILED,
                AgentEventType.RUN_CANCELLED: AgentRunStatus.CANCELLED,
            }[event.event_type]
            run.stop_reason = RunStopReason(_required_str(payload, "stop_reason"))
            run.terminal_at = event.occurred_at
            if event.event_type is AgentEventType.RUN_CANCELLED:
                await _settle_cancelled_step(session, run, event)
            if event.event_type is AgentEventType.RUN_COMPLETED:
                await _persist_final_message(session, run, event.occurred_at)


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
                            payload={"stop_reason": RunStopReason.CANCELLED.value},
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
                records = tuple(
                    await session.scalars(
                        select(AgentEventRecord)
                        .where(
                            AgentEventRecord.stream_id == stream_id,
                            AgentEventRecord.workspace_id == workspace_id,
                        )
                        .order_by(AgentEventRecord.sequence)
                    )
                )
            events = tuple(_to_domain_event(record) for record in records)
            latest = events[-1].sequence if events else 0
            return CommittedEventWindow(
                stream_id=stream_id,
                workspace_id=workspace_id,
                earliest_available_sequence=1 if events else 0,
                latest_committed_sequence=latest,
                events=events,
            )
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
    step.status = AgentStepStatus.CANCELLED
    step.completed_at = event.occurred_at
    step.last_event_sequence = event.sequence
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
