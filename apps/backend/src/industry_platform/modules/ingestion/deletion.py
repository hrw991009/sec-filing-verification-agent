"""Idempotent cross-store deletion orchestration for Knowledge documents."""

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from industry_platform.modules.files.ports import (
    FileObjectStoreError,
    PrivateFileObjectStore,
)
from industry_platform.modules.ingestion.domain import (
    IngestionDependencyError,
)
from industry_platform.modules.ingestion.ports import LexicalIndexWriter, VectorIndexWriter
from industry_platform.modules.jobs.domain import AcquiredJob, JobLeaseProof
from industry_platform.modules.knowledge.domain import DocumentDeletionTargetKind


@dataclass(frozen=True, slots=True)
class DeletionTarget:
    id: UUID
    kind: DocumentDeletionTargetKind
    target_key: str = field(repr=False)
    bucket: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.id.int == 0 or not self.target_key:
            raise ValueError("Knowledge deletion target is invalid")
        object_kinds = {
            DocumentDeletionTargetKind.OBJECT,
            DocumentDeletionTargetKind.OBJECT_PREFIX,
        }
        if (self.kind in object_kinds) != (self.bucket is not None):
            raise ValueError("Knowledge deletion object target is invalid")
        if self.kind is DocumentDeletionTargetKind.OBJECT_PREFIX and not self.target_key.endswith(
            "/"
        ):
            raise ValueError("Knowledge deletion object prefix is invalid")


@dataclass(frozen=True, slots=True)
class DeletionWorkItem:
    workspace_id: UUID
    document_id: UUID
    targets: tuple[DeletionTarget, ...]


class DeletionRepository(Protocol):
    async def load_work_item(
        self,
        proof: JobLeaseProof,
        *,
        document_id: UUID,
    ) -> DeletionWorkItem: ...

    async def mark_targets_deleted(
        self,
        proof: JobLeaseProof,
        *,
        document_id: UUID,
        target_ids: tuple[UUID, ...],
    ) -> None: ...

    async def mark_targets_failed(
        self,
        proof: JobLeaseProof,
        *,
        document_id: UUID,
        target_ids: tuple[UUID, ...],
        error_code: str,
    ) -> None: ...

    async def complete_deletion(
        self,
        proof: JobLeaseProof,
        *,
        document_id: UUID,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class DeletionReconciliationResult:
    selected: int
    finalized: int
    orphaned: int


class DeletionReconciliationUseCase(Protocol):
    async def reconcile_deletions(self, *, batch_size: int) -> DeletionReconciliationResult: ...


@dataclass(frozen=True, slots=True)
class KnowledgeDeletionService:
    repository: DeletionRepository
    vector_index: VectorIndexWriter
    lexical_index: LexicalIndexWriter
    object_store: PrivateFileObjectStore | None = field(repr=False)

    async def execute(self, job: AcquiredJob) -> UUID:
        document_id = _payload(job)
        work = await self.repository.load_work_item(
            job.lease_proof,
            document_id=document_id,
        )
        for kind in (
            DocumentDeletionTargetKind.VECTOR,
            DocumentDeletionTargetKind.LEXICAL,
        ):
            targets = tuple(target for target in work.targets if target.kind is kind)
            if not targets:
                continue
            try:
                if kind is DocumentDeletionTargetKind.VECTOR:
                    await self.vector_index.delete(tuple(target.target_key for target in targets))
                else:
                    await self.lexical_index.delete(tuple(target.target_key for target in targets))
            except IngestionDependencyError as error:
                await self._failed(job, document_id, targets, error.code)
                raise
            await self._deleted(job, document_id, targets)

        object_kinds = {
            DocumentDeletionTargetKind.OBJECT,
            DocumentDeletionTargetKind.OBJECT_PREFIX,
        }
        for target in (item for item in work.targets if item.kind in object_kinds):
            try:
                if target.kind is DocumentDeletionTargetKind.OBJECT_PREFIX:
                    await self._store().remove_prefix(
                        bucket=target.bucket or "",
                        object_prefix=target.target_key,
                    )
                else:
                    await self._store().remove(
                        bucket=target.bucket or "",
                        object_key=target.target_key,
                    )
            except (FileObjectStoreError, IngestionDependencyError) as caught:
                error_code = (
                    caught.code
                    if isinstance(caught, IngestionDependencyError)
                    else "knowledge_object_delete_failed"
                )
                await self._failed(job, document_id, (target,), error_code)
                raise IngestionDependencyError(error_code) from None
            await self._deleted(job, document_id, (target,))

        cache_targets = tuple(
            target for target in work.targets if target.kind is DocumentDeletionTargetKind.CACHE
        )
        if cache_targets:
            error_code = "knowledge_cache_delete_not_configured"
            await self._failed(job, document_id, cache_targets, error_code)
            raise IngestionDependencyError(error_code)
        await self.repository.complete_deletion(
            job.lease_proof,
            document_id=document_id,
        )
        return document_id

    async def _deleted(
        self,
        job: AcquiredJob,
        document_id: UUID,
        targets: tuple[DeletionTarget, ...],
    ) -> None:
        await self.repository.mark_targets_deleted(
            job.lease_proof,
            document_id=document_id,
            target_ids=tuple(target.id for target in targets),
        )

    async def _failed(
        self,
        job: AcquiredJob,
        document_id: UUID,
        targets: tuple[DeletionTarget, ...],
        error_code: str,
    ) -> None:
        await self.repository.mark_targets_failed(
            job.lease_proof,
            document_id=document_id,
            target_ids=tuple(target.id for target in targets),
            error_code=error_code,
        )

    def _store(self) -> PrivateFileObjectStore:
        if self.object_store is None:
            raise IngestionDependencyError("knowledge_object_store_not_configured")
        return self.object_store


def _payload(job: AcquiredJob) -> UUID:
    if job.scope.workspace_id is None or job.scope.system_scope_key is not None:
        raise ValueError("Knowledge deletion requires a Workspace scope")
    if set(job.payload) != {"document_id", "schema_version"}:
        raise ValueError("Knowledge deletion payload is invalid")
    if job.payload["schema_version"] != 1:
        raise ValueError("Knowledge deletion payload version is invalid")
    try:
        document_id = UUID(str(job.payload["document_id"]))
    except ValueError:
        raise ValueError("Knowledge deletion document ID is invalid") from None
    if document_id.int == 0:
        raise ValueError("Knowledge deletion document ID is invalid")
    return document_id
