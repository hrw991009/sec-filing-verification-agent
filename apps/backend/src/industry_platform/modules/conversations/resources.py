"""Composition helpers for conversation HTTP resources."""

from dataclasses import dataclass

from fastapi import Request

from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.conversations.adapters.management import (
    SqlAlchemyConversationManagementRepository,
)
from industry_platform.modules.conversations.management import (
    ConversationManagementService,
    ConversationManagementUseCase,
)


@dataclass(frozen=True, slots=True)
class ConversationResources:
    management_service: ConversationManagementUseCase


def create_conversation_resources(
    session_factory: AsyncSessionFactory,
) -> ConversationResources:
    return ConversationResources(
        management_service=ConversationManagementService(
            repository=SqlAlchemyConversationManagementRepository(session_factory)
        )
    )


def get_conversation_resources(request: Request) -> ConversationResources:
    resources = getattr(request.app.state, "conversation_resources", None)
    if not isinstance(resources, ConversationResources):
        raise RuntimeError("Application lifespan has not initialized conversation resources")
    return resources
