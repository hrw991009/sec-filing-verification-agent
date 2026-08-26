"""Contract tests for bounded Knowledge source validation."""

import hashlib
from io import BytesIO
from uuid import UUID

import pytest
from pypdf import PdfWriter

from industry_platform.modules.files.domain import (
    AttachmentMediaType,
    AttachmentValidationCode,
    AttachmentValidationError,
)
from industry_platform.modules.knowledge.domain import ClaimedKnowledgeUpload
from industry_platform.modules.knowledge.source_validation import validate_knowledge_source


def pdf(*, encrypted: bool = False) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    if encrypted:
        writer.encrypt("not-persisted")
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def claim(content: bytes, media_type: AttachmentMediaType) -> ClaimedKnowledgeUpload:
    return ClaimedKnowledgeUpload(
        file_id=UUID("11111111-1111-4111-8111-111111111111"),
        workspace_id=UUID("22222222-2222-4222-8222-222222222222"),
        knowledge_base_id=UUID("33333333-3333-4333-8333-333333333333"),
        created_by_user_id=UUID("44444444-4444-4444-8444-444444444444"),
        original_name=(
            "report.pdf" if media_type is AttachmentMediaType.APPLICATION_PDF else "notes.txt"
        ),
        declared_media_type=media_type,
        bucket="private",
        staging_key="staging/private",
        expected_size=len(content),
        expected_sha256=hashlib.sha256(content).hexdigest(),
        revision=1,
    )


@pytest.mark.parametrize(
    ("content", "media_type"),
    [
        (pdf(), AttachmentMediaType.APPLICATION_PDF),
        (b"bounded source\n", AttachmentMediaType.TEXT_PLAIN),
    ],
)
def test_valid_source_passes_without_extraction(
    content: bytes, media_type: AttachmentMediaType
) -> None:
    validate_knowledge_source(
        claim(content, media_type),
        content,
        actual_sha256=hashlib.sha256(content).hexdigest(),
    )


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (b"%PDF-1.7\nbroken\n%%EOF", AttachmentValidationCode.PDF_DECODE_FAILED),
        (pdf(encrypted=True), AttachmentValidationCode.PDF_ENCRYPTED),
    ],
)
def test_corrupt_and_encrypted_pdf_fail_explicitly(
    content: bytes, code: AttachmentValidationCode
) -> None:
    with pytest.raises(AttachmentValidationError) as error:
        validate_knowledge_source(
            claim(content, AttachmentMediaType.APPLICATION_PDF),
            content,
            actual_sha256=hashlib.sha256(content).hexdigest(),
        )
    assert error.value.code is code


def test_checksum_and_text_encoding_are_reverified() -> None:
    content = b"source"
    with pytest.raises(AttachmentValidationError) as checksum:
        validate_knowledge_source(
            claim(content, AttachmentMediaType.TEXT_PLAIN), content, actual_sha256="0" * 64
        )
    assert checksum.value.code is AttachmentValidationCode.CHECKSUM_MISMATCH

    invalid = b"\xff\xfe"
    with pytest.raises(AttachmentValidationError) as encoding:
        validate_knowledge_source(
            claim(invalid, AttachmentMediaType.TEXT_PLAIN),
            invalid,
            actual_sha256=hashlib.sha256(invalid).hexdigest(),
        )
    assert encoding.value.code is AttachmentValidationCode.INVALID_TEXT_ENCODING
