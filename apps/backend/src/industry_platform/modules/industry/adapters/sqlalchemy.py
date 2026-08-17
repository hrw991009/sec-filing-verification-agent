"""PostgreSQL adapters for industry context and source collection."""

from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Select, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from industry_platform.core.database import AsyncSessionFactory, safe_sqlstate
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.industry.domain import (
    INDUSTRY_COLLECTION_TASK_NAME,
    CollectionResult,
    CollectionRunRequest,
    CollectionRunStatus,
    CollectionScheduleSummary,
    CollectionStatusSummary,
    CollectionTriggerKind,
    IndustryNotFoundError,
    IndustryPersistenceError,
    IndustryPreference,
    IndustryPreset,
    ProviderItem,
    ProviderPage,
    SourceItemDisposition,
    SourceItemSummary,
    SourceKind,
    provider_for_kind,
    require_industry,
)
from industry_platform.modules.industry.models import (
    BiddingItemRecord,
    CollectionCursorRecord,
    CollectionRunItemRecord,
    CollectionRunRecord,
    MarketSnapshotRecord,
    NewsItemRecord,
    PolicyItemRecord,
    SourceItemRecord,
    UserIndustryPreference,
)
from industry_platform.modules.industry.ports import (
    IndustryCatalogRepository,
    IndustryCollectionRequestLoader,
    IndustryCollectionTransactionFactory,
    IndustryCollectionWriter,
)
from industry_platform.modules.jobs.domain import (
    ScheduleMisfirePolicy,
    ScheduleOccurrenceMaterialization,
    ScheduleTriggerKind,
)
from industry_platform.modules.jobs.models import Schedule
from industry_platform.modules.workspaces.domain import WorkspaceScope


async def industry_collection_occurrence_observer(
    session: AsyncSession,
    materialization: ScheduleOccurrenceMaterialization,
) -> None:
    """Create a Collection Run in the exact Schedule/Job/Outbox transaction."""

    if materialization.task_name != INDUSTRY_COLLECTION_TASK_NAME:
        return
    workspace_id = materialization.scope.workspace_id
    if workspace_id is None or materialization.scope.system_scope_key is not None:
        raise ValueError("Industry collection schedule must be Workspace-scoped")
    payload = materialization.payload
    if set(payload) != {"schema_version", "industry_id", "source_kind", "query"}:
        raise ValueError("Industry collection schedule payload is invalid")
    if payload.get("schema_version") != 1:
        raise ValueError("Industry collection schedule payload is invalid")
    try:
        industry_id = UUID(str(payload["industry_id"]))
        kind = SourceKind(str(payload["source_kind"]))
    except (KeyError, TypeError, ValueError):
        raise ValueError("Industry collection schedule payload is invalid") from None
    query = payload.get("query")
    if not isinstance(query, str):
        raise ValueError("Industry collection schedule payload is invalid")
    require_industry(industry_id)
    definition = provider_for_kind(kind)
    # The observer is invoked after the Job, Outbox, and occurrence have been added.
    # Flush those FK parents first; the surrounding transaction still commits or
    # rolls back the complete materialization graph as one unit.
    await session.flush()
    session.add(
        CollectionRunRecord(
            id=materialization.occurrence_id,
            workspace_id=workspace_id,
            industry_id=industry_id,
            data_source_id=definition.source_id,
            source_kind=kind,
            schedule_occurrence_id=materialization.occurrence_id,
            job_id=materialization.job_id,
            trigger_kind=(
                CollectionTriggerKind.SCHEDULED
                if materialization.trigger_kind is ScheduleTriggerKind.SCHEDULED
                else CollectionTriggerKind.MANUAL
            ),
            query=query,
            trace_id=materialization.trace_id,
            status=CollectionRunStatus.QUEUED,
            scheduled_for=materialization.scheduled_for,
            window_start=materialization.window_start,
            window_end=materialization.window_end,
            coalesced_count=materialization.coalesced_count,
            created_at=materialization.materialized_at,
            updated_at=materialization.materialized_at,
        )
    )


@dataclass(frozen=True, slots=True)
class SqlAlchemyIndustryCatalogRepository(
    IndustryCatalogRepository,
    IndustryCollectionRequestLoader,
):
    session_factory: AsyncSessionFactory

    async def get_preference(self, scope: WorkspaceScope) -> IndustryPreference | None:
        async with self.session_factory() as session:
            record = await session.get(
                UserIndustryPreference,
                {"workspace_id": scope.workspace_id, "user_id": scope.user_id},
            )
        if record is None:
            return None
        return IndustryPreference(
            workspace_id=record.workspace_id,
            user_id=record.user_id,
            industry=require_industry(record.industry_id),
            updated_at=record.updated_at.astimezone(UTC),
        )

    async def set_preference(
        self,
        scope: WorkspaceScope,
        industry: IndustryPreset,
    ) -> IndustryPreference:
        industry_id = industry.industry_id
        try:
            async with self.session_factory.begin() as session:
                database_now = await _database_now(session)
                statement = insert(UserIndustryPreference).values(
                    workspace_id=scope.workspace_id,
                    user_id=scope.user_id,
                    industry_id=industry_id,
                    created_at=database_now,
                    updated_at=database_now,
                )
                statement = statement.on_conflict_do_update(
                    index_elements=(
                        UserIndustryPreference.workspace_id,
                        UserIndustryPreference.user_id,
                    ),
                    set_={"industry_id": industry_id, "updated_at": database_now},
                )
                await session.execute(statement)
        except SQLAlchemyError as error:
            raise IndustryPersistenceError(sqlstate=safe_sqlstate(error)) from None
        return IndustryPreference(
            workspace_id=scope.workspace_id,
            user_id=scope.user_id,
            industry=require_industry(industry_id),
            updated_at=database_now,
        )

    async def list_source_items(
        self,
        scope: WorkspaceScope,
        *,
        industry_id: UUID,
        kind: SourceKind | None,
        limit: int,
        offset: int,
    ) -> tuple[SourceItemSummary, ...]:
        statement: Select[tuple[SourceItemRecord]] = select(SourceItemRecord).where(
            SourceItemRecord.workspace_id == scope.workspace_id,
            SourceItemRecord.industry_id == industry_id,
        )
        if kind is not None:
            statement = statement.where(SourceItemRecord.source_kind == kind)
        statement = (
            statement.order_by(SourceItemRecord.published_at.desc(), SourceItemRecord.id.desc())
            .limit(limit)
            .offset(offset)
        )
        async with self.session_factory() as session:
            records = tuple(await session.scalars(statement))
        return tuple(_source_summary(record) for record in records)

    async def list_collection_runs(
        self,
        scope: WorkspaceScope,
        *,
        limit: int,
    ) -> tuple[CollectionStatusSummary, ...]:
        async with self.session_factory() as session:
            records = tuple(
                await session.scalars(
                    select(CollectionRunRecord)
                    .where(CollectionRunRecord.workspace_id == scope.workspace_id)
                    .order_by(CollectionRunRecord.created_at.desc(), CollectionRunRecord.id.desc())
                    .limit(limit)
                )
            )
        return tuple(_collection_summary(record) for record in records)

    async def list_collection_schedules(
        self,
        scope: WorkspaceScope,
    ) -> tuple[CollectionScheduleSummary, ...]:
        async with self.session_factory() as session:
            records = tuple(
                await session.scalars(
                    select(Schedule)
                    .where(
                        Schedule.workspace_id == scope.workspace_id,
                        Schedule.task_name == INDUSTRY_COLLECTION_TASK_NAME,
                    )
                    .order_by(Schedule.name.asc())
                )
            )
        summaries: list[CollectionScheduleSummary] = []
        for record in records:
            payload = record.payload
            try:
                industry_id = UUID(str(payload["industry_id"]))
                kind = SourceKind(str(payload["source_kind"]))
            except (KeyError, TypeError, ValueError):
                raise IndustryPersistenceError from None
            summaries.append(
                CollectionScheduleSummary(
                    schedule_id=record.id,
                    industry_id=industry_id,
                    kind=kind,
                    cron_expression=record.cron_expression,
                    timezone_name=record.timezone_name,
                    next_due_at=(
                        record.next_due_at.astimezone(UTC)
                        if record.next_due_at is not None
                        else None
                    ),
                    last_fired_at=(
                        record.last_fired_at.astimezone(UTC)
                        if record.last_fired_at is not None
                        else None
                    ),
                    enabled=record.enabled,
                    misfire_policy=ScheduleMisfirePolicy(record.misfire_policy),
                    misfire_error_code=record.misfire_error_code,
                )
            )
        return tuple(summaries)

    async def collection_schedule_exists(self, scope: WorkspaceScope, schedule_id: UUID) -> bool:
        async with self.session_factory() as session:
            found = await session.scalar(
                select(Schedule.id).where(
                    Schedule.id == schedule_id,
                    Schedule.workspace_id == scope.workspace_id,
                    Schedule.task_name == INDUSTRY_COLLECTION_TASK_NAME,
                )
            )
        return found is not None

    async def load_collection_request(
        self,
        *,
        job_id: UUID,
        workspace_id: UUID,
        trace_id: TraceId,
    ) -> CollectionRunRequest:
        async with self.session_factory() as session:
            record = await session.scalar(
                select(CollectionRunRecord).where(
                    CollectionRunRecord.job_id == job_id,
                    CollectionRunRecord.workspace_id == workspace_id,
                    CollectionRunRecord.trace_id == trace_id,
                )
            )
        if record is None:
            raise IndustryNotFoundError
        return CollectionRunRequest(
            collection_run_id=record.id,
            job_id=record.job_id,
            workspace_id=record.workspace_id,
            industry_id=record.industry_id,
            kind=record.source_kind,
            query=record.query,
            trace_id=TraceId(record.trace_id),
        )


@dataclass(slots=True)
class SqlAlchemyIndustryCollectionWriter(IndustryCollectionWriter):
    session: AsyncSession

    async def claim(self, request: CollectionRunRequest) -> str | None:
        run = await self._locked_run(request)
        if run.status is CollectionRunStatus.SUCCEEDED:
            raise IndustryPersistenceError
        database_now = await _database_now(self.session)
        if run.status is not CollectionRunStatus.RUNNING:
            run.status = CollectionRunStatus.RUNNING
            run.started_at = database_now
            run.terminal_at = None
            run.last_error_code = None
            run.fetched_count = 0
            run.inserted_count = 0
            run.duplicate_count = 0
            run.updated_at = database_now
        cursor = await self.session.get(
            CollectionCursorRecord,
            {
                "workspace_id": request.workspace_id,
                "industry_id": request.industry_id,
                "data_source_id": provider_for_kind(request.kind).source_id,
            },
        )
        await self.session.flush()
        return None if cursor is None else cursor.cursor

    async def complete(
        self,
        request: CollectionRunRequest,
        page: ProviderPage,
    ) -> CollectionResult:
        run = await self._locked_run(request)
        definition = provider_for_kind(request.kind)
        if (
            run.status is not CollectionRunStatus.RUNNING
            or page.definition != definition
            or run.data_source_id != definition.source_id
        ):
            raise IndustryPersistenceError
        database_now = await _database_now(self.session)
        inserted_count = 0
        duplicate_count = 0
        for item in page.items:
            source_id, disposition = await self._persist_item(
                run,
                item,
                collected_at=database_now,
            )
            self.session.add(
                CollectionRunItemRecord(
                    collection_run_id=run.id,
                    external_id=item.external_id,
                    source_item_id=source_id,
                    content_sha256=bytes.fromhex(item.content_sha256),
                    disposition=disposition,
                )
            )
            if disposition is SourceItemDisposition.INSERTED:
                inserted_count += 1
            else:
                duplicate_count += 1
        run.status = CollectionRunStatus.SUCCEEDED
        run.terminal_at = database_now
        run.fetched_count = len(page.items)
        run.inserted_count = inserted_count
        run.duplicate_count = duplicate_count
        run.next_cursor = page.next_cursor
        run.updated_at = database_now
        await self._upsert_cursor_success(request, page, database_now=database_now)
        await self.session.flush()
        return CollectionResult(
            collection_run_id=run.id,
            provider=definition.provider,
            fetched_count=len(page.items),
            inserted_count=inserted_count,
            duplicate_count=duplicate_count,
            next_cursor=page.next_cursor,
        )

    async def fail(self, request: CollectionRunRequest, *, error_code: str) -> None:
        run = await self._locked_run(request)
        if run.status is CollectionRunStatus.SUCCEEDED:
            raise IndustryPersistenceError
        database_now = await _database_now(self.session)
        if run.started_at is None:
            run.started_at = database_now
        run.status = CollectionRunStatus.FAILED
        run.terminal_at = database_now
        run.last_error_code = error_code
        run.updated_at = database_now
        definition = provider_for_kind(request.kind)
        statement = insert(CollectionCursorRecord).values(
            workspace_id=request.workspace_id,
            industry_id=request.industry_id,
            data_source_id=definition.source_id,
            source_kind=request.kind,
            last_failure_at=database_now,
            last_error_code=error_code,
            failure_count=1,
            created_at=database_now,
            updated_at=database_now,
        )
        statement = statement.on_conflict_do_update(
            index_elements=(
                CollectionCursorRecord.workspace_id,
                CollectionCursorRecord.industry_id,
                CollectionCursorRecord.data_source_id,
            ),
            set_={
                "last_failure_at": database_now,
                "last_error_code": error_code,
                "failure_count": CollectionCursorRecord.failure_count + 1,
                "updated_at": database_now,
            },
        )
        await self.session.execute(statement)
        await self.session.flush()

    async def _locked_run(self, request: CollectionRunRequest) -> CollectionRunRecord:
        run = await self.session.scalar(
            select(CollectionRunRecord)
            .where(
                CollectionRunRecord.id == request.collection_run_id,
                CollectionRunRecord.job_id == request.job_id,
                CollectionRunRecord.workspace_id == request.workspace_id,
                CollectionRunRecord.industry_id == request.industry_id,
                CollectionRunRecord.source_kind == request.kind,
                CollectionRunRecord.query == request.query,
                CollectionRunRecord.trace_id == request.trace_id,
            )
            .with_for_update()
        )
        if run is None:
            raise IndustryNotFoundError
        return run

    async def _persist_item(
        self,
        run: CollectionRunRecord,
        item: ProviderItem,
        *,
        collected_at: datetime,
    ) -> tuple[UUID, SourceItemDisposition]:
        source_item_id = uuid4()
        content_hash = bytes.fromhex(item.content_sha256)
        statement = (
            insert(SourceItemRecord)
            .values(
                id=source_item_id,
                workspace_id=run.workspace_id,
                industry_id=run.industry_id,
                data_source_id=run.data_source_id,
                source_kind=run.source_kind,
                external_id=item.external_id,
                title=item.title,
                summary=item.summary,
                locator=item.locator,
                published_at=item.published_at,
                collected_at=collected_at,
                content_sha256=content_hash,
                source_metadata=dict(item.metadata),
                usage_constraints=provider_for_kind(run.source_kind).usage_constraints,
            )
            .on_conflict_do_nothing()
            .returning(SourceItemRecord.id)
        )
        inserted_id = await self.session.scalar(statement)
        if isinstance(inserted_id, UUID):
            await self._persist_domain_extension(inserted_id, run.source_kind, item.metadata)
            return inserted_id, SourceItemDisposition.INSERTED
        matches = tuple(
            await self.session.scalars(
                select(SourceItemRecord).where(
                    SourceItemRecord.workspace_id == run.workspace_id,
                    SourceItemRecord.data_source_id == run.data_source_id,
                    or_(
                        SourceItemRecord.external_id == item.external_id,
                        SourceItemRecord.content_sha256 == content_hash,
                    ),
                )
            )
        )
        if len(matches) != 1:
            raise IndustryPersistenceError
        existing = matches[0]
        disposition = (
            SourceItemDisposition.DUPLICATE_EXTERNAL_ID
            if existing.external_id == item.external_id
            else SourceItemDisposition.DUPLICATE_CONTENT
        )
        return existing.id, disposition

    async def _persist_domain_extension(
        self,
        source_item_id: UUID,
        kind: SourceKind,
        metadata: Mapping[str, object],
    ) -> None:
        if kind is SourceKind.NEWS:
            self.session.add(
                NewsItemRecord(
                    source_item_id=source_item_id,
                    source_kind=kind.value,
                    category=_metadata_text(metadata, "category", 100),
                )
            )
        elif kind is SourceKind.POLICY:
            self.session.add(
                PolicyItemRecord(
                    source_item_id=source_item_id,
                    source_kind=kind.value,
                    jurisdiction=_metadata_text(metadata, "jurisdiction", 100),
                    document_number=_metadata_text(metadata, "document_number", 100),
                    agency=_metadata_text(metadata, "agency", 500),
                )
            )
        elif kind is SourceKind.TENDER:
            self.session.add(
                BiddingItemRecord(
                    source_item_id=source_item_id,
                    source_kind=kind.value,
                    notice_type=_metadata_text(metadata, "notice_type", 100),
                    region=_metadata_text(metadata, "region", 100),
                )
            )
        else:
            observed_at_text = _metadata_text(metadata, "observed_at", 64)
            try:
                observed_at = datetime.fromisoformat(observed_at_text)
                price = Decimal(_metadata_text(metadata, "price", 64))
            except (ValueError, TypeError):
                raise IndustryPersistenceError from None
            if observed_at.tzinfo is None:
                raise IndustryPersistenceError
            self.session.add(
                MarketSnapshotRecord(
                    source_item_id=source_item_id,
                    source_kind=kind.value,
                    symbol=_metadata_text(metadata, "symbol", 16),
                    price=price,
                    currency=_metadata_text(metadata, "currency", 3),
                    observed_at=observed_at.astimezone(UTC),
                )
            )

    async def _upsert_cursor_success(
        self,
        request: CollectionRunRequest,
        page: ProviderPage,
        *,
        database_now: datetime,
    ) -> None:
        statement = insert(CollectionCursorRecord).values(
            workspace_id=request.workspace_id,
            industry_id=request.industry_id,
            data_source_id=page.definition.source_id,
            source_kind=request.kind,
            cursor=page.next_cursor,
            last_success_at=database_now,
            success_count=1,
            failure_count=0,
            created_at=database_now,
            updated_at=database_now,
        )
        statement = statement.on_conflict_do_update(
            index_elements=(
                CollectionCursorRecord.workspace_id,
                CollectionCursorRecord.industry_id,
                CollectionCursorRecord.data_source_id,
            ),
            set_={
                "cursor": page.next_cursor,
                "last_success_at": database_now,
                "last_error_code": None,
                "success_count": CollectionCursorRecord.success_count + 1,
                "updated_at": database_now,
            },
        )
        await self.session.execute(statement)


@dataclass(frozen=True, slots=True)
class SqlAlchemyIndustryCollectionTransactionFactory(IndustryCollectionTransactionFactory):
    session_factory: AsyncSessionFactory

    def __call__(self) -> AbstractAsyncContextManager[IndustryCollectionWriter]:
        return self._transaction()

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[IndustryCollectionWriter]:
        try:
            async with self.session_factory.begin() as session:
                yield SqlAlchemyIndustryCollectionWriter(session)
        except IndustryNotFoundError:
            raise
        except SQLAlchemyError as error:
            raise IndustryPersistenceError(sqlstate=safe_sqlstate(error)) from None


async def _database_now(session: AsyncSession) -> datetime:
    result = await session.scalar(select(func.clock_timestamp()))
    if not isinstance(result, datetime):
        raise IndustryPersistenceError
    return result.astimezone(UTC)


def _metadata_text(metadata: Mapping[str, object], key: str, maximum: int) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise IndustryPersistenceError
    return value


def _source_summary(record: SourceItemRecord) -> SourceItemSummary:
    definition = provider_for_kind(record.source_kind)
    return SourceItemSummary(
        source_item_id=record.id,
        industry_id=record.industry_id,
        kind=record.source_kind,
        provider=definition.provider,
        external_id=record.external_id,
        title=record.title,
        summary=record.summary,
        locator=record.locator,
        published_at=record.published_at.astimezone(UTC),
        collected_at=record.collected_at.astimezone(UTC),
        content_sha256=record.content_sha256.hex(),
        metadata=record.source_metadata,
    )


def _collection_summary(record: CollectionRunRecord) -> CollectionStatusSummary:
    return CollectionStatusSummary(
        collection_run_id=record.id,
        industry_id=record.industry_id,
        kind=record.source_kind,
        provider=provider_for_kind(record.source_kind).provider,
        status=record.status,
        scheduled_for=(
            record.scheduled_for.astimezone(UTC) if record.scheduled_for is not None else None
        ),
        started_at=record.started_at.astimezone(UTC) if record.started_at is not None else None,
        terminal_at=(
            record.terminal_at.astimezone(UTC) if record.terminal_at is not None else None
        ),
        last_error_code=record.last_error_code,
        fetched_count=record.fetched_count,
        inserted_count=record.inserted_count,
        duplicate_count=record.duplicate_count,
    )
