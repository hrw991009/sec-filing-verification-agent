"""Durable, same-version index reconstruction without rerunning parsing or the model."""

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.ingestion.domain import IndexableChunk, IngestionDependencyError
from industry_platform.modules.ingestion.ports import LexicalIndexWriter, VectorIndexWriter
from industry_platform.modules.jobs.domain import AcquiredJob, JobSubmissionRecord
from industry_platform.modules.workspaces.domain import WorkspaceAccessDeniedError, WorkspaceScope

INDEX_REBUILD_TASK_NAME = "knowledge.index_rebuild.v1"


class RebuildWork(Protocol):
    @property
    def chunks(self) -> tuple[IndexableChunk, ...]: ...

    async def complete(self) -> None: ...


class IndexRebuildRepository(Protocol):
    async def submit(
        self,
        scope: WorkspaceScope,
        *,
        knowledge_base_id: UUID,
        document_version_id: UUID,
        idempotency_key: str,
        trace_id: TraceId,
    ) -> JobSubmissionRecord: ...

    def lock(self, job: AcquiredJob) -> AbstractAsyncContextManager[RebuildWork]: ...


@dataclass(frozen=True, slots=True)
class IndexRebuildSubmissionService:
    repository: IndexRebuildRepository

    async def submit(
        self,
        scope: WorkspaceScope,
        *,
        knowledge_base_id: UUID,
        document_version_id: UUID,
        idempotency_key: str,
        trace_id: TraceId,
    ) -> JobSubmissionRecord:
        if scope.role != "owner":
            raise WorkspaceAccessDeniedError
        return await self.repository.submit(
            scope,
            knowledge_base_id=knowledge_base_id,
            document_version_id=document_version_id,
            idempotency_key=idempotency_key,
            trace_id=trace_id,
        )


@dataclass(frozen=True, slots=True)
class IndexRebuildService:
    repository: IndexRebuildRepository
    vector_index: VectorIndexWriter
    lexical_index: LexicalIndexWriter

    async def execute(self, job: AcquiredJob) -> dict[str, object]:
        async with self.repository.lock(job) as work:
            await rebuild_both_indexes(
                work.chunks, vector_index=self.vector_index, lexical_index=self.lexical_index
            )
            await work.complete()
            count = len(work.chunks)
        return {
            "document_version_id": str(job.payload["document_version_id"]),
            "chunk_count": count,
            "status": "ready",
        }


async def rebuild_both_indexes(
    chunks: tuple[IndexableChunk, ...],
    *,
    vector_index: VectorIndexWriter,
    lexical_index: LexicalIndexWriter,
) -> None:
    expected = tuple(chunk.external_id for chunk in chunks)
    if await vector_index.upsert(chunks) != expected:
        raise IngestionDependencyError("rebuild_vector_identity_mismatch")
    if await lexical_index.upsert(chunks) != expected:
        raise IngestionDependencyError("rebuild_lexical_identity_mismatch")
