"""Worker composition root for Knowledge ingestion."""

from dataclasses import dataclass

from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.files.ports import PrivateFileObjectStore
from industry_platform.modules.ingestion.adapters.document_parser import (
    PdfPlumberRapidOcrDocumentParser,
)
from industry_platform.modules.ingestion.adapters.sqlalchemy import (
    SqlAlchemyIngestionRepository,
)
from industry_platform.modules.ingestion.chunker import BoundedPageChunker
from industry_platform.modules.ingestion.service import KnowledgeIngestionService
from industry_platform.modules.jobs.ports import JobApplicationUseCase


@dataclass(frozen=True, slots=True)
class IngestionResources:
    service: KnowledgeIngestionService


def create_ingestion_resources(
    session_factory: AsyncSessionFactory,
    jobs: JobApplicationUseCase,
    object_store: PrivateFileObjectStore | None,
) -> IngestionResources:
    return IngestionResources(
        service=KnowledgeIngestionService(
            repository=SqlAlchemyIngestionRepository(session_factory),
            jobs=jobs,
            parser=PdfPlumberRapidOcrDocumentParser(),
            chunker=BoundedPageChunker(),
            object_store=object_store,
        )
    )
