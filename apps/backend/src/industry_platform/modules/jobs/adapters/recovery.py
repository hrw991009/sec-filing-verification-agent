"""Atomic owner recheck, replay audit and same-Job Outbox creation."""

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from industry_platform.core.database import AsyncSessionFactory, safe_sqlstate
from industry_platform.modules.agent_runtime.domain import AgentRunStatus
from industry_platform.modules.agent_runtime.models import AgentRunRecord
from industry_platform.modules.jobs.domain import (
    JOB_DISPATCH_OUTBOX_EVENT_TYPE,
    JOB_DISPATCH_OUTBOX_TOPIC,
    JobEventType,
    JobPersistenceError,
    JobStatus,
    JobSubmissionRecord,
    OutboxStatus,
    hash_job_idempotency_key,
)
from industry_platform.modules.jobs.models import Job, JobEvent, OutboxEvent
from industry_platform.modules.jobs.recovery import (
    JobRecoveryConflictError,
    JobRecoveryNotFoundError,
    ReplayDeadLetter,
)
from industry_platform.modules.workspaces.adapters.authorization import require_current_owner
from industry_platform.modules.workspaces.domain import WorkspaceScope


@dataclass(frozen=True, slots=True)
class SqlAlchemyJobRecoveryRepository:
    session_factory: AsyncSessionFactory

    async def replay(self, scope: WorkspaceScope, command: ReplayDeadLetter) -> JobSubmissionRecord:
        try:
            async with self.session_factory() as session, session.begin():
                await require_current_owner(session, scope)
                job = await session.scalar(
                    select(Job)
                    .where(Job.id == command.job_id, Job.workspace_id == scope.workspace_id)
                    .with_for_update()
                )
                if job is None:
                    raise JobRecoveryNotFoundError
                replay_hash = hash_job_idempotency_key(command.idempotency_key).hex()
                previous = await session.scalar(
                    select(JobEvent).where(
                        JobEvent.job_id == job.id,
                        JobEvent.event_type == JobEventType.RETRY_SCHEDULED,
                        JobEvent.details["replay_key_sha256"].as_string() == replay_hash,
                    )
                )
                if previous is not None:
                    if (
                        previous.details["previous_dispatch_generation"]
                        != command.expected_dispatch_generation
                        or previous.details["additional_attempts"] != command.additional_attempts
                    ):
                        raise JobRecoveryConflictError
                    return JobSubmissionRecord(
                        job_id=job.id,
                        outbox_event_id=UUID(str(previous.details["outbox_id"])),
                        status=job.status,
                        dispatch_generation=previous.dispatch_generation,
                        created=False,
                    )
                if (
                    job.status is not JobStatus.DEAD_LETTER
                    or job.dispatch_generation != command.expected_dispatch_generation
                    or job.cancel_requested_at is not None
                    or job.max_attempts + command.additional_attempts > 100
                ):
                    raise JobRecoveryConflictError
                # Replaying delivery must never resurrect a terminal business Run or
                # silently extend its original model/time budget.
                runs = tuple(
                    await session.scalars(
                        select(AgentRunRecord)
                        .where(AgentRunRecord.job_id == job.id)
                        .with_for_update()
                    )
                )
                now = await session.scalar(select(func.clock_timestamp()))
                if now is None or any(
                    run.status not in (AgentRunStatus.QUEUED, AgentRunStatus.RUNNING)
                    for run in runs
                ):
                    raise JobRecoveryConflictError
                outbox_id = uuid4()
                job.max_attempts += command.additional_attempts
                job.dispatch_generation += 1
                job.dispatch_attempt = 0
                job.dispatched_at = None
                job.started_at = None
                job.terminal_at = None
                job.status = JobStatus.RETRY_WAIT
                job.stage_name = JobStatus.RETRY_WAIT.value
                job.stage_sequence += 1
                job.available_at = now
                job.updated_at = now
                session.add(
                    OutboxEvent(
                        id=outbox_id,
                        workspace_id=job.workspace_id,
                        source_job_id=job.id,
                        job_dispatch_generation=job.dispatch_generation,
                        topic=JOB_DISPATCH_OUTBOX_TOPIC,
                        event_type=JOB_DISPATCH_OUTBOX_EVENT_TYPE,
                        payload={
                            "job_id": str(job.id),
                            "outbox_id": str(outbox_id),
                            "dispatch_generation": job.dispatch_generation,
                            "trace_id": job.trace_id,
                        },
                        deduplication_key=f"job:{job.id}:dispatch:{job.dispatch_generation}",
                        status=OutboxStatus.PENDING,
                        next_attempt_at=now,
                    )
                )
                session.add(
                    JobEvent(
                        id=uuid4(),
                        job_id=job.id,
                        event_type=JobEventType.RETRY_SCHEDULED,
                        generation=job.generation,
                        dispatch_generation=job.dispatch_generation,
                        fencing_token=job.fencing_token,
                        event_sequence=job.stage_sequence,
                        occurred_at=now,
                        details={
                            "recovery": "owner_dead_letter_replay",
                            "actor_user_id": str(scope.user_id),
                            "trace_id": str(command.trace_id),
                            "replay_key_sha256": replay_hash,
                            "previous_dispatch_generation": command.expected_dispatch_generation,
                            "additional_attempts": command.additional_attempts,
                            "outbox_id": str(outbox_id),
                        },
                    )
                )
                await session.flush()
                return JobSubmissionRecord(
                    job_id=job.id,
                    outbox_event_id=outbox_id,
                    status=job.status,
                    dispatch_generation=job.dispatch_generation,
                    created=True,
                )
        except SQLAlchemyError as error:
            raise JobPersistenceError(sqlstate=safe_sqlstate(error)) from None
