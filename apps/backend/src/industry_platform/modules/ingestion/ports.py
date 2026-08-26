"""Ports for parser adapters and fenced ingestion persistence."""

from typing import Protocol
from uuid import UUID

from industry_platform.modules.ingestion.domain import (
    ChunkEmbedding,
    CompleteIngestionStage,
    EmbeddingInput,
    IndexableChunk,
    IngestionStage,
    IngestionWorkItem,
    ParsedChunk,
    ParsedDocument,
    ParserRequest,
)
from industry_platform.modules.jobs.domain import JobLeaseProof


class DocumentParser(Protocol):
    async def parse(self, request: ParserRequest) -> ParsedDocument: ...


class DocumentChunker(Protocol):
    def chunk(self, document: ParsedDocument) -> tuple[ParsedChunk, ...]: ...


class EmbeddingProvider(Protocol):
    async def embed(self, inputs: tuple[EmbeddingInput, ...]) -> tuple[ChunkEmbedding, ...]: ...


class VectorIndexWriter(Protocol):
    async def upsert(self, chunks: tuple[IndexableChunk, ...]) -> tuple[str, ...]: ...

    async def delete(self, external_ids: tuple[str, ...]) -> None: ...


class LexicalIndexWriter(Protocol):
    async def upsert(self, chunks: tuple[IndexableChunk, ...]) -> tuple[str, ...]: ...

    async def delete(self, external_ids: tuple[str, ...]) -> None: ...


class IngestionRepository(Protocol):
    async def load_work_item(
        self,
        proof: JobLeaseProof,
        *,
        document_version_id: UUID,
        file_id: UUID,
    ) -> IngestionWorkItem: ...

    async def begin_stage(
        self,
        proof: JobLeaseProof,
        *,
        document_version_id: UUID,
        stage: IngestionStage,
    ) -> bool: ...

    async def complete_stage(self, command: CompleteIngestionStage) -> bool: ...

    async def load_embedding_inputs(
        self,
        proof: JobLeaseProof,
        *,
        document_version_id: UUID,
    ) -> tuple[EmbeddingInput, ...]: ...

    async def load_indexable_chunks(
        self,
        proof: JobLeaseProof,
        *,
        document_version_id: UUID,
    ) -> tuple[IndexableChunk, ...]: ...

    async def mark_retrying(
        self,
        proof: JobLeaseProof,
        *,
        document_version_id: UUID,
        error_code: str,
    ) -> None: ...

    async def mark_terminal_failure(
        self,
        proof: JobLeaseProof,
        *,
        document_version_id: UUID,
        error_code: str,
        cancelled: bool,
    ) -> None: ...
