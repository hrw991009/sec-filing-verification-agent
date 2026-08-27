"""PostgreSQL truth for immutable filing snapshots and authorized imports."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import and_, select, tuple_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.disclosures.domain import (
    SecCanonicalFiling,
    SecDisclosurePersistenceError,
    SecFilingArchive,
    SecFilingContentError,
    SecFilingContentPreparation,
    SecFilingContentStatus,
    SecFilingDocumentKind,
    SecFilingForm,
    SecFilingImportStatus,
    SecFilingSearchHit,
    SecFilingSection,
    SecFilingSnapshotReference,
    SecFilingSnapshotStatus,
    SecSourceErrorCode,
    SecWorkspaceFilingImport,
)
from industry_platform.modules.disclosures.models import (
    SecFilingDocumentRecord,
    SecFilingRecord,
    SecSourceSnapshotRecord,
    SecSubmissionSourceRecord,
    WorkspaceSecImportRecord,
)
from industry_platform.modules.knowledge.domain import (
    DocumentIndexKind,
    DocumentIndexStatus,
    DocumentStatus,
    DocumentVersionStatus,
)
from industry_platform.modules.knowledge.models import (
    DocumentChunkRecord,
    DocumentIndexRecord,
    DocumentRecord,
    DocumentVersionRecord,
)
from industry_platform.modules.retrieval.domain import DenseCandidate
from industry_platform.modules.workspaces.domain import WorkspaceScope


class SqlAlchemySecFilingContentRepository:
    def __init__(self, session_factory: AsyncSessionFactory, *, object_bucket: str) -> None:
        if not object_bucket.strip():
            raise ValueError("SEC filing object bucket is invalid")
        self._session_factory = session_factory
        self._object_bucket = object_bucket

    async def get_canonical_filing(self, accession: str) -> SecCanonicalFiling:
        try:
            async with self._session_factory() as session:
                row = (
                    await session.execute(
                        select(SecFilingRecord, SecSubmissionSourceRecord)
                        .join(
                            SecSubmissionSourceRecord,
                            SecSubmissionSourceRecord.id == SecFilingRecord.source_id,
                        )
                        .where(SecFilingRecord.accession == accession)
                    )
                ).one_or_none()
                if row is None:
                    raise SecFilingContentError(SecSourceErrorCode.FILING_NOT_FOUND)
                filing, source = row
                return _canonical_filing(filing, source)
        except SecFilingContentError:
            raise
        except SQLAlchemyError as error:
            raise SecDisclosurePersistenceError(sqlstate=_sqlstate(error)) from None

    async def persist_archive(
        self,
        archive: SecFilingArchive,
        *,
        object_keys: dict[str, str],
    ) -> tuple[SecFilingSnapshotReference, ...]:
        if set(object_keys) != {document.source_url for document in archive.documents}:
            raise ValueError("SEC archive object manifest is incomplete")
        try:
            async with self._session_factory.begin() as session:
                filing = await session.scalar(
                    select(SecFilingRecord)
                    .where(
                        SecFilingRecord.id == archive.filing.id,
                        SecFilingRecord.accession == archive.filing.accession,
                    )
                    .with_for_update()
                )
                if filing is None:
                    raise SecFilingContentError(SecSourceErrorCode.FILING_NOT_FOUND)
                references: list[SecFilingSnapshotReference] = []
                for source in archive.documents:
                    document = await session.scalar(
                        select(SecFilingDocumentRecord)
                        .where(
                            SecFilingDocumentRecord.filing_id == filing.id,
                            SecFilingDocumentRecord.document_kind == source.kind.value,
                            SecFilingDocumentRecord.filename == source.filename,
                        )
                        .with_for_update()
                    )
                    if document is None:
                        document = SecFilingDocumentRecord(
                            id=uuid4(),
                            filing_id=filing.id,
                            accession=filing.accession,
                            document_kind=source.kind.value,
                            filename=source.filename,
                            current_snapshot_id=None,
                        )
                        session.add(document)
                        await session.flush()
                    current = (
                        None
                        if document.current_snapshot_id is None
                        else await session.get(
                            SecSourceSnapshotRecord, document.current_snapshot_id
                        )
                    )
                    existing = await session.scalar(
                        select(SecSourceSnapshotRecord).where(
                            SecSourceSnapshotRecord.filing_document_id == document.id,
                            SecSourceSnapshotRecord.content_sha256
                            == bytes.fromhex(source.content_sha256),
                        )
                    )
                    if existing is not None:
                        references.append(_snapshot_reference(document, filing.id, existing))
                        continue
                    status = SecFilingSnapshotStatus.ACTIVE
                    anomaly_code: str | None = None
                    if current is not None:
                        status = SecFilingSnapshotStatus.QUARANTINED
                        anomaly_code = "source_identity_content_changed"
                    snapshot = SecSourceSnapshotRecord(
                        id=uuid4(),
                        filing_document_id=document.id,
                        source_url=source.source_url,
                        source_version=source.source_version,
                        content_type=source.content_type,
                        content_sha256=bytes.fromhex(source.content_sha256),
                        byte_size=source.byte_size,
                        object_bucket=self._object_bucket,
                        object_key=object_keys[source.source_url],
                        retrieved_at=source.retrieved_at,
                        source_available_at=source.source_available_at,
                        valid_to=None,
                        adapter_version=source.adapter_version,
                        status=status.value,
                        anomaly_code=anomaly_code,
                    )
                    session.add(snapshot)
                    await session.flush()
                    if current is None:
                        document.current_snapshot_id = snapshot.id
                    references.append(_snapshot_reference(document, filing.id, snapshot))
                return tuple(sorted(references, key=lambda item: (item.kind.value, item.filename)))
        except SecFilingContentError:
            raise
        except SQLAlchemyError as error:
            raise SecDisclosurePersistenceError(sqlstate=_sqlstate(error)) from None

    async def find_import(
        self,
        scope: WorkspaceScope,
        *,
        accession: str,
        knowledge_base_id: UUID,
        primary_snapshot_id: UUID,
    ) -> SecWorkspaceFilingImport | None:
        try:
            async with self._session_factory() as session:
                row = await _import_row(
                    session,
                    workspace_id=scope.workspace_id,
                    predicates=(
                        WorkspaceSecImportRecord.accession == accession,
                        WorkspaceSecImportRecord.knowledge_base_id == knowledge_base_id,
                        WorkspaceSecImportRecord.primary_snapshot_id == primary_snapshot_id,
                    ),
                )
                return None if row is None else _workspace_import(*row)
        except SQLAlchemyError as error:
            raise SecDisclosurePersistenceError(sqlstate=_sqlstate(error)) from None

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
    ) -> SecWorkspaceFilingImport:
        try:
            async with self._session_factory.begin() as session:
                row = await _import_row(
                    session,
                    workspace_id=scope.workspace_id,
                    predicates=(
                        WorkspaceSecImportRecord.accession == accession,
                        WorkspaceSecImportRecord.knowledge_base_id == knowledge_base_id,
                        WorkspaceSecImportRecord.primary_snapshot_id == primary_snapshot_id,
                    ),
                    for_update=True,
                )
                if row is not None:
                    existing = _workspace_import(*row)
                    if (
                        existing.complete_submission_snapshot_id != complete_submission_snapshot_id
                        or existing.file_id != file_id
                        or existing.document_id != document_id
                        or existing.document_version_id != document_version_id
                        or existing.ingestion_job_id != ingestion_job_id
                    ):
                        raise SecFilingContentError(SecSourceErrorCode.SNAPSHOT_ANOMALY)
                    return existing
                filing = await session.scalar(
                    select(SecFilingRecord).where(SecFilingRecord.accession == accession)
                )
                if filing is None:
                    raise SecFilingContentError(SecSourceErrorCode.FILING_NOT_FOUND)
                primary = await session.scalar(
                    select(SecSourceSnapshotRecord)
                    .join(
                        SecFilingDocumentRecord,
                        SecFilingDocumentRecord.id == SecSourceSnapshotRecord.filing_document_id,
                    )
                    .where(
                        SecSourceSnapshotRecord.id == primary_snapshot_id,
                        SecSourceSnapshotRecord.status == SecFilingSnapshotStatus.ACTIVE.value,
                        SecFilingDocumentRecord.filing_id == filing.id,
                        SecFilingDocumentRecord.document_kind
                        == SecFilingDocumentKind.PRIMARY_DOCUMENT.value,
                    )
                )
                complete = await session.scalar(
                    select(SecSourceSnapshotRecord)
                    .join(
                        SecFilingDocumentRecord,
                        SecFilingDocumentRecord.id == SecSourceSnapshotRecord.filing_document_id,
                    )
                    .where(
                        SecSourceSnapshotRecord.id == complete_submission_snapshot_id,
                        SecSourceSnapshotRecord.status == SecFilingSnapshotStatus.ACTIVE.value,
                        SecFilingDocumentRecord.filing_id == filing.id,
                        SecFilingDocumentRecord.document_kind
                        == SecFilingDocumentKind.COMPLETE_SUBMISSION.value,
                    )
                )
                if primary is None or complete is None:
                    raise SecFilingContentError(SecSourceErrorCode.SNAPSHOT_ANOMALY)
                record = WorkspaceSecImportRecord(
                    id=uuid4(),
                    workspace_id=scope.workspace_id,
                    created_by_user_id=scope.user_id,
                    filing_id=filing.id,
                    accession=accession,
                    primary_snapshot_id=primary_snapshot_id,
                    complete_submission_snapshot_id=complete_submission_snapshot_id,
                    knowledge_base_id=knowledge_base_id,
                    file_id=file_id,
                    document_id=document_id,
                    document_version_id=document_version_id,
                    ingestion_job_id=ingestion_job_id,
                    created_at=observed_at,
                    updated_at=observed_at,
                )
                session.add(record)
                await session.flush()
                version = await session.get(DocumentVersionRecord, document_version_id)
                if version is None:
                    raise SecFilingContentError(SecSourceErrorCode.SNAPSHOT_ANOMALY)
                return _workspace_import(record, version)
        except SecFilingContentError:
            raise
        except IntegrityError as error:
            concurrent = await self.find_import(
                scope,
                accession=accession,
                knowledge_base_id=knowledge_base_id,
                primary_snapshot_id=primary_snapshot_id,
            )
            if concurrent is None:
                raise SecDisclosurePersistenceError(sqlstate=_sqlstate(error)) from None
            if (
                concurrent.complete_submission_snapshot_id != complete_submission_snapshot_id
                or concurrent.file_id != file_id
                or concurrent.document_id != document_id
                or concurrent.document_version_id != document_version_id
                or concurrent.ingestion_job_id != ingestion_job_id
            ):
                raise SecFilingContentError(SecSourceErrorCode.SNAPSHOT_ANOMALY) from None
            return concurrent
        except SQLAlchemyError as error:
            raise SecDisclosurePersistenceError(sqlstate=_sqlstate(error)) from None

    async def list_imports(
        self,
        scope: WorkspaceScope,
        *,
        limit: int,
    ) -> tuple[SecWorkspaceFilingImport, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("SEC import limit is invalid")
        try:
            async with self._session_factory() as session:
                rows = (
                    await session.execute(
                        select(WorkspaceSecImportRecord, DocumentVersionRecord)
                        .join(
                            DocumentVersionRecord,
                            and_(
                                DocumentVersionRecord.id
                                == WorkspaceSecImportRecord.document_version_id,
                                DocumentVersionRecord.workspace_id
                                == WorkspaceSecImportRecord.workspace_id,
                            ),
                        )
                        .where(WorkspaceSecImportRecord.workspace_id == scope.workspace_id)
                        .order_by(
                            WorkspaceSecImportRecord.updated_at.desc(),
                            WorkspaceSecImportRecord.id,
                        )
                        .limit(limit)
                    )
                ).all()
                return tuple(_workspace_import(*row) for row in rows)
        except SQLAlchemyError as error:
            raise SecDisclosurePersistenceError(sqlstate=_sqlstate(error)) from None

    async def get_import(
        self,
        scope: WorkspaceScope,
        import_id: UUID,
    ) -> SecWorkspaceFilingImport:
        try:
            async with self._session_factory() as session:
                row = await _import_row(
                    session,
                    workspace_id=scope.workspace_id,
                    predicates=(WorkspaceSecImportRecord.id == import_id,),
                )
                if row is None:
                    raise SecFilingContentError(SecSourceErrorCode.FILING_NOT_FOUND)
                return _workspace_import(*row)
        except SecFilingContentError:
            raise
        except SQLAlchemyError as error:
            raise SecDisclosurePersistenceError(sqlstate=_sqlstate(error)) from None

    async def prepare_content(
        self,
        scope: WorkspaceScope,
        *,
        knowledge_base_ids: tuple[UUID, ...],
        accession: str,
        as_of: datetime,
    ) -> SecFilingContentPreparation:
        selected = tuple(knowledge_base_ids)
        if not selected or len(selected) != len(set(selected)):
            return SecFilingContentPreparation(
                status=SecFilingContentStatus.PERMISSION_DENIED,
                accession=accession,
            )
        try:
            async with self._session_factory() as session:
                rows = (
                    await session.execute(
                        select(
                            WorkspaceSecImportRecord,
                            DocumentVersionRecord,
                            DocumentRecord,
                            SecFilingRecord,
                            SecSourceSnapshotRecord,
                        )
                        .join(
                            DocumentVersionRecord,
                            and_(
                                DocumentVersionRecord.id
                                == WorkspaceSecImportRecord.document_version_id,
                                DocumentVersionRecord.workspace_id
                                == WorkspaceSecImportRecord.workspace_id,
                            ),
                        )
                        .join(
                            DocumentRecord,
                            and_(
                                DocumentRecord.id == WorkspaceSecImportRecord.document_id,
                                DocumentRecord.workspace_id
                                == WorkspaceSecImportRecord.workspace_id,
                            ),
                        )
                        .join(
                            SecFilingRecord,
                            SecFilingRecord.id == WorkspaceSecImportRecord.filing_id,
                        )
                        .join(
                            SecSourceSnapshotRecord,
                            SecSourceSnapshotRecord.id
                            == WorkspaceSecImportRecord.primary_snapshot_id,
                        )
                        .where(
                            WorkspaceSecImportRecord.workspace_id == scope.workspace_id,
                            WorkspaceSecImportRecord.accession == accession,
                            WorkspaceSecImportRecord.knowledge_base_id.in_(selected),
                        )
                        .order_by(WorkspaceSecImportRecord.created_at.desc())
                    )
                ).all()
                if not rows:
                    return SecFilingContentPreparation(
                        status=SecFilingContentStatus.PERMISSION_DENIED,
                        accession=accession,
                    )
                visible = tuple(
                    row
                    for row in rows
                    if row[3].public_available_at <= as_of
                    and row[4].source_available_at <= as_of
                    and row[4].status == SecFilingSnapshotStatus.ACTIVE.value
                )
                if not visible:
                    return SecFilingContentPreparation(
                        status=SecFilingContentStatus.NO_RESULT,
                        accession=accession,
                    )
                ready = tuple(
                    row
                    for row in visible
                    if row[1].status is DocumentVersionStatus.READY
                    and row[2].status is DocumentStatus.ACTIVE
                    and row[2].active_version_id == row[1].id
                )
                if not ready:
                    return SecFilingContentPreparation(
                        status=SecFilingContentStatus.NOT_READY,
                        accession=accession,
                    )
                record, version, _document, _filing, _snapshot = ready[0]
                return SecFilingContentPreparation(
                    status=SecFilingContentStatus.OK,
                    accession=accession,
                    import_record=_workspace_import(record, version),
                )
        except SQLAlchemyError:
            return SecFilingContentPreparation(
                status=SecFilingContentStatus.DEPENDENCY_FAILED,
                accession=accession,
            )

    async def resolve_candidates(
        self,
        scope: WorkspaceScope,
        *,
        preparation: SecFilingContentPreparation,
        candidates: tuple[DenseCandidate, ...],
    ) -> tuple[SecFilingSearchHit, ...]:
        imported = preparation.import_record
        if imported is None or preparation.status is not SecFilingContentStatus.OK:
            raise ValueError("Only a ready SEC filing import can resolve candidates")
        if not candidates:
            return ()
        pairs = tuple(
            (candidate.chunk_id, candidate.document_version_id) for candidate in candidates
        )
        vector = aliased(DocumentIndexRecord)
        lexical = aliased(DocumentIndexRecord)
        try:
            async with self._session_factory() as session:
                rows = (
                    await session.execute(
                        select(
                            DocumentChunkRecord,
                            DocumentRecord,
                            SecSourceSnapshotRecord,
                            vector,
                        )
                        .join(
                            DocumentRecord,
                            and_(
                                DocumentRecord.id == DocumentChunkRecord.document_id,
                                DocumentRecord.workspace_id == DocumentChunkRecord.workspace_id,
                            ),
                        )
                        .join(
                            WorkspaceSecImportRecord,
                            and_(
                                WorkspaceSecImportRecord.document_version_id
                                == DocumentChunkRecord.document_version_id,
                                WorkspaceSecImportRecord.workspace_id
                                == DocumentChunkRecord.workspace_id,
                            ),
                        )
                        .join(
                            SecSourceSnapshotRecord,
                            SecSourceSnapshotRecord.id
                            == WorkspaceSecImportRecord.primary_snapshot_id,
                        )
                        .join(
                            vector,
                            and_(
                                vector.chunk_id == DocumentChunkRecord.id,
                                vector.document_version_id
                                == DocumentChunkRecord.document_version_id,
                                vector.status == DocumentIndexStatus.SUCCEEDED,
                                vector.kind == DocumentIndexKind.VECTOR,
                            ),
                        )
                        .join(
                            lexical,
                            and_(
                                lexical.chunk_id == DocumentChunkRecord.id,
                                lexical.document_version_id
                                == DocumentChunkRecord.document_version_id,
                                lexical.status == DocumentIndexStatus.SUCCEEDED,
                                lexical.kind == DocumentIndexKind.LEXICAL,
                                lexical.index_version == vector.index_version,
                            ),
                        )
                        .where(
                            DocumentChunkRecord.workspace_id == scope.workspace_id,
                            DocumentChunkRecord.document_version_id == imported.document_version_id,
                            WorkspaceSecImportRecord.id == imported.id,
                            tuple_(
                                DocumentChunkRecord.id,
                                DocumentChunkRecord.document_version_id,
                            ).in_(pairs),
                        )
                    )
                ).all()
        except SQLAlchemyError as error:
            raise SecDisclosurePersistenceError(sqlstate=_sqlstate(error)) from None
        by_pair = {(chunk.id, chunk.document_version_id): row for row in rows for chunk in [row[0]]}
        hits: list[SecFilingSearchHit] = []
        for candidate in candidates:
            row = by_pair.get((candidate.chunk_id, candidate.document_version_id))
            if row is None:
                continue
            chunk, document, snapshot, _vector = row
            hits.append(
                SecFilingSearchHit(
                    chunk_id=chunk.id,
                    document_version_id=chunk.document_version_id,
                    snapshot_id=snapshot.id,
                    accession=imported.accession,
                    title=document.title,
                    excerpt=chunk.text_content,
                    score=candidate.score,
                    section=chunk.title_path[-1] if chunk.title_path else "Filing excerpt",
                    page_number=chunk.page_number,
                    content_sha256=chunk.content_hash.hex(),
                    source_content_sha256=snapshot.content_sha256.hex(),
                    source_url=snapshot.source_url,
                    source_version=snapshot.source_version,
                )
            )
        return tuple(hits)

    async def read_section(
        self,
        scope: WorkspaceScope,
        *,
        accession: str,
        as_of: datetime,
        knowledge_base_ids: tuple[UUID, ...],
        document_version_id: UUID,
        chunk_id: UUID,
    ) -> SecFilingSection:
        preparation = await self.prepare_content(
            scope,
            knowledge_base_ids=knowledge_base_ids,
            accession=accession,
            as_of=as_of,
        )
        imported = preparation.import_record
        if preparation.status is SecFilingContentStatus.DEPENDENCY_FAILED:
            raise SecDisclosurePersistenceError()
        if preparation.status is SecFilingContentStatus.NO_RESULT:
            raise SecFilingContentError(SecSourceErrorCode.SNAPSHOT_NOT_VISIBLE)
        if preparation.status is SecFilingContentStatus.PERMISSION_DENIED:
            raise SecFilingContentError(SecSourceErrorCode.FILING_NOT_FOUND)
        if imported is None or imported.document_version_id != document_version_id:
            raise SecFilingContentError(SecSourceErrorCode.IMPORT_NOT_READY)
        try:
            async with self._session_factory() as session:
                row = (
                    await session.execute(
                        select(DocumentChunkRecord, DocumentRecord, SecSourceSnapshotRecord)
                        .join(
                            DocumentRecord,
                            and_(
                                DocumentRecord.id == DocumentChunkRecord.document_id,
                                DocumentRecord.workspace_id == DocumentChunkRecord.workspace_id,
                            ),
                        )
                        .join(
                            WorkspaceSecImportRecord,
                            and_(
                                WorkspaceSecImportRecord.id == imported.id,
                                WorkspaceSecImportRecord.document_version_id
                                == DocumentChunkRecord.document_version_id,
                                WorkspaceSecImportRecord.workspace_id
                                == DocumentChunkRecord.workspace_id,
                            ),
                        )
                        .join(
                            SecSourceSnapshotRecord,
                            SecSourceSnapshotRecord.id
                            == WorkspaceSecImportRecord.primary_snapshot_id,
                        )
                        .where(
                            DocumentChunkRecord.id == chunk_id,
                            DocumentChunkRecord.document_version_id == document_version_id,
                            DocumentChunkRecord.workspace_id == scope.workspace_id,
                        )
                    )
                ).one_or_none()
                if row is None:
                    raise SecFilingContentError(SecSourceErrorCode.FILING_NOT_FOUND)
                chunk, document, snapshot = row
                return SecFilingSection(
                    import_id=imported.id,
                    snapshot_id=snapshot.id,
                    accession=accession,
                    document_version_id=document_version_id,
                    chunk_id=chunk_id,
                    title=document.title,
                    section=chunk.title_path[-1] if chunk.title_path else "Filing excerpt",
                    text=chunk.text_content,
                    page_number=chunk.page_number,
                    content_sha256=chunk.content_hash.hex(),
                    source_content_sha256=snapshot.content_sha256.hex(),
                    source_url=snapshot.source_url,
                    source_version=snapshot.source_version,
                )
        except SecFilingContentError:
            raise
        except SQLAlchemyError as error:
            raise SecDisclosurePersistenceError(sqlstate=_sqlstate(error)) from None


async def _import_row(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    predicates: tuple[ColumnElement[bool], ...],
    for_update: bool = False,
) -> tuple[WorkspaceSecImportRecord, DocumentVersionRecord] | None:
    statement = (
        select(WorkspaceSecImportRecord, DocumentVersionRecord)
        .join(
            DocumentVersionRecord,
            and_(
                DocumentVersionRecord.id == WorkspaceSecImportRecord.document_version_id,
                DocumentVersionRecord.workspace_id == WorkspaceSecImportRecord.workspace_id,
            ),
        )
        .where(WorkspaceSecImportRecord.workspace_id == workspace_id, *predicates)
    )
    if for_update:
        statement = statement.with_for_update(of=WorkspaceSecImportRecord)
    result = await session.execute(statement)
    row = result.one_or_none()
    return None if row is None else (row[0], row[1])


def _canonical_filing(
    filing: SecFilingRecord,
    source: SecSubmissionSourceRecord,
) -> SecCanonicalFiling:
    return SecCanonicalFiling(
        id=filing.id,
        cik=filing.cik,
        accession=filing.accession,
        form=SecFilingForm(filing.form),
        report_date=filing.report_date,
        filed_date=filing.filed_date,
        accepted_at=filing.accepted_at,
        public_available_at=filing.public_available_at,
        primary_document=filing.primary_document,
        source_available_at=source.source_available_at,
    )


def _snapshot_reference(
    document: SecFilingDocumentRecord,
    filing_id: UUID,
    snapshot: SecSourceSnapshotRecord,
) -> SecFilingSnapshotReference:
    return SecFilingSnapshotReference(
        document_id=document.id,
        snapshot_id=snapshot.id,
        filing_id=filing_id,
        kind=SecFilingDocumentKind(document.document_kind),
        filename=document.filename,
        source_url=snapshot.source_url,
        source_version=snapshot.source_version,
        content_type=snapshot.content_type,
        content_sha256=snapshot.content_sha256.hex(),
        byte_size=snapshot.byte_size,
        retrieved_at=snapshot.retrieved_at,
        source_available_at=snapshot.source_available_at,
        status=SecFilingSnapshotStatus(snapshot.status),
        anomaly_code=snapshot.anomaly_code,
        object_bucket=snapshot.object_bucket,
        object_key=snapshot.object_key,
    )


def _workspace_import(
    record: WorkspaceSecImportRecord,
    version: DocumentVersionRecord,
) -> SecWorkspaceFilingImport:
    if version.status is DocumentVersionStatus.READY:
        status = SecFilingImportStatus.READY
        error_code = None
    elif version.status is DocumentVersionStatus.FAILED:
        status = SecFilingImportStatus.FAILED
        error_code = version.error_code or "ingestion_failed"
    elif version.status in {
        DocumentVersionStatus.CANCELLED,
        DocumentVersionStatus.DELETING,
        DocumentVersionStatus.DELETED,
    }:
        status = SecFilingImportStatus.CANCELLED
        error_code = None
    else:
        status = SecFilingImportStatus.QUEUED
        error_code = None
    return SecWorkspaceFilingImport(
        id=record.id,
        workspace_id=record.workspace_id,
        filing_id=record.filing_id,
        accession=record.accession,
        knowledge_base_id=record.knowledge_base_id,
        primary_snapshot_id=record.primary_snapshot_id,
        complete_submission_snapshot_id=record.complete_submission_snapshot_id,
        file_id=record.file_id,
        document_id=record.document_id,
        document_version_id=record.document_version_id,
        ingestion_job_id=record.ingestion_job_id,
        status=status,
        error_code=error_code,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _sqlstate(error: SQLAlchemyError) -> str | None:
    original = getattr(error, "orig", None)
    return getattr(original, "sqlstate", None)
