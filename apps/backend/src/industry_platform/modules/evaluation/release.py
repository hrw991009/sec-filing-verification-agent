"""Strict Day 9 dataset registry and release evaluation manifest contracts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Sequence
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final, Self, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from industry_platform.modules.agent_runtime.domain import RunStopReason

DATASET_REGISTRY_ID: Final = "sec-agent-datasets-v1"
DATASET_REGISTRY_VERSION: Final = "v1"
RELEASE_MANIFEST_ID: Final = "sec-agent-release-v1"
RELEASE_SCHEMA_VERSION: Final = 1

_ACCESSION_PATTERN = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_REVISION_PATTERN = re.compile(r"^[a-f0-9]{40}$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_TOOL_REFERENCE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*@[A-Za-z0-9][A-Za-z0-9._-]*$")
_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class EvidenceLayer(StrEnum):
    DETERMINISTIC_CONTRACT = "deterministic_contract"
    OFFLINE_CAPABILITY = "offline_capability"
    LIVE_MODEL = "live_model"


class DatasetStatus(StrEnum):
    REGISTERED_ONLY = "registered_only"
    ADAPTER_READY = "adapter_ready"
    RELEASE_READY = "release_ready"
    BLOCKED = "blocked"


class DatasetUse(StrEnum):
    INTERNAL_EVALUATION = "internal_evaluation"
    PUBLISH_AGGREGATE_METRICS = "publish_aggregate_metrics"
    REDISTRIBUTE_PAYLOAD = "redistribute_payload"


class LicenseReviewStatus(StrEnum):
    METADATA_VERIFIED = "metadata_verified"
    OWNER_REVIEWED = "owner_reviewed"


class ArtifactRole(StrEnum):
    INPUT = "input"
    GOLD = "gold"
    MIXED_INPUT_GOLD = "mixed_input_gold"
    METADATA = "metadata"


class ReleaseManifestStatus(StrEnum):
    CONTRACT_ONLY = "contract_only"
    FROZEN = "frozen"
    EXECUTED = "executed"


class ReleaseCaseStatus(StrEnum):
    PLANNED = "planned"
    EXECUTED = "executed"


class ReleaseCaseKind(StrEnum):
    FIXED_CONTEXT = "fixed_context"
    SEC_TEMPORAL = "sec_temporal"
    LIVE_SEARCH = "live_search"
    AGENT_STATE = "agent_state"


class QuestionLanguage(StrEnum):
    EN = "en"
    ZH = "zh"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _require_https(value: str, *, field_name: str) -> str:
    if not value.startswith("https://") or any(character.isspace() for character in value):
        raise ValueError(f"{field_name} must be an absolute HTTPS URL")
    return value


def _require_unique(values: Sequence[object], *, field_name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must be unique")


class DatasetLicense(_FrozenModel):
    data_license_id: str = Field(min_length=1)
    data_license_url: str
    code_license_id: str | None = None
    code_license_url: str | None = None
    attribution_required: bool
    commercial_use_allowed: bool
    redistribution_allowed: bool
    source_documents_separately_governed: bool
    review_status: LicenseReviewStatus
    reviewed_on: date
    notes: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_license(self) -> Self:
        _require_https(self.data_license_url, field_name="Dataset license URL")
        if (self.code_license_id is None) != (self.code_license_url is None):
            raise ValueError("Dataset code license id and URL must be provided together")
        if self.code_license_url is not None:
            _require_https(self.code_license_url, field_name="Dataset code license URL")
        if not self.notes or any(not note.strip() for note in self.notes):
            raise ValueError("Dataset license notes must be non-empty")
        _require_unique(self.notes, field_name="Dataset license notes")
        return self


class DatasetArtifact(_FrozenModel):
    artifact_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    split: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    role: ArtifactRole
    relative_path: str = Field(min_length=1)
    download_url: str
    byte_size: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    document_count: int | None = Field(default=None, ge=1)
    question_count: int | None = Field(default=None, ge=1)
    contains_gold: bool
    allowed_in_model_context: bool = False
    redistribution_allowed: bool

    @model_validator(mode="after")
    def _validate_artifact(self) -> Self:
        _require_https(self.download_url, field_name="Dataset artifact URL")
        if (
            self.relative_path.startswith(("/", "\\"))
            or "\\" in self.relative_path
            or ".." in self.relative_path.split("/")
        ):
            raise ValueError("Dataset artifact path must be a safe relative POSIX path")
        if self.allowed_in_model_context and self.contains_gold:
            raise ValueError("Raw artifacts containing gold cannot enter model context")
        if self.role is ArtifactRole.GOLD and not self.contains_gold:
            raise ValueError("Gold artifact must declare that it contains gold")
        if self.role is ArtifactRole.METADATA:
            if self.question_count is not None:
                raise ValueError("Metadata artifact cannot declare question counts")
        elif (self.document_count is None) != (self.question_count is None):
            raise ValueError(
                "Dataset artifact document and question counts must be provided together"
            )
        return self


class DatasetRecord(_FrozenModel):
    dataset_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    display_name: str = Field(min_length=1)
    dataset_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    source_repository_url: str
    upstream_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    license: DatasetLicense
    allowed_uses: tuple[DatasetUse, ...]
    allowed_evidence_layers: tuple[EvidenceLayer, ...]
    official_metrics: tuple[str, ...]
    status: DatasetStatus
    release_eligible: bool
    artifacts: tuple[DatasetArtifact, ...]
    blockers: tuple[str, ...]
    limitations: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_record(self) -> Self:
        _require_https(self.source_repository_url, field_name="Dataset repository URL")
        if not self.allowed_uses or not self.allowed_evidence_layers or not self.official_metrics:
            raise ValueError("Dataset uses, evidence layers, and official metrics are required")
        _require_unique(self.allowed_uses, field_name="Dataset allowed uses")
        _require_unique(self.allowed_evidence_layers, field_name="Dataset evidence layers")
        _require_unique(self.official_metrics, field_name="Dataset official metrics")
        _require_unique(
            tuple(artifact.artifact_id for artifact in self.artifacts),
            field_name="Dataset artifact ids",
        )
        _require_unique(
            tuple(artifact.relative_path for artifact in self.artifacts),
            field_name="Dataset artifact paths",
        )
        if any(self.upstream_revision not in artifact.download_url for artifact in self.artifacts):
            raise ValueError("Dataset artifact URL must pin the record upstream revision")
        if any(
            artifact.redistribution_allowed and not self.license.redistribution_allowed
            for artifact in self.artifacts
        ):
            raise ValueError("Dataset artifact cannot exceed license redistribution rights")
        if (
            DatasetUse.REDISTRIBUTE_PAYLOAD in self.allowed_uses
            and not self.license.redistribution_allowed
        ):
            raise ValueError("Dataset use cannot exceed license redistribution rights")
        if self.status is DatasetStatus.RELEASE_READY:
            if (
                not self.release_eligible
                or self.blockers
                or not self.artifacts
                or self.license.review_status is not LicenseReviewStatus.OWNER_REVIEWED
            ):
                raise ValueError(
                    "Release-ready dataset must have owner-reviewed rights, "
                    "artifacts, and no blockers"
                )
        elif self.status is DatasetStatus.ADAPTER_READY:
            if not self.artifacts or any(
                artifact.document_count is None for artifact in self.artifacts
            ):
                raise ValueError("Adapter-ready dataset must freeze artifact split counts")
            if any(
                artifact.role is not ArtifactRole.METADATA and artifact.question_count is None
                for artifact in self.artifacts
            ):
                raise ValueError("Adapter-ready question artifacts must freeze question counts")
            if not self.blockers:
                raise ValueError("Adapter-ready dataset must retain release blockers")
        elif self.release_eligible:
            raise ValueError("Only a release-ready dataset may be release eligible")
        elif not self.blockers:
            raise ValueError("Non-release-ready dataset must declare blockers")
        if not self.limitations or any(not item.strip() for item in self.limitations):
            raise ValueError("Dataset limitations must be non-empty")
        _require_unique(self.blockers, field_name="Dataset blockers")
        _require_unique(self.limitations, field_name="Dataset limitations")
        return self


class DatasetRegistry(_FrozenModel):
    schema_version: int
    registry_id: str
    registry_version: str
    reviewed_on: date
    records: tuple[DatasetRecord, ...]

    @model_validator(mode="after")
    def _validate_registry(self) -> Self:
        if (
            self.schema_version != RELEASE_SCHEMA_VERSION
            or self.registry_id != DATASET_REGISTRY_ID
            or self.registry_version != DATASET_REGISTRY_VERSION
        ):
            raise ValueError("Dataset registry identity is invalid")
        if not self.records:
            raise ValueError("Dataset registry must contain records")
        _require_unique(
            tuple(record.dataset_id for record in self.records),
            field_name="Dataset registry ids",
        )
        return self


class ReleaseBudget(_FrozenModel):
    max_steps: int = Field(ge=1)
    max_tool_calls: int = Field(ge=0)
    max_total_tokens: int = Field(ge=1)
    max_cost_micro_usd: int = Field(ge=0)
    max_latency_ms: int = Field(ge=1)
    max_revisions: int = Field(ge=0, le=1)


class ReleaseRuntimeConfiguration(_FrozenModel):
    runtime_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    harness_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    model_provider: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    model_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    model_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    prompt_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    toolset_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    context_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    retrieval_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    graph_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    verifier_version: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    scorer_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ReleaseStrategy(_FrozenModel):
    strategy_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    configuration: ReleaseRuntimeConfiguration
    available_tools: tuple[str, ...]

    @field_validator("available_tools")
    @classmethod
    def _validate_available_tools(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        _require_unique(values, field_name="Release strategy tools")
        if any(not _TOOL_REFERENCE_PATTERN.fullmatch(value) for value in values):
            raise ValueError("Release strategy tool reference is invalid")
        return values


class MilestoneOrder(_FrozenModel):
    before: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    after: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")

    @model_validator(mode="after")
    def _validate_order(self) -> Self:
        if self.before == self.after:
            raise ValueError("Milestone order cannot reference the same milestone")
        return self


class ActionArgumentConstraint(_FrozenModel):
    action: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    argument: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    required: bool
    allowed_values: tuple[str, ...] = ()

    @field_validator("allowed_values")
    @classmethod
    def _validate_allowed_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        _require_unique(values, field_name="Argument constraint values")
        return values


class FinalStateExpectation(_FrozenModel):
    path: str = Field(pattern=r"^[a-z][A-Za-z0-9_.]*$")
    operator: str = Field(pattern=r"^(eq|gte|lte|absent)$")
    expected_value: str | int | bool | None

    @model_validator(mode="after")
    def _validate_expected_value(self) -> Self:
        if (self.operator == "absent") != (self.expected_value is None):
            raise ValueError("Only absent final-state expectations may omit a value")
        return self


class ReleaseTrajectoryContract(_FrozenModel):
    required_milestones: tuple[str, ...]
    allowed_actions: tuple[str, ...]
    forbidden_actions: tuple[str, ...]
    argument_constraints: tuple[ActionArgumentConstraint, ...]
    partial_order: tuple[MilestoneOrder, ...]
    final_state: tuple[FinalStateExpectation, ...]
    expected_stop_reason: RunStopReason

    @model_validator(mode="after")
    def _validate_trajectory(self) -> Self:
        if not self.required_milestones or not self.allowed_actions:
            raise ValueError("Release trajectory requires milestones and allowed actions")
        for field_name, values in (
            ("Required milestones", self.required_milestones),
            ("Allowed actions", self.allowed_actions),
            ("Forbidden actions", self.forbidden_actions),
        ):
            _require_unique(values, field_name=field_name)
            if any(_IDENTIFIER_PATTERN.fullmatch(value) is None for value in values):
                raise ValueError(f"{field_name} contain an invalid identifier")
        if set(self.allowed_actions) & set(self.forbidden_actions):
            raise ValueError("Allowed and forbidden trajectory actions must be disjoint")
        milestone_set = set(self.required_milestones)
        if any(
            order.before not in milestone_set or order.after not in milestone_set
            for order in self.partial_order
        ):
            raise ValueError("Partial order must reference required milestones")
        return self


class ReleaseSecSource(_FrozenModel):
    accession: str = Field(pattern=r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
    form: str
    available_at: datetime
    snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_locators: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_source(self) -> Self:
        if self.available_at.utcoffset() is None:
            raise ValueError("SEC source available_at must be timezone-aware")
        if self.form not in {"10-K", "10-K/A", "10-Q", "10-Q/A"}:
            raise ValueError("SEC source form is outside the frozen release scope")
        if not self.evidence_locators:
            raise ValueError("SEC source must expose Evidence locators")
        _require_unique(self.evidence_locators, field_name="SEC Evidence locators")
        return self


class ReleaseSecGold(_FrozenModel):
    cik: str = Field(pattern=r"^[0-9]{10}$")
    report_period: date
    as_of: datetime
    sources: tuple[ReleaseSecSource, ...]

    @model_validator(mode="after")
    def _validate_sec_gold(self) -> Self:
        if self.as_of.utcoffset() is None:
            raise ValueError("SEC release as_of must be timezone-aware")
        if not self.sources:
            raise ValueError("SEC release gold must contain sources")
        _require_unique(
            tuple(source.accession for source in self.sources),
            field_name="SEC release accessions",
        )
        if any(source.available_at > self.as_of for source in self.sources):
            raise ValueError("SEC release source cannot be visible after as_of")
        return self


class ReleaseAnswerGold(_FrozenModel):
    expected_answer_key: str | None = None
    supporting_fact_keys: tuple[str, ...] = ()
    expected_program: str | None = None
    expected_result: str | None = None
    tolerance: str | None = None
    unit: str | None = None
    rounding_places: int | None = Field(default=None, ge=0, le=12)
    expected_business_status: str | None = None

    @model_validator(mode="after")
    def _validate_answer_gold(self) -> Self:
        if self.expected_result is None and any(
            value is not None for value in (self.tolerance, self.unit, self.rounding_places)
        ):
            raise ValueError("Numerical result metadata requires an expected result")
        _require_unique(self.supporting_fact_keys, field_name="Supporting fact keys")
        return self


class ReleaseEvidenceBinding(_FrozenModel):
    run_id: UUID
    trace_id: UUID
    evidence_ids: tuple[UUID, ...]
    calculation_ids: tuple[UUID, ...]

    @model_validator(mode="after")
    def _validate_binding(self) -> Self:
        values = (self.run_id, self.trace_id, *self.evidence_ids, *self.calculation_ids)
        if any(value.int == 0 for value in values):
            raise ValueError("Release evidence binding cannot contain a nil UUID")
        _require_unique(self.evidence_ids, field_name="Release Evidence ids")
        _require_unique(self.calculation_ids, field_name="Release Calculation ids")
        return self


class ReleaseQuestion(_FrozenModel):
    language: QuestionLanguage
    text: str = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def _validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Release question text must be non-empty")
        return value


class ReleaseEvalCase(_FrozenModel):
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    case_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    dataset_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    dataset_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    split: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    document_group_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    source_artifact_ids: tuple[str, ...]
    kind: ReleaseCaseKind
    evidence_layer: EvidenceLayer
    questions: tuple[ReleaseQuestion, ...]
    strategy_ids: tuple[str, ...]
    budget: ReleaseBudget
    trajectory: ReleaseTrajectoryContract
    answer_gold: ReleaseAnswerGold
    sec_gold: ReleaseSecGold | None = None
    gold_allowed_in_model_context: bool = False
    status: ReleaseCaseStatus
    evidence_binding: ReleaseEvidenceBinding | None = None

    @model_validator(mode="after")
    def _validate_case(self) -> Self:
        if not self.source_artifact_ids or not self.strategy_ids or not self.questions:
            raise ValueError("Release case sources, strategies, and languages are required")
        _require_unique(self.source_artifact_ids, field_name="Release case artifacts")
        _require_unique(self.strategy_ids, field_name="Release case strategies")
        _require_unique(
            tuple(question.language for question in self.questions),
            field_name="Release case languages",
        )
        if self.gold_allowed_in_model_context:
            raise ValueError("Release gold cannot enter model context")
        if self.kind is ReleaseCaseKind.SEC_TEMPORAL and self.sec_gold is None:
            raise ValueError("SEC temporal case requires point-in-time SEC gold")
        if self.evidence_layer is EvidenceLayer.LIVE_MODEL and self.kind is not (
            ReleaseCaseKind.LIVE_SEARCH
        ):
            raise ValueError("Live model evidence must use a live-search case")
        if (self.status is ReleaseCaseStatus.EXECUTED) != (self.evidence_binding is not None):
            raise ValueError("Only an executed release case may have an Evidence binding")
        return self


class ReleaseEvalManifest(_FrozenModel):
    schema_version: int
    manifest_id: str
    manifest_version: str
    registry_id: str
    registry_version: str
    registry_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: ReleaseManifestStatus
    strategies: tuple[ReleaseStrategy, ...]
    cases: tuple[ReleaseEvalCase, ...]
    release_ready: bool
    blockers: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_manifest(self) -> Self:
        if (
            self.schema_version != RELEASE_SCHEMA_VERSION
            or self.manifest_id != RELEASE_MANIFEST_ID
            or self.registry_id != DATASET_REGISTRY_ID
            or self.registry_version != DATASET_REGISTRY_VERSION
        ):
            raise ValueError("Release manifest identity is invalid")
        if not _VERSION_PATTERN.fullmatch(self.manifest_version):
            raise ValueError("Release manifest version is invalid")
        _require_unique(
            tuple(strategy.strategy_id for strategy in self.strategies),
            field_name="Release strategy ids",
        )
        _require_unique(
            tuple(case.case_id for case in self.cases),
            field_name="Release case ids",
        )
        strategy_ids = {strategy.strategy_id for strategy in self.strategies}
        if any(not set(case.strategy_ids) <= strategy_ids for case in self.cases):
            raise ValueError("Release case references an unknown strategy")
        group_splits: dict[tuple[str, str], set[str]] = {}
        for case in self.cases:
            group_splits.setdefault((case.dataset_id, case.document_group_id), set()).add(
                case.split
            )
        if any(len(splits) != 1 for splits in group_splits.values()):
            raise ValueError("Release document groups cannot cross splits")
        if self.status is ReleaseManifestStatus.CONTRACT_ONLY:
            if self.release_ready or not self.blockers:
                raise ValueError("Contract-only manifest must remain blocked")
        elif not self.cases or not self.strategies:
            raise ValueError("Frozen or executed release manifest requires cases and strategies")
        if self.status is ReleaseManifestStatus.EXECUTED and any(
            case.status is not ReleaseCaseStatus.EXECUTED for case in self.cases
        ):
            raise ValueError("Executed release manifest requires executed cases")
        if self.release_ready and (
            self.status is not ReleaseManifestStatus.EXECUTED or self.blockers
        ):
            raise ValueError("Release-ready manifest must be executed with no blockers")
        if not self.release_ready and not self.blockers:
            raise ValueError("Non-ready release manifest must declare blockers")
        _require_unique(self.blockers, field_name="Release manifest blockers")
        return self


def _reject_duplicate_json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_finite_json(value: str) -> object:
    raise ValueError(f"Non-finite JSON number: {value}")


def _load_json(path: Path) -> str:
    raw = path.read_text(encoding="utf-8")
    json.loads(
        raw,
        object_pairs_hook=_reject_duplicate_json_pairs,
        parse_constant=_reject_non_finite_json,
    )
    return raw


def loads_strict_json(raw: str) -> object:
    """Parse evaluation JSON while rejecting duplicate keys and non-finite numbers."""

    return json.loads(
        raw,
        object_pairs_hook=_reject_duplicate_json_pairs,
        parse_constant=_reject_non_finite_json,
    )


def load_strict_json(path: Path) -> object:
    """Load an evaluation artifact while rejecting duplicate keys and non-finite numbers."""

    return loads_strict_json(path.read_text(encoding="utf-8"))


def canonical_sha256(model: BaseModel) -> str:
    payload = json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_dataset_registry(path: Path) -> DatasetRegistry:
    return DatasetRegistry.model_validate_json(_load_json(path), strict=True)


def load_release_manifest(path: Path) -> ReleaseEvalManifest:
    return ReleaseEvalManifest.model_validate_json(_load_json(path), strict=True)


def validate_manifest_against_registry(
    manifest: ReleaseEvalManifest,
    registry: DatasetRegistry,
) -> None:
    if manifest.registry_sha256 != canonical_sha256(registry):
        raise ValueError("Release manifest registry checksum does not match")
    records = {record.dataset_id: record for record in registry.records}
    for case in manifest.cases:
        record = records.get(case.dataset_id)
        if record is None:
            raise ValueError(f"Release case references an unregistered dataset: {case.dataset_id}")
        if case.dataset_version != record.dataset_version:
            raise ValueError(f"Release case dataset version does not match: {case.case_id}")
        artifact_ids = {artifact.artifact_id for artifact in record.artifacts}
        if not set(case.source_artifact_ids) <= artifact_ids:
            raise ValueError(f"Release case references an unknown artifact: {case.case_id}")
        artifacts = {
            artifact.artifact_id: artifact
            for artifact in record.artifacts
            if artifact.artifact_id in case.source_artifact_ids
        }
        if any(artifact.split not in {case.split, "metadata"} for artifact in artifacts.values()):
            raise ValueError(f"Release case artifact split does not match: {case.case_id}")
        if case.evidence_layer not in record.allowed_evidence_layers:
            raise ValueError(f"Release case evidence layer is not allowed: {case.case_id}")
        if (
            manifest.status is not ReleaseManifestStatus.CONTRACT_ONLY
            and not record.release_eligible
        ):
            raise ValueError(f"Release case dataset is not release eligible: {case.dataset_id}")


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_release_schemas(*, registry_output: Path, manifest_output: Path) -> None:
    _write_json(registry_output, DatasetRegistry.model_json_schema(mode="validation"))
    _write_json(manifest_output, ReleaseEvalManifest.model_json_schema(mode="validation"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Day 9 release evaluation contracts")
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--registry-schema-output", required=True, type=Path)
    parser.add_argument("--manifest-schema-output", required=True, type=Path)
    args = parser.parse_args(argv)

    registry = load_dataset_registry(cast(Path, args.registry))
    manifest = load_release_manifest(cast(Path, args.manifest))
    validate_manifest_against_registry(manifest, registry)
    write_release_schemas(
        registry_output=cast(Path, args.registry_schema_output),
        manifest_output=cast(Path, args.manifest_schema_output),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
