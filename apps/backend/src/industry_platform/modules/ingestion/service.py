"""Resumable seven-stage Knowledge ingestion orchestration."""

import hashlib
import json
from dataclasses import dataclass, field
from uuid import UUID

from industry_platform.modules.files.ports import (
    FileObjectStoreError,
    PrivateFileObjectStore,
)
from industry_platform.modules.ingestion.adapters.sqlalchemy import validate_work_item_contract
from industry_platform.modules.ingestion.domain import (
    INGESTION_STAGE_SEQUENCE,
    ChunkEmbedding,
    CompleteIngestionStage,
    DocumentParserError,
    IngestionCancelledError,
    IngestionDependencyError,
    IngestionPersistenceError,
    IngestionResult,
    IngestionStage,
    IngestionWorkItem,
    ParsedChunk,
    ParsedDocument,
    ParserBudget,
    ParserErrorCode,
    ParserRequest,
    StoredAssetPreview,
    StoredStageCheckpoint,
)
from industry_platform.modules.ingestion.ports import (
    DocumentChunker,
    DocumentParser,
    EmbeddingProvider,
    IngestionRepository,
    LexicalIndexWriter,
    VectorIndexWriter,
)
from industry_platform.modules.jobs.domain import (
    AcquiredJob,
    CheckpointJobCommand,
)
from industry_platform.modules.jobs.ports import JobApplicationUseCase
from industry_platform.modules.knowledge.parser_contract import (
    MAX_PARSER_OUTPUT_BYTES,
    chunker_config_snapshot,
    parser_config_snapshot,
)


@dataclass(frozen=True, slots=True)
class KnowledgeIngestionService:
    repository: IngestionRepository
    jobs: JobApplicationUseCase
    parser: DocumentParser
    chunker: DocumentChunker
    embedding_provider: EmbeddingProvider
    vector_index: VectorIndexWriter
    lexical_index: LexicalIndexWriter
    object_store: PrivateFileObjectStore | None = field(repr=False)

    async def execute(self, job: AcquiredJob) -> IngestionResult:
        document_version_id, file_id = _payload(job)
        try:
            return await self._execute(
                job, document_version_id=document_version_id, file_id=file_id
            )
        except IngestionCancelledError:
            await self.repository.mark_terminal_failure(
                job.lease_proof,
                document_version_id=document_version_id,
                error_code=ParserErrorCode.CANCELLED.value,
                cancelled=True,
            )
            raise
        except DocumentParserError as error:
            if error.retryable:
                await self.repository.mark_retrying(
                    job.lease_proof,
                    document_version_id=document_version_id,
                    error_code=error.code.value,
                )
            else:
                await self.repository.mark_terminal_failure(
                    job.lease_proof,
                    document_version_id=document_version_id,
                    error_code=error.code.value,
                    cancelled=False,
                )
            raise
        except IngestionDependencyError as error:
            await self.repository.mark_retrying(
                job.lease_proof,
                document_version_id=document_version_id,
                error_code=error.code,
            )
            raise

    async def _execute(
        self,
        job: AcquiredJob,
        *,
        document_version_id: UUID,
        file_id: UUID,
    ) -> IngestionResult:
        work = await self.repository.load_work_item(
            job.lease_proof,
            document_version_id=document_version_id,
            file_id=file_id,
        )
        validate_work_item_contract(work)
        parser_budget = _parser_budget(work)
        source: bytes | None = None

        if work.checkpoint(IngestionStage.VALIDATING) is None:
            await self._begin(job, work, IngestionStage.VALIDATING)
            source = await self._read_source(work)
            await self.repository.complete_stage(
                CompleteIngestionStage(
                    proof=job.lease_proof,
                    work_item=work,
                    stage=IngestionStage.VALIDATING,
                    attempt_count=job.attempt_count,
                    input_hash=work.source_sha256,
                    output_hash=work.source_sha256,
                    stats={"source_bytes": len(source)},
                )
            )

        parsing_checkpoint = work.checkpoint(IngestionStage.PARSING)
        if parsing_checkpoint is None:
            await self._begin(job, work, IngestionStage.PARSING)
            source = source if source is not None else await self._read_source(work)
            parsed = await self.parser.parse(
                ParserRequest(
                    document_version_id=work.document_version_id,
                    original_name=work.original_name,
                    media_type=work.media_type,
                    source_sha256=work.source_sha256,
                    content=source,
                    budget=parser_budget,
                )
            )
            snapshot = parsed.snapshot_bytes()
            snapshot_hash = hashlib.sha256(snapshot).hexdigest()
            snapshot_key = (
                f"derived/{work.workspace_id}/knowledge/{work.document_version_id}/"
                f"parser/{work.parser_version}/{snapshot_hash}.json"
            )
            await self._put_private(
                bucket=work.source_bucket,
                object_key=snapshot_key,
                content_type="application/json",
                content=snapshot,
            )
            await self.repository.complete_stage(
                CompleteIngestionStage(
                    proof=job.lease_proof,
                    work_item=work,
                    stage=IngestionStage.PARSING,
                    attempt_count=job.attempt_count,
                    input_hash=work.source_sha256,
                    output_hash=snapshot_hash,
                    output_bucket=work.source_bucket,
                    output_object_key=snapshot_key,
                    stats={
                        "asset_count": len(parsed.assets),
                        "page_count": len(parsed.pages),
                        "snapshot_bytes": len(snapshot),
                        "text_characters": parsed.text_characters,
                    },
                )
            )
        else:
            parsed = await self._load_parsed_snapshot(parsing_checkpoint)
            snapshot_hash = parsing_checkpoint.output_hash

        assets_checkpoint = work.checkpoint(IngestionStage.EXTRACTING_ASSETS)
        if assets_checkpoint is None:
            await self._begin(job, work, IngestionStage.EXTRACTING_ASSETS)
            previews = await self._store_asset_previews(work, parsed)
            assets_hash = _asset_output_hash(parsed)
            await self.repository.complete_stage(
                CompleteIngestionStage(
                    proof=job.lease_proof,
                    work_item=work,
                    stage=IngestionStage.EXTRACTING_ASSETS,
                    attempt_count=job.attempt_count,
                    input_hash=snapshot_hash,
                    output_hash=assets_hash,
                    stats={
                        "asset_count": len(parsed.assets),
                        "page_count": len(parsed.pages),
                    },
                    parsed_document=parsed,
                    asset_previews=previews,
                )
            )
        else:
            assets_hash = assets_checkpoint.output_hash

        chunks = self.chunker.chunk(parsed)
        chunking_checkpoint = work.checkpoint(IngestionStage.CHUNKING)
        if chunking_checkpoint is None:
            await self._begin(job, work, IngestionStage.CHUNKING)
            chunk_hash = _chunk_output_hash(chunks)
            await self.repository.complete_stage(
                CompleteIngestionStage(
                    proof=job.lease_proof,
                    work_item=work,
                    stage=IngestionStage.CHUNKING,
                    attempt_count=job.attempt_count,
                    input_hash=assets_hash,
                    output_hash=chunk_hash,
                    stats={"chunk_count": len(chunks)},
                    chunks=chunks,
                )
            )
            chunk_hash = _chunk_output_hash(chunks)
        else:
            chunk_hash = chunking_checkpoint.output_hash

        embedding_checkpoint = work.checkpoint(IngestionStage.EMBEDDING)
        if embedding_checkpoint is None:
            await self._begin(job, work, IngestionStage.EMBEDDING)
            inputs = await self.repository.load_embedding_inputs(
                job.lease_proof,
                document_version_id=work.document_version_id,
            )
            embeddings = await self.embedding_provider.embed(inputs)
            if len(embeddings) != len(inputs):
                raise IngestionDependencyError("embedding_output_invalid")
            embedding_hash = _embedding_output_hash(embeddings)
            await self.repository.complete_stage(
                CompleteIngestionStage(
                    proof=job.lease_proof,
                    work_item=work,
                    stage=IngestionStage.EMBEDDING,
                    attempt_count=job.attempt_count,
                    input_hash=chunk_hash,
                    output_hash=embedding_hash,
                    stats={
                        "chunk_count": len(embeddings),
                        "dimension": len(embeddings[0].vector),
                    },
                    embeddings=embeddings,
                )
            )
        else:
            embedding_hash = embedding_checkpoint.output_hash

        indexable = await self.repository.load_indexable_chunks(
            job.lease_proof,
            document_version_id=work.document_version_id,
        )
        vector_checkpoint = work.checkpoint(IngestionStage.VECTOR_INDEXING)
        if vector_checkpoint is None:
            await self._begin(job, work, IngestionStage.VECTOR_INDEXING)
            vector_ids = await self.vector_index.upsert(indexable)
            vector_hash = _index_output_hash(vector_ids)
            await self.repository.complete_stage(
                CompleteIngestionStage(
                    proof=job.lease_proof,
                    work_item=work,
                    stage=IngestionStage.VECTOR_INDEXING,
                    attempt_count=job.attempt_count,
                    input_hash=embedding_hash,
                    output_hash=vector_hash,
                    stats={"index_count": len(vector_ids)},
                    external_ids=vector_ids,
                )
            )
        else:
            vector_hash = vector_checkpoint.output_hash

        if work.checkpoint(IngestionStage.LEXICAL_INDEXING) is None:
            await self._begin(job, work, IngestionStage.LEXICAL_INDEXING)
            lexical_ids = await self.lexical_index.upsert(indexable)
            lexical_hash = _index_output_hash(lexical_ids)
            await self.repository.complete_stage(
                CompleteIngestionStage(
                    proof=job.lease_proof,
                    work_item=work,
                    stage=IngestionStage.LEXICAL_INDEXING,
                    attempt_count=job.attempt_count,
                    input_hash=vector_hash,
                    output_hash=lexical_hash,
                    stats={"index_count": len(lexical_ids)},
                    external_ids=lexical_ids,
                )
            )

        return IngestionResult(
            document_version_id=work.document_version_id,
            page_count=len(parsed.pages),
            chunk_count=len(chunks),
            asset_count=len(parsed.assets),
            status="ready",
        )

    async def _begin(
        self,
        job: AcquiredJob,
        work: IngestionWorkItem,
        stage: IngestionStage,
    ) -> None:
        await self.repository.begin_stage(
            job.lease_proof,
            document_version_id=work.document_version_id,
            stage=stage,
        )
        await self.jobs.checkpoint(
            CheckpointJobCommand(
                proof=job.lease_proof,
                stage_name=stage.value,
                stage_sequence=job.stage_sequence + INGESTION_STAGE_SEQUENCE[stage],
            )
        )

    async def _read_source(self, work: IngestionWorkItem) -> bytes:
        store = self._store()
        try:
            source = await store.read_bounded(
                bucket=work.source_bucket,
                object_key=work.source_object_key,
                maximum_bytes=work.source_size,
            )
        except FileObjectStoreError:
            raise IngestionDependencyError from None
        if (
            len(source) != work.source_size
            or hashlib.sha256(source).hexdigest() != work.source_sha256
        ):
            raise DocumentParserError(ParserErrorCode.CORRUPT_DOCUMENT)
        return source

    async def _load_parsed_snapshot(self, checkpoint: StoredStageCheckpoint) -> ParsedDocument:
        if checkpoint.output_bucket is None or checkpoint.output_object_key is None:
            raise IngestionPersistenceError
        store = self._store()
        try:
            snapshot = await store.read_bounded(
                bucket=checkpoint.output_bucket,
                object_key=checkpoint.output_object_key,
                maximum_bytes=MAX_PARSER_OUTPUT_BYTES,
            )
        except FileObjectStoreError:
            raise IngestionDependencyError from None
        if hashlib.sha256(snapshot).hexdigest() != checkpoint.output_hash:
            raise IngestionPersistenceError
        try:
            return ParsedDocument.from_snapshot_bytes(snapshot)
        except ValueError:
            raise IngestionPersistenceError from None

    async def _store_asset_previews(
        self,
        work: IngestionWorkItem,
        parsed: ParsedDocument,
    ) -> tuple[StoredAssetPreview, ...]:
        previews: list[StoredAssetPreview] = []
        for asset in parsed.assets:
            object_key = (
                f"derived/{work.workspace_id}/knowledge/{work.document_version_id}/"
                f"assets/{asset.preview_sha256}.png"
            )
            await self._put_private(
                bucket=work.source_bucket,
                object_key=object_key,
                content_type=asset.preview_mime_type,
                content=asset.preview,
            )
            previews.append(
                StoredAssetPreview(
                    ordinal=asset.ordinal,
                    bucket=work.source_bucket,
                    object_key=object_key,
                )
            )
        return tuple(previews)

    async def _put_private(
        self,
        *,
        bucket: str,
        object_key: str,
        content_type: str,
        content: bytes,
    ) -> None:
        store = self._store()
        try:
            await store.put_private(
                bucket=bucket,
                object_key=object_key,
                content_type=content_type,
                content=content,
            )
        except FileObjectStoreError:
            raise IngestionDependencyError from None

    def _store(self) -> PrivateFileObjectStore:
        if self.object_store is None:
            raise IngestionDependencyError
        return self.object_store


def _payload(job: AcquiredJob) -> tuple[UUID, UUID]:
    if job.scope.workspace_id is None or job.scope.system_scope_key is not None:
        raise ValueError("Knowledge ingestion requires a Workspace scope")
    if set(job.payload) != {"document_version_id", "file_id", "schema_version"}:
        raise ValueError("Knowledge ingestion payload is invalid")
    if job.payload["schema_version"] != 1:
        raise ValueError("Knowledge ingestion payload version is invalid")
    try:
        document_version_id = UUID(str(job.payload["document_version_id"]))
        file_id = UUID(str(job.payload["file_id"]))
    except ValueError:
        raise ValueError("Knowledge ingestion payload IDs are invalid") from None
    if document_version_id.int == 0 or file_id.int == 0:
        raise ValueError("Knowledge ingestion payload IDs are invalid")
    return document_version_id, file_id


def _parser_budget(work: IngestionWorkItem) -> ParserBudget:
    budget_raw = work.parser_config.get("budget")
    if not isinstance(budget_raw, dict):
        raise ValueError("Stored parser budget is invalid")
    budget = ParserBudget(
        max_input_bytes=_required_config_int(budget_raw, "max_input_bytes"),
        max_pages=_required_config_int(budget_raw, "max_pages"),
        max_text_characters=_required_config_int(budget_raw, "max_text_characters"),
        max_page_image_pixels=_required_config_int(budget_raw, "max_page_image_pixels"),
        max_output_bytes=_required_config_int(budget_raw, "max_output_bytes"),
        timeout_seconds=_required_config_int(budget_raw, "timeout_seconds"),
    )
    if budget.max_input_bytes < work.source_size:
        raise ValueError("Stored parser input budget is too small")
    if dict(work.parser_config) != parser_config_snapshot(max_input_bytes=budget.max_input_bytes):
        raise ValueError("Stored parser configuration is unsupported")
    if dict(work.chunker_config) != chunker_config_snapshot():
        raise ValueError("Stored chunker configuration is unsupported")
    return budget


def _required_config_int(value: dict[str, object], key: str) -> int:
    item = value.get(key)
    if isinstance(item, bool) or not isinstance(item, int):
        raise ValueError("Stored parser configuration is invalid")
    return item


def _asset_output_hash(document: ParsedDocument) -> str:
    payload = {
        "assets": [
            {
                "content_hash": asset.content_hash,
                "ordinal": asset.ordinal,
                "preview_sha256": asset.preview_sha256,
            }
            for asset in document.assets
        ],
        "pages": [
            {
                "content_hash": page.content_hash,
                "page_number": page.page_number,
            }
            for page in document.pages
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _chunk_output_hash(chunks: tuple[ParsedChunk, ...]) -> str:
    return hashlib.sha256(
        json.dumps(
            [chunk.content_hash for chunk in chunks],
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _embedding_output_hash(embeddings: tuple[ChunkEmbedding, ...]) -> str:
    return hashlib.sha256(
        json.dumps(
            [
                {
                    "chunk_id": str(value.chunk_id),
                    "content_hash": value.content_hash,
                    "vector": list(value.vector),
                }
                for value in embeddings
            ],
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _index_output_hash(external_ids: tuple[str, ...]) -> str:
    if not external_ids:
        raise ValueError("Index output is empty")
    return hashlib.sha256(
        json.dumps(list(external_ids), separators=(",", ":")).encode("utf-8")
    ).hexdigest()
