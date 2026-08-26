"""Bounded pre-ingestion validation for raw Knowledge sources."""

from io import BytesIO
from typing import Final

from pypdf import PdfReader
from pypdf.errors import PdfReadError, PdfStreamError

from industry_platform.modules.files.domain import (
    AttachmentMediaType,
    AttachmentValidationCode,
    AttachmentValidationError,
)
from industry_platform.modules.knowledge.domain import ClaimedKnowledgeUpload

KNOWLEDGE_SOURCE_VALIDATOR_VERSION: Final = "knowledge-source-validator-v1"
_UTF8_BOM: Final = b"\xef\xbb\xbf"


def validate_knowledge_source(
    claim: ClaimedKnowledgeUpload,
    content: bytes,
    *,
    actual_sha256: str,
) -> None:
    """Validate structure without extracting pages, text, chunks, or assets."""

    if len(content) != claim.expected_size:
        raise AttachmentValidationError(AttachmentValidationCode.SIZE_MISMATCH)
    if actual_sha256 != claim.expected_sha256:
        raise AttachmentValidationError(AttachmentValidationCode.CHECKSUM_MISMATCH)
    if claim.declared_media_type is AttachmentMediaType.APPLICATION_PDF:
        _validate_pdf(content)
    else:
        _validate_text(content)


def _validate_pdf(content: bytes) -> None:
    try:
        reader = PdfReader(BytesIO(content), strict=True)
        if reader.is_encrypted:
            raise AttachmentValidationError(AttachmentValidationCode.PDF_ENCRYPTED)
        if len(reader.pages) < 1:
            raise AttachmentValidationError(AttachmentValidationCode.EMPTY_FILE)
    except AttachmentValidationError:
        raise
    except (EOFError, OSError, PdfReadError, PdfStreamError, ValueError):
        raise AttachmentValidationError(AttachmentValidationCode.PDF_DECODE_FAILED) from None


def _validate_text(content: bytes) -> None:
    try:
        text = content.removeprefix(_UTF8_BOM).decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise AttachmentValidationError(AttachmentValidationCode.INVALID_TEXT_ENCODING) from None
    if not text.strip():
        raise AttachmentValidationError(AttachmentValidationCode.EMPTY_FILE)
    if any(
        (ord(character) < 32 and character not in {"\t", "\n", "\r"}) or ord(character) == 127
        for character in text
    ):
        raise AttachmentValidationError(AttachmentValidationCode.UNSAFE_TEXT_CONTENT)
