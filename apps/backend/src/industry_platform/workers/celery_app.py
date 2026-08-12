"""JSON-only Celery application factory and worker process entry point."""

import sys
from functools import partial
from urllib.parse import quote

from celery import Celery

from industry_platform.core.config import Settings, get_settings
from industry_platform.modules.jobs.domain import CELERY_JOB_DISPATCH_TASK_NAME
from industry_platform.workers.runtime import run_job_delivery
from industry_platform.workers.tasks import register_job_execution_task


def build_celery_broker_url(settings: Settings) -> str:
    """Build an escaped Redis broker URL without exposing it in configuration logs."""

    password = quote(settings.redis_password.get_secret_value(), safe="")
    host = settings.redis_host
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"redis://:{password}@{host}:{settings.redis_port}/{settings.celery_broker_redis_db}"


def create_celery_app(settings: Settings) -> Celery:
    """Create the broker-only app; PostgreSQL remains the result source of truth."""

    default_queue = settings.job_default_queue
    app = Celery(
        "industry_platform",
        broker=build_celery_broker_url(settings),
        backend=None,
    )
    app.conf.update(
        accept_content=["json"],
        event_serializer="json",
        task_serializer="json",
        result_serializer="json",
        task_protocol=2,
        enable_utc=True,
        timezone="UTC",
        task_ignore_result=True,
        task_store_errors_even_if_ignored=False,
        task_track_started=False,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        task_acks_on_failure_or_timeout=True,
        worker_cancel_long_running_tasks_on_connection_loss=True,
        worker_prefetch_multiplier=settings.celery_worker_prefetch_multiplier,
        broker_transport_options={
            "visibility_timeout": (settings.celery_broker_visibility_timeout_seconds)
        },
        broker_connection_retry=True,
        broker_connection_retry_on_startup=True,
        task_publish_retry=False,
        task_soft_time_limit=None,
        task_time_limit=settings.job_default_hard_time_limit_seconds,
        task_default_queue=default_queue,
        task_default_exchange=default_queue,
        task_default_exchange_type="direct",
        task_default_routing_key=default_queue,
        task_routes={
            CELERY_JOB_DISPATCH_TASK_NAME: {
                "queue": default_queue,
                "routing_key": default_queue,
            }
        },
    )
    return app


def create_worker_celery_app(settings: Settings) -> Celery:
    """Create the worker app and explicitly install its production task."""

    app = create_celery_app(settings)
    register_job_execution_task(
        app,
        delivery_runner=partial(run_job_delivery, settings=settings),
    )
    return app


def main() -> None:
    """Run a Celery worker with explicit, side-effect-free task registration."""

    app = create_worker_celery_app(get_settings())
    try:
        app.worker_main(["worker", *sys.argv[1:]])
    finally:
        app.close()


if __name__ == "__main__":
    main()
