"""Application services for locked filing import and Dense content reads."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from uuid import UUID, uuid5

from industry_platform.modules.disclosures.domain import (
    SecFilingContentError,
    SecFilingContentStatus,
    SecFilingDocumentKind,
    SecFilingSearchResult,
    SecFilingSection,
    SecFilingSnapshotStatus,
    SecSourceErrorCode,
    SecWorkspaceFilingImport,
)
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
from industry_platform.modules.retrieval.ports import DenseIndexPort, DenseSearchDependencyError
from industry_platform.modules.workspaces.domain import WorkspaceScope

SEC_IMPORT_FILE_NAMESPACE = UUID("4e129542-97e1-4d72-9702-0ff1c21d37aa")
_MAX_INDEXABLE_CHARACTERS = 5_000_000
_SPACE_PATTERN = re.compile(r"[ \t\f\v]+")
_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "div",
        "dl",
        "dt",
        "dd",
        "figcaption",
        "figure",
        "footer",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
    }
)
_SKIPPED_TAGS = frozenset({"script", "style", "noscript", "template", "ix:hidden"})


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


@dataclass(frozen=True, slots=True)
class SecFilingContentService:
    repository: SecFilingContentRepository
    dense_index: DenseIndexPort

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
                error_code=(
                    "filing_content_reload_failed"
                    if preparation.status is SecFilingContentStatus.DEPENDENCY_FAILED
                    else None
                ),
            )
        imported = preparation.import_record
        if imported is None:
            raise AssertionError("Ready SEC content preparation lost its import")
        try:
            candidates = await self.dense_index.search(
                embed_query_text(query),
                workspace_id=scope.workspace_id,
                knowledge_base_ids=(imported.knowledge_base_id,),
                document_version_ids=(imported.document_version_id,),
                limit=5,
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


class _FilingHtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skipped_depth = 0
        self._heading_level: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        normalized = tag.casefold()
        if normalized in _SKIPPED_TAGS:
            self._skipped_depth += 1
            return
        if self._skipped_depth:
            return
        if normalized in _BLOCK_TAGS or (normalized.startswith("h") and normalized[1:].isdigit()):
            self._parts.append("\n")
        if len(normalized) == 2 and normalized[0] == "h" and normalized[1] in "123456":
            self._heading_level = int(normalized[1])
            self._parts.append(f"{'#' * self._heading_level} ")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if normalized in _SKIPPED_TAGS:
            if self._skipped_depth:
                self._skipped_depth -= 1
            return
        if self._skipped_depth:
            return
        if normalized in _BLOCK_TAGS or (normalized.startswith("h") and normalized[1:].isdigit()):
            self._parts.append("\n")
        if len(normalized) == 2 and normalized[0] == "h" and normalized[1] in "123456":
            self._heading_level = None

    def handle_data(self, data: str) -> None:
        if not self._skipped_depth:
            self._parts.append(data)

    def markdown(self) -> str:
        return _normalize_text("".join(self._parts))


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
    parser = _FilingHtmlTextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        raise SecFilingContentError(SecSourceErrorCode.RESPONSE_INVALID) from None
    content = parser.markdown()
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
