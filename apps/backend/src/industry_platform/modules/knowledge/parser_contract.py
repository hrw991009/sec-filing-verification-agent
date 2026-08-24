"""Knowledge-owned immutable parser and chunker configuration contract."""

from typing import Final

PARSER_SCHEMA_VERSION: Final = 1
PARSER_NAME: Final = "pdfplumber-rapidocr"
PARSER_VERSION: Final = "1.0.0"
CHUNKER_NAME: Final = "bounded-page-chunker"
CHUNKER_VERSION: Final = "1.0.0"

MAX_PDF_PAGES: Final = 250
MAX_EXTRACTED_TEXT_CHARACTERS: Final = 5_000_000
MAX_PAGE_IMAGE_PIXELS: Final = 24_000_000
MAX_PARSER_OUTPUT_BYTES: Final = 64 * 1_024 * 1_024
PARSER_TIMEOUT_SECONDS: Final = 1_200
OCR_RENDER_DPI: Final = 144
MIN_DIGITAL_PAGE_CHARACTERS: Final = 24
MAX_CHUNK_CHARACTERS: Final = 1_200
CHUNK_OVERLAP_CHARACTERS: Final = 120


def parser_config_snapshot(*, max_input_bytes: int) -> dict[str, object]:
    return {
        "budget": {
            "max_input_bytes": max_input_bytes,
            "max_output_bytes": MAX_PARSER_OUTPUT_BYTES,
            "max_page_image_pixels": MAX_PAGE_IMAGE_PIXELS,
            "max_pages": MAX_PDF_PAGES,
            "max_text_characters": MAX_EXTRACTED_TEXT_CHARACTERS,
            "timeout_seconds": PARSER_TIMEOUT_SECONDS,
        },
        "ocr_render_dpi": OCR_RENDER_DPI,
        "schema_version": PARSER_SCHEMA_VERSION,
    }


def chunker_config_snapshot() -> dict[str, object]:
    return {
        "max_characters": MAX_CHUNK_CHARACTERS,
        "overlap_characters": CHUNK_OVERLAP_CHARACTERS,
    }
