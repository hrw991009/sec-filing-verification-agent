"""Composition root for SEC filer discovery and its typed Tool."""

from dataclasses import dataclass
from typing import cast

import httpx2
from fastapi import Request
from redis.asyncio import Redis

from industry_platform.core.config import Settings
from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.disclosures.adapters.filing_content_sqlalchemy import (
    SqlAlchemySecFilingContentRepository,
)
from industry_platform.modules.disclosures.adapters.filings_sqlalchemy import (
    SqlAlchemySecFilingRepository,
)
from industry_platform.modules.disclosures.adapters.sec_archives import (
    LiveSecFilingArchiveAdapter,
    UnavailableSecFilingArchiveAdapter,
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
    MinioSecFilingDocumentSnapshotStore,
    MinioSecSubmissionSnapshotStore,
    UnavailableSecFilingDocumentSnapshotStore,
    UnavailableSecSubmissionSnapshotStore,
)
from industry_platform.modules.disclosures.adapters.sqlalchemy import (
    SqlAlchemySecFilerCatalogRepository,
)
from industry_platform.modules.disclosures.filing_content_service import (
    SecFilingContentService,
    SecFilingImportService,
)
from industry_platform.modules.disclosures.ports import (
    SecEdgarPort,
    SecFilingArchivePort,
    SecFilingDocumentSnapshotStore,
    SecSubmissionSnapshotStore,
    SecSubmissionsPort,
)
from industry_platform.modules.disclosures.service import (
    SecFilerResolutionService,
    SecFilingSelectionService,
)
from industry_platform.modules.disclosures.tool import (
    SecListFilingsTool,
    SecReadFilingSectionTool,
    SecResolveFilerTool,
    SecSearchFilingTool,
)
from industry_platform.modules.files.ports import PrivateFileObjectStore
from industry_platform.modules.ingestion.index_contract import MILVUS_COLLECTION
from industry_platform.modules.knowledge.service import KnowledgeApplicationService
from industry_platform.modules.retrieval.adapters.milvus import MilvusDenseIndex


@dataclass(frozen=True, slots=True)
class DisclosureResources:
    resolution_service: SecFilerResolutionService
    resolve_filer_tool: SecResolveFilerTool
    filing_selection_service: SecFilingSelectionService
    list_filings_tool: SecListFilingsTool
    filing_import_service: SecFilingImportService
    filing_content_service: SecFilingContentService
    search_filing_tool: SecSearchFilingTool
    read_filing_section_tool: SecReadFilingSectionTool


def create_disclosure_resources(
    settings: Settings,
    session_factory: AsyncSessionFactory,
    http_client: httpx2.AsyncClient,
    redis_client: Redis,
    internal_http_client: httpx2.AsyncClient,
    knowledge_service: KnowledgeApplicationService,
    object_store: PrivateFileObjectStore | None = None,
    *,
    source: SecEdgarPort | None = None,
    submissions_source: SecSubmissionsPort | None = None,
    submission_snapshot_store: SecSubmissionSnapshotStore | None = None,
    archive_source: SecFilingArchivePort | None = None,
    filing_snapshot_store: SecFilingDocumentSnapshotStore | None = None,
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
    selected_archive_source = archive_source
    if selected_archive_source is None:
        if settings.sec_source_configured:
            selected_archive_source = LiveSecFilingArchiveAdapter(
                http_client,
                request_budget,
                user_agent=settings.sec_user_agent,
                timeout_seconds=settings.sec_request_timeout_seconds,
                maximum_attempts=settings.sec_request_max_attempts,
            )
        else:
            selected_archive_source = UnavailableSecFilingArchiveAdapter()
    selected_filing_snapshot_store = filing_snapshot_store
    if selected_filing_snapshot_store is None:
        if object_store is None:
            selected_filing_snapshot_store = UnavailableSecFilingDocumentSnapshotStore()
        else:
            if object_bucket is None:
                raise RuntimeError("Validated MinIO settings are incomplete")
            selected_filing_snapshot_store = MinioSecFilingDocumentSnapshotStore(
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
    filing_content_repository = SqlAlchemySecFilingContentRepository(
        session_factory,
        object_bucket=object_bucket or "sec-snapshots-unconfigured",
    )
    filing_import_service = SecFilingImportService(
        repository=filing_content_repository,
        archive_source=selected_archive_source,
        snapshot_store=selected_filing_snapshot_store,
        knowledge_service=knowledge_service,
    )
    filing_content_service = SecFilingContentService(
        repository=filing_content_repository,
        dense_index=MilvusDenseIndex(
            client=internal_http_client,
            endpoint=settings.milvus_endpoint,
            token=(
                None if settings.milvus_token is None else settings.milvus_token.get_secret_value()
            ),
            collection=MILVUS_COLLECTION,
            timeout_seconds=settings.knowledge_index_timeout_seconds,
        ),
    )
    return DisclosureResources(
        resolution_service=resolution_service,
        resolve_filer_tool=SecResolveFilerTool(resolution_service),
        filing_selection_service=filing_selection_service,
        list_filings_tool=SecListFilingsTool(filing_selection_service),
        filing_import_service=filing_import_service,
        filing_content_service=filing_content_service,
        search_filing_tool=SecSearchFilingTool(filing_content_service),
        read_filing_section_tool=SecReadFilingSectionTool(filing_content_service),
    )


def create_sec_filing_read_tools(
    settings: Settings,
    session_factory: AsyncSessionFactory,
    internal_http_client: httpx2.AsyncClient,
) -> tuple[SecSearchFilingTool, SecReadFilingSectionTool]:
    repository = SqlAlchemySecFilingContentRepository(
        session_factory,
        object_bucket=settings.minio_bucket or "sec-snapshots-unconfigured",
    )
    service = SecFilingContentService(
        repository=repository,
        dense_index=MilvusDenseIndex(
            client=internal_http_client,
            endpoint=settings.milvus_endpoint,
            token=(
                None if settings.milvus_token is None else settings.milvus_token.get_secret_value()
            ),
            collection=MILVUS_COLLECTION,
            timeout_seconds=settings.knowledge_index_timeout_seconds,
        ),
    )
    return SecSearchFilingTool(service), SecReadFilingSectionTool(service)


def get_disclosure_resources(request: Request) -> DisclosureResources:
    resources = getattr(request.app.state, "disclosure_resources", None)
    if not isinstance(resources, DisclosureResources):
        raise RuntimeError("Application lifespan has not initialized disclosure resources")
    return resources
