"""Fenced reconstruction from persisted chunks/embeddings, serialized with deletion."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from industry_platform.core.database import AsyncSessionFactory, safe_sqlstate
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.identity.models import AuditLog, AuditOutcome
from industry_platform.modules.ingestion.adapters.sqlalchemy import (
    _live_job,
    _persistence_error,
)
from industry_platform.modules.ingestion.domain import (
    IndexableChunk,
    IngestionCancelledError,
    IngestionConflictError,
    IngestionNotFoundError,
)
from industry_platform.modules.ingestion.index_contract import INDEX_VERSION
from industry_platform.modules.ingestion.rebuild import INDEX_REBUILD_TASK_NAME, RebuildWork
from industry_platform.modules.jobs.adapters.sqlalchemy import SqlAlchemyJobWriter
from industry_platform.modules.jobs.domain import (
    AcquiredJob,
    ExecutionScope,
    JobDefinition,
    JobIdempotencyConflictError,
    JobStatus,
    JobSubmissionRecord,
    LostJobLeaseError,
    PreparedJobSubmission,
    fingerprint_job_request,
    hash_job_idempotency_key,
)
from industry_platform.modules.jobs.models import Job
from industry_platform.modules.knowledge.domain import (
    DocumentIndexStatus,
    DocumentStatus,
    DocumentVersionStatus,
    KnowledgeConflictError,
    KnowledgeNotFoundError,
    KnowledgePersistenceError,
)
from industry_platform.modules.knowledge.models import (
    ChunkEmbeddingRecord,
    DocumentChunkRecord,
    DocumentIndexRecord,
    DocumentRecord,
    DocumentVersionRecord,
)
from industry_platform.modules.workspaces.adapters.authorization import require_current_owner
from industry_platform.modules.workspaces.domain import WorkspaceScope


@dataclass(frozen=True, slots=True)
class _RebuildWork:
    session: AsyncSession
    job: AcquiredJob
    version: DocumentVersionRecord
    chunks: tuple[IndexableChunk, ...]

    async def complete(self) -> None:
        live = await self.session.scalar(
            _live_job(self.job.lease_proof).execution_options(populate_existing=True)
        )
        if live is None:
            raise LostJobLeaseError
        if live.cancel_requested_at is not None:
            raise IngestionCancelledError
        indexes = tuple(
            await self.session.scalars(
                select(DocumentIndexRecord).where(
                    DocumentIndexRecord.document_version_id == self.version.id,
                    DocumentIndexRecord.index_version == INDEX_VERSION,
                )
            )
        )
        if len(indexes) != 2 * len(self.chunks):
            raise IngestionConflictError
        now = await self.session.scalar(select(func.clock_timestamp()))
        for index in indexes:
            index.status = DocumentIndexStatus.SUCCEEDED
            index.attempt_count = self.job.attempt_count
            index.indexed_at = now
            index.error_code = None
        self.version.status = DocumentVersionStatus.READY
        self.version.ready_at = now
        self.version.error_code = None
        self.version.revision += 1
        await self.session.flush()


@dataclass(frozen=True, slots=True)
class SqlAlchemyIndexRebuildRepository:
    session_factory: AsyncSessionFactory

    async def submit(
        self,
        scope: WorkspaceScope,
        *,
        knowledge_base_id: UUID,
        document_version_id: UUID,
        idempotency_key: str,
        trace_id: TraceId,
    ) -> JobSubmissionRecord:
        try:
            async with self.session_factory() as session, session.begin():
                await require_current_owner(session, scope)
                version = await session.scalar(
                    select(DocumentVersionRecord)
                    .join(DocumentRecord, DocumentRecord.id == DocumentVersionRecord.document_id)
                    .where(
                        DocumentVersionRecord.id == document_version_id,
                        DocumentVersionRecord.knowledge_base_id == knowledge_base_id,
                        DocumentVersionRecord.workspace_id == scope.workspace_id,
                        DocumentRecord.status == DocumentStatus.ACTIVE,
                    )
                    .with_for_update(of=DocumentVersionRecord)
                )
                if version is None:
                    raise KnowledgeNotFoundError
                now = await session.scalar(select(func.clock_timestamp()))
                if now is None:
                    raise KnowledgeConflictError
                definition = JobDefinition(
                    scope=ExecutionScope(workspace_id=scope.workspace_id),
                    task_name=INDEX_REBUILD_TASK_NAME,
                    queue_name="ingestion",
                    payload={"schema_version": 1, "document_version_id": str(version.id)},
                    # A fixed ASAP marker keeps the semantic fingerprint stable;
                    # the actual Outbox due time is this transaction's database time.
                    available_at=datetime(1970, 1, 1, tzinfo=UTC),
                    max_attempts=3,
                    idempotency_key=idempotency_key,
                )
                key_hash = hash_job_idempotency_key(idempotency_key)
                previous = await session.scalar(
                    select(Job.id).where(
                        Job.workspace_id == scope.workspace_id,
                        Job.task_name == INDEX_REBUILD_TASK_NAME,
                        Job.idempotency_key_hash == key_hash,
                    )
                )
                if previous is None and version.status is not DocumentVersionStatus.READY:
                    raise KnowledgeConflictError
                receipt = await SqlAlchemyJobWriter(session).submit(
                    PreparedJobSubmission(
                        job_id=uuid4(),
                        outbox_event_id=uuid4(),
                        scope=definition.scope,
                        task_name=definition.task_name,
                        queue_name=definition.queue_name,
                        payload=definition.payload,
                        available_at=now,
                        max_attempts=3,
                        priority=definition.priority,
                        soft_time_limit_seconds=definition.soft_time_limit_seconds,
                        hard_time_limit_seconds=definition.hard_time_limit_seconds,
                        trace_id=trace_id,
                        idempotency_key_hash=key_hash,
                        request_fingerprint=fingerprint_job_request(definition),
                        submitted_at=now,
                    )
                )
                if receipt.created:
                    version.status = DocumentVersionStatus.VECTOR_INDEXING
                    version.ready_at = None
                    version.error_code = "index_rebuild_requested"
                    version.revision += 1
                    session.add(
                        AuditLog(
                            id=uuid4(),
                            workspace_id=scope.workspace_id,
                            actor_user_id=scope.user_id,
                            action="knowledge.document.rebuild_indexes",
                            resource_type="knowledge_document_version",
                            resource_id=version.id,
                            outcome=AuditOutcome.SUCCEEDED,
                            trace_id=str(trace_id),
                            sanitized_metadata={"job_id": str(receipt.job_id)},
                        )
                    )
                return receipt
        except JobIdempotencyConflictError:
            raise KnowledgeConflictError from None
        except SQLAlchemyError as error:
            raise KnowledgePersistenceError(sqlstate=safe_sqlstate(error)) from None

    @asynccontextmanager
    async def lock(self, job: AcquiredJob) -> AsyncIterator[RebuildWork]:
        if job.task_name != INDEX_REBUILD_TASK_NAME or job.payload.get("schema_version") != 1:
            raise ValueError("Invalid index rebuild task")
        version_id = UUID(str(job.payload.get("document_version_id")))
        try:
            async with self.session_factory() as session, session.begin():
                # Do not hold the Job row during network I/O: heartbeats remain live.
                # Hold the version row so deletion cannot finish before late index writes.
                version = await session.scalar(
                    select(DocumentVersionRecord)
                    .join(DocumentRecord, DocumentRecord.id == DocumentVersionRecord.document_id)
                    .where(
                        DocumentVersionRecord.id == version_id,
                        DocumentVersionRecord.workspace_id == job.scope.workspace_id,
                        DocumentRecord.status == DocumentStatus.ACTIVE,
                        DocumentVersionRecord.status.in_(
                            (
                                DocumentVersionStatus.VECTOR_INDEXING,
                                DocumentVersionStatus.READY,
                            )
                        ),
                    )
                    .with_for_update(of=DocumentVersionRecord)
                )
                if version is None:
                    raise IngestionNotFoundError
                live = await session.scalar(
                    select(Job).where(
                        Job.id == job.job_id,
                        Job.status == JobStatus.RUNNING,
                        Job.lease_token == job.lease_proof.lease_token,
                        Job.fencing_token == job.lease_proof.fencing_token,
                        Job.lease_expires_at > func.clock_timestamp(),
                    )
                )
                if live is None:
                    raise LostJobLeaseError
                if live.cancel_requested_at is not None:
                    raise IngestionCancelledError
                rows = (
                    await session.execute(
                        select(DocumentChunkRecord, ChunkEmbeddingRecord)
                        .join(
                            ChunkEmbeddingRecord,
                            ChunkEmbeddingRecord.chunk_id == DocumentChunkRecord.id,
                        )
                        .where(DocumentChunkRecord.document_version_id == version_id)
                        .order_by(DocumentChunkRecord.ordinal)
                    )
                ).all()
                count = await session.scalar(
                    select(func.count(DocumentChunkRecord.id)).where(
                        DocumentChunkRecord.document_version_id == version_id
                    )
                )
                if not rows or count != len(rows):
                    raise IngestionConflictError
                chunks = tuple(
                    IndexableChunk(
                        workspace_id=chunk.workspace_id,
                        knowledge_base_id=version.knowledge_base_id,
                        document_id=chunk.document_id,
                        document_version_id=chunk.document_version_id,
                        chunk_id=chunk.id,
                        ordinal=chunk.ordinal,
                        page_number=chunk.page_number,
                        text=chunk.text_content,
                        content_hash=chunk.content_hash.hex(),
                        vector=tuple(float(value) for value in embedding.vector),
                        external_id=f"{chunk.id}:{INDEX_VERSION}",
                    )
                    for chunk, embedding in rows
                )
                yield _RebuildWork(session, job, version, chunks)
        except SQLAlchemyError as error:
            raise _persistence_error(error) from None
