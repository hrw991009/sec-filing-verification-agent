"""Process-wide composition for durable job application services."""

from dataclasses import dataclass

from fastapi import Request

from industry_platform.core.config import Settings
from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.jobs.adapters.sqlalchemy import (
    SqlAlchemyJobTransactionFactory,
)
from industry_platform.modules.jobs.ports import JobApplicationUseCase
from industry_platform.modules.jobs.service import JobApplicationService


@dataclass(frozen=True, slots=True)
class JobResources:
    """Long-lived stateless job services shared by delivery adapters."""

    application_service: JobApplicationUseCase


def create_job_resources(
    settings: Settings,
    session_factory: AsyncSessionFactory,
) -> JobResources:
    """Compose reliable job application services from validated settings."""

    return JobResources(
        application_service=JobApplicationService(
            transaction_factory=SqlAlchemyJobTransactionFactory(session_factory),
            lease_seconds=settings.job_lease_seconds,
        )
    )


def get_job_resources(request: Request) -> JobResources:
    """Return job resources initialized by the FastAPI lifespan."""

    resources = getattr(request.app.state, "job_resources", None)
    if not isinstance(resources, JobResources):
        raise RuntimeError("Application lifespan has not initialized job resources")
    return resources
