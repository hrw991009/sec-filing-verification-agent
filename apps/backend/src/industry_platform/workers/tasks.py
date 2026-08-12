"""Explicit registration for the single fixed-shape Celery execution task."""

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, Protocol, cast
from uuid import UUID

from celery import Celery

from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.jobs.domain import (
    CELERY_JOB_DISPATCH_TASK_NAME,
    JobDispatchMessage,
)
from industry_platform.server import create_selector_event_loop
from industry_platform.workers.runtime import JobExecutionDisposition

type JobDeliveryRunner = Callable[
    [JobDispatchMessage], Coroutine[Any, Any, JobExecutionDisposition]
]


class RegisteredJobTask(Protocol):
    """Typed surface used after crossing Celery's untyped decorator boundary."""

    name: str

    def run(
        self,
        *,
        job_id: str,
        dispatch_generation: int,
        outbox_id: str,
        trace_id: str,
    ) -> None: ...


type CeleryTaskDecorator = Callable[[Callable[..., None]], RegisteredJobTask]


def register_job_execution_task(
    app: Celery,
    *,
    delivery_runner: JobDeliveryRunner,
) -> RegisteredJobTask:
    """Register exactly one keyword-only broker contract on an explicit app."""

    existing = app.tasks.get(CELERY_JOB_DISPATCH_TASK_NAME)
    if existing is not None:
        return cast(RegisteredJobTask, existing)

    def execute_job(
        *,
        job_id: str,
        dispatch_generation: int,
        outbox_id: str,
        trace_id: str,
    ) -> None:
        """ACK validated no-ops and every outcome already committed in PostgreSQL."""

        if (
            not isinstance(job_id, str)
            or isinstance(dispatch_generation, bool)
            or not isinstance(dispatch_generation, int)
            or not isinstance(outbox_id, str)
            or not isinstance(trace_id, str)
        ):
            return

        try:
            delivery = JobDispatchMessage(
                job_id=UUID(job_id),
                dispatch_generation=dispatch_generation,
                outbox_id=UUID(outbox_id),
                trace_id=TraceId(trace_id),
            )
        except (TypeError, ValueError):
            return

        with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
            runner.run(delivery_runner(delivery))

    register = cast(
        CeleryTaskDecorator,
        app.task(
            name=CELERY_JOB_DISPATCH_TASK_NAME,
            ignore_result=True,
            shared=False,
            typing=True,
        ),
    )
    return register(execute_job)
