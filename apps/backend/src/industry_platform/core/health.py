"""Dependency health assessment and public response models."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel

type HealthCheck = Callable[[], Awaitable[None]]


class DependencyStatus(StrEnum):
    """Public status of one required dependency."""

    OK = "ok"
    FAILED = "failed"


class ReadinessStatus(StrEnum):
    """Public readiness state of the API."""

    READY = "ready"
    NOT_READY = "not_ready"


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    """Internal readiness result before HTTP serialization."""

    postgres: DependencyStatus
    redis: DependencyStatus

    @property
    def is_ready(self) -> bool:
        """Return whether every required dependency is healthy."""

        return self.postgres is DependencyStatus.OK and self.redis is DependencyStatus.OK


class LivenessResponse(BaseModel):
    """Stable public contract for the liveness endpoint."""

    status: Literal["ok"] = "ok"


class ReadinessChecks(BaseModel):
    """Public status of each required dependency."""

    postgres: DependencyStatus
    redis: DependencyStatus


class ReadinessResponse(BaseModel):
    """Stable public contract for the readiness endpoint."""

    status: ReadinessStatus
    checks: ReadinessChecks


async def _run_check(
    check: HealthCheck,
    timeout_seconds: float,
) -> DependencyStatus:
    try:
        async with asyncio.timeout(timeout_seconds):
            await check()
    except Exception:
        return DependencyStatus.FAILED

    return DependencyStatus.OK


async def assess_readiness(
    *,
    postgres_check: HealthCheck,
    redis_check: HealthCheck,
    timeout_seconds: float,
) -> ReadinessReport:
    """Run required checks concurrently and return a sanitized report."""

    postgres_status, redis_status = await asyncio.gather(
        _run_check(postgres_check, timeout_seconds),
        _run_check(redis_check, timeout_seconds),
    )

    return ReadinessReport(
        postgres=postgres_status,
        redis=redis_status,
    )
