"""HTTP contract tests for health endpoints."""

from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from industry_platform.core.config import Settings
from industry_platform.main import create_app


async def healthy_database(_engine: AsyncEngine) -> None:
    return None


async def failed_database(_engine: AsyncEngine) -> None:
    raise ConnectionError("internal database failure detail")


async def healthy_redis(_client: Redis) -> None:
    return None


async def failed_redis(_client: Redis) -> None:
    raise ConnectionError("internal Redis failure detail")


def test_live_remains_healthy_when_dependencies_fail(
    test_settings: Settings,
) -> None:
    application = create_app(
        settings=test_settings,
        database_health_check=failed_database,
        redis_health_check=failed_redis,
    )

    with TestClient(application) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_reports_healthy_dependencies(
    test_settings: Settings,
) -> None:
    application = create_app(
        settings=test_settings,
        database_health_check=healthy_database,
        redis_health_check=healthy_redis,
    )

    with TestClient(application) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {
            "postgres": "ok",
            "redis": "ok",
        },
    }


def test_ready_rejects_failed_postgres(
    test_settings: Settings,
) -> None:
    application = create_app(
        settings=test_settings,
        database_health_check=failed_database,
        redis_health_check=healthy_redis,
    )

    with TestClient(application) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {
            "postgres": "failed",
            "redis": "ok",
        },
    }
    assert "internal database failure detail" not in response.text


def test_ready_rejects_failed_redis(
    test_settings: Settings,
) -> None:
    application = create_app(
        settings=test_settings,
        database_health_check=healthy_database,
        redis_health_check=failed_redis,
    )

    with TestClient(application) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {
            "postgres": "ok",
            "redis": "failed",
        },
    }
    assert "internal Redis failure detail" not in response.text
