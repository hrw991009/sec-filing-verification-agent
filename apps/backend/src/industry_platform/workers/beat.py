"""PostgreSQL-backed Celery Beat scheduler without broker-side schedules."""

import asyncio
import sys
from dataclasses import dataclass
from typing import Final

from celery import Celery
from celery.beat import Scheduler
from sqlalchemy.ext.asyncio import AsyncEngine

from industry_platform.core.config import Settings, get_settings
from industry_platform.core.database import (
    create_database_engine,
    create_database_session_factory,
)
from industry_platform.modules.industry.adapters.sqlalchemy import (
    industry_collection_occurrence_observer,
)
from industry_platform.modules.jobs.adapters.sqlalchemy import (
    SqlAlchemyScheduleTransactionFactory,
)
from industry_platform.modules.jobs.domain import (
    ExecutionScope,
    ScheduleDefinition,
    ScheduleMisfirePolicy,
)
from industry_platform.modules.jobs.ports import ScheduleApplicationUseCase
from industry_platform.modules.jobs.service import ScheduleApplicationService
from industry_platform.server import create_selector_event_loop
from industry_platform.workers.celery_app import create_celery_app

SCHEDULE_BATCH_SIZE: Final = 5
REFRESH_CLEANUP_BATCH_SIZE: Final = 1_000
REFRESH_CLEANUP_SCHEDULE_NAME: Final = "identity-refresh-recovery-cleanup"
REFRESH_CLEANUP_TASK_NAME: Final = "identity.refresh_recovery.cleanup.v1"
DATABASE_SCHEDULER_PATH: Final = "industry_platform.workers.beat:DatabaseScheduleScheduler"


def default_refresh_cleanup_schedule(settings: Settings) -> ScheduleDefinition:
    """Return the stable system schedule that removes expired recovery envelopes."""

    return ScheduleDefinition(
        scope=ExecutionScope(system_scope_key="maintenance"),
        name=REFRESH_CLEANUP_SCHEDULE_NAME,
        task_name=REFRESH_CLEANUP_TASK_NAME,
        cron_expression="*/15 * * * *",
        timezone_name="Asia/Shanghai",
        payload={"batch_size": REFRESH_CLEANUP_BATCH_SIZE},
        queue_name=settings.job_default_queue,
        max_attempts=3,
        priority=0,
        soft_time_limit_seconds=settings.job_default_soft_time_limit_seconds,
        hard_time_limit_seconds=settings.job_default_hard_time_limit_seconds,
        misfire_policy=ScheduleMisfirePolicy.COALESCE_LATEST,
        catch_up_window_seconds=86_400,
        max_catch_up=100,
    )


@dataclass(frozen=True, slots=True)
class _ScheduleResources:
    engine: AsyncEngine
    service: ScheduleApplicationUseCase


async def _create_schedule_resources(settings: Settings) -> _ScheduleResources:
    """Create the engine and service from inside Beat's single event loop."""

    engine = create_database_engine(settings)
    try:
        session_factory = create_database_session_factory(engine)
        service = ScheduleApplicationService(
            transaction_factory=SqlAlchemyScheduleTransactionFactory(
                session_factory,
                occurrence_observer=industry_collection_occurrence_observer,
            ),
            batch_size=SCHEDULE_BATCH_SIZE,
        )
        return _ScheduleResources(engine=engine, service=service)
    except BaseException:
        await engine.dispose()
        raise


class DatabaseScheduleScheduler(Scheduler):  # type: ignore[misc]
    """Materialize due PostgreSQL schedules; never publish Celery tasks directly."""

    def __init__(
        self,
        app: Celery,
        schedule: object | None = None,
        max_interval: float | None = None,
        Producer: object | None = None,
        lazy: bool = False,
        sync_every_tasks: int | None = None,
        *,
        settings: Settings | None = None,
        service: ScheduleApplicationUseCase | None = None,
        **kwargs: object,
    ) -> None:
        del schedule, max_interval
        self._settings = settings if settings is not None else get_settings()
        self._runner: asyncio.Runner | None = None
        self._engine: AsyncEngine | None = None
        self._service: ScheduleApplicationUseCase | None = None
        self._injected_service = service
        self._closed = False
        super().__init__(
            app=app,
            schedule={},
            max_interval=self._settings.scheduler_scan_interval_seconds,
            Producer=Producer,
            lazy=lazy,
            sync_every_tasks=sync_every_tasks,
            **kwargs,
        )
        if not lazy:
            self._initialize()

    def setup_schedule(self) -> None:
        """Keep Celery's in-memory periodic-task registry permanently empty."""

        self.data.clear()
        self.app.conf.beat_schedule = {}

    def tick(self) -> float:
        """Commit one bounded database scan and choose the next polling delay."""

        if self._runner is None or self._service is None:
            raise RuntimeError("Database schedule runner is not initialized")
        result = self._runner.run(self._service.run_due_once())
        if result.selected_schedules >= SCHEDULE_BATCH_SIZE:
            return 0.0
        return float(self._settings.scheduler_scan_interval_seconds)

    def close(self) -> None:
        """Dispose the engine on its owning loop and then close that loop."""

        if self._closed:
            return
        self._closed = True
        runner = self._runner
        try:
            if runner is not None and self._engine is not None:
                runner.run(self._engine.dispose())
        finally:
            if runner is not None:
                runner.close()
            self._runner = None
            self._engine = None
            self._service = None

    def _initialize(self) -> None:
        runner = asyncio.Runner(loop_factory=create_selector_event_loop)
        self._runner = runner
        try:
            if self._injected_service is None:
                resources = runner.run(_create_schedule_resources(self._settings))
                self._engine = resources.engine
                service = resources.service
            else:
                service = self._injected_service
            self._service = service
            runner.run(service.ensure_schedule(default_refresh_cleanup_schedule(self._settings)))
        except BaseException:
            try:
                if self._engine is not None:
                    runner.run(self._engine.dispose())
            finally:
                runner.close()
                self._runner = None
                self._engine = None
                self._service = None
            raise


def main() -> None:
    """Run Celery Beat with the PostgreSQL scheduler as its only scheduler."""

    settings = get_settings()
    app = create_celery_app(settings)
    try:
        app.start(
            [
                "beat",
                *sys.argv[1:],
                "--scheduler",
                DATABASE_SCHEDULER_PATH,
            ]
        )
    finally:
        app.close()


if __name__ == "__main__":
    main()
