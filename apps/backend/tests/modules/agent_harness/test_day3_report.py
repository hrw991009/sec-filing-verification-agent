"""Validate that the Day 3 trajectory report stays bound to executable evidence."""

import ast
import json
from pathlib import Path
from typing import Any, cast

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
REPORT_PATH = REPOSITORY_ROOT / "evals" / "reports" / "day3-v1.json"
DATASET_PATHS = (
    REPOSITORY_ROOT / "evals" / "scenarios" / "day3-l1-v1.json",
    REPOSITORY_ROOT / "evals" / "scenarios" / "day3-l2-v1.json",
)


def _load_json(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def _assert_test_ref_exists(test_ref: str) -> None:
    relative_path, function_name = test_ref.split("::", maxsplit=1)
    test_path = (REPOSITORY_ROOT / relative_path).resolve()
    tests_root = (REPOSITORY_ROOT / "apps" / "backend" / "tests").resolve()
    assert test_path.is_relative_to(tests_root)
    assert test_path.is_file()
    syntax_tree = ast.parse(test_path.read_text(encoding="utf-8"))
    function_names = {
        node.name
        for node in ast.walk(syntax_tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    assert function_name in function_names


def test_day3_report_inventory_and_executable_bindings_are_current() -> None:
    report = _load_json(REPORT_PATH)
    datasets = [_load_json(path) for path in DATASET_PATHS]
    day3_case_count = sum(len(dataset["cases"]) for dataset in datasets)

    assert report["schema_version"] == 1
    assert report["scenario_inventory"] == {
        "day2_l0_case_count": 9,
        "day3_l1_case_count": 5,
        "day3_l2_case_count": 10,
        "cumulative_case_count": 24,
    }
    assert report["aggregate"] == {
        "day3_case_count": day3_case_count,
        "passed_case_count": day3_case_count,
        "expected_stop_reason_match_rate": 1.0,
        "trajectory_contract_match_rate": 1.0,
        "distinct_tool_selection_proved": True,
        "production_web_tool_journey_proved": True,
    }
    assert [(item["id"], item["version"], item["case_count"]) for item in report["datasets"]] == [
        (dataset["dataset_id"], dataset["dataset_version"], len(dataset["cases"]))
        for dataset in datasets
    ]
    for evidence in report["executable_evidence"]:
        _assert_test_ref_exists(evidence["test_ref"])
