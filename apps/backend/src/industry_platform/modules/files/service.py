"""Authorized application service for the bounded Day 2 attachment lifecycle."""

import hashlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol
from uuid import UUID, uuid4

from anyio import to_thread

from industry_platform.modules.files.domain import (
    DEFAULT_ATTACHMENT_LIMITS,
    AttachmentKind,
    AttachmentMediaType,
    AttachmentParserPort,
    AttachmentValidationCode,
    AttachmentValidationError,
    FileObjectPurpose,
    FileObjectStatus,
    ParseAttachmentRequest,
    ParsedAttachment,
    normalize_media_type,
    require_matching_extension,
    sanitize_display_filename,
)
from industry_platform.modules.files.ports import (
    FileObjectNotFoundError,
    FileObjectStoreError,
    PrivateFileObjectStore,
)
from industry_platform.modules.workspaces.domain import (
    WorkspaceAccessDeniedError,
    WorkspaceAction,
    WorkspaceScope,
)
from industry_platform.modules.workspaces.policy import scope_allows

_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_PROCESSING_LEASE = timedelta(minutes=5)
_CHAT_ATTACHMENT_MEDIA_TYPES = frozenset(
    {
        AttachmentMediaType.TEXT_PLAIN,
        AttachmentMediaType.TEXT_MARKDOWN,
        AttachmentMediaType.IMAGE_PNG,
        AttachmentMediaType.IMAGE_JPEG,
        AttachmentMediaType.IMAGE_WEBP,
    }
)


class FileNotFoundError(LookupError):
    """No visible file exists for the requested workspace and ID."""


class FileUploadExpiredError(RuntimeError):
    """The short-lived upload intent expired before completion."""

    def __init__(self, *, bucket: str, object_key: str) -> None:
        super().__init__("The file upload intent expired")
        self.bucket = bucket
        self.object_key = object_key


class FileStateConflictError(RuntimeError):
    """The file is being processed, attached, or otherwise cannot transition."""


class FileValidationRejectedError(RuntimeError):
    """The uploaded bytes failed a stable, non-sensitive validation rule."""

    def __init__(self, code: AttachmentValidationCode) -> None:
        self.code = code
        super().__init__("Attachment validation failed")


class FileStorageConfigurationError(RuntimeError):
    """Private storage is deliberately unavailable until fully configured."""


class FileServiceUnavailableError(RuntimeError):
    """A sanitized persistence or object-store outage."""

    def __init__(self, *, sqlstate: str | None = None) -> None:
        self.sqlstate = sqlstate
        super().__init__("File service is temporarily unavailable")


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    """Safe file metadata returned to application and HTTP layers."""

    file_id: UUID
    workspace_id: UUID
    original_name: str
    declared_media_type: str
    status: FileObjectStatus
    expected_size: int
    upload_expires_at: datetime
    purpose: FileObjectPurpose = FileObjectPurpose.CHAT_ATTACHMENT
    detected_media_type: AttachmentMediaType | None = None
    kind: AttachmentKind | None = None
    actual_size: int | None = None
    safe_sha256: str | None = None
    parser_version: str | None = None
    sanitizer_version: str | None = None
    width: int | None = None
    height: int | None = None
    error_code: str | None = None
    ready_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class StagingFile:
    file_id: UUID
    workspace_id: UUID
    created_by_user_id: UUID
    original_name: str
    declared_media_type: str
    bucket: str
    staging_object_key: str
    expected_size: int
    expected_sha256: str
    upload_expires_at: datetime
    created_at: datetime
    purpose: FileObjectPurpose = FileObjectPurpose.CHAT_ATTACHMENT


@dataclass(frozen=True, slots=True)
class ProcessingFile:
    snapshot: FileSnapshot
    bucket: str = field(repr=False)
    staging_object_key: str = field(repr=False)
    expected_sha256: str = field(repr=False)
    revision: int


@dataclass(frozen=True, slots=True)
class DeletingFile:
    snapshot: FileSnapshot
    bucket: str = field(repr=False)
    staging_object_key: str = field(repr=False)
    object_key: str | None = field(default=None, repr=False)
    revision: int = 0


@dataclass(frozen=True, slots=True)
class ReadyFile:
    snapshot: FileSnapshot
    bucket: str = field(repr=False)
    object_key: str = field(repr=False)


class FileRepository(Protocol):
    async def create_staging(self, file: StagingFile) -> FileSnapshot: ...

    async def get(self, *, workspace_id: UUID, file_id: UUID) -> FileSnapshot: ...

    async def get_ready(self, *, workspace_id: UUID, file_id: UUID) -> ReadyFile: ...

    async def claim_processing(
        self,
        *,
        workspace_id: UUID,
        file_id: UUID,
        claimed_at: datetime,
        stale_before: datetime,
    ) -> ProcessingFile | FileSnapshot: ...

    async def mark_ready(
        self,
        *,
        file: ProcessingFile,
        parsed: ParsedAttachment,
        object_key: str,
        source_etag: str,
        ready_at: datetime,
    ) -> FileSnapshot: ...

    async def mark_rejected(
        self,
        *,
        file: ProcessingFile,
        code: str,
        rejected_at: datetime,
    ) -> None: ...

    async def claim_deleting(
        self,
        *,
        workspace_id: UUID,
        file_id: UUID,
        requested_at: datetime,
    ) -> DeletingFile | FileSnapshot: ...

    async def mark_deleted(
        self,
        *,
        file: DeletingFile,
        deleted_at: datetime,
    ) -> FileSnapshot: ...


@dataclass(frozen=True, slots=True)
class CreateFileUpload:
    original_name: str
    declared_media_type: str
    expected_size: int
    expected_sha256: str


@dataclass(frozen=True, slots=True)
class FileUploadTicket:
    file: FileSnapshot
    method: Literal["POST"]
    url: str = field(repr=False)
    fields: Mapping[str, str] = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class FileDownloadTicket:
    url: str = field(repr=False)
    expires_at: datetime


type UtcClock = Callable[[], datetime]
type IdSource = Callable[[], UUID]


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class FileApplicationService:
    repository: FileRepository
    object_store: PrivateFileObjectStore | None = field(repr=False)
    parser: AttachmentParserPort = field(repr=False)
    bucket: str | None
    presign_expiry_seconds: int = 600
    clock: UtcClock = utc_now
    id_source: IdSource = uuid4

    def _configured_store(self) -> tuple[PrivateFileObjectStore, str]:
        if self.object_store is None or self.bucket is None:
            raise FileStorageConfigurationError
        return self.object_store, self.bucket

    async def create_upload(
        self,
        scope: WorkspaceScope,
        command: CreateFileUpload,
    ) -> FileUploadTicket:
        self._require_write(scope)
        store, bucket = self._configured_store()
        try:
            display_name = sanitize_display_filename(command.original_name)
            media_type = normalize_media_type(command.declared_media_type)
            if media_type not in _CHAT_ATTACHMENT_MEDIA_TYPES:
                raise AttachmentValidationError(AttachmentValidationCode.UNSUPPORTED_MEDIA_TYPE)
            require_matching_extension(display_name, media_type)
        except AttachmentValidationError as error:
            raise FileValidationRejectedError(error.code) from None
        maximum = DEFAULT_ATTACHMENT_LIMITS.maximum_bytes_for(media_type)
        if isinstance(command.expected_size, bool) or not 1 <= command.expected_size <= maximum:
            raise FileValidationRejectedError(
                AttachmentValidationCode.FILE_TOO_LARGE
                if command.expected_size > maximum
                else AttachmentValidationCode.EMPTY_FILE
            )
        if not _SHA256_PATTERN.fullmatch(command.expected_sha256):
            raise FileValidationRejectedError(AttachmentValidationCode.CHECKSUM_MISMATCH)

        now = self.clock()
        expiry = now + timedelta(seconds=self.presign_expiry_seconds)
        file_id = self.id_source()
        object_key = f"staging/{scope.workspace_id}/{file_id}/{self.id_source()}"
        snapshot = await self.repository.create_staging(
            StagingFile(
                file_id=file_id,
                workspace_id=scope.workspace_id,
                created_by_user_id=scope.user_id,
                original_name=display_name,
                declared_media_type=media_type.value,
                bucket=bucket,
                staging_object_key=object_key,
                expected_size=command.expected_size,
                expected_sha256=command.expected_sha256,
                upload_expires_at=expiry,
                created_at=now,
            )
        )
        try:
            signed = await store.presign_post(
                bucket=bucket,
                object_key=object_key,
                content_type=media_type.value,
                exact_size=command.expected_size,
                expires_at=expiry,
            )
        except FileObjectStoreError:
            raise FileServiceUnavailableError from None
        return FileUploadTicket(
            file=snapshot,
            method="POST",
            url=signed.url,
            fields=signed.fields,
            expires_at=signed.expires_at,
        )

    async def complete_upload(
        self,
        scope: WorkspaceScope,
        file_id: UUID,
    ) -> FileSnapshot:
        self._require_write(scope)
        store, _bucket = self._configured_store()
        now = self.clock()
        try:
            processing = await self.repository.claim_processing(
                workspace_id=scope.workspace_id,
                file_id=file_id,
                claimed_at=now,
                stale_before=now - _PROCESSING_LEASE,
            )
        except FileUploadExpiredError as error:
            await self._remove_best_effort(
                store,
                bucket=error.bucket,
                object_key=error.object_key,
            )
            raise
        if isinstance(processing, FileSnapshot):
            return processing
        final_key: str | None = None
        try:
            stat = await store.stat(
                bucket=processing.bucket,
                object_key=processing.staging_object_key,
            )
            if stat.size != processing.snapshot.expected_size:
                raise AttachmentValidationError(AttachmentValidationCode.SIZE_MISMATCH)
            if stat.content_type != processing.snapshot.declared_media_type:
                raise AttachmentValidationError(AttachmentValidationCode.UPLOAD_METADATA_MISMATCH)
            source = await store.read_bounded(
                bucket=processing.bucket,
                object_key=processing.staging_object_key,
                maximum_bytes=processing.snapshot.expected_size,
            )
            if hashlib.sha256(source).hexdigest() != processing.expected_sha256:
                raise AttachmentValidationError(AttachmentValidationCode.CHECKSUM_MISMATCH)
            parsed = await to_thread.run_sync(
                self.parser.parse,
                ParseAttachmentRequest(
                    filename=processing.snapshot.original_name,
                    declared_media_type=processing.snapshot.declared_media_type,
                    content=source,
                    expected_size_bytes=processing.snapshot.expected_size,
                ),
            )
            # A stable content-addressed key makes a retry converge on the same
            # object even when PostgreSQL's commit result is temporarily unknown.
            final_key = (
                f"ready/{processing.snapshot.workspace_id}/"
                f"{processing.snapshot.file_id}/{parsed.safe_sha256}"
            )
            await store.put_private(
                bucket=processing.bucket,
                object_key=final_key,
                content_type=parsed.media_type.value,
                content=parsed.safe_bytes,
            )
            ready = await self.repository.mark_ready(
                file=processing,
                parsed=parsed,
                object_key=final_key,
                source_etag=stat.etag,
                ready_at=self.clock(),
            )
            await self._remove_best_effort(
                store,
                bucket=processing.bucket,
                object_key=processing.staging_object_key,
            )
            return ready
        except AttachmentValidationError as error:
            await self.repository.mark_rejected(
                file=processing,
                code=error.code.value,
                rejected_at=self.clock(),
            )
            await self._remove_best_effort(
                store,
                bucket=processing.bucket,
                object_key=processing.staging_object_key,
            )
            raise FileValidationRejectedError(error.code) from None
        except FileObjectNotFoundError:
            await self.repository.mark_rejected(
                file=processing,
                code="upload_missing",
                rejected_at=self.clock(),
            )
            raise FileValidationRejectedError(AttachmentValidationCode.EMPTY_FILE) from None
        except FileObjectStoreError:
            if final_key is not None:
                await self._remove_best_effort(
                    store,
                    bucket=processing.bucket,
                    object_key=final_key,
                )
            raise FileServiceUnavailableError from None

    async def get_file(self, scope: WorkspaceScope, file_id: UUID) -> FileSnapshot:
        self._require_view(scope)
        return await self.repository.get(workspace_id=scope.workspace_id, file_id=file_id)

    async def create_download(
        self,
        scope: WorkspaceScope,
        file_id: UUID,
    ) -> FileDownloadTicket:
        self._require_view(scope)
        store, _bucket = self._configured_store()
        ready = await self.repository.get_ready(
            workspace_id=scope.workspace_id,
            file_id=file_id,
        )
        expires_at = self.clock() + timedelta(seconds=self.presign_expiry_seconds)
        try:
            url = await store.presign_get(
                bucket=ready.bucket,
                object_key=ready.object_key,
                expires_at=expires_at,
            )
        except FileObjectStoreError:
            raise FileServiceUnavailableError from None
        return FileDownloadTicket(url=url, expires_at=expires_at)

    async def delete_file(self, scope: WorkspaceScope, file_id: UUID) -> FileSnapshot:
        self._require_write(scope)
        store, _bucket = self._configured_store()
        claimed = await self.repository.claim_deleting(
            workspace_id=scope.workspace_id,
            file_id=file_id,
            requested_at=self.clock(),
        )
        if isinstance(claimed, FileSnapshot):
            return claimed
        try:
            if claimed.object_key is not None:
                await store.remove(bucket=claimed.bucket, object_key=claimed.object_key)
            await store.remove(
                bucket=claimed.bucket,
                object_key=claimed.staging_object_key,
            )
        except FileObjectStoreError:
            raise FileServiceUnavailableError from None
        return await self.repository.mark_deleted(
            file=claimed,
            deleted_at=self.clock(),
        )

    @staticmethod
    async def _remove_best_effort(
        store: PrivateFileObjectStore,
        *,
        bucket: str,
        object_key: str,
    ) -> None:
        try:
            await store.remove(bucket=bucket, object_key=object_key)
        except FileObjectStoreError:
            return

    @staticmethod
    def _require_view(scope: WorkspaceScope) -> None:
        if not scope_allows(scope, WorkspaceAction.VIEW):
            raise WorkspaceAccessDeniedError

    @staticmethod
    def _require_write(scope: WorkspaceScope) -> None:
        if not scope_allows(scope, WorkspaceAction.CREATE_RESOURCE):
            raise WorkspaceAccessDeniedError
