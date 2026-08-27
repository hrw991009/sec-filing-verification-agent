"""PostgreSQL truth and Workspace authorization for SEC XBRL facts."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import and_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from industry_platform.core.database import AsyncSessionFactory
from industry_platform.modules.disclosures.domain import (
    SecCanonicalFiling,
    SecDisclosurePersistenceError,
    SecFilingContentError,
    SecFilingContentStatus,
    SecFilingDocumentKind,
    SecFilingForm,
    SecFilingImportStatus,
    SecFilingSnapshotReference,
    SecFilingSnapshotStatus,
    SecSourceErrorCode,
    SecWorkspaceFilingImport,
    SecXbrlContextData,
    SecXbrlDataset,
    SecXbrlFact,
    SecXbrlFactData,
    SecXbrlFactQuery,
    SecXbrlFactResult,
    SecXbrlPeriod,
    SecXbrlPeriodKind,
    SecXbrlSourceKind,
    SecXbrlSourceSnapshot,
    SecXbrlSyncPreparation,
    SecXbrlSyncResult,
)
from industry_platform.modules.disclosures.models import (
    SecFilingDocumentRecord,
    SecFilingRecord,
    SecSourceSnapshotRecord,
    SecSubmissionSourceRecord,
    SecXbrlContextRecord,
    SecXbrlFactRecord,
    SecXbrlSourceRecord,
    WorkspaceSecImportRecord,
)
from industry_platform.modules.knowledge.domain import DocumentVersionStatus
from industry_platform.modules.knowledge.models import DocumentVersionRecord
from industry_platform.modules.workspaces.domain import WorkspaceScope


class SqlAlchemySecXbrlRepository:
    def __init__(self, session_factory: AsyncSessionFactory, *, object_bucket: str) -> None:
        if not object_bucket.strip():
            raise ValueError("SEC XBRL object bucket is invalid")
        self._session_factory = session_factory
        self._object_bucket = object_bucket

    async def prepare_sync(
        self,
        scope: WorkspaceScope,
        *,
        accession: str,
        knowledge_base_id: UUID,
    ) -> SecXbrlSyncPreparation:
        try:
            async with self._session_factory() as session:
                row = (
                    await session.execute(
                        select(
                            WorkspaceSecImportRecord,
                            DocumentVersionRecord,
                            SecFilingRecord,
                            SecSubmissionSourceRecord,
                        )
                        .join(
                            DocumentVersionRecord,
                            and_(
                                DocumentVersionRecord.id
                                == WorkspaceSecImportRecord.document_version_id,
                                DocumentVersionRecord.workspace_id
                                == WorkspaceSecImportRecord.workspace_id,
                            ),
                        )
                        .join(
                            SecFilingRecord,
                            SecFilingRecord.id == WorkspaceSecImportRecord.filing_id,
                        )
                        .join(
                            SecSubmissionSourceRecord,
                            SecSubmissionSourceRecord.id == SecFilingRecord.source_id,
                        )
                        .where(
                            WorkspaceSecImportRecord.workspace_id == scope.workspace_id,
                            WorkspaceSecImportRecord.knowledge_base_id == knowledge_base_id,
                            WorkspaceSecImportRecord.accession == accession,
                        )
                        .order_by(WorkspaceSecImportRecord.created_at.desc())
                    )
                ).first()
                if row is None:
                    raise SecFilingContentError(SecSourceErrorCode.FILING_NOT_FOUND)
                imported, version, filing, submission_source = row
                if version.status is not DocumentVersionStatus.READY:
                    raise SecFilingContentError(SecSourceErrorCode.IMPORT_NOT_READY)
                source_rows = (
                    await session.execute(
                        select(SecFilingDocumentRecord, SecSourceSnapshotRecord)
                        .join(
                            SecSourceSnapshotRecord,
                            and_(
                                SecSourceSnapshotRecord.id
                                == SecFilingDocumentRecord.current_snapshot_id,
                                SecSourceSnapshotRecord.filing_document_id
                                == SecFilingDocumentRecord.id,
                            ),
                        )
                        .where(
                            SecFilingDocumentRecord.filing_id == filing.id,
                            SecFilingDocumentRecord.document_kind.in_(
                                (
                                    SecFilingDocumentKind.PRIMARY_DOCUMENT.value,
                                    SecFilingDocumentKind.XBRL_INSTANCE.value,
                                )
                            ),
                            SecSourceSnapshotRecord.status == SecFilingSnapshotStatus.ACTIVE.value,
                        )
                    )
                ).all()
                sources = tuple(
                    sorted(
                        (
                            _snapshot_reference(document, filing.id, snapshot)
                            for document, snapshot in source_rows
                        ),
                        key=lambda item: (item.kind.value, item.filename),
                    )
                )
                if not any(
                    source.kind is SecFilingDocumentKind.PRIMARY_DOCUMENT for source in sources
                ):
                    raise SecFilingContentError(SecSourceErrorCode.IMPORT_NOT_READY)
                return SecXbrlSyncPreparation(
                    filing=_canonical_filing(filing, submission_source),
                    import_record=_workspace_import(imported, version),
                    raw_sources=sources,
                )
        except SecFilingContentError:
            raise
        except SQLAlchemyError as error:
            raise SecDisclosurePersistenceError(sqlstate=_sqlstate(error)) from None

    async def persist_dataset(
        self,
        dataset: SecXbrlDataset,
        *,
        aggregate_object_keys: dict[str, str],
    ) -> SecXbrlSyncResult:
        aggregate_urls = {
            batch.source.source_url
            for batch in dataset.batches
            if batch.source.source_kind is SecXbrlSourceKind.COMPANYFACTS_AGGREGATE
        }
        if set(aggregate_object_keys) != aggregate_urls:
            raise ValueError("SEC aggregate XBRL object manifest is incomplete")
        try:
            async with self._session_factory.begin() as session:
                filing = await session.scalar(
                    select(SecFilingRecord)
                    .where(
                        SecFilingRecord.id == dataset.filing.id,
                        SecFilingRecord.accession == dataset.filing.accession,
                    )
                    .with_for_update()
                )
                if filing is None:
                    raise SecFilingContentError(SecSourceErrorCode.FILING_NOT_FOUND)
                for batch in dataset.batches:
                    source = await self._source_record(
                        session,
                        batch.source,
                        object_key=aggregate_object_keys.get(batch.source.source_url),
                        object_bucket=self._object_bucket,
                        filing_id=filing.id,
                    )
                    contexts = await self._context_records(session, source, batch.contexts)
                    await self._fact_records(
                        session,
                        filing,
                        source,
                        contexts,
                        batch.facts,
                    )
                return SecXbrlSyncResult(
                    accession=dataset.filing.accession,
                    source_count=len(dataset.batches),
                    context_count=sum(len(batch.contexts) for batch in dataset.batches),
                    fact_count=sum(len(batch.facts) for batch in dataset.batches),
                    source_versions=tuple(
                        sorted(batch.source.source_version for batch in dataset.batches)
                    ),
                )
        except SecFilingContentError:
            raise
        except SQLAlchemyError as error:
            raise SecDisclosurePersistenceError(sqlstate=_sqlstate(error)) from None

    async def query_facts(
        self,
        scope: WorkspaceScope,
        *,
        knowledge_base_ids: tuple[UUID, ...],
        accession: str,
        as_of: datetime,
        query: SecXbrlFactQuery,
    ) -> SecXbrlFactResult:
        selected = tuple(knowledge_base_ids)
        if not selected or len(selected) != len(set(selected)):
            return SecXbrlFactResult(
                status=SecFilingContentStatus.PERMISSION_DENIED,
                accession=accession,
            )
        try:
            async with self._session_factory() as session:
                import_rows = (
                    await session.execute(
                        select(
                            WorkspaceSecImportRecord,
                            DocumentVersionRecord,
                            SecFilingRecord,
                            SecSourceSnapshotRecord,
                        )
                        .join(
                            DocumentVersionRecord,
                            and_(
                                DocumentVersionRecord.id
                                == WorkspaceSecImportRecord.document_version_id,
                                DocumentVersionRecord.workspace_id
                                == WorkspaceSecImportRecord.workspace_id,
                            ),
                        )
                        .join(
                            SecFilingRecord,
                            SecFilingRecord.id == WorkspaceSecImportRecord.filing_id,
                        )
                        .join(
                            SecSourceSnapshotRecord,
                            SecSourceSnapshotRecord.id
                            == WorkspaceSecImportRecord.primary_snapshot_id,
                        )
                        .where(
                            WorkspaceSecImportRecord.workspace_id == scope.workspace_id,
                            WorkspaceSecImportRecord.knowledge_base_id.in_(selected),
                            WorkspaceSecImportRecord.accession == accession,
                        )
                    )
                ).all()
                if not import_rows:
                    return SecXbrlFactResult(
                        status=SecFilingContentStatus.PERMISSION_DENIED,
                        accession=accession,
                    )
                visible = tuple(
                    row
                    for row in import_rows
                    if row[2].public_available_at <= as_of
                    and row[3].source_available_at <= as_of
                    and row[3].status == SecFilingSnapshotStatus.ACTIVE.value
                )
                if not visible:
                    return SecXbrlFactResult(
                        status=SecFilingContentStatus.NO_RESULT,
                        accession=accession,
                    )
                ready = tuple(
                    row for row in visible if row[1].status is DocumentVersionStatus.READY
                )
                if not ready:
                    return SecXbrlFactResult(
                        status=SecFilingContentStatus.NOT_READY,
                        accession=accession,
                    )
                filing_ids = {row[2].id for row in ready}
                predicates = [
                    SecXbrlFactRecord.filing_id.in_(filing_ids),
                    SecXbrlFactRecord.accession == accession,
                    SecXbrlSourceRecord.source_available_at <= as_of,
                    SecXbrlSourceRecord.source_kind.in_(
                        tuple(kind.value for kind in query.source_kinds)
                    ),
                ]
                if query.taxonomy is not None:
                    predicates.append(SecXbrlFactRecord.taxonomy == query.taxonomy)
                if query.concept is not None:
                    predicates.append(SecXbrlFactRecord.concept == query.concept)
                if query.unit is not None:
                    predicates.append(SecXbrlFactRecord.unit == query.unit)
                if query.period_kind is not None:
                    predicates.append(SecXbrlFactRecord.period_kind == query.period_kind.value)
                rows = (
                    await session.execute(
                        select(
                            SecXbrlFactRecord,
                            SecXbrlSourceRecord,
                            SecFilingRecord,
                        )
                        .join(
                            SecXbrlSourceRecord,
                            SecXbrlSourceRecord.id == SecXbrlFactRecord.source_id,
                        )
                        .join(
                            SecFilingRecord,
                            SecFilingRecord.id == SecXbrlFactRecord.filing_id,
                        )
                        .where(*predicates)
                        .order_by(
                            SecXbrlFactRecord.taxonomy,
                            SecXbrlFactRecord.concept,
                            SecXbrlFactRecord.period_kind,
                            SecXbrlFactRecord.start_date,
                            SecXbrlFactRecord.end_date,
                            SecXbrlSourceRecord.source_kind,
                            SecXbrlFactRecord.ordinal,
                        )
                        .limit(query.limit)
                    )
                ).all()
                facts = tuple(_fact(record, source, filing) for record, source, filing in rows)
                return SecXbrlFactResult(
                    status=(
                        SecFilingContentStatus.OK if facts else SecFilingContentStatus.NO_RESULT
                    ),
                    accession=accession,
                    facts=facts,
                )
        except SQLAlchemyError:
            return SecXbrlFactResult(
                status=SecFilingContentStatus.DEPENDENCY_FAILED,
                accession=accession,
                error_code="xbrl_fact_reload_failed",
            )

    async def _source_record(
        self,
        session: AsyncSession,
        source: SecXbrlSourceSnapshot,
        *,
        object_key: str | None,
        object_bucket: str,
        filing_id: UUID,
    ) -> SecXbrlSourceRecord:
        existing = await session.scalar(
            select(SecXbrlSourceRecord).where(
                SecXbrlSourceRecord.source_url == source.source_url,
                SecXbrlSourceRecord.source_version == source.source_version,
            )
        )
        if existing is not None:
            _verify_source(
                existing,
                source,
                object_key=object_key,
                object_bucket=object_bucket,
            )
            return existing
        if source.filing_snapshot_id is not None:
            expected_kind = {
                SecXbrlSourceKind.RAW_INLINE: SecFilingDocumentKind.PRIMARY_DOCUMENT,
                SecXbrlSourceKind.RAW_INSTANCE: SecFilingDocumentKind.XBRL_INSTANCE,
            }[source.source_kind]
            valid_raw = await session.scalar(
                select(SecSourceSnapshotRecord.id)
                .join(
                    SecFilingDocumentRecord,
                    SecFilingDocumentRecord.id == SecSourceSnapshotRecord.filing_document_id,
                )
                .where(
                    SecSourceSnapshotRecord.id == source.filing_snapshot_id,
                    SecSourceSnapshotRecord.status == SecFilingSnapshotStatus.ACTIVE.value,
                    SecFilingDocumentRecord.filing_id == filing_id,
                    SecFilingDocumentRecord.document_kind == expected_kind.value,
                )
            )
            if valid_raw is None:
                raise SecFilingContentError(SecSourceErrorCode.SNAPSHOT_ANOMALY)
        record = SecXbrlSourceRecord(
            id=uuid4(),
            cik=source.cik,
            source_kind=source.source_kind.value,
            filing_snapshot_id=source.filing_snapshot_id,
            source_url=source.source_url,
            source_version=source.source_version,
            content_type=source.content_type,
            content_sha256=bytes.fromhex(source.content_sha256),
            byte_size=source.byte_size,
            object_bucket=(
                object_bucket
                if source.source_kind is SecXbrlSourceKind.COMPANYFACTS_AGGREGATE
                else None
            ),
            object_key=object_key,
            retrieved_at=source.retrieved_at,
            source_available_at=source.source_available_at,
            adapter_version=source.adapter_version,
        )
        session.add(record)
        await session.flush()
        return record

    async def _context_records(
        self,
        session: AsyncSession,
        source: SecXbrlSourceRecord,
        contexts: tuple[SecXbrlContextData, ...],
    ) -> dict[str, SecXbrlContextRecord]:
        existing = {
            record.raw_context_id: record
            for record in (
                await session.scalars(
                    select(SecXbrlContextRecord).where(SecXbrlContextRecord.source_id == source.id)
                )
            ).all()
        }
        for context in contexts:
            record = existing.get(context.context_id)
            if record is not None:
                _verify_context(record, context)
                continue
            record = SecXbrlContextRecord(
                id=uuid4(),
                source_id=source.id,
                raw_context_id=context.context_id,
                entity_identifier=context.entity_identifier,
                period_kind=context.period.kind.value,
                instant=context.period.instant,
                start_date=context.period.start_date,
                end_date=context.period.end_date,
                dimensions=dict(context.dimensions),
            )
            session.add(record)
            existing[context.context_id] = record
        await session.flush()
        return existing

    async def _fact_records(
        self,
        session: AsyncSession,
        filing: SecFilingRecord,
        source: SecXbrlSourceRecord,
        contexts: dict[str, SecXbrlContextRecord],
        facts: tuple[SecXbrlFactData, ...],
    ) -> None:
        existing = {
            record.locator_key: record
            for record in (
                await session.scalars(
                    select(SecXbrlFactRecord).where(SecXbrlFactRecord.source_id == source.id)
                )
            ).all()
        }
        for fact in facts:
            record = existing.get(fact.locator_key)
            if record is not None:
                _verify_fact(record, filing, source, fact)
                continue
            context = None if fact.context_id is None else contexts.get(fact.context_id)
            if fact.context_id is not None and context is None:
                raise SecFilingContentError(SecSourceErrorCode.SNAPSHOT_ANOMALY)
            record = SecXbrlFactRecord(
                id=uuid4(),
                filing_id=filing.id,
                source_id=source.id,
                context_id=None if context is None else context.id,
                accession=filing.accession,
                taxonomy=fact.taxonomy,
                concept=fact.concept,
                value=fact.value,
                unit=fact.unit,
                period_kind=fact.period.kind.value,
                instant=fact.period.instant,
                start_date=fact.period.start_date,
                end_date=fact.period.end_date,
                filed_date=fact.filed_date,
                form=fact.form.value,
                raw_context_id=fact.context_id,
                dimensions=dict(fact.dimensions),
                decimals=fact.decimals,
                scale=fact.scale,
                format=fact.format,
                is_custom=fact.is_custom,
                ordinal=fact.ordinal,
                locator_key=fact.locator_key,
            )
            session.add(record)
            existing[fact.locator_key] = record
        await session.flush()


def _canonical_filing(
    filing: SecFilingRecord,
    source: SecSubmissionSourceRecord,
) -> SecCanonicalFiling:
    return SecCanonicalFiling(
        id=filing.id,
        cik=filing.cik,
        accession=filing.accession,
        form=SecFilingForm(filing.form),
        report_date=filing.report_date,
        filed_date=filing.filed_date,
        accepted_at=filing.accepted_at,
        public_available_at=filing.public_available_at,
        primary_document=filing.primary_document,
        source_available_at=source.source_available_at,
    )


def _snapshot_reference(
    document: SecFilingDocumentRecord,
    filing_id: UUID,
    snapshot: SecSourceSnapshotRecord,
) -> SecFilingSnapshotReference:
    return SecFilingSnapshotReference(
        document_id=document.id,
        snapshot_id=snapshot.id,
        filing_id=filing_id,
        kind=SecFilingDocumentKind(document.document_kind),
        filename=document.filename,
        source_url=snapshot.source_url,
        source_version=snapshot.source_version,
        content_type=snapshot.content_type,
        content_sha256=snapshot.content_sha256.hex(),
        byte_size=snapshot.byte_size,
        retrieved_at=snapshot.retrieved_at,
        source_available_at=snapshot.source_available_at,
        status=SecFilingSnapshotStatus(snapshot.status),
        anomaly_code=snapshot.anomaly_code,
        object_bucket=snapshot.object_bucket,
        object_key=snapshot.object_key,
    )


def _workspace_import(
    record: WorkspaceSecImportRecord,
    version: DocumentVersionRecord,
) -> SecWorkspaceFilingImport:
    if version.status is DocumentVersionStatus.READY:
        status = SecFilingImportStatus.READY
        error_code = None
    elif version.status is DocumentVersionStatus.FAILED:
        status = SecFilingImportStatus.FAILED
        error_code = version.error_code or "ingestion_failed"
    elif version.status in {
        DocumentVersionStatus.CANCELLED,
        DocumentVersionStatus.DELETING,
        DocumentVersionStatus.DELETED,
    }:
        status = SecFilingImportStatus.CANCELLED
        error_code = None
    else:
        status = SecFilingImportStatus.QUEUED
        error_code = None
    return SecWorkspaceFilingImport(
        id=record.id,
        workspace_id=record.workspace_id,
        filing_id=record.filing_id,
        accession=record.accession,
        knowledge_base_id=record.knowledge_base_id,
        primary_snapshot_id=record.primary_snapshot_id,
        complete_submission_snapshot_id=record.complete_submission_snapshot_id,
        file_id=record.file_id,
        document_id=record.document_id,
        document_version_id=record.document_version_id,
        ingestion_job_id=record.ingestion_job_id,
        status=status,
        error_code=error_code,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _verify_source(
    record: SecXbrlSourceRecord,
    source: SecXbrlSourceSnapshot,
    *,
    object_key: str | None,
    object_bucket: str,
) -> None:
    expected_bucket = (
        object_bucket if source.source_kind is SecXbrlSourceKind.COMPANYFACTS_AGGREGATE else None
    )
    if (
        record.cik != source.cik
        or record.source_kind != source.source_kind.value
        or record.filing_snapshot_id != source.filing_snapshot_id
        or record.content_type != source.content_type
        or record.content_sha256.hex() != source.content_sha256
        or record.byte_size != source.byte_size
        or record.object_bucket != expected_bucket
        or record.object_key != object_key
        or record.adapter_version != source.adapter_version
    ):
        raise SecFilingContentError(SecSourceErrorCode.SNAPSHOT_ANOMALY)


def _verify_context(record: SecXbrlContextRecord, context: SecXbrlContextData) -> None:
    if (
        record.entity_identifier != context.entity_identifier
        or record.period_kind != context.period.kind.value
        or record.instant != context.period.instant
        or record.start_date != context.period.start_date
        or record.end_date != context.period.end_date
        or record.dimensions != dict(context.dimensions)
    ):
        raise SecFilingContentError(SecSourceErrorCode.SNAPSHOT_ANOMALY)


def _verify_fact(
    record: SecXbrlFactRecord,
    filing: SecFilingRecord,
    source: SecXbrlSourceRecord,
    fact: SecXbrlFactData,
) -> None:
    if (
        record.filing_id != filing.id
        or record.source_id != source.id
        or record.accession != filing.accession
        or record.taxonomy != fact.taxonomy
        or record.concept != fact.concept
        or record.value != fact.value
        or record.unit != fact.unit
        or record.period_kind != fact.period.kind.value
        or record.instant != fact.period.instant
        or record.start_date != fact.period.start_date
        or record.end_date != fact.period.end_date
        or record.filed_date != fact.filed_date
        or record.form != fact.form.value
        or record.raw_context_id != fact.context_id
        or record.dimensions != dict(fact.dimensions)
        or record.decimals != fact.decimals
        or record.scale != fact.scale
        or record.format != fact.format
        or record.is_custom != fact.is_custom
        or record.ordinal != fact.ordinal
    ):
        raise SecFilingContentError(SecSourceErrorCode.SNAPSHOT_ANOMALY)


def _fact(
    record: SecXbrlFactRecord,
    source: SecXbrlSourceRecord,
    filing: SecFilingRecord,
) -> SecXbrlFact:
    source_kind = SecXbrlSourceKind(source.source_kind)
    aggregate = source_kind is SecXbrlSourceKind.COMPANYFACTS_AGGREGATE
    return SecXbrlFact(
        id=record.id,
        filing_id=record.filing_id,
        source_id=record.source_id,
        source_snapshot_id=source.filing_snapshot_id,
        source_kind=source_kind,
        cik=source.cik,
        accession=record.accession,
        taxonomy=record.taxonomy,
        concept=record.concept,
        value=record.value,
        unit=record.unit,
        period=SecXbrlPeriod(
            SecXbrlPeriodKind(record.period_kind),
            instant=record.instant,
            start_date=record.start_date,
            end_date=record.end_date,
        ),
        filed_date=record.filed_date,
        form=SecFilingForm(record.form),
        context_id=record.raw_context_id,
        dimensions=tuple(sorted(record.dimensions.items())),
        decimals=record.decimals,
        scale=record.scale,
        format=record.format,
        is_custom=record.is_custom,
        ordinal=record.ordinal,
        locator_key=record.locator_key,
        source_url=source.source_url,
        source_version=source.source_version,
        source_content_sha256=source.content_sha256.hex(),
        source_available_at=source.source_available_at,
        retrieved_at=source.retrieved_at,
        unavailable_fields=(("context_id", "decimals", "dimensions", "scale") if aggregate else ()),
    )


def _sqlstate(error: SQLAlchemyError) -> str | None:
    original = getattr(error, "orig", None)
    return getattr(original, "sqlstate", None)
