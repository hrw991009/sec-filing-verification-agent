"""Prove the industry schedule-to-collection slice against real PostgreSQL."""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from industry_platform.core.database import create_database_engine, create_database_session_factory
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.identity.models import (
    User,
    UserStatus,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
    WorkspaceStatus,
)
from industry_platform.modules.industry.adapters.sqlalchemy import (
    SqlAlchemyIndustryCatalogRepository,
    SqlAlchemyIndustryCollectionTransactionFactory,
    industry_collection_occurrence_observer,
)
from industry_platform.modules.industry.domain import (
    ENERGY_POWER_INDUSTRY_ID,
    CollectionRunStatus,
    CollectionScheduleRequest,
    ProviderCode,
    ProviderItem,
    ProviderPage,
    ProviderQuery,
    ProviderReadiness,
    ProviderStatus,
    SourceItemDisposition,
    SourceKind,
    provider_for_kind,
)
from industry_platform.modules.industry.models import (
    CollectionCursorRecord,
    CollectionRunItemRecord,
    CollectionRunRecord,
    NewsItemRecord,
    SourceItemRecord,
)
from industry_platform.modules.industry.service import (
    IndustryCatalogService,
    IndustryCollectionService,
    IndustryScheduleService,
)
from industry_platform.modules.jobs.domain import ScheduleMisfirePolicy
from industry_platform.modules.jobs.models import Job, OutboxEvent, ScheduleOccurrence
from industry_platform.modules.jobs.resources import create_job_resources
from industry_platform.modules.workspaces.domain import WorkspaceAccessDeniedError, WorkspaceScope
from industry_platform.server import create_selector_event_loop

from .postgres import PostgresProbe


@dataclass(slots=True)
class DeterministicWorldBankProvider:
    """Exercise the real Provider contract without making CI depend on the internet."""

    queries: list[ProviderQuery] = field(default_factory=list)

    @property
    def status(self) -> ProviderStatus:
        return ProviderStatus(
            provider=ProviderCode.WORLD_BANK_NEWS,
            kind=SourceKind.NEWS,
            readiness=ProviderReadiness.READY,
            reason_code=None,
        )

    async def fetch(self, query: ProviderQuery) -> ProviderPage:
        self.queries.append(query)
        return ProviderPage(
            definition=provider_for_kind(SourceKind.NEWS),
            items=(
                ProviderItem(
                    kind=SourceKind.NEWS,
                    provider=ProviderCode.WORLD_BANK_NEWS,
                    external_id="world-bank-energy-2026-08-17",
                    title="World Bank energy transition update",
                    summary="A deterministic normalized source snapshot for collection tests.",
                    locator="https://www.worldbank.org/en/news/feature/2026/08/17/energy",
                    published_at=datetime(2026, 8, 17, 1, 2, 3, tzinfo=UTC),
                    metadata={"category": "Feature Story"},
                ),
            ),
            next_cursor="next-page-2",
            fetched_at=datetime(2026, 8, 17, 2, 0, tzinfo=UTC),
        )


@dataclass(frozen=True, slots=True)
class NewsOnlyRegistry:
    provider_adapter: DeterministicWorldBankProvider

    def provider(self, kind: SourceKind) -> DeterministicWorldBankProvider:
        if kind is not SourceKind.NEWS:
            raise AssertionError(f"Unexpected Provider kind: {kind}")
        return self.provider_adapter

    def statuses(self) -> tuple[ProviderStatus, ...]:
        return (self.provider_adapter.status,)


def test_schedule_collection_dedup_and_workspace_policy_are_durable(
    migrated_postgres_probe: PostgresProbe,
) -> None:
    async def exercise() -> None:
        engine = create_database_engine(migrated_postgres_probe.settings)
        session_factory = create_database_session_factory(engine)
        owner_id = uuid4()
        viewer_id = uuid4()
        workspace_id = uuid4()
        other_workspace_id = uuid4()
        owner_scope = WorkspaceScope(workspace_id, owner_id, "owner")
        viewer_scope = WorkspaceScope(workspace_id, viewer_id, "viewer")
        other_scope = WorkspaceScope(other_workspace_id, owner_id, "owner")

        try:
            async with session_factory.begin() as session:
                session.add_all(
                    (
                        User(
                            id=owner_id,
                            email=f"industry-owner-{owner_id}@example.test",
                            password_hash=str(owner_id),
                            status=UserStatus.ACTIVE,
                        ),
                        User(
                            id=viewer_id,
                            email=f"industry-viewer-{viewer_id}@example.test",
                            password_hash=str(viewer_id),
                            status=UserStatus.ACTIVE,
                        ),
                        Workspace(
                            id=workspace_id,
                            name="Industry collection",
                            created_by_user_id=owner_id,
                            status=WorkspaceStatus.ACTIVE,
                        ),
                        Workspace(
                            id=other_workspace_id,
                            name="Other tenant",
                            created_by_user_id=owner_id,
                            status=WorkspaceStatus.ACTIVE,
                        ),
                        WorkspaceMembership(
                            id=uuid4(),
                            workspace_id=workspace_id,
                            user_id=owner_id,
                            role=WorkspaceRole.OWNER,
                        ),
                        WorkspaceMembership(
                            id=uuid4(),
                            workspace_id=workspace_id,
                            user_id=viewer_id,
                            role=WorkspaceRole.VIEWER,
                        ),
                        WorkspaceMembership(
                            id=uuid4(),
                            workspace_id=other_workspace_id,
                            user_id=owner_id,
                            role=WorkspaceRole.OWNER,
                        ),
                    )
                )

            repository = SqlAlchemyIndustryCatalogRepository(session_factory)
            catalog = IndustryCatalogService(repository)
            jobs = create_job_resources(
                migrated_postgres_probe.settings,
                session_factory,
                occurrence_observer=industry_collection_occurrence_observer,
            )
            schedules = IndustryScheduleService(repository, jobs.schedule_service)
            provider = DeterministicWorldBankProvider()
            collections = IndustryCollectionService(
                transaction_factory=SqlAlchemyIndustryCollectionTransactionFactory(session_factory),
                providers=NewsOnlyRegistry(provider),
                request_loader=repository,
            )

            preference = await catalog.set_preference(owner_scope, ENERGY_POWER_INDUSTRY_ID)
            assert preference.industry.industry_id == ENERGY_POWER_INDUSTRY_ID
            assert await catalog.get_preference(other_scope) is None
            with pytest.raises(WorkspaceAccessDeniedError):
                await catalog.set_preference(viewer_scope, ENERGY_POWER_INDUSTRY_ID)

            ensured = await schedules.ensure_schedule(
                CollectionScheduleRequest(
                    scope=owner_scope,
                    industry_id=ENERGY_POWER_INDUSTRY_ID,
                    kind=SourceKind.NEWS,
                    cron_expression="17 * * * *",
                    timezone_name="Asia/Shanghai",
                    misfire_policy=ScheduleMisfirePolicy.COALESCE_LATEST,
                    catch_up_window_seconds=86_400,
                    max_catch_up=24,
                )
            )

            trigger_id = uuid4()
            first_pair = await asyncio.gather(
                schedules.trigger_manual(
                    owner_scope,
                    schedule_id=ensured.schedule_id,
                    trigger_id=trigger_id,
                ),
                schedules.trigger_manual(
                    owner_scope,
                    schedule_id=ensured.schedule_id,
                    trigger_id=trigger_id,
                ),
            )
            assert len({result.occurrence_id for result in first_pair}) == 1
            assert len({result.job_id for result in first_pair}) == 1
            assert sum(result.created for result in first_pair) == 1
            first = first_pair[0]

            async with session_factory() as session:
                first_run = await session.get(CollectionRunRecord, first.occurrence_id)
                assert first_run is not None
                assert first_run.id == first.occurrence_id
                assert first_run.job_id == first.job_id
                assert first_run.status is CollectionRunStatus.QUEUED
                trace_id = TraceId(first_run.trace_id)
                graph_counts = (
                    await session.scalar(select(func.count()).select_from(ScheduleOccurrence)),
                    await session.scalar(select(func.count()).select_from(Job)),
                    await session.scalar(select(func.count()).select_from(OutboxEvent)),
                    await session.scalar(select(func.count()).select_from(CollectionRunRecord)),
                )
            assert graph_counts == (1, 1, 1, 1)

            inserted = await collections.collect_job(
                job_id=first.job_id,
                workspace_id=workspace_id,
                trace_id=trace_id,
            )
            assert (inserted.fetched_count, inserted.inserted_count, inserted.duplicate_count) == (
                1,
                1,
                0,
            )

            second = await schedules.trigger_manual(
                owner_scope,
                schedule_id=ensured.schedule_id,
                trigger_id=uuid4(),
            )
            async with session_factory() as session:
                second_run = await session.get(CollectionRunRecord, second.occurrence_id)
                assert second_run is not None
                second_trace_id = TraceId(second_run.trace_id)
            duplicate = await collections.collect_job(
                job_id=second.job_id,
                workspace_id=workspace_id,
                trace_id=second_trace_id,
            )
            assert (
                duplicate.fetched_count,
                duplicate.inserted_count,
                duplicate.duplicate_count,
            ) == (
                1,
                0,
                1,
            )
            assert [query.cursor for query in provider.queries] == [None, "next-page-2"]

            items = await catalog.list_items(
                owner_scope,
                industry_id=ENERGY_POWER_INDUSTRY_ID,
                kind=SourceKind.NEWS,
                limit=10,
                offset=0,
            )
            assert len(items) == 1
            assert items[0].provider is ProviderCode.WORLD_BANK_NEWS
            assert (
                await catalog.list_items(
                    other_scope,
                    industry_id=ENERGY_POWER_INDUSTRY_ID,
                    kind=SourceKind.NEWS,
                    limit=10,
                    offset=0,
                )
                == ()
            )

            async with session_factory() as session:
                persisted_runs = tuple(
                    await session.scalars(
                        select(CollectionRunRecord).order_by(
                            CollectionRunRecord.created_at,
                            CollectionRunRecord.id,
                        )
                    )
                )
                source_count = await session.scalar(
                    select(func.count()).select_from(SourceItemRecord)
                )
                extension_count = await session.scalar(
                    select(func.count()).select_from(NewsItemRecord)
                )
                cursor = await session.get(
                    CollectionCursorRecord,
                    {
                        "workspace_id": workspace_id,
                        "industry_id": ENERGY_POWER_INDUSTRY_ID,
                        "data_source_id": provider_for_kind(SourceKind.NEWS).source_id,
                    },
                )
                dispositions = tuple(
                    await session.scalars(
                        select(CollectionRunItemRecord.disposition).order_by(
                            CollectionRunItemRecord.collection_run_id
                        )
                    )
                )
            assert tuple(
                (run.status, run.inserted_count, run.duplicate_count) for run in persisted_runs
            ) == (
                (CollectionRunStatus.SUCCEEDED, 1, 0),
                (CollectionRunStatus.SUCCEEDED, 0, 1),
            )
            assert source_count == extension_count == 1
            assert cursor is not None
            assert cursor.cursor == "next-page-2"
            assert cursor.success_count == 2
            assert set(dispositions) == {
                SourceItemDisposition.INSERTED,
                SourceItemDisposition.DUPLICATE_EXTERNAL_ID,
            }
        finally:
            await engine.dispose()

    with asyncio.Runner(loop_factory=create_selector_event_loop) as runner:
        runner.run(exercise())
