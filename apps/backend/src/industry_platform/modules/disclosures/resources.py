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
    MinioSecXbrlSnapshotStore,
    UnavailableSecFilingDocumentSnapshotStore,
    UnavailableSecSubmissionSnapshotStore,
    UnavailableSecXbrlSnapshotStore,
)
from industry_platform.modules.disclosures.adapters.sqlalchemy import (
    SqlAlchemySecFilerCatalogRepository,
)
from industry_platform.modules.disclosures.adapters.xbrl import (
    LiveSecCompanyFactsAdapter,
    UnavailableSecCompanyFactsAdapter,
)
from industry_platform.modules.disclosures.adapters.xbrl_sqlalchemy import (
    SqlAlchemySecXbrlRepository,
)
from industry_platform.modules.disclosures.diff import SecFilingDiffService
from industry_platform.modules.disclosures.filing_content_service import (
    SecFilingContentService,
    SecFilingImportService,
)
from industry_platform.modules.disclosures.ports import (
    SecCompanyFactsPort,
    SecEdgarPort,
    SecFilingArchivePort,
    SecFilingDocumentSnapshotStore,
    SecSubmissionSnapshotStore,
    SecSubmissionsPort,
    SecXbrlSnapshotStore,
)
from industry_platform.modules.disclosures.profile import require_sec_source_tool_adapters
from industry_platform.modules.disclosures.service import (
    SecFilerResolutionService,
    SecFilingSelectionService,
)
from industry_platform.modules.disclosures.tool import (
    SecDiffFilingsTool,
    SecGetXbrlFactsTool,
    SecListFilingsTool,
    SecReadFilingSectionTool,
    SecResolveFilerTool,
    SecSearchFilingTool,
)
from industry_platform.modules.disclosures.xbrl_service import SecXbrlService
from industry_platform.modules.files.ports import PrivateFileObjectStore
from industry_platform.modules.ingestion.index_contract import (
    ELASTICSEARCH_INDEX,
    MILVUS_COLLECTION,
)
from industry_platform.modules.knowledge.service import KnowledgeApplicationService
from industry_platform.modules.retrieval.adapters.elasticsearch import ElasticsearchLexicalIndex
from industry_platform.modules.retrieval.adapters.milvus import MilvusDenseIndex
from industry_platform.modules.tools.registry import RegisteredToolAdapter


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
    xbrl_service: SecXbrlService
    get_xbrl_facts_tool: SecGetXbrlFactsTool
    filing_diff_service: SecFilingDiffService
    diff_filings_tool: SecDiffFilingsTool

    @property
    def sec_source_tool_adapters(self) -> tuple[RegisteredToolAdapter, ...]:
        """Return the exact five concrete Adapters accepted by the Day 6 profile."""

        return require_sec_source_tool_adapters(
            (
                self.resolve_filer_tool,
                self.list_filings_tool,
                self.get_xbrl_facts_tool,
                self.search_filing_tool,
                self.read_filing_section_tool,
            )
        )


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
    companyfacts_source: SecCompanyFactsPort | None = None,
    xbrl_snapshot_store: SecXbrlSnapshotStore | None = None,
) -> DisclosureResources:
    sec_redis = cast(SecRedisClient, redis_client)
    request_budget = RedisSecRequestBudget(
        sec_redis,
        requests_per_second=settings.sec_requests_per_second,
    )
    official_client = (
        OfficialSecJsonClient(
            http_client,
            request_budget,
            user_agent=settings.sec_user_agent,
            timeout_seconds=settings.sec_request_timeout_seconds,
            maximum_attempts=settings.sec_request_max_attempts,
        )
        if settings.sec_source_configured
        else None
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
            if official_client is None:
                raise AssertionError("Configured SEC client disappeared")
            selected_submissions_source = LiveSecSubmissionsAdapter(
                official_client,
                lambda key: RedisSecResponseCache(sec_redis, cache_key=key),
                cache_ttl_seconds=settings.sec_catalog_cache_ttl_seconds,
            )
        else:
            selected_submissions_source = UnavailableSecEdgarAdapter()
    selected_companyfacts_source = companyfacts_source
    if selected_companyfacts_source is None:
        if settings.sec_source_configured:
            if official_client is None:
                raise AssertionError("Configured SEC client disappeared")
            selected_companyfacts_source = LiveSecCompanyFactsAdapter(
                official_client,
                lambda key: RedisSecResponseCache(sec_redis, cache_key=key),
                cache_ttl_seconds=settings.sec_catalog_cache_ttl_seconds,
            )
        else:
            selected_companyfacts_source = UnavailableSecCompanyFactsAdapter()
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
    selected_xbrl_snapshot_store = xbrl_snapshot_store
    if selected_xbrl_snapshot_store is None:
        if object_store is None:
            selected_xbrl_snapshot_store = UnavailableSecXbrlSnapshotStore()
        else:
            if object_bucket is None:
                raise RuntimeError("Validated MinIO settings are incomplete")
            selected_xbrl_snapshot_store = MinioSecXbrlSnapshotStore(
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
        lexical_index=ElasticsearchLexicalIndex(
            client=internal_http_client,
            endpoint=settings.elasticsearch_endpoint,
            api_key=(
                None
                if settings.elasticsearch_api_key is None
                else settings.elasticsearch_api_key.get_secret_value()
            ),
            index=ELASTICSEARCH_INDEX,
            timeout_seconds=settings.knowledge_index_timeout_seconds,
        ),
    )
    xbrl_service = SecXbrlService(
        repository=SqlAlchemySecXbrlRepository(
            session_factory,
            object_bucket=object_bucket or "sec-snapshots-unconfigured",
        ),
        filing_repository=filing_content_repository,
        companyfacts_source=selected_companyfacts_source,
        snapshot_store=selected_xbrl_snapshot_store,
    )
    filing_diff_service = SecFilingDiffService(
        repository=filing_content_repository,
        content_service=filing_content_service,
        xbrl_service=xbrl_service,
    )
    resources = DisclosureResources(
        resolution_service=resolution_service,
        resolve_filer_tool=SecResolveFilerTool(resolution_service),
        filing_selection_service=filing_selection_service,
        list_filings_tool=SecListFilingsTool(filing_selection_service),
        filing_import_service=filing_import_service,
        filing_content_service=filing_content_service,
        search_filing_tool=SecSearchFilingTool(filing_content_service),
        read_filing_section_tool=SecReadFilingSectionTool(filing_content_service),
        xbrl_service=xbrl_service,
        get_xbrl_facts_tool=SecGetXbrlFactsTool(xbrl_service),
        filing_diff_service=filing_diff_service,
        diff_filings_tool=SecDiffFilingsTool(filing_diff_service),
    )
    # Fail application composition if profile references drift from concrete Tool definitions.
    _ = resources.sec_source_tool_adapters
    return resources


def create_sec_filing_tools(
    settings: Settings,
    session_factory: AsyncSessionFactory,
    internal_http_client: httpx2.AsyncClient,
) -> tuple[
    SecSearchFilingTool,
    SecReadFilingSectionTool,
    SecGetXbrlFactsTool,
    SecDiffFilingsTool,
]:
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
        lexical_index=ElasticsearchLexicalIndex(
            client=internal_http_client,
            endpoint=settings.elasticsearch_endpoint,
            api_key=(
                None
                if settings.elasticsearch_api_key is None
                else settings.elasticsearch_api_key.get_secret_value()
            ),
            index=ELASTICSEARCH_INDEX,
            timeout_seconds=settings.knowledge_index_timeout_seconds,
        ),
    )
    xbrl_service = SecXbrlService(
        repository=SqlAlchemySecXbrlRepository(
            session_factory,
            object_bucket=settings.minio_bucket or "sec-snapshots-unconfigured",
        ),
        filing_repository=repository,
        companyfacts_source=UnavailableSecCompanyFactsAdapter(),
        snapshot_store=UnavailableSecXbrlSnapshotStore(),
    )
    diff_service = SecFilingDiffService(
        repository=repository,
        content_service=service,
        xbrl_service=xbrl_service,
    )
    return (
        SecSearchFilingTool(service),
        SecReadFilingSectionTool(service),
        SecGetXbrlFactsTool(xbrl_service),
        SecDiffFilingsTool(diff_service),
    )


def get_disclosure_resources(request: Request) -> DisclosureResources:
    resources = getattr(request.app.state, "disclosure_resources", None)
    if not isinstance(resources, DisclosureResources):
        raise RuntimeError("Application lifespan has not initialized disclosure resources")
    return resources
