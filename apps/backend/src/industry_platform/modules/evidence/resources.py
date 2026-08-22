"""Composition helpers for Evidence ledger HTTP resources."""

from dataclasses import dataclass

from fastapi import Request

from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.evidence.adapters.sqlalchemy import (
    SqlAlchemyEvidenceRepository,
)
from industry_platform.modules.evidence.ports import EvidenceUseCase
from industry_platform.modules.evidence.service import EvidenceApplicationService


@dataclass(frozen=True, slots=True)
class EvidenceResources:
    service: EvidenceUseCase


def create_evidence_resources(session_factory: AsyncSessionFactory) -> EvidenceResources:
    return EvidenceResources(
        service=EvidenceApplicationService(SqlAlchemyEvidenceRepository(session_factory))
    )


def get_evidence_resources(request: Request) -> EvidenceResources:
    resources = getattr(request.app.state, "evidence_resources", None)
    if not isinstance(resources, EvidenceResources):
        raise RuntimeError("Application lifespan has not initialized Evidence resources")
    return resources
