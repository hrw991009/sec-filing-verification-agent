"""Fenced PostgreSQL persistence for resumable Knowledge ingestion."""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import Select, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from industry_platform.core.database import AsyncSessionFactory, safe_sqlstate
from industry_platform.modules.files.domain import (
    AttachmentMediaType,
    FileObjectPurpose,
    FileObjectStatus,
)
from industry_platform.modules.files.models import FileObject
from industry_platform.modules.ingestion.domain import (
    INGESTION_STAGE_SEQUENCE,
    CompleteIngestionStage,
    EmbeddingInput,
    IndexableChunk,
    IngestionCancelledError,
    IngestionConflictError,
    IngestionNotFoundError,
    IngestionPersistenceError,
    IngestionStage,
    IngestionWorkItem,
    StoredStageCheckpoint,
)
from industry_platform.modules.ingestion.index_contract import (
    EMBEDDING_DIMENSION,
    EMBEDDING_MODEL,
    EMBEDDING_PROVIDER_NAME,
    EMBEDDING_VERSION,
    INDEX_VERSION,
    embedding_config_snapshot,
    index_config_snapshot,
)
from industry_platform.modules.jobs.domain import JobLeaseProof, JobStatus, LostJobLeaseError
from industry_platform.modules.jobs.models import Job
from industry_platform.modules.knowledge.domain import (
    DocumentAssetKind,
    DocumentIndexKind,
    DocumentIndexStatus,
    DocumentPageTextSource,
    DocumentVersionStatus,
    IngestionCheckpointStage,
)
from industry_platform.modules.knowledge.models import (
    ChunkAssetLinkRecord,
    ChunkEmbeddingRecord,
    DocumentAssetRecord,
    DocumentChunkRecord,
    DocumentIndexRecord,
    DocumentPageRecord,
    DocumentRecord,
    DocumentVersionRecord,
    IngestionCheckpointRecord,
)
from industry_platform.modules.knowledge.parser_contract import (
    CHUNKER_NAME,
    CHUNKER_VERSION,
    PARSER_NAME,
    PARSER_SCHEMA_VERSION,
    PARSER_VERSION,
)


def _live_job(proof: JobLeaseProof) -> Select[tuple[Job]]:
    return (
        select(Job)
        .where(
            Job.id == proof.job_id,
            Job.status == JobStatus.RUNNING,
            Job.lease_owner == proof.owner,
            Job.lease_token == proof.lease_token,
            Job.fencing_token == proof.fencing_token,
            Job.lease_expires_at.is_not(None),
            Job.lease_expires_at > func.clock_timestamp(),
        )
        .with_for_update()
    )


def _deterministic_id(version_id: UUID, kind: str, ordinal: int) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"industry-platform:knowledge:{version_id}:{kind}:{ordinal}",
    )


def _stage_idempotency_hash(command: CompleteIngestionStage) -> bytes:
    return hashlib.sha256(
        b"industry-platform:ingestion-stage:v1\x00"
        + command.work_item.document_version_id.bytes
        + command.stage.value.encode("ascii")
        + bytes.fromhex(command.input_hash)
    ).digest()


def _persistence_error(error: SQLAlchemyError) -> IngestionPersistenceError:
    original = getattr(error, "orig", None)
    diagnostic = getattr(original, "diag", None)
    constraint_name = getattr(diagnostic, "constraint_name", None)
    return IngestionPersistenceError(
        sqlstate=safe_sqlstate(error),
        constraint_name=constraint_name if isinstance(constraint_name, str) else None,
    )


def _page_source(value: str) -> DocumentPageTextSource:
    return DocumentPageTextSource(value)


def _asset_kind(value: str) -> DocumentAssetKind:
    return DocumentAssetKind(value)


_NEXT_STATUS = {
    IngestionStage.VALIDATING: DocumentVersionStatus.PARSING,
    IngestionStage.PARSING: DocumentVersionStatus.EXTRACTING_ASSETS,
    IngestionStage.EXTRACTING_ASSETS: DocumentVersionStatus.CHUNKING,
    IngestionStage.CHUNKING: DocumentVersionStatus.PARSED,
    IngestionStage.EMBEDDING: DocumentVersionStatus.VECTOR_INDEXING,
    IngestionStage.VECTOR_INDEXING: DocumentVersionStatus.LEXICAL_INDEXING,
    IngestionStage.LEXICAL_INDEXING: DocumentVersionStatus.READY,
}


@dataclass(frozen=True, slots=True)
class SqlAlchemyIngestionRepository:
    session_factory: AsyncSessionFactory

    async def load_work_item(
        self,
        proof: JobLeaseProof,
        *,
        document_version_id: UUID,
        file_id: UUID,
    ) -> IngestionWorkItem:
        try:
            async with self.session_factory() as session, session.begin():
                job = await session.scalar(_live_job(proof))
                if job is None:
                    raise LostJobLeaseError
                if job.cancel_requested_at is not None:
                    raise IngestionCancelledError
                row = (
                    await session.execute(
                        select(DocumentVersionRecord, FileObject)
                        .join(
                            FileObject,
                            FileObject.id == DocumentVersionRecord.file_object_id,
                        )
                        .where(
                            DocumentVersionRecord.id == document_version_id,
                            DocumentVersionRecord.ingestion_job_id == proof.job_id,
                            DocumentVersionRecord.file_object_id == file_id,
                            DocumentVersionRecord.workspace_id == job.workspace_id,
                            FileObject.workspace_id == job.workspace_id,
                            FileObject.purpose == FileObjectPurpose.KNOWLEDGE_SOURCE,
                            FileObject.status == FileObjectStatus.READY,
                        )
                    )
                ).one_or_none()
                if row is None:
                    raise IngestionNotFoundError
                version, source = row
                if (
                    source.object_key is None
                    or source.actual_size is None
                    or source.safe_sha256 is None
                    or source.detected_media_type is None
                ):
                    raise IngestionPersistenceError
                checkpoints = tuple(
                    _checkpoint(record)
                    for record in (
                        await session.scalars(
                            select(IngestionCheckpointRecord)
                            .where(
                                IngestionCheckpointRecord.document_version_id == document_version_id
                            )
                            .order_by(IngestionCheckpointRecord.stage_sequence)
                        )
                    ).all()
                )
                return IngestionWorkItem(
                    workspace_id=version.workspace_id,
                    document_id=version.document_id,
                    document_version_id=version.id,
                    ingestion_job_id=version.ingestion_job_id,
                    file_id=version.file_object_id,
                    original_name=source.original_name,
                    media_type=AttachmentMediaType(source.detected_media_type),
                    source_bucket=source.bucket,
                    source_object_key=source.object_key,
                    source_size=source.actual_size,
                    source_sha256=source.safe_sha256,
                    parser_name=version.parser_name,
                    parser_version=version.parser_version,
                    parser_schema_version=version.parser_schema_version,
                    parser_config=version.parser_config,
                    chunker_name=version.chunker_name,
                    chunker_version=version.chunker_version,
                    chunker_config=version.chunker_config,
                    embedding_config=version.embedding_config,
                    index_config=version.index_config,
                    checkpoints=checkpoints,
                )
        except (
            IngestionCancelledError,
            IngestionNotFoundError,
            IngestionPersistenceError,
            LostJobLeaseError,
        ):
            raise
        except SQLAlchemyError as error:
            raise _persistence_error(error) from None

    async def begin_stage(
        self,
        proof: JobLeaseProof,
        *,
        document_version_id: UUID,
        stage: IngestionStage,
    ) -> bool:
        try:
            async with self.session_factory() as session, session.begin():
                job = await session.scalar(_live_job(proof))
                if job is None:
                    raise LostJobLeaseError
                if job.cancel_requested_at is not None:
                    raise IngestionCancelledError
                version = await session.scalar(
                    select(DocumentVersionRecord)
                    .where(
                        DocumentVersionRecord.id == document_version_id,
                        DocumentVersionRecord.ingestion_job_id == proof.job_id,
                        DocumentVersionRecord.workspace_id == job.workspace_id,
                    )
                    .with_for_update()
                )
                if version is None:
                    raise IngestionNotFoundError
                existing = await session.scalar(
                    select(IngestionCheckpointRecord.id).where(
                        IngestionCheckpointRecord.document_version_id == document_version_id,
                        IngestionCheckpointRecord.stage == IngestionCheckpointStage(stage.value),
                    )
                )
                if existing is not None:
                    return False
                required_sequence = INGESTION_STAGE_SEQUENCE[stage] - 1
                if required_sequence > 0:
                    completed = await session.scalar(
                        select(func.count(IngestionCheckpointRecord.id)).where(
                            IngestionCheckpointRecord.document_version_id == document_version_id,
                            IngestionCheckpointRecord.stage_sequence <= required_sequence,
                        )
                    )
                    if completed != required_sequence:
                        raise IngestionConflictError
                version.status = DocumentVersionStatus(stage.value)
                version.error_code = None
                version.processing_started_at = (
                    version.processing_started_at or await _database_now(session)
                )
                version.revision += 1
                return True
        except (
            IngestionCancelledError,
            IngestionConflictError,
            IngestionNotFoundError,
            LostJobLeaseError,
        ):
            raise
        except SQLAlchemyError as error:
            raise _persistence_error(error) from None

    async def complete_stage(self, command: CompleteIngestionStage) -> bool:
        try:
            async with self.session_factory() as session, session.begin():
                job = await session.scalar(_live_job(command.proof))
                if job is None:
                    raise LostJobLeaseError
                if job.cancel_requested_at is not None:
                    raise IngestionCancelledError
                version = await session.scalar(
                    select(DocumentVersionRecord)
                    .where(
                        DocumentVersionRecord.id == command.work_item.document_version_id,
                        DocumentVersionRecord.ingestion_job_id == command.proof.job_id,
                        DocumentVersionRecord.workspace_id == job.workspace_id,
                    )
                    .with_for_update()
                )
                if version is None:
                    raise IngestionNotFoundError
                existing = await session.scalar(
                    select(IngestionCheckpointRecord).where(
                        IngestionCheckpointRecord.document_version_id == version.id,
                        IngestionCheckpointRecord.stage
                        == IngestionCheckpointStage(command.stage.value),
                    )
                )
                if existing is not None:
                    if existing.input_hash == bytes.fromhex(
                        command.input_hash
                    ) and existing.output_hash == bytes.fromhex(command.output_hash):
                        return False
                    raise IngestionConflictError

                required_sequence = INGESTION_STAGE_SEQUENCE[command.stage] - 1
                if required_sequence > 0:
                    completed = await session.scalar(
                        select(func.count(IngestionCheckpointRecord.id)).where(
                            IngestionCheckpointRecord.document_version_id == version.id,
                            IngestionCheckpointRecord.stage_sequence <= required_sequence,
                        )
                    )
                    if completed != required_sequence:
                        raise IngestionConflictError

                if command.stage is IngestionStage.EXTRACTING_ASSETS:
                    await self._persist_assets(session, command)
                elif command.stage is IngestionStage.CHUNKING:
                    await self._persist_chunks(session, command)
                elif command.stage is IngestionStage.EMBEDDING:
                    await self._persist_embeddings(session, command)
                elif command.stage in {
                    IngestionStage.VECTOR_INDEXING,
                    IngestionStage.LEXICAL_INDEXING,
                }:
                    await self._persist_index_results(session, command)

                completed_at = await _database_now(session)
                session.add(
                    IngestionCheckpointRecord(
                        id=uuid4(),
                        workspace_id=version.workspace_id,
                        document_id=version.document_id,
                        document_version_id=version.id,
                        ingestion_job_id=version.ingestion_job_id,
                        stage=IngestionCheckpointStage(command.stage.value),
                        stage_sequence=INGESTION_STAGE_SEQUENCE[command.stage],
                        fencing_token=command.proof.fencing_token,
                        attempt_count=command.attempt_count,
                        stage_idempotency_hash=_stage_idempotency_hash(command),
                        input_hash=bytes.fromhex(command.input_hash),
                        output_hash=bytes.fromhex(command.output_hash),
                        output_bucket=command.output_bucket,
                        output_object_key=command.output_object_key,
                        stats=dict(command.stats),
                        completed_at=completed_at,
                    )
                )
                version.status = _NEXT_STATUS[command.stage]
                version.error_code = None
                version.revision += 1
                if command.stage is IngestionStage.LEXICAL_INDEXING:
                    await self._mark_ready(session, version, completed_at=completed_at)
                return True
        except (
            IngestionCancelledError,
            IngestionConflictError,
            IngestionNotFoundError,
            LostJobLeaseError,
        ):
            raise
        except SQLAlchemyError as error:
            raise _persistence_error(error) from None

    async def _persist_assets(self, session: AsyncSession, command: CompleteIngestionStage) -> None:
        document = command.parsed_document
        if document is None:
            raise IngestionConflictError
        page_ids: dict[int, UUID] = {}
        for page in document.pages:
            page_id = _deterministic_id(
                command.work_item.document_version_id, "page", page.page_number
            )
            page_ids[page.page_number] = page_id
            session.add(
                DocumentPageRecord(
                    id=page_id,
                    workspace_id=command.work_item.workspace_id,
                    document_id=command.work_item.document_id,
                    document_version_id=command.work_item.document_version_id,
                    page_number=page.page_number,
                    width_points=page.width_points,
                    height_points=page.height_points,
                    text_content=page.text,
                    text_source=_page_source(page.text_source.value),
                    bbox=page.bbox.snapshot(),
                    title_path=list(page.title_path),
                    content_hash=bytes.fromhex(page.content_hash),
                    parser_version=PARSER_VERSION,
                )
            )
        await session.flush()
        previews = {item.ordinal: item for item in command.asset_previews}
        for asset in document.assets:
            preview = previews[asset.ordinal]
            session.add(
                DocumentAssetRecord(
                    id=_deterministic_id(
                        command.work_item.document_version_id,
                        "asset",
                        asset.ordinal,
                    ),
                    workspace_id=command.work_item.workspace_id,
                    document_id=command.work_item.document_id,
                    document_version_id=command.work_item.document_version_id,
                    page_id=page_ids[asset.page_number],
                    ordinal=asset.ordinal,
                    page_number=asset.page_number,
                    kind=_asset_kind(asset.kind.value),
                    bbox=asset.bbox.snapshot(),
                    title_path=list(asset.title_path),
                    content_hash=bytes.fromhex(asset.content_hash),
                    preview_sha256=bytes.fromhex(asset.preview_sha256),
                    preview_mime_type=asset.preview_mime_type,
                    preview_bucket=preview.bucket,
                    preview_object_key=preview.object_key,
                    html_content=asset.html,
                    parser_version=PARSER_VERSION,
                )
            )

    async def _persist_chunks(self, session: AsyncSession, command: CompleteIngestionStage) -> None:
        chunk_ids: dict[int, UUID] = {}
        for chunk in command.chunks:
            chunk_id = _deterministic_id(
                command.work_item.document_version_id,
                "chunk",
                chunk.ordinal,
            )
            chunk_ids[chunk.ordinal] = chunk_id
            session.add(
                DocumentChunkRecord(
                    id=chunk_id,
                    workspace_id=command.work_item.workspace_id,
                    document_id=command.work_item.document_id,
                    document_version_id=command.work_item.document_version_id,
                    ordinal=chunk.ordinal,
                    page_number=chunk.page_number,
                    text_content=chunk.text,
                    token_count=chunk.token_count,
                    bbox=chunk.bbox.snapshot(),
                    title_path=list(chunk.title_path),
                    content_hash=bytes.fromhex(chunk.content_hash),
                    chunker_version=CHUNKER_VERSION,
                )
            )
        await session.flush()
        for chunk in command.chunks:
            chunk_id = chunk_ids[chunk.ordinal]
            for asset_ordinal in chunk.asset_ordinals:
                asset_id = _deterministic_id(
                    command.work_item.document_version_id,
                    "asset",
                    asset_ordinal,
                )
                session.add(
                    ChunkAssetLinkRecord(
                        id=_deterministic_id(
                            command.work_item.document_version_id,
                            f"chunk-{chunk.ordinal}-asset",
                            asset_ordinal,
                        ),
                        workspace_id=command.work_item.workspace_id,
                        document_version_id=command.work_item.document_version_id,
                        chunk_id=chunk_id,
                        asset_id=asset_id,
                    )
                )

    async def load_embedding_inputs(
        self,
        proof: JobLeaseProof,
        *,
        document_version_id: UUID,
    ) -> tuple[EmbeddingInput, ...]:
        try:
            async with self.session_factory() as session, session.begin():
                job = await session.scalar(_live_job(proof))
                if job is None:
                    raise LostJobLeaseError
                if job.cancel_requested_at is not None:
                    raise IngestionCancelledError
                rows = (
                    await session.scalars(
                        select(DocumentChunkRecord)
                        .join(
                            DocumentVersionRecord,
                            DocumentVersionRecord.id == DocumentChunkRecord.document_version_id,
                        )
                        .where(
                            DocumentChunkRecord.document_version_id == document_version_id,
                            DocumentChunkRecord.workspace_id == job.workspace_id,
                            DocumentVersionRecord.ingestion_job_id == proof.job_id,
                        )
                        .order_by(DocumentChunkRecord.ordinal)
                    )
                ).all()
                if not rows:
                    raise IngestionConflictError
                return tuple(
                    EmbeddingInput(
                        chunk_id=row.id,
                        text=row.text_content,
                        content_hash=row.content_hash.hex(),
                    )
                    for row in rows
                )
        except (
            IngestionCancelledError,
            IngestionConflictError,
            LostJobLeaseError,
        ):
            raise
        except SQLAlchemyError as error:
            raise _persistence_error(error) from None

    async def load_indexable_chunks(
        self,
        proof: JobLeaseProof,
        *,
        document_version_id: UUID,
    ) -> tuple[IndexableChunk, ...]:
        try:
            async with self.session_factory() as session, session.begin():
                job = await session.scalar(_live_job(proof))
                if job is None:
                    raise LostJobLeaseError
                if job.cancel_requested_at is not None:
                    raise IngestionCancelledError
                version = await session.scalar(
                    select(DocumentVersionRecord).where(
                        DocumentVersionRecord.id == document_version_id,
                        DocumentVersionRecord.workspace_id == job.workspace_id,
                        DocumentVersionRecord.ingestion_job_id == proof.job_id,
                    )
                )
                if version is None:
                    raise IngestionConflictError
                rows = (
                    await session.execute(
                        select(DocumentChunkRecord, ChunkEmbeddingRecord)
                        .join(
                            ChunkEmbeddingRecord,
                            ChunkEmbeddingRecord.chunk_id == DocumentChunkRecord.id,
                        )
                        .join(
                            DocumentVersionRecord,
                            DocumentVersionRecord.id == DocumentChunkRecord.document_version_id,
                        )
                        .where(
                            DocumentChunkRecord.document_version_id == document_version_id,
                            DocumentChunkRecord.workspace_id == job.workspace_id,
                            DocumentVersionRecord.ingestion_job_id == proof.job_id,
                        )
                        .order_by(DocumentChunkRecord.ordinal)
                    )
                ).all()
                if not rows:
                    raise IngestionConflictError
                chunk_count = await session.scalar(
                    select(func.count(DocumentChunkRecord.id)).where(
                        DocumentChunkRecord.document_version_id == document_version_id
                    )
                )
                if chunk_count != len(rows):
                    raise IngestionConflictError
                return tuple(
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
        except (
            IngestionCancelledError,
            IngestionConflictError,
            LostJobLeaseError,
        ):
            raise
        except SQLAlchemyError as error:
            raise _persistence_error(error) from None

    async def _persist_embeddings(
        self,
        session: AsyncSession,
        command: CompleteIngestionStage,
    ) -> None:
        chunks = {
            row.id: row
            for row in (
                await session.scalars(
                    select(DocumentChunkRecord).where(
                        DocumentChunkRecord.document_version_id
                        == command.work_item.document_version_id
                    )
                )
            ).all()
        }
        if set(chunks) != {embedding.chunk_id for embedding in command.embeddings}:
            raise IngestionConflictError
        for embedding in command.embeddings:
            chunk = chunks[embedding.chunk_id]
            if (
                embedding.content_hash != chunk.content_hash.hex()
                or len(embedding.vector) != EMBEDDING_DIMENSION
            ):
                raise IngestionConflictError
            session.add(
                ChunkEmbeddingRecord(
                    id=_deterministic_id(
                        command.work_item.document_version_id,
                        "embedding",
                        chunk.ordinal,
                    ),
                    workspace_id=chunk.workspace_id,
                    document_id=chunk.document_id,
                    document_version_id=chunk.document_version_id,
                    chunk_id=chunk.id,
                    provider=EMBEDDING_PROVIDER_NAME,
                    model=EMBEDDING_MODEL,
                    embedding_version=EMBEDDING_VERSION,
                    dimension=EMBEDDING_DIMENSION,
                    normalized=True,
                    content_hash=chunk.content_hash,
                    vector=list(embedding.vector),
                )
            )

    async def _persist_index_results(
        self,
        session: AsyncSession,
        command: CompleteIngestionStage,
    ) -> None:
        chunks = (
            await session.scalars(
                select(DocumentChunkRecord)
                .where(
                    DocumentChunkRecord.document_version_id == command.work_item.document_version_id
                )
                .order_by(DocumentChunkRecord.ordinal)
            )
        ).all()
        expected_ids = tuple(f"{chunk.id}:{INDEX_VERSION}" for chunk in chunks)
        if command.external_ids != expected_ids:
            raise IngestionConflictError
        kind = (
            DocumentIndexKind.VECTOR
            if command.stage is IngestionStage.VECTOR_INDEXING
            else DocumentIndexKind.LEXICAL
        )
        indexed_at = await _database_now(session)
        for chunk, external_id in zip(chunks, expected_ids, strict=True):
            existing = await session.scalar(
                select(DocumentIndexRecord)
                .where(
                    DocumentIndexRecord.document_version_id == chunk.document_version_id,
                    DocumentIndexRecord.chunk_id == chunk.id,
                    DocumentIndexRecord.kind == kind,
                    DocumentIndexRecord.index_version == INDEX_VERSION,
                )
                .with_for_update()
            )
            if existing is None:
                session.add(
                    DocumentIndexRecord(
                        id=_deterministic_id(
                            command.work_item.document_version_id,
                            f"{kind.value}-index",
                            chunk.ordinal,
                        ),
                        workspace_id=chunk.workspace_id,
                        document_id=chunk.document_id,
                        document_version_id=chunk.document_version_id,
                        chunk_id=chunk.id,
                        kind=kind,
                        status=DocumentIndexStatus.SUCCEEDED,
                        index_version=INDEX_VERSION,
                        external_id=external_id,
                        attempt_count=command.attempt_count,
                        error_code=None,
                        indexed_at=indexed_at,
                    )
                )
            else:
                existing.status = DocumentIndexStatus.SUCCEEDED
                existing.attempt_count = command.attempt_count
                existing.error_code = None
                existing.indexed_at = indexed_at
        await session.flush()

    async def _mark_ready(
        self,
        session: AsyncSession,
        version: DocumentVersionRecord,
        *,
        completed_at: datetime,
    ) -> None:
        chunk_count = await session.scalar(
            select(func.count(DocumentChunkRecord.id)).where(
                DocumentChunkRecord.document_version_id == version.id
            )
        )
        counts = {
            kind: await session.scalar(
                select(func.count(DocumentIndexRecord.id)).where(
                    DocumentIndexRecord.document_version_id == version.id,
                    DocumentIndexRecord.kind == kind,
                    DocumentIndexRecord.status == DocumentIndexStatus.SUCCEEDED,
                    DocumentIndexRecord.index_version == INDEX_VERSION,
                )
            )
            for kind in (DocumentIndexKind.VECTOR, DocumentIndexKind.LEXICAL)
        }
        if not chunk_count or any(count != chunk_count for count in counts.values()):
            raise IngestionConflictError
        document = await session.scalar(
            select(DocumentRecord)
            .where(
                DocumentRecord.id == version.document_id,
                DocumentRecord.workspace_id == version.workspace_id,
            )
            .with_for_update()
        )
        if document is None:
            raise IngestionNotFoundError
        version.ready_at = completed_at
        if version.version == document.latest_version_number:
            document.active_version_id = version.id
            document.revision += 1

    async def mark_retrying(
        self,
        proof: JobLeaseProof,
        *,
        document_version_id: UUID,
        error_code: str,
    ) -> None:
        await self._mark_failure(
            proof,
            document_version_id=document_version_id,
            status=DocumentVersionStatus.RETRYING,
            error_code=error_code,
        )

    async def mark_terminal_failure(
        self,
        proof: JobLeaseProof,
        *,
        document_version_id: UUID,
        error_code: str,
        cancelled: bool,
    ) -> None:
        await self._mark_failure(
            proof,
            document_version_id=document_version_id,
            status=(DocumentVersionStatus.CANCELLED if cancelled else DocumentVersionStatus.FAILED),
            error_code=error_code,
        )

    async def _mark_failure(
        self,
        proof: JobLeaseProof,
        *,
        document_version_id: UUID,
        status: DocumentVersionStatus,
        error_code: str,
    ) -> None:
        try:
            async with self.session_factory() as session, session.begin():
                job = await session.scalar(_live_job(proof))
                if job is None:
                    raise LostJobLeaseError
                version = await session.scalar(
                    select(DocumentVersionRecord)
                    .where(
                        DocumentVersionRecord.id == document_version_id,
                        DocumentVersionRecord.ingestion_job_id == proof.job_id,
                        DocumentVersionRecord.workspace_id == job.workspace_id,
                    )
                    .with_for_update()
                )
                if version is None:
                    raise IngestionNotFoundError
                failed_stage = version.status
                if failed_stage in {
                    DocumentVersionStatus.VECTOR_INDEXING,
                    DocumentVersionStatus.LEXICAL_INDEXING,
                }:
                    await self._record_index_failures(
                        session,
                        version,
                        kind=(
                            DocumentIndexKind.VECTOR
                            if failed_stage is DocumentVersionStatus.VECTOR_INDEXING
                            else DocumentIndexKind.LEXICAL
                        ),
                        attempt_count=job.attempt_count,
                        error_code=error_code,
                    )
                version.status = status
                version.error_code = error_code
                version.revision += 1
        except (IngestionNotFoundError, LostJobLeaseError):
            raise
        except SQLAlchemyError as error:
            raise _persistence_error(error) from None

    async def _record_index_failures(
        self,
        session: AsyncSession,
        version: DocumentVersionRecord,
        *,
        kind: DocumentIndexKind,
        attempt_count: int,
        error_code: str,
    ) -> None:
        chunks = (
            await session.scalars(
                select(DocumentChunkRecord)
                .where(DocumentChunkRecord.document_version_id == version.id)
                .order_by(DocumentChunkRecord.ordinal)
            )
        ).all()
        for chunk in chunks:
            existing = await session.scalar(
                select(DocumentIndexRecord)
                .where(
                    DocumentIndexRecord.document_version_id == version.id,
                    DocumentIndexRecord.chunk_id == chunk.id,
                    DocumentIndexRecord.kind == kind,
                    DocumentIndexRecord.index_version == INDEX_VERSION,
                )
                .with_for_update()
            )
            if existing is None:
                session.add(
                    DocumentIndexRecord(
                        id=_deterministic_id(
                            version.id,
                            f"{kind.value}-index",
                            chunk.ordinal,
                        ),
                        workspace_id=version.workspace_id,
                        document_id=version.document_id,
                        document_version_id=version.id,
                        chunk_id=chunk.id,
                        kind=kind,
                        status=DocumentIndexStatus.FAILED,
                        index_version=INDEX_VERSION,
                        external_id=f"{chunk.id}:{INDEX_VERSION}",
                        attempt_count=max(1, attempt_count),
                        error_code=error_code,
                        indexed_at=None,
                    )
                )
            else:
                existing.status = DocumentIndexStatus.FAILED
                existing.attempt_count = max(1, attempt_count)
                existing.error_code = error_code
                existing.indexed_at = None


async def _database_now(session: AsyncSession) -> datetime:
    value = await session.scalar(select(func.clock_timestamp()))
    if not isinstance(value, datetime):
        raise IngestionPersistenceError
    return value


def _checkpoint(record: IngestionCheckpointRecord) -> StoredStageCheckpoint:
    return StoredStageCheckpoint(
        stage=IngestionStage(record.stage.value),
        stage_sequence=record.stage_sequence,
        input_hash=record.input_hash.hex(),
        output_hash=record.output_hash.hex(),
        output_bucket=record.output_bucket,
        output_object_key=record.output_object_key,
    )


def validate_work_item_contract(work: IngestionWorkItem) -> None:
    if (
        work.parser_name != PARSER_NAME
        or work.parser_version != PARSER_VERSION
        or work.parser_schema_version != PARSER_SCHEMA_VERSION
        or work.chunker_name != CHUNKER_NAME
        or work.chunker_version != CHUNKER_VERSION
        or dict(work.embedding_config) != embedding_config_snapshot()
        or dict(work.index_config) != index_config_snapshot()
    ):
        raise IngestionConflictError
