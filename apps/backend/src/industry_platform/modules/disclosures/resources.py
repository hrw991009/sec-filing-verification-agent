"""Composition root for SEC filer discovery and its typed Tool."""

from dataclasses import dataclass
from typing import cast

import httpx2
from fastapi import Request
from redis.asyncio import Redis

from industry_platform.core.config import Settings
from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.disclosures.adapters.sec_edgar import (
    LiveSecEdgarAdapter,
    RedisSecRequestBudget,
    RedisSecResponseCache,
    SecRedisClient,
    UnavailableSecEdgarAdapter,
)
from industry_platform.modules.disclosures.adapters.sqlalchemy import (
    SqlAlchemySecFilerCatalogRepository,
)
from industry_platform.modules.disclosures.ports import SecEdgarPort
from industry_platform.modules.disclosures.service import SecFilerResolutionService
from industry_platform.modules.disclosures.tool import SecResolveFilerTool


@dataclass(frozen=True, slots=True)
class DisclosureResources:
    resolution_service: SecFilerResolutionService
    resolve_filer_tool: SecResolveFilerTool


def create_disclosure_resources(
    settings: Settings,
    session_factory: AsyncSessionFactory,
    http_client: httpx2.AsyncClient,
    redis_client: Redis,
    *,
    source: SecEdgarPort | None = None,
) -> DisclosureResources:
    selected_source = source
    if selected_source is None:
        if settings.sec_source_configured:
            sec_redis = cast(SecRedisClient, redis_client)
            selected_source = LiveSecEdgarAdapter(
                http_client,
                RedisSecRequestBudget(
                    sec_redis,
                    requests_per_second=settings.sec_requests_per_second,
                ),
                RedisSecResponseCache(sec_redis),
                user_agent=settings.sec_user_agent,
                cache_ttl_seconds=settings.sec_catalog_cache_ttl_seconds,
                timeout_seconds=settings.sec_request_timeout_seconds,
                maximum_attempts=settings.sec_request_max_attempts,
            )
        else:
            selected_source = UnavailableSecEdgarAdapter()
    service = SecFilerResolutionService(
        repository=SqlAlchemySecFilerCatalogRepository(session_factory),
        source=selected_source,
    )
    return DisclosureResources(
        resolution_service=service,
        resolve_filer_tool=SecResolveFilerTool(service),
    )


def get_disclosure_resources(request: Request) -> DisclosureResources:
    resources = getattr(request.app.state, "disclosure_resources", None)
    if not isinstance(resources, DisclosureResources):
        raise RuntimeError("Application lifespan has not initialized disclosure resources")
    return resources
