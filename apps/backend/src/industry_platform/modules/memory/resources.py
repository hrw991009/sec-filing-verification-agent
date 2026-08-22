"""Composition helpers for Memory HTTP resources."""

from dataclasses import dataclass

from fastapi import Request

from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.memory.adapters.sqlalchemy import SqlAlchemyMemoryRepository
from industry_platform.modules.memory.ports import MemoryUseCase
from industry_platform.modules.memory.service import MemoryApplicationService


@dataclass(frozen=True, slots=True)
class MemoryResources:
    service: MemoryUseCase


def create_memory_resources(session_factory: AsyncSessionFactory) -> MemoryResources:
    return MemoryResources(
        service=MemoryApplicationService(SqlAlchemyMemoryRepository(session_factory))
    )


def get_memory_resources(request: Request) -> MemoryResources:
    resources = getattr(request.app.state, "memory_resources", None)
    if not isinstance(resources, MemoryResources):
        raise RuntimeError("Application lifespan has not initialized Memory resources")
    return resources
