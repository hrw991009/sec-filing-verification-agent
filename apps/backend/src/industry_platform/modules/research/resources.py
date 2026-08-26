"""Composition helpers for Research HTTP resources."""

from dataclasses import dataclass

from fastapi import Request

from industry_platform.core.config import Settings
from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.conversations.adapters.sqlalchemy import (
    SqlAlchemyDirectAnswerTurnTransactionFactory,
)
from industry_platform.modules.conversations.service import ConversationApplicationService
from industry_platform.modules.research.adapters.durability import (
    SqlAlchemyResearchDurabilityRepository,
)
from industry_platform.modules.research.adapters.sqlalchemy import (
    SqlAlchemyResearchQueryRepository,
)
from industry_platform.modules.research.durability import (
    ResearchDurabilityService,
    ResumeTokenCodec,
)
from industry_platform.modules.research.service import (
    ResearchQueryService,
    ResearchSubmissionService,
)


@dataclass(frozen=True, slots=True)
class ResearchResources:
    submission_service: ResearchSubmissionService
    query_service: ResearchQueryService
    durability_service: ResearchDurabilityService


def create_research_resources(
    settings: Settings,
    session_factory: AsyncSessionFactory,
) -> ResearchResources:
    return ResearchResources(
        submission_service=ResearchSubmissionService(
            ConversationApplicationService(
                SqlAlchemyDirectAnswerTurnTransactionFactory(session_factory)
            )
        ),
        query_service=ResearchQueryService(SqlAlchemyResearchQueryRepository(session_factory)),
        durability_service=ResearchDurabilityService(
            repository=SqlAlchemyResearchDurabilityRepository(session_factory),
            token_codec=ResumeTokenCodec(settings.csrf_token_hmac_key.get_secret_value()),
        ),
    )


def get_research_resources(request: Request) -> ResearchResources:
    resources = getattr(request.app.state, "research_resources", None)
    if not isinstance(resources, ResearchResources):
        raise RuntimeError("Application lifespan has not initialized Research resources")
    return resources
