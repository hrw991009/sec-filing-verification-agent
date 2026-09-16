"""Kill an owned process after durable Run/Job checkpoints, then resume in a new process."""

import asyncio
import os
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, update

from industry_platform.core.database import create_database_engine, create_database_session_factory
from industry_platform.modules.agent_runtime.domain import AgentRunStatus, RunBudget
from industry_platform.modules.agent_runtime.models import AgentCheckpointRecord, AgentRunRecord
from industry_platform.modules.conversations.adapters.sqlalchemy import (
    SqlAlchemyDirectAnswerTurnTransactionFactory,
)
from industry_platform.modules.conversations.models import Message, MessageRole
from industry_platform.modules.conversations.service import ConversationApplicationService
from industry_platform.modules.jobs.domain import (
    HeartbeatJobCommand,
    JobLeaseProof,
    JobStatus,
    LostJobLeaseError,
)
from industry_platform.modules.jobs.models import Job, OutboxEvent
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe
from .test_conversation_agent_postgres import command, seed_workspace
from .test_jobs_postgres import job_service, reconciliation_service


def test_hard_killed_process_resumes_same_job_run_and_checkpoint_once(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        factory = create_database_session_factory(engine)
        child: subprocess.Popen[bytes] | None = None
        try:
            await seed_workspace(factory)
            now = datetime.now(UTC)
            receipt = await ConversationApplicationService(
                SqlAlchemyDirectAnswerTurnTransactionFactory(factory),
                clock=lambda: now,
            ).start_direct_answer(
                replace(
                    command(),
                    budget=RunBudget(
                        schema_version=1,
                        max_steps=2,
                        max_total_tokens=1_000,
                        max_cost_micro_usd=100_000,
                        deadline=now + timedelta(minutes=5),
                    ),
                )
            )
            base = (
                sys.executable,
                str(Path(__file__).parents[1] / "recovery_worker.py"),
                "--job-id",
                str(receipt.job_id),
                "--generation",
            )
            child = await asyncio.to_thread(
                subprocess.Popen,
                (*base, "1"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            async with asyncio.timeout(30):
                while True:
                    async with factory() as session:
                        job = await session.get(Job, receipt.job_id)
                        checkpoint = await session.scalar(
                            select(AgentCheckpointRecord).where(
                                AgentCheckpointRecord.run_id == receipt.run_id
                            )
                        )
                    if (
                        job is not None
                        and job.stage_name == "durable-before-kill"
                        and checkpoint is not None
                    ):
                        break
                    if child.poll() is not None:
                        _stdout, stderr = await asyncio.to_thread(child.communicate, timeout=5)
                        raise AssertionError(
                            "Recovery child exited before its checkpoint: "
                            + stderr.decode(errors="replace")[-4000:]
                        )
                    await asyncio.sleep(0.1)
            assert job.lease_owner is not None
            assert job.lease_token is not None
            old_proof = JobLeaseProof(job.id, job.lease_owner, job.lease_token, job.fencing_token)
            checkpoint_id = checkpoint.id
            child.kill()
            await asyncio.to_thread(child.communicate, timeout=10)
            assert child.returncode != 0
            reconciler = reconciliation_service(factory)
            assert job.lease_expires_at is not None
            await asyncio.sleep(
                max(0, (job.lease_expires_at - datetime.now(UTC)).total_seconds()) + 0.2
            )
            assert (await reconciler.reconcile_once()).retry_scheduled == 1
            jobs = job_service(factory)
            with pytest.raises(LostJobLeaseError):
                await jobs.heartbeat(HeartbeatJobCommand(old_proof))
            async with factory.begin() as session:
                due = datetime.now(UTC) - timedelta(seconds=1)
                await session.execute(
                    update(Job).where(Job.id == receipt.job_id).values(available_at=due)
                )
                await session.execute(
                    update(OutboxEvent)
                    .where(
                        OutboxEvent.source_job_id == receipt.job_id,
                        OutboxEvent.job_dispatch_generation == 2,
                    )
                    .values(next_attempt_at=due)
                )
            for _ in range(2):
                completed = await asyncio.to_thread(
                    subprocess.run,
                    (*base, "2"),
                    capture_output=True,
                    check=False,
                    timeout=30,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                assert completed.returncode == 0, completed.stderr.decode(errors="replace")
            async with factory() as session:
                final = await session.get(Job, receipt.job_id)
                run = await session.get(AgentRunRecord, receipt.run_id)
                assert final is not None
                assert final.status is JobStatus.SUCCEEDED
                assert run is not None
                assert run.status is AgentRunStatus.COMPLETED
                assert run.job_id == receipt.job_id
                assert final.result is not None
                assert final.result["checkpoint_id"] == str(checkpoint_id)
                assert await session.scalar(select(func.count()).select_from(AgentRunRecord)) == 1
                assert (
                    await session.scalar(select(func.count()).select_from(AgentCheckpointRecord))
                    == 1
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(Message)
                        .where(
                            Message.role == MessageRole.ASSISTANT,
                            Message.agent_run_id == receipt.run_id,
                        )
                    )
                    == 1
                )
        finally:
            if child is not None and child.poll() is None:
                child.kill()
                await asyncio.to_thread(child.communicate, timeout=10)
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())
