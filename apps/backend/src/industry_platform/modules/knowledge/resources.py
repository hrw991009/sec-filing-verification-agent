"""FastAPI composition root for Knowledge resources."""

from dataclasses import dataclass

from fastapi import Request

from industry_platform.core.config import Settings
from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.files.ports import PrivateFileObjectStore
from industry_platform.modules.knowledge.adapters.sqlalchemy import (
    SqlAlchemyKnowledgeAcceptanceTransactionFactory,
    SqlAlchemyKnowledgeRepository,
)
from industry_platform.modules.knowledge.service import KnowledgeApplicationService


@dataclass(frozen=True, slots=True)
class KnowledgeResources:
    service: KnowledgeApplicationService


def create_knowledge_resources(
    settings: Settings,
    session_factory: AsyncSessionFactory,
    object_store: PrivateFileObjectStore | None,
) -> KnowledgeResources:
    return KnowledgeResources(
        service=KnowledgeApplicationService(
            repository=SqlAlchemyKnowledgeRepository(session_factory),
            transaction_factory=SqlAlchemyKnowledgeAcceptanceTransactionFactory(session_factory),
            object_store=object_store,
            bucket=settings.minio_bucket,
            presign_expiry_seconds=settings.minio_presign_expiry_seconds,
        )
    )


def get_knowledge_resources(request: Request) -> KnowledgeResources:
    resources = getattr(request.app.state, "knowledge_resources", None)
    if not isinstance(resources, KnowledgeResources):
        raise RuntimeError("Application lifespan has not initialized Knowledge resources")
    return resources
