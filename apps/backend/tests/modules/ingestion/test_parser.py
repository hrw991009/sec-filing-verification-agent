from __future__ import annotations

import hashlib
import io
import time
from pathlib import Path
from uuid import uuid4

import pytest
import reportlab
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from industry_platform.modules.files.domain import AttachmentMediaType
from industry_platform.modules.ingestion.adapters.document_parser import (
    PdfPlumberRapidOcrDocumentParser,
)
from industry_platform.modules.ingestion.chunker import BoundedPageChunker
from industry_platform.modules.ingestion.domain import (
    DocumentParserError,
    ParsedAssetKind,
    ParsedDocument,
    ParsedTextSource,
    ParserBudget,
    ParserErrorCode,
    ParserRequest,
)


def _request(
    content: bytes,
    media_type: AttachmentMediaType,
    name: str,
    *,
    budget: ParserBudget | None = None,
) -> ParserRequest:
    return ParserRequest(
        document_version_id=uuid4(),
        original_name=name,
        media_type=media_type,
        source_sha256=hashlib.sha256(content).hexdigest(),
        content=content,
        budget=budget or ParserBudget(max_input_bytes=25 * 1_024 * 1_024),
    )


def _digital_pdf() -> bytes:
    output = io.BytesIO()
    document = canvas.Canvas(output, pagesize=letter, pageCompression=0)
    document.setFont("Helvetica-Bold", 18)
    document.drawString(72, 720, "Digital market outlook")
    document.setFont("Helvetica", 12)
    document.drawString(72, 690, "Revenue increased by 18 percent in 2026.")
    document.save()
    return output.getvalue()


def _scanned_pdf() -> bytes:
    image = Image.new("RGB", (1_600, 500), "white")
    draw = ImageDraw.Draw(image)
    font_path = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
    font = ImageFont.truetype(str(font_path), 76)
    draw.text((70, 170), "SCANNED INVOICE 4827", fill="black", font=font)
    image_bytes = io.BytesIO()
    image.save(image_bytes, format="PNG")

    output = io.BytesIO()
    document = canvas.Canvas(output, pagesize=letter, pageCompression=0)
    document.drawImage(
        ImageReader(io.BytesIO(image_bytes.getvalue())),
        40,
        300,
        width=530,
        height=166,
        preserveAspectRatio=True,
        mask="auto",
    )
    document.save()
    return output.getvalue()


def _chart_table_pdf() -> bytes:
    chart = Image.new("RGB", (480, 240), "white")
    draw = ImageDraw.Draw(chart)
    draw.rectangle((40, 40, 110, 210), fill="#2f6fed")
    draw.rectangle((150, 90, 220, 210), fill="#e05a47")
    draw.rectangle((260, 125, 330, 210), fill="#1f8a70")
    draw.text((40, 10), "Quarterly output", fill="black")
    chart_bytes = io.BytesIO()
    chart.save(chart_bytes, format="PNG")

    output = io.BytesIO()
    document = canvas.Canvas(output, pagesize=letter, pageCompression=0)
    _, height = letter
    for page_number in range(1, 21):
        document.setFont("Helvetica-Bold", 16)
        document.drawString(60, height - 60, f"Industry report page {page_number}")
        document.setFont("Helvetica", 11)
        document.drawString(60, height - 85, f"Traceable narrative for page {page_number}.")
        if page_number == 1:
            document.drawImage(
                ImageReader(io.BytesIO(chart_bytes.getvalue())),
                60,
                430,
                width=300,
                height=150,
                mask="auto",
            )
            left, bottom, table_width, row_height = 60, 180, 480, 42
            columns = (0, 120, 240, 360, 480)
            rows = (0, 42, 84, 126, 168)
            document.setStrokeColor(HexColor("#475569"))
            for offset in rows:
                document.line(left, bottom + offset, left + table_width, bottom + offset)
            document.line(left, bottom, left, bottom + rows[-1])
            document.line(left + table_width, bottom, left + table_width, bottom + rows[-1])
            for offset in columns[1:-1]:
                document.line(left + offset, bottom, left + offset, bottom + rows[-2])
            labels = (
                ("Region summary",),
                ("Region", "Q1", "Q2", "Q3"),
                ("North", "12", "15", "18"),
                ("South", "9", "11", "14"),
            )
            for row_index, row in enumerate(labels):
                y = bottom + rows[-1] - (row_index + 1) * row_height + 14
                for column_index, value in enumerate(row):
                    x = left + 8 + (0 if row_index == 0 else column_index * 120)
                    document.drawString(x, y, value)
        document.showPage()
    document.save()
    return output.getvalue()


def _encrypted_pdf() -> bytes:
    reader = PdfReader(io.BytesIO(_digital_pdf()))
    writer = PdfWriter()
    writer.append_pages_from_reader(reader)
    writer.encrypt("private")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _two_page_pdf() -> bytes:
    output = io.BytesIO()
    document = canvas.Canvas(output, pagesize=letter, pageCompression=0)
    for page_number in (1, 2):
        document.drawString(72, 720, f"Bounded page {page_number} with extractable digital text.")
        document.showPage()
    document.save()
    return output.getvalue()


@pytest.mark.asyncio
async def test_parser_handles_plain_text_and_markdown_with_versioned_locators() -> None:
    parser = PdfPlumberRapidOcrDocumentParser()
    plain = await parser.parse(
        _request(
            b"Market demand remains resilient.\n\nCapacity expands next quarter.",
            AttachmentMediaType.TEXT_PLAIN,
            "outlook.txt",
        )
    )
    markdown = await parser.parse(
        _request(
            b"# Sector\n\n## Capacity\n\nUtilization reached 82 percent.",
            AttachmentMediaType.TEXT_MARKDOWN,
            "outlook.md",
        )
    )

    assert plain.pages[0].text_source is ParsedTextSource.PLAIN_TEXT
    assert markdown.pages[0].text_source is ParsedTextSource.MARKDOWN
    assert markdown.pages[0].title_path == ("Sector", "Capacity")
    assert plain.pages[0].content_hash != markdown.pages[0].content_hash


@pytest.mark.asyncio
async def test_parser_extracts_digital_pdf_text_and_page_locator() -> None:
    parsed = await PdfPlumberRapidOcrDocumentParser().parse(
        _request(_digital_pdf(), AttachmentMediaType.APPLICATION_PDF, "digital.pdf")
    )

    assert parsed.pages[0].page_number == 1
    assert parsed.pages[0].text_source is ParsedTextSource.DIGITAL
    assert "Revenue increased by 18 percent" in parsed.pages[0].text
    assert parsed.pages[0].bbox.x1 > parsed.pages[0].bbox.x0


@pytest.mark.asyncio
async def test_parser_uses_real_ocr_for_scanned_pdf() -> None:
    parsed = await PdfPlumberRapidOcrDocumentParser().parse(
        _request(_scanned_pdf(), AttachmentMediaType.APPLICATION_PDF, "scan.pdf")
    )

    assert parsed.pages[0].text_source is ParsedTextSource.OCR
    assert "4827" in parsed.pages[0].text


@pytest.mark.asyncio
async def test_parser_extracts_20_page_image_and_complex_table_assets() -> None:
    parsed = await PdfPlumberRapidOcrDocumentParser().parse(
        _request(_chart_table_pdf(), AttachmentMediaType.APPLICATION_PDF, "report.pdf")
    )

    assert len(parsed.pages) == 20
    image = next(asset for asset in parsed.assets if asset.kind is ParsedAssetKind.IMAGE)
    table = next(asset for asset in parsed.assets if asset.kind is ParsedAssetKind.TABLE)
    assert image.page_number == table.page_number == 1
    assert table.html is not None
    assert "Region summary" in table.html
    assert table.bbox.x1 > table.bbox.x0
    assert image.preview.startswith(b"\x89PNG")

    chunks = BoundedPageChunker().chunk(parsed)
    first_page_chunks = tuple(chunk for chunk in chunks if chunk.page_number == 1)
    assert first_page_chunks
    assert image.ordinal in first_page_chunks[0].asset_ordinals
    assert table.ordinal in first_page_chunks[0].asset_ordinals


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"", ParserErrorCode.EMPTY_DOCUMENT),
        (b"\xff\xfe", ParserErrorCode.CORRUPT_DOCUMENT),
    ],
)
async def test_text_parser_classifies_empty_and_corrupt_sources(
    content: bytes,
    expected: ParserErrorCode,
) -> None:
    if not content:
        content = b" \n\t"
    request = _request(content, AttachmentMediaType.TEXT_PLAIN, "invalid.txt")

    with pytest.raises(DocumentParserError) as captured:
        await PdfPlumberRapidOcrDocumentParser().parse(request)

    assert captured.value.code is expected
    assert captured.value.retryable is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "media_type", "name", "budget", "expected"),
    [
        (
            _encrypted_pdf(),
            AttachmentMediaType.APPLICATION_PDF,
            "encrypted.pdf",
            ParserBudget(max_input_bytes=25 * 1_024 * 1_024),
            ParserErrorCode.ENCRYPTED_DOCUMENT,
        ),
        (
            _two_page_pdf(),
            AttachmentMediaType.APPLICATION_PDF,
            "pages.pdf",
            ParserBudget(max_input_bytes=25 * 1_024 * 1_024, max_pages=1),
            ParserErrorCode.PAGE_LIMIT_EXCEEDED,
        ),
        (
            b"text beyond the configured limit",
            AttachmentMediaType.TEXT_PLAIN,
            "large.txt",
            ParserBudget(max_input_bytes=1_024, max_text_characters=4),
            ParserErrorCode.TEXT_LIMIT_EXCEEDED,
        ),
        (
            _chart_table_pdf(),
            AttachmentMediaType.APPLICATION_PDF,
            "pixels.pdf",
            ParserBudget(max_input_bytes=25 * 1_024 * 1_024, max_page_image_pixels=10),
            ParserErrorCode.IMAGE_PIXEL_LIMIT_EXCEEDED,
        ),
        (
            b"bounded output",
            AttachmentMediaType.TEXT_PLAIN,
            "output.txt",
            ParserBudget(max_input_bytes=1_024, max_output_bytes=1),
            ParserErrorCode.OUTPUT_LIMIT_EXCEEDED,
        ),
    ],
)
async def test_parser_enforces_stable_resource_and_security_limits(
    content: bytes,
    media_type: AttachmentMediaType,
    name: str,
    budget: ParserBudget,
    expected: ParserErrorCode,
) -> None:
    with pytest.raises(DocumentParserError) as captured:
        await PdfPlumberRapidOcrDocumentParser().parse(
            _request(content, media_type, name, budget=budget)
        )

    assert captured.value.code is expected
    assert captured.value.retryable is False


@pytest.mark.asyncio
async def test_parser_timeout_has_a_retryable_stable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser = PdfPlumberRapidOcrDocumentParser()
    original = parser._parse_sync

    def slow_parse(request: ParserRequest) -> ParsedDocument:
        time.sleep(1.1)
        return original(request)

    monkeypatch.setattr(parser, "_parse_sync", slow_parse)
    source = _digital_pdf()
    with pytest.raises(DocumentParserError) as captured:
        await parser.parse(
            _request(
                source,
                AttachmentMediaType.APPLICATION_PDF,
                "timeout.pdf",
                budget=ParserBudget(max_input_bytes=len(source), timeout_seconds=1),
            )
        )

    assert captured.value.code is ParserErrorCode.TIMEOUT
    assert captured.value.retryable is True
