"""Technology-independent contracts for Knowledge ingestion acceptance."""

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from industry_platform.modules.files.domain import (
    AttachmentKind,
    AttachmentMediaType,
    require_matching_extension,
    sanitize_display_filename,
)
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.jobs.domain import JobStatus, PreparedJobSubmission, require_utc

KNOWLEDGE_SCHEMA_VERSION: Final = 1
KNOWLEDGE_INGESTION_TASK_NAME: Final = "knowledge.ingestion.v1"
KNOWLEDGE_INGESTION_QUEUE_NAME: Final = "ingestion"
MAX_KNOWLEDGE_DOCUMENT_BYTES: Final = 25 * 1_024 * 1_024
MAX_KNOWLEDGE_BASE_NAME_LENGTH: Final = 160
MAX_KNOWLEDGE_BASE_DESCRIPTION_LENGTH: Final = 2_000
MAX_DOCUMENT_TITLE_LENGTH: Final = 255

KNOWLEDGE_MEDIA_TYPES: Final = frozenset(
    {
        AttachmentMediaType.APPLICATION_PDF,
        AttachmentMediaType.TEXT_PLAIN,
        AttachmentMediaType.TEXT_MARKDOWN,
    }
)

_KEY_HASH_DOMAIN: Final = b"industry-platform:knowledge-ingestion-key:v1\x00"
_REQUEST_HASH_DOMAIN: Final = b"industry-platform:knowledge-ingestion-request:v1\x00"
_IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[\x21-\x7e]{1,200}$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class KnowledgeBaseStatus(StrEnum):
    ACTIVE = "active"
    DELETED = "deleted"


class DocumentStatus(StrEnum):
    ACTIVE = "active"
    DELETED = "deleted"


class DocumentVersionStatus(StrEnum):
    QUEUED = "queued"
    VALIDATING = "validating"
    PARSING = "parsing"
    EXTRACTING_ASSETS = "extracting_assets"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    VECTOR_INDEXING = "vector_indexing"
    LEXICAL_INDEXING = "lexical_indexing"
    RETRYING = "retrying"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DELETING = "deleting"
    DELETED = "deleted"


class KnowledgeError(RuntimeError):
    """Base type for stable Knowledge failures."""


class KnowledgeNotFoundError(KnowledgeError):
    pass


class KnowledgeConflictError(KnowledgeError):
    pass


class KnowledgeNotEmptyError(KnowledgeConflictError):
    pass


class KnowledgePersistenceError(KnowledgeError):
    def __init__(self, *, sqlstate: str | None = None) -> None:
        super().__init__("Knowledge persistence is unavailable")
        self.sqlstate = sqlstate if sqlstate is not None and len(sqlstate) == 5 else None


def _uuid(value: UUID, *, field_name: str) -> None:
    if value.int == 0:
        raise ValueError(f"{field_name} must not be nil")


def _single_line(value: str, *, field_name: str, maximum: int) -> str:
    normalized = value.strip()
    if (
        not normalized
        or normalized != value
        or len(normalized) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        raise ValueError(f"{field_name} is invalid")
    return normalized


def _description(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > MAX_KNOWLEDGE_BASE_DESCRIPTION_LENGTH:
        raise ValueError("Knowledge-base description is invalid")
    if "\x00" in normalized or any(ord(character) == 127 for character in normalized):
        raise ValueError("Knowledge-base description is invalid")
    return normalized


def _revision(value: int) -> None:
    if isinstance(value, bool) or value < 1:
        raise ValueError("Knowledge revision is invalid")


def validate_idempotency_key(value: str) -> str:
    if not _IDEMPOTENCY_KEY_PATTERN.fullmatch(value):
        raise ValueError("Knowledge ingestion idempotency key is invalid")
    return value


def hash_knowledge_idempotency_key(value: str) -> bytes:
    return hashlib.sha256(
        _KEY_HASH_DOMAIN + validate_idempotency_key(value).encode("ascii")
    ).digest()


def fingerprint_knowledge_request(*, knowledge_base_id: UUID, file_id: UUID, title: str) -> bytes:
    normalized_title = _single_line(
        title,
        field_name="Document title",
        maximum=MAX_DOCUMENT_TITLE_LENGTH,
    )
    encoded = json.dumps(
        {
            "file_id": str(file_id),
            "knowledge_base_id": str(knowledge_base_id),
            "schema_version": KNOWLEDGE_SCHEMA_VERSION,
            "title": normalized_title,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(_REQUEST_HASH_DOMAIN + encoded).digest()


@dataclass(frozen=True, slots=True)
class CreateKnowledgeBase:
    name: str
    description: str | None
    trace_id: TraceId

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "name",
            _single_line(
                self.name,
                field_name="Knowledge-base name",
                maximum=MAX_KNOWLEDGE_BASE_NAME_LENGTH,
            ),
        )
        object.__setattr__(self, "description", _description(self.description))


@dataclass(frozen=True, slots=True)
class UpdateKnowledgeBase:
    knowledge_base_id: UUID
    expected_revision: int
    name: str
    description: str | None
    trace_id: TraceId

    def __post_init__(self) -> None:
        _uuid(self.knowledge_base_id, field_name="Knowledge-base ID")
        _revision(self.expected_revision)
        object.__setattr__(
            self,
            "name",
            _single_line(
                self.name,
                field_name="Knowledge-base name",
                maximum=MAX_KNOWLEDGE_BASE_NAME_LENGTH,
            ),
        )
        object.__setattr__(self, "description", _description(self.description))


@dataclass(frozen=True, slots=True)
class DeleteKnowledgeBase:
    knowledge_base_id: UUID
    expected_revision: int
    trace_id: TraceId

    def __post_init__(self) -> None:
        _uuid(self.knowledge_base_id, field_name="Knowledge-base ID")
        _revision(self.expected_revision)


@dataclass(frozen=True, slots=True)
class CreateKnowledgeUpload:
    knowledge_base_id: UUID
    original_name: str
    declared_media_type: AttachmentMediaType
    expected_size: int
    expected_sha256: str
    trace_id: TraceId

    def __post_init__(self) -> None:
        _uuid(self.knowledge_base_id, field_name="Knowledge-base ID")
        display_name = sanitize_display_filename(self.original_name)
        if self.declared_media_type not in KNOWLEDGE_MEDIA_TYPES:
            raise ValueError("Knowledge media type is unsupported")
        require_matching_extension(display_name, self.declared_media_type)
        if (
            isinstance(self.expected_size, bool)
            or not 1 <= self.expected_size <= MAX_KNOWLEDGE_DOCUMENT_BYTES
        ):
            raise ValueError("Knowledge document size is invalid")
        if not _SHA256_PATTERN.fullmatch(self.expected_sha256):
            raise ValueError("Knowledge document checksum is invalid")
        object.__setattr__(self, "original_name", display_name)


@dataclass(frozen=True, slots=True)
class CompleteKnowledgeUpload:
    knowledge_base_id: UUID
    file_id: UUID
    title: str
    idempotency_key: str = field(repr=False)
    trace_id: TraceId

    def __post_init__(self) -> None:
        _uuid(self.knowledge_base_id, field_name="Knowledge-base ID")
        _uuid(self.file_id, field_name="Knowledge file ID")
        object.__setattr__(
            self,
            "title",
            _single_line(
                self.title,
                field_name="Document title",
                maximum=MAX_DOCUMENT_TITLE_LENGTH,
            ),
        )
        validate_idempotency_key(self.idempotency_key)


@dataclass(frozen=True, slots=True)
class KnowledgeBase:
    id: UUID
    workspace_id: UUID
    created_by_user_id: UUID
    name: str
    description: str | None
    status: KnowledgeBaseStatus
    document_count: int
    revision: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class KnowledgeSource:
    file_id: UUID
    original_name: str
    declared_media_type: AttachmentMediaType
    expected_size: int
    actual_size: int


@dataclass(frozen=True, slots=True)
class Document:
    id: UUID
    workspace_id: UUID
    knowledge_base_id: UUID
    created_by_user_id: UUID
    title: str
    status: DocumentStatus
    active_version_id: UUID | None
    latest_version_number: int
    revision: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class DocumentVersion:
    id: UUID
    document_id: UUID
    workspace_id: UUID
    knowledge_base_id: UUID
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


@dataclass(frozen=True, slots=True)
class DocumentView:
    document: Document
    latest_version: DocumentVersion
    source: KnowledgeSource


@dataclass(frozen=True, slots=True)
class DocumentDetail:
    document: Document
    versions: tuple[DocumentVersion, ...]
    sources: tuple[KnowledgeSource, ...]


@dataclass(frozen=True, slots=True)
class KnowledgeUploadTicket:
    file_id: UUID
    original_name: str
    declared_media_type: AttachmentMediaType
    expected_size: int
    method: str
    url: str = field(repr=False)
    fields: dict[str, str] = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class StagingKnowledgeUpload:
    file_id: UUID
    workspace_id: UUID
    created_by_user_id: UUID
    knowledge_base_id: UUID
    original_name: str
    declared_media_type: AttachmentMediaType
    bucket: str = field(repr=False)
    staging_key: str = field(repr=False)
    expected_size: int
    expected_sha256: str = field(repr=False)
    expires_at: datetime
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ClaimedKnowledgeUpload:
    file_id: UUID
    workspace_id: UUID
    knowledge_base_id: UUID
    created_by_user_id: UUID
    original_name: str
    declared_media_type: AttachmentMediaType
    bucket: str = field(repr=False)
    staging_key: str = field(repr=False)
    expected_size: int
    expected_sha256: str = field(repr=False)
    revision: int


@dataclass(frozen=True, slots=True)
class VerifiedKnowledgeUpload:
    claim: ClaimedKnowledgeUpload
    final_key: str = field(repr=False)
    source_etag: str = field(repr=False)
    media_type: AttachmentMediaType
    kind: AttachmentKind
    actual_size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class PreparedKnowledgeAcceptance:
    document_id: UUID
    version_id: UUID
    knowledge_base_id: UUID
    workspace_id: UUID
    created_by_user_id: UUID
    title: str
    idempotency_key_hash: bytes = field(repr=False)
    request_fingerprint: bytes = field(repr=False)
    upload: VerifiedKnowledgeUpload = field(repr=False)
    job: PreparedJobSubmission = field(repr=False)
    accepted_at: datetime

    def __post_init__(self) -> None:
        require_utc(self.accepted_at, field_name="accepted_at")
        if self.upload.claim.workspace_id != self.workspace_id:
            raise ValueError("Knowledge acceptance crosses Workspaces")
        if self.upload.claim.knowledge_base_id != self.knowledge_base_id:
            raise ValueError("Knowledge acceptance crosses knowledge bases")
        if self.job.scope.workspace_id != self.workspace_id or self.job.scope.system_scope_key:
            raise ValueError("Knowledge Job crosses execution scopes")
        if (
            self.job.task_name != KNOWLEDGE_INGESTION_TASK_NAME
            or self.job.queue_name != KNOWLEDGE_INGESTION_QUEUE_NAME
        ):
            raise ValueError("Knowledge Job routing is invalid")
        if dict(self.job.payload) != {
            "document_version_id": str(self.version_id),
            "file_id": str(self.upload.claim.file_id),
            "schema_version": KNOWLEDGE_SCHEMA_VERSION,
        }:
            raise ValueError("Knowledge Job payload does not match its upload")


@dataclass(frozen=True, slots=True)
class KnowledgeAcceptanceReceipt:
    document: Document
    version: DocumentVersion
    source: KnowledgeSource
    job_id: UUID
    job_status: JobStatus
    outbox_event_id: UUID
    created: bool


@dataclass(frozen=True, slots=True)
class KnowledgeIngestionEvent:
    id: UUID
    event_type: str
    generation: int
    event_sequence: int
    occurred_at: datetime
