"""Construct only bounded table data and allowlisted ECharts options."""

from __future__ import annotations

import hashlib
from datetime import datetime
from types import MappingProxyType
from uuid import UUID

from industry_platform.modules.data_explorer.domain import (
    ChartArtifact,
    ChartRequest,
    ChartType,
    QueryResultArtifact,
    canonical_json_bytes,
)


class ArtifactValidationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("Query Artifact was rejected")
        self.code = code


def create_table_artifact(
    *,
    artifact_id: UUID,
    query_run_id: UUID,
    workspace_id: UUID,
    columns: tuple[str, ...],
    rows: tuple[tuple[object, ...], ...],
    truncated: bool,
    created_at: datetime,
) -> QueryResultArtifact:
    document = {
        "columns": list(columns),
        "rows": [list(row) for row in rows],
        "truncated": truncated,
    }
    digest = hashlib.sha256(canonical_json_bytes(document)).hexdigest()
    try:
        return QueryResultArtifact(
            artifact_id=artifact_id,
            query_run_id=query_run_id,
            workspace_id=workspace_id,
            columns=columns,
            rows=rows,
            truncated=truncated,
            content_sha256=digest,
            created_at=created_at,
        )
    except ValueError:
        raise ArtifactValidationError("table_artifact_invalid") from None


def create_chart_artifact(
    *,
    artifact_id: UUID,
    table: QueryResultArtifact,
    request: ChartRequest,
    created_at: datetime,
) -> ChartArtifact | None:
    if request.chart_type is ChartType.TABLE:
        return None
    available = set(table.columns)
    required = {request.x_column, request.y_column}
    if request.series_column is not None:
        required.add(request.series_column)
    if None in required or not required.issubset(available):
        raise ArtifactValidationError("chart_column_not_found")
    x_column = request.x_column
    y_column = request.y_column
    if x_column is None or y_column is None:
        raise ArtifactValidationError("chart_encoding_invalid")
    x_index = table.columns.index(x_column)
    y_index = table.columns.index(y_column)
    if any(
        isinstance(row[y_index], bool) or not isinstance(row[y_index], int | float)
        for row in table.rows
    ):
        raise ArtifactValidationError("chart_data_type_invalid")
    if request.chart_type is ChartType.SCATTER and any(
        isinstance(row[x_index], bool) or not isinstance(row[x_index], int | float)
        for row in table.rows
    ):
        raise ArtifactValidationError("chart_data_type_invalid")
    if request.chart_type is ChartType.PIE:
        data = [{"name": row[x_index], "value": row[y_index]} for row in table.rows]
        option: dict[str, object] = {
            "dataset": {"source": [list(table.columns), *[list(row) for row in table.rows]]},
            "series": [{"type": "pie", "data": data}],
            "tooltip": {"trigger": "item"},
        }
    else:
        if request.series_column is None:
            option = {
                "dataset": {"source": [list(table.columns), *[list(row) for row in table.rows]]},
                "series": [
                    {
                        "type": request.chart_type.value,
                        "encode": {"x": x_column, "y": y_column},
                    }
                ],
                "tooltip": {"trigger": "axis"},
                "xAxis": {
                    "type": "value" if request.chart_type is ChartType.SCATTER else "category"
                },
                "yAxis": {"type": "value"},
            }
        else:
            series_index = table.columns.index(request.series_column)
            grouped: dict[str, list[list[object]]] = {}
            for row in table.rows:
                grouped.setdefault(str(row[series_index]), []).append([row[x_index], row[y_index]])
            option = {
                "series": [
                    {"name": name, "type": request.chart_type.value, "data": points}
                    for name, points in grouped.items()
                ],
                "tooltip": {"trigger": "axis"},
                "xAxis": {
                    "type": "value" if request.chart_type is ChartType.SCATTER else "category"
                },
                "yAxis": {"type": "value"},
            }
    if request.title is not None:
        option["title"] = {"text": request.title}
    digest = hashlib.sha256(canonical_json_bytes(option)).hexdigest()
    try:
        return ChartArtifact(
            artifact_id=artifact_id,
            query_run_id=table.query_run_id,
            workspace_id=table.workspace_id,
            chart_type=request.chart_type,
            option=MappingProxyType(option),
            content_sha256=digest,
            created_at=created_at,
        )
    except ValueError:
        raise ArtifactValidationError("chart_artifact_invalid") from None
