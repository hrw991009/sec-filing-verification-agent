"""Bounded real parsers for Day 2 text and static image attachments."""

import hashlib
import io
import unicodedata
import warnings
from dataclasses import dataclass
from typing import Final

from PIL import Image, ImageOps

from industry_platform.modules.files.domain import (
    ATTACHMENT_PARSER_VERSION,
    ATTACHMENT_SANITIZER_VERSION,
    DEFAULT_ATTACHMENT_LIMITS,
    FILE_CONTRACT_SCHEMA_VERSION,
    AttachmentKind,
    AttachmentLimits,
    AttachmentMediaType,
    AttachmentValidationCode,
    AttachmentValidationError,
    ParseAttachmentRequest,
    ParsedAttachment,
    ParsedImageMetadata,
    normalize_media_type,
    require_matching_extension,
    sanitize_display_filename,
)

_UTF8_BOM: Final = b"\xef\xbb\xbf"
_PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"
_JPEG_SIGNATURE: Final = b"\xff\xd8\xff"

_PIL_FORMATS: Final[dict[AttachmentMediaType, str]] = {
    AttachmentMediaType.IMAGE_PNG: "PNG",
    AttachmentMediaType.IMAGE_JPEG: "JPEG",
    AttachmentMediaType.IMAGE_WEBP: "WEBP",
}

_BINARY_SIGNATURES: Final[tuple[bytes, ...]] = (
    _PNG_SIGNATURE,
    _JPEG_SIGNATURE,
    b"GIF87a",
    b"GIF89a",
    b"%PDF-",
    b"PK\x03\x04",
    b"PK\x05\x06",
    b"PK\x07\x08",
    b"\x1f\x8b",
    b"7z\xbc\xaf\x27\x1c",
    b"Rar!\x1a\x07",
    b"II*\x00",
    b"MM\x00*",
    b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
)

_DANGEROUS_FORMAT_CONTROLS: Final = frozenset(
    {
        "\u061c",  # Arabic letter mark
        "\u200b",  # zero-width space
        "\u200c",  # zero-width non-joiner
        "\u200d",  # zero-width joiner
        "\u200e",  # left-to-right mark
        "\u200f",  # right-to-left mark
        "\u202a",  # bidi embedding/override controls
        "\u202b",
        "\u202c",
        "\u202d",
        "\u202e",
        "\u2066",  # bidi isolate controls
        "\u2067",
        "\u2068",
        "\u2069",
        "\ufeff",  # an embedded byte-order mark
    }
)


@dataclass(frozen=True, slots=True)
class BoundedAttachmentParser:
    """Parse only the small, explicit Day 2 allow-list under fixed limits."""

    limits: AttachmentLimits = DEFAULT_ATTACHMENT_LIMITS

    def parse(self, request: ParseAttachmentRequest) -> ParsedAttachment:
        """Verify declarations, parse real bytes, and return sanitized output."""

        display_filename = sanitize_display_filename(
            request.filename,
            maximum_characters=self.limits.max_display_filename_characters,
        )
        media_type = normalize_media_type(request.declared_media_type)
        require_matching_extension(display_filename, media_type)
        content = request.content
        self._require_source_size(
            content,
            media_type=media_type,
            expected_size_bytes=request.expected_size_bytes,
        )

        if media_type.kind is AttachmentKind.TEXT:
            return self._parse_text(
                content,
                display_filename=display_filename,
                media_type=media_type,
            )
        return self._parse_image(
            content,
            display_filename=display_filename,
            media_type=media_type,
        )

    def _require_source_size(
        self,
        content: bytes,
        *,
        media_type: AttachmentMediaType,
        expected_size_bytes: int | None,
    ) -> None:
        size_bytes = len(content)
        if expected_size_bytes is not None and size_bytes != expected_size_bytes:
            raise AttachmentValidationError(AttachmentValidationCode.SIZE_MISMATCH)
        if size_bytes == 0:
            raise AttachmentValidationError(AttachmentValidationCode.EMPTY_FILE)

        if size_bytes > self.limits.maximum_bytes_for(media_type):
            raise AttachmentValidationError(AttachmentValidationCode.FILE_TOO_LARGE)

    def _parse_text(
        self,
        content: bytes,
        *,
        display_filename: str,
        media_type: AttachmentMediaType,
    ) -> ParsedAttachment:
        if _looks_like_binary(content):
            raise AttachmentValidationError(AttachmentValidationCode.MAGIC_MISMATCH)
        encoded_text = content.removeprefix(_UTF8_BOM)
        try:
            text = encoded_text.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise AttachmentValidationError(
                AttachmentValidationCode.INVALID_TEXT_ENCODING
            ) from None
        if any(_is_unsafe_text_character(character) for character in text):
            raise AttachmentValidationError(AttachmentValidationCode.UNSAFE_TEXT_CONTENT)

        normalized_text = text.replace("\r\n", "\n").replace("\r", "\n")
        if not normalized_text.strip():
            raise AttachmentValidationError(AttachmentValidationCode.EMPTY_FILE)
        if len(normalized_text) > self.limits.max_text_characters:
            raise AttachmentValidationError(AttachmentValidationCode.TEXT_TOO_LARGE)
        safe_bytes = normalized_text.encode("utf-8")
        if len(safe_bytes) > self.limits.max_text_bytes:
            raise AttachmentValidationError(AttachmentValidationCode.SAFE_OUTPUT_TOO_LARGE)
        return _result(
            display_filename=display_filename,
            media_type=media_type,
            content=content,
            safe_bytes=safe_bytes,
            extracted_text=normalized_text,
        )

    def _parse_image(
        self,
        content: bytes,
        *,
        display_filename: str,
        media_type: AttachmentMediaType,
    ) -> ParsedAttachment:
        if not _magic_matches_image(content, media_type):
            raise AttachmentValidationError(AttachmentValidationCode.MAGIC_MISMATCH)

        expected_format = _PIL_FORMATS[media_type]
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(content)) as probe:
                    if probe.format != expected_format:
                        raise AttachmentValidationError(AttachmentValidationCode.MAGIC_MISMATCH)
                    _require_static_image(probe)
                    self._require_dimensions(probe.width, probe.height)
                    probe.verify()

                with Image.open(io.BytesIO(content)) as decoded:
                    if decoded.format != expected_format:
                        raise AttachmentValidationError(AttachmentValidationCode.MAGIC_MISMATCH)
                    _require_static_image(decoded)
                    self._require_dimensions(decoded.width, decoded.height)
                    decoded.load()
                    oriented = ImageOps.exif_transpose(decoded)
                    self._require_dimensions(oriented.width, oriented.height)
                    safe_bytes = _encode_without_metadata(oriented, media_type)
        except AttachmentValidationError:
            raise
        except Image.DecompressionBombError:
            raise AttachmentValidationError(
                AttachmentValidationCode.IMAGE_DIMENSIONS_EXCEEDED
            ) from None
        except Image.DecompressionBombWarning:
            raise AttachmentValidationError(
                AttachmentValidationCode.IMAGE_DIMENSIONS_EXCEEDED
            ) from None
        except (EOFError, OSError, OverflowError, SyntaxError, ValueError):
            raise AttachmentValidationError(AttachmentValidationCode.IMAGE_DECODE_FAILED) from None

        if len(safe_bytes) > self.limits.max_image_bytes:
            raise AttachmentValidationError(AttachmentValidationCode.SAFE_OUTPUT_TOO_LARGE)
        metadata = _verify_safe_image(
            safe_bytes,
            expected_format=expected_format,
            limits=self.limits,
        )
        return _result(
            display_filename=display_filename,
            media_type=media_type,
            content=content,
            safe_bytes=safe_bytes,
            image=metadata,
        )

    def _require_dimensions(self, width: int, height: int) -> None:
        if (
            width < 1
            or height < 1
            or width > self.limits.max_image_width
            or height > self.limits.max_image_height
            or width * height > self.limits.max_image_pixels
        ):
            raise AttachmentValidationError(AttachmentValidationCode.IMAGE_DIMENSIONS_EXCEEDED)


def parse_attachment(
    request: ParseAttachmentRequest,
    *,
    limits: AttachmentLimits = DEFAULT_ATTACHMENT_LIMITS,
) -> ParsedAttachment:
    """Convenience entry point for application services that do not retain a parser."""

    return BoundedAttachmentParser(limits=limits).parse(request)


def _looks_like_binary(content: bytes) -> bool:
    content_without_bom = content.removeprefix(_UTF8_BOM)
    if content_without_bom.startswith(_BINARY_SIGNATURES):
        return True
    return (
        len(content_without_bom) >= 12
        and content_without_bom.startswith(b"RIFF")
        and content_without_bom[8:12] in {b"WEBP", b"WAVE", b"AVI ", b"ACON", b"RMID"}
    )


def _is_unsafe_text_character(character: str) -> bool:
    if character in _DANGEROUS_FORMAT_CONTROLS:
        return True
    return character not in {"\n", "\r", "\t"} and unicodedata.category(character) == "Cc"


def _magic_matches_image(content: bytes, media_type: AttachmentMediaType) -> bool:
    if media_type is AttachmentMediaType.IMAGE_PNG:
        return content.startswith(_PNG_SIGNATURE)
    if media_type is AttachmentMediaType.IMAGE_JPEG:
        return content.startswith(_JPEG_SIGNATURE)
    if media_type is AttachmentMediaType.IMAGE_WEBP:
        return len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP"
    return False


def _require_static_image(image: Image.Image) -> None:
    frame_count = int(getattr(image, "n_frames", 1))
    if bool(getattr(image, "is_animated", False)) or frame_count != 1:
        raise AttachmentValidationError(AttachmentValidationCode.IMAGE_ANIMATED)


def _encode_without_metadata(
    image: Image.Image,
    media_type: AttachmentMediaType,
) -> bytes:
    has_alpha = "A" in image.getbands() or (image.mode == "P" and "transparency" in image.info)
    output = io.BytesIO()
    if media_type is AttachmentMediaType.IMAGE_PNG:
        normalized = image.convert("RGBA" if has_alpha else "RGB")
        normalized.save(output, format="PNG", optimize=True)
    elif media_type is AttachmentMediaType.IMAGE_JPEG:
        normalized = image.convert("RGB")
        normalized.save(
            output,
            format="JPEG",
            quality=90,
            optimize=True,
            progressive=False,
        )
    else:
        normalized = image.convert("RGBA" if has_alpha else "RGB")
        normalized.save(output, format="WEBP", lossless=True, method=6)
    return output.getvalue()


def _verify_safe_image(
    content: bytes,
    *,
    expected_format: str,
    limits: AttachmentLimits,
) -> ParsedImageMetadata:
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.format != expected_format:
                raise AttachmentValidationError(AttachmentValidationCode.IMAGE_DECODE_FAILED)
            _require_static_image(image)
            width, height = image.size
            if (
                width < 1
                or height < 1
                or width > limits.max_image_width
                or height > limits.max_image_height
                or width * height > limits.max_image_pixels
            ):
                raise AttachmentValidationError(AttachmentValidationCode.IMAGE_DIMENSIONS_EXCEEDED)
            image.verify()
    except AttachmentValidationError:
        raise
    except (EOFError, OSError, OverflowError, SyntaxError, ValueError):
        raise AttachmentValidationError(AttachmentValidationCode.IMAGE_DECODE_FAILED) from None
    return ParsedImageMetadata(width=width, height=height)


def _result(
    *,
    display_filename: str,
    media_type: AttachmentMediaType,
    content: bytes,
    safe_bytes: bytes,
    extracted_text: str | None = None,
    image: ParsedImageMetadata | None = None,
) -> ParsedAttachment:
    return ParsedAttachment(
        schema_version=FILE_CONTRACT_SCHEMA_VERSION,
        display_filename=display_filename,
        media_type=media_type,
        kind=media_type.kind,
        source_size_bytes=len(content),
        source_sha256=hashlib.sha256(content).hexdigest(),
        safe_size_bytes=len(safe_bytes),
        safe_sha256=hashlib.sha256(safe_bytes).hexdigest(),
        safe_bytes=safe_bytes,
        extracted_text=extracted_text,
        image=image,
        parser_version=ATTACHMENT_PARSER_VERSION,
        sanitizer_version=ATTACHMENT_SANITIZER_VERSION,
    )
