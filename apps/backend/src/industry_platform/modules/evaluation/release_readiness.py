"""Build a fail-closed Day 10 release-readiness ledger from repository evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from industry_platform.modules.evaluation.release import load_strict_json
from industry_platform.modules.evaluation.release_suite import (
    FailureTaxonomyReport,
    load_failure_taxonomy_report,
)

READINESS_MANIFEST_ID: Final = "sec-release-readiness-manifest-v1"
READINESS_REPORT_ID: Final = "sec-release-readiness-v1"
READINESS_VERSION: Final = "v1"
READINESS_SCHEMA_VERSION: Final = 1

_IDENTIFIER_PATTERN: Final = r"^[a-z0-9][a-z0-9._-]*$"
_REQUIREMENT_PATTERN: Final = r"^D(?:10|[1-9])-[0-9]{2}$"
_SHA256_PATTERN: Final = r"^[a-f0-9]{64}$"
_COMMIT_PATTERN: Final = r"^[a-f0-9]{40}$"
_TARGET_HEADER: Final = (
    "ID",
    "目标能力与用户结果",
    "来源",
    "冻结范围",
    "验收证据",
    "当前状态",
    "Day 10",
)
_REQUIREMENT_RE: Final = re.compile(_REQUIREMENT_PATTERN)


class RequirementStatus(StrEnum):
    COMPLETE = "complete"
    IMPLEMENTED_PENDING_VERIFICATION = "implemented_pending_verification"
    THIN_SLICE = "thin_slice"
    CONTRACT_ONLY = "contract_only"
    BLOCKED = "blocked"
    PLANNED = "planned"


class ExternalGateStatus(StrEnum):
    VERIFIED = "verified"
    PENDING = "pending"


class BlockerStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class BlockerSource(StrEnum):
    EVALUATION_TAXONOMY = "evaluation_taxonomy"
    CROSS_DAY_AUDIT = "cross_day_audit"


class ReleaseDecision(StrEnum):
    NO_GO = "no_go"
    RC_READY = "rc_ready"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _require_unique(values: Sequence[object], *, field_name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must be unique")


def _validate_relative_path(value: str) -> str:
    path = Path(value)
    if path.is_absolute() or "\\" in value or ".." in path.parts or not value.strip():
        raise ValueError("Evidence path must be a safe relative POSIX path")
    return value


def _validate_https(value: str, *, field_name: str) -> None:
    if not value.startswith("https://") or any(character.isspace() for character in value):
        raise ValueError(f"{field_name} must be an absolute HTTPS URL")


class MatrixRequirement(_FrozenModel):
    requirement_id: str = Field(pattern=_REQUIREMENT_PATTERN)
    title: str = Field(min_length=1)
    source: str = Field(min_length=1)
    frozen_scope: str = Field(min_length=1)
    acceptance_evidence: str = Field(min_length=1)
    current_status: RequirementStatus
    target_status: Literal["complete"] = "complete"

    @property
    def day(self) -> int:
        return int(self.requirement_id[1:].split("-", maxsplit=1)[0])


class EvidenceArtifactSpec(_FrozenModel):
    artifact_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    relative_path: str
    description: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_path(self) -> Self:
        _validate_relative_path(self.relative_path)
        return self


class RequirementGroupSpec(_FrozenModel):
    day: int = Field(ge=1, le=10)
    owners: tuple[str, ...]
    dependency_days: tuple[int, ...]
    evidence_artifact_ids: tuple[str, ...]
    verification_commands: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_group(self) -> Self:
        if not self.owners or any(not owner.strip() for owner in self.owners):
            raise ValueError("Requirement group owners must be non-empty")
        if not self.evidence_artifact_ids or not self.verification_commands:
            raise ValueError("Requirement group requires artifacts and verification commands")
        if any(not command.strip() for command in self.verification_commands):
            raise ValueError("Requirement group commands must be non-empty")
        if any(day >= self.day or day < 1 for day in self.dependency_days):
            raise ValueError("Requirement group dependencies must reference earlier days")
        _require_unique(self.owners, field_name="Requirement group owners")
        _require_unique(self.dependency_days, field_name="Requirement group dependency days")
        _require_unique(
            self.evidence_artifact_ids,
            field_name="Requirement group evidence artifacts",
        )
        _require_unique(
            self.verification_commands,
            field_name="Requirement group verification commands",
        )
        return self


class ExternalGateSpec(_FrozenModel):
    gate_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    status: ExternalGateStatus
    owner: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    evidence_url: str | None = None
    source_commit: str | None = Field(default=None, pattern=_COMMIT_PATTERN)
    evidence_artifact_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_gate(self) -> Self:
        if self.evidence_url is not None:
            _validate_https(self.evidence_url, field_name="External gate evidence URL")
        if self.status is ExternalGateStatus.VERIFIED and (
            self.evidence_url is None and not self.evidence_artifact_ids
        ):
            raise ValueError("Verified external gate requires evidence")
        if self.status is ExternalGateStatus.PENDING and (
            self.evidence_url is not None or self.source_commit is not None
        ):
            raise ValueError("Pending external gate cannot claim URL or commit evidence")
        _require_unique(
            self.evidence_artifact_ids,
            field_name="External gate evidence artifacts",
        )
        return self


class BlockerBindingSpec(_FrozenModel):
    blocker_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    source: BlockerSource
    status: BlockerStatus
    owner: str = Field(min_length=1)
    requirement_ids: tuple[str, ...]
    external_gate_ids: tuple[str, ...] = ()
    closure_artifact_ids: tuple[str, ...] = ()
    detail: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_binding(self) -> Self:
        if not self.requirement_ids:
            raise ValueError("Blocker binding requires affected requirements")
        if any(not _REQUIREMENT_RE.fullmatch(value) for value in self.requirement_ids):
            raise ValueError("Blocker binding contains an invalid requirement id")
        if self.status is BlockerStatus.CLOSED and not self.closure_artifact_ids:
            raise ValueError("Closed blocker requires closure artifacts")
        if self.status is BlockerStatus.OPEN and self.closure_artifact_ids:
            raise ValueError("Open blocker cannot claim closure artifacts")
        _require_unique(self.requirement_ids, field_name="Blocker requirement ids")
        _require_unique(self.external_gate_ids, field_name="Blocker external gate ids")
        _require_unique(self.closure_artifact_ids, field_name="Blocker closure artifacts")
        return self


class ReleaseReadinessManifest(_FrozenModel):
    schema_version: Literal[1] = READINESS_SCHEMA_VERSION
    manifest_id: Literal["sec-release-readiness-manifest-v1"] = READINESS_MANIFEST_ID
    manifest_version: Literal["v1"] = READINESS_VERSION
    baseline_commit: str = Field(pattern=_COMMIT_PATTERN)
    matrix_relative_path: str
    failure_taxonomy_relative_path: str
    expected_requirement_count: int = Field(ge=1)
    expected_requirement_digest: str = Field(pattern=_SHA256_PATTERN)
    expected_current_status_counts: Mapping[RequirementStatus, int]
    artifacts: tuple[EvidenceArtifactSpec, ...]
    requirement_groups: tuple[RequirementGroupSpec, ...]
    external_gates: tuple[ExternalGateSpec, ...]
    blockers: tuple[BlockerBindingSpec, ...]
    limitations: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_manifest(self) -> Self:
        _validate_relative_path(self.matrix_relative_path)
        _validate_relative_path(self.failure_taxonomy_relative_path)
        if set(self.expected_current_status_counts) != set(RequirementStatus):
            raise ValueError("Expected status counts must include every requirement status")
        if sum(self.expected_current_status_counts.values()) != self.expected_requirement_count:
            raise ValueError("Expected status counts do not match requirement count")
        if any(value < 0 for value in self.expected_current_status_counts.values()):
            raise ValueError("Expected status counts cannot be negative")
        if {group.day for group in self.requirement_groups} != set(range(1, 11)):
            raise ValueError("Requirement groups must cover Day 1 through Day 10 exactly")
        if not self.artifacts or not self.blockers or not self.limitations:
            raise ValueError("Readiness manifest requires artifacts, blockers, and limitations")
        _require_unique(
            tuple(artifact.artifact_id for artifact in self.artifacts),
            field_name="Evidence artifact ids",
        )
        _require_unique(
            tuple(artifact.relative_path for artifact in self.artifacts),
            field_name="Evidence artifact paths",
        )
        _require_unique(
            tuple(gate.gate_id for gate in self.external_gates),
            field_name="External gate ids",
        )
        _require_unique(
            tuple(blocker.blocker_id for blocker in self.blockers),
            field_name="Blocker ids",
        )
        _require_unique(self.limitations, field_name="Readiness limitations")
        return self


class ArtifactSnapshot(_FrozenModel):
    artifact_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    relative_path: str
    sha256: str = Field(pattern=_SHA256_PATTERN)
    byte_size: int = Field(gt=0)


class RequirementReadiness(_FrozenModel):
    requirement_id: str = Field(pattern=_REQUIREMENT_PATTERN)
    title: str
    day: int = Field(ge=1, le=10)
    current_status: RequirementStatus
    target_status: Literal["complete"] = "complete"
    owners: tuple[str, ...]
    dependency_days: tuple[int, ...]
    evidence_artifact_ids: tuple[str, ...]
    verification_commands: tuple[str, ...]


class ReadinessBlocker(_FrozenModel):
    blocker_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    source: BlockerSource
    status: BlockerStatus
    owner: str
    requirement_ids: tuple[str, ...]
    external_gate_ids: tuple[str, ...]
    closure_artifact_ids: tuple[str, ...]
    detail: str
    source_detail: str | None = None


class ReleaseReadinessReport(_FrozenModel):
    schema_version: Literal[1] = READINESS_SCHEMA_VERSION
    report_id: Literal["sec-release-readiness-v1"] = READINESS_REPORT_ID
    report_version: Literal["v1"] = READINESS_VERSION
    baseline_commit: str = Field(pattern=_COMMIT_PATTERN)
    manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    matrix_sha256: str = Field(pattern=_SHA256_PATTERN)
    requirement_digest: str = Field(pattern=_SHA256_PATTERN)
    artifacts: tuple[ArtifactSnapshot, ...]
    requirements: tuple[RequirementReadiness, ...]
    status_counts: Mapping[RequirementStatus, int]
    incomplete_requirement_count: int = Field(ge=0)
    external_gates: tuple[ExternalGateSpec, ...]
    pending_external_gate_count: int = Field(ge=0)
    blockers: tuple[ReadinessBlocker, ...]
    release_blocker_count: int = Field(ge=0)
    release_decision: ReleaseDecision
    rc_ready: bool
    limitations: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_reconciliation(self) -> Self:
        _require_unique(
            tuple(artifact.artifact_id for artifact in self.artifacts),
            field_name="Readiness report artifact ids",
        )
        _require_unique(
            tuple(item.requirement_id for item in self.requirements),
            field_name="Readiness report requirement ids",
        )
        _require_unique(
            tuple(gate.gate_id for gate in self.external_gates),
            field_name="Readiness report external gate ids",
        )
        _require_unique(
            tuple(blocker.blocker_id for blocker in self.blockers),
            field_name="Readiness report blocker ids",
        )
        counted_statuses = Counter(item.current_status for item in self.requirements)
        expected_status_counts = {status: counted_statuses[status] for status in RequirementStatus}
        if expected_status_counts != dict(self.status_counts):
            raise ValueError("Readiness requirement status counts do not reconcile")
        expected_incomplete = sum(
            item.current_status is not RequirementStatus.COMPLETE for item in self.requirements
        )
        if expected_incomplete != self.incomplete_requirement_count:
            raise ValueError("Readiness incomplete requirement count does not reconcile")
        expected_pending = sum(
            gate.status is ExternalGateStatus.PENDING for gate in self.external_gates
        )
        if expected_pending != self.pending_external_gate_count:
            raise ValueError("Readiness pending external gate count does not reconcile")
        expected_blockers = sum(blocker.status is BlockerStatus.OPEN for blocker in self.blockers)
        if expected_blockers != self.release_blocker_count:
            raise ValueError("Readiness blocker count does not reconcile")
        expected_ready = expected_incomplete == expected_pending == expected_blockers == 0
        if self.rc_ready != expected_ready:
            raise ValueError("Readiness decision does not match its evidence counts")
        expected_decision = ReleaseDecision.RC_READY if expected_ready else ReleaseDecision.NO_GO
        if self.release_decision is not expected_decision:
            raise ValueError("Readiness release decision does not reconcile")
        return self


def parse_feature_matrix(path: Path) -> tuple[MatrixRequirement, ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"Unable to read feature matrix: {path}") from exc

    requirements: list[MatrixRequirement] = []
    in_target_table = False
    header_count = 0
    for line in lines:
        cells = _markdown_cells(line)
        if cells == _TARGET_HEADER:
            in_target_table = True
            header_count += 1
            continue
        if not in_target_table:
            continue
        if cells is None:
            in_target_table = False
            continue
        if _is_separator_row(cells):
            continue
        if len(cells) != len(_TARGET_HEADER) or not _REQUIREMENT_RE.fullmatch(cells[0]):
            in_target_table = False
            continue
        try:
            current_status = RequirementStatus(_strip_code(cells[5]))
        except ValueError as exc:
            raise ValueError(f"Unknown matrix status for {cells[0]}: {cells[5]}") from exc
        target_status = _strip_code(cells[6])
        if target_status != "complete":
            raise ValueError(f"Unexpected Day 10 target status for {cells[0]}: {cells[6]}")
        requirements.append(
            MatrixRequirement(
                requirement_id=cells[0],
                title=cells[1],
                source=cells[2],
                frozen_scope=cells[3],
                acceptance_evidence=cells[4],
                current_status=current_status,
                target_status="complete",
            )
        )

    if header_count != 10:
        raise ValueError("Feature matrix must contain exactly ten target capability tables")
    _require_unique(
        tuple(requirement.requirement_id for requirement in requirements),
        field_name="Feature matrix requirement ids",
    )
    if {requirement.day for requirement in requirements} != set(range(1, 11)):
        raise ValueError("Feature matrix requirements must cover Day 1 through Day 10")
    return tuple(sorted(requirements, key=_requirement_sort_key))


def requirement_digest(requirements: Sequence[MatrixRequirement]) -> str:
    payload = [requirement.model_dump(mode="json") for requirement in requirements]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_release_readiness_manifest(path: Path) -> ReleaseReadinessManifest:
    return ReleaseReadinessManifest.model_validate(load_strict_json(path))


def load_release_readiness_report(path: Path) -> ReleaseReadinessReport:
    return ReleaseReadinessReport.model_validate(load_strict_json(path))


def build_release_readiness(*, root: Path, manifest_path: Path) -> ReleaseReadinessReport:
    resolved_root = root.resolve(strict=True)
    resolved_manifest = _resolve_evidence_path(resolved_root, manifest_path)
    manifest = load_release_readiness_manifest(resolved_manifest)
    matrix_path = _resolve_relative_evidence(resolved_root, manifest.matrix_relative_path)
    failure_path = _resolve_relative_evidence(
        resolved_root,
        manifest.failure_taxonomy_relative_path,
    )
    requirements = parse_feature_matrix(matrix_path)
    digest = requirement_digest(requirements)
    status_counts = _status_counts(requirements)
    if len(requirements) != manifest.expected_requirement_count:
        raise ValueError("Feature matrix requirement count changed")
    if digest != manifest.expected_requirement_digest:
        raise ValueError("Feature matrix requirement scope or status changed")
    if status_counts != dict(manifest.expected_current_status_counts):
        raise ValueError("Feature matrix status counts changed")

    artifact_snapshots = tuple(
        _snapshot_artifact(resolved_root, artifact) for artifact in manifest.artifacts
    )
    artifact_ids = {artifact.artifact_id for artifact in artifact_snapshots}
    matrix_specs = [
        artifact
        for artifact in manifest.artifacts
        if artifact.relative_path == manifest.matrix_relative_path
    ]
    failure_specs = [
        artifact
        for artifact in manifest.artifacts
        if artifact.relative_path == manifest.failure_taxonomy_relative_path
    ]
    if len(matrix_specs) != 1 or len(failure_specs) != 1:
        raise ValueError("Matrix and failure taxonomy must each be registered as one artifact")

    groups = {group.day: group for group in manifest.requirement_groups}
    for group in groups.values():
        _require_known_ids(
            group.evidence_artifact_ids,
            artifact_ids,
            field_name=f"Day {group.day} evidence artifact",
        )
    gate_ids = {gate.gate_id for gate in manifest.external_gates}
    for gate in manifest.external_gates:
        _require_known_ids(
            gate.evidence_artifact_ids,
            artifact_ids,
            field_name=f"External gate {gate.gate_id} artifact",
        )

    requirement_records = tuple(
        RequirementReadiness(
            requirement_id=requirement.requirement_id,
            title=requirement.title,
            day=requirement.day,
            current_status=requirement.current_status,
            owners=groups[requirement.day].owners,
            dependency_days=groups[requirement.day].dependency_days,
            evidence_artifact_ids=groups[requirement.day].evidence_artifact_ids,
            verification_commands=groups[requirement.day].verification_commands,
        )
        for requirement in requirements
    )
    taxonomy = load_failure_taxonomy_report(failure_path)
    blockers = _build_blockers(
        manifest=manifest,
        taxonomy=taxonomy,
        requirements=requirement_records,
        artifact_ids=artifact_ids,
        gate_ids=gate_ids,
    )
    incomplete_count = sum(
        requirement.current_status is not RequirementStatus.COMPLETE
        for requirement in requirement_records
    )
    pending_gate_count = sum(
        gate.status is ExternalGateStatus.PENDING for gate in manifest.external_gates
    )
    open_blocker_count = sum(blocker.status is BlockerStatus.OPEN for blocker in blockers)
    ready = incomplete_count == pending_gate_count == open_blocker_count == 0
    return ReleaseReadinessReport(
        baseline_commit=manifest.baseline_commit,
        manifest_sha256=_sha256(resolved_manifest),
        matrix_sha256=_sha256(matrix_path),
        requirement_digest=digest,
        artifacts=artifact_snapshots,
        requirements=requirement_records,
        status_counts=status_counts,
        incomplete_requirement_count=incomplete_count,
        external_gates=manifest.external_gates,
        pending_external_gate_count=pending_gate_count,
        blockers=blockers,
        release_blocker_count=open_blocker_count,
        release_decision=ReleaseDecision.RC_READY if ready else ReleaseDecision.NO_GO,
        rc_ready=ready,
        limitations=manifest.limitations,
    )


def render_release_readiness_markdown(report: ReleaseReadinessReport) -> str:
    lines = [
        "# SEC release readiness report",
        "",
        f"- Decision: `{report.release_decision.value}`",
        f"- RC ready: `{str(report.rc_ready).lower()}`",
        f"- Audited baseline commit: `{report.baseline_commit}`",
        f"- Requirements: {len(report.requirements)}",
        f"- Incomplete requirements: {report.incomplete_requirement_count}",
        f"- Open release blockers: {report.release_blocker_count}",
        f"- Pending external gates: {report.pending_external_gate_count}",
        "",
        "## Requirement status",
        "",
        "| Status | Count |",
        "|---|---:|",
    ]
    lines.extend(
        f"| `{status.value}` | {report.status_counts[status]} |" for status in RequirementStatus
    )
    lines.extend(
        [
            "",
            "## Open blockers",
            "",
            "| Blocker | Source | Owner | Requirements | External gates |",
            "|---|---|---|---:|---:|",
        ]
    )
    lines.extend(
        f"| `{blocker.blocker_id}` | `{blocker.source.value}` | {blocker.owner} | "
        f"{len(blocker.requirement_ids)} | {len(blocker.external_gate_ids)} |"
        for blocker in report.blockers
        if blocker.status is BlockerStatus.OPEN
    )
    lines.extend(
        [
            "",
            "## External gates",
            "",
            "| Gate | Status | Owner | Evidence |",
            "|---|---|---|---|",
        ]
    )
    for gate in report.external_gates:
        evidence = gate.evidence_url or ", ".join(
            f"`{item}`" for item in gate.evidence_artifact_ids
        )
        lines.append(
            f"| `{gate.gate_id}` | `{gate.status.value}` | {gate.owner} | {evidence or '-'} |"
        )
    lines.extend(
        [
            "",
            "A checked artifact or historical `complete` status is not promoted to release "
            "readiness while any requirement, blocker, or external gate remains open.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_release_readiness(
    *,
    report: ReleaseReadinessReport,
    json_output: Path,
    markdown_output: Path,
    manifest_schema_output: Path,
    report_schema_output: Path,
) -> None:
    _write_json(json_output, report)
    _write_markdown(markdown_output, render_release_readiness_markdown(report))
    _write_json(
        manifest_schema_output,
        ReleaseReadinessManifest.model_json_schema(mode="validation"),
    )
    _write_json(
        report_schema_output,
        ReleaseReadinessReport.model_json_schema(mode="validation"),
    )


def _build_blockers(
    *,
    manifest: ReleaseReadinessManifest,
    taxonomy: FailureTaxonomyReport,
    requirements: Sequence[RequirementReadiness],
    artifact_ids: set[str],
    gate_ids: set[str],
) -> tuple[ReadinessBlocker, ...]:
    requirement_by_id = {item.requirement_id: item for item in requirements}
    taxonomy_by_id = {item.failure_id: item for item in taxonomy.items if item.release_blocking}
    evaluation_bindings = {
        binding.blocker_id
        for binding in manifest.blockers
        if binding.source is BlockerSource.EVALUATION_TAXONOMY
    }
    if evaluation_bindings != set(taxonomy_by_id):
        raise ValueError("Evaluation blocker bindings do not match failure taxonomy")

    blockers: list[ReadinessBlocker] = []
    open_coverage: set[str] = set()
    referenced_open_gates: set[str] = set()
    for binding in manifest.blockers:
        _require_known_ids(
            binding.requirement_ids,
            set(requirement_by_id),
            field_name=f"Blocker {binding.blocker_id} requirement",
        )
        _require_known_ids(
            binding.external_gate_ids,
            gate_ids,
            field_name=f"Blocker {binding.blocker_id} external gate",
        )
        _require_known_ids(
            binding.closure_artifact_ids,
            artifact_ids,
            field_name=f"Blocker {binding.blocker_id} closure artifact",
        )
        source_detail: str | None = None
        if binding.source is BlockerSource.EVALUATION_TAXONOMY:
            source_item = taxonomy_by_id[binding.blocker_id]
            source_detail = source_item.detail
            if binding.status is BlockerStatus.CLOSED:
                raise ValueError("Release-blocking taxonomy item cannot be marked closed")
        if binding.status is BlockerStatus.OPEN:
            completed = [
                requirement_id
                for requirement_id in binding.requirement_ids
                if requirement_by_id[requirement_id].current_status is RequirementStatus.COMPLETE
            ]
            if completed:
                raise ValueError(
                    f"Open blocker {binding.blocker_id} references complete requirements: "
                    f"{', '.join(completed)}"
                )
            open_coverage.update(binding.requirement_ids)
            referenced_open_gates.update(binding.external_gate_ids)
        else:
            pending = [
                gate_id
                for gate_id in binding.external_gate_ids
                if next(gate for gate in manifest.external_gates if gate.gate_id == gate_id).status
                is ExternalGateStatus.PENDING
            ]
            if pending:
                raise ValueError("Closed blocker still depends on pending external gates")
        blockers.append(
            ReadinessBlocker(
                blocker_id=binding.blocker_id,
                source=binding.source,
                status=binding.status,
                owner=binding.owner,
                requirement_ids=binding.requirement_ids,
                external_gate_ids=binding.external_gate_ids,
                closure_artifact_ids=binding.closure_artifact_ids,
                detail=binding.detail,
                source_detail=source_detail,
            )
        )

    incomplete_ids = {
        requirement.requirement_id
        for requirement in requirements
        if requirement.current_status is not RequirementStatus.COMPLETE
    }
    if open_coverage != incomplete_ids:
        missing = sorted(incomplete_ids - open_coverage, key=_requirement_id_sort_key)
        extra = sorted(open_coverage - incomplete_ids, key=_requirement_id_sort_key)
        raise ValueError(
            f"Open blocker coverage does not match incomplete requirements; "
            f"missing={missing}, extra={extra}"
        )
    pending_gate_ids = {
        gate.gate_id
        for gate in manifest.external_gates
        if gate.status is ExternalGateStatus.PENDING
    }
    if not pending_gate_ids.issubset(referenced_open_gates):
        missing_gates = sorted(pending_gate_ids - referenced_open_gates)
        raise ValueError(f"Pending external gates are not bound to open blockers: {missing_gates}")
    return tuple(blockers)


def _markdown_cells(line: str) -> tuple[str, ...] | None:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    cells: list[str] = []
    current: list[str] = []
    in_code_span = False
    escaped = False
    for character in stripped[1:-1]:
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            current.append(character)
            escaped = True
        elif character == "`":
            current.append(character)
            in_code_span = not in_code_span
        elif character == "|" and not in_code_span:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    if in_code_span:
        raise ValueError("Feature matrix contains an unterminated code span")
    cells.append("".join(current).strip())
    return tuple(cells)


def _is_separator_row(cells: Sequence[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _strip_code(value: str) -> str:
    if len(value) >= 2 and value.startswith("`") and value.endswith("`"):
        return value[1:-1]
    return value


def _requirement_sort_key(requirement: MatrixRequirement) -> tuple[int, int]:
    return _requirement_id_sort_key(requirement.requirement_id)


def _requirement_id_sort_key(requirement_id: str) -> tuple[int, int]:
    day, item = requirement_id[1:].split("-", maxsplit=1)
    return int(day), int(item)


def _status_counts(
    requirements: Sequence[MatrixRequirement],
) -> dict[RequirementStatus, int]:
    counts = Counter(requirement.current_status for requirement in requirements)
    return {status: counts[status] for status in RequirementStatus}


def _resolve_evidence_path(root: Path, path: Path) -> Path:
    candidate = path if path.is_absolute() else root / path
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ValueError(f"Evidence path is missing or outside repository root: {path}") from exc
    if not resolved.is_file():
        raise ValueError(f"Evidence path is not a file: {path}")
    return resolved


def _resolve_relative_evidence(root: Path, relative_path: str) -> Path:
    _validate_relative_path(relative_path)
    return _resolve_evidence_path(root, Path(relative_path))


def _snapshot_artifact(root: Path, artifact: EvidenceArtifactSpec) -> ArtifactSnapshot:
    path = _resolve_relative_evidence(root, artifact.relative_path)
    size = path.stat().st_size
    if size <= 0:
        raise ValueError(f"Evidence artifact is empty: {artifact.relative_path}")
    return ArtifactSnapshot(
        artifact_id=artifact.artifact_id,
        relative_path=artifact.relative_path,
        sha256=_sha256(path),
        byte_size=size,
    )


def _require_known_ids(
    values: Sequence[str],
    allowed: set[str],
    *,
    field_name: str,
) -> None:
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ValueError(f"{field_name} references unknown ids: {unknown}")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: BaseModel | object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_markdown(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Day 10 release-readiness ledger")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    parser.add_argument("--manifest-schema-output", type=Path, required=True)
    parser.add_argument("--report-schema-output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = build_release_readiness(root=args.root, manifest_path=args.manifest)
    write_release_readiness(
        report=report,
        json_output=args.json_output,
        markdown_output=args.markdown_output,
        manifest_schema_output=args.manifest_schema_output,
        report_schema_output=args.report_schema_output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
