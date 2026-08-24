"""Ports for parser adapters and fenced ingestion persistence."""

from typing import Protocol
from uuid import UUID

from industry_platform.modules.ingestion.domain import (
    CompleteIngestionStage,
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
