"""Validate release recovery exercises without inferring unexecuted success."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Final, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from industry_platform.modules.agent_runtime.domain import require_utc
from industry_platform.modules.evaluation.release import load_strict_json

RECOVERY_MANIFEST_ID: Final = "sec-release-recovery-v1"
RECOVERY_REPORT_ID: Final = "sec-release-recovery-v1"
RECOVERY_SCORER_VERSION: Final = "sec-release-recovery-scorer-v1"
RECOVERY_SCHEMA_VERSION: Final = 1

_SHA256_PATTERN: Final = r"^[a-f0-9]{64}$"
_COMMIT_PATTERN: Final = r"^[a-f0-9]{40}$"
_SCENARIO_IDS: Final = (
    "fresh-migration",
    "postgres-backup-restore",
    "filing-index-rebuild",
    "worker-interruption-resume",
    "redis-outage-recovery",
    "minio-outage-recovery",
    "elasticsearch-outage-rebuild",
    "milvus-outage-rebuild",
    "sec-429-backoff",
    "dead-letter-replay",
    "notification-unknown-idempotency",
    "previous-image-rollback",
)


class RecoveryExecutionStatus(StrEnum):
    NOT_EXECUTED = "not_executed"
    EXECUTED = "executed"


class RecoveryAutomation(StrEnum):
    PYTEST = "pytest"
    RUNBOOK = "runbook"


class RecoveryMetricStatus(StrEnum):
    MEASURED = "measured"
    NOT_MEASURED = "not_measured"


class RecoveryAlertStatus(StrEnum):
    CLEAR = "clear"
    FIRING = "firing"
    UNKNOWN = "unknown"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RecoveryScenarioContract(_FrozenModel):
    scenario_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    fault_target: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    automation: RecoveryAutomation
    runtime_binding_required: bool
    required_services: tuple[str, ...]
    verification_ref: str = Field(min_length=1)
    expected_outcome: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_contract(self) -> Self:
        if (
            not self.required_services
            or len(self.required_services) != len(set(self.required_services))
            or any(
                re.fullmatch(r"[a-z0-9][a-z0-9-]*", item) is None for item in self.required_services
            )
        ):
            raise ValueError("Recovery required services are invalid")
        if "\n" in self.verification_ref or not self.verification_ref.strip():
            raise ValueError("Recovery verification reference must be one auditable reference")
        if self.automation is RecoveryAutomation.PYTEST and not self.verification_ref.startswith(
            "uv run --locked --all-packages pytest "
        ):
            raise ValueError("Automated recovery verification must use locked pytest")
        if self.automation is RecoveryAutomation.RUNBOOK and not self.verification_ref.startswith(
            "docs/runbooks/day-10-release-recovery.md#"
        ):
            raise ValueError("Manual recovery verification must reference the release runbook")
        return self


class RecoveryManifest(_FrozenModel):
    schema_version: Literal[1] = RECOVERY_SCHEMA_VERSION
    manifest_id: Literal["sec-release-recovery-v1"] = RECOVERY_MANIFEST_ID
    manifest_version: Literal["v1"] = "v1"
    scorer_version: Literal["sec-release-recovery-scorer-v1"] = RECOVERY_SCORER_VERSION
    scenarios: tuple[RecoveryScenarioContract, ...]
    recovery_success_threshold: float = Field(default=1.0, ge=1.0, le=1.0)
    zero_side_effect_threshold: float = Field(default=1.0, ge=1.0, le=1.0)
    zero_data_loss_threshold: float = Field(default=1.0, ge=1.0, le=1.0)
    zero_unauthorized_write_threshold: float = Field(default=1.0, ge=1.0, le=1.0)

    @model_validator(mode="after")
    def _validate_manifest(self) -> Self:
        if tuple(item.scenario_id for item in self.scenarios) != _SCENARIO_IDS:
            raise ValueError("Recovery scenarios changed or are out of order")
        references = tuple(item.verification_ref for item in self.scenarios)
        if len(references) != len(set(references)):
            raise ValueError("Recovery verification references must be unique")
        return self


class RecoveryObservation(_FrozenModel):
    scenario_id: str
    exercise_id: UUID
    run_id: UUID | None = None
    case_id: UUID | None = None
    workspace_id: UUID | None = None
    started_at: datetime
    completed_at: datetime
    start_state_sha256: str = Field(pattern=_SHA256_PATTERN)
    final_state_sha256: str = Field(pattern=_SHA256_PATTERN)
    recovery_command_sha256: str = Field(pattern=_SHA256_PATTERN)
    evidence_path: str = Field(min_length=1)
    evidence_sha256: str = Field(pattern=_SHA256_PATTERN)
    recovery_succeeded: bool
    duplicate_side_effect_count: int = Field(ge=0)
    data_loss_count: int = Field(ge=0)
    unauthorized_write_count: int = Field(ge=0)
    duration_ms: int = Field(ge=0)

    @field_validator("evidence_path")
    @classmethod
    def _validate_evidence_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
            raise ValueError("Recovery evidence path must be repository-relative POSIX")
        return value

    @model_validator(mode="after")
    def _validate_observation(self) -> Self:
        if self.exercise_id.int == 0:
            raise ValueError("Recovery exercise ID cannot be nil")
        for identifier in (self.run_id, self.case_id, self.workspace_id):
            if identifier is not None and identifier.int == 0:
                raise ValueError("Recovery runtime binding cannot contain nil UUIDs")
        require_utc(self.started_at, field_name="Recovery exercise start time")
        require_utc(self.completed_at, field_name="Recovery exercise completion time")
        if self.completed_at < self.started_at:
            raise ValueError("Recovery exercise completion cannot precede start")
        elapsed_ms = round((self.completed_at - self.started_at).total_seconds() * 1_000)
        if self.duration_ms != elapsed_ms:
            raise ValueError("Recovery duration is inconsistent")
        return self


class RecoveryObservationSet(_FrozenModel):
    schema_version: Literal[1] = RECOVERY_SCHEMA_VERSION
    manifest_id: Literal["sec-release-recovery-v1"] = RECOVERY_MANIFEST_ID
    manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    execution_status: RecoveryExecutionStatus
    source_commit: str | None = Field(default=None, pattern=_COMMIT_PATTERN)
    environment: str | None = Field(default=None, min_length=1)
    observations: tuple[RecoveryObservation, ...]
    limitations: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_execution_boundary(self) -> Self:
        identity = (self.source_commit, self.environment)
        if self.execution_status is RecoveryExecutionStatus.NOT_EXECUTED:
            if self.observations or any(item is not None for item in identity):
                raise ValueError("Unexecuted recovery evidence cannot claim exercises or identity")
        elif not self.observations or any(item is None for item in identity):
            raise ValueError(
                "Executed recovery evidence requires exercises and environment identity"
            )
        if not self.limitations or any(not item.strip() for item in self.limitations):
            raise ValueError("Recovery evidence limitations are required")
        return self


class RecoveryMetric(_FrozenModel):
    metric_name: str
    status: RecoveryMetricStatus
    numerator: int | None = Field(default=None, ge=0)
    denominator: int | None = Field(default=None, ge=1)
    value: float | None = Field(default=None, ge=0, le=1)
    threshold: float = Field(default=1.0, ge=1.0, le=1.0)
    gate_passed: bool | None
    limitation: str | None = None

    @model_validator(mode="after")
    def _validate_metric(self) -> Self:
        measured = self.status is RecoveryMetricStatus.MEASURED
        values = (self.numerator, self.denominator, self.value)
        if measured != all(item is not None for item in values):
            raise ValueError("Measured recovery metric requires a ratio")
        if measured:
            numerator = self.numerator
            denominator = self.denominator
            value = self.value
            if numerator is None or denominator is None or value is None:
                raise ValueError("Measured recovery metric requires a ratio")
            if value != round(numerator / denominator, 6):
                raise ValueError("Recovery metric ratio is inconsistent")
            if self.gate_passed != (value >= self.threshold) or self.limitation is not None:
                raise ValueError("Measured recovery gate is inconsistent")
        elif self.gate_passed is not None or not self.limitation:
            raise ValueError("Unmeasured recovery metric requires a limitation")
        return self


class RecoveryAlert(_FrozenModel):
    alert_id: str
    metric_name: str
    status: RecoveryAlertStatus
    detail: str


class RecoveryReport(_FrozenModel):
    schema_version: Literal[1] = RECOVERY_SCHEMA_VERSION
    report_id: Literal["sec-release-recovery-v1"] = RECOVERY_REPORT_ID
    report_version: Literal["v1"] = "v1"
    scorer_version: Literal["sec-release-recovery-scorer-v1"] = RECOVERY_SCORER_VERSION
    manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    execution_status: RecoveryExecutionStatus
    expected_scenario_count: Literal[12] = 12
    observed_scenario_count: int = Field(ge=0)
    metrics: Mapping[str, RecoveryMetric]
    alerts: tuple[RecoveryAlert, ...]
    recovery_gate_passed: bool
    release_ready: bool = False
    blockers: tuple[str, ...]
    limitations: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_report(self) -> Self:
        if self.release_ready or not self.blockers:
            raise ValueError("Recovery evidence cannot independently make the release ready")
        if (
            self.execution_status is RecoveryExecutionStatus.NOT_EXECUTED
            and self.observed_scenario_count
        ):
            raise ValueError("Unexecuted recovery report cannot claim observations")
        if len({item.alert_id for item in self.alerts}) != len(self.alerts):
            raise ValueError("Recovery alert ids must be unique")
        return self


def load_recovery_manifest(path: Path) -> RecoveryManifest:
    return _load_model(path, RecoveryManifest)


def load_recovery_observations(path: Path) -> RecoveryObservationSet:
    return _load_model(path, RecoveryObservationSet)


def load_recovery_report(path: Path) -> RecoveryReport:
    return _load_model(path, RecoveryReport)


def build_recovery_report(
    manifest: RecoveryManifest,
    observations: RecoveryObservationSet,
    *,
    root: Path,
) -> RecoveryReport:
    manifest_sha256 = _canonical_sha256(manifest)
    if observations.manifest_sha256 != manifest_sha256:
        raise ValueError("Recovery observations do not match the manifest")
    contracts = {item.scenario_id: item for item in manifest.scenarios}
    observed = {item.scenario_id: item for item in observations.observations}
    if len(observed) != len(observations.observations):
        raise ValueError("Recovery scenario observations must be unique")
    if observations.execution_status is RecoveryExecutionStatus.EXECUTED and set(observed) != set(
        contracts
    ):
        raise ValueError("Executed recovery evidence must cover every frozen scenario")
    for item in observations.observations:
        contract = contracts.get(item.scenario_id)
        if contract is None:
            raise ValueError("Recovery observation references an unknown scenario")
        if contract.runtime_binding_required and (item.run_id is None or item.workspace_id is None):
            raise ValueError("Recovery scenario requires Run and Workspace binding")
        evidence = (root / Path(item.evidence_path)).resolve()
        if not evidence.is_relative_to(root.resolve()) or not evidence.is_file():
            raise ValueError("Recovery evidence artifact is unavailable")
        if hashlib.sha256(evidence.read_bytes()).hexdigest() != item.evidence_sha256:
            raise ValueError("Recovery evidence artifact checksum changed")

    metrics = _score(tuple(observations.observations))
    alerts = tuple(_alert(metric) for metric in metrics.values())
    blockers = [
        f"{name}_{'not_measured' if metric.gate_passed is None else 'failed'}"
        for name, metric in metrics.items()
        if metric.gate_passed is not True
    ]
    previous_image = observed.get("previous-image-rollback")
    if previous_image is None or not previous_image.recovery_succeeded:
        blockers.append("previous_image_release_artifact_not_verified")
    blockers.append("remote_ci_not_verified")
    return RecoveryReport(
        manifest_sha256=manifest_sha256,
        execution_status=observations.execution_status,
        observed_scenario_count=len(observations.observations),
        metrics=metrics,
        alerts=alerts,
        recovery_gate_passed=all(metric.gate_passed is True for metric in metrics.values()),
        blockers=tuple(blockers),
        limitations=observations.limitations,
    )


def write_recovery_report(
    report: RecoveryReport,
    *,
    json_output: Path,
    markdown_output: Path,
    schema_output: Path,
) -> None:
    for path in (json_output, markdown_output, schema_output):
        path.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    markdown_output.write_text(render_recovery_markdown(report), encoding="utf-8")
    schema_output.write_text(
        json.dumps(
            RecoveryReport.model_json_schema(mode="validation"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def render_recovery_markdown(report: RecoveryReport) -> str:
    lines = [
        "# SEC release recovery evidence",
        "",
        f"- Execution: `{report.execution_status.value}`",
        f"- Scenarios: {report.observed_scenario_count}/{report.expected_scenario_count}",
        f"- Recovery gate: `{str(report.recovery_gate_passed).lower()}`",
        "- Release ready: `false`",
        "",
        "## Metrics",
        "",
        "| Metric | Status | Value | Threshold | Gate |",
        "|---|---|---:|---:|---|",
    ]
    for metric in report.metrics.values():
        value = "null" if metric.value is None else f"{metric.value:.6f}"
        gate = "unknown" if metric.gate_passed is None else str(metric.gate_passed).lower()
        lines.append(
            f"| `{metric.metric_name}` | `{metric.status.value}` | {value} | "
            f">= {metric.threshold:.2f} | `{gate}` |"
        )
    lines.extend(["", "## Alerts", ""])
    lines.extend(
        f"- `{item.alert_id}`: `{item.status.value}` - {item.detail}" for item in report.alerts
    )
    lines.extend(["", "## Blockers", ""])
    lines.extend(f"- `{item}`" for item in report.blockers)
    lines.extend(
        [
            "",
            "Unit tests and recovery contracts are not exercise observations. Missing backup, "
            "dependency-fault, and previous-image evidence remains unknown and release-blocking.",
        ]
    )
    return "\n".join(lines) + "\n"


def _score(observations: tuple[RecoveryObservation, ...]) -> dict[str, RecoveryMetric]:
    names = (
        "recovery_success",
        "zero_duplicate_side_effect_rate",
        "zero_data_loss_rate",
        "zero_unauthorized_write_rate",
    )
    if not observations:
        return {name: _unmeasured(name) for name in names}
    denominator = len(observations)
    numerators = {
        "recovery_success": sum(item.recovery_succeeded for item in observations),
        "zero_duplicate_side_effect_rate": sum(
            item.duplicate_side_effect_count == 0 for item in observations
        ),
        "zero_data_loss_rate": sum(item.data_loss_count == 0 for item in observations),
        "zero_unauthorized_write_rate": sum(
            item.unauthorized_write_count == 0 for item in observations
        ),
    }
    return {name: _measured(name, numerator, denominator) for name, numerator in numerators.items()}


def _measured(name: str, numerator: int, denominator: int) -> RecoveryMetric:
    value = round(numerator / denominator, 6)
    return RecoveryMetric(
        metric_name=name,
        status=RecoveryMetricStatus.MEASURED,
        numerator=numerator,
        denominator=denominator,
        value=value,
        gate_passed=value >= 1,
    )


def _unmeasured(name: str) -> RecoveryMetric:
    return RecoveryMetric(
        metric_name=name,
        status=RecoveryMetricStatus.NOT_MEASURED,
        gate_passed=None,
        limitation="No eligible release recovery exercise evidence was provided.",
    )


def _alert(metric: RecoveryMetric) -> RecoveryAlert:
    if metric.gate_passed is None:
        status = RecoveryAlertStatus.UNKNOWN
        detail = "Metric has no eligible recovery exercise evidence."
    elif metric.gate_passed:
        status = RecoveryAlertStatus.CLEAR
        detail = "Measured recovery result satisfies the frozen threshold."
    else:
        status = RecoveryAlertStatus.FIRING
        detail = "Measured recovery result violates the frozen threshold."
    return RecoveryAlert(
        alert_id=f"sec-release-{metric.metric_name.replace('_', '-')}",
        metric_name=metric.metric_name,
        status=status,
        detail=detail,
    )


def _canonical_sha256(model: BaseModel) -> str:
    payload = json.dumps(
        model.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_model[ModelT: BaseModel](path: Path, model: type[ModelT]) -> ModelT:
    raw = path.read_text(encoding="utf-8")
    load_strict_json(path)
    return model.model_validate_json(raw, strict=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    parser.add_argument("--schema-output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = build_recovery_report(
        load_recovery_manifest(args.manifest),
        load_recovery_observations(args.observations),
        root=args.root,
    )
    write_recovery_report(
        report,
        json_output=args.json_output,
        markdown_output=args.markdown_output,
        schema_output=args.schema_output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
