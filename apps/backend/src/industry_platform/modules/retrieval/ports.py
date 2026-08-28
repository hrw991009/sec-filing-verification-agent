"""Ports for Dense candidates and PostgreSQL Knowledge authorization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from industry_platform.modules.financial_verification.domain import FinancialScope
from industry_platform.modules.retrieval.domain import (
    DenseCandidate,
    KnowledgeSearchHit,
    KnowledgeSearchStatus,
    LexicalCandidate,
    SecFilingFixture,
)
from industry_platform.modules.workspaces.domain import WorkspaceScope


class DenseSearchDependencyError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("Dense search dependency failed")
        self.code = code


class LexicalSearchDependencyError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("Lexical search dependency failed")
        self.code = code


class KnowledgeSearchDependencyError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("Knowledge search dependency failed")
        self.code = code


@dataclass(frozen=True, slots=True)
class KnowledgeSearchPreparation:
    status: KnowledgeSearchStatus
    fixture: SecFilingFixture | None = None
    document_version_ids: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        versions = tuple(self.document_version_ids)
        if (self.status is KnowledgeSearchStatus.OK) != bool(versions):
            raise ValueError("Knowledge search preparation is inconsistent")
        if (self.status is KnowledgeSearchStatus.OK) != (self.fixture is not None):
            raise ValueError("Knowledge search preparation fixture is inconsistent")
        object.__setattr__(self, "document_version_ids", versions)


class DenseIndexPort(Protocol):
    async def search(
        self,
        vector: tuple[float, ...],
        *,
        workspace_id: UUID,
        knowledge_base_ids: tuple[UUID, ...],
        document_version_ids: tuple[UUID, ...],
        limit: int,
    ) -> tuple[DenseCandidate, ...]: ...


class LexicalIndexPort(Protocol):
    async def search(
        self,
        query: str,
        *,
        workspace_id: UUID,
        knowledge_base_ids: tuple[UUID, ...],
        document_version_ids: tuple[UUID, ...],
        limit: int,
    ) -> tuple[LexicalCandidate, ...]: ...


class KnowledgeCandidateRepository(Protocol):
    async def prepare(
        self,
        scope: WorkspaceScope,
        *,
        knowledge_base_ids: tuple[UUID, ...],
        financial_scope: FinancialScope,
        fixture: SecFilingFixture | None,
    ) -> KnowledgeSearchPreparation: ...

    async def resolve(
        self,
        scope: WorkspaceScope,
        *,
        preparation: KnowledgeSearchPreparation,
        knowledge_base_ids: tuple[UUID, ...],
        candidates: tuple[DenseCandidate, ...],
    ) -> tuple[KnowledgeSearchHit, ...]: ...

    async def validate_operands(
        self,
        scope: WorkspaceScope,
        *,
        knowledge_base_ids: tuple[UUID, ...],
        financial_scope: FinancialScope,
        evidence_values: tuple[tuple[UUID, str], ...],
        fixture: SecFilingFixture | None,
    ) -> KnowledgeSearchStatus: ...
