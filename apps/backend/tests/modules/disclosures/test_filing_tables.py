"""SEC filing HTML table structure contracts."""

import pytest

from industry_platform.modules.disclosures.filing_tables import (
    extract_filing_html,
    table_cells_from_markdown,
)
from industry_platform.modules.ingestion.chunker import _page_chunks
from industry_platform.modules.knowledge.parser_contract import MAX_CHUNK_CHARACTERS


def test_extracts_stable_table_coordinates_with_row_and_column_spans() -> None:
    extraction = extract_filing_html(
        """
        <html><body><h1>Financial data</h1>
        <table>
          <tr><th rowspan="2">Metric</th><th colspan="2">Fiscal year</th></tr>
          <tr><th>2022</th><th>2023</th></tr>
          <tr><td>Net sales</td><td>394328</td><td><strong>383285</strong></td></tr>
        </table>
        <script>ignore-secret-value</script>
        </body></html>
        """
    )

    coordinates = [
        (cell.table_index, cell.row_index, cell.column_index, cell.row_span, cell.column_span)
        for cell in extraction.table_cells
    ]
    assert coordinates == [
        (1, 1, 1, 2, 1),
        (1, 1, 2, 1, 2),
        (1, 2, 2, 1, 1),
        (1, 2, 3, 1, 1),
        (1, 3, 1, 1, 1),
        (1, 3, 2, 1, 1),
        (1, 3, 3, 1, 1),
    ]
    assert "ignore-secret-value" not in extraction.markdown
    assert table_cells_from_markdown(extraction.markdown) == extraction.table_cells


def test_rejects_a_tampered_table_cell_marker() -> None:
    extraction = extract_filing_html("<table><tr><td>Net sales</td></tr></table>")

    with pytest.raises(ValueError, match="marker hash"):
        table_cells_from_markdown(extraction.markdown.replace("Net sales", "Gross sales"))


def test_numbers_multiple_top_level_tables_without_parsing_nested_layout_tables_twice() -> None:
    extraction = extract_filing_html(
        """
        <table><tr><td>Outer <table><tr><td>nested</td></tr></table></td></tr></table>
        <table><tr><td>Second</td></tr></table>
        """
    )

    assert [(cell.table_index, cell.text) for cell in extraction.table_cells] == [
        (1, "Outer nested"),
        (2, "Second"),
    ]


def test_cell_markers_remain_atomic_through_the_production_chunker() -> None:
    extraction = extract_filing_html(
        "<table><tr><td>Revenue<br>from products</td><td>"
        + ("long value " * 500)
        + "</td></tr></table>"
    )

    chunks = _page_chunks(extraction.markdown)
    reparsed = tuple(cell for chunk in chunks for cell in table_cells_from_markdown(chunk))

    assert all(len(chunk) <= MAX_CHUNK_CHARACTERS for chunk in chunks)
    assert reparsed == extraction.table_cells
    assert extraction.table_cells[0].text == "Revenue from products"
