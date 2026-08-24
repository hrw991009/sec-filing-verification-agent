"""PostgreSQL persistence and atomic Knowledge acceptance."""

import hmac
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from industry_platform.core.database import AsyncSessionFactory, safe_sqlstate
from industry_platform.modules.files.domain import (
    AttachmentMediaType,
    FileObjectPurpose,
    FileObjectStatus,
)
from industry_platform.modules.files.models import FileObject
from industry_platform.modules.files.service import (
    FileNotFoundError,
    FileStateConflictError,
    FileUploadExpiredError,
)
from industry_platform.modules.jobs.adapters.sqlalchemy import SqlAlchemyJobWriter
from industry_platform.modules.jobs.domain import JobIdempotencyConflictError
from industry_platform.modules.jobs.models import Job, JobEvent, OutboxEvent
from industry_platform.modules.knowledge.domain import (
    KNOWLEDGE_SCHEMA_VERSION,
    ClaimedKnowledgeUpload,
    CreateKnowledgeBase,
    DeleteKnowledgeBase,
    Document,
    DocumentDetail,
    DocumentStatus,
    DocumentVersion,
    DocumentVersionStatus,
    DocumentView,
    KnowledgeAcceptanceReceipt,
    KnowledgeBase,
    KnowledgeBaseStatus,
    KnowledgeConflictError,
    KnowledgeIngestionEvent,
    KnowledgeNotEmptyError,
    KnowledgeNotFoundError,
    KnowledgePersistenceError,
    KnowledgeSource,
    PreparedKnowledgeAcceptance,
    StagingKnowledgeUpload,
    UpdateKnowledgeBase,
)
from industry_platform.modules.knowledge.models import (
    DocumentRecord,
    DocumentVersionRecord,
    KnowledgeBaseRecord,
)
from industry_platform.modules.knowledge.ports import KnowledgeAcceptanceWriter
from industry_platform.modules.workspaces.domain import WorkspaceScope


@dataclass(frozen=True, slots=True)
class SqlAlchemyKnowledgeRepository:
    session_factory: AsyncSessionFactory

    async def create_knowledge_base(
        self, scope: WorkspaceScope, command: CreateKnowledgeBase
    ) -> KnowledgeBase:
        try:
            async with self.session_factory.begin() as session:
                record = KnowledgeBaseRecord(
                    id=uuid4(),
                    workspace_id=scope.workspace_id,
                    created_by_user_id=scope.user_id,
                    name=command.name,
                    description=command.description,
                    status=KnowledgeBaseStatus.ACTIVE,
                    revision=1,
                )
                session.add(record)
                await session.flush()
                return _knowledge_base(record, document_count=0)
        except SQLAlchemyError as error:
            raise KnowledgePersistenceError(sqlstate=safe_sqlstate(error)) from None

    async def list_knowledge_bases(
        self, scope: WorkspaceScope, *, limit: int
    ) -> tuple[KnowledgeBase, ...]:
        count = (
            select(func.count(DocumentRecord.id))
            .where(
                DocumentRecord.knowledge_base_id == KnowledgeBaseRecord.id,
                DocumentRecord.workspace_id == KnowledgeBaseRecord.workspace_id,
                DocumentRecord.status == DocumentStatus.ACTIVE,
            )
            .correlate(KnowledgeBaseRecord)
            .scalar_subquery()
        )
        try:
            async with self.session_factory() as session:
                rows = (
                    await session.execute(
                        select(KnowledgeBaseRecord, count)
                        .where(
                            KnowledgeBaseRecord.workspace_id == scope.workspace_id,
                            KnowledgeBaseRecord.status == KnowledgeBaseStatus.ACTIVE,
                        )
                        .order_by(KnowledgeBaseRecord.updated_at.desc(), KnowledgeBaseRecord.id)
                        .limit(limit)
                    )
                ).all()
                return tuple(
                    _knowledge_base(record, document_count=int(document_count))
                    for record, document_count in rows
                )
        except SQLAlchemyError as error:
            raise KnowledgePersistenceError(sqlstate=safe_sqlstate(error)) from None

    async def get_knowledge_base(
        self, scope: WorkspaceScope, knowledge_base_id: UUID
    ) -> KnowledgeBase:
        try:
            async with self.session_factory() as session:
                record = await session.scalar(
                    select(KnowledgeBaseRecord).where(
                        KnowledgeBaseRecord.id == knowledge_base_id,
                        KnowledgeBaseRecord.workspace_id == scope.workspace_id,
                        KnowledgeBaseRecord.status == KnowledgeBaseStatus.ACTIVE,
                    )
                )
                if record is None:
                    raise KnowledgeNotFoundError
                count = await session.scalar(
                    select(func.count(DocumentRecord.id)).where(
                        DocumentRecord.knowledge_base_id == record.id,
                        DocumentRecord.workspace_id == scope.workspace_id,
                        DocumentRecord.status == DocumentStatus.ACTIVE,
                    )
                )
                return _knowledge_base(record, document_count=int(count or 0))
        except KnowledgeNotFoundError:
            raise
        except SQLAlchemyError as error:
            raise KnowledgePersistenceError(sqlstate=safe_sqlstate(error)) from None

    async def update_knowledge_base(
        self, scope: WorkspaceScope, command: UpdateKnowledgeBase
    ) -> KnowledgeBase:
        try:
            async with self.session_factory.begin() as session:
                record = await session.scalar(
                    select(KnowledgeBaseRecord)
                    .where(
                        KnowledgeBaseRecord.id == command.knowledge_base_id,
                        KnowledgeBaseRecord.workspace_id == scope.workspace_id,
                        KnowledgeBaseRecord.status == KnowledgeBaseStatus.ACTIVE,
                    )
                    .with_for_update()
                )
                if record is None:
                    raise KnowledgeNotFoundError
                if record.revision != command.expected_revision:
                    raise KnowledgeConflictError
                record.name = command.name
                record.description = command.description
                record.revision += 1
                record.updated_at = datetime.now(UTC)
                count = await session.scalar(
                    select(func.count(DocumentRecord.id)).where(
                        DocumentRecord.knowledge_base_id == record.id,
                        DocumentRecord.workspace_id == scope.workspace_id,
                        DocumentRecord.status == DocumentStatus.ACTIVE,
                    )
                )
                await session.flush()
                return _knowledge_base(record, document_count=int(count or 0))
        except (KnowledgeConflictError, KnowledgeNotFoundError):
            raise
        except SQLAlchemyError as error:
            raise KnowledgePersistenceError(sqlstate=safe_sqlstate(error)) from None

    async def delete_empty_knowledge_base(
        self, scope: WorkspaceScope, command: DeleteKnowledgeBase
    ) -> None:
        try:
            async with self.session_factory.begin() as session:
                record = await session.scalar(
                    select(KnowledgeBaseRecord)
                    .where(
                        KnowledgeBaseRecord.id == command.knowledge_base_id,
                        KnowledgeBaseRecord.workspace_id == scope.workspace_id,
                        KnowledgeBaseRecord.status == KnowledgeBaseStatus.ACTIVE,
                    )
                    .with_for_update()
                )
                if record is None:
                    raise KnowledgeNotFoundError
                if record.revision != command.expected_revision:
                    raise KnowledgeConflictError
                count = await session.scalar(
                    select(func.count(DocumentRecord.id)).where(
                        DocumentRecord.knowledge_base_id == record.id,
                        DocumentRecord.workspace_id == scope.workspace_id,
                        DocumentRecord.status == DocumentStatus.ACTIVE,
                    )
                )
                if count:
                    raise KnowledgeNotEmptyError
                record.status = KnowledgeBaseStatus.DELETED
                record.deleted_at = datetime.now(UTC)
                record.updated_at = record.deleted_at
                record.revision += 1
        except (KnowledgeConflictError, KnowledgeNotEmptyError, KnowledgeNotFoundError):
            raise
        except SQLAlchemyError as error:
            raise KnowledgePersistenceError(sqlstate=safe_sqlstate(error)) from None

    async def create_staging_upload(self, upload: StagingKnowledgeUpload) -> None:
        try:
            async with self.session_factory.begin() as session:
                knowledge_base = await session.scalar(
                    select(KnowledgeBaseRecord.id).where(
                        KnowledgeBaseRecord.id == upload.knowledge_base_id,
                        KnowledgeBaseRecord.workspace_id == upload.workspace_id,
                        KnowledgeBaseRecord.status == KnowledgeBaseStatus.ACTIVE,
                    )
                )
                if knowledge_base is None:
                    raise KnowledgeNotFoundError
                session.add(
                    FileObject(
                        id=upload.file_id,
                        workspace_id=upload.workspace_id,
                        created_by_user_id=upload.created_by_user_id,
                        purpose=FileObjectPurpose.KNOWLEDGE_SOURCE,
                        knowledge_base_id=upload.knowledge_base_id,
                        original_name=upload.original_name,
                        declared_media_type=upload.declared_media_type.value,
                        bucket=upload.bucket,
                        staging_object_key=upload.staging_key,
                        expected_size=upload.expected_size,
                        expected_sha256=upload.expected_sha256,
                        status=FileObjectStatus.STAGING,
                        revision=0,
                        upload_expires_at=upload.expires_at,
                        created_at=upload.created_at,
                        updated_at=upload.created_at,
                    )
                )
                await session.flush()
        except KnowledgeNotFoundError:
            raise
        except SQLAlchemyError as error:
            raise KnowledgePersistenceError(sqlstate=safe_sqlstate(error)) from None

    async def existing_receipt(
        self,
        *,
        workspace_id: UUID,
        knowledge_base_id: UUID,
        file_id: UUID,
        idempotency_key_hash: bytes,
        request_fingerprint: bytes,
    ) -> KnowledgeAcceptanceReceipt | None:
        try:
            async with self.session_factory() as session:
                return await _existing_receipt(
                    session,
                    workspace_id=workspace_id,
                    knowledge_base_id=knowledge_base_id,
                    file_id=file_id,
                    idempotency_key_hash=idempotency_key_hash,
                    request_fingerprint=request_fingerprint,
                )
        except (KnowledgeConflictError, KnowledgeNotFoundError):
            raise
        except SQLAlchemyError as error:
            raise KnowledgePersistenceError(sqlstate=safe_sqlstate(error)) from None

    async def claim_upload(
        self,
        *,
        workspace_id: UUID,
        knowledge_base_id: UUID,
        file_id: UUID,
        claimed_at: datetime,
        stale_before: datetime,
    ) -> ClaimedKnowledgeUpload:
        expired: tuple[str, str] | None = None
        claim: ClaimedKnowledgeUpload | None = None
        try:
            async with self.session_factory.begin() as session:
                record = await session.scalar(
                    select(FileObject)
                    .where(
                        FileObject.id == file_id,
                        FileObject.workspace_id == workspace_id,
                        FileObject.knowledge_base_id == knowledge_base_id,
                        FileObject.purpose == FileObjectPurpose.KNOWLEDGE_SOURCE,
                    )
                    .with_for_update()
                )
                if record is None:
                    raise FileNotFoundError
                if (
                    record.status is FileObjectStatus.STAGING
                    and claimed_at >= record.upload_expires_at
                ):
                    record.status = FileObjectStatus.REJECTED
                    record.error_code = "upload_expired"
                    record.revision += 1
                    record.updated_at = claimed_at
                    expired = (record.bucket, record.staging_object_key)
                elif (
                    record.status is FileObjectStatus.PROCESSING
                    and (
                        record.processing_started_at is None
                        or record.processing_started_at > stale_before
                    )
                ) or record.status not in {
                    FileObjectStatus.STAGING,
                    FileObjectStatus.PROCESSING,
                    FileObjectStatus.FAILED,
                }:
                    raise FileStateConflictError
                else:
                    record.status = FileObjectStatus.PROCESSING
                    record.error_code = None
                    record.processing_started_at = claimed_at
                    record.revision += 1
                    record.updated_at = claimed_at
                    await session.flush()
                    claim = _claim(record)
            if expired is not None:
                raise FileUploadExpiredError(bucket=expired[0], object_key=expired[1])
            if claim is None:
                raise KnowledgePersistenceError
            return claim
        except (FileNotFoundError, FileStateConflictError, FileUploadExpiredError):
            raise
        except SQLAlchemyError as error:
            raise KnowledgePersistenceError(sqlstate=safe_sqlstate(error)) from None

    async def reject_upload(self, claim: ClaimedKnowledgeUpload, *, code: str) -> None:
        try:
            async with self.session_factory.begin() as session:
                record = await session.scalar(
                    select(FileObject)
                    .where(
                        FileObject.id == claim.file_id,
                        FileObject.workspace_id == claim.workspace_id,
                        FileObject.knowledge_base_id == claim.knowledge_base_id,
                    )
                    .with_for_update()
                )
                if (
                    record is None
                    or record.status is not FileObjectStatus.PROCESSING
                    or record.revision != claim.revision
                ):
                    raise FileStateConflictError
                record.status = FileObjectStatus.REJECTED
                record.error_code = code
                record.revision += 1
                record.updated_at = datetime.now(UTC)
        except FileStateConflictError:
            raise
        except SQLAlchemyError as error:
            raise KnowledgePersistenceError(sqlstate=safe_sqlstate(error)) from None

    async def list_documents(
        self, scope: WorkspaceScope, *, knowledge_base_id: UUID, limit: int
    ) -> tuple[DocumentView, ...]:
        try:
            async with self.session_factory() as session:
                await _require_knowledge_base(session, scope.workspace_id, knowledge_base_id)
                rows = (
                    await session.execute(
                        select(DocumentRecord, DocumentVersionRecord, FileObject)
                        .join(
                            DocumentVersionRecord,
                            (DocumentVersionRecord.document_id == DocumentRecord.id)
                            & (
                                DocumentVersionRecord.version
                                == DocumentRecord.latest_version_number
                            ),
                        )
                        .join(FileObject, FileObject.id == DocumentVersionRecord.file_object_id)
                        .where(
                            DocumentRecord.workspace_id == scope.workspace_id,
                            DocumentRecord.knowledge_base_id == knowledge_base_id,
                            DocumentRecord.status == DocumentStatus.ACTIVE,
                        )
                        .order_by(DocumentRecord.updated_at.desc(), DocumentRecord.id)
                        .limit(limit)
                    )
                ).all()
                return tuple(
                    _document_view(document, version, source) for document, version, source in rows
                )
        except KnowledgeNotFoundError:
            raise
        except SQLAlchemyError as error:
            raise KnowledgePersistenceError(sqlstate=safe_sqlstate(error)) from None

    async def get_document(
        self, scope: WorkspaceScope, *, knowledge_base_id: UUID, document_id: UUID
    ) -> DocumentDetail:
        try:
            async with self.session_factory() as session:
                document = await session.scalar(
                    select(DocumentRecord).where(
                        DocumentRecord.id == document_id,
                        DocumentRecord.workspace_id == scope.workspace_id,
                        DocumentRecord.knowledge_base_id == knowledge_base_id,
                        DocumentRecord.status == DocumentStatus.ACTIVE,
                    )
                )
                if document is None:
                    raise KnowledgeNotFoundError
                rows = (
                    await session.execute(
                        select(DocumentVersionRecord, FileObject)
                        .join(FileObject, FileObject.id == DocumentVersionRecord.file_object_id)
                        .where(
                            DocumentVersionRecord.document_id == document_id,
                            DocumentVersionRecord.workspace_id == scope.workspace_id,
                        )
                        .order_by(DocumentVersionRecord.version.desc())
                    )
                ).all()
                return DocumentDetail(
                    document=_document(document),
                    versions=tuple(_version(version) for version, _source in rows),
                    sources=tuple(_source(source) for _version_record, source in rows),
                )
        except KnowledgeNotFoundError:
            raise
        except SQLAlchemyError as error:
            raise KnowledgePersistenceError(sqlstate=safe_sqlstate(error)) from None

    async def list_ingestion_events(
        self,
        scope: WorkspaceScope,
        *,
        knowledge_base_id: UUID,
        document_id: UUID,
        version_id: UUID,
    ) -> tuple[KnowledgeIngestionEvent, ...]:
        try:
            async with self.session_factory() as session:
                job_id = await session.scalar(
                    select(DocumentVersionRecord.ingestion_job_id).where(
                        DocumentVersionRecord.id == version_id,
                        DocumentVersionRecord.document_id == document_id,
                        DocumentVersionRecord.knowledge_base_id == knowledge_base_id,
                        DocumentVersionRecord.workspace_id == scope.workspace_id,
                    )
                )
                if job_id is None:
                    raise KnowledgeNotFoundError
                records = tuple(
                    await session.scalars(
                        select(JobEvent)
                        .where(JobEvent.job_id == job_id)
                        .order_by(JobEvent.occurred_at, JobEvent.id)
                    )
                )
                return tuple(
                    KnowledgeIngestionEvent(
                        id=record.id,
                        event_type=record.event_type.value,
                        generation=record.generation,
                        event_sequence=record.event_sequence,
                        occurred_at=record.occurred_at,
                    )
                    for record in records
                )
        except KnowledgeNotFoundError:
            raise
        except SQLAlchemyError as error:
            raise KnowledgePersistenceError(sqlstate=safe_sqlstate(error)) from None


@dataclass(slots=True)
class SqlAlchemyKnowledgeAcceptanceWriter:
    session: AsyncSession

    async def submit(self, prepared: PreparedKnowledgeAcceptance) -> KnowledgeAcceptanceReceipt:
        existing = await _existing_receipt(
            self.session,
            workspace_id=prepared.workspace_id,
            knowledge_base_id=prepared.knowledge_base_id,
            file_id=prepared.upload.claim.file_id,
            idempotency_key_hash=prepared.idempotency_key_hash,
            request_fingerprint=prepared.request_fingerprint,
        )
        if existing is not None:
            return existing

        knowledge_base = await self.session.scalar(
            select(KnowledgeBaseRecord)
            .where(
                KnowledgeBaseRecord.id == prepared.knowledge_base_id,
                KnowledgeBaseRecord.workspace_id == prepared.workspace_id,
                KnowledgeBaseRecord.status == KnowledgeBaseStatus.ACTIVE,
            )
            .with_for_update()
        )
        if knowledge_base is None:
            raise KnowledgeNotFoundError
        file = await self.session.scalar(
            select(FileObject)
            .where(
                FileObject.id == prepared.upload.claim.file_id,
                FileObject.workspace_id == prepared.workspace_id,
                FileObject.knowledge_base_id == prepared.knowledge_base_id,
                FileObject.purpose == FileObjectPurpose.KNOWLEDGE_SOURCE,
            )
            .with_for_update()
        )
        if (
            file is None
            or file.status is not FileObjectStatus.PROCESSING
            or file.revision != prepared.upload.claim.revision
        ):
            raise FileStateConflictError

        job = await SqlAlchemyJobWriter(self.session).submit(prepared.job)
        if not job.created:
            existing = await _existing_receipt(
                self.session,
                workspace_id=prepared.workspace_id,
                knowledge_base_id=prepared.knowledge_base_id,
                file_id=prepared.upload.claim.file_id,
                idempotency_key_hash=prepared.idempotency_key_hash,
                request_fingerprint=prepared.request_fingerprint,
            )
            if existing is None:
                raise KnowledgeConflictError
            return existing

        file.detected_media_type = prepared.upload.media_type
        file.kind = prepared.upload.kind
        file.object_key = prepared.upload.final_key
        file.actual_size = prepared.upload.actual_size
        file.safe_size = prepared.upload.actual_size
        file.source_sha256 = prepared.upload.sha256
        file.safe_sha256 = prepared.upload.sha256
        file.source_etag = prepared.upload.source_etag
        file.status = FileObjectStatus.READY
        file.ready_at = prepared.accepted_at
        file.updated_at = prepared.accepted_at
        file.revision += 1

        document_record = DocumentRecord(
            id=prepared.document_id,
            workspace_id=prepared.workspace_id,
            knowledge_base_id=prepared.knowledge_base_id,
            created_by_user_id=prepared.created_by_user_id,
            title=prepared.title,
            status=DocumentStatus.ACTIVE,
            active_version_id=None,
            latest_version_number=1,
            revision=1,
            created_at=prepared.accepted_at,
            updated_at=prepared.accepted_at,
        )
        version_record = DocumentVersionRecord(
            id=prepared.version_id,
            workspace_id=prepared.workspace_id,
            knowledge_base_id=prepared.knowledge_base_id,
            document_id=prepared.document_id,
            file_object_id=prepared.upload.claim.file_id,
            ingestion_job_id=job.job_id,
            created_by_user_id=prepared.created_by_user_id,
            version=1,
            status=DocumentVersionStatus.QUEUED,
            ingestion_schema_version=KNOWLEDGE_SCHEMA_VERSION,
            revision=1,
            idempotency_key_hash=prepared.idempotency_key_hash,
            request_fingerprint=prepared.request_fingerprint,
            uploaded_at=prepared.accepted_at,
            queued_at=prepared.accepted_at,
            created_at=prepared.accepted_at,
            updated_at=prepared.accepted_at,
        )
        self.session.add(document_record)
        self.session.add(version_record)
        await self.session.flush()
        return KnowledgeAcceptanceReceipt(
            document=_document(document_record),
            version=_version(version_record),
            source=_source(file),
            job_id=job.job_id,
            job_status=job.status,
            outbox_event_id=job.outbox_event_id,
            created=True,
        )


@dataclass(frozen=True, slots=True)
class SqlAlchemyKnowledgeAcceptanceTransactionFactory:
    session_factory: AsyncSessionFactory

    def __call__(self) -> AbstractAsyncContextManager[KnowledgeAcceptanceWriter]:
        return self._transaction()

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[KnowledgeAcceptanceWriter]:
        try:
            async with self.session_factory.begin() as session:
                yield SqlAlchemyKnowledgeAcceptanceWriter(session)
        except (
            FileStateConflictError,
            KnowledgeConflictError,
            KnowledgeNotFoundError,
        ):
            raise
        except JobIdempotencyConflictError:
            raise KnowledgeConflictError from None
        except SQLAlchemyError as error:
            raise KnowledgePersistenceError(sqlstate=safe_sqlstate(error)) from None


async def _require_knowledge_base(
    session: AsyncSession, workspace_id: UUID, knowledge_base_id: UUID
) -> None:
    exists = await session.scalar(
        select(KnowledgeBaseRecord.id).where(
            KnowledgeBaseRecord.id == knowledge_base_id,
            KnowledgeBaseRecord.workspace_id == workspace_id,
            KnowledgeBaseRecord.status == KnowledgeBaseStatus.ACTIVE,
        )
    )
    if exists is None:
        raise KnowledgeNotFoundError


async def _existing_receipt(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    knowledge_base_id: UUID,
    file_id: UUID,
    idempotency_key_hash: bytes,
    request_fingerprint: bytes,
) -> KnowledgeAcceptanceReceipt | None:
    rows = (
        await session.execute(
            select(DocumentVersionRecord, DocumentRecord, FileObject, Job, OutboxEvent)
            .join(DocumentRecord, DocumentRecord.id == DocumentVersionRecord.document_id)
            .join(FileObject, FileObject.id == DocumentVersionRecord.file_object_id)
            .join(Job, Job.id == DocumentVersionRecord.ingestion_job_id)
            .join(
                OutboxEvent,
                (OutboxEvent.source_job_id == Job.id) & (OutboxEvent.job_dispatch_generation == 1),
            )
            .where(
                DocumentVersionRecord.workspace_id == workspace_id,
                or_(
                    DocumentVersionRecord.file_object_id == file_id,
                    DocumentVersionRecord.idempotency_key_hash == idempotency_key_hash,
                ),
            )
        )
    ).all()
    if not rows:
        return None

    file_row = next((row for row in rows if row[0].file_object_id == file_id), None)
    key_row = next(
        (
            row
            for row in rows
            if hmac.compare_digest(row[0].idempotency_key_hash, idempotency_key_hash)
        ),
        None,
    )
    if file_row is None:
        raise KnowledgeConflictError
    version, document, source, job, outbox = file_row
    if version.knowledge_base_id != knowledge_base_id:
        raise KnowledgeNotFoundError
    if key_row is not None and key_row[0].id != version.id:
        raise KnowledgeConflictError
    if version.file_object_id != file_id or not hmac.compare_digest(
        version.request_fingerprint, request_fingerprint
    ):
        raise KnowledgeConflictError
    return KnowledgeAcceptanceReceipt(
        document=_document(document),
        version=_version(version),
        source=_source(source),
        job_id=job.id,
        job_status=job.status,
        outbox_event_id=outbox.id,
        created=False,
    )


def _knowledge_base(record: KnowledgeBaseRecord, *, document_count: int) -> KnowledgeBase:
    return KnowledgeBase(
        id=record.id,
        workspace_id=record.workspace_id,
        created_by_user_id=record.created_by_user_id,
        name=record.name,
        description=record.description,
        status=record.status,
        document_count=document_count,
        revision=record.revision,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _document(record: DocumentRecord) -> Document:
    return Document(
        id=record.id,
        workspace_id=record.workspace_id,
        knowledge_base_id=record.knowledge_base_id,
        created_by_user_id=record.created_by_user_id,
        title=record.title,
        status=record.status,
        active_version_id=record.active_version_id,
        latest_version_number=record.latest_version_number,
        revision=record.revision,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _version(record: DocumentVersionRecord) -> DocumentVersion:
    return DocumentVersion(
        id=record.id,
        document_id=record.document_id,
        workspace_id=record.workspace_id,
        knowledge_base_id=record.knowledge_base_id,
        file_id=record.file_object_id,
        ingestion_job_id=record.ingestion_job_id,
        version=record.version,
        status=record.status,
        revision=record.revision,
        error_code=record.error_code,
        uploaded_at=record.uploaded_at,
        queued_at=record.queued_at,
        processing_started_at=record.processing_started_at,
        ready_at=record.ready_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _source(record: FileObject) -> KnowledgeSource:
    if record.actual_size is None:
        raise KnowledgePersistenceError
    return KnowledgeSource(
        file_id=record.id,
        original_name=record.original_name,
        declared_media_type=AttachmentMediaType(record.declared_media_type),
        expected_size=record.expected_size,
        actual_size=record.actual_size,
    )


def _claim(record: FileObject) -> ClaimedKnowledgeUpload:
    if record.knowledge_base_id is None:
        raise KnowledgePersistenceError
    return ClaimedKnowledgeUpload(
        file_id=record.id,
        workspace_id=record.workspace_id,
        knowledge_base_id=record.knowledge_base_id,
        created_by_user_id=record.created_by_user_id,
        original_name=record.original_name,
        declared_media_type=AttachmentMediaType(record.declared_media_type),
        bucket=record.bucket,
        staging_key=record.staging_object_key,
        expected_size=record.expected_size,
        expected_sha256=record.expected_sha256,
        revision=record.revision,
    )


def _document_view(
    document: DocumentRecord, version: DocumentVersionRecord, source: FileObject
) -> DocumentView:
    return DocumentView(
        document=_document(document),
        latest_version=_version(version),
        source=_source(source),
    )
