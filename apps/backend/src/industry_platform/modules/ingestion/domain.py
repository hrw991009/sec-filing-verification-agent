"""Technology-independent contracts for versioned document parsing and chunking."""

import base64
import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Self, cast
from uuid import UUID

from industry_platform.modules.files.domain import AttachmentMediaType
from industry_platform.modules.jobs.domain import JobLeaseProof
from industry_platform.modules.knowledge.parser_contract import (
    MAX_EXTRACTED_TEXT_CHARACTERS,
    MAX_PAGE_IMAGE_PIXELS,
    MAX_PARSER_OUTPUT_BYTES,
    MAX_PDF_PAGES,
    PARSER_NAME,
    PARSER_SCHEMA_VERSION,
    PARSER_TIMEOUT_SECONDS,
    PARSER_VERSION,
)

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class IngestionStage(StrEnum):
    VALIDATING = "validating"
    PARSING = "parsing"
    EXTRACTING_ASSETS = "extracting_assets"
    CHUNKING = "chunking"


INGESTION_STAGE_SEQUENCE: Final[Mapping[IngestionStage, int]] = MappingProxyType(
    {
        IngestionStage.VALIDATING: 1,
        IngestionStage.PARSING: 2,
        IngestionStage.EXTRACTING_ASSETS: 3,
        IngestionStage.CHUNKING: 4,
    }
)


class ParsedTextSource(StrEnum):
    DIGITAL = "digital"
    OCR = "ocr"
    PLAIN_TEXT = "plain_text"
    MARKDOWN = "markdown"


class ParsedAssetKind(StrEnum):
    IMAGE = "image"
    TABLE = "table"


class ParserErrorCode(StrEnum):
    CORRUPT_DOCUMENT = "parser_corrupt_document"
    ENCRYPTED_DOCUMENT = "parser_encrypted_document"
    PAGE_LIMIT_EXCEEDED = "parser_page_limit_exceeded"
    TEXT_LIMIT_EXCEEDED = "parser_text_limit_exceeded"
    IMAGE_PIXEL_LIMIT_EXCEEDED = "parser_image_pixel_limit_exceeded"
    OUTPUT_LIMIT_EXCEEDED = "parser_output_limit_exceeded"
    TIMEOUT = "parser_timeout"
    DEPENDENCY_MISSING = "parser_dependency_missing"
    DEPENDENCY_FAILED = "parser_dependency_failed"
    UNSUPPORTED_MEDIA_TYPE = "parser_unsupported_media_type"
    EMPTY_DOCUMENT = "parser_empty_document"
    CANCELLED = "ingestion_cancelled"


_RETRYABLE_PARSER_ERRORS: Final = frozenset(
    {
        ParserErrorCode.TIMEOUT,
        ParserErrorCode.DEPENDENCY_FAILED,
    }
)


class DocumentParserError(RuntimeError):
    """Stable parser failure that does not expose document contents."""

    def __init__(self, code: ParserErrorCode) -> None:
        super().__init__("Document parsing failed")
        self.code = code

    @property
    def retryable(self) -> bool:
        return self.code in _RETRYABLE_PARSER_ERRORS


class IngestionNotFoundError(RuntimeError):
    pass


class IngestionConflictError(RuntimeError):
    pass


class IngestionPersistenceError(RuntimeError):
    """Persistence failed with only non-sensitive database coordinates retained."""

    def __init__(
        self,
        *,
        sqlstate: str | None = None,
        constraint_name: str | None = None,
    ) -> None:
        super().__init__("Knowledge ingestion persistence failed")
        self.sqlstate = sqlstate
        self.constraint_name = constraint_name


class IngestionDependencyError(RuntimeError):
    pass


class IngestionCancelledError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ParserBudget:
    max_input_bytes: int
    max_pages: int = MAX_PDF_PAGES
    max_text_characters: int = MAX_EXTRACTED_TEXT_CHARACTERS
    max_page_image_pixels: int = MAX_PAGE_IMAGE_PIXELS
    max_output_bytes: int = MAX_PARSER_OUTPUT_BYTES
    timeout_seconds: int = PARSER_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        values = (
            self.max_input_bytes,
            self.max_pages,
            self.max_text_characters,
            self.max_page_image_pixels,
            self.max_output_bytes,
            self.timeout_seconds,
        )
        if any(isinstance(value, bool) or value < 1 for value in values):
            raise ValueError("Parser budget is invalid")

    def snapshot(self) -> dict[str, int]:
        return {
            "max_input_bytes": self.max_input_bytes,
            "max_output_bytes": self.max_output_bytes,
            "max_page_image_pixels": self.max_page_image_pixels,
            "max_pages": self.max_pages,
            "max_text_characters": self.max_text_characters,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass(frozen=True, slots=True)
class ParserRequest:
    document_version_id: UUID
    original_name: str
    media_type: AttachmentMediaType
    source_sha256: str
    content: bytes = field(repr=False)
    budget: ParserBudget

    def __post_init__(self) -> None:
        if self.document_version_id.int == 0:
            raise ValueError("Document version ID must not be nil")
        if not self.original_name or len(self.original_name) > 255:
            raise ValueError("Parser source name is invalid")
        if not _SHA256_PATTERN.fullmatch(self.source_sha256):
            raise ValueError("Parser source checksum is invalid")
        if not self.content or len(self.content) > self.budget.max_input_bytes:
            raise ValueError("Parser input exceeds its fixed budget")
        if hashlib.sha256(self.content).hexdigest() != self.source_sha256:
            raise ValueError("Parser input checksum does not match")


@dataclass(frozen=True, slots=True)
class BoundingBox:
    x0: float
    top: float
    x1: float
    bottom: float

    def __post_init__(self) -> None:
        values = (self.x0, self.top, self.x1, self.bottom)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Bounding box contains a non-finite coordinate")
        if self.x0 < 0 or self.top < 0 or self.x1 <= self.x0 or self.bottom <= self.top:
            raise ValueError("Bounding box is invalid")

    def snapshot(self) -> list[float]:
        return [self.x0, self.top, self.x1, self.bottom]

    @classmethod
    def from_snapshot(cls, value: object) -> Self:
        if (
            not isinstance(value, list)
            or len(value) != 4
            or any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value)
        ):
            raise ValueError("Bounding-box snapshot is invalid")
        return cls(*(float(item) for item in value))


def _content_hash(value: str) -> str:
    if not _SHA256_PATTERN.fullmatch(value):
        raise ValueError("Parsed content hash is invalid")
    return value


def _title_path(value: Sequence[str]) -> tuple[str, ...]:
    result = tuple(item.strip() for item in value)
    if len(result) > 12 or any(not item or len(item) > 240 for item in result):
        raise ValueError("Parsed title path is invalid")
    return result


@dataclass(frozen=True, slots=True)
class ParsedPage:
    page_number: int
    width_points: float
    height_points: float
    text: str
    text_source: ParsedTextSource
    bbox: BoundingBox
    title_path: tuple[str, ...]
    content_hash: str

    def __post_init__(self) -> None:
        if self.page_number < 1 or self.width_points <= 0 or self.height_points <= 0:
            raise ValueError("Parsed page geometry is invalid")
        if not self.text.strip():
            raise ValueError("Parsed page text is empty")
        object.__setattr__(self, "title_path", _title_path(self.title_path))
        object.__setattr__(self, "content_hash", _content_hash(self.content_hash))


@dataclass(frozen=True, slots=True)
class ParsedAsset:
    ordinal: int
    page_number: int
    kind: ParsedAssetKind
    bbox: BoundingBox
    title_path: tuple[str, ...]
    content_hash: str
    preview_sha256: str
    preview_mime_type: str
    preview: bytes = field(repr=False)
    html: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.ordinal < 1 or self.page_number < 1:
            raise ValueError("Parsed asset locator is invalid")
        object.__setattr__(self, "title_path", _title_path(self.title_path))
        object.__setattr__(self, "content_hash", _content_hash(self.content_hash))
        object.__setattr__(self, "preview_sha256", _content_hash(self.preview_sha256))
        if self.preview_mime_type != "image/png" or not self.preview:
            raise ValueError("Parsed asset preview is invalid")
        if hashlib.sha256(self.preview).hexdigest() != self.preview_sha256:
            raise ValueError("Parsed asset preview checksum does not match")
        if self.kind is ParsedAssetKind.TABLE:
            if self.html is None or not self.html.startswith("<table>"):
                raise ValueError("Parsed table HTML is invalid")
        elif self.html is not None:
            raise ValueError("Only table assets may contain HTML")


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    source_sha256: str
    media_type: AttachmentMediaType
    pages: tuple[ParsedPage, ...]
    assets: tuple[ParsedAsset, ...]
    schema_version: int = PARSER_SCHEMA_VERSION
    parser_name: str = PARSER_NAME
    parser_version: str = PARSER_VERSION

    def __post_init__(self) -> None:
        _content_hash(self.source_sha256)
        if (
            self.schema_version != PARSER_SCHEMA_VERSION
            or self.parser_name != PARSER_NAME
            or self.parser_version != PARSER_VERSION
        ):
            raise ValueError("Parsed-document version is unsupported")
        if not self.pages:
            raise ValueError("Parsed document has no pages")
        expected_pages = tuple(range(1, len(self.pages) + 1))
        if tuple(page.page_number for page in self.pages) != expected_pages:
            raise ValueError("Parsed page numbering is not contiguous")
        if any(asset.page_number > len(self.pages) for asset in self.assets):
            raise ValueError("Parsed asset references a missing page")
        if tuple(asset.ordinal for asset in self.assets) != tuple(range(1, len(self.assets) + 1)):
            raise ValueError("Parsed asset numbering is not contiguous")

    @property
    def text_characters(self) -> int:
        return sum(len(page.text) for page in self.pages)

    def snapshot_bytes(self) -> bytes:
        document = {
            "assets": [
                {
                    "bbox": asset.bbox.snapshot(),
                    "content_hash": asset.content_hash,
                    "html": asset.html,
                    "kind": asset.kind.value,
                    "ordinal": asset.ordinal,
                    "page_number": asset.page_number,
                    "preview": base64.b64encode(asset.preview).decode("ascii"),
                    "preview_mime_type": asset.preview_mime_type,
                    "preview_sha256": asset.preview_sha256,
                    "title_path": list(asset.title_path),
                }
                for asset in self.assets
            ],
            "media_type": self.media_type.value,
            "pages": [
                {
                    "bbox": page.bbox.snapshot(),
                    "content_hash": page.content_hash,
                    "height_points": page.height_points,
                    "page_number": page.page_number,
                    "text": page.text,
                    "text_source": page.text_source.value,
                    "title_path": list(page.title_path),
                    "width_points": page.width_points,
                }
                for page in self.pages
            ],
            "parser_name": self.parser_name,
            "parser_version": self.parser_version,
            "schema_version": self.schema_version,
            "source_sha256": self.source_sha256,
        }
        return json.dumps(
            document, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")

    @classmethod
    def from_snapshot_bytes(cls, snapshot: bytes) -> Self:
        try:
            raw: object = json.loads(snapshot)
            if not isinstance(raw, dict):
                raise ValueError
            document = cast(dict[str, object], raw)
            pages_raw = document["pages"]
            assets_raw = document["assets"]
            if not isinstance(pages_raw, list) or not isinstance(assets_raw, list):
                raise ValueError
            pages = tuple(_page_from_snapshot(item) for item in pages_raw)
            assets = tuple(_asset_from_snapshot(item) for item in assets_raw)
            return cls(
                source_sha256=_required_str(document, "source_sha256"),
                media_type=AttachmentMediaType(_required_str(document, "media_type")),
                pages=pages,
                assets=assets,
                schema_version=_required_int(document, "schema_version"),
                parser_name=_required_str(document, "parser_name"),
                parser_version=_required_str(document, "parser_version"),
            )
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Parsed-document snapshot is invalid") from None


def _required_str(document: Mapping[str, object], key: str) -> str:
    value = document[key]
    if not isinstance(value, str):
        raise ValueError
    return value


def _required_int(document: Mapping[str, object], key: str) -> int:
    value = document[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError
    return value


def _required_float(document: Mapping[str, object], key: str) -> float:
    value = document[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError
    return float(value)


def _required_title_path(document: Mapping[str, object]) -> tuple[str, ...]:
    value = document["title_path"]
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError
    return tuple(cast(list[str], value))


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError
    return cast(dict[str, object], value)


def _page_from_snapshot(value: object) -> ParsedPage:
    page = _mapping(value)
    return ParsedPage(
        page_number=_required_int(page, "page_number"),
        width_points=_required_float(page, "width_points"),
        height_points=_required_float(page, "height_points"),
        text=_required_str(page, "text"),
        text_source=ParsedTextSource(_required_str(page, "text_source")),
        bbox=BoundingBox.from_snapshot(page["bbox"]),
        title_path=_required_title_path(page),
        content_hash=_required_str(page, "content_hash"),
    )


def _asset_from_snapshot(value: object) -> ParsedAsset:
    asset = _mapping(value)
    html = asset["html"]
    if html is not None and not isinstance(html, str):
        raise ValueError
    try:
        preview = base64.b64decode(_required_str(asset, "preview"), validate=True)
    except ValueError:
        raise ValueError from None
    return ParsedAsset(
        ordinal=_required_int(asset, "ordinal"),
        page_number=_required_int(asset, "page_number"),
        kind=ParsedAssetKind(_required_str(asset, "kind")),
        bbox=BoundingBox.from_snapshot(asset["bbox"]),
        title_path=_required_title_path(asset),
        content_hash=_required_str(asset, "content_hash"),
        preview_sha256=_required_str(asset, "preview_sha256"),
        preview_mime_type=_required_str(asset, "preview_mime_type"),
        preview=preview,
        html=html,
    )


@dataclass(frozen=True, slots=True)
class ParsedChunk:
    ordinal: int
    page_number: int
    text: str
    title_path: tuple[str, ...]
    bbox: BoundingBox
    content_hash: str
    token_count: int
    asset_ordinals: tuple[int, ...]

    def __post_init__(self) -> None:
        if self.ordinal < 1 or self.page_number < 1 or not self.text.strip():
            raise ValueError("Parsed chunk locator is invalid")
        object.__setattr__(self, "title_path", _title_path(self.title_path))
        object.__setattr__(self, "content_hash", _content_hash(self.content_hash))
        if self.token_count < 1:
            raise ValueError("Parsed chunk token count is invalid")
        if any(value < 1 for value in self.asset_ordinals):
            raise ValueError("Parsed chunk asset relation is invalid")


@dataclass(frozen=True, slots=True)
class StoredStageCheckpoint:
    stage: IngestionStage
    stage_sequence: int
    input_hash: str
    output_hash: str
    output_bucket: str | None
    output_object_key: str | None = field(repr=False)

    def __post_init__(self) -> None:
        if self.stage_sequence != INGESTION_STAGE_SEQUENCE[self.stage]:
            raise ValueError("Stored ingestion stage sequence is invalid")
        _content_hash(self.input_hash)
        _content_hash(self.output_hash)
        if (self.output_bucket is None) != (self.output_object_key is None):
            raise ValueError("Stored ingestion output reference is incomplete")


@dataclass(frozen=True, slots=True)
class IngestionWorkItem:
    workspace_id: UUID
    document_id: UUID
    document_version_id: UUID
    ingestion_job_id: UUID
    file_id: UUID
    original_name: str
    media_type: AttachmentMediaType
    source_bucket: str = field(repr=False)
    source_object_key: str = field(repr=False)
    source_size: int
    source_sha256: str
    parser_name: str
    parser_version: str
    parser_schema_version: int
    parser_config: Mapping[str, object]
    chunker_name: str
    chunker_version: str
    chunker_config: Mapping[str, object]
    checkpoints: tuple[StoredStageCheckpoint, ...]

    def __post_init__(self) -> None:
        identifiers = (
            self.workspace_id,
            self.document_id,
            self.document_version_id,
            self.ingestion_job_id,
            self.file_id,
        )
        if any(value.int == 0 for value in identifiers):
            raise ValueError("Ingestion work item contains a nil identifier")
        if not self.original_name or not self.source_bucket or not self.source_object_key:
            raise ValueError("Ingestion source reference is invalid")
        if self.source_size < 1:
            raise ValueError("Ingestion source size is invalid")
        _content_hash(self.source_sha256)
        object.__setattr__(self, "parser_config", MappingProxyType(dict(self.parser_config)))
        object.__setattr__(self, "chunker_config", MappingProxyType(dict(self.chunker_config)))

    def checkpoint(self, stage: IngestionStage) -> StoredStageCheckpoint | None:
        return next((item for item in self.checkpoints if item.stage is stage), None)


@dataclass(frozen=True, slots=True)
class StoredAssetPreview:
    ordinal: int
    bucket: str
    object_key: str = field(repr=False)

    def __post_init__(self) -> None:
        if self.ordinal < 1 or not self.bucket or not self.object_key:
            raise ValueError("Stored asset preview reference is invalid")


@dataclass(frozen=True, slots=True)
class CompleteIngestionStage:
    proof: JobLeaseProof
    work_item: IngestionWorkItem
    stage: IngestionStage
    attempt_count: int
    input_hash: str
    output_hash: str
    stats: Mapping[str, object]
    output_bucket: str | None = None
    output_object_key: str | None = field(default=None, repr=False)
    parsed_document: ParsedDocument | None = field(default=None, repr=False)
    asset_previews: tuple[StoredAssetPreview, ...] = field(default=(), repr=False)
    chunks: tuple[ParsedChunk, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        if self.proof.job_id != self.work_item.ingestion_job_id:
            raise ValueError("Ingestion stage uses the wrong Job lease")
        if self.attempt_count < 1:
            raise ValueError("Ingestion attempt is invalid")
        _content_hash(self.input_hash)
        _content_hash(self.output_hash)
        if (self.output_bucket is None) != (self.output_object_key is None):
            raise ValueError("Ingestion output reference is incomplete")
        if self.stage is IngestionStage.EXTRACTING_ASSETS:
            if self.parsed_document is None:
                raise ValueError("Asset extraction requires a parsed document")
            if len(self.asset_previews) != len(self.parsed_document.assets):
                raise ValueError("Asset preview references do not match parsed assets")
        elif self.parsed_document is not None or self.asset_previews:
            raise ValueError("Only asset extraction may persist parsed assets")
        if self.stage is IngestionStage.CHUNKING:
            if not self.chunks:
                raise ValueError("Chunking stage requires chunks")
        elif self.chunks:
            raise ValueError("Only chunking may persist chunks")
        object.__setattr__(self, "stats", MappingProxyType(dict(self.stats)))


@dataclass(frozen=True, slots=True)
class IngestionResult:
    document_version_id: UUID
    page_count: int
    chunk_count: int
    asset_count: int
    status: str


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
