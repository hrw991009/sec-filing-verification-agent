"""Worker composition root for Knowledge ingestion."""

from dataclasses import dataclass

import httpx2

from industry_platform.core.config import Settings
from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.files.ports import PrivateFileObjectStore
from industry_platform.modules.ingestion.adapters.deletion_sqlalchemy import (
    SqlAlchemyDeletionRepository,
)
from industry_platform.modules.ingestion.adapters.document_parser import (
    PdfPlumberRapidOcrDocumentParser,
)
from industry_platform.modules.ingestion.adapters.embedding import (
    DeterministicHashEmbeddingProvider,
)
from industry_platform.modules.ingestion.adapters.indexes import (
    ElasticsearchLexicalIndexWriter,
    MilvusVectorIndexWriter,
)
from industry_platform.modules.ingestion.adapters.sqlalchemy import (
    SqlAlchemyIngestionRepository,
)
from industry_platform.modules.ingestion.chunker import BoundedPageChunker
from industry_platform.modules.ingestion.deletion import KnowledgeDeletionService
from industry_platform.modules.ingestion.index_contract import (
    ELASTICSEARCH_INDEX,
    EMBEDDING_DIMENSION,
    MILVUS_COLLECTION,
)
from industry_platform.modules.ingestion.service import KnowledgeIngestionService
from industry_platform.modules.jobs.ports import JobApplicationUseCase


@dataclass(frozen=True, slots=True)
class IngestionResources:
    service: KnowledgeIngestionService
    deletion_service: KnowledgeDeletionService


def create_ingestion_resources(
    settings: Settings,
    session_factory: AsyncSessionFactory,
    jobs: JobApplicationUseCase,
    object_store: PrivateFileObjectStore | None,
    internal_http_client: httpx2.AsyncClient,
) -> IngestionResources:
    vector_index = MilvusVectorIndexWriter(
        client=internal_http_client,
        endpoint=settings.milvus_endpoint,
        token=(None if settings.milvus_token is None else settings.milvus_token.get_secret_value()),
        collection=MILVUS_COLLECTION,
        dimension=EMBEDDING_DIMENSION,
        timeout_seconds=settings.knowledge_index_timeout_seconds,
    )
    lexical_index = ElasticsearchLexicalIndexWriter(
        client=internal_http_client,
        endpoint=settings.elasticsearch_endpoint,
        api_key=(
            None
            if settings.elasticsearch_api_key is None
            else settings.elasticsearch_api_key.get_secret_value()
        ),
        index=ELASTICSEARCH_INDEX,
        timeout_seconds=settings.knowledge_index_timeout_seconds,
    )
    return IngestionResources(
        service=KnowledgeIngestionService(
            repository=SqlAlchemyIngestionRepository(session_factory),
            jobs=jobs,
            parser=PdfPlumberRapidOcrDocumentParser(),
            chunker=BoundedPageChunker(),
            embedding_provider=DeterministicHashEmbeddingProvider(),
            vector_index=vector_index,
            lexical_index=lexical_index,
            object_store=object_store,
        ),
        deletion_service=KnowledgeDeletionService(
            repository=SqlAlchemyDeletionRepository(session_factory),
            vector_index=vector_index,
            lexical_index=lexical_index,
            object_store=object_store,
        ),
    )
