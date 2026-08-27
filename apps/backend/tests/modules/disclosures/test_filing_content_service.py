"""Locked filing import and Dense retrieval service contracts."""

from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest

from industry_platform.modules.disclosures.adapters.sec_archives import (
    FrozenSecFilingArchiveAdapter,
)
from industry_platform.modules.disclosures.domain import (
    SecCanonicalFiling,
    SecFilingArchive,
    SecFilingContentError,
    SecFilingContentPreparation,
    SecFilingContentStatus,
    SecFilingDocumentKind,
    SecFilingDocumentSnapshot,
    SecFilingForm,
    SecFilingImportStatus,
    SecFilingSearchHit,
    SecFilingSection,
    SecFilingSnapshotReference,
    SecFilingSnapshotStatus,
    SecSourceErrorCode,
    SecWorkspaceFilingImport,
    sec_complete_submission_url,
    sec_primary_document_url,
    sha256_hex,
)
from industry_platform.modules.disclosures.filing_content_service import (
    SecFilingContentService,
    SecFilingImportService,
)
from industry_platform.modules.disclosures.ports import (
    SecFilingArchivePort,
    SecFilingContentRepository,
    SecFilingDocumentSnapshotStore,
)
from industry_platform.modules.financial_verification.domain import (
    FinancialForm,
    FinancialScope,
)
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.knowledge.domain import ImportKnowledgeTextSource
from industry_platform.modules.knowledge.service import KnowledgeApplicationService
from industry_platform.modules.retrieval.domain import DenseCandidate
from industry_platform.modules.retrieval.ports import DenseIndexPort
from industry_platform.modules.workspaces.domain import WorkspaceScope

NOW = datetime(2026, 8, 26, 4, 0, tzinfo=UTC)
WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
USER_ID = UUID("22222222-2222-4222-8222-222222222222")
KNOWLEDGE_BASE_ID = UUID("33333333-3333-4333-8333-333333333333")
FILING_ID = UUID("44444444-4444-4444-8444-444444444444")
PRIMARY_DOCUMENT_ID = UUID("55555555-5555-4555-8555-555555555555")
PRIMARY_SNAPSHOT_ID = UUID("66666666-6666-4666-8666-666666666666")
COMPLETE_DOCUMENT_ID = UUID("77777777-7777-4777-8777-777777777777")
COMPLETE_SNAPSHOT_ID = UUID("88888888-8888-4888-8888-888888888888")
FILE_ID = UUID("99999999-9999-4999-8999-999999999999")
DOCUMENT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
VERSION_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
JOB_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
IMPORT_ID = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
CHUNK_ID = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
ACCESSION = "0000320193-23-000106"


def canonical_filing() -> SecCanonicalFiling:
    accepted = datetime(2023, 11, 3, 6, 1, tzinfo=UTC)
    return SecCanonicalFiling(
        id=FILING_ID,
        cik="0000320193",
        accession=ACCESSION,
        form=SecFilingForm.TEN_K,
        report_date=date(2023, 9, 30),
        filed_date=date(2023, 11, 3),
        accepted_at=accepted,
        public_available_at=accepted,
        primary_document="aapl-20230930.htm",
        source_available_at=accepted,
    )


def filing_archive() -> SecFilingArchive:
    filing = canonical_filing()
    complete_body = b"<SEC-DOCUMENT>complete</SEC-DOCUMENT>"
    primary_body = b"<html><h1>Net sales</h1><script>ignore()</script><p>Increased.</p></html>"
    return SecFilingArchive(
        filing=filing,
        documents=(
            SecFilingDocumentSnapshot(
                kind=SecFilingDocumentKind.COMPLETE_SUBMISSION,
                cik=filing.cik,
                accession=filing.accession,
                filename=f"{filing.accession}.txt",
                source_url=sec_complete_submission_url(filing.cik, filing.accession),
                source_version="sec-filing-complete-v1",
                content_type="text/plain",
                content_sha256=sha256_hex(complete_body),
                byte_size=len(complete_body),
                retrieved_at=NOW,
                source_available_at=filing.accepted_at,
                body=complete_body,
            ),
            SecFilingDocumentSnapshot(
                kind=SecFilingDocumentKind.PRIMARY_DOCUMENT,
                cik=filing.cik,
                accession=filing.accession,
                filename=filing.primary_document,
                source_url=sec_primary_document_url(
                    filing.cik,
                    filing.accession,
                    filing.primary_document,
                ),
                source_version="sec-filing-primary-v1",
                content_type="text/html",
                content_sha256=sha256_hex(primary_body),
                byte_size=len(primary_body),
                retrieved_at=NOW,
                source_available_at=filing.accepted_at,
                body=primary_body,
            ),
        ),
    )


def workspace_import(
    *, status: SecFilingImportStatus = SecFilingImportStatus.READY
) -> SecWorkspaceFilingImport:
    return SecWorkspaceFilingImport(
        id=IMPORT_ID,
        workspace_id=WORKSPACE_ID,
        filing_id=FILING_ID,
        accession=ACCESSION,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        primary_snapshot_id=PRIMARY_SNAPSHOT_ID,
        complete_submission_snapshot_id=COMPLETE_SNAPSHOT_ID,
        file_id=FILE_ID,
        document_id=DOCUMENT_ID,
        document_version_id=VERSION_ID,
        ingestion_job_id=JOB_ID,
        status=status,
        created_at=NOW,
        updated_at=NOW,
    )


def snapshot_references() -> tuple[SecFilingSnapshotReference, ...]:
    archive = filing_archive()
    identifiers = {
        SecFilingDocumentKind.COMPLETE_SUBMISSION: (
            COMPLETE_DOCUMENT_ID,
            COMPLETE_SNAPSHOT_ID,
        ),
        SecFilingDocumentKind.PRIMARY_DOCUMENT: (
            PRIMARY_DOCUMENT_ID,
            PRIMARY_SNAPSHOT_ID,
        ),
    }
    return tuple(
        SecFilingSnapshotReference(
            document_id=identifiers[source.kind][0],
            snapshot_id=identifiers[source.kind][1],
            filing_id=FILING_ID,
            kind=source.kind,
            filename=source.filename,
            source_url=source.source_url,
            source_version=source.source_version,
            content_type=source.content_type,
            content_sha256=source.content_sha256,
            byte_size=source.byte_size,
            retrieved_at=source.retrieved_at,
            source_available_at=source.source_available_at,
            status=SecFilingSnapshotStatus.ACTIVE,
            object_bucket="private",
            object_key=f"snapshots/{source.content_sha256}",
        )
        for source in archive.documents
    )


@dataclass(slots=True)
class MemoryRepository:
    references: tuple[SecFilingSnapshotReference, ...] = field(default_factory=snapshot_references)
    existing: SecWorkspaceFilingImport | None = None
    preparation: SecFilingContentPreparation | None = None
    persist_calls: int = 0
    record_calls: int = 0
    resolve_calls: int = 0
    prepare_calls: int = 0

    async def get_canonical_filing(self, accession: str) -> SecCanonicalFiling:
        assert accession == ACCESSION
        return canonical_filing()

    async def persist_archive(
        self,
        archive: SecFilingArchive,
        *,
        object_keys: dict[str, str],
    ) -> tuple[SecFilingSnapshotReference, ...]:
        assert archive == filing_archive()
        assert set(object_keys) == {source.source_url for source in archive.documents}
        self.persist_calls += 1
        return self.references

    async def find_import(
        self,
        scope: WorkspaceScope,
        *,
        accession: str,
        knowledge_base_id: UUID,
        primary_snapshot_id: UUID,
    ) -> SecWorkspaceFilingImport | None:
        del scope, accession, knowledge_base_id, primary_snapshot_id
        return self.existing

    async def record_import(
        self, scope: WorkspaceScope, **values: object
    ) -> SecWorkspaceFilingImport:
        assert scope.workspace_id == WORKSPACE_ID
        assert values["file_id"] == FILE_ID
        self.record_calls += 1
        return workspace_import(status=SecFilingImportStatus.QUEUED)

    async def list_imports(
        self, scope: WorkspaceScope, *, limit: int
    ) -> tuple[SecWorkspaceFilingImport, ...]:
        del scope, limit
        return ()

    async def get_import(self, scope: WorkspaceScope, import_id: UUID) -> SecWorkspaceFilingImport:
        del scope, import_id
        return workspace_import()

    async def prepare_content(
        self, scope: WorkspaceScope, **values: object
    ) -> SecFilingContentPreparation:
        del scope, values
        self.prepare_calls += 1
        return self.preparation or SecFilingContentPreparation(
            status=SecFilingContentStatus.OK,
            accession=ACCESSION,
            import_record=workspace_import(),
        )

    async def resolve_candidates(
        self,
        scope: WorkspaceScope,
        *,
        preparation: SecFilingContentPreparation,
        candidates: tuple[DenseCandidate, ...],
    ) -> tuple[SecFilingSearchHit, ...]:
        del scope, preparation
        self.resolve_calls += 1
        assert candidates[0].chunk_id == CHUNK_ID
        return (
            SecFilingSearchHit(
                chunk_id=CHUNK_ID,
                document_version_id=VERSION_ID,
                snapshot_id=PRIMARY_SNAPSHOT_ID,
                accession=ACCESSION,
                title="10-K filing",
                excerpt="Net sales increased.",
                score=candidates[0].score,
                section="Net sales",
                page_number=1,
                content_sha256="a" * 64,
                source_content_sha256="b" * 64,
                source_url=filing_archive()
                .document(SecFilingDocumentKind.PRIMARY_DOCUMENT)
                .source_url,
                source_version="sec-filing-primary-v1",
            ),
        )

    async def read_section(self, scope: WorkspaceScope, **values: object) -> SecFilingSection:
        del scope, values
        raise AssertionError("not used")


@dataclass(slots=True)
class MemorySnapshotStore:
    sources: list[SecFilingDocumentSnapshot] = field(default_factory=list)

    async def persist(self, source: SecFilingDocumentSnapshot) -> str:
        self.sources.append(source)
        return f"snapshots/{source.content_sha256}"


@dataclass(slots=True)
class MemoryKnowledgeService:
    contents: list[bytes] = field(default_factory=list)

    async def import_text_source(
        self, scope: WorkspaceScope, command: ImportKnowledgeTextSource
    ) -> SimpleNamespace:
        assert scope.workspace_id == WORKSPACE_ID
        self.contents.append(command.content)
        return SimpleNamespace(
            source=SimpleNamespace(file_id=FILE_ID),
            document=SimpleNamespace(id=DOCUMENT_ID),
            version=SimpleNamespace(id=VERSION_ID),
            job_id=JOB_ID,
        )


@dataclass(slots=True)
class MemoryDenseIndex:
    calls: int = 0

    async def search(
        self, vector: tuple[float, ...], **values: object
    ) -> tuple[DenseCandidate, ...]:
        assert vector
        assert values["document_version_ids"] == (VERSION_ID,)
        self.calls += 1
        return (DenseCandidate(CHUNK_ID, VERSION_ID, 0.91),)


def scope() -> WorkspaceScope:
    return WorkspaceScope(WORKSPACE_ID, USER_ID, "member")


def financial_scope(*, cik: str = "0000320193") -> FinancialScope:
    return FinancialScope(
        cik=cik,
        accession=ACCESSION,
        form=FinancialForm.TEN_K,
        report_period=date(2023, 9, 30),
        as_of=NOW,
        unit="USD",
        scale=0,
    )


@pytest.mark.asyncio
async def test_import_persists_both_snapshots_then_uses_canonical_knowledge_acceptance() -> None:
    repository = MemoryRepository()
    store = MemorySnapshotStore()
    knowledge = MemoryKnowledgeService()
    service = SecFilingImportService(
        repository=cast(SecFilingContentRepository, repository),
        archive_source=cast(
            SecFilingArchivePort,
            FrozenSecFilingArchiveAdapter(filing_archive()),
        ),
        snapshot_store=cast(SecFilingDocumentSnapshotStore, store),
        knowledge_service=cast(KnowledgeApplicationService, knowledge),
        clock=lambda: NOW,
    )

    imported = await service.import_filing(
        scope(),
        accession=ACCESSION,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        as_of=NOW,
        trace_id=TraceId("trace-sec-import"),
    )

    assert imported.status is SecFilingImportStatus.QUEUED
    assert {source.kind for source in store.sources} == {
        SecFilingDocumentKind.COMPLETE_SUBMISSION,
        SecFilingDocumentKind.PRIMARY_DOCUMENT,
    }
    assert repository.persist_calls == 1
    assert repository.record_calls == 1
    assert len(knowledge.contents) == 1
    markdown = knowledge.contents[0].decode("utf-8")
    assert "# Net sales" in markdown
    assert "Increased." in markdown
    assert "ignore()" not in markdown


@pytest.mark.asyncio
async def test_quarantined_source_identity_change_hard_stops_before_knowledge_import() -> None:
    repository = MemoryRepository(
        references=tuple(
            replace(
                reference,
                status=SecFilingSnapshotStatus.QUARANTINED,
                anomaly_code="source_identity_content_changed",
            )
            for reference in snapshot_references()
        )
    )
    knowledge = MemoryKnowledgeService()

    service = SecFilingImportService(
        repository=cast(SecFilingContentRepository, repository),
        archive_source=cast(
            SecFilingArchivePort,
            FrozenSecFilingArchiveAdapter(filing_archive()),
        ),
        snapshot_store=cast(SecFilingDocumentSnapshotStore, MemorySnapshotStore()),
        knowledge_service=cast(KnowledgeApplicationService, knowledge),
        clock=lambda: NOW,
    )

    with pytest.raises(SecFilingContentError) as caught:
        await service.import_filing(
            scope(),
            accession=ACCESSION,
            knowledge_base_id=KNOWLEDGE_BASE_ID,
            as_of=NOW,
            trace_id=TraceId("trace-sec-anomaly"),
        )

    assert caught.value.code is SecSourceErrorCode.SNAPSHOT_ANOMALY
    assert knowledge.contents == []
    assert repository.record_calls == 0


@pytest.mark.asyncio
async def test_search_rejects_financial_scope_identity_mismatch_before_dense_search() -> None:
    repository = MemoryRepository()
    dense = MemoryDenseIndex()
    service = SecFilingContentService(
        repository=cast(SecFilingContentRepository, repository),
        dense_index=cast(DenseIndexPort, dense),
    )

    result = await service.search(
        scope(),
        knowledge_base_ids=(KNOWLEDGE_BASE_ID,),
        financial_scope=financial_scope(cik="0000789019"),
        query="net sales",
    )

    assert result.status is SecFilingContentStatus.PERMISSION_DENIED
    assert repository.prepare_calls == 0
    assert dense.calls == 0


@pytest.mark.asyncio
async def test_search_reloads_authorized_chunk_truth_after_dense_candidates() -> None:
    repository = MemoryRepository()
    dense = MemoryDenseIndex()
    service = SecFilingContentService(
        repository=cast(SecFilingContentRepository, repository),
        dense_index=cast(DenseIndexPort, dense),
    )

    result = await service.search(
        scope(),
        knowledge_base_ids=(KNOWLEDGE_BASE_ID,),
        financial_scope=financial_scope(),
        query="net sales",
    )

    assert result.status is SecFilingContentStatus.OK
    assert result.hits[0].chunk_id == CHUNK_ID
    assert repository.prepare_calls == 1
    assert repository.resolve_calls == 1
    assert dense.calls == 1
