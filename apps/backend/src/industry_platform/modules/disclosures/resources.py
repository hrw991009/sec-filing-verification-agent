"""Composition root for SEC filer discovery and its typed Tool."""

from dataclasses import dataclass
from pathlib import Path
from typing import cast

import httpx2
from fastapi import Request
from redis.asyncio import Redis

from industry_platform.core.config import Settings
from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.disclosures.adapters.bulk_sqlalchemy import (
    SqlAlchemySecBulkSyncRepository,
)
from industry_platform.modules.disclosures.adapters.controlled import (
    ControlledSecSourceBundle,
    load_controlled_sec_source_bundle,
)
from industry_platform.modules.disclosures.adapters.filing_content_sqlalchemy import (
    SqlAlchemySecFilingContentRepository,
)
from industry_platform.modules.disclosures.adapters.filings_sqlalchemy import (
    SqlAlchemySecFilingRepository,
)
from industry_platform.modules.disclosures.adapters.monitor_sqlalchemy import (
    SqlAlchemySecMonitorRepository,
)
from industry_platform.modules.disclosures.adapters.sec_archives import (
    FrozenSecFilingArchiveAdapter,
    LiveSecFilingArchiveAdapter,
    UnavailableSecFilingArchiveAdapter,
)
from industry_platform.modules.disclosures.adapters.sec_bulk import (
    LiveSecBulkArchiveAdapter,
    MinioSecBulkSnapshotStore,
    SecBulkObjectStore,
    UnavailableSecBulkArchiveAdapter,
    UnavailableSecBulkSnapshotStore,
)
from industry_platform.modules.disclosures.adapters.sec_edgar import (
    FrozenSecEdgarAdapter,
    LiveSecEdgarAdapter,
    OfficialSecJsonClient,
    RedisSecRequestBudget,
    RedisSecResponseCache,
    SecRedisClient,
    UnavailableSecEdgarAdapter,
)
from industry_platform.modules.disclosures.adapters.sec_submissions import (
    FrozenSecSubmissionsAdapter,
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
from industry_platform.modules.disclosures.adapters.subscription_sqlalchemy import (
    SqlAlchemySecMonitorSubscriptionRepository,
)
from industry_platform.modules.disclosures.adapters.xbrl import (
    FrozenSecCompanyFactsAdapter,
    LiveSecCompanyFactsAdapter,
    UnavailableSecCompanyFactsAdapter,
)
from industry_platform.modules.disclosures.adapters.xbrl_sqlalchemy import (
    SqlAlchemySecXbrlRepository,
)
from industry_platform.modules.disclosures.bulk import (
    SecBulkSyncService,
    SecPostWatermarkCompanyFactsPort,
    SecPostWatermarkSubmissionsPort,
)
from industry_platform.modules.disclosures.diff import SecFilingDiffService
from industry_platform.modules.disclosures.filing_content_service import (
    SecFilingContentService,
    SecFilingImportService,
)
from industry_platform.modules.disclosures.monitor import (
    SecMonitorAnalysisService,
    SecMonitorApplicationService,
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
from industry_platform.modules.disclosures.subscription import SecMonitorSubscriptionService
from industry_platform.modules.disclosures.tool import (
    SecDiffFilingsTool,
    SecGetXbrlFactsTool,
    SecListFilingsTool,
    SecMonitorSubscribeTool,
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
from industry_platform.modules.jobs.ports import ScheduleApplicationUseCase
from industry_platform.modules.knowledge.service import KnowledgeApplicationService
from industry_platform.modules.retrieval.adapters.elasticsearch import ElasticsearchLexicalIndex
from industry_platform.modules.retrieval.adapters.milvus import MilvusDenseIndex
from industry_platform.modules.tools.registry import RegisteredToolAdapter


@dataclass(frozen=True, slots=True)
class DisclosureResources:
    bulk_sync_service: SecBulkSyncService
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
    monitor_service: SecMonitorApplicationService
    monitor_subscription_service: SecMonitorSubscriptionService

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
    schedule_service: ScheduleApplicationUseCase,
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
    controlled = _controlled_source_bundle(settings)
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
        if settings.sec_source_configured and controlled is None
        else None
    )
    selected_source = source
    if selected_source is None:
        if controlled is not None:
            selected_source = FrozenSecEdgarAdapter(controlled.catalog)
        elif settings.sec_source_configured:
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
        if controlled is not None:
            selected_submissions_source = FrozenSecSubmissionsAdapter(controlled.submissions)
        elif settings.sec_source_configured:
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
        if controlled is not None:
            selected_companyfacts_source = FrozenSecCompanyFactsAdapter(controlled.companyfacts)
        elif settings.sec_source_configured:
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
        if controlled is not None:
            selected_archive_source = FrozenSecFilingArchiveAdapter(controlled.archives)
        elif settings.sec_source_configured:
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
    selected_bulk_source = (
        LiveSecBulkArchiveAdapter(
            http_client,
            request_budget,
            user_agent=settings.sec_user_agent,
            timeout_seconds=settings.sec_bulk_request_timeout_seconds,
            maximum_attempts=settings.sec_request_max_attempts,
        )
        if settings.sec_source_configured and controlled is None
        else UnavailableSecBulkArchiveAdapter()
    )
    selected_bulk_store = (
        MinioSecBulkSnapshotStore(
            cast(SecBulkObjectStore, object_store),
            bucket=object_bucket,
        )
        if object_store is not None and object_bucket is not None
        else UnavailableSecBulkSnapshotStore()
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
        bulk_sync_service=SecBulkSyncService(
            archive_source=selected_bulk_source,
            snapshot_store=selected_bulk_store,
            repository=SqlAlchemySecBulkSyncRepository(session_factory),
            submissions_source=cast(
                SecPostWatermarkSubmissionsPort,
                selected_submissions_source,
            ),
            companyfacts_source=cast(
                SecPostWatermarkCompanyFactsPort,
                selected_companyfacts_source,
            ),
        ),
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
        monitor_service=SecMonitorApplicationService(
            repository=SqlAlchemySecMonitorRepository(session_factory),
            analyzer=SecMonitorAnalysisService(
                selection=filing_selection_service,
                imports=filing_import_service,
                xbrl=xbrl_service,
                diff=filing_diff_service,
            ),
        ),
        monitor_subscription_service=SecMonitorSubscriptionService(
            repository=SqlAlchemySecMonitorSubscriptionRepository(session_factory),
            schedules=schedule_service,
        ),
    )
    # Fail application composition if profile references drift from concrete Tool definitions.
    _ = resources.sec_source_tool_adapters
    return resources


def _controlled_source_bundle(settings: Settings) -> ControlledSecSourceBundle | None:
    configured = settings.sec_controlled_source_manifest_path
    if configured is None:
        return None
    repository_root = Path(__file__).resolve().parents[6]
    candidate = configured if configured.is_absolute() else Path.cwd() / configured
    if not candidate.exists() and not configured.is_absolute():
        candidate = repository_root / configured
    manifest = candidate.resolve(strict=True)
    if not manifest.is_relative_to(repository_root):
        raise ValueError("Controlled SEC manifest must be inside the repository")
    return load_controlled_sec_source_bundle(manifest)


def create_sec_filing_tools(
    settings: Settings,
    session_factory: AsyncSessionFactory,
    internal_http_client: httpx2.AsyncClient,
) -> tuple[
    SecSearchFilingTool,
    SecReadFilingSectionTool,
    SecGetXbrlFactsTool,
    SecDiffFilingsTool,
    SecMonitorSubscribeTool,
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
        SecMonitorSubscribeTool(),
    )


def get_disclosure_resources(request: Request) -> DisclosureResources:
    resources = getattr(request.app.state, "disclosure_resources", None)
    if not isinstance(resources, DisclosureResources):
        raise RuntimeError("Application lifespan has not initialized disclosure resources")
    return resources
