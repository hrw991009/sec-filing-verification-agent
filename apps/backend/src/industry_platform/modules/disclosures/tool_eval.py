"""Deterministic Day 7 SEC Tool ablation scorer and report generator."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final, Self, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from industry_platform.modules.disclosures.profile import (
    SEC_L4_PROFILE_VERSION,
    SEC_L4_PROMPT_VERSION,
    SEC_L4_TOOL_REFERENCES,
    SEC_L4_TOOLSET_VERSION,
)
from industry_platform.modules.disclosures.tool import (
    SEC_READ_FILING_SECTION_TOOL_NAME,
    SEC_READ_FILING_SECTION_TOOL_VERSION,
    SEC_SEARCH_FILING_TOOL_NAME,
    SEC_SEARCH_FILING_TOOL_VERSION,
)

SEC_TOOL_DATASET_ID: Final = "sec-tool-v1"
SEC_TOOL_DATASET_VERSION: Final = "v1"
SEC_TOOL_SCORER_VERSION: Final = "sec-tool-scorer-v1"
SEC_TOOL_REPORT_VERSION: Final = "v1"

_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_ACCESSION_PATTERN = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
_EVIDENCE_REF_PATTERN = re.compile(r"^apps/backend/tests/[A-Za-z0-9_./-]+\.py::test_[A-Za-z0-9_]+$")
_A1_TOOL_SURFACE: Final = (
    f"{SEC_SEARCH_FILING_TOOL_NAME}@{SEC_SEARCH_FILING_TOOL_VERSION}",
    f"{SEC_READ_FILING_SECTION_TOOL_NAME}@{SEC_READ_FILING_SECTION_TOOL_VERSION}",
)
_A2_TOOL_SURFACE: Final = tuple(
    f"{reference.name}@{reference.version}" for reference in SEC_L4_TOOL_REFERENCES
)
_COMPLEX_KINDS: Final = frozenset({"calculation", "cross_section", "amendment"})


class SecToolStrategy(StrEnum):
    A0 = "a0"
    A1 = "a1"
    A2 = "a2"


class SecToolCaseKind(StrEnum):
    SIMPLE_FACT = "simple_fact"
    CALCULATION = "calculation"
    CROSS_SECTION = "cross_section"
    AMENDMENT = "amendment"
    NO_ANSWER = "no_answer"


class SecToolOutcome(StrEnum):
    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SecToolBudget(_FrozenModel):
    max_steps: int = Field(ge=1)
    max_total_tokens: int = Field(ge=1)
    max_cost_micro_usd: int = Field(ge=0)
    max_latency_ms: int = Field(ge=1)


class SecToolStrategyManifest(_FrozenModel):
    strategy: SecToolStrategy
    profile_version: str
    prompt_version: str
    context_version: str
    toolset_version: str
    available_tools: tuple[str, ...]


class SecToolEvalCase(_FrozenModel):
    case_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]+$")
    case_version: str
    kind: SecToolCaseKind
    question: str = Field(min_length=1)
    expected_cik: str = Field(pattern=r"^[0-9]{10}$")
    expected_form: str
    expected_report_period: date
    as_of: datetime
    expected_accessions: tuple[str, ...]
    expected_outcome: SecToolOutcome
    expected_answer_key: str | None
    expected_evidence_keys: tuple[str, ...]
    expected_program: str | None = None
    executable_evidence_refs: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_case(self) -> Self:
        if self.as_of.utcoffset() is None:
            raise ValueError("SEC Tool case as_of must be timezone-aware")
        if self.expected_form not in {"10-K", "10-K/A", "10-Q", "10-Q/A"}:
            raise ValueError("SEC Tool case form is outside the frozen scope")
        if any(_ACCESSION_PATTERN.fullmatch(value) is None for value in self.expected_accessions):
            raise ValueError("SEC Tool case accession identity is invalid")
        if len(set(self.expected_accessions)) != len(self.expected_accessions):
            raise ValueError("SEC Tool case accessions must be unique")
        if self.expected_outcome is SecToolOutcome.ANSWERED:
            if (
                not self.expected_answer_key
                or not self.expected_evidence_keys
                or not self.expected_accessions
            ):
                raise ValueError("Answered SEC Tool case requires answer and Evidence gold")
        elif self.expected_answer_key is not None or self.expected_program is not None:
            raise ValueError("No-answer SEC Tool case cannot define answer or program gold")
        if (self.kind is SecToolCaseKind.CALCULATION) != (self.expected_program is not None):
            raise ValueError("Only calculation cases may define a gold program")
        if self.kind is SecToolCaseKind.AMENDMENT and len(self.expected_accessions) != 2:
            raise ValueError("Amendment cases must lock base and amendment accessions")
        if self.kind is SecToolCaseKind.NO_ANSWER and self.expected_outcome is not (
            SecToolOutcome.INSUFFICIENT_EVIDENCE
        ):
            raise ValueError("No-answer cases must expect insufficient_evidence")
        if not self.executable_evidence_refs or any(
            _EVIDENCE_REF_PATTERN.fullmatch(value) is None
            for value in self.executable_evidence_refs
        ):
            raise ValueError("SEC Tool executable evidence references are invalid")
        return self


class SecToolDataset(_FrozenModel):
    schema_version: int
    dataset_id: str
    dataset_version: str
    scorer_version: str
    data_version: str
    model_fixture_version: str
    shared_budget: SecToolBudget
    strategies: tuple[SecToolStrategyManifest, ...]
    cases: tuple[SecToolEvalCase, ...]

    @model_validator(mode="after")
    def _validate_dataset(self) -> Self:
        if (
            self.schema_version != 1
            or self.dataset_id != SEC_TOOL_DATASET_ID
            or self.dataset_version != SEC_TOOL_DATASET_VERSION
            or self.scorer_version != SEC_TOOL_SCORER_VERSION
        ):
            raise ValueError("SEC Tool dataset identity is invalid")
        expected_strategies = (
            SecToolStrategyManifest(
                strategy=SecToolStrategy.A0,
                profile_version="sec-oracle-v1",
                prompt_version="sec-tool-eval-prompt-v1",
                context_version="oracle-full-context-v1",
                toolset_version="no-tools-v1",
                available_tools=(),
            ),
            SecToolStrategyManifest(
                strategy=SecToolStrategy.A1,
                profile_version="sec-hybrid-rag-v1",
                prompt_version="sec-tool-eval-prompt-v1",
                context_version="financial-context-v1",
                toolset_version="sec-hybrid-rag-toolset-v1",
                available_tools=_A1_TOOL_SURFACE,
            ),
            SecToolStrategyManifest(
                strategy=SecToolStrategy.A2,
                profile_version=SEC_L4_PROFILE_VERSION,
                prompt_version=SEC_L4_PROMPT_VERSION,
                context_version="financial-context-v1",
                toolset_version=SEC_L4_TOOLSET_VERSION,
                available_tools=_A2_TOOL_SURFACE,
            ),
        )
        if self.strategies != expected_strategies:
            raise ValueError("SEC Tool strategies are not the frozen A0/A1/A2 surfaces")
        if len(self.cases) != 10 or len({case.case_id for case in self.cases}) != 10:
            raise ValueError("SEC Tool dataset must contain exactly 10 unique cases")
        kind_counts = {
            kind: sum(case.kind is kind for case in self.cases) for kind in SecToolCaseKind
        }
        if any(count != 2 for count in kind_counts.values()):
            raise ValueError("SEC Tool dataset must contain two cases for every frozen kind")
        return self


class SecToolCaseObservation(_FrozenModel):
    case_id: str
    strategy: SecToolStrategy
    observed_outcome: SecToolOutcome
    answer_key: str | None = None
    evidence_keys: tuple[str, ...] = ()
    program: str | None = None
    selected_cik: str | None = Field(default=None, pattern=r"^[0-9]{10}$")
    selected_report_period: date | None = None
    selected_accessions: tuple[str, ...] = ()
    observed_tools: tuple[str, ...] = ()
    citations_resolvable: bool
    derived_lineage_complete: bool
    steps: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    cost_micro_usd: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    evidence_ref: str

    @field_validator("selected_accessions")
    @classmethod
    def _validate_accessions(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(_ACCESSION_PATTERN.fullmatch(value) is None for value in values):
            raise ValueError("Observed SEC Tool accession identity is invalid")
        return values

    @field_validator("evidence_ref")
    @classmethod
    def _validate_evidence_ref(cls, value: str) -> str:
        if _EVIDENCE_REF_PATTERN.fullmatch(value) is None:
            raise ValueError("SEC Tool observation evidence reference is invalid")
        return value


class SecToolExecutionBoundary(_FrozenModel):
    evidence_layer: str
    deterministic_contract_executed: bool
    real_dependencies_executed: bool
    live_sec_executed: bool
    live_model_executed: bool
    browser_e2e_executed: bool
    paired_bilingual_executed: bool
    branch_ci_passed: bool
    pr_ci_passed: bool
    main_ci_passed: bool
    owner_reviewed: bool
    limitations: tuple[str, ...]


class SecToolObservationSet(_FrozenModel):
    schema_version: int
    dataset_id: str
    dataset_version: str
    scorer_version: str
    execution_boundary: SecToolExecutionBoundary
    observations: tuple[SecToolCaseObservation, ...]

    @model_validator(mode="after")
    def _validate_identity(self) -> Self:
        if (
            self.schema_version != 1
            or self.dataset_id != SEC_TOOL_DATASET_ID
            or self.dataset_version != SEC_TOOL_DATASET_VERSION
            or self.scorer_version != SEC_TOOL_SCORER_VERSION
        ):
            raise ValueError("SEC Tool observation identity is invalid")
        return self


class SecToolMetric(_FrozenModel):
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=1)
    value: float = Field(ge=0)

    @model_validator(mode="after")
    def _validate_ratio(self) -> Self:
        if self.numerator > self.denominator:
            raise ValueError("SEC Tool metric numerator exceeds denominator")
        if self.value != round(self.numerator / self.denominator, 6):
            raise ValueError("SEC Tool metric value is inconsistent")
        return self


class SecToolStrategyScore(_FrozenModel):
    strategy: SecToolStrategy
    metrics: Mapping[str, SecToolMetric]
    total_steps: int
    total_tokens: int
    total_cost_micro_usd: int
    total_latency_ms: int


class SecToolComparison(_FrozenModel):
    a2_complex_gain_over_a1: float
    a2_simple_degradation_from_a1: float
    a2_cost_increase_micro_usd: int
    a2_latency_increase_ms: int


class SecToolScore(_FrozenModel):
    strategy_scores: Mapping[SecToolStrategy, SecToolStrategyScore]
    comparison: SecToolComparison
    deterministic_gate_passed: bool
    deterministic_blockers: tuple[str, ...]


class SecToolCheckedReport(_FrozenModel):
    schema_version: int
    report_version: str
    dataset_id: str
    dataset_version: str
    scorer_version: str
    manifest_sha256: str
    case_count: int
    run_count: int
    execution_boundary: SecToolExecutionBoundary
    strategy_scores: Mapping[SecToolStrategy, SecToolStrategyScore]
    comparison: SecToolComparison
    deterministic_gate_passed: bool
    deterministic_blockers: tuple[str, ...]
    day7_closeout_ready: bool
    closeout_blockers: tuple[str, ...]

    @field_validator("manifest_sha256")
    @classmethod
    def _validate_manifest_sha256(cls, value: str) -> str:
        if _SHA256_PATTERN.fullmatch(value) is None:
            raise ValueError("SEC Tool manifest checksum is invalid")
        return value


def load_sec_tool_dataset(path: Path) -> SecToolDataset:
    return SecToolDataset.model_validate(_load_json(path))


def load_sec_tool_observations(path: Path) -> SecToolObservationSet:
    return SecToolObservationSet.model_validate(_load_json(path))


def load_sec_tool_report(path: Path) -> SecToolCheckedReport:
    return SecToolCheckedReport.model_validate(_load_json(path))


def score_sec_tool_dataset(
    dataset: SecToolDataset,
    observations: Iterable[SecToolCaseObservation],
) -> SecToolScore:
    observed_by_key = {
        (observation.case_id, observation.strategy): observation for observation in observations
    }
    expected_keys = {
        (case.case_id, strategy.strategy)
        for case in dataset.cases
        for strategy in dataset.strategies
    }
    if len(observed_by_key) != len(expected_keys) or set(observed_by_key) != expected_keys:
        raise ValueError("SEC Tool observations must cover every case and strategy exactly")

    case_by_id = {case.case_id: case for case in dataset.cases}
    strategy_by_name = {strategy.strategy: strategy for strategy in dataset.strategies}
    strategy_scores: dict[SecToolStrategy, SecToolStrategyScore] = {}
    for strategy in SecToolStrategy:
        selected = tuple(observed_by_key[(case.case_id, strategy)] for case in dataset.cases)
        passed = tuple(_case_passed(case_by_id[item.case_id], item) for item in selected)
        simple_indexes = tuple(
            index
            for index, item in enumerate(selected)
            if case_by_id[item.case_id].kind is SecToolCaseKind.SIMPLE_FACT
        )
        complex_indexes = tuple(
            index
            for index, item in enumerate(selected)
            if case_by_id[item.case_id].kind.value in _COMPLEX_KINDS
        )
        no_answer_indexes = tuple(
            index
            for index, item in enumerate(selected)
            if case_by_id[item.case_id].kind is SecToolCaseKind.NO_ANSWER
        )
        answered_indexes = tuple(
            index
            for index, item in enumerate(selected)
            if case_by_id[item.case_id].expected_outcome is SecToolOutcome.ANSWERED
        )
        calculation_indexes = tuple(
            index
            for index, item in enumerate(selected)
            if case_by_id[item.case_id].kind is SecToolCaseKind.CALCULATION
        )
        manifest = strategy_by_name[strategy]
        metrics = {
            "case_accuracy": _boolean_metric(passed),
            "simple_accuracy": _boolean_metric(tuple(passed[index] for index in simple_indexes)),
            "complex_accuracy": _boolean_metric(tuple(passed[index] for index in complex_indexes)),
            "no_answer_abstention": _boolean_metric(
                tuple(
                    selected[index].observed_outcome is SecToolOutcome.INSUFFICIENT_EVIDENCE
                    and selected[index].answer_key is None
                    for index in no_answer_indexes
                )
            ),
            "citation_resolvability": _boolean_metric(
                tuple(selected[index].citations_resolvable for index in answered_indexes)
            ),
            "calculation_lineage": _boolean_metric(
                tuple(
                    selected[index].program == case_by_id[selected[index].case_id].expected_program
                    and selected[index].derived_lineage_complete
                    for index in calculation_indexes
                )
            ),
            "tool_surface_adherence": _boolean_metric(
                tuple(
                    all(tool in manifest.available_tools for tool in item.observed_tools)
                    for item in selected
                )
            ),
            "budget_adherence": _boolean_metric(
                tuple(_within_budget(dataset.shared_budget, item) for item in selected)
            ),
            "wrong_company_rate": _boolean_metric(
                tuple(
                    item.selected_cik is not None
                    and item.selected_cik != case_by_id[item.case_id].expected_cik
                    for item in selected
                )
            ),
            "wrong_period_rate": _boolean_metric(
                tuple(
                    item.selected_report_period is not None
                    and item.selected_report_period
                    != case_by_id[item.case_id].expected_report_period
                    for item in selected
                )
            ),
            "wrong_accession_rate": _boolean_metric(
                tuple(
                    bool(
                        set(item.selected_accessions)
                        - set(case_by_id[item.case_id].expected_accessions)
                    )
                    for item in selected
                )
            ),
        }
        strategy_scores[strategy] = SecToolStrategyScore(
            strategy=strategy,
            metrics=metrics,
            total_steps=sum(item.steps for item in selected),
            total_tokens=sum(item.total_tokens for item in selected),
            total_cost_micro_usd=sum(item.cost_micro_usd for item in selected),
            total_latency_ms=sum(item.latency_ms for item in selected),
        )

    a1 = strategy_scores[SecToolStrategy.A1]
    a2 = strategy_scores[SecToolStrategy.A2]
    comparison = SecToolComparison(
        a2_complex_gain_over_a1=round(
            a2.metrics["complex_accuracy"].value - a1.metrics["complex_accuracy"].value,
            6,
        ),
        a2_simple_degradation_from_a1=round(
            a1.metrics["simple_accuracy"].value - a2.metrics["simple_accuracy"].value,
            6,
        ),
        a2_cost_increase_micro_usd=(a2.total_cost_micro_usd - a1.total_cost_micro_usd),
        a2_latency_increase_ms=(a2.total_latency_ms - a1.total_latency_ms),
    )
    blockers: list[str] = []
    for strategy, score in strategy_scores.items():
        for name in ("wrong_company_rate", "wrong_period_rate", "wrong_accession_rate"):
            if score.metrics[name].value != 0:
                blockers.append(f"{strategy.value}:{name}")
    for name in (
        "case_accuracy",
        "citation_resolvability",
        "calculation_lineage",
        "tool_surface_adherence",
        "budget_adherence",
    ):
        if a2.metrics[name].value != 1:
            blockers.append(f"a2:{name}")
    if a2.metrics["no_answer_abstention"].value < 0.9:
        blockers.append("a2:no_answer_abstention")
    if comparison.a2_complex_gain_over_a1 <= 0:
        blockers.append("a2:no_complex_net_benefit")
    if comparison.a2_simple_degradation_from_a1 > 0.02:
        blockers.append("a2:simple_degradation")
    return SecToolScore(
        strategy_scores=strategy_scores,
        comparison=comparison,
        deterministic_gate_passed=not blockers,
        deterministic_blockers=tuple(blockers),
    )


def build_sec_tool_report(
    dataset_path: Path,
    dataset: SecToolDataset,
    observation_set: SecToolObservationSet,
) -> SecToolCheckedReport:
    score = score_sec_tool_dataset(dataset, observation_set.observations)
    boundary = observation_set.execution_boundary
    closeout_checks = {
        "real_dependencies_not_executed": boundary.real_dependencies_executed,
        "live_sec_not_executed": boundary.live_sec_executed,
        "live_model_not_executed": boundary.live_model_executed,
        "browser_e2e_not_executed": boundary.browser_e2e_executed,
        "paired_bilingual_not_executed": boundary.paired_bilingual_executed,
        "branch_ci_not_passed": boundary.branch_ci_passed,
        "pr_ci_not_passed": boundary.pr_ci_passed,
        "main_ci_not_passed": boundary.main_ci_passed,
        "owner_review_missing": boundary.owner_reviewed,
    }
    closeout_blockers = tuple(name for name, passed in closeout_checks.items() if not passed)
    return SecToolCheckedReport(
        schema_version=1,
        report_version=SEC_TOOL_REPORT_VERSION,
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        scorer_version=dataset.scorer_version,
        manifest_sha256=hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        case_count=len(dataset.cases),
        run_count=len(observation_set.observations),
        execution_boundary=observation_set.execution_boundary,
        strategy_scores=score.strategy_scores,
        comparison=score.comparison,
        deterministic_gate_passed=score.deterministic_gate_passed,
        deterministic_blockers=score.deterministic_blockers,
        day7_closeout_ready=score.deterministic_gate_passed and not closeout_blockers,
        closeout_blockers=closeout_blockers,
    )


def render_sec_tool_markdown(report: SecToolCheckedReport) -> str:
    lines = [
        "# `sec-tool-v1` deterministic A0/A1/A2 report",
        "",
        f"- Manifest SHA-256: `{report.manifest_sha256}`",
        f"- Cases / strategy runs: {report.case_count} / {report.run_count}",
        f"- Deterministic gate: {'PASS' if report.deterministic_gate_passed else 'FAIL'}",
        f"- Day 7 closeout ready: {'YES' if report.day7_closeout_ready else 'NO'}",
        "",
        (
            "| Strategy | Case | Simple | Complex | Abstention | Citation | Calc lineage "
            "| Steps | Cost (micro USD) | Latency (ms) |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for strategy in SecToolStrategy:
        score = report.strategy_scores[strategy]
        metrics = score.metrics
        lines.append(
            f"| {strategy.value.upper()} | {metrics['case_accuracy'].value:.6f} | "
            f"{metrics['simple_accuracy'].value:.6f} | "
            f"{metrics['complex_accuracy'].value:.6f} | "
            f"{metrics['no_answer_abstention'].value:.6f} | "
            f"{metrics['citation_resolvability'].value:.6f} | "
            f"{metrics['calculation_lineage'].value:.6f} | {score.total_steps} | "
            f"{score.total_cost_micro_usd} | {score.total_latency_ms} |"
        )
    lines.extend(
        [
            "",
            "## Comparison",
            "",
            f"- A2 complex gain over A1: {report.comparison.a2_complex_gain_over_a1:.6f}",
            (
                "- A2 simple degradation from A1: "
                f"{report.comparison.a2_simple_degradation_from_a1:.6f}"
            ),
            f"- A2 cost increase: {report.comparison.a2_cost_increase_micro_usd} micro USD",
            f"- A2 latency increase: {report.comparison.a2_latency_increase_ms} ms",
            "",
            "## Boundary",
            "",
            (
                "This is a deterministic frozen-observation contract report. It is not a live "
                "SEC, live model, public benchmark, bilingual, or browser end-to-end result."
            ),
            "",
            "Closeout blockers:",
        ]
    )
    lines.extend(f"- `{blocker}`" for blocker in report.closeout_blockers)
    return "\n".join(lines) + "\n"


def _case_passed(case: SecToolEvalCase, observation: SecToolCaseObservation) -> bool:
    identity_complete = (
        observation.selected_cik == case.expected_cik
        and observation.selected_report_period == case.expected_report_period
        and set(observation.selected_accessions) == set(case.expected_accessions)
    )
    if case.expected_outcome is SecToolOutcome.INSUFFICIENT_EVIDENCE:
        return (
            observation.observed_outcome is SecToolOutcome.INSUFFICIENT_EVIDENCE
            and observation.answer_key is None
            and not observation.evidence_keys
        )
    base_passed = (
        observation.observed_outcome is SecToolOutcome.ANSWERED
        and observation.answer_key == case.expected_answer_key
        and set(case.expected_evidence_keys) <= set(observation.evidence_keys)
        and identity_complete
        and observation.citations_resolvable
    )
    if not base_passed or observation.strategy is SecToolStrategy.A0:
        return base_passed
    if case.kind is SecToolCaseKind.CALCULATION:
        return observation.program == case.expected_program and observation.derived_lineage_complete
    if case.kind is SecToolCaseKind.AMENDMENT:
        return "sec.diff_filings@v1" in observation.observed_tools
    return True


def _within_budget(budget: SecToolBudget, observation: SecToolCaseObservation) -> bool:
    return (
        observation.steps <= budget.max_steps
        and observation.total_tokens <= budget.max_total_tokens
        and observation.cost_micro_usd <= budget.max_cost_micro_usd
        and observation.latency_ms <= budget.max_latency_ms
    )


def _boolean_metric(values: Sequence[bool]) -> SecToolMetric:
    if not values:
        raise ValueError("SEC Tool metric denominator cannot be empty")
    numerator = sum(values)
    return SecToolMetric(
        numerator=numerator,
        denominator=len(values),
        value=round(numerator / len(values), 6),
    )


def _load_json(path: Path) -> Mapping[str, object]:
    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_non_finite,
    )
    if not isinstance(value, Mapping):
        raise ValueError("SEC Tool JSON root must be an object")
    return cast(Mapping[str, object], value)


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> object:
    raise ValueError(f"Non-finite JSON number is forbidden: {value}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the deterministic sec-tool-v1 report")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args(argv)
    dataset = load_sec_tool_dataset(args.dataset)
    observations = load_sec_tool_observations(args.observations)
    report = build_sec_tool_report(args.dataset, dataset, observations)
    args.json_output.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    args.markdown_output.write_text(
        render_sec_tool_markdown(report),
        encoding="utf-8",
        newline="\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
