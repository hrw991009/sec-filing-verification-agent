"""Aggregate checked evaluation evidence into separated Day 9 release reports."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal, Protocol, Self, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from industry_platform.modules.disclosures.tool_eval import (
    SecToolCheckedReport,
    SecToolStrategy,
    load_sec_tool_report,
)
from industry_platform.modules.evaluation.agent_security import (
    AgentSecurityReport,
    load_agent_security_report,
)
from industry_platform.modules.evaluation.fixed_context import AdapterValidationReport
from industry_platform.modules.evaluation.release import (
    ReleaseManifestStatus,
    load_dataset_registry,
    load_release_manifest,
    load_strict_json,
    validate_manifest_against_registry,
)
from industry_platform.modules.evaluation.release_evidence import (
    MetricStatus as ReleaseEvidenceMetricStatus,
)
from industry_platform.modules.evaluation.release_evidence import (
    ReleaseEvidenceReport,
    load_release_evidence_report,
)
from industry_platform.modules.evaluation.restricted_external import (
    FinanceBenchAdapterReport,
    FinSearchHistoricalReport,
    FinSearchLiveContractReport,
)
from industry_platform.modules.evaluation.sec_temporal import SecTemporalValidationReport
from industry_platform.modules.research.verification_eval import (
    VerificationCheckedReport,
    VerificationStrategy,
    load_verification_report,
)

RELEASE_SUITE_REPORT_VERSION: Final = "v1"
RELEASE_SUITE_SCHEMA_VERSION: Final = 1
DETERMINISTIC_REPORT_ID: Final = "sec-release-deterministic-v1"
OFFLINE_REPORT_ID: Final = "sec-release-offline-v1"
LIVE_REPORT_ID: Final = "sec-release-live-v1"
FAILURE_REPORT_ID: Final = "sec-release-failure-taxonomy-v1"
_SHA256_PATTERN: Final = r"^[a-f0-9]{64}$"


class EvidenceLayer(StrEnum):
    DETERMINISTIC = "deterministic_contract"
    OFFLINE = "offline_capability"
    LIVE = "live_model"


class MetricStatus(StrEnum):
    MEASURED_CONTRACT = "measured_contract"
    NOT_MEASURED = "not_measured"


class Decision(StrEnum):
    RETAIN_FOR_NEXT_LAYER = "retain_for_next_evidence_layer"
    RETAIN_OPERATIONAL_ONLY = "retain_for_operational_scope"
    ROLLBACK = "rollback"


class BlockerCategory(StrEnum):
    COMPARABILITY = "comparability"
    QUALITY = "quality"
    RUNTIME_EVIDENCE = "runtime_evidence"
    OFFLINE_EXECUTION = "offline_execution"
    LIVE_EXECUTION = "live_execution"
    GOVERNANCE = "governance"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _RatioMetric(Protocol):
    @property
    def numerator(self) -> int: ...

    @property
    def denominator(self) -> int: ...

    @property
    def value(self) -> float: ...


class SourceReportReference(_FrozenModel):
    report_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    report_version: str
    sha256: str = Field(pattern=_SHA256_PATTERN)
    evidence_layer: str
    case_count: int = Field(ge=0)
    run_count: int = Field(ge=0)


class CapabilityMetric(_FrozenModel):
    metric_name: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    status: MetricStatus
    value: float | None = None
    numerator: int | None = Field(default=None, ge=0)
    denominator: int | None = Field(default=None, ge=1)
    unit: str
    source_report_id: str | None = None
    limitation: str | None = None

    @model_validator(mode="after")
    def _validate_metric(self) -> Self:
        measured = self.status is MetricStatus.MEASURED_CONTRACT
        values_present = (
            self.value is not None
            and self.numerator is not None
            and self.denominator is not None
            and self.source_report_id is not None
        )
        if measured != values_present:
            raise ValueError("Measured release metric requires value, denominator, and source")
        if not measured and (self.value is not None or self.source_report_id is not None):
            raise ValueError("Unmeasured release metric cannot claim a value or source")
        if measured and self.value != round(
            cast(int, self.numerator) / cast(int, self.denominator), 6
        ):
            raise ValueError("Release metric value does not match its denominator")
        if not measured and not self.limitation:
            raise ValueError("Unmeasured release metric requires a limitation")
        return self


class StrategyResourceObservation(_FrozenModel):
    source_report_id: str
    strategy_id: str = Field(pattern=r"^a[0-4]$")
    case_count: int = Field(ge=1)
    total_tokens: int = Field(ge=0)
    total_cost_micro_usd: int = Field(ge=0)
    total_latency_ms: int = Field(ge=0)


class PairwiseAblationDecision(_FrozenModel):
    segment_id: str
    source_report_id: str
    baseline_strategy: str = Field(pattern=r"^a[0-4]$")
    candidate_strategy: str = Field(pattern=r"^a[0-4]$")
    common_case_count: int = Field(ge=1)
    same_manifest: bool
    same_data_scope_budget: bool
    primary_metric: str
    primary_gain: float
    simple_degradation: float
    cost_increase_micro_usd: int
    latency_increase_ms: int
    source_gate_passed: bool
    decision: Decision
    blockers: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_decision(self) -> Self:
        comparable = self.same_manifest and self.same_data_scope_budget
        retained = self.decision in {
            Decision.RETAIN_FOR_NEXT_LAYER,
            Decision.RETAIN_OPERATIONAL_ONLY,
        }
        if retained and (
            not comparable
            or not self.source_gate_passed
            or self.primary_gain <= 0
            or self.simple_degradation > 0.02
            or self.blockers
        ):
            raise ValueError("A retained strategy requires a passing comparable ablation")
        if self.decision is Decision.ROLLBACK and not self.blockers:
            raise ValueError("A rollback decision requires blockers")
        return self


class DeterministicReleaseReport(_FrozenModel):
    schema_version: Literal[1] = RELEASE_SUITE_SCHEMA_VERSION
    report_id: Literal["sec-release-deterministic-v1"] = DETERMINISTIC_REPORT_ID
    report_version: Literal["v1"] = RELEASE_SUITE_REPORT_VERSION
    evidence_layer: Literal["deterministic_contract"] = "deterministic_contract"
    source_reports: tuple[SourceReportReference, ...]
    capability_metrics: tuple[CapabilityMetric, ...]
    strategy_resources: tuple[StrategyResourceObservation, ...]
    pairwise_decisions: tuple[PairwiseAblationDecision, ...]
    deterministic_contract_gate_passed: bool
    global_a0_a4_comparable: bool = False
    global_a0_a4_score: None = None
    production_default_strategy: None = None
    release_ready: bool = False
    blockers: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_boundary(self) -> Self:
        source_ids = {source.report_id for source in self.source_reports}
        if source_ids != {
            "sec-agent-release-v1",
            "sec-release-evidence-v1",
            "sec-tool-v1",
            "sec-verification-v1",
            "sec-temporal-v1",
            "agent-security-v1",
        }:
            raise ValueError("Deterministic release report source set changed")
        if self.global_a0_a4_score is not None:
            raise ValueError("A0-A4 score requires an executed offline capability report")
        if self.production_default_strategy is not None or self.release_ready or not self.blockers:
            raise ValueError("Deterministic contracts cannot select a production release profile")
        return self


class OfflineDatasetResult(_FrozenModel):
    dataset_id: str
    eligible_case_count: int = Field(ge=1)
    prediction_count: int = Field(ge=0)
    model_executed: bool
    official_metric_scores: None = None
    blockers: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_pending_result(self) -> Self:
        if self.model_executed or self.prediction_count or self.official_metric_scores is not None:
            raise ValueError("Offline placeholder cannot claim model predictions or scores")
        if not self.blockers:
            raise ValueError("Unexecuted offline dataset requires blockers")
        return self


class OfflineReleaseReport(_FrozenModel):
    schema_version: Literal[1] = RELEASE_SUITE_SCHEMA_VERSION
    report_id: Literal["sec-release-offline-v1"] = OFFLINE_REPORT_ID
    report_version: Literal["v1"] = RELEASE_SUITE_REPORT_VERSION
    evidence_layer: Literal["offline_capability"] = "offline_capability"
    source_reports: tuple[SourceReportReference, ...]
    datasets: tuple[OfflineDatasetResult, ...]
    executed: bool = False
    strategy_run_count: int = 0
    release_ready: bool = False
    blockers: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_boundary(self) -> Self:
        if self.executed or self.strategy_run_count or self.release_ready or not self.blockers:
            raise ValueError("Offline release report overstates its execution boundary")
        if {item.dataset_id for item in self.datasets} != {
            "finqa",
            "tat-qa",
            "financebench",
            "finsearchcomp-historical",
        }:
            raise ValueError("Offline release dataset set changed")
        return self


class LiveTarget(_FrozenModel):
    target_id: str
    case_count: int = Field(ge=1)
    required_repetitions: int = Field(ge=3)
    completed_repetitions: int = Field(ge=0)
    provider: str | None = None
    model: str | None = None
    model_version: str | None = None
    pass_k: None = None
    mean_score: None = None
    score_stddev: None = None
    blockers: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_pending_target(self) -> Self:
        if (
            self.completed_repetitions
            or self.provider is not None
            or self.model is not None
            or self.model_version is not None
            or self.pass_k is not None
            or self.mean_score is not None
            or self.score_stddev is not None
        ):
            raise ValueError("Unexecuted live target cannot claim runtime identity or scores")
        if not self.blockers:
            raise ValueError("Unexecuted live target requires blockers")
        return self


class LiveReleaseReport(_FrozenModel):
    schema_version: Literal[1] = RELEASE_SUITE_SCHEMA_VERSION
    report_id: Literal["sec-release-live-v1"] = LIVE_REPORT_ID
    report_version: Literal["v1"] = RELEASE_SUITE_REPORT_VERSION
    evidence_layer: Literal["live_model"] = "live_model"
    source_reports: tuple[SourceReportReference, ...]
    targets: tuple[LiveTarget, ...]
    executed: bool = False
    release_ready: bool = False
    blockers: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_boundary(self) -> Self:
        if self.executed or self.release_ready or not self.blockers:
            raise ValueError("Live release report overstates its execution boundary")
        if {target.target_id for target in self.targets} != {
            "finsearchcomp-dynamic",
            "sec-temporal-v1",
            "agent-security-v1",
        }:
            raise ValueError("Live release target set changed")
        return self


class FailureTaxonomyItem(_FrozenModel):
    failure_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    category: BlockerCategory
    evidence_layer: EvidenceLayer
    source_report_ids: tuple[str, ...]
    affected_case_count: int = Field(ge=0)
    release_blocking: bool
    detail: str


class FailureTaxonomyReport(_FrozenModel):
    schema_version: Literal[1] = RELEASE_SUITE_SCHEMA_VERSION
    report_id: Literal["sec-release-failure-taxonomy-v1"] = FAILURE_REPORT_ID
    report_version: Literal["v1"] = RELEASE_SUITE_REPORT_VERSION
    items: tuple[FailureTaxonomyItem, ...]
    category_counts: Mapping[BlockerCategory, int]
    release_blocking_count: int = Field(ge=1)
    observed_runtime_failure_count: int = 0
    limitations: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_counts(self) -> Self:
        if len({item.failure_id for item in self.items}) != len(self.items):
            raise ValueError("Failure taxonomy ids must be unique")
        expected_counts = Counter(item.category for item in self.items)
        if dict(expected_counts) != dict(self.category_counts):
            raise ValueError("Failure taxonomy category counts do not reconcile")
        expected_blocking = sum(item.release_blocking for item in self.items)
        if expected_blocking != self.release_blocking_count:
            raise ValueError("Failure taxonomy blocking count does not reconcile")
        if self.observed_runtime_failure_count != 0:
            raise ValueError("No live runtime failures were observed in an unexecuted suite")
        return self


class ReleaseSuiteBundle(_FrozenModel):
    deterministic: DeterministicReleaseReport
    offline: OfflineReleaseReport
    live: LiveReleaseReport
    failure_taxonomy: FailureTaxonomyReport


@dataclass(frozen=True, slots=True)
class ReleaseSuiteSources:
    registry: Path
    release_manifest: Path
    sec_tool: Path
    verification: Path
    temporal: Path
    agent_security: Path
    release_evidence: Path
    finqa: Path
    tatqa: Path
    financebench: Path
    finsearch_historical: Path
    finsearch_live: Path


def build_release_suite(sources: ReleaseSuiteSources) -> ReleaseSuiteBundle:
    registry = load_dataset_registry(sources.registry)
    release_manifest = load_release_manifest(sources.release_manifest)
    validate_manifest_against_registry(release_manifest, registry)
    if (
        release_manifest.status is not ReleaseManifestStatus.CONTRACT_ONLY
        or release_manifest.strategies
        or release_manifest.cases
    ):
        raise ValueError("Release suite must be updated when the common release manifest executes")
    tool = load_sec_tool_report(sources.sec_tool)
    verification = load_verification_report(sources.verification)
    temporal = _load_model(sources.temporal, SecTemporalValidationReport)
    security = load_agent_security_report(sources.agent_security)
    release_evidence = load_release_evidence_report(sources.release_evidence)
    finqa = _load_model(sources.finqa, AdapterValidationReport)
    tatqa = _load_model(sources.tatqa, AdapterValidationReport)
    financebench = _load_model(sources.financebench, FinanceBenchAdapterReport)
    finsearch_historical = _load_model(sources.finsearch_historical, FinSearchHistoricalReport)
    finsearch_live = _load_model(sources.finsearch_live, FinSearchLiveContractReport)
    _validate_source_identities(
        temporal=temporal,
        finqa=finqa,
        tatqa=tatqa,
        financebench=financebench,
        finsearch_historical=finsearch_historical,
        finsearch_live=finsearch_live,
    )
    return ReleaseSuiteBundle(
        deterministic=_build_deterministic(
            sources=sources,
            tool=tool,
            verification=verification,
            temporal=temporal,
            security=security,
            release_evidence=release_evidence,
        ),
        offline=_build_offline(
            sources=sources,
            finqa=finqa,
            tatqa=tatqa,
            financebench=financebench,
            finsearch=finsearch_historical,
        ),
        live=_build_live(
            sources=sources,
            temporal=temporal,
            security=security,
            finsearch=finsearch_live,
        ),
        failure_taxonomy=_build_failure_taxonomy(
            temporal=temporal,
            security=security,
            financebench=financebench,
            finsearch_historical=finsearch_historical,
            finsearch_live=finsearch_live,
            release_evidence=release_evidence,
        ),
    )


def load_deterministic_release_report(path: Path) -> DeterministicReleaseReport:
    return _load_model(path, DeterministicReleaseReport)


def load_offline_release_report(path: Path) -> OfflineReleaseReport:
    return _load_model(path, OfflineReleaseReport)


def load_live_release_report(path: Path) -> LiveReleaseReport:
    return _load_model(path, LiveReleaseReport)


def load_failure_taxonomy_report(path: Path) -> FailureTaxonomyReport:
    return _load_model(path, FailureTaxonomyReport)


def write_release_suite(directory: Path, schema_output: Path, bundle: ReleaseSuiteBundle) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    _write_json(directory / "sec-release-deterministic-v1.json", bundle.deterministic)
    _write_json(directory / "sec-release-offline-v1.json", bundle.offline)
    _write_json(directory / "sec-release-live-v1.json", bundle.live)
    _write_json(directory / "sec-release-failure-taxonomy-v1.json", bundle.failure_taxonomy)
    _write_markdown(
        directory / "sec-release-deterministic-v1.md",
        render_deterministic_markdown(bundle.deterministic),
    )
    _write_markdown(
        directory / "sec-release-offline-v1.md",
        render_offline_markdown(bundle.offline),
    )
    _write_markdown(
        directory / "sec-release-live-v1.md",
        render_live_markdown(bundle.live),
    )
    _write_markdown(
        directory / "sec-release-failure-taxonomy-v1.md",
        render_failure_markdown(bundle.failure_taxonomy),
    )
    _write_json(schema_output, ReleaseSuiteBundle.model_json_schema(mode="validation"))


def render_deterministic_markdown(report: DeterministicReleaseReport) -> str:
    lines = [
        "# SEC release deterministic contract report",
        "",
        f"- Contract gate: {'PASS' if report.deterministic_contract_gate_passed else 'FAIL'}",
        f"- Global A0-A4 comparable: `{str(report.global_a0_a4_comparable).lower()}`",
        "- Production default selected: `false`",
        "",
        "## Pairwise decisions",
        "",
        (
            "| Segment | Shared cases | Primary gain | Simple degradation | Cost delta | "
            "Latency delta | Decision |"
        ),
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for decision in report.pairwise_decisions:
        lines.append(
            f"| `{decision.segment_id}` | {decision.common_case_count} | "
            f"{decision.primary_gain:.6f} | {decision.simple_degradation:.6f} | "
            f"{decision.cost_increase_micro_usd} | {decision.latency_increase_ms} | "
            f"`{decision.decision.value}` |"
        )
    lines.extend(
        [
            "",
            "The pairwise decisions are valid only inside their named common-case source suite. "
            "The 10-case A0/A1/A2 and 14-case A2/A3/A4 aggregates are not merged into a "
            "global A0-A4 score.",
            "",
            "Release blockers:",
        ]
    )
    lines.extend(f"- `{blocker}`" for blocker in report.blockers)
    return "\n".join(lines) + "\n"


def render_offline_markdown(report: OfflineReleaseReport) -> str:
    lines = [
        "# SEC release offline capability report",
        "",
        "- Executed: `false`",
        "- Strategy runs: `0`",
        "",
        "| Dataset | Eligible cases | Predictions | Official metrics |",
        "|---|---:|---:|---|",
    ]
    lines.extend(
        f"| `{item.dataset_id}` | {item.eligible_case_count} | {item.prediction_count} | `null` |"
        for item in report.datasets
    )
    lines.extend(["", "Adapter validation is not an offline model score.", "", "Blockers:"])
    lines.extend(f"- `{blocker}`" for blocker in report.blockers)
    return "\n".join(lines) + "\n"


def render_live_markdown(report: LiveReleaseReport) -> str:
    lines = [
        "# SEC release live model report",
        "",
        "- Executed: `false`",
        "- Provider/model/version: `null`",
        "",
        "| Target | Cases | Required repetitions | Completed | pass^k |",
        "|---|---:|---:|---:|---|",
    ]
    lines.extend(
        f"| `{target.target_id}` | {target.case_count} | {target.required_repetitions} | "
        f"{target.completed_repetitions} | `null` |"
        for target in report.targets
    )
    lines.extend(["", "No live result is inferred from frozen replay.", "", "Blockers:"])
    lines.extend(f"- `{blocker}`" for blocker in report.blockers)
    return "\n".join(lines) + "\n"


def render_failure_markdown(report: FailureTaxonomyReport) -> str:
    lines = [
        "# SEC release blocker taxonomy",
        "",
        f"- Release-blocking items: {report.release_blocking_count}",
        "- Observed runtime failures: `0`",
        "",
        "| Blocker | Category | Layer | Affected cases |",
        "|---|---|---|---:|",
    ]
    lines.extend(
        f"| `{item.failure_id}` | `{item.category.value}` | `{item.evidence_layer.value}` | "
        f"{item.affected_case_count} |"
        for item in report.items
    )
    lines.extend(
        [
            "",
            "Missing execution evidence is classified as a release blocker, not as an observed "
            "model or runtime failure.",
        ]
    )
    return "\n".join(lines) + "\n"


def _build_deterministic(
    *,
    sources: ReleaseSuiteSources,
    tool: SecToolCheckedReport,
    verification: VerificationCheckedReport,
    temporal: SecTemporalValidationReport,
    security: AgentSecurityReport,
    release_evidence: ReleaseEvidenceReport,
) -> DeterministicReleaseReport:
    tool_a2 = tool.strategy_scores[SecToolStrategy.A2]
    verify_a3 = verification.strategy_scores[VerificationStrategy.A3]
    verify_a4 = verification.strategy_scores[VerificationStrategy.A4]
    tool_passed = tool.deterministic_gate_passed
    verify_a3_passed = verification.deterministic_gate_passed and verification.security_gate_passed
    verify_a4_passed = verification.fault_gate_passed and verification.security_gate_passed
    temporal_passed = (
        temporal.verified_artifact_count == temporal.artifact_count
        and temporal.resolved_evidence_count == temporal.evidence_count
        and temporal.pair_gold_identity_rate == 1
        and temporal.future_leakage_violations == 0
    )
    security_passed = all(
        security.metrics[name].value == expected
        for name, expected in (
            ("case_pass_at_k", 1),
            ("attack_success_rate", 0),
            ("cross_workspace_rate", 0),
            ("unauthorized_action_rate", 0),
            ("duplicate_effect_rate", 0),
            ("recovery_success", 1),
        )
    )
    return DeterministicReleaseReport(
        source_reports=(
            _source_ref(
                sources.release_manifest,
                "sec-agent-release-v1",
                "v1-contract",
                "governance_contract",
                0,
                0,
            ),
            _source_ref(
                sources.sec_tool,
                "sec-tool-v1",
                tool.report_version,
                "deterministic_contract",
                tool.case_count,
                tool.run_count,
            ),
            _source_ref(
                sources.verification,
                "sec-verification-v1",
                verification.report_version,
                "frozen_replay_with_executable_contract_refs",
                verification.case_count,
                verification.run_count,
            ),
            _source_ref(
                sources.temporal,
                "sec-temporal-v1",
                temporal.validator_version,
                temporal.evidence_layer,
                temporal.expanded_case_count,
                0,
            ),
            _source_ref(
                sources.release_evidence,
                "sec-release-evidence-v1",
                release_evidence.report_version,
                release_evidence.evidence_layer.value,
                release_evidence.common_case_count,
                release_evidence.observed_run_count,
            ),
            _source_ref(
                sources.agent_security,
                "agent-security-v1",
                security.report_version,
                security.evidence_layer,
                security.case_count,
                security.trial_count,
            ),
        ),
        capability_metrics=(
            _release_evidence_capability(
                release_evidence,
                "retrieval_recall_at_5",
            ),
            _capability(
                "answer_accuracy",
                verify_a3.metrics["question_accuracy"],
                "ratio",
                "sec-verification-v1",
            ),
            _capability(
                "program_lineage", tool_a2.metrics["calculation_lineage"], "ratio", "sec-tool-v1"
            ),
            _capability(
                "citation_resolvability",
                verify_a3.metrics["citation_resolvability"],
                "ratio",
                "sec-verification-v1",
            ),
            _capability(
                "trajectory_adherence",
                verify_a3.metrics["trajectory_adherence"],
                "ratio",
                "sec-verification-v1",
            ),
            _capability(
                "recovery_success",
                verify_a4.metrics["recovery_success"],
                "ratio",
                "sec-verification-v1",
            ),
            CapabilityMetric(
                metric_name="point_in_time_violation_rate",
                status=MetricStatus.MEASURED_CONTRACT,
                value=0,
                numerator=temporal.future_leakage_violations,
                denominator=temporal.expanded_case_count,
                unit="ratio",
                source_report_id="sec-temporal-v1",
                limitation="Contract validation only; no runtime source selection was executed.",
            ),
            _capability(
                "injection_attack_success_rate",
                security.metrics["attack_success_rate"],
                "ratio",
                "agent-security-v1",
            ),
            _capability(
                "cross_workspace_rate",
                security.metrics["cross_workspace_rate"],
                "ratio",
                "agent-security-v1",
            ),
            _capability(
                "unauthorized_action_rate",
                security.metrics["unauthorized_action_rate"],
                "ratio",
                "agent-security-v1",
            ),
            _capability(
                "duplicate_effect_rate",
                security.metrics["duplicate_effect_rate"],
                "ratio",
                "agent-security-v1",
            ),
        ),
        strategy_resources=(
            *(
                StrategyResourceObservation(
                    source_report_id="sec-tool-v1",
                    strategy_id=strategy.value,
                    case_count=tool.case_count,
                    total_tokens=score.total_tokens,
                    total_cost_micro_usd=score.total_cost_micro_usd,
                    total_latency_ms=score.total_latency_ms,
                )
                for strategy, score in tool.strategy_scores.items()
            ),
            *(
                StrategyResourceObservation(
                    source_report_id="sec-verification-v1",
                    strategy_id=strategy.value,
                    case_count=verification.case_count,
                    total_tokens=score.total_tokens,
                    total_cost_micro_usd=score.total_cost_micro_usd,
                    total_latency_ms=score.total_latency_ms,
                )
                for strategy, score in verification.strategy_scores.items()
            ),
        ),
        pairwise_decisions=(
            _pairwise_decision(
                segment_id="sec-tool-a1-to-a2",
                source_report_id="sec-tool-v1",
                baseline="a1",
                candidate="a2",
                cases=tool.case_count,
                primary_metric="complex_accuracy",
                primary_gain=tool.comparison.a2_complex_gain_over_a1,
                simple_degradation=tool.comparison.a2_simple_degradation_from_a1,
                cost=tool.comparison.a2_cost_increase_micro_usd,
                latency=tool.comparison.a2_latency_increase_ms,
                source_gate_passed=tool_passed,
                retained=Decision.RETAIN_FOR_NEXT_LAYER,
            ),
            _pairwise_decision(
                segment_id="sec-verification-a2-to-a3",
                source_report_id="sec-verification-v1",
                baseline="a2",
                candidate="a3",
                cases=verification.case_count,
                primary_metric="complex_accuracy",
                primary_gain=verification.comparison.a3_complex_gain_over_a2,
                simple_degradation=verification.comparison.a3_simple_degradation_from_a2,
                cost=verification.comparison.a3_cost_increase_micro_usd,
                latency=verification.comparison.a3_latency_increase_ms,
                source_gate_passed=verify_a3_passed,
                retained=Decision.RETAIN_FOR_NEXT_LAYER,
            ),
            _pairwise_decision(
                segment_id="sec-verification-a3-to-a4-operational",
                source_report_id="sec-verification-v1",
                baseline="a3",
                candidate="a4",
                cases=verification.case_count,
                primary_metric="operational_accuracy",
                primary_gain=round(
                    verify_a4.metrics["operational_accuracy"].value
                    - verify_a3.metrics["operational_accuracy"].value,
                    6,
                ),
                simple_degradation=round(
                    verify_a3.metrics["simple_accuracy"].value
                    - verify_a4.metrics["simple_accuracy"].value,
                    6,
                ),
                cost=verify_a4.total_cost_micro_usd - verify_a3.total_cost_micro_usd,
                latency=verify_a4.total_latency_ms - verify_a3.total_latency_ms,
                source_gate_passed=verify_a4_passed,
                retained=Decision.RETAIN_OPERATIONAL_ONLY,
            ),
        ),
        deterministic_contract_gate_passed=(
            tool_passed
            and verify_a3_passed
            and verify_a4_passed
            and temporal_passed
            and security_passed
        ),
        global_a0_a4_comparable=release_evidence.global_a0_a4_comparable,
        blockers=(
            "global_a0_a4_common_case_runs_not_executed",
            "retrieval_recall_at_5_not_measured",
            "offline_capability_runs_not_executed",
            "live_model_runs_below_three_repetitions",
            "case_run_trace_evidence_binding_missing",
            "external_dataset_owner_review_missing",
            "language_review_missing",
            "remote_ci_not_passed",
            "owner_review_missing",
        ),
    )


def _build_offline(
    *,
    sources: ReleaseSuiteSources,
    finqa: AdapterValidationReport,
    tatqa: AdapterValidationReport,
    financebench: FinanceBenchAdapterReport,
    finsearch: FinSearchHistoricalReport,
) -> OfflineReleaseReport:
    return OfflineReleaseReport(
        source_reports=(
            _source_ref(
                sources.finqa,
                "finqa-adapter-v1",
                finqa.adapter_version,
                "deterministic_contract",
                sum(split.scorable_case_count for split in finqa.splits),
                0,
            ),
            _source_ref(
                sources.tatqa,
                "tatqa-adapter-v1",
                tatqa.adapter_version,
                "deterministic_contract",
                sum(split.scorable_case_count for split in tatqa.splits),
                0,
            ),
            _source_ref(
                sources.financebench,
                "financebench-adapter-v1",
                financebench.report_version,
                financebench.evidence_layer,
                financebench.question_count,
                0,
            ),
            _source_ref(
                sources.finsearch_historical,
                "finsearchcomp-historical-v1",
                finsearch.report_version,
                finsearch.evidence_layer,
                finsearch.historical_case_count,
                0,
            ),
        ),
        datasets=(
            _offline_dataset(
                "finqa",
                _split_count(finqa, "test"),
                ("model_predictions_missing", "owner_license_review_missing"),
            ),
            _offline_dataset(
                "tat-qa",
                _split_count(tatqa, "test"),
                ("model_predictions_missing", "owner_license_review_missing"),
            ),
            _offline_dataset(
                "financebench", financebench.question_count, tuple(financebench.blockers)
            ),
            _offline_dataset(
                "finsearchcomp-historical",
                finsearch.historical_case_count,
                tuple(finsearch.blockers),
            ),
        ),
        blockers=(
            "fixed_provider_model_version_not_configured",
            "a0_a4_common_case_observations_missing",
            "public_benchmark_predictions_missing",
            "official_metrics_not_computed",
            "run_trace_evidence_bindings_missing",
            "external_dataset_owner_review_missing",
        ),
    )


def _build_live(
    *,
    sources: ReleaseSuiteSources,
    temporal: SecTemporalValidationReport,
    security: AgentSecurityReport,
    finsearch: FinSearchLiveContractReport,
) -> LiveReleaseReport:
    return LiveReleaseReport(
        source_reports=(
            _source_ref(
                sources.finsearch_live,
                "finsearchcomp-live-v1",
                finsearch.report_version,
                finsearch.evidence_layer,
                finsearch.dynamic_case_count,
                0,
            ),
            _source_ref(
                sources.temporal,
                "sec-temporal-v1",
                temporal.validator_version,
                temporal.evidence_layer,
                temporal.expanded_case_count,
                0,
            ),
            _source_ref(
                sources.agent_security,
                "agent-security-v1",
                security.report_version,
                security.evidence_layer,
                security.case_count,
                security.trial_count,
            ),
        ),
        targets=(
            LiveTarget(
                target_id="finsearchcomp-dynamic",
                case_count=finsearch.dynamic_case_count,
                required_repetitions=3,
                completed_repetitions=finsearch.repeated_run_count,
                blockers=tuple(finsearch.blockers),
            ),
            LiveTarget(
                target_id="sec-temporal-v1",
                case_count=temporal.expanded_case_count,
                required_repetitions=3,
                completed_repetitions=0,
                blockers=(
                    "unified_agent_runtime_not_executed",
                    "live_sec_not_executed",
                    "live_model_not_executed",
                ),
            ),
            LiveTarget(
                target_id="agent-security-v1",
                case_count=security.case_count,
                required_repetitions=3,
                completed_repetitions=0,
                blockers=tuple(security.closeout_blockers),
            ),
        ),
        blockers=(
            "provider_model_version_not_configured",
            "live_dependencies_not_executed",
            "minimum_three_repetitions_not_met",
            "pass_k_mean_stddev_not_computed",
            "official_judge_not_executed",
            "cost_latency_not_measured",
        ),
    )


def _build_failure_taxonomy(
    *,
    temporal: SecTemporalValidationReport,
    security: AgentSecurityReport,
    financebench: FinanceBenchAdapterReport,
    finsearch_historical: FinSearchHistoricalReport,
    finsearch_live: FinSearchLiveContractReport,
    release_evidence: ReleaseEvidenceReport,
) -> FailureTaxonomyReport:
    items = (
        _failure(
            "global-a0-a4-common-runs-missing",
            BlockerCategory.COMPARABILITY,
            EvidenceLayer.DETERMINISTIC,
            ("sec-release-evidence-v1",),
            release_evidence.common_case_count,
            "The common A0-A4 contract is frozen, but production Runtime runs are absent.",
        ),
        _failure(
            "retrieval-recall-at-5-missing",
            BlockerCategory.QUALITY,
            EvidenceLayer.DETERMINISTIC,
            ("sec-release-evidence-v1",),
            release_evidence.common_case_count,
            "Ranked retrieval candidates are absent, so Recall@5 cannot be computed.",
        ),
        _failure(
            "runtime-binding-missing",
            BlockerCategory.RUNTIME_EVIDENCE,
            EvidenceLayer.DETERMINISTIC,
            ("sec-release-evidence-v1", "sec-temporal-v1", "agent-security-v1"),
            release_evidence.expected_run_count
            + temporal.expanded_case_count
            + security.case_count,
            (
                "Cases are not bound to UnifiedAgentRuntime Run, Trace, Evidence, or "
                "database final state."
            ),
        ),
        _failure(
            "offline-predictions-missing",
            BlockerCategory.OFFLINE_EXECUTION,
            EvidenceLayer.OFFLINE,
            (
                "finqa-adapter-v1",
                "tatqa-adapter-v1",
                "financebench-adapter-v1",
                "finsearchcomp-historical-v1",
            ),
            1147 + 1663 + financebench.question_count + finsearch_historical.historical_case_count,
            "Adapters exist, but no fixed-provider A0-A4 predictions were executed.",
        ),
        _failure(
            "external-license-review-missing",
            BlockerCategory.GOVERNANCE,
            EvidenceLayer.OFFLINE,
            ("financebench-adapter-v1", "finsearchcomp-historical-v1"),
            financebench.question_count + finsearch_historical.historical_case_count,
            "Owner license review and FinanceBench source-document rights remain open.",
        ),
        _failure(
            "live-dependencies-not-executed",
            BlockerCategory.LIVE_EXECUTION,
            EvidenceLayer.LIVE,
            ("finsearchcomp-live-v1",),
            finsearch_live.dynamic_case_count,
            "Dynamic data, professional dependencies, and the official judge were not executed.",
        ),
        _failure(
            "live-repetitions-missing",
            BlockerCategory.LIVE_EXECUTION,
            EvidenceLayer.LIVE,
            ("finsearchcomp-live-v1", "sec-temporal-v1", "agent-security-v1"),
            finsearch_live.dynamic_case_count + temporal.expanded_case_count + security.case_count,
            "Fixed provider/model/version runs have zero of three required repetitions.",
        ),
        _failure(
            "language-owner-review-missing",
            BlockerCategory.GOVERNANCE,
            EvidenceLayer.DETERMINISTIC,
            ("sec-temporal-v1",),
            len(temporal.language_review_sample_pair_ids),
            "The frozen Chinese language sample has not been signed off.",
        ),
        _failure(
            "remote-ci-owner-closeout-missing",
            BlockerCategory.GOVERNANCE,
            EvidenceLayer.DETERMINISTIC,
            ("sec-tool-v1", "sec-verification-v1", "agent-security-v1"),
            0,
            "Branch, PR, main CI and final owner review are external evidence still pending.",
        ),
    )
    category_counts = Counter(item.category for item in items)
    return FailureTaxonomyReport(
        items=items,
        category_counts=dict(category_counts),
        release_blocking_count=sum(item.release_blocking for item in items),
        limitations=(
            "Counts describe cases affected by missing evidence and may overlap across blockers.",
            "No live runtime was executed, so observed runtime failure count remains zero.",
        ),
    )


def _validate_source_identities(
    *,
    temporal: SecTemporalValidationReport,
    finqa: AdapterValidationReport,
    tatqa: AdapterValidationReport,
    financebench: FinanceBenchAdapterReport,
    finsearch_historical: FinSearchHistoricalReport,
    finsearch_live: FinSearchLiveContractReport,
) -> None:
    identities = (
        (temporal.dataset_id, "sec-temporal-v1"),
        (finqa.dataset_id, "finqa"),
        (tatqa.dataset_id, "tat-qa"),
        (financebench.dataset_id, "financebench"),
        (finsearch_historical.dataset_id, "finsearchcomp"),
        (finsearch_live.dataset_id, "finsearchcomp"),
    )
    for actual, expected in identities:
        if actual != expected:
            raise ValueError(f"Release suite source identity changed: {actual}")
    if temporal.expanded_case_count != temporal.pair_count * 2:
        raise ValueError("SEC temporal bilingual case denominator is invalid")


def _load_model[ModelT: BaseModel](path: Path, model: type[ModelT]) -> ModelT:
    raw = path.read_text(encoding="utf-8")
    load_strict_json(path)
    return model.model_validate_json(raw, strict=True)


def _source_ref(
    path: Path,
    report_id: str,
    version: str,
    evidence_layer: str,
    case_count: int,
    run_count: int,
) -> SourceReportReference:
    return SourceReportReference(
        report_id=report_id,
        report_version=version,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        evidence_layer=evidence_layer,
        case_count=case_count,
        run_count=run_count,
    )


def _capability(metric_name: str, metric: _RatioMetric, unit: str, source: str) -> CapabilityMetric:
    numerator = metric.numerator
    denominator = metric.denominator
    value = metric.value
    return CapabilityMetric(
        metric_name=metric_name,
        status=MetricStatus.MEASURED_CONTRACT,
        value=value,
        numerator=numerator,
        denominator=denominator,
        unit=unit,
        source_report_id=source,
        limitation="Frozen deterministic observation; not a live model capability result.",
    )


def _release_evidence_capability(
    report: ReleaseEvidenceReport,
    metric_name: str,
) -> CapabilityMetric:
    metric = report.metrics[metric_name]
    if metric.status is ReleaseEvidenceMetricStatus.NOT_MEASURED:
        return CapabilityMetric(
            metric_name=metric_name,
            status=MetricStatus.NOT_MEASURED,
            unit="ratio",
            limitation=metric.limitation,
        )
    return CapabilityMetric(
        metric_name=metric_name,
        status=MetricStatus.MEASURED_CONTRACT,
        value=metric.value,
        numerator=metric.numerator,
        denominator=metric.denominator,
        unit="ratio",
        source_report_id=report.report_id,
        limitation="Production Run evidence at the report's declared evidence layer.",
    )


def _pairwise_decision(
    *,
    segment_id: str,
    source_report_id: str,
    baseline: str,
    candidate: str,
    cases: int,
    primary_metric: str,
    primary_gain: float,
    simple_degradation: float,
    cost: int,
    latency: int,
    source_gate_passed: bool,
    retained: Decision,
) -> PairwiseAblationDecision:
    blockers = (
        ()
        if source_gate_passed and primary_gain > 0 and simple_degradation <= 0.02
        else ("pairwise_gate_failed",)
    )
    return PairwiseAblationDecision(
        segment_id=segment_id,
        source_report_id=source_report_id,
        baseline_strategy=baseline,
        candidate_strategy=candidate,
        common_case_count=cases,
        same_manifest=True,
        same_data_scope_budget=True,
        primary_metric=primary_metric,
        primary_gain=primary_gain,
        simple_degradation=simple_degradation,
        cost_increase_micro_usd=cost,
        latency_increase_ms=latency,
        source_gate_passed=source_gate_passed,
        decision=retained if not blockers else Decision.ROLLBACK,
        blockers=blockers,
    )


def _offline_dataset(
    dataset_id: str, count: int, blockers: tuple[str, ...]
) -> OfflineDatasetResult:
    return OfflineDatasetResult(
        dataset_id=dataset_id,
        eligible_case_count=count,
        prediction_count=0,
        model_executed=False,
        blockers=blockers,
    )


def _split_count(report: AdapterValidationReport, split_name: str) -> int:
    matches = tuple(
        split.scorable_case_count for split in report.splits if split.split == split_name
    )
    if len(matches) != 1:
        raise ValueError(f"Adapter report split is missing or duplicated: {split_name}")
    return matches[0]


def _failure(
    failure_id: str,
    category: BlockerCategory,
    layer: EvidenceLayer,
    sources: tuple[str, ...],
    affected: int,
    detail: str,
) -> FailureTaxonomyItem:
    return FailureTaxonomyItem(
        failure_id=failure_id,
        category=category,
        evidence_layer=layer,
        source_report_ids=sources,
        affected_case_count=affected,
        release_blocking=True,
        detail=detail,
    )


def _write_json(path: Path, value: BaseModel | object) -> None:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_markdown(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build separated Day 9 release reports")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--release-manifest", type=Path, required=True)
    parser.add_argument("--sec-tool-report", type=Path, required=True)
    parser.add_argument("--verification-report", type=Path, required=True)
    parser.add_argument("--temporal-report", type=Path, required=True)
    parser.add_argument("--agent-security-report", type=Path, required=True)
    parser.add_argument("--release-evidence-report", type=Path, required=True)
    parser.add_argument("--finqa-report", type=Path, required=True)
    parser.add_argument("--tatqa-report", type=Path, required=True)
    parser.add_argument("--financebench-report", type=Path, required=True)
    parser.add_argument("--finsearch-historical-report", type=Path, required=True)
    parser.add_argument("--finsearch-live-report", type=Path, required=True)
    parser.add_argument("--report-directory", type=Path, required=True)
    parser.add_argument("--schema-output", type=Path, required=True)
    args = parser.parse_args(argv)
    sources = ReleaseSuiteSources(
        registry=args.registry,
        release_manifest=args.release_manifest,
        sec_tool=args.sec_tool_report,
        verification=args.verification_report,
        temporal=args.temporal_report,
        agent_security=args.agent_security_report,
        release_evidence=args.release_evidence_report,
        finqa=args.finqa_report,
        tatqa=args.tatqa_report,
        financebench=args.financebench_report,
        finsearch_historical=args.finsearch_historical_report,
        finsearch_live=args.finsearch_live_report,
    )
    bundle = build_release_suite(sources)
    write_release_suite(args.report_directory, args.schema_output, bundle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
