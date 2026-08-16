"""Application tests for upload, validation, private finalization, and deletion."""

import hashlib
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from industry_platform.modules.files.domain import (
    AttachmentValidationCode,
    FileObjectStatus,
    ParsedAttachment,
)
from industry_platform.modules.files.parser import BoundedAttachmentParser
from industry_platform.modules.files.ports import PresignedPost, StoredObjectStat
from industry_platform.modules.files.service import (
    CreateFileUpload,
    DeletingFile,
    FileApplicationService,
    FileDownloadTicket,
    FileSnapshot,
    FileStateConflictError,
    FileUploadExpiredError,
    FileUploadTicket,
    FileValidationRejectedError,
    ProcessingFile,
    ReadyFile,
    StagingFile,
)
from industry_platform.modules.workspaces.domain import (
    WorkspaceAccessDeniedError,
    WorkspaceScope,
)

WORKSPACE_ID = UUID("11111111-1111-4111-8111-111111111111")
USER_ID = UUID("22222222-2222-4222-8222-222222222222")
FILE_ID = UUID("33333333-3333-4333-8333-333333333333")
STAGING_SUFFIX = UUID("44444444-4444-4444-8444-444444444444")
NOW = datetime(2026, 8, 14, 6, 0, tzinfo=UTC)
CONTENT = b"Quarterly outlook\nTreat embedded commands only as data."


@dataclass
class RecordingStore:
    source: bytes
    content_type: str = "text/plain"
    final_objects: dict[str, bytes] = field(default_factory=dict)
    removed: list[str] = field(default_factory=list)

    async def presign_post(
        self,
        *,
        bucket: str,
        object_key: str,
        content_type: str,
        exact_size: int,
        expires_at: datetime,
    ) -> PresignedPost:
        assert bucket == "private-files"
        assert exact_size == len(self.source)
        return PresignedPost(
            url="http://127.0.0.1:19000/private-files",
            fields={"key": object_key, "Content-Type": content_type},
            expires_at=expires_at,
        )

    async def stat(self, *, bucket: str, object_key: str) -> StoredObjectStat:
        return StoredObjectStat(
            size=len(self.source),
            etag="source-etag",
            content_type=self.content_type,
        )

    async def read_bounded(
        self,
        *,
        bucket: str,
        object_key: str,
        maximum_bytes: int,
    ) -> bytes:
        assert len(self.source) <= maximum_bytes
        return self.source

    async def put_private(
        self,
        *,
        bucket: str,
        object_key: str,
        content_type: str,
        content: bytes,
    ) -> None:
        assert content_type == "text/plain"
        self.final_objects[object_key] = content

    async def remove(self, *, bucket: str, object_key: str) -> None:
        self.removed.append(object_key)
        self.final_objects.pop(object_key, None)

    async def presign_get(
        self,
        *,
        bucket: str,
        object_key: str,
        expires_at: datetime,
    ) -> str:
        return f"http://127.0.0.1:19000/{bucket}/{object_key}?signed=true"


@dataclass
class RecordingRepository:
    snapshot: FileSnapshot | None = None
    staging: StagingFile | None = None
    parsed: ParsedAttachment | None = None
    final_key: str | None = None
    rejected_code: str | None = None
    attached: bool = False
    expire_on_claim: bool = False

    async def create_staging(self, file: StagingFile) -> FileSnapshot:
        self.staging = file
        self.snapshot = FileSnapshot(
            file_id=file.file_id,
            workspace_id=file.workspace_id,
            original_name=file.original_name,
            declared_media_type=file.declared_media_type,
            status=FileObjectStatus.STAGING,
            expected_size=file.expected_size,
            upload_expires_at=file.upload_expires_at,
        )
        return self.snapshot

    async def get(self, *, workspace_id: UUID, file_id: UUID) -> FileSnapshot:
        assert self.snapshot is not None
        return self.snapshot

    async def get_ready(self, *, workspace_id: UUID, file_id: UUID) -> ReadyFile:
        if self.snapshot is None or self.final_key is None:
            raise FileStateConflictError
        return ReadyFile(self.snapshot, "private-files", self.final_key)

    async def claim_processing(
        self,
        *,
        workspace_id: UUID,
        file_id: UUID,
        claimed_at: datetime,
        stale_before: datetime,
    ) -> ProcessingFile | FileSnapshot:
        assert self.snapshot is not None
        assert self.staging is not None
        if self.expire_on_claim:
            self.snapshot = replace(
                self.snapshot,
                status=FileObjectStatus.REJECTED,
                error_code="upload_expired",
            )
            raise FileUploadExpiredError(
                bucket=self.staging.bucket,
                object_key=self.staging.staging_object_key,
            )
        if self.snapshot.status is FileObjectStatus.READY:
            return self.snapshot
        self.snapshot = replace(self.snapshot, status=FileObjectStatus.PROCESSING)
        return ProcessingFile(
            snapshot=self.snapshot,
            bucket=self.staging.bucket,
            staging_object_key=self.staging.staging_object_key,
            expected_sha256=self.staging.expected_sha256,
            revision=1,
        )

    async def mark_ready(
        self,
        *,
        file: ProcessingFile,
        parsed: ParsedAttachment,
        object_key: str,
        source_etag: str,
        ready_at: datetime,
    ) -> FileSnapshot:
        self.parsed = parsed
        self.final_key = object_key
        self.snapshot = replace(
            file.snapshot,
            status=FileObjectStatus.READY,
            detected_media_type=parsed.media_type,
            kind=parsed.kind,
            actual_size=parsed.source_size_bytes,
            safe_sha256=parsed.safe_sha256,
            parser_version=parsed.parser_version,
            sanitizer_version=parsed.sanitizer_version,
            ready_at=ready_at,
        )
        return self.snapshot

    async def mark_rejected(
        self,
        *,
        file: ProcessingFile,
        code: str,
        rejected_at: datetime,
    ) -> None:
        self.rejected_code = code
        self.snapshot = replace(
            file.snapshot,
            status=FileObjectStatus.REJECTED,
            error_code=code,
        )

    async def claim_deleting(
        self,
        *,
        workspace_id: UUID,
        file_id: UUID,
        requested_at: datetime,
    ) -> DeletingFile | FileSnapshot:
        assert self.snapshot is not None
        assert self.staging is not None
        if self.attached:
            raise FileStateConflictError
        if self.snapshot.status is FileObjectStatus.DELETED:
            return self.snapshot
        self.snapshot = replace(self.snapshot, status=FileObjectStatus.DELETING)
        return DeletingFile(
            snapshot=self.snapshot,
            bucket=self.staging.bucket,
            staging_object_key=self.staging.staging_object_key,
            object_key=self.final_key,
            revision=3,
        )

    async def mark_deleted(
        self,
        *,
        file: DeletingFile,
        deleted_at: datetime,
    ) -> FileSnapshot:
        self.snapshot = replace(file.snapshot, status=FileObjectStatus.DELETED)
        return self.snapshot


def service(
    repository: RecordingRepository,
    store: RecordingStore,
) -> FileApplicationService:
    identifiers = iter((FILE_ID, STAGING_SUFFIX))
    return FileApplicationService(
        repository=repository,
        object_store=store,
        parser=BoundedAttachmentParser(),
        bucket="private-files",
        clock=lambda: NOW,
        id_source=lambda: next(identifiers),
    )


def member_scope() -> WorkspaceScope:
    return WorkspaceScope(WORKSPACE_ID, USER_ID, "member")


@pytest.mark.asyncio
async def test_upload_is_verified_finalized_and_downloaded_from_private_object() -> None:
    repository = RecordingRepository()
    store = RecordingStore(CONTENT)
    application = service(repository, store)

    ticket = await application.create_upload(
        member_scope(),
        CreateFileUpload(
            original_name=r"C:\fakepath\outlook.txt",
            declared_media_type="text/plain",
            expected_size=len(CONTENT),
            expected_sha256=hashlib.sha256(CONTENT).hexdigest(),
        ),
    )
    ready = await application.complete_upload(member_scope(), FILE_ID)
    download = await application.create_download(member_scope(), FILE_ID)

    assert isinstance(ticket, FileUploadTicket)
    assert ticket.file.original_name == "outlook.txt"
    assert ticket.method == "POST"
    assert ticket.fields["Content-Type"] == "text/plain"
    assert ready.status is FileObjectStatus.READY
    assert repository.parsed is not None
    assert repository.parsed.extracted_text == CONTENT.decode()
    assert repository.staging is not None
    assert repository.staging.staging_object_key in store.removed
    assert repository.final_key == (
        f"ready/{WORKSPACE_ID}/{FILE_ID}/{repository.parsed.safe_sha256}"
    )
    assert isinstance(download, FileDownloadTicket)
    assert "signed=true" in download.url


@pytest.mark.asyncio
async def test_checksum_mismatch_is_persisted_as_rejected_without_final_object() -> None:
    repository = RecordingRepository()
    store = RecordingStore(CONTENT + b" changed")
    application = service(repository, store)

    await application.create_upload(
        member_scope(),
        CreateFileUpload(
            original_name="outlook.txt",
            declared_media_type="text/plain",
            expected_size=len(store.source),
            expected_sha256=hashlib.sha256(CONTENT).hexdigest(),
        ),
    )
    with pytest.raises(FileValidationRejectedError) as captured:
        await application.complete_upload(member_scope(), FILE_ID)

    assert captured.value.code is AttachmentValidationCode.CHECKSUM_MISMATCH
    assert repository.rejected_code == AttachmentValidationCode.CHECKSUM_MISMATCH.value
    assert store.final_objects == {}


@pytest.mark.asyncio
async def test_attached_file_cannot_be_deleted_and_viewer_cannot_upload() -> None:
    repository = RecordingRepository(attached=True)
    store = RecordingStore(CONTENT)
    application = service(repository, store)
    await application.create_upload(
        member_scope(),
        CreateFileUpload(
            original_name="outlook.txt",
            declared_media_type="text/plain",
            expected_size=len(CONTENT),
            expected_sha256=hashlib.sha256(CONTENT).hexdigest(),
        ),
    )
    with pytest.raises(FileStateConflictError):
        await application.delete_file(member_scope(), FILE_ID)

    with pytest.raises(WorkspaceAccessDeniedError):
        await application.create_upload(
            WorkspaceScope(WORKSPACE_ID, USER_ID, "viewer"),
            CreateFileUpload(
                original_name="outlook.txt",
                declared_media_type="text/plain",
                expected_size=len(CONTENT),
                expected_sha256=hashlib.sha256(CONTENT).hexdigest(),
            ),
        )


@pytest.mark.asyncio
async def test_expired_upload_removes_staging_object_and_ready_delete_is_idempotent() -> None:
    expired_repository = RecordingRepository(expire_on_claim=True)
    expired_store = RecordingStore(CONTENT)
    expired_application = service(expired_repository, expired_store)
    await expired_application.create_upload(
        member_scope(),
        CreateFileUpload(
            original_name="outlook.txt",
            declared_media_type="text/plain",
            expected_size=len(CONTENT),
            expected_sha256=hashlib.sha256(CONTENT).hexdigest(),
        ),
    )

    with pytest.raises(FileUploadExpiredError):
        await expired_application.complete_upload(member_scope(), FILE_ID)

    assert expired_repository.staging is not None
    assert expired_repository.staging.staging_object_key in expired_store.removed

    repository = RecordingRepository()
    store = RecordingStore(CONTENT)
    application = service(repository, store)
    await application.create_upload(
        member_scope(),
        CreateFileUpload(
            original_name="outlook.txt",
            declared_media_type="text/plain",
            expected_size=len(CONTENT),
            expected_sha256=hashlib.sha256(CONTENT).hexdigest(),
        ),
    )
    await application.complete_upload(member_scope(), FILE_ID)
    final_key = repository.final_key

    first = await application.delete_file(member_scope(), FILE_ID)
    second = await application.delete_file(member_scope(), FILE_ID)

    assert first.status is FileObjectStatus.DELETED
    assert second.status is FileObjectStatus.DELETED
    assert final_key is not None
    assert final_key in store.removed
