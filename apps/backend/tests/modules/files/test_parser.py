"""Real fixture tests for the bounded Day 2 attachment parser."""

import hashlib
import io
from dataclasses import replace

import pytest
from PIL import Image, PngImagePlugin

from industry_platform.modules.files.domain import (
    FILE_CONTRACT_SCHEMA_VERSION,
    AttachmentKind,
    AttachmentLimits,
    AttachmentMediaType,
    AttachmentValidationCode,
    AttachmentValidationError,
    ParseAttachmentRequest,
    normalize_media_type,
    sanitize_display_filename,
)
from industry_platform.modules.files.parser import BoundedAttachmentParser, parse_attachment


def _image_bytes(
    image_format: str,
    *,
    size: tuple[int, int] = (4, 3),
    metadata: bool = False,
) -> bytes:
    image = Image.new("RGB", size, color=(20, 80, 140))
    output = io.BytesIO()
    if image_format == "PNG" and metadata:
        png_info = PngImagePlugin.PngInfo()
        png_info.add_text("Comment", "private source metadata")
        image.save(output, format=image_format, pnginfo=png_info)
    elif image_format == "JPEG" and metadata:
        exif = Image.Exif()
        exif[0x010E] = "private source metadata"
        image.save(
            output,
            format=image_format,
            exif=exif,
            icc_profile=b"not-a-real-profile-but-still-private",
        )
    else:
        image.save(output, format=image_format)
    return output.getvalue()


def _animated_png() -> bytes:
    first = Image.new("RGB", (2, 2), color="red")
    second = Image.new("RGB", (2, 2), color="blue")
    output = io.BytesIO()
    first.save(
        output,
        format="PNG",
        save_all=True,
        append_images=[second],
        duration=100,
        loop=0,
    )
    return output.getvalue()


def _request(
    content: bytes,
    *,
    filename: str,
    media_type: str,
) -> ParseAttachmentRequest:
    return ParseAttachmentRequest(
        filename=filename,
        declared_media_type=media_type,
        content=content,
        expected_size_bytes=len(content),
    )


def test_display_filename_removes_paths_controls_and_keeps_extension_when_truncated() -> None:
    assert sanitize_display_filename("C:\\fakepath\\re\x00port.txt") == "report.txt"
    assert sanitize_display_filename("../../quarterly.md") == "quarterly.md"
    assert sanitize_display_filename("very-long-report-name.txt", maximum_characters=12) == (
        "very-lon.txt"
    )

    with pytest.raises(AttachmentValidationError) as invalid:
        sanitize_display_filename("../\x00..")
    assert invalid.value.code is AttachmentValidationCode.INVALID_FILENAME


def test_text_parser_accepts_utf8_bom_and_normalizes_newlines() -> None:
    content = b"\xef\xbb\xbfheading\r\nbody\rend"
    parsed = parse_attachment(_request(content, filename="notes.txt", media_type="TEXT/PLAIN"))

    assert parsed.schema_version == FILE_CONTRACT_SCHEMA_VERSION
    assert parsed.kind is AttachmentKind.TEXT
    assert parsed.media_type is AttachmentMediaType.TEXT_PLAIN
    assert parsed.extracted_text == "heading\nbody\nend"
    assert parsed.safe_bytes == b"heading\nbody\nend"
    assert parsed.source_size_bytes == len(content)
    assert parsed.source_sha256 == hashlib.sha256(content).hexdigest()
    assert parsed.safe_sha256 == hashlib.sha256(parsed.safe_bytes).hexdigest()
    assert parsed.image is None


def test_markdown_is_bounded_untrusted_text_not_rendered_by_the_parser() -> None:
    content = b"# Report\n\n<script>alert(1)</script>"
    parsed = parse_attachment(
        _request(content, filename="report.markdown", media_type="text/markdown")
    )

    assert parsed.media_type is AttachmentMediaType.TEXT_MARKDOWN
    assert parsed.extracted_text == content.decode()


@pytest.mark.parametrize(
    ("filename", "media_type", "content", "code"),
    [
        ("report.pdf", "application/pdf", b"%PDF-1.7", "unsupported_media_type"),
        ("report.md", "text/plain", b"plain text", "extension_mismatch"),
        (
            "report.txt",
            "text/plain; charset=utf-8",
            b"plain text",
            "unsupported_media_type",
        ),
        ("report.txt", "text/plain", b"%PDF-1.7 fake", "magic_mismatch"),
        ("report.txt", "text/plain", b"\xff\xfe", "invalid_text_encoding"),
        ("report.txt", "text/plain", b"before\x00after", "unsafe_text_content"),
        ("report.txt", "text/plain", "before\u0085after".encode(), "unsafe_text_content"),
        ("report.txt", "text/plain", "before\u202eafter".encode(), "unsafe_text_content"),
        ("report.txt", "text/plain", b" \r\n\t ", "empty_file"),
    ],
)
def test_text_parser_rejects_unsupported_fake_or_unsafe_content(
    filename: str,
    media_type: str,
    content: bytes,
    code: str,
) -> None:
    with pytest.raises(AttachmentValidationError) as rejected:
        parse_attachment(_request(content, filename=filename, media_type=media_type))
    assert rejected.value.code.value == code


def test_parser_checks_declared_bytes_and_separate_text_limits() -> None:
    request = _request(b"abcd", filename="note.txt", media_type="text/plain")

    with pytest.raises(AttachmentValidationError) as mismatch:
        parse_attachment(replace(request, expected_size_bytes=5))
    assert mismatch.value.code is AttachmentValidationCode.SIZE_MISMATCH

    limits = AttachmentLimits(
        max_attachment_bytes=4,
        max_text_bytes=3,
        max_text_characters=3,
        max_image_bytes=4,
    )
    with pytest.raises(AttachmentValidationError) as too_large:
        BoundedAttachmentParser(limits).parse(request)
    assert too_large.value.code is AttachmentValidationCode.FILE_TOO_LARGE

    character_limited = AttachmentLimits(max_text_characters=3)
    with pytest.raises(AttachmentValidationError) as too_much_text:
        BoundedAttachmentParser(character_limited).parse(request)
    assert too_much_text.value.code is AttachmentValidationCode.TEXT_TOO_LARGE


@pytest.mark.parametrize(
    ("image_format", "filename", "media_type"),
    [
        ("PNG", "diagram.png", "image/png"),
        ("JPEG", "photo.jpeg", "image/jpeg"),
        ("WEBP", "chart.webp", "image/webp"),
    ],
)
def test_image_parser_uses_a_real_decoder_and_returns_one_sanitized_static_image(
    image_format: str,
    filename: str,
    media_type: str,
) -> None:
    content = _image_bytes(image_format)
    parsed = parse_attachment(_request(content, filename=filename, media_type=media_type))

    assert parsed.kind is AttachmentKind.IMAGE
    assert parsed.extracted_text is None
    assert parsed.image is not None
    assert (parsed.image.width, parsed.image.height, parsed.image.frame_count) == (4, 3, 1)
    assert parsed.safe_size_bytes == len(parsed.safe_bytes)
    with Image.open(io.BytesIO(parsed.safe_bytes)) as safe_image:
        assert safe_image.format == image_format
        assert safe_image.size == (4, 3)
        assert int(getattr(safe_image, "n_frames", 1)) == 1
        safe_image.verify()


@pytest.mark.parametrize(
    ("image_format", "filename", "media_type"),
    [
        ("PNG", "source.png", "image/png"),
        ("JPEG", "source.jpg", "image/jpeg"),
    ],
)
def test_image_reencoding_removes_source_metadata(
    image_format: str,
    filename: str,
    media_type: str,
) -> None:
    content = _image_bytes(image_format, metadata=True)
    parsed = parse_attachment(_request(content, filename=filename, media_type=media_type))

    assert parsed.source_sha256 != parsed.safe_sha256
    with Image.open(io.BytesIO(parsed.safe_bytes)) as safe_image:
        assert "Comment" not in safe_image.info
        assert "exif" not in safe_image.info
        assert "icc_profile" not in safe_image.info
        assert not safe_image.getexif()


def test_image_parser_rejects_wrong_magic_corruption_animation_and_excess_pixels() -> None:
    png = _image_bytes("PNG", size=(3, 3))

    with pytest.raises(AttachmentValidationError) as wrong_magic:
        parse_attachment(_request(png, filename="fake.jpg", media_type="image/jpeg"))
    assert wrong_magic.value.code is AttachmentValidationCode.MAGIC_MISMATCH

    corrupt = b"\x89PNG\r\n\x1a\nnot-an-image"
    with pytest.raises(AttachmentValidationError) as decode_failure:
        parse_attachment(_request(corrupt, filename="broken.png", media_type="image/png"))
    assert decode_failure.value.code is AttachmentValidationCode.IMAGE_DECODE_FAILED

    animated = _animated_png()
    with pytest.raises(AttachmentValidationError) as animation:
        parse_attachment(_request(animated, filename="animated.png", media_type="image/png"))
    assert animation.value.code is AttachmentValidationCode.IMAGE_ANIMATED

    limits = AttachmentLimits(max_image_width=2, max_image_height=2, max_image_pixels=4)
    with pytest.raises(AttachmentValidationError) as dimensions:
        BoundedAttachmentParser(limits).parse(
            _request(png, filename="large.png", media_type="image/png")
        )
    assert dimensions.value.code is AttachmentValidationCode.IMAGE_DIMENSIONS_EXCEEDED


def test_parse_objects_and_errors_do_not_reveal_file_content_in_repr() -> None:
    secret = b"commercially sensitive forecast"
    request = _request(secret, filename="forecast.txt", media_type="text/plain")
    parsed = parse_attachment(request)

    assert secret.decode() not in repr(request)
    assert secret.decode() not in repr(parsed)

    with pytest.raises(AttachmentValidationError) as captured:
        parse_attachment(
            _request(
                b"before\x00commercially sensitive forecast",
                filename="forecast.txt",
                media_type="text/plain",
            )
        )
    assert secret.decode() not in repr(captured.value)
    assert captured.value.code is AttachmentValidationCode.UNSAFE_TEXT_CONTENT


def test_media_type_allowlist_does_not_accept_similar_or_parameterized_values() -> None:
    assert normalize_media_type(" image/webp ") is AttachmentMediaType.IMAGE_WEBP
    for unsupported in (
        "image/jpg",
        "image/svg+xml",
        "text/html",
        "text/plain;charset=utf-8",
    ):
        with pytest.raises(AttachmentValidationError) as rejected:
            normalize_media_type(unsupported)
        assert rejected.value.code is AttachmentValidationCode.UNSUPPORTED_MEDIA_TYPE
