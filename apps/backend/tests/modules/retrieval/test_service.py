"""Dense retrieval orchestration tests with PostgreSQL and Milvus ports replaced."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import pytest

from industry_platform.modules.financial_verification.domain import (
    FinancialForm,
    FinancialScope,
)
from industry_platform.modules.retrieval.domain import (
    DenseCandidate,
    KnowledgeSearchHit,
    KnowledgeSearchStatus,
    knowledge_evidence_ref,
)
from industry_platform.modules.retrieval.fixtures import (
    SecFixtureCatalog,
    load_sec_fixture_catalog,
)
from industry_platform.modules.retrieval.ports import (
    DenseSearchDependencyError,
    KnowledgeSearchDependencyError,
    KnowledgeSearchPreparation,
)
from industry_platform.modules.retrieval.service import KnowledgeSearchService
from industry_platform.modules.workspaces.domain import WorkspaceScope

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
MANIFEST = REPOSITORY_ROOT / "evals" / "fixtures" / "sec" / "sec-fixture-v1" / "manifest.json"
WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
USER_ID = UUID("22222222-2222-4222-8222-222222222222")
KNOWLEDGE_BASE_ID = UUID("33333333-3333-4333-8333-333333333333")
DOCUMENT_ID = UUID("44444444-4444-4444-8444-444444444444")
VERSION_ID = UUID("55555555-5555-4555-8555-555555555555")
CHUNK_ID = UUID("66666666-6666-4666-8666-666666666666")
CHUNK_HASH = "a" * 64


def catalog() -> SecFixtureCatalog:
    return load_sec_fixture_catalog(MANIFEST, repository_root=REPOSITORY_ROOT)


def financial_scope() -> FinancialScope:
    return FinancialScope(
        cik="0000320193",
        accession="0000320193-23-000106",
        form=FinancialForm.TEN_K,
        report_period=date(2023, 9, 30),
        as_of=datetime(2023, 11, 3, tzinfo=UTC),
        unit="USD",
        scale=6,
    )


@dataclass(slots=True)
class CandidateRepositoryStub:
    status: KnowledgeSearchStatus = KnowledgeSearchStatus.OK
    resolve_failure: str | None = None
    resolve_calls: int = 0

    async def prepare(self, scope: WorkspaceScope, **kwargs: object) -> KnowledgeSearchPreparation:
        del scope, kwargs
        if self.status is not KnowledgeSearchStatus.OK:
            return KnowledgeSearchPreparation(status=self.status)
        return KnowledgeSearchPreparation(
            status=self.status,
            fixture=catalog().filings[0],
            document_version_ids=(VERSION_ID,),
        )

    async def resolve(
        self,
        scope: WorkspaceScope,
        *,
        preparation: KnowledgeSearchPreparation,
        knowledge_base_ids: tuple[UUID, ...],
        candidates: tuple[DenseCandidate, ...],
    ) -> tuple[KnowledgeSearchHit, ...]:
        del knowledge_base_ids
        self.resolve_calls += 1
        if self.resolve_failure is not None:
            raise KnowledgeSearchDependencyError(self.resolve_failure)
        assert preparation.fixture is not None
        return tuple(
            KnowledgeSearchHit(
                evidence_ref=knowledge_evidence_ref(
                    workspace_id=scope.workspace_id,
                    accession=preparation.fixture.accession,
                    document_version_id=item.document_version_id,
                    chunk_id=item.chunk_id,
                    content_sha256=CHUNK_HASH,
                ),
                knowledge_base_id=KNOWLEDGE_BASE_ID,
                document_id=DOCUMENT_ID,
                document_version_id=item.document_version_id,
                chunk_id=item.chunk_id,
                title="Apple 2023 Form 10-K",
                excerpt="Total net sales 383285 394328",
                score=item.score,
                page_number=29,
                section="Item 8. Consolidated Statements of Operations",
                content_sha256=CHUNK_HASH,
                parser_version="1.0.0",
                chunker_version="1.0.0",
                index_version="knowledge-index-v1",
                fixture=preparation.fixture,
            )
            for item in candidates
        )

    async def validate_operands(
        self,
        scope: WorkspaceScope,
        **kwargs: object,
    ) -> KnowledgeSearchStatus:
        del scope, kwargs
        return self.status


@dataclass(slots=True)
class DenseIndexStub:
    failure: str | None = None
    candidates: tuple[DenseCandidate, ...] = (DenseCandidate(CHUNK_ID, VERSION_ID, 0.95),)
    calls: list[tuple[UUID, tuple[UUID, ...], tuple[UUID, ...], int]] = field(default_factory=list)

    async def search(
        self,
        vector: tuple[float, ...],
        *,
        workspace_id: UUID,
        knowledge_base_ids: tuple[UUID, ...],
        document_version_ids: tuple[UUID, ...],
        limit: int,
    ) -> tuple[DenseCandidate, ...]:
        assert len(vector) == 64
        self.calls.append((workspace_id, knowledge_base_ids, document_version_ids, limit))
        if self.failure is not None:
            raise DenseSearchDependencyError(self.failure)
        return self.candidates


@pytest.mark.asyncio
async def test_search_preflights_postgres_then_resolves_dense_candidates() -> None:
    repository = CandidateRepositoryStub()
    index = DenseIndexStub()
    service = KnowledgeSearchService(catalog(), repository, index)

    result = await service.search(
        WorkspaceScope(WORKSPACE_ID, USER_ID, "member"),
        knowledge_base_ids=(KNOWLEDGE_BASE_ID,),
        financial_scope=financial_scope(),
        query="2023 net sales change",
    )

    assert result.status is KnowledgeSearchStatus.OK
    assert result.hits[0].chunk_id == CHUNK_ID
    assert index.calls == [(WORKSPACE_ID, (KNOWLEDGE_BASE_ID,), (VERSION_ID,), 5)]
    assert repository.resolve_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        KnowledgeSearchStatus.NOT_READY,
        KnowledgeSearchStatus.PARTIAL_INDEX,
        KnowledgeSearchStatus.PERMISSION_DENIED,
        KnowledgeSearchStatus.AMBIGUOUS_FILER,
        KnowledgeSearchStatus.PERIOD_MISMATCH,
    ],
)
async def test_preflight_statuses_do_not_query_the_vector_index(
    status: KnowledgeSearchStatus,
) -> None:
    repository = CandidateRepositoryStub(status=status)
    index = DenseIndexStub()

    result = await KnowledgeSearchService(catalog(), repository, index).search(
        WorkspaceScope(WORKSPACE_ID, USER_ID, "member"),
        knowledge_base_ids=(KNOWLEDGE_BASE_ID,),
        financial_scope=financial_scope(),
        query="net sales",
    )

    assert result.status is status
    assert not index.calls


@pytest.mark.asyncio
async def test_dense_dependency_failure_is_sanitized_to_a_stable_status() -> None:
    result = await KnowledgeSearchService(
        catalog(),
        CandidateRepositoryStub(),
        DenseIndexStub(failure="vector_search_timeout"),
    ).search(
        WorkspaceScope(WORKSPACE_ID, USER_ID, "member"),
        knowledge_base_ids=(KNOWLEDGE_BASE_ID,),
        financial_scope=financial_scope(),
        query="net sales",
    )

    assert result.status is KnowledgeSearchStatus.DEPENDENCY_FAILED
    assert result.error_code == "vector_search_timeout"


@pytest.mark.asyncio
async def test_empty_dense_candidates_return_no_result_without_postgres_resolution() -> None:
    repository = CandidateRepositoryStub()
    result = await KnowledgeSearchService(
        catalog(),
        repository,
        DenseIndexStub(candidates=()),
    ).search(
        WorkspaceScope(WORKSPACE_ID, USER_ID, "member"),
        knowledge_base_ids=(KNOWLEDGE_BASE_ID,),
        financial_scope=financial_scope(),
        query="missing metric",
    )

    assert result.status is KnowledgeSearchStatus.NO_RESULT
    assert repository.resolve_calls == 0


@pytest.mark.asyncio
async def test_postgres_resolution_failure_is_sanitized_to_a_stable_status() -> None:
    result = await KnowledgeSearchService(
        catalog(),
        CandidateRepositoryStub(resolve_failure="knowledge_candidate_lookup_failed"),
        DenseIndexStub(),
    ).search(
        WorkspaceScope(WORKSPACE_ID, USER_ID, "member"),
        knowledge_base_ids=(KNOWLEDGE_BASE_ID,),
        financial_scope=financial_scope(),
        query="net sales",
    )

    assert result.status is KnowledgeSearchStatus.DEPENDENCY_FAILED
    assert result.error_code == "knowledge_candidate_lookup_failed"
