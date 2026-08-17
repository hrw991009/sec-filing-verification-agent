"""Application services for industry context, collection, and schedules."""

from dataclasses import dataclass, field
from uuid import UUID

from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.industry.domain import (
    INDUSTRY_COLLECTION_QUEUE_NAME,
    INDUSTRY_COLLECTION_TASK_NAME,
    CollectionResult,
    CollectionRunRequest,
    CollectionScheduleRequest,
    CollectionScheduleSummary,
    CollectionStatusSummary,
    IndustryCollectionNotFoundError,
    IndustryPersistenceError,
    IndustryPreference,
    IndustryPreset,
    IndustryProviderError,
    ProviderQuery,
    ProviderStatus,
    SourceItemSummary,
    SourceKind,
    provider_for_kind,
    require_industry,
    search_industries,
)
from industry_platform.modules.industry.ports import (
    IndustryCatalogRepository,
    IndustryCollectionRequestLoader,
    IndustryCollectionTransactionFactory,
    ProviderRegistryPort,
)
from industry_platform.modules.jobs.domain import (
    EnsuredSchedule,
    ExecutionScope,
    JobPersistenceError,
    ManualScheduleTriggerCommand,
    ManualScheduleTriggerResult,
    ScheduleDefinition,
)
from industry_platform.modules.jobs.ports import ScheduleApplicationUseCase
from industry_platform.modules.workspaces.domain import (
    WorkspaceAccessDeniedError,
    WorkspaceAction,
    WorkspaceScope,
)
from industry_platform.modules.workspaces.policy import scope_allows


@dataclass(frozen=True, slots=True)
class IndustryCatalogService:
    repository: IndustryCatalogRepository = field(repr=False)

    def list_industries(
        self,
        scope: WorkspaceScope,
        *,
        query: str | None = None,
    ) -> tuple[IndustryPreset, ...]:
        _require_action(scope, WorkspaceAction.VIEW)
        return search_industries(query)

    async def get_preference(self, scope: WorkspaceScope) -> IndustryPreference | None:
        _require_action(scope, WorkspaceAction.VIEW)
        return await self.repository.get_preference(scope)

    async def set_preference(
        self,
        scope: WorkspaceScope,
        industry_id: UUID,
    ) -> IndustryPreference:
        _require_action(scope, WorkspaceAction.UPDATE_RESOURCE)
        return await self.repository.set_preference(scope, require_industry(industry_id))

    async def list_items(
        self,
        scope: WorkspaceScope,
        *,
        industry_id: UUID,
        kind: SourceKind | None,
        limit: int,
        offset: int,
    ) -> tuple[SourceItemSummary, ...]:
        _require_action(scope, WorkspaceAction.VIEW)
        require_industry(industry_id)
        if not 1 <= limit <= 100 or not 0 <= offset <= 10_000:
            raise ValueError("Source item page is invalid")
        return await self.repository.list_source_items(
            scope,
            industry_id=industry_id,
            kind=kind,
            limit=limit,
            offset=offset,
        )

    async def list_runs(
        self,
        scope: WorkspaceScope,
        *,
        limit: int,
    ) -> tuple[CollectionStatusSummary, ...]:
        _require_action(scope, WorkspaceAction.VIEW)
        if not 1 <= limit <= 100:
            raise ValueError("Collection Run page is invalid")
        return await self.repository.list_collection_runs(scope, limit=limit)

    async def list_schedules(
        self,
        scope: WorkspaceScope,
    ) -> tuple[CollectionScheduleSummary, ...]:
        _require_action(scope, WorkspaceAction.VIEW)
        return await self.repository.list_collection_schedules(scope)


@dataclass(frozen=True, slots=True)
class IndustryCollectionService:
    transaction_factory: IndustryCollectionTransactionFactory = field(repr=False)
    providers: ProviderRegistryPort = field(repr=False)
    request_loader: IndustryCollectionRequestLoader = field(repr=False)

    async def collect(self, request: CollectionRunRequest) -> CollectionResult:
        require_industry(request.industry_id)
        async with self.transaction_factory() as writer:
            cursor = await writer.claim(request)
        provider = self.providers.provider(request.kind)
        try:
            page = await provider.fetch(
                ProviderQuery(
                    industry=require_industry(request.industry_id),
                    query=request.query,
                    cursor=cursor,
                )
            )
        except IndustryProviderError as error:
            async with self.transaction_factory() as writer:
                await writer.fail(request, error_code=error.code.value)
            raise
        async with self.transaction_factory() as writer:
            return await writer.complete(request, page)

    def provider_statuses(self) -> tuple[ProviderStatus, ...]:
        return self.providers.statuses()

    async def collect_job(
        self,
        *,
        job_id: UUID,
        workspace_id: UUID,
        trace_id: TraceId,
    ) -> CollectionResult:
        if not trace_id:
            raise ValueError("Collection trace ID is invalid")
        request = await self.request_loader.load_collection_request(
            job_id=job_id,
            workspace_id=workspace_id,
            trace_id=trace_id,
        )
        return await self.collect(request)


@dataclass(frozen=True, slots=True)
class IndustryScheduleService:
    repository: IndustryCatalogRepository = field(repr=False)
    schedules: ScheduleApplicationUseCase = field(repr=False)

    async def ensure_schedule(
        self,
        request: CollectionScheduleRequest,
    ) -> EnsuredSchedule:
        _require_action(request.scope, WorkspaceAction.CREATE_RESOURCE)
        industry = require_industry(request.industry_id)
        definition = provider_for_kind(request.kind)
        query = (
            industry.default_symbol if request.kind is SourceKind.STOCK else industry.default_query
        )
        try:
            return await self.schedules.ensure_schedule(
                ScheduleDefinition(
                    scope=ExecutionScope(workspace_id=request.scope.workspace_id),
                    name=f"industry-{industry.code}-{request.kind.value}",
                    task_name=INDUSTRY_COLLECTION_TASK_NAME,
                    cron_expression=request.cron_expression,
                    timezone_name=request.timezone_name,
                    payload={
                        "schema_version": 1,
                        "industry_id": str(industry.industry_id),
                        "source_kind": definition.kind.value,
                        "query": query,
                    },
                    queue_name=INDUSTRY_COLLECTION_QUEUE_NAME,
                    max_attempts=3,
                    priority=0,
                    soft_time_limit_seconds=60,
                    hard_time_limit_seconds=90,
                    misfire_policy=request.misfire_policy,
                    catch_up_window_seconds=request.catch_up_window_seconds,
                    max_catch_up=request.max_catch_up,
                )
            )
        except JobPersistenceError as error:
            raise IndustryPersistenceError(sqlstate=error.sqlstate) from None

    async def trigger_manual(
        self,
        scope: WorkspaceScope,
        *,
        schedule_id: UUID,
        trigger_id: UUID,
    ) -> ManualScheduleTriggerResult:
        _require_action(scope, WorkspaceAction.RUN_TOOL)
        exists = await self.repository.collection_schedule_exists(scope, schedule_id)
        if not exists:
            raise IndustryCollectionNotFoundError
        try:
            return await self.schedules.trigger_manual(
                ManualScheduleTriggerCommand(schedule_id=schedule_id, trigger_id=trigger_id)
            )
        except JobPersistenceError as error:
            raise IndustryPersistenceError(sqlstate=error.sqlstate) from None


def _require_action(scope: WorkspaceScope, action: WorkspaceAction) -> None:
    if not scope_allows(scope, action):
        raise WorkspaceAccessDeniedError
