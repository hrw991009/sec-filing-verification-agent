"""Process-wide identity services assembled from concrete adapters."""

from dataclasses import dataclass

from anyio import CapacityLimiter
from fastapi import Request

from industry_platform.core.config import Settings
from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.identity.adapters.argon2 import Argon2idPasswordHasher
from industry_platform.modules.identity.adapters.sqlalchemy import (
    SqlAlchemyRegistrationTransactionFactory,
)
from industry_platform.modules.identity.ports import RegistrationUseCase
from industry_platform.modules.identity.service import RegistrationService


@dataclass(frozen=True, slots=True)
class IdentityResources:
    """Long-lived, stateless identity application services."""

    registration_service: RegistrationUseCase


def create_identity_resources(
    settings: Settings,
    session_factory: AsyncSessionFactory,
) -> IdentityResources:
    """Create registration adapters and share one Argon2 concurrency limiter."""

    argon2_limiter = CapacityLimiter(settings.argon2_max_concurrency)
    password_hasher = Argon2idPasswordHasher(
        settings,
        limiter=argon2_limiter,
    )
    transaction_factory = SqlAlchemyRegistrationTransactionFactory(session_factory)

    return IdentityResources(
        registration_service=RegistrationService(
            password_hasher=password_hasher,
            transaction_factory=transaction_factory,
        )
    )


def get_identity_resources(request: Request) -> IdentityResources:
    """Return identity resources initialized by the application lifespan."""

    resources = getattr(request.app.state, "identity_resources", None)

    if not isinstance(resources, IdentityResources):
        raise RuntimeError("Application lifespan has not initialized identity resources")

    return resources
