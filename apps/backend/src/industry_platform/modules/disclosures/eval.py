"""Deterministic Day 6 SEC source contract and closeout scorer."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final, Self, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from industry_platform.modules.disclosures.domain import SEC_VISIBILITY_POLICY_VERSION
from industry_platform.modules.disclosures.profile import (
    SEC_SOURCE_HARNESS_VERSION,
    SEC_SOURCE_MODEL_FIXTURE_VERSION,
    SEC_SOURCE_PROFILE_VERSION,
    SEC_SOURCE_PROMPT_VERSION,
    SEC_SOURCE_TOOL_REFERENCES,
    SEC_SOURCE_TOOLSET_VERSION,
)

SEC_SOURCE_SCORER_VERSION: Final = "sec-source-scorer-v1"
SEC_SOURCE_DATASET_ID: Final = "sec-source-v1"
SEC_SOURCE_DATASET_VERSION: Final = "v1"

_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_EVIDENCE_REF_PATTERN = re.compile(r"^apps/backend/tests/[A-Za-z0-9_./-]+\.py::test_[A-Za-z0-9_]+$")
_SEC_TOOL_KEYS: Final = tuple(
    f"{reference.name}@{reference.version}" for reference in SEC_SOURCE_TOOL_REFERENCES
)
_KNOWN_METRICS: Final = frozenset(
    {
        "source_locator_resolvability",
        "snapshot_presence_accuracy",
        "import_presence_accuracy",
        "tool_surface_adherence",
        "bulk_coverage_readiness",
        "future_leakage",
        "scope_violation",
        "workspace_leakage",
        "duplicate_commit",
        "dependency_as_no_result",
    }
)


class SecSourceSplit(StrEnum):
    CONTRACT = "contract"
    CLOSEOUT_REGRESSION = "closeout_regression"


class SecSourceExecutionKind(StrEnum):
    TOOL = "tool"
    SYNC = "sync"


class SecSourceSyncKind(StrEnum):
    CANONICAL_SOURCE = "canonical_source"
    WORKSPACE_IMPORT = "workspace_import"


class SecSourceOutcome(StrEnum):
    SUCCESS = "success"
    AMBIGUOUS = "ambiguous"
    NO_RESULT = "no_result"
    DEPENDENCY_ERROR = "dependency_error"
    PERMISSION_DENIED = "permission_denied"
    PARTIAL = "partial"
    CAPABILITY_MISSING = "capability_missing"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SecSourceProfileManifest(_FrozenModel):
    profile_name: str
    profile_version: str
    prompt_version: str
    harness_version: str
    model_fixture_version: str
    toolset_version: str
    available_tools: tuple[str, ...]


class SecSourceFixtureManifest(_FrozenModel):
    fixture_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]+$")
    fixture_version: str = Field(min_length=1, max_length=64)
    content_sha256: str
    source_kind: str = Field(min_length=1, max_length=64)

    @field_validator("content_sha256")
    @classmethod
    def _validate_sha256(cls, value: str) -> str:
        if _SHA256_PATTERN.fullmatch(value) is None:
            raise ValueError("SEC source fixture checksum is invalid")
        return value


class SecSourceBulkCoverage(_FrozenModel):
    required: bool
    bulk_published_at: datetime | None = None
    coverage_through: datetime | None = None
    incremental_source_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_coverage(self) -> Self:
        if self.required and (self.bulk_published_at is None or self.coverage_through is None):
            raise ValueError("Required SEC bulk coverage must freeze both watermarks")
        if not self.required and (
            self.bulk_published_at is not None
            or self.coverage_through is not None
            or self.incremental_source_refs
        ):
            raise ValueError("Non-bulk SEC case cannot claim bulk coverage")
        if len(set(self.incremental_source_refs)) != len(self.incremental_source_refs):
            raise ValueError("SEC incremental source references must be unique")
        for value in (self.bulk_published_at, self.coverage_through):
            if value is not None and value.utcoffset() is None:
                raise ValueError("SEC bulk coverage watermark must be timezone-aware")
        if (
            self.bulk_published_at is not None
            and self.coverage_through is not None
            and self.coverage_through > self.bulk_published_at
        ):
            raise ValueError("SEC bulk coverage cannot extend beyond its publication time")
        return self


class SecSourceEvalCase(_FrozenModel):
    case_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]+$")
    case_version: str = Field(min_length=1, max_length=64)
    split: SecSourceSplit
    execution_kind: SecSourceExecutionKind
    sync_kind: SecSourceSyncKind | None
    source_fixture_ref: str
    cik: str = Field(pattern=r"^[0-9]{10}$")
    accession: str | None = Field(default=None, pattern=r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
    form: str | None = None
    report_period: date | None = None
    as_of: datetime
    visibility_policy_version: str
    allowed_tools: tuple[str, ...]
    forbidden_tools: tuple[str, ...]
    expected_tools: tuple[str, ...]
    expected_milestones: tuple[str, ...]
    expected_outcome: SecSourceOutcome
    expected_error_code: str | None
    expected_snapshot_presence: bool
    expected_import_presence: bool
    expected_source_locator: bool
    bulk_coverage: SecSourceBulkCoverage
    eligible_metrics: tuple[str, ...]
    evidence_ref: str

    @model_validator(mode="after")
    def _validate_case(self) -> Self:
        if self.as_of.utcoffset() is None:
            raise ValueError("SEC source case as_of must be timezone-aware")
        if self.visibility_policy_version != SEC_VISIBILITY_POLICY_VERSION:
            raise ValueError("SEC source case visibility policy is invalid")
        if self.form is not None and self.form not in {"10-K", "10-K/A", "10-Q", "10-Q/A"}:
            raise ValueError("SEC source case form is outside the frozen scope")
        if (self.accession is None) != (self.form is None) or (self.accession is None) != (
            self.report_period is None
        ):
            raise ValueError("SEC source case filing identity is incomplete")
        if self.execution_kind is SecSourceExecutionKind.TOOL:
            if self.sync_kind is not None or self.allowed_tools != _SEC_TOOL_KEYS:
                raise ValueError("SEC Tool case must use the exact five-Tool profile")
            if not self.expected_tools or any(
                tool not in self.allowed_tools for tool in self.expected_tools
            ):
                raise ValueError("SEC Tool trajectory is outside the frozen surface")
        elif self.sync_kind is None or self.allowed_tools or self.expected_tools:
            raise ValueError("SEC sync case must declare sync ownership without Tools")
        if set(self.allowed_tools) & set(self.forbidden_tools):
            raise ValueError("SEC allowed and forbidden Tool surfaces overlap")
        if len(set(self.forbidden_tools)) != len(self.forbidden_tools):
            raise ValueError("SEC forbidden Tool surface contains duplicates")
        if not self.expected_milestones or len(set(self.expected_milestones)) != len(
            self.expected_milestones
        ):
            raise ValueError("SEC expected milestones are invalid")
        if self.expected_outcome is SecSourceOutcome.SUCCESS:
            if self.expected_error_code is not None:
                raise ValueError("Successful SEC case cannot expect an error")
        elif not self.expected_error_code:
            raise ValueError("Non-success SEC case requires a stable error code")
        if self.bulk_coverage.required and self.execution_kind is not SecSourceExecutionKind.SYNC:
            raise ValueError("SEC bulk coverage belongs to a sync case")
        if self.sync_kind is SecSourceSyncKind.CANONICAL_SOURCE and self.expected_import_presence:
            raise ValueError("Canonical SEC sync cannot claim a Workspace import")
        if not self.eligible_metrics or set(self.eligible_metrics) - _KNOWN_METRICS:
            raise ValueError("SEC case metric eligibility is invalid")
        if len(set(self.eligible_metrics)) != len(self.eligible_metrics):
            raise ValueError("SEC case metric eligibility contains duplicates")
        if _EVIDENCE_REF_PATTERN.fullmatch(self.evidence_ref) is None:
            raise ValueError("SEC executable evidence reference is invalid")
        return self


class SecSourceDataset(_FrozenModel):
    schema_version: int
    dataset_id: str
    dataset_version: str
    scorer_version: str
    profile: SecSourceProfileManifest
    fixtures: tuple[SecSourceFixtureManifest, ...]
    cases: tuple[SecSourceEvalCase, ...]

    @model_validator(mode="after")
    def _validate_dataset(self) -> Self:
        if (
            self.schema_version != 1
            or self.dataset_id != SEC_SOURCE_DATASET_ID
            or self.dataset_version != SEC_SOURCE_DATASET_VERSION
            or self.scorer_version != SEC_SOURCE_SCORER_VERSION
        ):
            raise ValueError("SEC source dataset identity is invalid")
        expected_profile = SecSourceProfileManifest(
            profile_name="tool-l2",
            profile_version=SEC_SOURCE_PROFILE_VERSION,
            prompt_version=SEC_SOURCE_PROMPT_VERSION,
            harness_version=SEC_SOURCE_HARNESS_VERSION,
            model_fixture_version=SEC_SOURCE_MODEL_FIXTURE_VERSION,
            toolset_version=SEC_SOURCE_TOOLSET_VERSION,
            available_tools=_SEC_TOOL_KEYS,
        )
        if self.profile != expected_profile:
            raise ValueError("SEC source dataset profile is not the frozen Harness profile")
        if len(self.cases) != 24:
            raise ValueError("SEC source dataset must contain exactly 24 cases")
        split_counts = {
            split: sum(case.split is split for case in self.cases) for split in SecSourceSplit
        }
        if split_counts != {
            SecSourceSplit.CONTRACT: 18,
            SecSourceSplit.CLOSEOUT_REGRESSION: 6,
        }:
            raise ValueError("SEC source dataset split must remain 18 contract plus 6 closeout")
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("SEC source dataset case IDs must be unique")
        fixture_ids = {fixture.fixture_id for fixture in self.fixtures}
        if len(fixture_ids) != len(self.fixtures) or any(
            case.source_fixture_ref not in fixture_ids for case in self.cases
        ):
            raise ValueError("SEC source dataset fixture references are invalid")
        if len({case.evidence_ref for case in self.cases}) != len(self.cases):
            raise ValueError("SEC source executable evidence references must be unique")
        for metric in _KNOWN_METRICS:
            if not any(metric in case.eligible_metrics for case in self.cases):
                raise ValueError(f"SEC source metric {metric} has no eligible denominator")
        return self


class SecSourceCaseObservation(_FrozenModel):
    case_id: str
    observed_outcome: SecSourceOutcome
    observed_error_code: str | None
    observed_snapshot_presence: bool
    observed_import_presence: bool
    observed_source_locator: bool
    observed_tools: tuple[str, ...]
    observed_milestones: tuple[str, ...]
    observed_bulk_coverage_complete: bool
    future_leakage_count: int = Field(ge=0)
    scope_violation_count: int = Field(ge=0)
    workspace_leakage_count: int = Field(ge=0)
    duplicate_commit_count: int = Field(ge=0)
    dependency_as_no_result_count: int = Field(ge=0)
    evidence_ref: str


class SecSourceMetric(_FrozenModel):
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=1)
    value: float = Field(ge=0)

    @model_validator(mode="after")
    def _validate_ratio(self) -> Self:
        if self.numerator > self.denominator:
            raise ValueError("SEC source metric numerator exceeds its denominator")
        if self.value != round(self.numerator / self.denominator, 6):
            raise ValueError("SEC source metric value is inconsistent")
        return self


class SecSourceScore(_FrozenModel):
    dataset_id: str
    dataset_version: str
    scorer_version: str
    case_count: int
    contract_case_count: int
    closeout_case_count: int
    metrics: Mapping[str, SecSourceMetric]
    gate_passed: bool
    blockers: tuple[str, ...]


class SecSourceCheckedReport(_FrozenModel):
    schema_version: int
    report_version: str
    dataset_id: str
    dataset_version: str
    scorer_version: str
    execution_boundary: Mapping[str, object]
    observations: tuple[SecSourceCaseObservation, ...]
    metrics: Mapping[str, SecSourceMetric]
    gate_passed: bool
    blockers: tuple[str, ...]


def load_sec_source_dataset(path: Path) -> SecSourceDataset:
    return SecSourceDataset.model_validate(_load_json(path))


def load_sec_source_report(path: Path) -> SecSourceCheckedReport:
    return SecSourceCheckedReport.model_validate(_load_json(path))


def score_sec_source_dataset(
    dataset: SecSourceDataset,
    observations: Iterable[SecSourceCaseObservation],
) -> SecSourceScore:
    observed_by_case = {observation.case_id: observation for observation in observations}
    if len(observed_by_case) != len(dataset.cases) or set(observed_by_case) != {
        case.case_id for case in dataset.cases
    }:
        raise ValueError("SEC source observations do not cover the dataset exactly")

    case_results = {
        case.case_id: _case_passed(case, observed_by_case[case.case_id]) for case in dataset.cases
    }
    metrics = {
        "case_pass_rate": _accuracy_metric(dataset.cases, case_results),
        "contract_pass_rate": _accuracy_metric(
            tuple(case for case in dataset.cases if case.split is SecSourceSplit.CONTRACT),
            case_results,
        ),
        "closeout_pass_rate": _accuracy_metric(
            tuple(
                case for case in dataset.cases if case.split is SecSourceSplit.CLOSEOUT_REGRESSION
            ),
            case_results,
        ),
        "source_locator_resolvability": _boolean_metric(
            dataset.cases,
            observed_by_case,
            "source_locator_resolvability",
            lambda case, observation: (
                observation.observed_source_locator == case.expected_source_locator
            ),
        ),
        "snapshot_presence_accuracy": _boolean_metric(
            dataset.cases,
            observed_by_case,
            "snapshot_presence_accuracy",
            lambda case, observation: (
                observation.observed_snapshot_presence == case.expected_snapshot_presence
            ),
        ),
        "import_presence_accuracy": _boolean_metric(
            dataset.cases,
            observed_by_case,
            "import_presence_accuracy",
            lambda case, observation: (
                observation.observed_import_presence == case.expected_import_presence
            ),
        ),
        "tool_surface_adherence": _boolean_metric(
            dataset.cases,
            observed_by_case,
            "tool_surface_adherence",
            lambda case, observation: (
                observation.observed_tools == case.expected_tools
                and all(tool in case.allowed_tools for tool in observation.observed_tools)
                and not set(observation.observed_tools) & set(case.forbidden_tools)
            ),
        ),
        "bulk_coverage_readiness": _boolean_metric(
            dataset.cases,
            observed_by_case,
            "bulk_coverage_readiness",
            lambda _case, observation: observation.observed_bulk_coverage_complete,
        ),
        "future_leakage": _zero_error_metric(
            dataset.cases,
            observed_by_case,
            "future_leakage",
            "future_leakage_count",
        ),
        "scope_violation": _zero_error_metric(
            dataset.cases,
            observed_by_case,
            "scope_violation",
            "scope_violation_count",
        ),
        "workspace_leakage": _zero_error_metric(
            dataset.cases,
            observed_by_case,
            "workspace_leakage",
            "workspace_leakage_count",
        ),
        "duplicate_commit": _zero_error_metric(
            dataset.cases,
            observed_by_case,
            "duplicate_commit",
            "duplicate_commit_count",
        ),
        "dependency_as_no_result": _zero_error_metric(
            dataset.cases,
            observed_by_case,
            "dependency_as_no_result",
            "dependency_as_no_result_count",
        ),
    }
    rate_metrics = {
        "case_pass_rate",
        "contract_pass_rate",
        "closeout_pass_rate",
        "source_locator_resolvability",
        "snapshot_presence_accuracy",
        "import_presence_accuracy",
        "tool_surface_adherence",
        "bulk_coverage_readiness",
    }
    error_metrics = {
        "future_leakage",
        "scope_violation",
        "workspace_leakage",
        "duplicate_commit",
        "dependency_as_no_result",
    }
    gate_passed = all(metrics[name].value == 1 for name in rate_metrics) and all(
        metrics[name].value == 0 for name in error_metrics
    )
    blockers = tuple(case.case_id for case in dataset.cases if not case_results[case.case_id])
    return SecSourceScore(
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        scorer_version=dataset.scorer_version,
        case_count=len(dataset.cases),
        contract_case_count=sum(case.split is SecSourceSplit.CONTRACT for case in dataset.cases),
        closeout_case_count=sum(
            case.split is SecSourceSplit.CLOSEOUT_REGRESSION for case in dataset.cases
        ),
        metrics=metrics,
        gate_passed=gate_passed,
        blockers=blockers,
    )


def _case_passed(case: SecSourceEvalCase, observation: SecSourceCaseObservation) -> bool:
    return (
        observation.evidence_ref == case.evidence_ref
        and observation.observed_outcome is case.expected_outcome
        and observation.observed_error_code == case.expected_error_code
        and observation.observed_snapshot_presence == case.expected_snapshot_presence
        and observation.observed_import_presence == case.expected_import_presence
        and observation.observed_source_locator == case.expected_source_locator
        and observation.observed_tools == case.expected_tools
        and observation.observed_milestones == case.expected_milestones
        and observation.observed_bulk_coverage_complete == case.bulk_coverage.required
        and observation.future_leakage_count == 0
        and observation.scope_violation_count == 0
        and observation.workspace_leakage_count == 0
        and observation.duplicate_commit_count == 0
        and observation.dependency_as_no_result_count == 0
    )


def _accuracy_metric(
    cases: tuple[SecSourceEvalCase, ...],
    results: Mapping[str, bool],
) -> SecSourceMetric:
    return _metric(sum(results[case.case_id] for case in cases), len(cases))


def _boolean_metric(
    cases: tuple[SecSourceEvalCase, ...],
    observations: Mapping[str, SecSourceCaseObservation],
    metric_name: str,
    predicate: Callable[[SecSourceEvalCase, SecSourceCaseObservation], bool],
) -> SecSourceMetric:
    eligible = tuple(case for case in cases if metric_name in case.eligible_metrics)
    return _metric(
        sum(predicate(case, observations[case.case_id]) for case in eligible),
        len(eligible),
    )


def _zero_error_metric(
    cases: tuple[SecSourceEvalCase, ...],
    observations: Mapping[str, SecSourceCaseObservation],
    metric_name: str,
    field_name: str,
) -> SecSourceMetric:
    eligible = tuple(case for case in cases if metric_name in case.eligible_metrics)
    return _metric(
        sum(getattr(observations[case.case_id], field_name) for case in eligible),
        len(eligible),
    )


def _metric(numerator: int, denominator: int) -> SecSourceMetric:
    if denominator < 1:
        raise ValueError("SEC source metric has no eligible denominator")
    return SecSourceMetric(
        numerator=numerator,
        denominator=denominator,
        value=round(numerator / denominator, 6),
    )


def _load_json(path: Path) -> Mapping[str, object]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("SEC source dataset JSON is unreadable") from exc
    if not isinstance(value, dict):
        raise ValueError("SEC source dataset JSON root must be an object")
    return cast(Mapping[str, object], value)


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("SEC source dataset JSON contains duplicate keys")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> object:
    raise ValueError(f"SEC source dataset JSON contains non-finite number {value}")
