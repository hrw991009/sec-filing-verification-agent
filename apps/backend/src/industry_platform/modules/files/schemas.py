"""Strict HTTP contracts for private chat attachments."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from industry_platform.modules.files.domain import (
    AttachmentKind,
    AttachmentMediaType,
    FileObjectStatus,
)
from industry_platform.modules.files.service import FileSnapshot


class StrictFileModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateFileUploadRequest(StrictFileModel):
    original_name: str = Field(min_length=1, max_length=1_024)
    declared_media_type: AttachmentMediaType
    expected_size: int = Field(ge=1, le=5_000_000)
    expected_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class FileResponse(StrictFileModel):
    id: UUID
    original_name: str
    declared_media_type: AttachmentMediaType
    detected_media_type: AttachmentMediaType | None
    kind: AttachmentKind | None
    status: FileObjectStatus
    expected_size: int
    actual_size: int | None
    error_code: str | None
    width: int | None
    height: int | None
    upload_expires_at: datetime
    ready_at: datetime | None


class FileUploadResponse(StrictFileModel):
    file: FileResponse
    method: Literal["POST"]
    url: str
    fields: dict[str, str]
    expires_at: datetime


class FileDownloadResponse(StrictFileModel):
    url: str
    expires_at: datetime


def file_response(file: FileSnapshot) -> FileResponse:
    return FileResponse(
        id=file.file_id,
        original_name=file.original_name,
        declared_media_type=AttachmentMediaType(file.declared_media_type),
        detected_media_type=file.detected_media_type,
        kind=file.kind,
        status=file.status,
        expected_size=file.expected_size,
        actual_size=file.actual_size,
        error_code=file.error_code,
        width=file.width,
        height=file.height,
        upload_expires_at=file.upload_expires_at,
        ready_at=file.ready_at,
    )
