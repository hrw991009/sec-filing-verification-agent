"""Application services for locked filing import and authorized content reads."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid5

from industry_platform.modules.disclosures.domain import (
    SEC_DENSE_RETRIEVAL_PROFILE_VERSION,
    SEC_HYBRID_RETRIEVAL_PROFILE_VERSION,
    SEC_IDENTITY_RERANKER_VERSION,
    SecFilingContentError,
    SecFilingContentPreparation,
    SecFilingContentStatus,
    SecFilingDocumentKind,
    SecFilingRetrievalTrace,
    SecFilingSearchHit,
    SecFilingSearchResult,
    SecFilingSection,
    SecFilingSnapshotStatus,
    SecSourceErrorCode,
    SecWorkspaceFilingImport,
)
from industry_platform.modules.disclosures.filing_tables import extract_filing_html
from industry_platform.modules.disclosures.ports import (
    SecFilingArchivePort,
    SecFilingContentRepository,
    SecFilingDocumentSnapshotStore,
)
from industry_platform.modules.files.domain import AttachmentMediaType
from industry_platform.modules.financial_verification.domain import FinancialScope
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.ingestion.adapters.embedding import embed_query_text
from industry_platform.modules.knowledge.domain import ImportKnowledgeTextSource
from industry_platform.modules.knowledge.service import KnowledgeApplicationService
from industry_platform.modules.retrieval.domain import HYBRID_RRF_K, reciprocal_rank_fusion
from industry_platform.modules.retrieval.ports import (
    DenseIndexPort,
    DenseSearchDependencyError,
    LexicalIndexPort,
    LexicalSearchDependencyError,
)
from industry_platform.modules.workspaces.domain import WorkspaceScope

SEC_IMPORT_FILE_NAMESPACE = UUID("4e129542-97e1-4d72-9702-0ff1c21d37aa")
_MAX_INDEXABLE_CHARACTERS = 5_000_000
_SPACE_PATTERN = re.compile(r"[ \t\f\v]+")
_HYBRID_CANDIDATE_LIMIT = 20
_FINAL_RESULT_LIMIT = 5
_MAX_RESULTS_PER_SECTION = 2
_QUERY_REWRITE_VERSION = "identity-query-v1"
_DIVERSITY_POLICY_VERSION = "section-cap-2-v1"


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class SecFilingImportService:
    repository: SecFilingContentRepository
    archive_source: SecFilingArchivePort
    snapshot_store: SecFilingDocumentSnapshotStore
    knowledge_service: KnowledgeApplicationService
    clock: Callable[[], datetime] = utc_now

    async def import_filing(
        self,
        scope: WorkspaceScope,
        *,
        accession: str,
        knowledge_base_id: UUID,
        as_of: datetime,
        trace_id: TraceId,
    ) -> SecWorkspaceFilingImport:
        filing = await self.repository.get_canonical_filing(accession)
        if (
            filing.public_available_at > as_of
            or filing.source_available_at > as_of
            or as_of > self.clock()
        ):
            raise SecFilingContentError(SecSourceErrorCode.SNAPSHOT_NOT_VISIBLE)
        archive = await self.archive_source.fetch_archive(filing)
        if any(document.source_available_at > as_of for document in archive.documents):
            raise SecFilingContentError(SecSourceErrorCode.SNAPSHOT_NOT_VISIBLE)

        object_keys: dict[str, str] = {}
        for document in archive.documents:
            object_keys[document.source_url] = await self.snapshot_store.persist(document)
        references = await self.repository.persist_archive(archive, object_keys=object_keys)
        if any(reference.status is SecFilingSnapshotStatus.QUARANTINED for reference in references):
            raise SecFilingContentError(SecSourceErrorCode.SNAPSHOT_ANOMALY)
        primary = next(
            reference
            for reference in references
            if reference.kind is SecFilingDocumentKind.PRIMARY_DOCUMENT
        )
        complete = next(
            reference
            for reference in references
            if reference.kind is SecFilingDocumentKind.COMPLETE_SUBMISSION
        )
        existing = await self.repository.find_import(
            scope,
            accession=accession,
            knowledge_base_id=knowledge_base_id,
            primary_snapshot_id=primary.snapshot_id,
        )
        if existing is not None:
            return existing

        primary_source = archive.document(SecFilingDocumentKind.PRIMARY_DOCUMENT)
        indexable = _indexable_markdown(
            primary_source.body,
            accession=filing.accession,
            cik=filing.cik,
            form=filing.form.value,
            report_date=filing.report_date.isoformat(),
            source_url=primary.source_url,
            source_sha256=primary.content_sha256,
        )
        file_id = uuid5(
            SEC_IMPORT_FILE_NAMESPACE,
            f"{scope.workspace_id}:{knowledge_base_id}:{primary.snapshot_id}",
        )
        idempotency_key = (
            f"sec-import:{scope.workspace_id}:{knowledge_base_id}:{primary.snapshot_id}"
        )
        receipt = await self.knowledge_service.import_text_source(
            scope,
            ImportKnowledgeTextSource(
                file_id=file_id,
                knowledge_base_id=knowledge_base_id,
                original_name=f"{filing.accession}.md",
                title=f"{filing.form.value} {filing.accession}",
                content=indexable.encode("utf-8"),
                idempotency_key=idempotency_key,
                trace_id=trace_id,
                declared_media_type=AttachmentMediaType.TEXT_MARKDOWN,
            ),
        )
        return await self.repository.record_import(
            scope,
            accession=accession,
            knowledge_base_id=knowledge_base_id,
            primary_snapshot_id=primary.snapshot_id,
            complete_submission_snapshot_id=complete.snapshot_id,
            file_id=receipt.source.file_id,
            document_id=receipt.document.id,
            document_version_id=receipt.version.id,
            ingestion_job_id=receipt.job_id,
            observed_at=self.clock(),
        )

    async def list_imports(
        self,
        scope: WorkspaceScope,
        *,
        limit: int = 100,
    ) -> tuple[SecWorkspaceFilingImport, ...]:
        return await self.repository.list_imports(scope, limit=limit)

    async def get_import(
        self,
        scope: WorkspaceScope,
        import_id: UUID,
    ) -> SecWorkspaceFilingImport:
        return await self.repository.get_import(scope, import_id)


class SecFilingReranker(Protocol):
    @property
    def version(self) -> str: ...

    async def rerank(
        self,
        query: str,
        hits: tuple[SecFilingSearchHit, ...],
        *,
        limit: int,
    ) -> tuple[SecFilingSearchHit, ...]: ...


@dataclass(frozen=True, slots=True)
class IdentitySecFilingReranker:
    version: str = SEC_IDENTITY_RERANKER_VERSION

    async def rerank(
        self,
        query: str,
        hits: tuple[SecFilingSearchHit, ...],
        *,
        limit: int,
    ) -> tuple[SecFilingSearchHit, ...]:
        del query
        return hits[:limit]


@dataclass(frozen=True, slots=True)
class SecFilingContentService:
    repository: SecFilingContentRepository
    dense_index: DenseIndexPort
    lexical_index: LexicalIndexPort | None = None
    reranker: SecFilingReranker = field(default_factory=IdentitySecFilingReranker)

    async def search(
        self,
        scope: WorkspaceScope,
        *,
        knowledge_base_ids: tuple[UUID, ...],
        financial_scope: FinancialScope,
        query: str,
    ) -> SecFilingSearchResult:
        filing = await self.repository.get_canonical_filing(financial_scope.accession)
        if (
            filing.cik != financial_scope.cik
            or filing.form.value != financial_scope.form.value
            or filing.report_date != financial_scope.report_period
        ):
            return SecFilingSearchResult(
                status=SecFilingContentStatus.PERMISSION_DENIED,
                accession=financial_scope.accession,
                retrieval_profile_version=self.retrieval_profile_version,
                retrieval_trace=self._empty_trace(financial_scope.as_of),
            )
        return await self.search_imported(
            scope,
            knowledge_base_ids=knowledge_base_ids,
            accession=financial_scope.accession,
            as_of=financial_scope.as_of,
            query=query,
        )

    async def search_imported(
        self,
        scope: WorkspaceScope,
        *,
        knowledge_base_ids: tuple[UUID, ...],
        accession: str,
        as_of: datetime,
        query: str,
    ) -> SecFilingSearchResult:
        preparation = await self.repository.prepare_content(
            scope,
            knowledge_base_ids=knowledge_base_ids,
            accession=accession,
            as_of=as_of,
        )
        if preparation.status is not SecFilingContentStatus.OK:
            return SecFilingSearchResult(
                status=preparation.status,
                accession=accession,
                retrieval_profile_version=self.retrieval_profile_version,
                error_code=(
                    "filing_content_reload_failed"
                    if preparation.status is SecFilingContentStatus.DEPENDENCY_FAILED
                    else None
                ),
                retrieval_trace=self._empty_trace(as_of),
            )
        imported = preparation.import_record
        if imported is None:
            raise AssertionError("Ready SEC content preparation lost its import")
        lexical_index = self.lexical_index
        if lexical_index is None:
            return await self._search_dense(
                scope,
                preparation=preparation,
                query=query,
                accession=accession,
            )
        try:
            dense_candidates, lexical_candidates = await asyncio.gather(
                self.dense_index.search(
                    embed_query_text(query),
                    workspace_id=scope.workspace_id,
                    knowledge_base_ids=(imported.knowledge_base_id,),
                    document_version_ids=(imported.document_version_id,),
                    limit=_HYBRID_CANDIDATE_LIMIT,
                ),
                lexical_index.search(
                    query,
                    workspace_id=scope.workspace_id,
                    knowledge_base_ids=(imported.knowledge_base_id,),
                    document_version_ids=(imported.document_version_id,),
                    limit=_HYBRID_CANDIDATE_LIMIT,
                ),
            )
        except (DenseSearchDependencyError, LexicalSearchDependencyError) as error:
            return SecFilingSearchResult(
                status=SecFilingContentStatus.DEPENDENCY_FAILED,
                accession=accession,
                retrieval_profile_version=SEC_HYBRID_RETRIEVAL_PROFILE_VERSION,
                error_code=error.code,
                retrieval_trace=self._empty_trace(as_of),
            )
        candidates = reciprocal_rank_fusion(
            dense_candidates,
            lexical_candidates,
            limit=_HYBRID_CANDIDATE_LIMIT,
        )
        trace = SecFilingRetrievalTrace(
            profile_version=SEC_HYBRID_RETRIEVAL_PROFILE_VERSION,
            dense_candidate_count=len(dense_candidates),
            lexical_candidate_count=len(lexical_candidates),
            fused_candidate_count=len(candidates),
            rrf_k=HYBRID_RRF_K,
            reranker_version=self.reranker.version,
            query_rewrite_version=_QUERY_REWRITE_VERSION,
            dense_candidate_limit=_HYBRID_CANDIDATE_LIMIT,
            lexical_candidate_limit=_HYBRID_CANDIDATE_LIMIT,
            final_limit=_FINAL_RESULT_LIMIT,
            diversity_policy_version=_DIVERSITY_POLICY_VERSION,
            as_of=as_of,
        )
        if not candidates:
            return SecFilingSearchResult(
                status=SecFilingContentStatus.NO_RESULT,
                accession=accession,
                retrieval_profile_version=SEC_HYBRID_RETRIEVAL_PROFILE_VERSION,
                retrieval_trace=trace,
            )
        hits = await self.repository.resolve_candidates(
            scope,
            preparation=preparation,
            candidates=candidates,
        )
        if not hits:
            return SecFilingSearchResult(
                status=SecFilingContentStatus.NO_RESULT,
                accession=accession,
                retrieval_profile_version=SEC_HYBRID_RETRIEVAL_PROFILE_VERSION,
                retrieval_trace=trace,
            )
        reranked = await self.reranker.rerank(query, hits, limit=_HYBRID_CANDIDATE_LIMIT)
        _validate_reranked_hits(hits, reranked)
        selected = _select_diverse_sections(reranked, limit=_FINAL_RESULT_LIMIT)
        trace = replace(
            trace,
            active_source_versions=tuple(dict.fromkeys(hit.source_version for hit in selected)),
            index_versions=tuple(dict.fromkeys(hit.index_version for hit in selected)),
        )
        return SecFilingSearchResult(
            status=SecFilingContentStatus.OK,
            accession=accession,
            retrieval_profile_version=SEC_HYBRID_RETRIEVAL_PROFILE_VERSION,
            hits=selected,
            retrieval_trace=trace,
        )

    @property
    def retrieval_profile_version(self) -> str:
        return (
            SEC_HYBRID_RETRIEVAL_PROFILE_VERSION
            if self.lexical_index is not None
            else SEC_DENSE_RETRIEVAL_PROFILE_VERSION
        )

    def _empty_trace(self, as_of: datetime) -> SecFilingRetrievalTrace | None:
        if self.lexical_index is None:
            return None
        return SecFilingRetrievalTrace(
            profile_version=SEC_HYBRID_RETRIEVAL_PROFILE_VERSION,
            dense_candidate_count=0,
            lexical_candidate_count=0,
            fused_candidate_count=0,
            rrf_k=HYBRID_RRF_K,
            reranker_version=self.reranker.version,
            query_rewrite_version=_QUERY_REWRITE_VERSION,
            dense_candidate_limit=_HYBRID_CANDIDATE_LIMIT,
            lexical_candidate_limit=_HYBRID_CANDIDATE_LIMIT,
            final_limit=_FINAL_RESULT_LIMIT,
            diversity_policy_version=_DIVERSITY_POLICY_VERSION,
            as_of=as_of,
        )

    async def _search_dense(
        self,
        scope: WorkspaceScope,
        *,
        preparation: SecFilingContentPreparation,
        query: str,
        accession: str,
    ) -> SecFilingSearchResult:
        imported = preparation.import_record
        if imported is None:
            raise AssertionError("Ready SEC content preparation lost its import")
        try:
            candidates = await self.dense_index.search(
                embed_query_text(query),
                workspace_id=scope.workspace_id,
                knowledge_base_ids=(imported.knowledge_base_id,),
                document_version_ids=(imported.document_version_id,),
                limit=_FINAL_RESULT_LIMIT,
            )
        except DenseSearchDependencyError as error:
            return SecFilingSearchResult(
                status=SecFilingContentStatus.DEPENDENCY_FAILED,
                accession=accession,
                error_code=error.code,
            )
        if not candidates:
            return SecFilingSearchResult(
                status=SecFilingContentStatus.NO_RESULT,
                accession=accession,
            )
        hits = await self.repository.resolve_candidates(
            scope,
            preparation=preparation,
            candidates=candidates,
        )
        if not hits:
            return SecFilingSearchResult(
                status=SecFilingContentStatus.NO_RESULT,
                accession=accession,
            )
        return SecFilingSearchResult(
            status=SecFilingContentStatus.OK,
            accession=accession,
            hits=hits,
        )

    async def read_section(
        self,
        scope: WorkspaceScope,
        *,
        knowledge_base_ids: tuple[UUID, ...],
        financial_scope: FinancialScope,
        document_version_id: UUID,
        chunk_id: UUID,
    ) -> SecFilingSection:
        filing = await self.repository.get_canonical_filing(financial_scope.accession)
        if (
            filing.cik != financial_scope.cik
            or filing.form.value != financial_scope.form.value
            or filing.report_date != financial_scope.report_period
        ):
            raise SecFilingContentError(SecSourceErrorCode.SNAPSHOT_NOT_VISIBLE)
        return await self.read_imported_section(
            scope,
            accession=financial_scope.accession,
            as_of=financial_scope.as_of,
            knowledge_base_ids=knowledge_base_ids,
            document_version_id=document_version_id,
            chunk_id=chunk_id,
        )

    async def read_imported_section(
        self,
        scope: WorkspaceScope,
        *,
        accession: str,
        as_of: datetime,
        knowledge_base_ids: tuple[UUID, ...],
        document_version_id: UUID,
        chunk_id: UUID,
    ) -> SecFilingSection:
        return await self.repository.read_section(
            scope,
            accession=accession,
            as_of=as_of,
            knowledge_base_ids=knowledge_base_ids,
            document_version_id=document_version_id,
            chunk_id=chunk_id,
        )


def _select_diverse_sections(
    hits: tuple[SecFilingSearchHit, ...],
    *,
    limit: int,
) -> tuple[SecFilingSearchHit, ...]:
    selected: list[SecFilingSearchHit] = []
    section_counts: dict[str, int] = {}
    for hit in hits:
        section_key = " ".join(hit.section.casefold().split())
        if section_counts.get(section_key, 0) >= _MAX_RESULTS_PER_SECTION:
            continue
        selected.append(hit)
        section_counts[section_key] = section_counts.get(section_key, 0) + 1
        if len(selected) == limit:
            break
    return tuple(selected)


def _validate_reranked_hits(
    authorized: tuple[SecFilingSearchHit, ...],
    reranked: tuple[SecFilingSearchHit, ...],
) -> None:
    authorized_ids = {(hit.document_version_id, hit.chunk_id) for hit in authorized}
    reranked_ids = [(hit.document_version_id, hit.chunk_id) for hit in reranked]
    if len(reranked_ids) != len(set(reranked_ids)) or not set(reranked_ids).issubset(
        authorized_ids
    ):
        raise ValueError("SEC reranker introduced an unauthorized candidate")


def _indexable_markdown(
    body: bytes,
    *,
    accession: str,
    cik: str,
    form: str,
    report_date: str,
    source_url: str,
    source_sha256: str,
) -> str:
    try:
        html = body.decode("utf-8")
    except UnicodeDecodeError:
        try:
            html = body.decode("cp1252")
        except UnicodeDecodeError:
            raise SecFilingContentError(SecSourceErrorCode.RESPONSE_INVALID) from None
    try:
        content = extract_filing_html(html).markdown
    except (ValueError, AssertionError):
        raise SecFilingContentError(SecSourceErrorCode.RESPONSE_INVALID) from None
    if not content or len(content) > _MAX_INDEXABLE_CHARACTERS:
        raise SecFilingContentError(SecSourceErrorCode.RESPONSE_TOO_LARGE)
    header = (
        f"# SEC Filing {accession}\n\n"
        f"- CIK: {cik}\n"
        f"- Form: {form}\n"
        f"- Report date: {report_date}\n"
        f"- Official source: {source_url}\n"
        f"- Source SHA-256: {source_sha256}\n\n"
    )
    return f"{header}{content}"


def _normalize_text(value: str) -> str:
    lines = [_SPACE_PATTERN.sub(" ", line).strip() for line in value.replace("\r", "").split("\n")]
    normalized: list[str] = []
    blank = False
    for line in lines:
        if line:
            normalized.append(line)
            blank = False
        elif normalized and not blank:
            normalized.append("")
            blank = True
    return "\n".join(normalized).strip()
