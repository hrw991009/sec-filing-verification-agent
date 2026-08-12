"""Independent PostgreSQL job-delivery and expired-lease reconciler."""

import asyncio
import logging
from dataclasses import dataclass

from industry_platform.core.config import Settings, get_settings
from industry_platform.core.database import (
    create_database_engine,
    create_database_session_factory,
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

    async def reconcile_once(self) -> JobReconciliationResult:
        return await self.service.reconcile_once()

    async def run_forever(self, *, idle_sleep_seconds: float = 1.0) -> None:
        if idle_sleep_seconds <= 0:
            raise ValueError("Reconciler idle sleep must be positive")

        while True:
            try:
                result = await self.reconcile_once()
            except JobPersistenceError as error:
                logger.error(
                    "Job reconciliation persistence unavailable sqlstate=%s",
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
        await JobReconciler(service).run_forever()
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
