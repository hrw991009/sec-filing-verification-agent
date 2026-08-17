"""Composition root for industry APIs, collection workers, and the real Web Tool."""

from dataclasses import dataclass

import httpx2
from fastapi import Request

from industry_platform.core.config import Settings
from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.industry.adapters.sqlalchemy import (
    SqlAlchemyIndustryCatalogRepository,
    SqlAlchemyIndustryCollectionTransactionFactory,
)
from industry_platform.modules.industry.providers import (
    FixedIndustryProviderRegistry,
    create_provider_registry,
)
from industry_platform.modules.industry.service import (
    IndustryCatalogService,
    IndustryCollectionService,
    IndustryScheduleService,
)
from industry_platform.modules.industry.tool import IndustryWebSearchTool
from industry_platform.modules.jobs.ports import ScheduleApplicationUseCase


@dataclass(frozen=True, slots=True)
class IndustryResources:
    catalog_service: IndustryCatalogService
    collection_service: IndustryCollectionService
    schedule_service: IndustryScheduleService
    providers: FixedIndustryProviderRegistry
    web_search_tool: IndustryWebSearchTool


def create_industry_resources(
    settings: Settings,
    session_factory: AsyncSessionFactory,
    http_client: httpx2.AsyncClient,
    schedule_service: ScheduleApplicationUseCase,
) -> IndustryResources:
    repository = SqlAlchemyIndustryCatalogRepository(session_factory)
    providers = create_provider_registry(
        http_client,
        world_bank_news_terms_approved=settings.world_bank_news_terms_approved,
        alpha_vantage_api_key=settings.alpha_vantage_api_key,
        alpha_vantage_terms_approved=settings.alpha_vantage_terms_approved,
    )
    collection = IndustryCollectionService(
        transaction_factory=SqlAlchemyIndustryCollectionTransactionFactory(session_factory),
        providers=providers,
        request_loader=repository,
    )
    return IndustryResources(
        catalog_service=IndustryCatalogService(repository),
        collection_service=collection,
        schedule_service=IndustryScheduleService(repository, schedule_service),
        providers=providers,
        web_search_tool=IndustryWebSearchTool(providers),
    )


def get_industry_resources(request: Request) -> IndustryResources:
    resources = getattr(request.app.state, "industry_resources", None)
    if not isinstance(resources, IndustryResources):
        raise RuntimeError("Application lifespan has not initialized industry resources")
    return resources
