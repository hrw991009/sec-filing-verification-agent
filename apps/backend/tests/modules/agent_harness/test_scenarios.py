"""Tests for the versioned Scenario/EvalCase v1 contract."""

import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from industry_platform.modules.agent_harness.scenarios import (
    MAX_SCENARIO_DATASET_BYTES,
    EvalCase,
    ScenarioDataset,
    VersionedReference,
    load_scenario_dataset,
    parse_scenario_dataset,
)
from industry_platform.modules.agent_runtime.domain import RunStopReason

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
DATASET_PATH = REPOSITORY_ROOT / "evals" / "scenarios" / "day2-v1.json"
NOW = datetime(2026, 8, 13, 7, 0, tzinfo=UTC)


def dataset() -> ScenarioDataset:
    return load_scenario_dataset(DATASET_PATH)


def case() -> EvalCase:
    return dataset().cases[0]


def test_versioned_dataset_loads_and_materializes_a_relative_budget() -> None:
    loaded = dataset()
    loaded_case = loaded.cases[0]
    budget = loaded_case.scenario.budget.materialize(started_at=NOW)

    assert loaded.dataset_id == "day2-direct-answer"
    assert loaded_case.scenario.available_tools == ()
    assert loaded_case.scenario.toolset_version == "toolset-none-v1"
    assert budget.deadline == NOW + timedelta(seconds=30)
    assert "deadline" not in DATASET_PATH.read_text(encoding="utf-8")


def test_scenario_json_and_notes_are_immutable_and_hidden_from_repr() -> None:
    sensitive_question = "private evaluation question"
    sensitive_notes = "private reviewer note"
    scenario = replace(
        case().scenario,
        input={
            "question": sensitive_question,
            "conversation_summary": "The earlier turn discussed manufacturing.",
        },
    )
    eval_case = replace(
        case(),
        scenario=scenario,
        expected_behavior={
            "final_output": "A direct answer.",
            "criteria": {"style": "concise"},
        },
        human_notes=sensitive_notes,
    )
    criteria = eval_case.expected_behavior["criteria"]
    rebuilt = replace(eval_case, human_notes="A second reviewer note.")

    assert isinstance(criteria, Mapping)
    assert criteria["style"] == "concise"
    assert rebuilt.expected_behavior == eval_case.expected_behavior
    assert sensitive_question not in repr(eval_case)
    assert sensitive_notes not in repr(eval_case)
    with pytest.raises(TypeError):
        criteria["style"] = "changed"  # type: ignore[index]


def test_loader_rejects_unknown_duplicate_and_non_finite_json() -> None:
    document = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    document["unknown"] = True
    with pytest.raises(ValueError, match="unknown fields"):
        parse_scenario_dataset(json.dumps(document))

    duplicate_key = '{"schema_version":1,"schema_version":1}'
    with pytest.raises(ValueError, match="duplicate object keys"):
        parse_scenario_dataset(duplicate_key)

    non_finite = '{"schema_version":NaN}'
    with pytest.raises(ValueError, match="non-finite"):
        parse_scenario_dataset(non_finite)


def test_direct_answer_cannot_carry_tools_or_runtime_context_fields() -> None:
    loaded_scenario = case().scenario
    with pytest.raises(ValueError, match="cannot expose tools"):
        replace(
            loaded_scenario,
            available_tools=(VersionedReference(name="web-search", version="v1"),),
            toolset_version="tools-v1",
        )
    for forbidden_field in ("workspace_id", "authorization", "providerSecret"):
        with pytest.raises(ValueError, match="unsupported fields"):
            replace(
                loaded_scenario,
                input={
                    "question": "Explain this result.",
                    forbidden_field: "must-not-be-trusted",
                },
            )


def test_logical_references_reject_path_traversal_segments() -> None:
    with pytest.raises(ValueError, match="Reference name"):
        VersionedReference(name="fake-model/../../outside", version="v1")
    with pytest.raises(ValueError, match="Reference name"):
        VersionedReference(name="fake-model//fixture", version="v1")


def test_loader_reads_only_the_bounded_prefix_and_rejects_invalid_utf8(
    tmp_path: Path,
) -> None:
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"x" * (MAX_SCENARIO_DATASET_BYTES + 1))
    with pytest.raises(ValueError, match="size limit"):
        load_scenario_dataset(oversized)

    invalid_utf8 = tmp_path / "invalid-utf8.json"
    invalid_utf8.write_bytes(b"{\xff}")
    with pytest.raises(ValueError, match="valid UTF-8"):
        load_scenario_dataset(invalid_utf8)


def test_scenario_rejects_non_string_conversation_summary() -> None:
    with pytest.raises(ValueError, match="conversation summary"):
        replace(
            case().scenario,
            input={
                "question": "Explain this result.",
                "conversation_summary": {"text": "not a string"},
            },
        )


def test_expected_stop_reason_cannot_disagree_with_a_successful_result() -> None:
    loaded_case = case()
    with pytest.raises(ValueError, match="non-final"):
        replace(
            loaded_case,
            expected_stop_reason=RunStopReason.PROVIDER_TIMEOUT,
        )
    with pytest.raises(ValueError, match="expected final output"):
        replace(loaded_case, expected_behavior={})


def test_dataset_rejects_duplicate_cases_but_allows_scenario_reuse() -> None:
    loaded = dataset()
    with pytest.raises(ValueError, match="duplicate EvalCases"):
        replace(loaded, cases=(loaded.cases[0], loaded.cases[0]))

    second_case = replace(loaded.cases[0], case_id="day2-direct-answer-second-eval")

    assert replace(loaded, cases=(loaded.cases[0], second_case)).cases[1] is second_case
