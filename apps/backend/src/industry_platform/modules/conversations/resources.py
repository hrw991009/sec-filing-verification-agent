"""Composition helpers for conversation HTTP resources."""

from dataclasses import dataclass

from fastapi import Request

from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.conversations.adapters.management import (
    SqlAlchemyConversationManagementRepository,
)
from industry_platform.modules.conversations.adapters.sqlalchemy import (
    SqlAlchemyDirectAnswerTurnTransactionFactory,
)
from industry_platform.modules.conversations.management import (
    ConversationManagementService,
    ConversationManagementUseCase,
)
from industry_platform.modules.conversations.service import ConversationApplicationService
from industry_platform.modules.conversations.submission import (
    ConversationSubmissionService,
    ConversationSubmissionUseCase,
)


@dataclass(frozen=True, slots=True)
class ConversationResources:
    management_service: ConversationManagementUseCase
    submission_service: ConversationSubmissionUseCase


def create_conversation_resources(
    session_factory: AsyncSessionFactory,
    *,
    supports_image_input: bool = False,
) -> ConversationResources:
    return ConversationResources(
        management_service=ConversationManagementService(
            repository=SqlAlchemyConversationManagementRepository(session_factory)
        ),
        submission_service=ConversationSubmissionService(
            application=ConversationApplicationService(
                transaction_factory=SqlAlchemyDirectAnswerTurnTransactionFactory(
                    session_factory,
                    supports_image_input=supports_image_input,
                )
            )
        ),
    )


def get_conversation_resources(request: Request) -> ConversationResources:
    resources = getattr(request.app.state, "conversation_resources", None)
    if not isinstance(resources, ConversationResources):
        raise RuntimeError("Application lifespan has not initialized conversation resources")
    return resources
