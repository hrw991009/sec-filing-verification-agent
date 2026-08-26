"""PostgreSQL authorization and source-of-truth reload for Dense candidates."""

from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from sqlalchemy import and_, select, tuple_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.files.models import FileObject
from industry_platform.modules.financial_verification.domain import FinancialScope
from industry_platform.modules.knowledge.domain import (
    DocumentIndexKind,
    DocumentIndexStatus,
    DocumentStatus,
    DocumentVersionStatus,
    KnowledgeBaseStatus,
)
from industry_platform.modules.knowledge.models import (
    DocumentChunkRecord,
    DocumentIndexRecord,
    DocumentRecord,
    DocumentVersionRecord,
    KnowledgeBaseRecord,
)
from industry_platform.modules.retrieval.domain import (
    DenseCandidate,
    KnowledgeSearchHit,
    KnowledgeSearchStatus,
    SecFilingFixture,
    knowledge_evidence_ref,
)
from industry_platform.modules.retrieval.ports import (
    KnowledgeSearchDependencyError,
    KnowledgeSearchPreparation,
)
from industry_platform.modules.workspaces.domain import WorkspaceScope


class SqlAlchemyKnowledgeCandidateRepository:
    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        self._session_factory = session_factory

    async def prepare(
        self,
        scope: WorkspaceScope,
        *,
        knowledge_base_ids: tuple[UUID, ...],
        financial_scope: FinancialScope,
        fixture: SecFilingFixture | None,
    ) -> KnowledgeSearchPreparation:
        selected = tuple(knowledge_base_ids)
        if not selected or len(selected) != len(set(selected)):
            return KnowledgeSearchPreparation(status=KnowledgeSearchStatus.PERMISSION_DENIED)
        if fixture is None:
            return KnowledgeSearchPreparation(status=KnowledgeSearchStatus.AMBIGUOUS_FILER)
        scope_status = fixture.scope_status(financial_scope)
        if scope_status is not KnowledgeSearchStatus.OK:
            return KnowledgeSearchPreparation(status=scope_status)
        try:
            async with self._session_factory() as session:
                visible_ids = tuple(
                    (
                        await session.execute(
                            select(KnowledgeBaseRecord.id).where(
                                KnowledgeBaseRecord.workspace_id == scope.workspace_id,
                                KnowledgeBaseRecord.id.in_(selected),
                                KnowledgeBaseRecord.status == KnowledgeBaseStatus.ACTIVE,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                if set(visible_ids) != set(selected):
                    return KnowledgeSearchPreparation(
                        status=KnowledgeSearchStatus.PERMISSION_DENIED
                    )
                rows = (
                    await session.execute(
                        select(DocumentVersionRecord, DocumentRecord)
                        .join(
                            DocumentRecord,
                            and_(
                                DocumentRecord.id == DocumentVersionRecord.document_id,
                                DocumentRecord.workspace_id == DocumentVersionRecord.workspace_id,
                            ),
                        )
                        .join(
                            FileObject,
                            and_(
                                FileObject.id == DocumentVersionRecord.file_object_id,
                                FileObject.workspace_id == DocumentVersionRecord.workspace_id,
                            ),
                        )
                        .where(
                            DocumentVersionRecord.workspace_id == scope.workspace_id,
                            DocumentVersionRecord.knowledge_base_id.in_(selected),
                            FileObject.source_sha256 == fixture.content_sha256,
                        )
                    )
                ).all()
                ready = tuple(
                    version.id
                    for version, document in rows
                    if version.status is DocumentVersionStatus.READY
                    and document.status is DocumentStatus.ACTIVE
                    and document.active_version_id == version.id
                )
                if ready:
                    return KnowledgeSearchPreparation(
                        status=KnowledgeSearchStatus.OK,
                        fixture=fixture,
                        document_version_ids=ready,
                    )
                version_ids = tuple(version.id for version, _document in rows)
                if version_ids and await self._has_partial_index(session, version_ids):
                    return KnowledgeSearchPreparation(status=KnowledgeSearchStatus.PARTIAL_INDEX)
                return KnowledgeSearchPreparation(status=KnowledgeSearchStatus.NOT_READY)
        except SQLAlchemyError:
            return KnowledgeSearchPreparation(status=KnowledgeSearchStatus.DEPENDENCY_FAILED)

    async def resolve(
        self,
        scope: WorkspaceScope,
        *,
        preparation: KnowledgeSearchPreparation,
        knowledge_base_ids: tuple[UUID, ...],
        candidates: tuple[DenseCandidate, ...],
    ) -> tuple[KnowledgeSearchHit, ...]:
        fixture = preparation.fixture
        if fixture is None or preparation.status is not KnowledgeSearchStatus.OK:
            raise ValueError("Only a ready Knowledge search can resolve candidates")
        if not candidates:
            return ()
        pairs = tuple((item.chunk_id, item.document_version_id) for item in candidates)
        vector_record = aliased(DocumentIndexRecord)
        lexical_record = aliased(DocumentIndexRecord)
        try:
            async with self._session_factory() as session:
                rows = (
                    await session.execute(
                        select(
                            DocumentChunkRecord,
                            DocumentVersionRecord,
                            DocumentRecord,
                            vector_record,
                        )
                        .join(
                            DocumentVersionRecord,
                            and_(
                                DocumentVersionRecord.id == DocumentChunkRecord.document_version_id,
                                DocumentVersionRecord.workspace_id
                                == DocumentChunkRecord.workspace_id,
                            ),
                        )
                        .join(
                            DocumentRecord,
                            and_(
                                DocumentRecord.id == DocumentChunkRecord.document_id,
                                DocumentRecord.workspace_id == DocumentChunkRecord.workspace_id,
                            ),
                        )
                        .join(
                            FileObject,
                            and_(
                                FileObject.id == DocumentVersionRecord.file_object_id,
                                FileObject.workspace_id == DocumentVersionRecord.workspace_id,
                            ),
                        )
                        .join(
                            vector_record,
                            and_(
                                vector_record.chunk_id == DocumentChunkRecord.id,
                                vector_record.document_version_id
                                == DocumentChunkRecord.document_version_id,
                                vector_record.workspace_id == DocumentChunkRecord.workspace_id,
                                vector_record.kind == DocumentIndexKind.VECTOR,
                                vector_record.status == DocumentIndexStatus.SUCCEEDED,
                            ),
                        )
                        .join(
                            lexical_record,
                            and_(
                                lexical_record.chunk_id == DocumentChunkRecord.id,
                                lexical_record.document_version_id
                                == DocumentChunkRecord.document_version_id,
                                lexical_record.workspace_id == DocumentChunkRecord.workspace_id,
                                lexical_record.kind == DocumentIndexKind.LEXICAL,
                                lexical_record.status == DocumentIndexStatus.SUCCEEDED,
                                lexical_record.index_version == vector_record.index_version,
                            ),
                        )
                        .where(
                            DocumentChunkRecord.workspace_id == scope.workspace_id,
                            DocumentVersionRecord.knowledge_base_id.in_(knowledge_base_ids),
                            DocumentVersionRecord.id.in_(preparation.document_version_ids),
                            DocumentVersionRecord.status == DocumentVersionStatus.READY,
                            DocumentRecord.status == DocumentStatus.ACTIVE,
                            DocumentRecord.active_version_id == DocumentVersionRecord.id,
                            FileObject.source_sha256 == fixture.content_sha256,
                            tuple_(
                                DocumentChunkRecord.id,
                                DocumentChunkRecord.document_version_id,
                            ).in_(pairs),
                        )
                    )
                ).all()
        except SQLAlchemyError:
            raise KnowledgeSearchDependencyError("knowledge_reload_failed") from None
        by_pair = {(chunk.id, chunk.document_version_id): row for row in rows for chunk in [row[0]]}
        hits: list[KnowledgeSearchHit] = []
        for candidate in candidates:
            row = by_pair.get((candidate.chunk_id, candidate.document_version_id))
            if row is None:
                continue
            chunk, version, document, vector = row
            section, source_page = _source_locator(chunk.text_content, chunk.title_path, fixture)
            content_sha256 = chunk.content_hash.hex()
            hits.append(
                KnowledgeSearchHit(
                    evidence_ref=knowledge_evidence_ref(
                        workspace_id=scope.workspace_id,
                        accession=fixture.accession,
                        document_version_id=version.id,
                        chunk_id=chunk.id,
                        content_sha256=content_sha256,
                    ),
                    knowledge_base_id=version.knowledge_base_id,
                    document_id=document.id,
                    document_version_id=version.id,
                    chunk_id=chunk.id,
                    title=document.title,
                    excerpt=chunk.text_content,
                    score=candidate.score,
                    page_number=source_page,
                    section=section,
                    content_sha256=content_sha256,
                    parser_version=version.parser_version,
                    chunker_version=chunk.chunker_version,
                    index_version=vector.index_version,
                    fixture=fixture,
                )
            )
        return tuple(hits)

    async def validate_operands(
        self,
        scope: WorkspaceScope,
        *,
        knowledge_base_ids: tuple[UUID, ...],
        financial_scope: FinancialScope,
        evidence_values: tuple[tuple[UUID, str], ...],
        fixture: SecFilingFixture | None,
    ) -> KnowledgeSearchStatus:
        preparation = await self.prepare(
            scope,
            knowledge_base_ids=knowledge_base_ids,
            financial_scope=financial_scope,
            fixture=fixture,
        )
        if preparation.status is not KnowledgeSearchStatus.OK or fixture is None:
            return preparation.status
        if not evidence_values:
            return KnowledgeSearchStatus.NO_RESULT
        try:
            async with self._session_factory() as session:
                chunks = tuple(
                    (
                        await session.execute(
                            select(DocumentChunkRecord).where(
                                DocumentChunkRecord.workspace_id == scope.workspace_id,
                                DocumentChunkRecord.document_version_id.in_(
                                    preparation.document_version_ids
                                ),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
        except SQLAlchemyError:
            return KnowledgeSearchStatus.DEPENDENCY_FAILED
        values_by_ref: dict[UUID, set[str]] = defaultdict(set)
        for chunk in chunks:
            reference = knowledge_evidence_ref(
                workspace_id=scope.workspace_id,
                accession=fixture.accession,
                document_version_id=chunk.document_version_id,
                chunk_id=chunk.id,
                content_sha256=chunk.content_hash.hex(),
            )
            for fact in fixture.facts:
                if fact.anchor.casefold() in chunk.text_content.casefold() and (
                    fact.value in chunk.text_content.replace(",", "")
                ):
                    values_by_ref[reference].add(fact.value)
        if all(value in values_by_ref[reference] for reference, value in evidence_values):
            return KnowledgeSearchStatus.OK
        return KnowledgeSearchStatus.NO_RESULT

    @staticmethod
    async def _has_partial_index(
        session: AsyncSession,
        version_ids: tuple[UUID, ...],
    ) -> bool:
        records = tuple(
            (
                await session.execute(
                    select(DocumentIndexRecord).where(
                        DocumentIndexRecord.document_version_id.in_(version_ids),
                        DocumentIndexRecord.status == DocumentIndexStatus.SUCCEEDED,
                    )
                )
            )
            .scalars()
            .all()
        )
        kinds_by_version: dict[UUID, set[DocumentIndexKind]] = defaultdict(set)
        for record in records:
            kinds_by_version[record.document_version_id].add(record.kind)
        return any(kinds and len(kinds) < 2 for kinds in kinds_by_version.values())


def _source_locator(
    text: str,
    title_path: list[str],
    fixture: SecFilingFixture,
) -> tuple[str, int]:
    matching = tuple(fact for fact in fixture.facts if fact.anchor.casefold() in text.casefold())
    if matching:
        return matching[0].section, matching[0].source_page
    if title_path:
        return title_path[-1], 1
    return "Filing excerpt", 1
