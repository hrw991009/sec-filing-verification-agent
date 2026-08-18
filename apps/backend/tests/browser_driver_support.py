"""Shared formal Job/Outbox setup for browser-created Agent Run drivers."""

import argparse
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from industry_platform.core.config import Settings
from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.agent_runtime.domain import AgentRunStatus
from industry_platform.modules.agent_runtime.models import AgentRunRecord
from industry_platform.modules.jobs.adapters.sqlalchemy import SqlAlchemyOutboxTransactionFactory
from industry_platform.modules.jobs.domain import ClaimedJobDispatch, JobStatus, OutboxStatus
from industry_platform.modules.jobs.models import Job, OutboxEvent
from industry_platform.workers.dispatcher import OutboxDispatcher


class BrowserSuccessDriverError(RuntimeError):
    """One expected formal-path fact was absent or inconsistent."""


def non_nil_uuid(value: str) -> UUID:
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Expected a UUID") from error
    if parsed.int == 0:
        raise argparse.ArgumentTypeError("Expected a non-zero UUID")
    return parsed


@dataclass(slots=True)
class TargetOutboxPublisher:
    """Capture the target selected while every unrelated Outbox row is locked."""

    target_job_id: UUID
    delivery: ClaimedJobDispatch | None = None

    async def publish(self, dispatch: ClaimedJobDispatch) -> None:
        if dispatch.message.job_id != self.target_job_id:
            raise BrowserSuccessDriverError("The scoped Dispatcher selected an unrelated Outbox")
        if self.delivery is not None:
            raise BrowserSuccessDriverError("The target Outbox was published more than once")
        self.delivery = dispatch


async def require_pending_target(
    session_factory: AsyncSessionFactory,
    *,
    run_id: UUID,
    job_id: UUID,
) -> UUID:
    async with session_factory() as session:
        run = await session.get(AgentRunRecord, run_id)
        job = await session.get(Job, job_id)
        outbox_ids = tuple(
            await session.scalars(
                select(OutboxEvent.id)
                .where(
                    OutboxEvent.source_job_id == job_id,
                    OutboxEvent.status == OutboxStatus.PENDING,
                )
                .order_by(OutboxEvent.id)
            )
        )
    if (
        run is None
        or job is None
        or run.job_id != job_id
        or run.status is not AgentRunStatus.QUEUED
        or job.status is not JobStatus.PENDING
        or len(outbox_ids) != 1
    ):
        raise BrowserSuccessDriverError("The browser-created Run, Job and Outbox are not pending")
    return outbox_ids[0]


async def claim_target_delivery(
    settings: Settings,
    session_factory: AsyncSessionFactory,
    *,
    job_id: UUID,
    outbox_id: UUID,
) -> ClaimedJobDispatch:
    publisher = TargetOutboxPublisher(job_id)
    dispatcher = OutboxDispatcher(
        transaction_factory=SqlAlchemyOutboxTransactionFactory(session_factory),
        publisher=publisher,
        dispatcher_id=f"e2e-browser-success-{job_id.hex}",
        batch_size=1,
        claim_seconds=settings.outbox_claim_seconds,
    )
    async with session_factory.begin() as isolation_session:
        tuple(
            await isolation_session.scalars(
                select(OutboxEvent.id).where(OutboxEvent.id != outbox_id).with_for_update()
            )
        )
        result = await dispatcher.dispatch_once()
    if (
        result.claimed != 1
        or result.published != 1
        or result.retry_scheduled != 0
        or result.dead_lettered != 0
        or result.claim_lost != 0
        or publisher.delivery is None
        or publisher.delivery.proof.outbox_id != outbox_id
    ):
        raise BrowserSuccessDriverError("The scoped Dispatcher did not publish only the target")
    return publisher.delivery
