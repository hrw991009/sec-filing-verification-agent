"""Tests for dependency readiness aggregation."""

import asyncio

import pytest

from industry_platform.core.health import (
    DependencyStatus,
    HealthCheck,
    assess_readiness,
)


async def successful_check() -> None:
    return None


async def failing_check() -> None:
    raise ConnectionError("intentional dependency failure")


async def slow_check() -> None:
    await asyncio.sleep(1)


@pytest.mark.asyncio
async def test_readiness_succeeds_when_all_dependencies_are_healthy() -> None:
    report = await assess_readiness(
        postgres_check=successful_check,
        redis_check=successful_check,
        timeout_seconds=0.05,
    )

    assert report.is_ready
    assert report.postgres is DependencyStatus.OK
    assert report.redis is DependencyStatus.OK


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "postgres_check",
        "redis_check",
        "expected_postgres",
        "expected_redis",
    ),
    [
        (
            failing_check,
            successful_check,
            DependencyStatus.FAILED,
            DependencyStatus.OK,
        ),
        (
            successful_check,
            failing_check,
            DependencyStatus.OK,
            DependencyStatus.FAILED,
        ),
    ],
)
async def test_readiness_fails_when_one_dependency_fails(
    postgres_check: HealthCheck,
    redis_check: HealthCheck,
    expected_postgres: DependencyStatus,
    expected_redis: DependencyStatus,
) -> None:
    report = await assess_readiness(
        postgres_check=postgres_check,
        redis_check=redis_check,
        timeout_seconds=0.05,
    )

    assert not report.is_ready
    assert report.postgres is expected_postgres
    assert report.redis is expected_redis


@pytest.mark.asyncio
async def test_readiness_marks_timeout_as_failure() -> None:
    report = await assess_readiness(
        postgres_check=slow_check,
        redis_check=successful_check,
        timeout_seconds=0.05,
    )

    assert not report.is_ready
    assert report.postgres is DependencyStatus.FAILED
    assert report.redis is DependencyStatus.OK


@pytest.mark.asyncio
async def test_readiness_runs_dependency_checks_concurrently() -> None:
    both_checks_started = asyncio.Event()
    started_check_count = 0

    async def coordinated_check() -> None:
        nonlocal started_check_count

        started_check_count += 1
        if started_check_count == 2:
            both_checks_started.set()

        await both_checks_started.wait()

    report = await assess_readiness(
        postgres_check=coordinated_check,
        redis_check=coordinated_check,
        timeout_seconds=0.5,
    )

    assert report.is_ready
