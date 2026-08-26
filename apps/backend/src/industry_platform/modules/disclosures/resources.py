"""Composition root for SEC filer discovery and its typed Tool."""

from dataclasses import dataclass
from typing import cast

import httpx2
from fastapi import Request
from redis.asyncio import Redis

from industry_platform.core.config import Settings
from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.disclosures.adapters.filings_sqlalchemy import (
    SqlAlchemySecFilingRepository,
)
from industry_platform.modules.disclosures.adapters.sec_edgar import (
    LiveSecEdgarAdapter,
    OfficialSecJsonClient,
    RedisSecRequestBudget,
    RedisSecResponseCache,
    SecRedisClient,
    UnavailableSecEdgarAdapter,
)
from industry_platform.modules.disclosures.adapters.sec_submissions import (
    LiveSecSubmissionsAdapter,
)
from industry_platform.modules.disclosures.adapters.snapshots import (
    MinioSecSubmissionSnapshotStore,
    UnavailableSecSubmissionSnapshotStore,
)
from industry_platform.modules.disclosures.adapters.sqlalchemy import (
    SqlAlchemySecFilerCatalogRepository,
)
from industry_platform.modules.disclosures.ports import (
    SecEdgarPort,
    SecSubmissionSnapshotStore,
    SecSubmissionsPort,
)
from industry_platform.modules.disclosures.service import (
    SecFilerResolutionService,
    SecFilingSelectionService,
)
from industry_platform.modules.disclosures.tool import SecListFilingsTool, SecResolveFilerTool
from industry_platform.modules.files.ports import PrivateFileObjectStore


@dataclass(frozen=True, slots=True)
class DisclosureResources:
    resolution_service: SecFilerResolutionService
    resolve_filer_tool: SecResolveFilerTool
    filing_selection_service: SecFilingSelectionService
    list_filings_tool: SecListFilingsTool


def create_disclosure_resources(
    settings: Settings,
    session_factory: AsyncSessionFactory,
    http_client: httpx2.AsyncClient,
    redis_client: Redis,
    object_store: PrivateFileObjectStore | None = None,
    *,
    source: SecEdgarPort | None = None,
    submissions_source: SecSubmissionsPort | None = None,
    submission_snapshot_store: SecSubmissionSnapshotStore | None = None,
) -> DisclosureResources:
    sec_redis = cast(SecRedisClient, redis_client)
    request_budget = RedisSecRequestBudget(
        sec_redis,
        requests_per_second=settings.sec_requests_per_second,
    )
    selected_source = source
    if selected_source is None:
        if settings.sec_source_configured:
            selected_source = LiveSecEdgarAdapter(
                http_client,
                request_budget,
                RedisSecResponseCache(sec_redis),
                user_agent=settings.sec_user_agent,
                cache_ttl_seconds=settings.sec_catalog_cache_ttl_seconds,
                timeout_seconds=settings.sec_request_timeout_seconds,
                maximum_attempts=settings.sec_request_max_attempts,
            )
        else:
            selected_source = UnavailableSecEdgarAdapter()
    selected_submissions_source = submissions_source
    if selected_submissions_source is None:
        if settings.sec_source_configured:
            official_client = OfficialSecJsonClient(
                http_client,
                request_budget,
                user_agent=settings.sec_user_agent,
                timeout_seconds=settings.sec_request_timeout_seconds,
                maximum_attempts=settings.sec_request_max_attempts,
            )
            selected_submissions_source = LiveSecSubmissionsAdapter(
                official_client,
                lambda key: RedisSecResponseCache(sec_redis, cache_key=key),
                cache_ttl_seconds=settings.sec_catalog_cache_ttl_seconds,
            )
        else:
            selected_submissions_source = UnavailableSecEdgarAdapter()
    selected_snapshot_store = submission_snapshot_store
    object_bucket = settings.minio_bucket
    if object_store is not None and object_bucket is None:
        raise RuntimeError("Validated MinIO settings are incomplete")
    if selected_snapshot_store is None:
        if object_store is None:
            selected_snapshot_store = UnavailableSecSubmissionSnapshotStore()
        else:
            if object_bucket is None:
                raise RuntimeError("Validated MinIO settings are incomplete")
            selected_snapshot_store = MinioSecSubmissionSnapshotStore(
                object_store,
                bucket=object_bucket,
            )
    resolution_service = SecFilerResolutionService(
        repository=SqlAlchemySecFilerCatalogRepository(session_factory),
        source=selected_source,
    )
    filing_selection_service = SecFilingSelectionService(
        repository=SqlAlchemySecFilingRepository(
            session_factory,
            object_bucket=object_bucket or "sec-snapshots-unconfigured",
        ),
        source=selected_submissions_source,
        snapshot_store=selected_snapshot_store,
    )
    return DisclosureResources(
        resolution_service=resolution_service,
        resolve_filer_tool=SecResolveFilerTool(resolution_service),
        filing_selection_service=filing_selection_service,
        list_filings_tool=SecListFilingsTool(filing_selection_service),
    )


def get_disclosure_resources(request: Request) -> DisclosureResources:
    resources = getattr(request.app.state, "disclosure_resources", None)
    if not isinstance(resources, DisclosureResources):
        raise RuntimeError("Application lifespan has not initialized disclosure resources")
    return resources
