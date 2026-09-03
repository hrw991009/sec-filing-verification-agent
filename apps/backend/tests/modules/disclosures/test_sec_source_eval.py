"""Machine-check the 24-case Day 6 SEC source closeout report."""

from pathlib import Path

import pytest

from industry_platform.modules.disclosures.eval import (
    SEC_SOURCE_DATASET_ID,
    SEC_SOURCE_DATASET_VERSION,
    SEC_SOURCE_SCORER_VERSION,
    SecSourceExecutionKind,
    SecSourceSplit,
    load_sec_source_dataset,
    load_sec_source_report,
    score_sec_source_dataset,
)
from industry_platform.modules.disclosures.profile import SEC_SOURCE_TOOL_REFERENCES

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
DATASET_PATH = REPOSITORY_ROOT / "evals" / "scenarios" / "sec-source-v1.json"
REPORT_PATH = REPOSITORY_ROOT / "evals" / "reports" / "sec-source-v1.json"


def test_sec_source_dataset_and_report_are_recomputed_from_24_fixed_cases() -> None:
    dataset = load_sec_source_dataset(DATASET_PATH)
    checked = load_sec_source_report(REPORT_PATH)
    score = score_sec_source_dataset(dataset, checked.observations)

    assert dataset.dataset_id == checked.dataset_id == SEC_SOURCE_DATASET_ID
    assert dataset.dataset_version == checked.dataset_version == SEC_SOURCE_DATASET_VERSION
    assert dataset.scorer_version == checked.scorer_version == SEC_SOURCE_SCORER_VERSION
    assert score.case_count == len(dataset.cases) == 24
    assert score.contract_case_count == 18
    assert score.closeout_case_count == 6
    assert score.metrics == checked.metrics
    assert score.gate_passed is checked.gate_passed is True
    assert score.blockers == checked.blockers == ()
    assert score.metrics["contract_pass_rate"].value == 1
    assert score.metrics["closeout_pass_rate"].value == 1
    assert score.metrics["tool_surface_adherence"].value == 1
    assert score.metrics["bulk_coverage_readiness"].value == 1
    assert checked.execution_boundary["live_sec_executed"] is False


def test_sec_source_tool_cases_use_only_the_frozen_profile_surface() -> None:
    dataset = load_sec_source_dataset(DATASET_PATH)
    expected = tuple(f"{item.name}@{item.version}" for item in SEC_SOURCE_TOOL_REFERENCES)
    tool_cases = tuple(
        case for case in dataset.cases if case.execution_kind is SecSourceExecutionKind.TOOL
    )

    assert tool_cases
    assert all(case.allowed_tools == expected for case in tool_cases)
    assert all(not set(case.allowed_tools) & set(case.forbidden_tools) for case in tool_cases)
    assert all(set(case.expected_tools) <= set(case.allowed_tools) for case in tool_cases)


def test_sec_source_cases_bind_unique_executable_pytest_evidence() -> None:
    dataset = load_sec_source_dataset(DATASET_PATH)

    for case in dataset.cases:
        relative_path, test_name = case.evidence_ref.split("::", maxsplit=1)
        test_path = REPOSITORY_ROOT / relative_path
        assert test_path.is_file(), case.evidence_ref
        source = test_path.read_text(encoding="utf-8")
        assert f"def {test_name}(" in source, case.evidence_ref


def test_submissions_bulk_closeout_is_backed_by_executable_evidence() -> None:
    dataset = load_sec_source_dataset(DATASET_PATH)
    report = load_sec_source_report(REPORT_PATH)
    case = next(case for case in dataset.cases if case.case_id == "submissions-bulk-watermark")
    observed = next(
        item for item in report.observations if item.case_id == "submissions-bulk-watermark"
    )

    assert case.split is SecSourceSplit.CLOSEOUT_REGRESSION
    assert case.bulk_coverage.required is True
    assert observed.observed_outcome.value == "success"
    assert observed.observed_error_code is None
    assert observed.observed_bulk_coverage_complete is True
    assert observed.evidence_ref.endswith(
        "test_submissions_bulk_watermark_persists_snapshot_and_closes_post_watermark_gap"
    )


def test_companyfacts_bulk_closeout_is_backed_by_executable_evidence() -> None:
    dataset = load_sec_source_dataset(DATASET_PATH)
    report = load_sec_source_report(REPORT_PATH)
    case = next(case for case in dataset.cases if case.case_id == "companyfacts-bulk-watermark")
    observed = next(
        item for item in report.observations if item.case_id == "companyfacts-bulk-watermark"
    )

    assert case.split is SecSourceSplit.CLOSEOUT_REGRESSION
    assert case.bulk_coverage.required is True
    assert observed.observed_outcome.value == "success"
    assert observed.observed_error_code is None
    assert observed.observed_bulk_coverage_complete is True
    assert observed.evidence_ref.endswith(
        "test_companyfacts_bulk_watermark_persists_snapshot_and_closes_post_watermark_gap"
    )


def test_sec_source_scorer_rejects_a_missing_case_observation() -> None:
    dataset = load_sec_source_dataset(DATASET_PATH)
    report = load_sec_source_report(REPORT_PATH)

    with pytest.raises(ValueError, match="cover the dataset exactly"):
        score_sec_source_dataset(dataset, report.observations[:-1])
