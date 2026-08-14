"""Technology-independent contracts for bounded chat attachments."""

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Final, Protocol

FILE_CONTRACT_SCHEMA_VERSION: Final = 1
ATTACHMENT_PARSER_VERSION: Final = "chat-attachment-parser-v1"
ATTACHMENT_SANITIZER_VERSION: Final = "chat-attachment-sanitizer-v1"

MAX_ATTACHMENT_BYTES: Final = 5_000_000
MAX_TEXT_ATTACHMENT_BYTES: Final = 1 * 1024 * 1024
MAX_TEXT_ATTACHMENT_CHARACTERS: Final = 500_000
MAX_IMAGE_ATTACHMENT_BYTES: Final = MAX_ATTACHMENT_BYTES
MAX_IMAGE_WIDTH: Final = 4_096
MAX_IMAGE_HEIGHT: Final = 4_096
MAX_IMAGE_PIXELS: Final = 16_000_000
MAX_DISPLAY_FILENAME_CHARACTERS: Final = 255
MAX_RAW_FILENAME_CHARACTERS: Final = 1_024
MAX_DECLARED_MEDIA_TYPE_CHARACTERS: Final = 128

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class FileObjectStatus(StrEnum):
    """Persisted lifecycle of one private file object."""

    STAGING = "staging"
    PROCESSING = "processing"
    READY = "ready"
    REJECTED = "rejected"
    FAILED = "failed"
    DELETING = "deleting"
    DELETED = "deleted"


class AttachmentKind(StrEnum):
    """The two attachment paths intentionally available on Day 2."""

    TEXT = "text"
    IMAGE = "image"


class AttachmentMediaType(StrEnum):
    """Exact media types accepted by the Day 2 parser."""

    TEXT_PLAIN = "text/plain"
    TEXT_MARKDOWN = "text/markdown"
    IMAGE_PNG = "image/png"
    IMAGE_JPEG = "image/jpeg"
    IMAGE_WEBP = "image/webp"

    @property
    def kind(self) -> AttachmentKind:
        """Classify the media type without trusting a filename."""

        if self in {self.TEXT_PLAIN, self.TEXT_MARKDOWN}:
            return AttachmentKind.TEXT
        return AttachmentKind.IMAGE

    @property
    def allowed_extensions(self) -> frozenset[str]:
        """Return the filename extensions that may declare this media type."""

        return _ALLOWED_EXTENSIONS[self]


class AttachmentValidationCode(StrEnum):
    """Stable, non-sensitive reasons why an uploaded object was rejected."""

    EMPTY_FILE = "empty_file"
    INVALID_FILENAME = "invalid_filename"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    EXTENSION_MISMATCH = "extension_mismatch"
    SIZE_MISMATCH = "size_mismatch"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    UPLOAD_METADATA_MISMATCH = "upload_metadata_mismatch"
    FILE_TOO_LARGE = "file_too_large"
    MAGIC_MISMATCH = "magic_mismatch"
    INVALID_TEXT_ENCODING = "invalid_text_encoding"
    UNSAFE_TEXT_CONTENT = "unsafe_text_content"
    TEXT_TOO_LARGE = "text_too_large"
    IMAGE_DECODE_FAILED = "image_decode_failed"
    IMAGE_ANIMATED = "image_animated"
    IMAGE_DIMENSIONS_EXCEEDED = "image_dimensions_exceeded"
    SAFE_OUTPUT_TOO_LARGE = "safe_output_too_large"


_SAFE_ERROR_MESSAGES: Final[dict[AttachmentValidationCode, str]] = {
    AttachmentValidationCode.EMPTY_FILE: "Attachment content is empty",
    AttachmentValidationCode.INVALID_FILENAME: "Attachment filename is invalid",
    AttachmentValidationCode.UNSUPPORTED_MEDIA_TYPE: ("Attachment media type is not supported"),
    AttachmentValidationCode.EXTENSION_MISMATCH: (
        "Attachment filename extension does not match its media type"
    ),
    AttachmentValidationCode.SIZE_MISMATCH: "Attachment size does not match the upload declaration",
    AttachmentValidationCode.CHECKSUM_MISMATCH: (
        "Attachment checksum does not match the upload declaration"
    ),
    AttachmentValidationCode.UPLOAD_METADATA_MISMATCH: (
        "Uploaded object metadata does not match the upload declaration"
    ),
    AttachmentValidationCode.FILE_TOO_LARGE: "Attachment exceeds its byte limit",
    AttachmentValidationCode.MAGIC_MISMATCH: (
        "Attachment content does not match its declared media type"
    ),
    AttachmentValidationCode.INVALID_TEXT_ENCODING: "Text attachment is not valid UTF-8",
    AttachmentValidationCode.UNSAFE_TEXT_CONTENT: "Text attachment contains unsafe control data",
    AttachmentValidationCode.TEXT_TOO_LARGE: "Extracted text exceeds its character limit",
    AttachmentValidationCode.IMAGE_DECODE_FAILED: "Image attachment could not be decoded safely",
    AttachmentValidationCode.IMAGE_ANIMATED: "Animated images are not supported",
    AttachmentValidationCode.IMAGE_DIMENSIONS_EXCEEDED: "Image dimensions exceed their limit",
    AttachmentValidationCode.SAFE_OUTPUT_TOO_LARGE: ("Sanitized attachment exceeds its byte limit"),
}

_ALLOWED_EXTENSIONS: Final[dict[AttachmentMediaType, frozenset[str]]] = {
    AttachmentMediaType.TEXT_PLAIN: frozenset({".txt"}),
    AttachmentMediaType.TEXT_MARKDOWN: frozenset({".md", ".markdown"}),
    AttachmentMediaType.IMAGE_PNG: frozenset({".png"}),
    AttachmentMediaType.IMAGE_JPEG: frozenset({".jpg", ".jpeg"}),
    AttachmentMediaType.IMAGE_WEBP: frozenset({".webp"}),
}


class AttachmentValidationError(ValueError):
    """Expose a stable code without retaining filename, bytes, or extracted text."""

    def __init__(self, code: AttachmentValidationCode) -> None:
        self.code = code
        super().__init__(_SAFE_ERROR_MESSAGES[code])


def _require_declared_string(
    value: object,
    *,
    code: AttachmentValidationCode,
) -> str:
    if not isinstance(value, str):
        raise AttachmentValidationError(code)
    return value


def _require_attachment_bytes(value: object) -> bytes:
    if not isinstance(value, bytes):
        raise TypeError("Attachment content must be immutable bytes")
    return value


@dataclass(frozen=True, slots=True)
class AttachmentLimits:
    """All resource ceilings applied before attachment data reaches a model."""

    max_attachment_bytes: int = MAX_ATTACHMENT_BYTES
    max_text_bytes: int = MAX_TEXT_ATTACHMENT_BYTES
    max_text_characters: int = MAX_TEXT_ATTACHMENT_CHARACTERS
    max_image_bytes: int = MAX_IMAGE_ATTACHMENT_BYTES
    max_image_width: int = MAX_IMAGE_WIDTH
    max_image_height: int = MAX_IMAGE_HEIGHT
    max_image_pixels: int = MAX_IMAGE_PIXELS
    max_display_filename_characters: int = MAX_DISPLAY_FILENAME_CHARACTERS

    def __post_init__(self) -> None:
        for value, maximum, field_name in (
            (self.max_attachment_bytes, MAX_ATTACHMENT_BYTES, "Attachment byte limit"),
            (
                self.max_text_bytes,
                MAX_TEXT_ATTACHMENT_BYTES,
                "Text attachment byte limit",
            ),
            (
                self.max_text_characters,
                MAX_TEXT_ATTACHMENT_CHARACTERS,
                "Text attachment character limit",
            ),
            (
                self.max_image_bytes,
                MAX_IMAGE_ATTACHMENT_BYTES,
                "Image attachment byte limit",
            ),
            (self.max_image_width, MAX_IMAGE_WIDTH, "Image width limit"),
            (self.max_image_height, MAX_IMAGE_HEIGHT, "Image height limit"),
            (self.max_image_pixels, MAX_IMAGE_PIXELS, "Image pixel limit"),
            (
                self.max_display_filename_characters,
                MAX_DISPLAY_FILENAME_CHARACTERS,
                "Display filename limit",
            ),
        ):
            if isinstance(value, bool) or not 1 <= value <= maximum:
                raise ValueError(f"{field_name} must be between 1 and {maximum}")
        if self.max_text_bytes > self.max_attachment_bytes:
            raise ValueError("Text byte limit cannot exceed the attachment byte limit")
        if self.max_image_bytes > self.max_attachment_bytes:
            raise ValueError("Image byte limit cannot exceed the attachment byte limit")

    def maximum_bytes_for(self, media_type: AttachmentMediaType) -> int:
        """Return the stricter global and kind-specific upload ceiling."""

        kind_limit = (
            self.max_text_bytes if media_type.kind is AttachmentKind.TEXT else self.max_image_bytes
        )
        return min(self.max_attachment_bytes, kind_limit)


DEFAULT_ATTACHMENT_LIMITS: Final = AttachmentLimits()


@dataclass(frozen=True, slots=True)
class ParseAttachmentRequest:
    """One uploaded object plus the browser declarations that must be verified."""

    filename: str
    declared_media_type: str
    content: bytes = field(repr=False)
    expected_size_bytes: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "filename",
            _require_declared_string(
                self.filename,
                code=AttachmentValidationCode.INVALID_FILENAME,
            ),
        )
        object.__setattr__(
            self,
            "declared_media_type",
            _require_declared_string(
                self.declared_media_type,
                code=AttachmentValidationCode.UNSUPPORTED_MEDIA_TYPE,
            ),
        )
        object.__setattr__(self, "content", _require_attachment_bytes(self.content))
        if self.expected_size_bytes is not None and (
            isinstance(self.expected_size_bytes, bool) or self.expected_size_bytes < 0
        ):
            raise ValueError("Expected attachment size must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class ParsedImageMetadata:
    """Safe image facts derived by a decoder instead of browser input."""

    width: int
    height: int
    frame_count: int = 1

    def __post_init__(self) -> None:
        if (
            isinstance(self.width, bool)
            or isinstance(self.height, bool)
            or not 1 <= self.width <= MAX_IMAGE_WIDTH
            or not 1 <= self.height <= MAX_IMAGE_HEIGHT
            or self.width * self.height > MAX_IMAGE_PIXELS
        ):
            raise ValueError("Parsed image dimensions are invalid")
        if isinstance(self.frame_count, bool) or self.frame_count != 1:
            raise ValueError("Parsed chat images must contain exactly one frame")

    @property
    def pixel_count(self) -> int:
        """Return the decoded pixel count used by resource accounting."""

        return self.width * self.height


@dataclass(frozen=True, slots=True)
class ParsedAttachment:
    """Sanitized bytes and bounded derived data safe for later application layers."""

    schema_version: int
    display_filename: str
    media_type: AttachmentMediaType
    kind: AttachmentKind
    source_size_bytes: int
    source_sha256: str
    safe_size_bytes: int
    safe_sha256: str
    safe_bytes: bytes = field(repr=False)
    extracted_text: str | None = field(default=None, repr=False)
    image: ParsedImageMetadata | None = None
    parser_version: str = ATTACHMENT_PARSER_VERSION
    sanitizer_version: str = ATTACHMENT_SANITIZER_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != FILE_CONTRACT_SCHEMA_VERSION:
            raise ValueError(f"File contract schema version must be {FILE_CONTRACT_SCHEMA_VERSION}")
        if self.kind is not self.media_type.kind:
            raise ValueError("Parsed attachment kind does not match its media type")
        if sanitize_display_filename(self.display_filename) != self.display_filename:
            raise ValueError("Parsed attachment display filename is not sanitized")
        require_matching_extension(self.display_filename, self.media_type)
        for value, field_name in (
            (self.source_size_bytes, "Source attachment size"),
            (self.safe_size_bytes, "Safe attachment size"),
        ):
            if isinstance(value, bool) or value < 1:
                raise ValueError(f"{field_name} must be a positive integer")
        if self.source_size_bytes > DEFAULT_ATTACHMENT_LIMITS.maximum_bytes_for(self.media_type):
            raise ValueError("Source attachment size exceeds its hard limit")
        if self.safe_size_bytes > DEFAULT_ATTACHMENT_LIMITS.maximum_bytes_for(self.media_type):
            raise ValueError("Safe attachment size exceeds its hard limit")
        if self.safe_size_bytes != len(self.safe_bytes):
            raise ValueError("Safe attachment size does not match its bytes")
        if not _SHA256_PATTERN.fullmatch(self.source_sha256):
            raise ValueError("Source attachment hash is invalid")
        if not _SHA256_PATTERN.fullmatch(self.safe_sha256):
            raise ValueError("Safe attachment hash is invalid")
        if hashlib.sha256(self.safe_bytes).hexdigest() != self.safe_sha256:
            raise ValueError("Safe attachment hash does not match its bytes")
        if self.kind is AttachmentKind.TEXT:
            if (
                self.extracted_text is None
                or not self.extracted_text.strip()
                or self.image is not None
            ):
                raise ValueError("Parsed text requires text and no image metadata")
        elif self.extracted_text is not None or self.image is None:
            raise ValueError("Parsed image requires image metadata and no text")
        if not self.parser_version or not self.sanitizer_version:
            raise ValueError("Parsed attachment versions must not be blank")


class AttachmentParserPort(Protocol):
    """Reusable parser boundary shared by chat now and document ingestion later."""

    def parse(self, request: ParseAttachmentRequest) -> ParsedAttachment:
        """Return verified bounded content or a stable validation error."""

        ...


def sanitize_display_filename(
    filename: object,
    *,
    maximum_characters: int = MAX_DISPLAY_FILENAME_CHARACTERS,
) -> str:
    """Remove client paths/control characters while preserving a useful extension."""

    if isinstance(maximum_characters, bool) or maximum_characters < 1:
        raise ValueError("Display filename limit must be a positive integer")
    if not isinstance(filename, str) or len(filename) > MAX_RAW_FILENAME_CHARACTERS:
        raise AttachmentValidationError(AttachmentValidationCode.INVALID_FILENAME)

    normalized = unicodedata.normalize("NFKC", filename).replace("\\", "/")
    basename = PurePosixPath(normalized).name
    cleaned = "".join(
        character for character in basename if not unicodedata.category(character).startswith("C")
    ).strip(" .\t\r\n")
    if cleaned in {"", ".", ".."}:
        raise AttachmentValidationError(AttachmentValidationCode.INVALID_FILENAME)

    if len(cleaned) > maximum_characters:
        suffix = PurePosixPath(cleaned).suffix
        if len(suffix) >= maximum_characters:
            raise AttachmentValidationError(AttachmentValidationCode.INVALID_FILENAME)
        cleaned = f"{cleaned[: maximum_characters - len(suffix)]}{suffix}"
    return cleaned


def normalize_media_type(value: object) -> AttachmentMediaType:
    """Resolve an exact allow-listed media type and reject parameters or aliases."""

    if not isinstance(value, str) or len(value) > MAX_DECLARED_MEDIA_TYPE_CHARACTERS:
        raise AttachmentValidationError(AttachmentValidationCode.UNSUPPORTED_MEDIA_TYPE)
    normalized = value.strip().lower()
    try:
        return AttachmentMediaType(normalized)
    except ValueError:
        raise AttachmentValidationError(AttachmentValidationCode.UNSUPPORTED_MEDIA_TYPE) from None


def require_matching_extension(
    display_filename: str,
    media_type: AttachmentMediaType,
) -> None:
    """Require the display extension to agree with the declared media type."""

    extension = PurePosixPath(display_filename).suffix.lower()
    if extension not in media_type.allowed_extensions:
        raise AttachmentValidationError(AttachmentValidationCode.EXTENSION_MISMATCH)
