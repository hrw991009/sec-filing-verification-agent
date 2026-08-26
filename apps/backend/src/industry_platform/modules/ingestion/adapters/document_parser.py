"""Bounded PDF/TXT/Markdown parser using pdfplumber and RapidOCR."""

import asyncio
import hashlib
import html
import io
import re
from collections.abc import Sequence
from functools import partial
from typing import Protocol, cast

from anyio import to_thread
from PIL import Image

from industry_platform.modules.files.domain import AttachmentMediaType
from industry_platform.modules.ingestion.domain import (
    BoundingBox,
    DocumentParserError,
    ParsedAsset,
    ParsedAssetKind,
    ParsedDocument,
    ParsedPage,
    ParsedTextSource,
    ParserErrorCode,
    ParserRequest,
    sha256_text,
)
from industry_platform.modules.knowledge.parser_contract import (
    MIN_DIGITAL_PAGE_CHARACTERS,
    OCR_RENDER_DPI,
)

_SPACE_PATTERN = re.compile(r"[ \t\f\v]+")
_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


class _OcrResult(Protocol):
    boxes: object
    txts: Sequence[str] | None
    scores: Sequence[float] | None


class _OcrEngine(Protocol):
    def __call__(self, image: object) -> object: ...


class PdfPlumberRapidOcrDocumentParser:
    """Keep all PDF/OCR SDK calls behind the versioned parser port."""

    async def parse(self, request: ParserRequest) -> ParsedDocument:
        try:
            async with asyncio.timeout(request.budget.timeout_seconds):
                document = await to_thread.run_sync(partial(self._parse_sync, request))
        except TimeoutError:
            raise DocumentParserError(ParserErrorCode.TIMEOUT) from None
        snapshot_size = len(document.snapshot_bytes())
        if snapshot_size > request.budget.max_output_bytes:
            raise DocumentParserError(ParserErrorCode.OUTPUT_LIMIT_EXCEEDED)
        return document

    def _parse_sync(self, request: ParserRequest) -> ParsedDocument:
        if request.media_type is AttachmentMediaType.APPLICATION_PDF:
            return self._parse_pdf(request)
        if request.media_type in {
            AttachmentMediaType.TEXT_PLAIN,
            AttachmentMediaType.TEXT_MARKDOWN,
        }:
            return self._parse_text(request)
        raise DocumentParserError(ParserErrorCode.UNSUPPORTED_MEDIA_TYPE)

    def _parse_text(self, request: ParserRequest) -> ParsedDocument:
        try:
            text = request.content.decode("utf-8")
        except UnicodeDecodeError:
            raise DocumentParserError(ParserErrorCode.CORRUPT_DOCUMENT) from None
        normalized = _normalize_text(text)
        if not normalized:
            raise DocumentParserError(ParserErrorCode.EMPTY_DOCUMENT)
        if len(normalized) > request.budget.max_text_characters:
            raise DocumentParserError(ParserErrorCode.TEXT_LIMIT_EXCEEDED)
        source = (
            ParsedTextSource.MARKDOWN
            if request.media_type is AttachmentMediaType.TEXT_MARKDOWN
            else ParsedTextSource.PLAIN_TEXT
        )
        title_path = _markdown_title_path(normalized) if source is ParsedTextSource.MARKDOWN else ()
        page = ParsedPage(
            page_number=1,
            width_points=612.0,
            height_points=792.0,
            text=normalized,
            text_source=source,
            bbox=BoundingBox(0.0, 0.0, 612.0, 792.0),
            title_path=title_path,
            content_hash=sha256_text(normalized),
        )
        return ParsedDocument(
            source_sha256=request.source_sha256,
            media_type=request.media_type,
            pages=(page,),
            assets=(),
        )

    def _parse_pdf(self, request: ParserRequest) -> ParsedDocument:
        try:
            import numpy as np
            import pdfplumber
            from pdfminer.pdfdocument import PDFEncryptionError, PDFPasswordIncorrect
            from pdfminer.pdfparser import PDFSyntaxError
            from pdfplumber.utils.exceptions import PdfminerException
            from rapidocr import RapidOCR
        except ImportError:
            raise DocumentParserError(ParserErrorCode.DEPENDENCY_MISSING) from None

        try:
            with pdfplumber.open(io.BytesIO(request.content)) as pdf:
                if pdf.doc.is_extractable is False:
                    raise DocumentParserError(ParserErrorCode.ENCRYPTED_DOCUMENT)
                if not pdf.pages:
                    raise DocumentParserError(ParserErrorCode.EMPTY_DOCUMENT)
                if len(pdf.pages) > request.budget.max_pages:
                    raise DocumentParserError(ParserErrorCode.PAGE_LIMIT_EXCEEDED)

                pages: list[ParsedPage] = []
                assets: list[ParsedAsset] = []
                text_characters = 0
                ocr_engine: _OcrEngine | None = None

                for page in pdf.pages:
                    page_number = int(page.page_number)
                    width = round(float(page.width), 3)
                    height = round(float(page.height), 3)
                    full_bbox = BoundingBox(0.0, 0.0, width, height)
                    digital_text = _normalize_text(page.extract_text(layout=True) or "")
                    needs_ocr = (
                        len(digital_text.replace("\n", "").strip()) < MIN_DIGITAL_PAGE_CHARACTERS
                    )
                    has_assets = bool(page.images) or bool(page.lines) or bool(page.rects)
                    page_image: Image.Image | None = None

                    if needs_ocr or has_assets:
                        page_image = page.to_image(
                            resolution=OCR_RENDER_DPI,
                            antialias=True,
                        ).original.convert("RGB")
                        if (
                            page_image.width * page_image.height
                            > request.budget.max_page_image_pixels
                        ):
                            raise DocumentParserError(ParserErrorCode.IMAGE_PIXEL_LIMIT_EXCEEDED)

                    if needs_ocr:
                        if page_image is None:
                            raise DocumentParserError(ParserErrorCode.DEPENDENCY_FAILED)
                        if ocr_engine is None:
                            try:
                                ocr_engine = cast(_OcrEngine, RapidOCR())
                            except Exception:
                                raise DocumentParserError(
                                    ParserErrorCode.DEPENDENCY_MISSING
                                ) from None
                        try:
                            result = cast(_OcrResult, ocr_engine(np.asarray(page_image)))
                        except Exception:
                            raise DocumentParserError(ParserErrorCode.DEPENDENCY_FAILED) from None
                        page_text = _normalize_text("\n".join(result.txts or ()))
                        text_source = ParsedTextSource.OCR
                    else:
                        page_text = digital_text
                        text_source = ParsedTextSource.DIGITAL

                    if not page_text:
                        page_text = f"[Page {page_number} contains no extractable text]"
                    text_characters += len(page_text)
                    if text_characters > request.budget.max_text_characters:
                        raise DocumentParserError(ParserErrorCode.TEXT_LIMIT_EXCEEDED)

                    title_path = _pdf_title_path(page_text)
                    pages.append(
                        ParsedPage(
                            page_number=page_number,
                            width_points=width,
                            height_points=height,
                            text=page_text,
                            text_source=text_source,
                            bbox=full_bbox,
                            title_path=title_path,
                            content_hash=sha256_text(page_text),
                        )
                    )
                    if page_image is not None:
                        self._extract_page_assets(
                            page=page,
                            page_image=page_image,
                            page_number=page_number,
                            page_width=width,
                            page_height=height,
                            title_path=title_path,
                            assets=assets,
                        )
                    page.close()

            return ParsedDocument(
                source_sha256=request.source_sha256,
                media_type=request.media_type,
                pages=tuple(pages),
                assets=tuple(assets),
            )
        except DocumentParserError:
            raise
        except PdfminerException as error:
            if any(
                isinstance(item, (PDFPasswordIncorrect, PDFEncryptionError)) for item in error.args
            ):
                raise DocumentParserError(ParserErrorCode.ENCRYPTED_DOCUMENT) from None
            raise DocumentParserError(ParserErrorCode.CORRUPT_DOCUMENT) from None
        except (PDFPasswordIncorrect, PDFEncryptionError):
            raise DocumentParserError(ParserErrorCode.ENCRYPTED_DOCUMENT) from None
        except PDFSyntaxError:
            raise DocumentParserError(ParserErrorCode.CORRUPT_DOCUMENT) from None
        except Exception:
            raise DocumentParserError(ParserErrorCode.CORRUPT_DOCUMENT) from None

    def _extract_page_assets(
        self,
        *,
        page: object,
        page_image: Image.Image,
        page_number: int,
        page_width: float,
        page_height: float,
        title_path: tuple[str, ...],
        assets: list[ParsedAsset],
    ) -> None:
        page_with_objects = cast(_PdfPage, page)
        table_boxes: list[BoundingBox] = []
        for table in page_with_objects.find_tables():
            bbox = _bounded_bbox(table.bbox, page_width=page_width, page_height=page_height)
            rows = table.extract()
            table_html = _table_html(rows)
            preview = _crop_png(page_image, bbox, page_width=page_width, page_height=page_height)
            table_boxes.append(bbox)
            assets.append(
                ParsedAsset(
                    ordinal=len(assets) + 1,
                    page_number=page_number,
                    kind=ParsedAssetKind.TABLE,
                    bbox=bbox,
                    title_path=title_path,
                    content_hash=sha256_text(table_html),
                    preview_sha256=hashlib.sha256(preview).hexdigest(),
                    preview_mime_type="image/png",
                    preview=preview,
                    html=table_html,
                )
            )

        for raw_image in page_with_objects.images:
            image_bbox = _image_bbox(
                raw_image,
                page_width=page_width,
                page_height=page_height,
            )
            if image_bbox is None or any(
                _mostly_overlaps(image_bbox, table_bbox) for table_bbox in table_boxes
            ):
                continue
            preview = _crop_png(
                page_image,
                image_bbox,
                page_width=page_width,
                page_height=page_height,
            )
            assets.append(
                ParsedAsset(
                    ordinal=len(assets) + 1,
                    page_number=page_number,
                    kind=ParsedAssetKind.IMAGE,
                    bbox=image_bbox,
                    title_path=title_path,
                    content_hash=hashlib.sha256(preview).hexdigest(),
                    preview_sha256=hashlib.sha256(preview).hexdigest(),
                    preview_mime_type="image/png",
                    preview=preview,
                )
            )


class _PdfTable(Protocol):
    bbox: tuple[float, float, float, float]

    def extract(self) -> list[list[str | None]]: ...


class _PdfPage(Protocol):
    images: list[dict[str, object]]

    def find_tables(self) -> list[_PdfTable]: ...


def _normalize_text(value: str) -> str:
    lines = [_SPACE_PATTERN.sub(" ", line).strip() for line in value.replace("\r", "").split("\n")]
    normalized: list[str] = []
    blank = False
    for line in lines:
        if line:
            normalized.append(line)
            blank = False
        elif normalized and not blank:
            normalized.append("")
            blank = True
    return "\n".join(normalized).strip()


def _markdown_title_path(value: str) -> tuple[str, ...]:
    headings: list[str] = []
    for line in value.splitlines():
        match = _HEADING_PATTERN.match(line)
        if match is None:
            continue
        level = len(match.group(1))
        headings = headings[: level - 1]
        headings.append(match.group(2).strip()[:240])
    return tuple(headings[-6:])


def _pdf_title_path(value: str) -> tuple[str, ...]:
    first_line = value.splitlines()[0].strip()
    if 1 <= len(first_line) <= 160:
        return (first_line,)
    return ()


def _bounded_bbox(value: Sequence[float], *, page_width: float, page_height: float) -> BoundingBox:
    if len(value) != 4:
        raise DocumentParserError(ParserErrorCode.CORRUPT_DOCUMENT)
    x0, top, x1, bottom = (round(float(item), 3) for item in value)
    x0 = min(max(0.0, x0), page_width)
    x1 = min(max(0.0, x1), page_width)
    top = min(max(0.0, top), page_height)
    bottom = min(max(0.0, bottom), page_height)
    if x1 - x0 < 1.0 or bottom - top < 1.0:
        raise DocumentParserError(ParserErrorCode.CORRUPT_DOCUMENT)
    return BoundingBox(x0, top, x1, bottom)


def _image_bbox(
    image: dict[str, object], *, page_width: float, page_height: float
) -> BoundingBox | None:
    values = tuple(image.get(key) for key in ("x0", "top", "x1", "bottom"))
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
        return None
    return _bounded_bbox(
        cast(tuple[float, float, float, float], values),
        page_width=page_width,
        page_height=page_height,
    )


def _crop_png(
    page_image: Image.Image,
    bbox: BoundingBox,
    *,
    page_width: float,
    page_height: float,
) -> bytes:
    scale_x = page_image.width / page_width
    scale_y = page_image.height / page_height
    crop_box = (
        max(0, int(bbox.x0 * scale_x)),
        max(0, int(bbox.top * scale_y)),
        min(page_image.width, max(1, int(bbox.x1 * scale_x + 0.999))),
        min(page_image.height, max(1, int(bbox.bottom * scale_y + 0.999))),
    )
    output = io.BytesIO()
    page_image.crop(crop_box).save(output, format="PNG", optimize=True)
    return output.getvalue()


def _table_html(rows: Sequence[Sequence[str | None]]) -> str:
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape((cell or '').strip())}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return f"<table><tbody>{body}</tbody></table>"


def _mostly_overlaps(left: BoundingBox, right: BoundingBox) -> bool:
    intersection_width = max(0.0, min(left.x1, right.x1) - max(left.x0, right.x0))
    intersection_height = max(0.0, min(left.bottom, right.bottom) - max(left.top, right.top))
    intersection = intersection_width * intersection_height
    left_area = (left.x1 - left.x0) * (left.bottom - left.top)
    return left_area > 0 and intersection / left_area >= 0.9
