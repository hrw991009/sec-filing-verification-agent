"""Ports for industry context, external Providers, and durable collection."""

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from typing import Protocol
from uuid import UUID

from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.industry.domain import (
    CollectionResult,
    CollectionRunRequest,
    CollectionScheduleSummary,
    CollectionStatusSummary,
    IndustryPreference,
    IndustryPreset,
    ProviderPage,
    ProviderQuery,
    ProviderStatus,
    SourceItemSummary,
    SourceKind,
)
from industry_platform.modules.workspaces.domain import WorkspaceScope


class IndustrySourceProvider(Protocol):
    """One fixed-host, bounded external source Adapter."""

    @property
    def status(self) -> ProviderStatus: ...

    async def fetch(self, query: ProviderQuery) -> ProviderPage: ...


class IndustryCatalogRepository(Protocol):
    async def get_preference(self, scope: WorkspaceScope) -> IndustryPreference | None: ...

    async def set_preference(
        self,
        scope: WorkspaceScope,
        industry: IndustryPreset,
    ) -> IndustryPreference: ...

    async def list_source_items(
        self,
        scope: WorkspaceScope,
        *,
        industry_id: UUID,
        kind: SourceKind | None,
        limit: int,
        offset: int,
    ) -> tuple[SourceItemSummary, ...]: ...

    async def list_collection_runs(
        self,
        scope: WorkspaceScope,
        *,
        limit: int,
    ) -> tuple[CollectionStatusSummary, ...]: ...

    async def list_collection_schedules(
        self,
        scope: WorkspaceScope,
    ) -> tuple[CollectionScheduleSummary, ...]: ...

    async def collection_schedule_exists(
        self,
        scope: WorkspaceScope,
        schedule_id: UUID,
    ) -> bool: ...


class IndustryCollectionWriter(Protocol):
    async def claim(self, request: CollectionRunRequest) -> str | None: ...

    async def complete(
        self,
        request: CollectionRunRequest,
        page: ProviderPage,
    ) -> CollectionResult: ...

    async def fail(self, request: CollectionRunRequest, *, error_code: str) -> None: ...


class IndustryCollectionTransactionFactory(Protocol):
    def __call__(self) -> AbstractAsyncContextManager[IndustryCollectionWriter]: ...


class IndustryCollectionRequestLoader(Protocol):
    async def load_collection_request(
        self,
        *,
        job_id: UUID,
        workspace_id: UUID,
        trace_id: TraceId,
    ) -> CollectionRunRequest: ...


class IndustryCollectionUseCase(Protocol):
    async def collect(self, request: CollectionRunRequest) -> CollectionResult: ...

    async def collect_job(
        self,
        *,
        job_id: UUID,
        workspace_id: UUID,
        trace_id: TraceId,
    ) -> CollectionResult: ...


class ProviderRegistryPort(Protocol):
    def provider(self, kind: SourceKind) -> IndustrySourceProvider: ...

    def statuses(self) -> tuple[ProviderStatus, ...]: ...


def provider_statuses(
    providers: Sequence[IndustrySourceProvider],
) -> tuple[ProviderStatus, ...]:
    return tuple(provider.status for provider in providers)
