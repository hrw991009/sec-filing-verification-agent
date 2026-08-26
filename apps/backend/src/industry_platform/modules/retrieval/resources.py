"""Composition root for the fixed SEC Dense retrieval and calculation Tools."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx2

from industry_platform.core.config import Settings
from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.financial_verification.tool import FinanceCalculateTool
from industry_platform.modules.ingestion.index_contract import MILVUS_COLLECTION
from industry_platform.modules.retrieval.adapters.milvus import MilvusDenseIndex
from industry_platform.modules.retrieval.adapters.sqlalchemy import (
    SqlAlchemyKnowledgeCandidateRepository,
)
from industry_platform.modules.retrieval.fixtures import (
    SecFixtureCatalog,
    load_sec_fixture_catalog,
)
from industry_platform.modules.retrieval.service import KnowledgeSearchService
from industry_platform.modules.retrieval.tool import KnowledgeSearchTool


@dataclass(frozen=True, slots=True)
class RetrievalResources:
    catalog: SecFixtureCatalog
    knowledge_search_tool: KnowledgeSearchTool
    finance_calculate_tool: FinanceCalculateTool


def create_retrieval_resources(
    settings: Settings,
    session_factory: AsyncSessionFactory,
    internal_http_client: httpx2.AsyncClient,
) -> RetrievalResources:
    configured = settings.sec_fixture_manifest_path
    source_repository_root = Path(__file__).resolve().parents[6]
    candidate = configured if configured.is_absolute() else Path.cwd() / configured
    if not candidate.exists() and not configured.is_absolute():
        candidate = source_repository_root / configured
    manifest = candidate.resolve(strict=True)
    catalog = load_sec_fixture_catalog(manifest, repository_root=source_repository_root)
    repository = SqlAlchemyKnowledgeCandidateRepository(session_factory)
    service = KnowledgeSearchService(
        catalog=catalog,
        repository=repository,
        dense_index=MilvusDenseIndex(
            client=internal_http_client,
            endpoint=settings.milvus_endpoint,
            token=(
                None if settings.milvus_token is None else settings.milvus_token.get_secret_value()
            ),
            collection=MILVUS_COLLECTION,
            timeout_seconds=settings.knowledge_index_timeout_seconds,
        ),
    )
    return RetrievalResources(
        catalog=catalog,
        knowledge_search_tool=KnowledgeSearchTool(service),
        finance_calculate_tool=FinanceCalculateTool(repository, catalog),
    )
