"""Fenced PostgreSQL facts for cross-store Knowledge deletion."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.files.domain import FileObjectStatus
from industry_platform.modules.files.models import FileObject
from industry_platform.modules.ingestion.adapters.sqlalchemy import (
    _database_now,
    _live_job,
    _persistence_error,
)
from industry_platform.modules.ingestion.deletion import (
    DeletionReconciliationResult,
    DeletionTarget,
    DeletionWorkItem,
)
from industry_platform.modules.ingestion.domain import (
    IngestionConflictError,
    IngestionDependencyError,
    IngestionNotFoundError,
)
from industry_platform.modules.jobs.domain import JobLeaseProof, JobStatus, LostJobLeaseError
from industry_platform.modules.jobs.models import Job
from industry_platform.modules.knowledge.domain import (
    DocumentDeletionTargetStatus,
    DocumentStatus,
    DocumentVersionStatus,
)
from industry_platform.modules.knowledge.models import (
    DocumentDeletionTargetRecord,
    DocumentRecord,
    DocumentVersionRecord,
)


@dataclass(frozen=True, slots=True)
class SqlAlchemyDeletionRepository:
    session_factory: AsyncSessionFactory

    async def load_work_item(
        self,
        proof: JobLeaseProof,
        *,
        document_id: UUID,
    ) -> DeletionWorkItem:
        try:
            async with self.session_factory() as session, session.begin():
                job = await session.scalar(_live_job(proof))
                if job is None:
                    raise LostJobLeaseError
                document = await session.scalar(
                    select(DocumentRecord).where(
                        DocumentRecord.id == document_id,
                        DocumentRecord.workspace_id == job.workspace_id,
                        DocumentRecord.deletion_job_id == proof.job_id,
                        DocumentRecord.status == DocumentStatus.DELETING,
                    )
                )
                if document is None:
                    raise IngestionNotFoundError
                running_ingestions = await session.scalar(
                    select(func.count(Job.id))
                    .join(
                        DocumentVersionRecord,
                        DocumentVersionRecord.ingestion_job_id == Job.id,
                    )
                    .where(
                        DocumentVersionRecord.document_id == document.id,
                        Job.status == JobStatus.RUNNING,
                    )
                )
                if running_ingestions:
                    raise IngestionDependencyError("knowledge_deletion_waiting_for_ingestion")
                records = tuple(
                    await session.scalars(
                        select(DocumentDeletionTargetRecord)
                        .where(
                            DocumentDeletionTargetRecord.document_id == document.id,
                            DocumentDeletionTargetRecord.status
                            != DocumentDeletionTargetStatus.DELETED,
                        )
                        .order_by(
                            DocumentDeletionTargetRecord.kind,
                            DocumentDeletionTargetRecord.target_key,
                        )
                    )
                )
                return DeletionWorkItem(
                    workspace_id=document.workspace_id,
                    document_id=document.id,
                    targets=tuple(
                        DeletionTarget(
                            id=record.id,
                            kind=record.kind,
                            target_key=record.target_key,
                            bucket=record.bucket,
                        )
                        for record in records
                    ),
                )
        except (IngestionNotFoundError, LostJobLeaseError):
            raise
        except SQLAlchemyError as error:
            raise _persistence_error(error) from None

    async def mark_targets_deleted(
        self,
        proof: JobLeaseProof,
        *,
        document_id: UUID,
        target_ids: tuple[UUID, ...],
    ) -> None:
        await self._mark_targets(
            proof,
            document_id=document_id,
            target_ids=target_ids,
            status=DocumentDeletionTargetStatus.DELETED,
            error_code=None,
        )

    async def mark_targets_failed(
        self,
        proof: JobLeaseProof,
        *,
        document_id: UUID,
        target_ids: tuple[UUID, ...],
        error_code: str,
    ) -> None:
        await self._mark_targets(
            proof,
            document_id=document_id,
            target_ids=target_ids,
            status=DocumentDeletionTargetStatus.FAILED,
            error_code=error_code,
        )

    async def _mark_targets(
        self,
        proof: JobLeaseProof,
        *,
        document_id: UUID,
        target_ids: tuple[UUID, ...],
        status: DocumentDeletionTargetStatus,
        error_code: str | None,
    ) -> None:
        if not target_ids:
            raise ValueError("Knowledge deletion target set is empty")
        try:
            async with self.session_factory() as session, session.begin():
                job = await session.scalar(_live_job(proof))
                if job is None:
                    raise LostJobLeaseError
                document = await session.scalar(
                    select(DocumentRecord)
                    .where(
                        DocumentRecord.id == document_id,
                        DocumentRecord.workspace_id == job.workspace_id,
                        DocumentRecord.deletion_job_id == proof.job_id,
                        DocumentRecord.status == DocumentStatus.DELETING,
                    )
                    .with_for_update()
                )
                if document is None:
                    raise IngestionNotFoundError
                records = tuple(
                    await session.scalars(
                        select(DocumentDeletionTargetRecord)
                        .where(
                            DocumentDeletionTargetRecord.document_id == document.id,
                            DocumentDeletionTargetRecord.id.in_(target_ids),
                        )
                        .with_for_update()
                    )
                )
                if len(records) != len(set(target_ids)):
                    raise IngestionConflictError
                now = await _database_now(session)
                for record in records:
                    record.status = status
                    record.attempt_count = max(1, job.attempt_count)
                    record.error_code = error_code
                    record.deleted_at = (
                        now if status is DocumentDeletionTargetStatus.DELETED else None
                    )
                document.deletion_error_code = error_code
                for version in await session.scalars(
                    select(DocumentVersionRecord)
                    .where(DocumentVersionRecord.document_id == document.id)
                    .with_for_update()
                ):
                    version.error_code = error_code
                    version.revision += 1
                document.revision += 1
        except (
            IngestionConflictError,
            IngestionNotFoundError,
            LostJobLeaseError,
        ):
            raise
        except SQLAlchemyError as error:
            raise _persistence_error(error) from None

    async def complete_deletion(
        self,
        proof: JobLeaseProof,
        *,
        document_id: UUID,
    ) -> None:
        try:
            async with self.session_factory() as session, session.begin():
                job = await session.scalar(_live_job(proof))
                if job is None:
                    raise LostJobLeaseError
                document = await session.scalar(
                    select(DocumentRecord)
                    .where(
                        DocumentRecord.id == document_id,
                        DocumentRecord.workspace_id == job.workspace_id,
                        DocumentRecord.deletion_job_id == proof.job_id,
                        DocumentRecord.status == DocumentStatus.DELETING,
                    )
                    .with_for_update()
                )
                if document is None:
                    raise IngestionNotFoundError
                remaining = await session.scalar(
                    select(func.count(DocumentDeletionTargetRecord.id)).where(
                        DocumentDeletionTargetRecord.document_id == document.id,
                        DocumentDeletionTargetRecord.status != DocumentDeletionTargetStatus.DELETED,
                    )
                )
                if remaining:
                    raise IngestionConflictError
                await _finalize_document(session, document)
        except (
            IngestionConflictError,
            IngestionNotFoundError,
            LostJobLeaseError,
        ):
            raise
        except SQLAlchemyError as error:
            raise _persistence_error(error) from None


@dataclass(frozen=True, slots=True)
class SqlAlchemyDeletionReconciler:
    session_factory: AsyncSessionFactory

    async def reconcile_deletions(
        self,
        *,
        batch_size: int,
    ) -> DeletionReconciliationResult:
        if isinstance(batch_size, bool) or not 1 <= batch_size <= 1_000:
            raise ValueError("Knowledge deletion reconciliation batch is invalid")
        try:
            async with self.session_factory() as session, session.begin():
                remaining_target_exists = (
                    select(DocumentDeletionTargetRecord.id)
                    .where(
                        DocumentDeletionTargetRecord.document_id == DocumentRecord.id,
                        DocumentDeletionTargetRecord.status != DocumentDeletionTargetStatus.DELETED,
                    )
                    .exists()
                )
                documents = tuple(
                    await session.scalars(
                        select(DocumentRecord)
                        .join(Job, Job.id == DocumentRecord.deletion_job_id)
                        .where(
                            DocumentRecord.status == DocumentStatus.DELETING,
                            Job.status.in_(
                                {
                                    JobStatus.FAILED,
                                    JobStatus.CANCELLED,
                                    JobStatus.DEAD_LETTER,
                                }
                            ),
                            or_(
                                ~remaining_target_exists,
                                DocumentRecord.deletion_error_code.is_(None),
                                DocumentRecord.deletion_error_code != "knowledge_deletion_orphaned",
                            ),
                        )
                        .order_by(DocumentRecord.updated_at, DocumentRecord.id)
                        .limit(batch_size)
                        .with_for_update(skip_locked=True)
                    )
                )
                finalized = 0
                orphaned = 0
                for document in documents:
                    remaining = await session.scalar(
                        select(func.count(DocumentDeletionTargetRecord.id)).where(
                            DocumentDeletionTargetRecord.document_id == document.id,
                            DocumentDeletionTargetRecord.status
                            != DocumentDeletionTargetStatus.DELETED,
                        )
                    )
                    if not remaining:
                        await _finalize_document(session, document)
                        finalized += 1
                    else:
                        document.deletion_error_code = "knowledge_deletion_orphaned"
                        document.revision += 1
                        orphaned += 1
                return DeletionReconciliationResult(
                    selected=len(documents),
                    finalized=finalized,
                    orphaned=orphaned,
                )
        except SQLAlchemyError as error:
            raise _persistence_error(error) from None


async def _finalize_document(session: AsyncSession, document: DocumentRecord) -> None:
    now = await _database_now(session)
    versions = tuple(
        await session.scalars(
            select(DocumentVersionRecord)
            .where(DocumentVersionRecord.document_id == document.id)
            .with_for_update()
        )
    )
    for version in versions:
        version.status = DocumentVersionStatus.DELETED
        version.error_code = None
        version.revision += 1
    file_ids = {version.file_object_id for version in versions}
    files = tuple(
        await session.scalars(
            select(FileObject).where(FileObject.id.in_(file_ids)).with_for_update()
        )
    )
    for file in files:
        file.status = FileObjectStatus.DELETED
        file.deleted_at = now
        file.error_code = None
        file.revision += 1
    document.status = DocumentStatus.DELETED
    document.deletion_error_code = None
    document.deleted_at = now
    document.revision += 1
