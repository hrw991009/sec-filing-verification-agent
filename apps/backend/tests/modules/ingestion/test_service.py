from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest

from industry_platform.modules.files.domain import AttachmentMediaType
from industry_platform.modules.files.ports import PrivateFileObjectStore
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.ingestion.adapters.embedding import (
    DeterministicHashEmbeddingProvider,
)
from industry_platform.modules.ingestion.chunker import BoundedPageChunker
from industry_platform.modules.ingestion.domain import (
    INGESTION_STAGE_SEQUENCE,
    BoundingBox,
    ChunkEmbedding,
    CompleteIngestionStage,
    DocumentParserError,
    EmbeddingInput,
    IndexableChunk,
    IngestionDependencyError,
    IngestionStage,
    IngestionWorkItem,
    ParsedDocument,
    ParsedPage,
    ParsedTextSource,
    ParserErrorCode,
    ParserRequest,
    StoredStageCheckpoint,
    sha256_text,
)
from industry_platform.modules.ingestion.index_contract import (
    INDEX_VERSION,
    embedding_config_snapshot,
    index_config_snapshot,
)
from industry_platform.modules.ingestion.ports import DocumentParser, IngestionRepository
from industry_platform.modules.ingestion.service import KnowledgeIngestionService
from industry_platform.modules.jobs.domain import (
    AcquiredJob,
    CheckpointJobCommand,
    ExecutionScope,
    JobLease,
    LostJobLeaseError,
)
from industry_platform.modules.jobs.ports import JobApplicationUseCase
from industry_platform.modules.knowledge.domain import KNOWLEDGE_INGESTION_TASK_NAME
from industry_platform.modules.knowledge.parser_contract import (
    CHUNKER_NAME,
    CHUNKER_VERSION,
    PARSER_NAME,
    PARSER_SCHEMA_VERSION,
    PARSER_VERSION,
    chunker_config_snapshot,
    parser_config_snapshot,
)

NOW = datetime(2026, 8, 24, 8, 0, tzinfo=UTC)
WORKSPACE_ID = uuid4()
DOCUMENT_ID = uuid4()
KNOWLEDGE_BASE_ID = uuid4()
VERSION_ID = uuid4()
FILE_ID = uuid4()
JOB_ID = uuid4()
SOURCE = b"Durable ingestion source text for recovery."
SOURCE_HASH = hashlib.sha256(SOURCE).hexdigest()
CHUNK_ID = uuid4()


def _work() -> IngestionWorkItem:
    return IngestionWorkItem(
        workspace_id=WORKSPACE_ID,
        document_id=DOCUMENT_ID,
        document_version_id=VERSION_ID,
        ingestion_job_id=JOB_ID,
        file_id=FILE_ID,
        original_name="recovery.txt",
        media_type=AttachmentMediaType.TEXT_PLAIN,
        source_bucket="private",
        source_object_key="ready/source",
        source_size=len(SOURCE),
        source_sha256=SOURCE_HASH,
        parser_name=PARSER_NAME,
        parser_version=PARSER_VERSION,
        parser_schema_version=PARSER_SCHEMA_VERSION,
        parser_config=parser_config_snapshot(max_input_bytes=len(SOURCE)),
        chunker_name=CHUNKER_NAME,
        chunker_version=CHUNKER_VERSION,
        chunker_config=chunker_config_snapshot(),
        embedding_config=embedding_config_snapshot(),
        index_config=index_config_snapshot(),
        checkpoints=(),
    )


def _job(*, fencing_token: int, attempt_count: int) -> AcquiredJob:
    return AcquiredJob(
        job_id=JOB_ID,
        scope=ExecutionScope(workspace_id=WORKSPACE_ID),
        trace_id=TraceId("ingestion-test"),
        task_name=KNOWLEDGE_INGESTION_TASK_NAME,
        queue_name="ingestion",
        payload={
            "document_version_id": str(VERSION_ID),
            "file_id": str(FILE_ID),
            "schema_version": 1,
        },
        dispatch_generation=attempt_count,
        lease=JobLease(
            owner=f"worker-{attempt_count}",
            lease_token=uuid4(),
            generation=attempt_count,
            fencing_token=fencing_token,
            heartbeat_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
        ),
        stage_sequence=attempt_count,
        attempt_count=attempt_count,
        max_attempts=5,
        soft_time_limit_seconds=1_500,
        hard_time_limit_seconds=1_800,
    )


class RecordingRepository:
    def __init__(self) -> None:
        self.work = _work()
        self.completions: list[CompleteIngestionStage] = []
        self.hard_stop_before: IngestionStage | None = None
        self.retry_errors: list[str] = []
        self.terminal_errors: list[tuple[str, bool]] = []
        self.embedding_inputs: tuple[EmbeddingInput, ...] = ()
        self.embeddings: tuple[ChunkEmbedding, ...] = ()

    async def load_work_item(
        self,
        proof: object,
        *,
        document_version_id: UUID,
        file_id: UUID,
    ) -> IngestionWorkItem:
        del proof
        assert document_version_id == VERSION_ID
        assert file_id == FILE_ID
        return self.work

    async def begin_stage(
        self,
        proof: object,
        *,
        document_version_id: UUID,
        stage: IngestionStage,
    ) -> bool:
        del proof
        assert document_version_id == VERSION_ID
        if self.hard_stop_before is stage:
            self.hard_stop_before = None
            raise LostJobLeaseError
        return self.work.checkpoint(stage) is None

    async def complete_stage(self, command: CompleteIngestionStage) -> bool:
        self.completions.append(command)
        if command.stage is IngestionStage.CHUNKING:
            self.embedding_inputs = tuple(
                EmbeddingInput(
                    chunk_id=CHUNK_ID,
                    text=chunk.text,
                    content_hash=chunk.content_hash,
                )
                for chunk in command.chunks
            )
        elif command.stage is IngestionStage.EMBEDDING:
            self.embeddings = command.embeddings
        checkpoint = StoredStageCheckpoint(
            stage=command.stage,
            stage_sequence=INGESTION_STAGE_SEQUENCE[command.stage],
            input_hash=command.input_hash,
            output_hash=command.output_hash,
            output_bucket=command.output_bucket,
            output_object_key=command.output_object_key,
        )
        self.work = replace(
            self.work,
            checkpoints=(*self.work.checkpoints, checkpoint),
        )
        return True

    async def load_embedding_inputs(
        self,
        proof: object,
        *,
        document_version_id: UUID,
    ) -> tuple[EmbeddingInput, ...]:
        del proof
        assert document_version_id == VERSION_ID
        return self.embedding_inputs

    async def load_indexable_chunks(
        self,
        proof: object,
        *,
        document_version_id: UUID,
    ) -> tuple[IndexableChunk, ...]:
        del proof
        assert document_version_id == VERSION_ID
        return tuple(
            IndexableChunk(
                workspace_id=WORKSPACE_ID,
                knowledge_base_id=KNOWLEDGE_BASE_ID,
                document_id=DOCUMENT_ID,
                document_version_id=VERSION_ID,
                chunk_id=embedding.chunk_id,
                ordinal=index,
                page_number=1,
                text=self.embedding_inputs[index - 1].text,
                content_hash=embedding.content_hash,
                vector=embedding.vector,
                external_id=f"{embedding.chunk_id}:{INDEX_VERSION}",
            )
            for index, embedding in enumerate(self.embeddings, start=1)
        )

    async def mark_retrying(
        self,
        proof: object,
        *,
        document_version_id: UUID,
        error_code: str,
    ) -> None:
        del proof
        assert document_version_id == VERSION_ID
        self.retry_errors.append(error_code)

    async def mark_terminal_failure(
        self,
        proof: object,
        *,
        document_version_id: UUID,
        error_code: str,
        cancelled: bool,
    ) -> None:
        del proof
        assert document_version_id == VERSION_ID
        self.terminal_errors.append((error_code, cancelled))


class RecordingJobs:
    def __init__(self) -> None:
        self.checkpoints: list[CheckpointJobCommand] = []

    async def checkpoint(self, command: CheckpointJobCommand) -> None:
        self.checkpoints.append(command)


class MemoryObjectStore:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {("private", "ready/source"): SOURCE}
        self.puts: list[tuple[str, str]] = []

    async def read_bounded(self, *, bucket: str, object_key: str, maximum_bytes: int) -> bytes:
        content = self.objects[(bucket, object_key)]
        assert len(content) <= maximum_bytes
        return content

    async def put_private(
        self,
        *,
        bucket: str,
        object_key: str,
        content_type: str,
        content: bytes,
    ) -> None:
        del content_type
        self.objects[(bucket, object_key)] = content
        self.puts.append((bucket, object_key))


class RecordingParser:
    def __init__(self) -> None:
        self.calls = 0

    async def parse(self, request: ParserRequest) -> ParsedDocument:
        self.calls += 1
        text = request.content.decode("utf-8")
        return ParsedDocument(
            source_sha256=request.source_sha256,
            media_type=request.media_type,
            pages=(
                ParsedPage(
                    page_number=1,
                    width_points=612,
                    height_points=792,
                    text=text,
                    text_source=ParsedTextSource.PLAIN_TEXT,
                    bbox=BoundingBox(0, 0, 612, 792),
                    title_path=(),
                    content_hash=sha256_text(text),
                ),
            ),
            assets=(),
        )


class FailingParser:
    def __init__(self, code: ParserErrorCode) -> None:
        self.code = code

    async def parse(self, request: ParserRequest) -> ParsedDocument:
        del request
        raise DocumentParserError(self.code)


class RecordingIndex:
    def __init__(self, *, error_code: str | None = None) -> None:
        self.error_code = error_code
        self.upserts: list[tuple[IndexableChunk, ...]] = []

    async def upsert(self, chunks: tuple[IndexableChunk, ...]) -> tuple[str, ...]:
        self.upserts.append(chunks)
        if self.error_code is not None:
            raise IngestionDependencyError(self.error_code)
        return tuple(chunk.external_id for chunk in chunks)

    async def delete(self, external_ids: tuple[str, ...]) -> None:
        del external_ids


def _service(
    repository: RecordingRepository,
    jobs: RecordingJobs,
    store: MemoryObjectStore,
    parser: DocumentParser,
    *,
    vector_index: RecordingIndex | None = None,
    lexical_index: RecordingIndex | None = None,
) -> KnowledgeIngestionService:
    return KnowledgeIngestionService(
        repository=cast(IngestionRepository, repository),
        jobs=cast(JobApplicationUseCase, jobs),
        parser=parser,
        chunker=BoundedPageChunker(),
        embedding_provider=DeterministicHashEmbeddingProvider(),
        vector_index=vector_index or RecordingIndex(),
        lexical_index=lexical_index or RecordingIndex(),
        object_store=cast(PrivateFileObjectStore, store),
    )


@pytest.mark.asyncio
async def test_ingestion_completes_seven_versioned_stages_and_becomes_ready() -> None:
    repository = RecordingRepository()
    jobs = RecordingJobs()
    store = MemoryObjectStore()
    parser = RecordingParser()

    result = await _service(repository, jobs, store, parser).execute(
        _job(fencing_token=7, attempt_count=1)
    )

    assert result.status == "ready"
    assert [item.stage for item in repository.completions] == list(IngestionStage)
    assert [item.stage_name for item in jobs.checkpoints] == [
        stage.value for stage in IngestionStage
    ]
    assert parser.calls == 1
    assert result.page_count == 1
    assert result.chunk_count == 1


@pytest.mark.asyncio
async def test_hard_stop_after_parsing_resumes_from_snapshot_without_source_or_reparse() -> None:
    repository = RecordingRepository()
    repository.hard_stop_before = IngestionStage.EXTRACTING_ASSETS
    jobs = RecordingJobs()
    store = MemoryObjectStore()
    parser = RecordingParser()
    service = _service(repository, jobs, store, parser)

    with pytest.raises(LostJobLeaseError):
        await service.execute(_job(fencing_token=7, attempt_count=1))
    assert repository.work.checkpoint(IngestionStage.PARSING) is not None
    del store.objects[("private", "ready/source")]

    result = await service.execute(_job(fencing_token=8, attempt_count=2))

    assert result.status == "ready"
    assert parser.calls == 1
    assert len(repository.work.checkpoints) == 7
    assert len({checkpoint.stage for checkpoint in repository.work.checkpoints}) == 7


@pytest.mark.asyncio
async def test_vector_dependency_failure_is_stable_and_does_not_run_lexical_index() -> None:
    repository = RecordingRepository()
    vector = RecordingIndex(error_code="vector_index_unavailable")
    lexical = RecordingIndex()
    service = _service(
        repository,
        RecordingJobs(),
        MemoryObjectStore(),
        RecordingParser(),
        vector_index=vector,
        lexical_index=lexical,
    )

    with pytest.raises(IngestionDependencyError) as captured:
        await service.execute(_job(fencing_token=7, attempt_count=1))

    assert captured.value.code == "vector_index_unavailable"
    assert repository.retry_errors == ["vector_index_unavailable"]
    assert lexical.upserts == []
    assert repository.work.checkpoint(IngestionStage.EMBEDDING) is not None
    assert repository.work.checkpoint(IngestionStage.VECTOR_INDEXING) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "retryable"),
    [
        (ParserErrorCode.TIMEOUT, True),
        (ParserErrorCode.CORRUPT_DOCUMENT, False),
    ],
)
async def test_parser_failures_record_retryable_or_terminal_version_state(
    code: ParserErrorCode,
    retryable: bool,
) -> None:
    repository = RecordingRepository()
    service = _service(
        repository,
        RecordingJobs(),
        MemoryObjectStore(),
        FailingParser(code),
    )

    with pytest.raises(DocumentParserError) as captured:
        await service.execute(_job(fencing_token=7, attempt_count=1))

    assert captured.value.code is code
    assert repository.retry_errors == ([code.value] if retryable else [])
    assert repository.terminal_errors == ([] if retryable else [(code.value, False)])
