"""Deterministic SEC HTML table extraction and chunk-local locator parsing."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Final

from industry_platform.modules.disclosures.domain import SecFilingTableCell
from industry_platform.modules.knowledge.parser_contract import MAX_CHUNK_CHARACTERS

_SKIPPED_TAGS: Final = frozenset({"script", "style", "noscript", "template", "ix:hidden"})
_BLOCK_TAGS: Final = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "div",
        "dl",
        "dt",
        "dd",
        "figcaption",
        "figure",
        "footer",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "ul",
    }
)
_SPACE_PATTERN: Final = re.compile(r"\s+")
_CELL_MARKER: Final = re.compile(
    r"^\[SEC_TABLE_CELL_V1 table=(?P<table>[1-9][0-9]*) "
    r"row=(?P<row>[1-9][0-9]*) column=(?P<column>[1-9][0-9]*) "
    r"rowspan=(?P<rowspan>[1-9][0-9]*) colspan=(?P<colspan>[1-9][0-9]*) "
    r"sha256=(?P<sha256>[a-f0-9]{64})\] (?P<text>.+)$"
)
_MAX_TABLES: Final = 1_000
_MAX_CELLS: Final = 100_000
_MAX_CELL_CHARACTERS: Final = MAX_CHUNK_CHARACTERS - 240


@dataclass(slots=True)
class _TableState:
    table_index: int
    row_index: int = 0
    next_column: int = 1
    occupied_through_row: dict[int, int] = field(default_factory=dict)
    cell_column: int | None = None
    cell_row_span: int = 1
    cell_column_span: int = 1
    cell_parts: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class FilingHtmlExtraction:
    markdown: str
    table_cells: tuple[SecFilingTableCell, ...]


class FilingHtmlExtractor(HTMLParser):
    """Extract visible text while preserving top-level table cell coordinates."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._cells: list[SecFilingTableCell] = []
        self._skipped_depth = 0
        self._heading_level: int | None = None
        self._table_depth = 0
        self._table: _TableState | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.casefold()
        if normalized in _SKIPPED_TAGS:
            self._skipped_depth += 1
            return
        if self._skipped_depth:
            return
        if normalized == "table":
            self._start_table()
            return
        if self._table_depth:
            self._handle_table_start(normalized, attrs)
            return
        if normalized in _BLOCK_TAGS or _heading_level(normalized) is not None:
            self._parts.append("\n")
        level = _heading_level(normalized)
        if level is not None:
            self._heading_level = level
            self._parts.append(f"{'#' * level} ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if normalized in _SKIPPED_TAGS:
            if self._skipped_depth:
                self._skipped_depth -= 1
            return
        if self._skipped_depth:
            return
        if normalized == "table" and self._table_depth:
            self._end_table()
            return
        if self._table_depth:
            self._handle_table_end(normalized)
            return
        if normalized in _BLOCK_TAGS or _heading_level(normalized) is not None:
            self._parts.append("\n")
        if _heading_level(normalized) is not None:
            self._heading_level = None

    def handle_data(self, data: str) -> None:
        if self._skipped_depth:
            return
        if self._table_depth:
            if self._table is not None and self._table.cell_column is not None:
                self._table.cell_parts.append(data)
            return
        self._parts.append(data)

    def extraction(self) -> FilingHtmlExtraction:
        if self._table_depth or self._table is not None:
            raise ValueError("SEC filing HTML contains an unclosed table")
        return FilingHtmlExtraction(
            markdown=_normalize_document_text("".join(self._parts)),
            table_cells=tuple(self._cells),
        )

    def _start_table(self) -> None:
        self._table_depth += 1
        if self._table_depth != 1:
            return
        table_index = 1 + sum(1 for part in self._parts if part.startswith("\n\n## SEC Table "))
        if table_index > _MAX_TABLES:
            raise ValueError("SEC filing HTML contains too many tables")
        self._table = _TableState(table_index=table_index)
        self._parts.append(f"\n\n## SEC Table {table_index}\n\n")

    def _end_table(self) -> None:
        if self._table_depth == 1:
            self._finish_cell()
            self._table = None
            self._parts.append("\n")
        self._table_depth -= 1

    def _handle_table_start(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        table = self._table
        if table is None or self._table_depth != 1:
            return
        if tag == "tr":
            self._finish_cell()
            table.row_index += 1
            table.next_column = 1
            return
        if table.cell_column is not None and (
            tag == "br" or tag in _BLOCK_TAGS or _heading_level(tag) is not None
        ):
            table.cell_parts.append(" ")
            return
        if tag not in {"td", "th"} or table.row_index < 1:
            return
        self._finish_cell()
        while table.occupied_through_row.get(table.next_column, 0) >= table.row_index:
            table.next_column += 1
        table.cell_column = table.next_column
        attributes = {key.casefold(): value for key, value in attrs}
        table.cell_row_span = _bounded_span(attributes.get("rowspan"))
        table.cell_column_span = _bounded_span(attributes.get("colspan"))
        last_row = table.row_index + table.cell_row_span - 1
        for column in range(table.next_column, table.next_column + table.cell_column_span):
            table.occupied_through_row[column] = max(
                table.occupied_through_row.get(column, 0), last_row
            )

    def _handle_table_end(self, tag: str) -> None:
        if self._table_depth == 1 and tag in {"td", "th"}:
            self._finish_cell()
        elif (
            self._table_depth == 1
            and self._table is not None
            and self._table.cell_column is not None
            and (tag in _BLOCK_TAGS or _heading_level(tag) is not None)
        ):
            self._table.cell_parts.append(" ")

    def _finish_cell(self) -> None:
        table = self._table
        if table is None or table.cell_column is None:
            return
        text = _normalize_cell_text("".join(table.cell_parts))
        if text:
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            cell = SecFilingTableCell(
                table_index=table.table_index,
                row_index=table.row_index,
                column_index=table.cell_column,
                row_span=table.cell_row_span,
                column_span=table.cell_column_span,
                text=text,
                content_sha256=digest,
            )
            self._cells.append(cell)
            if len(self._cells) > _MAX_CELLS:
                raise ValueError("SEC filing HTML contains too many table cells")
            # One marker per paragraph keeps the generic bounded chunker from
            # splitting a coordinate envelope away from its cell text.
            self._parts.append(_cell_marker(cell) + "\n\n")
        table.next_column = table.cell_column + table.cell_column_span
        table.cell_column = None
        table.cell_row_span = 1
        table.cell_column_span = 1
        table.cell_parts.clear()


def extract_filing_html(value: str) -> FilingHtmlExtraction:
    parser = FilingHtmlExtractor()
    parser.feed(value)
    parser.close()
    return parser.extraction()


def table_cells_from_markdown(value: str) -> tuple[SecFilingTableCell, ...]:
    cells: list[SecFilingTableCell] = []
    for line in value.splitlines():
        match = _CELL_MARKER.fullmatch(line.strip())
        if match is None:
            continue
        text = match.group("text")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest != match.group("sha256"):
            raise ValueError("SEC table cell marker hash does not match")
        cells.append(
            SecFilingTableCell(
                table_index=int(match.group("table")),
                row_index=int(match.group("row")),
                column_index=int(match.group("column")),
                row_span=int(match.group("rowspan")),
                column_span=int(match.group("colspan")),
                text=text,
                content_sha256=digest,
            )
        )
    return tuple(cells)


def _cell_marker(cell: SecFilingTableCell) -> str:
    return (
        f"[SEC_TABLE_CELL_V1 table={cell.table_index} row={cell.row_index} "
        f"column={cell.column_index} rowspan={cell.row_span} colspan={cell.column_span} "
        f"sha256={cell.content_sha256}] {cell.text}"
    )


def _bounded_span(value: str | None) -> int:
    if value is None:
        return 1
    try:
        parsed = int(value)
    except ValueError:
        return 1
    return parsed if 1 <= parsed <= 1_000 else 1


def _heading_level(tag: str) -> int | None:
    if len(tag) == 2 and tag[0] == "h" and tag[1] in "123456":
        return int(tag[1])
    return None


def _normalize_cell_text(value: str) -> str:
    return _SPACE_PATTERN.sub(" ", value).strip()[:_MAX_CELL_CHARACTERS]


def _normalize_document_text(value: str) -> str:
    lines = [line.rstrip() for line in value.replace("\r", "").split("\n")]
    normalized: list[str] = []
    blank = False
    for line in lines:
        text = line.strip()
        if text:
            normalized.append(text)
            blank = False
        elif normalized and not blank:
            normalized.append("")
            blank = True
    return "\n".join(normalized).strip()
