"""Same-Job replay with live authorization, fencing and retained dead-letter history."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

from industry_platform.core.database import create_database_engine, create_database_session_factory
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.identity.models import (
    User,
    UserStatus,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
    WorkspaceStatus,
)
from industry_platform.modules.jobs.adapters.recovery import SqlAlchemyJobRecoveryRepository
from industry_platform.modules.jobs.domain import (
    AcquireJobCommand,
    ExecutionScope,
    FinishJobCommand,
    HeartbeatJobCommand,
    JobDefinition,
    JobEventType,
    JobExecutionErrorCode,
    JobStatus,
    LostJobLeaseError,
    RetryJobCommand,
    SubmitJobCommand,
)
from industry_platform.modules.jobs.models import Job, JobEvent, OutboxEvent
from industry_platform.modules.jobs.recovery import (
    JobRecoveryConflictError,
    JobRecoveryNotFoundError,
    JobRecoveryService,
    ReplayDeadLetter,
)
from industry_platform.modules.workspaces.domain import WorkspaceAccessDeniedError, WorkspaceScope
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe
from .test_jobs_postgres import job_service


def test_authorized_replay_keeps_identity_and_history_and_rejects_stale_owner(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        factory = create_database_session_factory(engine)
        jobs = job_service(factory)
        recovery = JobRecoveryService(SqlAlchemyJobRecoveryRepository(factory))
        user_id, workspace_id = uuid4(), uuid4()
        scope = WorkspaceScope(workspace_id, user_id, "owner")
        now = datetime.now(UTC)
        try:
            async with factory.begin() as session:
                session.add(
                    User(
                        id=user_id,
                        email=f"recovery-{user_id}@example.test",
                        password_hash=str(user_id),
                        status=UserStatus.ACTIVE,
                        password_changed_at=now,
                    )
                )
                await session.flush()
                session.add(
                    Workspace(
                        id=workspace_id,
                        name="Recovery isolation",
                        created_by_user_id=user_id,
                        status=WorkspaceStatus.ACTIVE,
                    )
                )
                await session.flush()
                session.add(
                    WorkspaceMembership(
                        id=uuid4(),
                        workspace_id=workspace_id,
                        user_id=user_id,
                        role=WorkspaceRole.OWNER,
                    )
                )
            submitted = await jobs.submit(
                SubmitJobCommand(
                    definition=JobDefinition(
                        scope=ExecutionScope(workspace_id=workspace_id),
                        task_name="recovery.controlled",
                        queue_name="default",
                        payload={"identity": "original"},
                        available_at=now,
                        max_attempts=1,
                        idempotency_key="original-business-key",
                    ),
                    trace_id=TraceId("recovery-test"),
                )
            )
            first = await jobs.acquire(
                AcquireJobCommand(job_id=submitted.job_id, dispatch_generation=1, worker_id="old")
            )
            await jobs.retry(
                RetryJobCommand(
                    proof=first.lease_proof,
                    error_code=JobExecutionErrorCode.INGESTION_DEPENDENCY_RETRYABLE,
                    retry_delay_seconds=1,
                )
            )
            async with factory() as session:
                dead = await session.get(Job, submitted.job_id)
                assert dead is not None
                assert dead.status is JobStatus.DEAD_LETTER
                original_hash = dead.idempotency_key_hash
                original_fingerprint = dead.request_fingerprint
            command = ReplayDeadLetter(
                job_id=submitted.job_id,
                expected_dispatch_generation=1,
                additional_attempts=1,
                idempotency_key="operator-replay-key",
                trace_id=TraceId("owner-replay"),
            )
            with pytest.raises(WorkspaceAccessDeniedError):
                await recovery.replay(WorkspaceScope(workspace_id, user_id, "member"), command)
            with pytest.raises(JobRecoveryNotFoundError):
                await recovery.replay(
                    scope,
                    ReplayDeadLetter(
                        job_id=uuid4(),
                        expected_dispatch_generation=1,
                        additional_attempts=1,
                        idempotency_key="missing-job",
                        trace_id=command.trace_id,
                    ),
                )
            receipts = await asyncio.gather(
                recovery.replay(scope, command), recovery.replay(scope, command)
            )
            assert sum(item.created for item in receipts) == 1
            assert len({item.outbox_event_id for item in receipts}) == 1
            assert all(item.job_id == submitted.job_id for item in receipts)
            assert all(item.dispatch_generation == 2 for item in receipts)
            with pytest.raises(JobRecoveryConflictError):
                await recovery.replay(
                    scope,
                    ReplayDeadLetter(
                        job_id=submitted.job_id,
                        expected_dispatch_generation=1,
                        additional_attempts=2,
                        idempotency_key=command.idempotency_key,
                        trace_id=command.trace_id,
                    ),
                )
            with pytest.raises(LostJobLeaseError):
                await jobs.heartbeat(HeartbeatJobCommand(proof=first.lease_proof))
            replacement = await jobs.acquire(
                AcquireJobCommand(job_id=submitted.job_id, dispatch_generation=2, worker_id="new")
            )
            assert replacement.lease_proof.fencing_token > first.lease_proof.fencing_token
            await jobs.finish(
                FinishJobCommand(
                    proof=replacement.lease_proof,
                    outcome=JobStatus.SUCCEEDED,
                    result={"effect_identity": "original"},
                )
            )
            assert (await recovery.replay(scope, command)).created is False
            async with factory() as session:
                final = await session.get(Job, submitted.job_id)
                assert final is not None
                assert final.status is JobStatus.SUCCEEDED
                assert final.attempt_count == final.max_attempts == 2
                assert final.idempotency_key_hash == original_hash
                assert final.request_fingerprint == original_fingerprint
                assert await session.scalar(select(func.count()).select_from(Job)) == 1
                assert await session.scalar(select(func.count()).select_from(OutboxEvent)) == 2
                terminal = tuple(
                    await session.scalars(
                        select(JobEvent.event_type).where(
                            JobEvent.event_type.in_(
                                (JobEventType.DEAD_LETTER, JobEventType.SUCCEEDED)
                            )
                        )
                    )
                )
                assert set(terminal) == {JobEventType.DEAD_LETTER, JobEventType.SUCCEEDED}
                assert len(terminal) == 2
            async with factory.begin() as session:
                await session.execute(
                    update(WorkspaceMembership)
                    .where(WorkspaceMembership.workspace_id == workspace_id)
                    .values(role=WorkspaceRole.MEMBER)
                )
            # A cached/forged owner scope cannot bypass the live membership check,
            # including on an otherwise idempotent replay.
            with pytest.raises(WorkspaceAccessDeniedError):
                await recovery.replay(scope, command)
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())
