"""Deterministic bounded page chunking with same-page asset relations."""

import re

from industry_platform.modules.ingestion.domain import (
    ParsedChunk,
    ParsedDocument,
    sha256_text,
)
from industry_platform.modules.knowledge.parser_contract import (
    CHUNK_OVERLAP_CHARACTERS,
    MAX_CHUNK_CHARACTERS,
)

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+|[\u3400-\u9fff]|[^\s]")


class BoundedPageChunker:
    def chunk(self, document: ParsedDocument) -> tuple[ParsedChunk, ...]:
        chunks: list[ParsedChunk] = []
        assets_by_page: dict[int, tuple[int, ...]] = {}
        for page in document.pages:
            assets_by_page[page.page_number] = tuple(
                asset.ordinal for asset in document.assets if asset.page_number == page.page_number
            )
            for text in _page_chunks(page.text):
                chunks.append(
                    ParsedChunk(
                        ordinal=len(chunks) + 1,
                        page_number=page.page_number,
                        text=text,
                        title_path=page.title_path,
                        bbox=page.bbox,
                        content_hash=sha256_text(
                            f"{page.page_number}\x00{len(chunks) + 1}\x00{text}"
                        ),
                        token_count=max(1, len(_TOKEN_PATTERN.findall(text))),
                        asset_ordinals=assets_by_page[page.page_number],
                    )
                )
        return tuple(chunks)


def _page_chunks(value: str) -> tuple[str, ...]:
    paragraphs = tuple(part.strip() for part in re.split(r"\n\s*\n", value) if part.strip())
    if not paragraphs:
        return (value.strip(),)
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        for segment in _bounded_segments(paragraph):
            candidate = segment if not current else f"{current}\n\n{segment}"
            if len(candidate) <= MAX_CHUNK_CHARACTERS:
                current = candidate
                continue
            if current:
                chunks.append(current)
            current = segment
    if current:
        chunks.append(current)
    return tuple(chunks)


def _bounded_segments(value: str) -> tuple[str, ...]:
    if len(value) <= MAX_CHUNK_CHARACTERS:
        return (value,)
    segments: list[str] = []
    start = 0
    while start < len(value):
        end = min(len(value), start + MAX_CHUNK_CHARACTERS)
        if end < len(value):
            boundary = value.rfind(" ", start + MAX_CHUNK_CHARACTERS // 2, end)
            if boundary > start:
                end = boundary
        segment = value[start:end].strip()
        if segment:
            segments.append(segment)
        if end >= len(value):
            break
        start = max(start + 1, end - CHUNK_OVERLAP_CHARACTERS)
    return tuple(segments)
