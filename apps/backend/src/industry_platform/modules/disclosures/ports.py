"""Ports for official SEC sources and the canonical filer catalog."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from industry_platform.modules.disclosures.domain import (
    FilingSelectionScope,
    SecCanonicalFiling,
    SecFiler,
    SecFilerCatalogSnapshot,
    SecFilingArchive,
    SecFilingContentPreparation,
    SecFilingDataset,
    SecFilingDocumentSnapshot,
    SecFilingSearchHit,
    SecFilingSection,
    SecFilingSnapshotReference,
    SecSubmissionSet,
    SecSubmissionSourceSnapshot,
    SecWorkspaceFilingImport,
    SecXbrlDataset,
    SecXbrlFactQuery,
    SecXbrlFactResult,
    SecXbrlSourceSnapshot,
    SecXbrlSyncPreparation,
    SecXbrlSyncResult,
)
from industry_platform.modules.retrieval.domain import DenseCandidate
from industry_platform.modules.workspaces.domain import WorkspaceScope


class SecEdgarPort(Protocol):
    async def fetch_filer_catalog(self) -> SecFilerCatalogSnapshot: ...


class SecSubmissionsPort(Protocol):
    async def fetch_submission_set(self, scope: FilingSelectionScope) -> SecSubmissionSet: ...


class SecSubmissionSnapshotStore(Protocol):
    async def persist(self, source: SecSubmissionSourceSnapshot) -> str: ...


class SecFilingArchivePort(Protocol):
    async def fetch_archive(self, filing: SecCanonicalFiling) -> SecFilingArchive: ...


class SecFilingDocumentSnapshotStore(Protocol):
    async def persist(self, source: SecFilingDocumentSnapshot) -> str: ...


class SecCompanyFactsPort(Protocol):
    async def fetch(self, filing: SecCanonicalFiling) -> SecXbrlSourceSnapshot: ...


class SecXbrlSnapshotStore(Protocol):
    async def persist_aggregate(self, source: SecXbrlSourceSnapshot) -> str: ...

    async def read_raw(
        self,
        source: SecFilingSnapshotReference,
        *,
        cik: str,
    ) -> SecXbrlSourceSnapshot: ...


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


class SecFilingContentRepository(Protocol):
    async def get_canonical_filing(self, accession: str) -> SecCanonicalFiling: ...

    async def persist_archive(
        self,
        archive: SecFilingArchive,
        *,
        object_keys: dict[str, str],
    ) -> tuple[SecFilingSnapshotReference, ...]: ...

    async def find_import(
        self,
        scope: WorkspaceScope,
        *,
        accession: str,
        knowledge_base_id: UUID,
        primary_snapshot_id: UUID,
    ) -> SecWorkspaceFilingImport | None: ...

    async def record_import(
        self,
        scope: WorkspaceScope,
        *,
        accession: str,
        knowledge_base_id: UUID,
        primary_snapshot_id: UUID,
        complete_submission_snapshot_id: UUID,
        file_id: UUID,
        document_id: UUID,
        document_version_id: UUID,
        ingestion_job_id: UUID,
        observed_at: datetime,
    ) -> SecWorkspaceFilingImport: ...

    async def list_imports(
        self,
        scope: WorkspaceScope,
        *,
        limit: int,
    ) -> tuple[SecWorkspaceFilingImport, ...]: ...

    async def get_import(
        self,
        scope: WorkspaceScope,
        import_id: UUID,
    ) -> SecWorkspaceFilingImport: ...

    async def prepare_content(
        self,
        scope: WorkspaceScope,
        *,
        knowledge_base_ids: tuple[UUID, ...],
        accession: str,
        as_of: datetime,
    ) -> SecFilingContentPreparation: ...

    async def resolve_candidates(
        self,
        scope: WorkspaceScope,
        *,
        preparation: SecFilingContentPreparation,
        candidates: tuple[DenseCandidate, ...],
    ) -> tuple[SecFilingSearchHit, ...]: ...

    async def read_section(
        self,
        scope: WorkspaceScope,
        *,
        accession: str,
        as_of: datetime,
        knowledge_base_ids: tuple[UUID, ...],
        document_version_id: UUID,
        chunk_id: UUID,
    ) -> SecFilingSection: ...


class SecXbrlRepository(Protocol):
    async def prepare_sync(
        self,
        scope: WorkspaceScope,
        *,
        accession: str,
        knowledge_base_id: UUID,
    ) -> SecXbrlSyncPreparation: ...

    async def persist_dataset(
        self,
        dataset: SecXbrlDataset,
        *,
        aggregate_object_keys: dict[str, str],
    ) -> SecXbrlSyncResult: ...

    async def query_facts(
        self,
        scope: WorkspaceScope,
        *,
        knowledge_base_ids: tuple[UUID, ...],
        accession: str,
        as_of: datetime,
        query: SecXbrlFactQuery,
    ) -> SecXbrlFactResult: ...
