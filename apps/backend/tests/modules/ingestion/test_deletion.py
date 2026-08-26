from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest

from industry_platform.modules.files.ports import PrivateFileObjectStore
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.ingestion.deletion import (
    DeletionRepository,
    DeletionTarget,
    DeletionWorkItem,
    KnowledgeDeletionService,
)
from industry_platform.modules.ingestion.domain import IngestionDependencyError
from industry_platform.modules.ingestion.ports import LexicalIndexWriter, VectorIndexWriter
from industry_platform.modules.jobs.domain import (
    AcquiredJob,
    ExecutionScope,
    JobLease,
    LostJobLeaseError,
)
from industry_platform.modules.knowledge.domain import (
    KNOWLEDGE_DELETION_TASK_NAME,
    DocumentDeletionTargetKind,
)

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
WORKSPACE_ID = uuid4()
DOCUMENT_ID = uuid4()
JOB_ID = uuid4()


def _job(*, attempt: int) -> AcquiredJob:
    return AcquiredJob(
        job_id=JOB_ID,
        scope=ExecutionScope(workspace_id=WORKSPACE_ID),
        trace_id=TraceId("knowledge-deletion-test"),
        task_name=KNOWLEDGE_DELETION_TASK_NAME,
        queue_name="ingestion",
        payload={"document_id": str(DOCUMENT_ID), "schema_version": 1},
        dispatch_generation=attempt,
        lease=JobLease(
            owner=f"worker-{attempt}",
            lease_token=uuid4(),
            generation=attempt,
            fencing_token=attempt,
            heartbeat_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
        ),
        stage_sequence=0,
        attempt_count=attempt,
        max_attempts=10,
        soft_time_limit_seconds=1_500,
        hard_time_limit_seconds=1_800,
    )


class RecordingDeletionRepository:
    def __init__(self) -> None:
        self.targets = {
            target.id: target
            for target in (
                DeletionTarget(uuid4(), DocumentDeletionTargetKind.VECTOR, "chunk:v1"),
                DeletionTarget(uuid4(), DocumentDeletionTargetKind.LEXICAL, "chunk:v1"),
                DeletionTarget(
                    uuid4(),
                    DocumentDeletionTargetKind.OBJECT,
                    "derived/document.json",
                    "private",
                ),
                DeletionTarget(
                    uuid4(),
                    DocumentDeletionTargetKind.OBJECT_PREFIX,
                    "derived/version/",
                    "private",
                ),
            )
        }
        self.deleted: set[UUID] = set()
        self.failed: dict[UUID, str] = {}
        self.completed = False
        self.hard_stop_once = False

    async def load_work_item(
        self,
        proof: object,
        *,
        document_id: UUID,
    ) -> DeletionWorkItem:
        del proof
        assert document_id == DOCUMENT_ID
        return DeletionWorkItem(
            workspace_id=WORKSPACE_ID,
            document_id=DOCUMENT_ID,
            targets=tuple(
                target
                for target_id, target in self.targets.items()
                if target_id not in self.deleted
            ),
        )

    async def mark_targets_deleted(
        self,
        proof: object,
        *,
        document_id: UUID,
        target_ids: tuple[UUID, ...],
    ) -> None:
        del proof
        assert document_id == DOCUMENT_ID
        if self.hard_stop_once:
            self.hard_stop_once = False
            raise LostJobLeaseError
        self.deleted.update(target_ids)
        for target_id in target_ids:
            self.failed.pop(target_id, None)

    async def mark_targets_failed(
        self,
        proof: object,
        *,
        document_id: UUID,
        target_ids: tuple[UUID, ...],
        error_code: str,
    ) -> None:
        del proof
        assert document_id == DOCUMENT_ID
        self.failed.update({target_id: error_code for target_id in target_ids})

    async def complete_deletion(
        self,
        proof: object,
        *,
        document_id: UUID,
    ) -> None:
        del proof
        assert document_id == DOCUMENT_ID
        assert self.deleted == set(self.targets)
        self.completed = True


class RecordingIndex:
    def __init__(self, error_code: str | None = None) -> None:
        self.error_code = error_code
        self.deletes: list[tuple[str, ...]] = []

    async def upsert(self, chunks: object) -> tuple[str, ...]:
        del chunks
        raise AssertionError("Deletion must not index chunks")

    async def delete(self, external_ids: tuple[str, ...]) -> None:
        self.deletes.append(external_ids)
        if self.error_code is not None:
            raise IngestionDependencyError(self.error_code)


class RecordingObjectStore:
    def __init__(self) -> None:
        self.removes: list[tuple[str, str]] = []
        self.prefix_removes: list[tuple[str, str]] = []

    async def remove(self, *, bucket: str, object_key: str) -> None:
        self.removes.append((bucket, object_key))

    async def remove_prefix(self, *, bucket: str, object_prefix: str) -> None:
        self.prefix_removes.append((bucket, object_prefix))


def _service(
    repository: RecordingDeletionRepository,
    *,
    vector: RecordingIndex | None = None,
) -> tuple[KnowledgeDeletionService, RecordingIndex, RecordingIndex, RecordingObjectStore]:
    vector_index = vector or RecordingIndex()
    lexical_index = RecordingIndex()
    store = RecordingObjectStore()
    return (
        KnowledgeDeletionService(
            repository=cast(DeletionRepository, repository),
            vector_index=cast(VectorIndexWriter, vector_index),
            lexical_index=cast(LexicalIndexWriter, lexical_index),
            object_store=cast(PrivateFileObjectStore, store),
        ),
        vector_index,
        lexical_index,
        store,
    )


@pytest.mark.asyncio
async def test_deletion_cleans_all_stores_before_completing_postgres() -> None:
    repository = RecordingDeletionRepository()
    service, vector, lexical, store = _service(repository)

    assert await service.execute(_job(attempt=1)) == DOCUMENT_ID

    assert vector.deletes == [("chunk:v1",)]
    assert lexical.deletes == [("chunk:v1",)]
    assert store.removes == [("private", "derived/document.json")]
    assert store.prefix_removes == [("private", "derived/version/")]
    assert repository.completed is True


@pytest.mark.asyncio
async def test_deletion_records_dependency_failure_without_touching_later_stores() -> None:
    repository = RecordingDeletionRepository()
    service, _vector, lexical, store = _service(
        repository,
        vector=RecordingIndex("vector_index_delete_failed"),
    )

    with pytest.raises(IngestionDependencyError) as captured:
        await service.execute(_job(attempt=1))

    assert captured.value.code == "vector_index_delete_failed"
    assert set(repository.failed.values()) == {"vector_index_delete_failed"}
    assert lexical.deletes == []
    assert store.removes == []
    assert repository.completed is False


@pytest.mark.asyncio
async def test_ack_loss_repeats_idempotent_external_delete_then_finishes() -> None:
    repository = RecordingDeletionRepository()
    repository.hard_stop_once = True
    service, vector, _lexical, _store = _service(repository)

    with pytest.raises(LostJobLeaseError):
        await service.execute(_job(attempt=1))
    assert repository.completed is False

    assert await service.execute(_job(attempt=2)) == DOCUMENT_ID
    assert vector.deletes == [("chunk:v1",), ("chunk:v1",)]
    assert repository.completed is True


@pytest.mark.asyncio
async def test_deletion_does_not_claim_an_unconfigured_cache_was_deleted() -> None:
    repository = RecordingDeletionRepository()
    cache = DeletionTarget(uuid4(), DocumentDeletionTargetKind.CACHE, "document:cache")
    repository.targets[cache.id] = cache
    service, _vector, _lexical, _store = _service(repository)

    with pytest.raises(IngestionDependencyError) as captured:
        await service.execute(_job(attempt=1))

    assert captured.value.code == "knowledge_cache_delete_not_configured"
    assert repository.failed[cache.id] == "knowledge_cache_delete_not_configured"
    assert cache.id not in repository.deleted
    assert repository.completed is False
