"""Security boundary tests for PostgreSQL plan and configuration handling."""

import pytest

from industry_platform.modules.data_explorer.adapters.postgresql import _plan_metrics
from industry_platform.modules.data_explorer.domain import DataExplorerError


def test_plan_budget_counts_nested_scan_rows_not_only_root_output() -> None:
    cost, rows = _plan_metrics(
        [
            {
                "Plan": {
                    "Node Type": "Aggregate",
                    "Total Cost": 123.45,
                    "Plan Rows": 4,
                    "Plans": [
                        {
                            "Node Type": "Seq Scan",
                            "Total Cost": 100.0,
                            "Plan Rows": 250_000,
                        }
                    ],
                }
            }
        ]
    )

    assert cost == 123.45
    assert rows == 250_004


@pytest.mark.parametrize(
    "document",
    [
        [{"Plan": {"Total Cost": 1, "Plan Rows": -1}}],
        [{"Plan": {"Total Cost": 1, "Plan Rows": 1, "Plans": ["not-a-plan"]}}],
        [{"Plan": {"Total Cost": float("nan"), "Plan Rows": 1}}],
    ],
)
def test_malformed_or_nonfinite_plan_is_rejected(document: object) -> None:
    with pytest.raises(DataExplorerError) as captured:
        _plan_metrics(document)
    assert captured.value.code == "query_plan_invalid"
