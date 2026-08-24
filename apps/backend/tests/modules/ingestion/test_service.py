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
from industry_platform.modules.ingestion.chunker import BoundedPageChunker
from industry_platform.modules.ingestion.domain import (
    BoundingBox,
    CompleteIngestionStage,
    DocumentParserError,
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
VERSION_ID = uuid4()
FILE_ID = uuid4()
JOB_ID = uuid4()
SOURCE = b"Durable ingestion source text for recovery."
SOURCE_HASH = hashlib.sha256(SOURCE).hexdigest()


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
        checkpoint = StoredStageCheckpoint(
            stage=command.stage,
            stage_sequence={
                IngestionStage.VALIDATING: 1,
                IngestionStage.PARSING: 2,
                IngestionStage.EXTRACTING_ASSETS: 3,
                IngestionStage.CHUNKING: 4,
            }[command.stage],
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


def _service(
    repository: RecordingRepository,
    jobs: RecordingJobs,
    store: MemoryObjectStore,
    parser: DocumentParser,
) -> KnowledgeIngestionService:
    return KnowledgeIngestionService(
        repository=cast(IngestionRepository, repository),
        jobs=cast(JobApplicationUseCase, jobs),
        parser=parser,
        chunker=BoundedPageChunker(),
        object_store=cast(PrivateFileObjectStore, store),
    )


@pytest.mark.asyncio
async def test_ingestion_completes_four_versioned_stages_without_ready_status() -> None:
    repository = RecordingRepository()
    jobs = RecordingJobs()
    store = MemoryObjectStore()
    parser = RecordingParser()

    result = await _service(repository, jobs, store, parser).execute(
        _job(fencing_token=7, attempt_count=1)
    )

    assert result.status == "parsed"
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

    assert result.status == "parsed"
    assert parser.calls == 1
    assert len(repository.work.checkpoints) == 4
    assert len({checkpoint.stage for checkpoint in repository.work.checkpoints}) == 4


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
