"""Disposable-database-only process used to prove hard termination after a checkpoint."""

import argparse
import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select

from industry_platform.core.config import Settings
from industry_platform.core.database import create_database_engine, create_database_session_factory
from industry_platform.modules.agent_runtime.adapters.checkpoints import SqlAlchemyCheckpointStore
from industry_platform.modules.agent_runtime.adapters.persistence import (
    SqlAlchemyAgentEventCommitter,
)
from industry_platform.modules.agent_runtime.checkpoints import (
    LoadCheckpointRequest,
    SaveCheckpointCommand,
)
from industry_platform.modules.agent_runtime.domain import AgentRunStatus
from industry_platform.modules.agent_runtime.events import AgentEvent, AgentEventType
from industry_platform.modules.agent_runtime.models import AgentRunRecord
from industry_platform.modules.agent_runtime.state import RunState
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.jobs.adapters.sqlalchemy import SqlAlchemyJobTransactionFactory
from industry_platform.modules.jobs.domain import (
    AcquireJobCommand,
    CheckpointJobCommand,
    FinishJobCommand,
    JobNotAcquirableError,
    JobStatus,
)
from industry_platform.modules.jobs.service import JobApplicationService
from industry_platform.server import create_selector_event_loop


async def exercise(job_id: UUID, generation: int) -> None:
    settings = Settings(_env_file=Path(".env"))
    if not settings.postgres_db.startswith("iip_postgres_test_"):
        raise ValueError("Recovery worker requires a disposable test database")
    engine = create_database_engine(settings)
    factory = create_database_session_factory(engine)
    jobs = JobApplicationService(SqlAlchemyJobTransactionFactory(factory), lease_seconds=5)
    try:
        try:
            job = await jobs.acquire(
                AcquireJobCommand(
                    job_id=job_id,
                    dispatch_generation=generation,
                    worker_id=f"process-{generation}",
                )
            )
        except JobNotAcquirableError:
            return
        async with factory() as session:
            run = await session.scalar(
                select(AgentRunRecord).where(AgentRunRecord.job_id == job_id)
            )
        if run is None:
            raise ValueError("Recovery Job must own a real Agent Run")
        checkpoints = SqlAlchemyCheckpointStore(factory)
        committer = SqlAlchemyAgentEventCommitter(factory)
        now = datetime.now(UTC)
        if generation == 1:
            await committer.append(
                AgentEvent(
                    schema_version=1,
                    stream_id=run.event_stream_id,
                    run_id=run.id,
                    workspace_id=run.workspace_id,
                    sequence=2,
                    occurred_at=now,
                    trace_id=TraceId(run.trace_id),
                    event_type=AgentEventType.RUN_STARTED,
                    payload={"state_revision": 1},
                )
            )
            await checkpoints.save(
                SaveCheckpointCommand(
                    run_id=run.id,
                    workspace_id=run.workspace_id,
                    expected_revision=None,
                    checkpoint_revision=0,
                    state=RunState(
                        schema_version=1,
                        run_id=run.id,
                        workspace_id=run.workspace_id,
                        revision=1,
                        status=AgentRunStatus.RUNNING,
                        step_count=0,
                        event_count=2,
                        input_tokens_used=0,
                        output_tokens_used=0,
                        cost_micro_usd=0,
                        updated_at=now,
                    ),
                    payload={
                        "controlled_recovery_result": "Checkpoint survived process termination."
                    },
                )
            )
            await jobs.checkpoint(
                CheckpointJobCommand(
                    proof=job.lease_proof,
                    stage_name="durable-before-kill",
                    stage_sequence=job.stage_sequence + 1,
                )
            )
            # Bound a stranded test process even if its parent pytest is terminated.
            await asyncio.wait_for(asyncio.Event().wait(), timeout=60)
        else:
            envelope = await checkpoints.load(LoadCheckpointRequest(run.id, run.workspace_id))
            result = envelope.payload["controlled_recovery_result"]
            step_id = uuid4()
            events = (
                (
                    AgentEventType.STEP_STARTED,
                    {
                        "step_id": str(step_id),
                        "step_kind": "final",
                        "step_sequence": 1,
                    },
                ),
                (
                    AgentEventType.STEP_COMPLETED,
                    {
                        "step_id": str(step_id),
                        "step_kind": "final",
                        "cost_micro_usd": 0,
                        "content_markdown": result,
                    },
                ),
                (AgentEventType.RUN_COMPLETED, {"stop_reason": "final", "state_revision": 2}),
            )
            await committer.append_batch(
                tuple(
                    AgentEvent(
                        schema_version=1,
                        stream_id=run.event_stream_id,
                        run_id=run.id,
                        workspace_id=run.workspace_id,
                        sequence=3 + offset,
                        occurred_at=now,
                        trace_id=TraceId(run.trace_id),
                        event_type=kind,
                        payload=payload,
                    )
                    for offset, (kind, payload) in enumerate(events)
                )
            )
            await jobs.finish(
                FinishJobCommand(
                    proof=job.lease_proof,
                    outcome=JobStatus.SUCCEEDED,
                    result={"run_id": str(run.id), "checkpoint_id": str(envelope.checkpoint_id)},
                )
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", type=UUID, required=True)
    parser.add_argument("--generation", type=int, choices=(1, 2), required=True)
    args = parser.parse_args()
    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise(args.job_id, args.generation))
