"""Strict HTTP contracts for Knowledge management and ingestion acceptance."""

import re
from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from industry_platform.modules.files.domain import (
    AttachmentMediaType,
    require_matching_extension,
    sanitize_display_filename,
)
from industry_platform.modules.jobs.domain import JobStatus
from industry_platform.modules.knowledge.domain import (
    KNOWLEDGE_MEDIA_TYPES,
    MAX_DOCUMENT_TITLE_LENGTH,
    MAX_KNOWLEDGE_BASE_DESCRIPTION_LENGTH,
    MAX_KNOWLEDGE_BASE_NAME_LENGTH,
    MAX_KNOWLEDGE_DOCUMENT_BYTES,
    DocumentDetail,
    DocumentVersion,
    DocumentVersionStatus,
    DocumentView,
    KnowledgeAcceptanceReceipt,
    KnowledgeBase,
    KnowledgeIngestionEvent,
    KnowledgeSource,
    KnowledgeUploadTicket,
)

_IDEMPOTENCY_PATTERN = re.compile(r"^[\x21-\x7e]{1,200}$")


def _idempotency_key(value: str) -> str:
    if not _IDEMPOTENCY_PATTERN.fullmatch(value):
        raise ValueError("Idempotency key is invalid")
    return value


type IdempotencyKey = Annotated[str, AfterValidator(_idempotency_key)]


class StrictKnowledgeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateKnowledgeBaseRequest(StrictKnowledgeModel):
    name: str = Field(min_length=1, max_length=MAX_KNOWLEDGE_BASE_NAME_LENGTH)
    description: str | None = Field(default=None, max_length=MAX_KNOWLEDGE_BASE_DESCRIPTION_LENGTH)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if value != value.strip() or any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("Knowledge-base name is invalid")
        return value

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if (
            not normalized
            or "\x00" in normalized
            or any(ord(character) == 127 for character in normalized)
        ):
            raise ValueError("Knowledge-base description is invalid")
        return normalized


class UpdateKnowledgeBaseRequest(CreateKnowledgeBaseRequest):
    pass


class CreateKnowledgeUploadRequest(StrictKnowledgeModel):
    original_name: str = Field(min_length=1, max_length=1_024)
    declared_media_type: AttachmentMediaType
    expected_size: int = Field(ge=1, le=MAX_KNOWLEDGE_DOCUMENT_BYTES)
    expected_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_file_contract(self) -> Self:
        if self.declared_media_type not in KNOWLEDGE_MEDIA_TYPES:
            raise ValueError("Knowledge media type is unsupported")
        display_name = sanitize_display_filename(self.original_name)
        require_matching_extension(display_name, self.declared_media_type)
        self.original_name = display_name
        return self


class CompleteKnowledgeUploadRequest(StrictKnowledgeModel):
    title: str = Field(min_length=1, max_length=MAX_DOCUMENT_TITLE_LENGTH)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        if value != value.strip() or any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("Document title is invalid")
        return value


class KnowledgeBaseResponse(StrictKnowledgeModel):
    id: UUID
    workspace_id: UUID
    name: str
    description: str | None
    document_count: int
    revision: int
    created_at: datetime
    updated_at: datetime


class KnowledgeBaseCollectionResponse(StrictKnowledgeModel):
    knowledge_bases: list[KnowledgeBaseResponse]


class KnowledgeSourceResponse(StrictKnowledgeModel):
    file_id: UUID
    original_name: str
    declared_media_type: AttachmentMediaType
    expected_size: int
    actual_size: int


class DocumentVersionResponse(StrictKnowledgeModel):
    id: UUID
    document_id: UUID
    file_id: UUID
    ingestion_job_id: UUID
    version: int
    status: DocumentVersionStatus
    revision: int
    error_code: str | None
    uploaded_at: datetime
    queued_at: datetime
    processing_started_at: datetime | None
    ready_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DocumentResponse(StrictKnowledgeModel):
    id: UUID
    workspace_id: UUID
    knowledge_base_id: UUID
    title: str
    active_version_id: UUID | None
    latest_version_number: int
    revision: int
    created_at: datetime
    updated_at: datetime
    latest_version: DocumentVersionResponse
    source: KnowledgeSourceResponse


class DocumentCollectionResponse(StrictKnowledgeModel):
    documents: list[DocumentResponse]


class DocumentVersionDetailResponse(StrictKnowledgeModel):
    version: DocumentVersionResponse
    source: KnowledgeSourceResponse


class DocumentDetailResponse(StrictKnowledgeModel):
    document: DocumentResponse
    versions: list[DocumentVersionDetailResponse]


class KnowledgeUploadFileResponse(StrictKnowledgeModel):
    id: UUID
    original_name: str
    declared_media_type: AttachmentMediaType
    expected_size: int
    status: Literal["uploaded"] = "uploaded"


class KnowledgeUploadResponse(StrictKnowledgeModel):
    file: KnowledgeUploadFileResponse
    method: Literal["POST"]
    url: str
    fields: dict[str, str]
    expires_at: datetime


class KnowledgeJobResponse(StrictKnowledgeModel):
    id: UUID
    status: JobStatus
    outbox_event_id: UUID
    events_url: str


class KnowledgeAcceptanceResponse(StrictKnowledgeModel):
    document: DocumentResponse
    version: DocumentVersionResponse
    job: KnowledgeJobResponse
    created: bool


class KnowledgeIngestionEventResponse(StrictKnowledgeModel):
    id: UUID
    event_type: str
    generation: int
    event_sequence: int
    occurred_at: datetime


class KnowledgeIngestionEventCollectionResponse(StrictKnowledgeModel):
    events: list[KnowledgeIngestionEventResponse]


def knowledge_base_response(value: KnowledgeBase) -> KnowledgeBaseResponse:
    return KnowledgeBaseResponse(
        id=value.id,
        workspace_id=value.workspace_id,
        name=value.name,
        description=value.description,
        document_count=value.document_count,
        revision=value.revision,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def source_response(value: KnowledgeSource) -> KnowledgeSourceResponse:
    return KnowledgeSourceResponse(
        file_id=value.file_id,
        original_name=value.original_name,
        declared_media_type=value.declared_media_type,
        expected_size=value.expected_size,
        actual_size=value.actual_size,
    )


def version_response(value: DocumentVersion) -> DocumentVersionResponse:
    return DocumentVersionResponse(
        id=value.id,
        document_id=value.document_id,
        file_id=value.file_id,
        ingestion_job_id=value.ingestion_job_id,
        version=value.version,
        status=value.status,
        revision=value.revision,
        error_code=value.error_code,
        uploaded_at=value.uploaded_at,
        queued_at=value.queued_at,
        processing_started_at=value.processing_started_at,
        ready_at=value.ready_at,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def document_response(value: DocumentView) -> DocumentResponse:
    document = value.document
    return DocumentResponse(
        id=document.id,
        workspace_id=document.workspace_id,
        knowledge_base_id=document.knowledge_base_id,
        title=document.title,
        active_version_id=document.active_version_id,
        latest_version_number=document.latest_version_number,
        revision=document.revision,
        created_at=document.created_at,
        updated_at=document.updated_at,
        latest_version=version_response(value.latest_version),
        source=source_response(value.source),
    )


def document_detail_response(value: DocumentDetail) -> DocumentDetailResponse:
    latest_version = value.versions[0]
    latest_source = value.sources[0]
    return DocumentDetailResponse(
        document=document_response(
            DocumentView(
                document=value.document,
                latest_version=latest_version,
                source=latest_source,
            )
        ),
        versions=[
            DocumentVersionDetailResponse(
                version=version_response(version),
                source=source_response(source),
            )
            for version, source in zip(value.versions, value.sources, strict=True)
        ],
    )


def upload_response(value: KnowledgeUploadTicket) -> KnowledgeUploadResponse:
    return KnowledgeUploadResponse(
        file=KnowledgeUploadFileResponse(
            id=value.file_id,
            original_name=value.original_name,
            declared_media_type=value.declared_media_type,
            expected_size=value.expected_size,
        ),
        method="POST",
        url=value.url,
        fields=value.fields,
        expires_at=value.expires_at,
    )


def acceptance_response(
    value: KnowledgeAcceptanceReceipt,
    *,
    events_url: str,
) -> KnowledgeAcceptanceResponse:
    view = DocumentView(
        document=value.document,
        latest_version=value.version,
        source=value.source,
    )
    return KnowledgeAcceptanceResponse(
        document=document_response(view),
        version=version_response(value.version),
        job=KnowledgeJobResponse(
            id=value.job_id,
            status=value.job_status,
            outbox_event_id=value.outbox_event_id,
            events_url=events_url,
        ),
        created=value.created,
    )


def ingestion_event_response(
    value: KnowledgeIngestionEvent,
) -> KnowledgeIngestionEventResponse:
    return KnowledgeIngestionEventResponse(
        id=value.id,
        event_type=value.event_type,
        generation=value.generation,
        event_sequence=value.event_sequence,
        occurred_at=value.occurred_at,
    )
