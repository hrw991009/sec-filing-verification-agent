"""Worker-level orchestration for independent durable-fact reconciliation."""

from dataclasses import dataclass
from typing import cast

import pytest

from industry_platform.modules.agent_runtime.execution import AgentRunOrphanReconciler
from industry_platform.modules.data_explorer.ports import QueryRunReconciliationUseCase
from industry_platform.modules.ingestion.deletion import (
    DeletionReconciliationResult,
    DeletionReconciliationUseCase,
)
from industry_platform.modules.jobs.domain import JobReconciliationResult
from industry_platform.modules.jobs.ports import JobReconciliationUseCase
from industry_platform.workers.reconciler import JobReconciler


@dataclass(slots=True)
class RecordingJobReconciler:
    calls: int = 0

    async def reconcile_once(self) -> JobReconciliationResult:
        self.calls += 1
        return JobReconciliationResult(0, 0, 0, 0, 0, 0)


@dataclass(slots=True)
class RecordingAgentReconciler:
    batch_size: int | None = None

    async def reconcile_orphans(self, *, batch_size: int) -> int:
        self.batch_size = batch_size
        return 1


@dataclass(slots=True)
class RecordingQueryReconciler:
    batch_size: int | None = None

    async def reconcile_stale(self, *, batch_size: int) -> int:
        self.batch_size = batch_size
        return 2


@dataclass(slots=True)
class RecordingDeletionReconciler:
    batch_size: int | None = None

    async def reconcile_deletions(self, *, batch_size: int) -> DeletionReconciliationResult:
        self.batch_size = batch_size
        return DeletionReconciliationResult(selected=1, finalized=0, orphaned=1)


@pytest.mark.asyncio
async def test_one_tick_reconciles_jobs_agent_runs_query_runs_and_deletions() -> None:
    jobs = RecordingJobReconciler()
    agent_runs = RecordingAgentReconciler()
    query_runs = RecordingQueryReconciler()
    deletions = RecordingDeletionReconciler()
    reconciler = JobReconciler(
        cast(JobReconciliationUseCase, jobs),
        agent_runs=cast(AgentRunOrphanReconciler, agent_runs),
        query_runs=cast(QueryRunReconciliationUseCase, query_runs),
        knowledge_deletions=cast(DeletionReconciliationUseCase, deletions),
        agent_batch_size=17,
    )

    result = await reconciler.reconcile_once()

    assert result.selected == 0
    assert jobs.calls == 1
    assert agent_runs.batch_size == 17
    assert query_runs.batch_size == 17
    assert deletions.batch_size == 17
