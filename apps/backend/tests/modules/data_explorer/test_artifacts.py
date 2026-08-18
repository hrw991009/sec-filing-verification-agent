"""Typed table and chart Artifact tests."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from industry_platform.modules.data_explorer.artifacts import (
    ArtifactValidationError,
    create_chart_artifact,
    create_table_artifact,
)
from industry_platform.modules.data_explorer.domain import (
    ChartRequest,
    ChartType,
    QueryResultArtifact,
)

NOW = datetime(2026, 8, 17, 8, 0, tzinfo=UTC)


def _table() -> QueryResultArtifact:
    return create_table_artifact(
        artifact_id=uuid4(),
        query_run_id=uuid4(),
        workspace_id=uuid4(),
        columns=("company_name", "revenue"),
        rows=(("A", 10), ("B", 20)),
        truncated=False,
        created_at=NOW,
    )


@pytest.mark.parametrize("chart_type", [ChartType.LINE, ChartType.BAR])
def test_cartesian_chart_is_built_from_a_fixed_safe_shape(chart_type: ChartType) -> None:
    artifact = create_chart_artifact(
        artifact_id=uuid4(),
        table=_table(),
        request=ChartRequest(
            chart_type=chart_type,
            x_column="company_name",
            y_column="revenue",
            title="Revenue",
        ),
        created_at=NOW,
    )

    assert artifact is not None
    assert set(artifact.option) == {"dataset", "series", "title", "tooltip", "xAxis", "yAxis"}
    assert artifact.option["series"] == [
        {"type": chart_type.value, "encode": {"x": "company_name", "y": "revenue"}}
    ]
    assert "function" not in repr(artifact.option).lower()
    assert "http" not in repr(artifact.option).lower()


def test_scatter_requires_numeric_axes() -> None:
    with pytest.raises(ArtifactValidationError) as captured:
        create_chart_artifact(
            artifact_id=uuid4(),
            table=_table(),
            request=ChartRequest(
                chart_type=ChartType.SCATTER,
                x_column="company_name",
                y_column="revenue",
            ),
            created_at=NOW,
        )
    assert captured.value.code == "chart_data_type_invalid"


def test_series_column_produces_only_server_built_grouped_series() -> None:
    table = create_table_artifact(
        artifact_id=uuid4(),
        query_run_id=uuid4(),
        workspace_id=uuid4(),
        columns=("quarter", "revenue", "industry"),
        rows=(("Q1", 10, "energy"), ("Q1", 20, "health"), ("Q2", 30, "energy")),
        truncated=False,
        created_at=NOW,
    )
    artifact = create_chart_artifact(
        artifact_id=uuid4(),
        table=table,
        request=ChartRequest(
            chart_type=ChartType.BAR,
            x_column="quarter",
            y_column="revenue",
            series_column="industry",
        ),
        created_at=NOW,
    )
    assert artifact is not None
    assert artifact.option["series"] == [
        {"name": "energy", "type": "bar", "data": [["Q1", 10], ["Q2", 30]]},
        {"name": "health", "type": "bar", "data": [["Q1", 20]]},
    ]


def test_pie_chart_has_only_bounded_name_value_data() -> None:
    artifact = create_chart_artifact(
        artifact_id=uuid4(),
        table=_table(),
        request=ChartRequest(
            chart_type=ChartType.PIE,
            x_column="company_name",
            y_column="revenue",
        ),
        created_at=NOW,
    )
    assert artifact is not None
    assert artifact.option["series"] == [
        {"type": "pie", "data": [{"name": "A", "value": 10}, {"name": "B", "value": 20}]}
    ]


def test_chart_rejects_unknown_result_column() -> None:
    with pytest.raises(ArtifactValidationError) as captured:
        create_chart_artifact(
            artifact_id=uuid4(),
            table=_table(),
            request=ChartRequest(
                chart_type=ChartType.BAR,
                x_column="company_name",
                y_column="secret",
            ),
            created_at=NOW,
        )
    assert captured.value.code == "chart_column_not_found"


def test_table_mode_never_creates_an_echarts_option() -> None:
    assert (
        create_chart_artifact(
            artifact_id=uuid4(),
            table=_table(),
            request=ChartRequest(chart_type=ChartType.TABLE),
            created_at=NOW,
        )
        is None
    )
