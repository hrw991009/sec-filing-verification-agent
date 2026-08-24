"""PostgreSQL repository for private attachment lifecycle state."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from industry_platform.core.database import AsyncSessionFactory, safe_sqlstate
from industry_platform.modules.files.domain import (
    FileObjectPurpose,
    FileObjectStatus,
    ParsedAttachment,
)
from industry_platform.modules.files.models import FileObject
from industry_platform.modules.files.service import (
    DeletingFile,
    FileNotFoundError,
    FileServiceUnavailableError,
    FileSnapshot,
    FileStateConflictError,
    FileUploadExpiredError,
    ProcessingFile,
    ReadyFile,
    StagingFile,
)


@dataclass(frozen=True, slots=True)
class SqlAlchemyFileRepository:
    session_factory: AsyncSessionFactory

    async def create_staging(self, file: StagingFile) -> FileSnapshot:
        try:
            async with self.session_factory.begin() as session:
                record = FileObject(
                    id=file.file_id,
                    workspace_id=file.workspace_id,
                    created_by_user_id=file.created_by_user_id,
                    original_name=file.original_name,
                    declared_media_type=file.declared_media_type,
                    bucket=file.bucket,
                    staging_object_key=file.staging_object_key,
                    expected_size=file.expected_size,
                    expected_sha256=file.expected_sha256,
                    purpose=file.purpose,
                    status=FileObjectStatus.STAGING,
                    revision=0,
                    upload_expires_at=file.upload_expires_at,
                    created_at=file.created_at,
                    updated_at=file.created_at,
                )
                session.add(record)
                await session.flush()
                return _snapshot(record)
        except SQLAlchemyError as error:
            raise FileServiceUnavailableError(sqlstate=safe_sqlstate(error)) from None

    async def get(self, *, workspace_id: UUID, file_id: UUID) -> FileSnapshot:
        try:
            async with self.session_factory() as session:
                record = await session.scalar(
                    select(FileObject).where(
                        FileObject.id == file_id,
                        FileObject.workspace_id == workspace_id,
                        FileObject.purpose == FileObjectPurpose.CHAT_ATTACHMENT,
                    )
                )
                if record is None:
                    raise FileNotFoundError
                if record.status in {FileObjectStatus.DELETING, FileObjectStatus.DELETED}:
                    raise FileNotFoundError
                return _snapshot(record)
        except FileNotFoundError:
            raise
        except SQLAlchemyError as error:
            raise FileServiceUnavailableError(sqlstate=safe_sqlstate(error)) from None

    async def get_ready(self, *, workspace_id: UUID, file_id: UUID) -> ReadyFile:
        try:
            async with self.session_factory() as session:
                record = await session.scalar(
                    select(FileObject).where(
                        FileObject.id == file_id,
                        FileObject.workspace_id == workspace_id,
                        FileObject.purpose == FileObjectPurpose.CHAT_ATTACHMENT,
                    )
                )
                if record is None:
                    raise FileNotFoundError
                if record.status is not FileObjectStatus.READY or record.object_key is None:
                    raise FileStateConflictError
                return ReadyFile(
                    snapshot=_snapshot(record),
                    bucket=record.bucket,
                    object_key=record.object_key,
                )
        except (FileNotFoundError, FileStateConflictError):
            raise
        except SQLAlchemyError as error:
            raise FileServiceUnavailableError(sqlstate=safe_sqlstate(error)) from None

    async def claim_processing(
        self,
        *,
        workspace_id: UUID,
        file_id: UUID,
        claimed_at: datetime,
        stale_before: datetime,
    ) -> ProcessingFile | FileSnapshot:
        expired_object: tuple[str, str] | None = None
        try:
            async with self.session_factory.begin() as session:
                record = await session.scalar(
                    select(FileObject)
                    .where(
                        FileObject.id == file_id,
                        FileObject.workspace_id == workspace_id,
                        FileObject.purpose == FileObjectPurpose.CHAT_ATTACHMENT,
                    )
                    .with_for_update()
                )
                if record is None:
                    raise FileNotFoundError
                if record.status is FileObjectStatus.READY:
                    return _snapshot(record)
                if record.status is FileObjectStatus.STAGING and (
                    claimed_at >= record.upload_expires_at
                ):
                    record.status = FileObjectStatus.REJECTED
                    record.error_code = "upload_expired"
                    record.updated_at = claimed_at
                    record.revision += 1
                    expired_object = (record.bucket, record.staging_object_key)
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
                    record.updated_at = claimed_at
                    record.revision += 1
                    await session.flush()
                    result = ProcessingFile(
                        snapshot=_snapshot(record),
                        bucket=record.bucket,
                        staging_object_key=record.staging_object_key,
                        expected_sha256=record.expected_sha256,
                        revision=record.revision,
                    )
            if expired_object is not None:
                bucket, object_key = expired_object
                raise FileUploadExpiredError(bucket=bucket, object_key=object_key)
            return result
        except (FileNotFoundError, FileStateConflictError, FileUploadExpiredError):
            raise
        except SQLAlchemyError as error:
            raise FileServiceUnavailableError(sqlstate=safe_sqlstate(error)) from None

    async def mark_ready(
        self,
        *,
        file: ProcessingFile,
        parsed: ParsedAttachment,
        object_key: str,
        source_etag: str,
        ready_at: datetime,
    ) -> FileSnapshot:
        try:
            async with self.session_factory.begin() as session:
                record = await session.scalar(
                    select(FileObject)
                    .where(
                        FileObject.id == file.snapshot.file_id,
                        FileObject.workspace_id == file.snapshot.workspace_id,
                        FileObject.purpose == FileObjectPurpose.CHAT_ATTACHMENT,
                    )
                    .with_for_update()
                )
                if (
                    record is None
                    or record.status is not FileObjectStatus.PROCESSING
                    or record.revision != file.revision
                ):
                    raise FileStateConflictError
                record.original_name = parsed.display_filename
                record.detected_media_type = parsed.media_type
                record.kind = parsed.kind
                record.object_key = object_key
                record.actual_size = parsed.source_size_bytes
                record.safe_size = parsed.safe_size_bytes
                record.source_sha256 = parsed.source_sha256
                record.safe_sha256 = parsed.safe_sha256
                record.source_etag = source_etag
                record.extracted_text = parsed.extracted_text
                record.parser_version = parsed.parser_version
                record.sanitizer_version = parsed.sanitizer_version
                record.width = parsed.image.width if parsed.image is not None else None
                record.height = parsed.image.height if parsed.image is not None else None
                record.status = FileObjectStatus.READY
                record.error_code = None
                record.ready_at = ready_at
                record.updated_at = ready_at
                record.revision += 1
                await session.flush()
                return _snapshot(record)
        except FileStateConflictError:
            raise
        except SQLAlchemyError as error:
            raise FileServiceUnavailableError(sqlstate=safe_sqlstate(error)) from None

    async def mark_rejected(
        self,
        *,
        file: ProcessingFile,
        code: str,
        rejected_at: datetime,
    ) -> None:
        try:
            async with self.session_factory.begin() as session:
                record = await session.scalar(
                    select(FileObject)
                    .where(
                        FileObject.id == file.snapshot.file_id,
                        FileObject.workspace_id == file.snapshot.workspace_id,
                        FileObject.purpose == FileObjectPurpose.CHAT_ATTACHMENT,
                    )
                    .with_for_update()
                )
                if (
                    record is None
                    or record.status is not FileObjectStatus.PROCESSING
                    or record.revision != file.revision
                ):
                    raise FileStateConflictError
                record.status = FileObjectStatus.REJECTED
                record.error_code = code
                record.updated_at = rejected_at
                record.revision += 1
                await session.flush()
        except FileStateConflictError:
            raise
        except SQLAlchemyError as error:
            raise FileServiceUnavailableError(sqlstate=safe_sqlstate(error)) from None

    async def claim_deleting(
        self,
        *,
        workspace_id: UUID,
        file_id: UUID,
        requested_at: datetime,
    ) -> DeletingFile | FileSnapshot:
        try:
            async with self.session_factory.begin() as session:
                record = await session.scalar(
                    select(FileObject)
                    .where(
                        FileObject.id == file_id,
                        FileObject.workspace_id == workspace_id,
                        FileObject.purpose == FileObjectPurpose.CHAT_ATTACHMENT,
                    )
                    .with_for_update()
                )
                if record is None:
                    raise FileNotFoundError
                if record.status is FileObjectStatus.DELETED:
                    return _snapshot(record)
                if record.attached_at is not None or record.status in {
                    FileObjectStatus.PROCESSING,
                    FileObjectStatus.DELETING,
                }:
                    if record.status is not FileObjectStatus.DELETING:
                        raise FileStateConflictError
                else:
                    record.status = FileObjectStatus.DELETING
                    record.delete_requested_at = requested_at
                    record.updated_at = requested_at
                    record.revision += 1
                    await session.flush()
                return DeletingFile(
                    snapshot=_snapshot(record),
                    bucket=record.bucket,
                    staging_object_key=record.staging_object_key,
                    object_key=record.object_key,
                    revision=record.revision,
                )
        except (FileNotFoundError, FileStateConflictError):
            raise
        except SQLAlchemyError as error:
            raise FileServiceUnavailableError(sqlstate=safe_sqlstate(error)) from None

    async def mark_deleted(
        self,
        *,
        file: DeletingFile,
        deleted_at: datetime,
    ) -> FileSnapshot:
        try:
            async with self.session_factory.begin() as session:
                record = await session.scalar(
                    select(FileObject)
                    .where(
                        FileObject.id == file.snapshot.file_id,
                        FileObject.workspace_id == file.snapshot.workspace_id,
                        FileObject.purpose == FileObjectPurpose.CHAT_ATTACHMENT,
                    )
                    .with_for_update()
                )
                if (
                    record is None
                    or record.status is not FileObjectStatus.DELETING
                    or record.revision != file.revision
                ):
                    raise FileStateConflictError
                record.status = FileObjectStatus.DELETED
                record.deleted_at = deleted_at
                record.updated_at = deleted_at
                record.revision += 1
                await session.flush()
                return _snapshot(record)
        except FileStateConflictError:
            raise
        except SQLAlchemyError as error:
            raise FileServiceUnavailableError(sqlstate=safe_sqlstate(error)) from None


def _snapshot(record: FileObject) -> FileSnapshot:
    return FileSnapshot(
        file_id=record.id,
        workspace_id=record.workspace_id,
        original_name=record.original_name,
        declared_media_type=record.declared_media_type,
        detected_media_type=record.detected_media_type,
        kind=record.kind,
        status=record.status,
        expected_size=record.expected_size,
        purpose=record.purpose,
        actual_size=record.actual_size,
        safe_sha256=record.safe_sha256,
        parser_version=record.parser_version,
        sanitizer_version=record.sanitizer_version,
        width=record.width,
        height=record.height,
        error_code=record.error_code,
        upload_expires_at=record.upload_expires_at,
        ready_at=record.ready_at,
    )
