"""Independent PostgreSQL job-delivery and expired-lease reconciler."""

import asyncio
import logging
from dataclasses import dataclass

from industry_platform.core.config import Settings, get_settings
from industry_platform.core.database import (
    create_database_engine,
    create_database_session_factory,
)
from industry_platform.modules.agent_runtime.adapters.persistence import (
    AgentEventPersistenceError,
    SqlAlchemyAgentRunTerminalizer,
)
from industry_platform.modules.agent_runtime.execution import AgentRunOrphanReconciler
from industry_platform.modules.data_explorer.adapters.sqlalchemy import (
    SqlAlchemyDataExplorerRepository,
)
from industry_platform.modules.data_explorer.domain import DataExplorerPersistenceError
from industry_platform.modules.data_explorer.ports import QueryRunReconciliationUseCase
from industry_platform.modules.data_explorer.service import (
    StaleQueryRunReconciliationService,
)
from industry_platform.modules.jobs.adapters.sqlalchemy import (
    SqlAlchemyJobTransactionFactory,
)
from industry_platform.modules.jobs.domain import (
    JobPersistenceError,
    JobReconciliationResult,
)
from industry_platform.modules.jobs.ports import JobReconciliationUseCase
from industry_platform.modules.jobs.service import JobReconciliationService
from industry_platform.server import create_selector_event_loop

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class JobReconciler:
    """Continuously execute short, skip-locked recovery transactions."""

    service: JobReconciliationUseCase
    agent_runs: AgentRunOrphanReconciler | None = None
    query_runs: QueryRunReconciliationUseCase | None = None
    agent_batch_size: int = 100

    def __post_init__(self) -> None:
        if isinstance(self.agent_batch_size, bool) or not 1 <= self.agent_batch_size <= 1_000:
            raise ValueError("Agent reconciliation batch size is invalid")

    async def reconcile_once(self) -> JobReconciliationResult:
        result = await self.service.reconcile_once()
        if self.agent_runs is not None:
            terminalized = await self.agent_runs.reconcile_orphans(batch_size=self.agent_batch_size)
            if terminalized:
                logger.info("agent_run_reconciliation terminalized=%d", terminalized)
        if self.query_runs is not None:
            reconciled = await self.query_runs.reconcile_stale(batch_size=self.agent_batch_size)
            if reconciled:
                logger.info("query_run_reconciliation terminalized=%d", reconciled)
        return result

    async def run_forever(self, *, idle_sleep_seconds: float = 1.0) -> None:
        if idle_sleep_seconds <= 0:
            raise ValueError("Reconciler idle sleep must be positive")

        while True:
            try:
                result = await self.reconcile_once()
            except (
                AgentEventPersistenceError,
                DataExplorerPersistenceError,
                JobPersistenceError,
            ) as error:
                logger.error(
                    "Job or Agent Run reconciliation persistence unavailable sqlstate=%s",
                    error.sqlstate or "unknown",
                )
                await asyncio.sleep(idle_sleep_seconds)
                continue

            if result.selected == 0:
                await asyncio.sleep(idle_sleep_seconds)
                continue

            logger.info(
                "Job reconciliation committed selected=%d retry_scheduled=%d "
                "cancelled=%d dead_lettered=%d",
                result.selected,
                result.retry_scheduled,
                result.cancelled,
                result.dead_lettered,
            )


async def run_reconciler(settings: Settings) -> None:
    """Compose one reconciler with process-owned database resources."""

    engine = create_database_engine(settings)
    try:
        session_factory = create_database_session_factory(engine)
        service = JobReconciliationService(
            transaction_factory=SqlAlchemyJobTransactionFactory(session_factory),
            unstarted_timeout_seconds=settings.job_unstarted_timeout_seconds,
            batch_size=settings.job_reconcile_batch_size,
        )
        await JobReconciler(
            service,
            agent_runs=SqlAlchemyAgentRunTerminalizer(session_factory),
            query_runs=StaleQueryRunReconciliationService(
                SqlAlchemyDataExplorerRepository(session_factory),
                stale_after_seconds=settings.text2sql_query_stale_seconds,
            ),
            agent_batch_size=settings.job_reconcile_batch_size,
        ).run_forever()
    finally:
        await engine.dispose()


def main() -> None:
    """Run reconciliation until interrupted by its process supervisor."""

    logging.basicConfig(level=logging.INFO)
    try:
        with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
            runner.run(run_reconciler(get_settings()))
    except KeyboardInterrupt:
        logger.info("Job reconciler stopped")


if __name__ == "__main__":
    main()
