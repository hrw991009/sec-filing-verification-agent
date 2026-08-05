"""FastAPI application entry point."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, Request, Response, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from industry_platform.core.config import Settings, get_settings
from industry_platform.core.database import (
    check_database_connection,
    create_database_engine,
)
from industry_platform.core.health import (
    LivenessResponse,
    ReadinessChecks,
    ReadinessResponse,
    ReadinessStatus,
    assess_readiness,
)
from industry_platform.core.redis_client import (
    check_redis_connection,
    create_redis_client,
)

type DatabaseHealthCheck = Callable[[AsyncEngine], Awaitable[None]]
type RedisHealthCheck = Callable[[Redis], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ApplicationResources:
    """Process-wide resources created and closed by FastAPI lifespan."""

    settings: Settings
    database_engine: AsyncEngine
    redis_client: Redis


def _get_resources(request: Request) -> ApplicationResources:
    resources = getattr(request.app.state, "resources", None)

    if not isinstance(resources, ApplicationResources):
        raise RuntimeError("Application lifespan has not initialized resources")

    return resources


def create_app(
    *,
    settings: Settings | None = None,
    database_health_check: DatabaseHealthCheck = check_database_connection,
    redis_health_check: RedisHealthCheck = check_redis_connection,
) -> FastAPI:
    """Create a configured FastAPI application."""

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        active_settings = settings if settings is not None else get_settings()
        database_engine = create_database_engine(active_settings)
        redis_client = create_redis_client(active_settings)

        application.state.resources = ApplicationResources(
            settings=active_settings,
            database_engine=database_engine,
            redis_client=redis_client,
        )

        try:
            yield
        finally:
            try:
                await redis_client.aclose()
            finally:
                await database_engine.dispose()

    application = FastAPI(
        title="Industry Intelligence Platform API",
        version="0.1.0",
        lifespan=lifespan,
    )

    @application.get(
        "/health/live",
        response_model=LivenessResponse,
        tags=["health"],
    )
    async def live() -> LivenessResponse:
        return LivenessResponse()

    @application.get(
        "/health/ready",
        response_model=ReadinessResponse,
        responses={
            status.HTTP_503_SERVICE_UNAVAILABLE: {
                "model": ReadinessResponse,
            },
        },
        tags=["health"],
    )
    async def ready(
        request: Request,
        response: Response,
    ) -> ReadinessResponse:
        resources = _get_resources(request)

        async def postgres_probe() -> None:
            await database_health_check(resources.database_engine)

        async def redis_probe() -> None:
            await redis_health_check(resources.redis_client)

        report = await assess_readiness(
            postgres_check=postgres_probe,
            redis_check=redis_probe,
            timeout_seconds=resources.settings.health_check_timeout_seconds,
        )

        readiness_status = ReadinessStatus.READY if report.is_ready else ReadinessStatus.NOT_READY

        if not report.is_ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

        return ReadinessResponse(
            status=readiness_status,
            checks=ReadinessChecks(
                postgres=report.postgres,
                redis=report.redis,
            ),
        )

    return application


app = create_app()
