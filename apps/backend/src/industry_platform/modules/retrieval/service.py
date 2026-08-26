"""Application service for the unique Day 5 Dense Knowledge baseline."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from industry_platform.modules.financial_verification.domain import FinancialScope
from industry_platform.modules.ingestion.adapters.embedding import embed_query_text
from industry_platform.modules.retrieval.domain import (
    KNOWLEDGE_SEARCH_TOP_K,
    KnowledgeSearchResult,
    KnowledgeSearchStatus,
)
from industry_platform.modules.retrieval.fixtures import SecFixtureCatalog
from industry_platform.modules.retrieval.ports import (
    DenseIndexPort,
    DenseSearchDependencyError,
    KnowledgeCandidateRepository,
    KnowledgeSearchDependencyError,
)
from industry_platform.modules.workspaces.domain import WorkspaceScope


@dataclass(frozen=True, slots=True)
class KnowledgeSearchService:
    catalog: SecFixtureCatalog
    repository: KnowledgeCandidateRepository
    dense_index: DenseIndexPort

    async def search(
        self,
        scope: WorkspaceScope,
        *,
        knowledge_base_ids: tuple[UUID, ...],
        financial_scope: FinancialScope,
        query: str,
    ) -> KnowledgeSearchResult:
        fixture = self.catalog.select(financial_scope)
        preparation = await self.repository.prepare(
            scope,
            knowledge_base_ids=knowledge_base_ids,
            financial_scope=financial_scope,
            fixture=fixture,
        )
        if preparation.status is not KnowledgeSearchStatus.OK:
            return KnowledgeSearchResult(status=preparation.status)
        try:
            candidates = await self.dense_index.search(
                embed_query_text(query),
                workspace_id=scope.workspace_id,
                knowledge_base_ids=knowledge_base_ids,
                document_version_ids=preparation.document_version_ids,
                limit=KNOWLEDGE_SEARCH_TOP_K,
            )
        except DenseSearchDependencyError as error:
            return KnowledgeSearchResult(
                status=KnowledgeSearchStatus.DEPENDENCY_FAILED,
                error_code=error.code,
            )
        if not candidates:
            return KnowledgeSearchResult(status=KnowledgeSearchStatus.NO_RESULT)
        try:
            hits = await self.repository.resolve(
                scope,
                preparation=preparation,
                knowledge_base_ids=knowledge_base_ids,
                candidates=candidates,
            )
        except KnowledgeSearchDependencyError as error:
            return KnowledgeSearchResult(
                status=KnowledgeSearchStatus.DEPENDENCY_FAILED,
                error_code=error.code,
            )
        if not hits:
            return KnowledgeSearchResult(status=KnowledgeSearchStatus.NO_RESULT)
        return KnowledgeSearchResult(status=KnowledgeSearchStatus.OK, hits=hits)
