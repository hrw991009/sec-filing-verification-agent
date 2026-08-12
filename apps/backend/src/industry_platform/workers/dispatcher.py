"""Independent PostgreSQL-outbox to Celery dispatcher process."""

import asyncio
import logging
from dataclasses import dataclass
from uuid import uuid4

from industry_platform.core.config import Settings, get_settings
from industry_platform.core.database import (
    create_database_engine,
    create_database_session_factory,
)
from industry_platform.modules.jobs.adapters.celery import (
    CeleryJobDispatchPublisher,
)
from industry_platform.modules.jobs.adapters.sqlalchemy import (
    SqlAlchemyOutboxTransactionFactory,
)
from industry_platform.modules.jobs.domain import (
    ClaimOutboxCommand,
    OutboxFailureDisposition,
    OutboxPersistenceError,
    OutboxPublishError,
    outbox_retry_delay_seconds,
)
from industry_platform.modules.jobs.ports import (
    JobDispatchPublisher,
    OutboxTransactionFactory,
)
from industry_platform.server import create_selector_event_loop
from industry_platform.workers.celery_app import create_celery_app

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DispatchBatchResult:
    """Observable, payload-free result of one bounded dispatcher scan."""

    claimed: int
    published: int
    retry_scheduled: int
    dead_lettered: int
    claim_lost: int


@dataclass(frozen=True, slots=True)
class OutboxDispatcher:
    """Publish only after claim commit, then CAS-settle in a new transaction."""

    transaction_factory: OutboxTransactionFactory
    publisher: JobDispatchPublisher
    dispatcher_id: str
    batch_size: int
    claim_seconds: int

    def __post_init__(self) -> None:
        ClaimOutboxCommand(
            dispatcher_id=self.dispatcher_id,
            batch_size=self.batch_size,
            claim_seconds=self.claim_seconds,
        )

    async def dispatch_once(self) -> DispatchBatchResult:
        command = ClaimOutboxCommand(
            dispatcher_id=self.dispatcher_id,
            batch_size=self.batch_size,
            claim_seconds=self.claim_seconds,
        )
        async with self.transaction_factory() as writer:
            dispatches = await writer.claim_job_dispatches(command)

        published = 0
        retry_scheduled = 0
        dead_lettered = 0
        claim_lost = 0

        for dispatch in dispatches:
            try:
                await self.publisher.publish(dispatch)
            except OutboxPublishError as error:
                retry_delay = outbox_retry_delay_seconds(
                    dispatch.proof.outbox_id,
                    dispatch.attempt_count,
                )
                async with self.transaction_factory() as writer:
                    disposition = await writer.mark_failed(
                        dispatch.proof,
                        error_code=error.error_code,
                        retry_delay_seconds=retry_delay,
                    )

                if disposition is OutboxFailureDisposition.RETRY_SCHEDULED:
                    retry_scheduled += 1
                elif disposition is OutboxFailureDisposition.DEAD_LETTER:
                    dead_lettered += 1
                else:
                    claim_lost += 1
                continue

            async with self.transaction_factory() as writer:
                retained = await writer.mark_published(dispatch.proof)
            if retained:
                published += 1
            else:
                claim_lost += 1

        return DispatchBatchResult(
            claimed=len(dispatches),
            published=published,
            retry_scheduled=retry_scheduled,
            dead_lettered=dead_lettered,
            claim_lost=claim_lost,
        )

    async def run_forever(
        self,
        *,
        idle_sleep_seconds: float = 1.0,
    ) -> None:
        """Continuously drain batches, backing off only while idle or DB-unavailable."""

        if idle_sleep_seconds <= 0:
            raise ValueError("Dispatcher idle sleep must be positive")

        while True:
            try:
                result = await self.dispatch_once()
            except OutboxPersistenceError as error:
                logger.error(
                    "Outbox persistence unavailable dispatcher_id=%s sqlstate=%s",
                    self.dispatcher_id,
                    error.sqlstate or "unknown",
                )
                await asyncio.sleep(idle_sleep_seconds)
                continue

            if result.claimed == 0:
                await asyncio.sleep(idle_sleep_seconds)


async def run_dispatcher(settings: Settings) -> None:
    """Compose and run one dispatcher with independently owned resources."""

    engine = create_database_engine(settings)
    celery_app = create_celery_app(settings)
    try:
        session_factory = create_database_session_factory(engine)
        dispatcher = OutboxDispatcher(
            transaction_factory=SqlAlchemyOutboxTransactionFactory(session_factory),
            publisher=CeleryJobDispatchPublisher(celery_app),
            dispatcher_id=f"dispatcher-{uuid4().hex}",
            batch_size=settings.outbox_dispatch_batch_size,
            claim_seconds=settings.outbox_claim_seconds,
        )
        await dispatcher.run_forever()
    finally:
        celery_app.close()
        await engine.dispose()


def main() -> None:
    """Run the standalone dispatcher until interrupted by its process supervisor."""

    logging.basicConfig(level=logging.INFO)
    try:
        with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
            runner.run(run_dispatcher(get_settings()))
    except KeyboardInterrupt:
        logger.info("Outbox dispatcher stopped")


if __name__ == "__main__":
    main()
