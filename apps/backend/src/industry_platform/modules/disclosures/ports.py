"""Ports for official SEC sources and the canonical filer catalog."""

from typing import Protocol

from industry_platform.modules.disclosures.domain import (
    FilingSelectionScope,
    SecFiler,
    SecFilerCatalogSnapshot,
    SecFilingDataset,
    SecSubmissionSet,
    SecSubmissionSourceSnapshot,
)


class SecEdgarPort(Protocol):
    async def fetch_filer_catalog(self) -> SecFilerCatalogSnapshot: ...


class SecSubmissionsPort(Protocol):
    async def fetch_submission_set(self, scope: FilingSelectionScope) -> SecSubmissionSet: ...


class SecSubmissionSnapshotStore(Protocol):
    async def persist(self, source: SecSubmissionSourceSnapshot) -> str: ...


class SecFilerCatalogRepository(Protocol):
    async def replace_catalog(self, snapshot: SecFilerCatalogSnapshot) -> None: ...

    async def search(
        self,
        *,
        cik: str | None,
        normalized_name: str,
        ticker: str | None,
        limit: int,
    ) -> tuple[SecFiler, ...]: ...


class SecFilingRepository(Protocol):
    async def replace_submission_set(
        self,
        snapshot: SecSubmissionSet,
        *,
        object_keys: dict[str, str],
        scope: FilingSelectionScope,
    ) -> str: ...

    async def load_dataset(
        self,
        *,
        coverage_version: str,
        scope: FilingSelectionScope,
    ) -> SecFilingDataset: ...
