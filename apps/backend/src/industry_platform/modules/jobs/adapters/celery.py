"""Celery publisher for the single generic job-execution entry task."""

from dataclasses import dataclass
from functools import partial

from anyio import to_thread
from celery import Celery

from industry_platform.modules.jobs.domain import (
    CELERY_JOB_DISPATCH_TASK_NAME,
    ClaimedJobDispatch,
    OutboxPublishError,
    OutboxPublishErrorCode,
)


@dataclass(frozen=True, slots=True)
class CeleryJobDispatchPublisher:
    """Publish fixed-shape coordinates without trusting outbox task metadata."""

    celery_app: Celery

    async def publish(self, dispatch: ClaimedJobDispatch) -> None:
        try:
            await to_thread.run_sync(
                partial(
                    self.celery_app.send_task,
                    CELERY_JOB_DISPATCH_TASK_NAME,
                    args=(),
                    kwargs=dispatch.message.as_json_kwargs(),
                    task_id=str(dispatch.message.outbox_id),
                    queue=dispatch.queue_name,
                    routing_key=dispatch.queue_name,
                    serializer="json",
                    ignore_result=True,
                    retry=False,
                    time_limit=dispatch.hard_time_limit_seconds,
                )
            )
        except Exception:
            raise OutboxPublishError(OutboxPublishErrorCode.CELERY_PUBLISH_FAILED) from None
