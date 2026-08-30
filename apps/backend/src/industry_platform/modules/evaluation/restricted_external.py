"""Adapters for license-restricted and dynamic external finance benchmarks."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Final, Self, cast

import httpx2
from pydantic import BaseModel, ConfigDict, Field, model_validator

from industry_platform.adapters.public_egress import create_public_egress_http_client
from industry_platform.modules.evaluation.fixed_context import (
    FixedContextArtifactStore,
    VerifiedArtifact,
    require_artifact,
    require_dataset,
)
from industry_platform.modules.evaluation.materialize import materialize_registered_artifacts
from industry_platform.modules.evaluation.release import (
    DatasetRecord,
    load_dataset_registry,
    loads_strict_json,
)

FINANCEBENCH_DATASET_ID: Final = "financebench"
FINSEARCHCOMP_DATASET_ID: Final = "finsearchcomp"
RESTRICTED_EXTERNAL_ADAPTER_VERSION: Final = "restricted-external-adapter-v1"
FINANCEBENCH_REPORT_VERSION: Final = "financebench-adapter-report-v1"
FINSEARCHCOMP_REPORT_VERSION: Final = "finsearchcomp-adapter-report-v1"

_FINANCEBENCH_QUESTION_ARTIFACT: Final = "financebench-open-source"
_FINANCEBENCH_METADATA_ARTIFACT: Final = "financebench-document-information"
_FINSEARCHCOMP_FULL_ARTIFACT: Final = "finsearchcomp-full"
_FINSEARCHCOMP_AKSHARE_ARTIFACT: Final = "finsearchcomp-akshare"
_FINSEARCHCOMP_ID_PATTERN = re.compile(r"^\((T[123])\)[A-Za-z0-9_ -]+$")


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FinanceBenchDocument(_FrozenModel):
    document_id: str = Field(min_length=1, max_length=256)
    company: str = Field(min_length=1)
    document_type: str = Field(min_length=1)
    document_period: int = Field(ge=1900, le=2200)
    document_url: str = Field(pattern=r"^https://\S+$")
    gics_sector: str = Field(min_length=1)


class FinanceBenchInput(_FrozenModel):
    adapter_version: str = RESTRICTED_EXTERNAL_ADAPTER_VERSION
    dataset_id: str = FINANCEBENCH_DATASET_ID
    dataset_version: str
    split: str = "open-sample"
    case_id: str = Field(pattern=r"^financebench_id_[0-9]+$")
    question: str = Field(min_length=1)
    question_type: str = Field(min_length=1)
    reasoning_type: str | None
    company: str = Field(min_length=1)
    document: FinanceBenchDocument
    source_artifact_ids: tuple[str, ...] = (_FINANCEBENCH_QUESTION_ARTIFACT,)

    @model_validator(mode="after")
    def _validate_identity(self) -> Self:
        if (
            self.adapter_version != RESTRICTED_EXTERNAL_ADAPTER_VERSION
            or self.dataset_id != FINANCEBENCH_DATASET_ID
        ):
            raise ValueError("FinanceBench input identity is invalid")
        if self.document.company != self.company:
            raise ValueError("FinanceBench question and document company differ")
        return self


class FinanceBenchEvidence(_FrozenModel):
    document_id: str = Field(min_length=1, max_length=256)
    page_index: int = Field(ge=0)
    evidence_text: str = Field(min_length=1)
    full_page_text: str = Field(min_length=1)


class FinanceBenchGold(_FrozenModel):
    answer: str = Field(min_length=1)
    justification: str
    evidence: tuple[FinanceBenchEvidence, ...]

    @model_validator(mode="after")
    def _validate_evidence(self) -> Self:
        if not self.evidence:
            raise ValueError("FinanceBench gold requires Evidence")
        return self


class FinanceBenchCase(_FrozenModel):
    input: FinanceBenchInput
    gold: FinanceBenchGold


class FinSearchTask(StrEnum):
    DYNAMIC = "T1"
    HISTORICAL = "T2"
    INVESTIGATION = "T3"


class FinSearchDependency(StrEnum):
    HISTORICAL_WEB = "historical_web"
    DYNAMIC_AKSHARE = "dynamic_akshare"
    DYNAMIC_PROFESSIONAL = "dynamic_professional"


class FinSearchInput(_FrozenModel):
    adapter_version: str = RESTRICTED_EXTERNAL_ADAPTER_VERSION
    dataset_id: str = FINSEARCHCOMP_DATASET_ID
    dataset_version: str
    case_id: str = Field(pattern=r"^finsearchcomp-[a-f0-9]{20}$")
    upstream_prompt_id: str = Field(min_length=1)
    task: FinSearchTask
    prompt: str = Field(min_length=1)
    dependency: FinSearchDependency
    source_artifact_id: str

    @model_validator(mode="after")
    def _validate_dependency(self) -> Self:
        if (
            self.adapter_version != RESTRICTED_EXTERNAL_ADAPTER_VERSION
            or self.dataset_id != FINSEARCHCOMP_DATASET_ID
        ):
            raise ValueError("FinSearchComp input identity is invalid")
        if self.task is FinSearchTask.DYNAMIC:
            if self.dependency is FinSearchDependency.HISTORICAL_WEB:
                raise ValueError("Dynamic FinSearchComp input requires a live dependency")
        elif self.dependency is not FinSearchDependency.HISTORICAL_WEB:
            raise ValueError("Historical FinSearchComp input cannot require a live dependency")
        return self


class FinSearchGold(_FrozenModel):
    reference_answer: str = Field(min_length=1)
    judge_system_prompt: str = Field(min_length=1)
    judge_prompt_template: str = Field(min_length=1)
    frozen_ground_truth: str | None = None
    ground_truth_as_of: str | None = None

    @model_validator(mode="after")
    def _validate_dynamic_gold(self) -> Self:
        if (self.frozen_ground_truth is None) != (self.ground_truth_as_of is None):
            raise ValueError("FinSearchComp dynamic ground truth and timestamp must be paired")
        return self


class FinSearchCase(_FrozenModel):
    input: FinSearchInput
    gold: FinSearchGold


class FinanceBenchAdapterReport(_FrozenModel):
    schema_version: int = 1
    report_version: str = FINANCEBENCH_REPORT_VERSION
    evidence_layer: str = "deterministic_contract"
    dataset_id: str = FINANCEBENCH_DATASET_ID
    dataset_version: str
    artifacts: tuple[VerifiedArtifact, ...]
    question_count: int = Field(ge=1)
    referenced_document_count: int = Field(ge=1)
    metadata_record_count: int = Field(ge=1)
    unique_metadata_document_count: int = Field(ge=1)
    evidence_count: int = Field(ge=1)
    question_type_counts: Mapping[str, int]
    case_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    conflicting_unreferenced_document_ids: tuple[str, ...]
    model_executed: bool = False
    source_documents_materialized: bool = False
    official_metric_scores: None = None
    release_eligible: bool = False
    blockers: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_boundary(self) -> Self:
        if (
            self.schema_version != 1
            or self.report_version != FINANCEBENCH_REPORT_VERSION
            or self.dataset_id != FINANCEBENCH_DATASET_ID
            or self.model_executed
            or self.source_documents_materialized
            or self.official_metric_scores is not None
            or self.release_eligible
            or not self.blockers
        ):
            raise ValueError("FinanceBench report overstates its execution boundary")
        if sum(self.question_type_counts.values()) != self.question_count:
            raise ValueError("FinanceBench question type counts do not reconcile")
        if not (
            self.referenced_document_count
            <= self.unique_metadata_document_count
            <= self.metadata_record_count
        ):
            raise ValueError("FinanceBench document counts do not reconcile")
        return self


class FinSearchHistoricalReport(_FrozenModel):
    schema_version: int = 1
    report_version: str = FINSEARCHCOMP_REPORT_VERSION
    report_scope: str = "historical"
    evidence_layer: str = "deterministic_contract"
    dataset_id: str = FINSEARCHCOMP_DATASET_ID
    dataset_version: str
    artifacts: tuple[VerifiedArtifact, ...]
    historical_case_count: int = Field(ge=1)
    simple_lookup_count: int = Field(ge=1)
    investigation_count: int = Field(ge=1)
    akshare_overlap_count: int = Field(ge=1)
    case_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_executed: bool = False
    official_judge_executed: bool = False
    official_metric_scores: None = None
    release_eligible: bool = False
    blockers: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_boundary(self) -> Self:
        if (
            self.model_executed
            or self.official_judge_executed
            or self.official_metric_scores is not None
            or self.release_eligible
            or not self.blockers
        ):
            raise ValueError("FinSearchComp historical report overstates its execution boundary")
        if (
            self.simple_lookup_count + self.investigation_count != self.historical_case_count
            or self.akshare_overlap_count != self.historical_case_count
        ):
            raise ValueError("FinSearchComp historical counts do not reconcile")
        return self


class FinSearchLiveContractReport(_FrozenModel):
    schema_version: int = 1
    report_version: str = FINSEARCHCOMP_REPORT_VERSION
    report_scope: str = "dynamic_live"
    evidence_layer: str = "deterministic_contract"
    dataset_id: str = FINSEARCHCOMP_DATASET_ID
    dataset_version: str
    artifacts: tuple[VerifiedArtifact, ...]
    dynamic_case_count: int = Field(ge=1)
    akshare_compatible_count: int = Field(ge=1)
    professional_dependency_count: int = Field(ge=1)
    akshare_timestamp_drift_count: int = Field(ge=0)
    case_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    live_dependencies_executed: bool = False
    model_executed: bool = False
    official_judge_executed: bool = False
    repeated_run_count: int = 0
    pass_k: None = None
    official_metric_scores: None = None
    release_eligible: bool = False
    blockers: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_boundary(self) -> Self:
        if (
            self.live_dependencies_executed
            or self.model_executed
            or self.official_judge_executed
            or self.repeated_run_count != 0
            or self.pass_k is not None
            or self.official_metric_scores is not None
            or self.release_eligible
            or not self.blockers
        ):
            raise ValueError("FinSearchComp live report overstates its execution boundary")
        if self.akshare_compatible_count + self.professional_dependency_count != (
            self.dynamic_case_count
        ):
            raise ValueError("FinSearchComp dynamic dependency counts do not reconcile")
        return self


class FinanceBenchAdapter:
    def __init__(self, record: DatasetRecord, store: FixedContextArtifactStore) -> None:
        require_dataset(record, FINANCEBENCH_DATASET_ID)
        self._record = record
        self._store = store

    def cases(self) -> tuple[FinanceBenchCase, ...]:
        question_artifact = require_artifact(self._record, _FINANCEBENCH_QUESTION_ARTIFACT)
        metadata_artifact = require_artifact(self._record, _FINANCEBENCH_METADATA_ARTIFACT)
        question_path = self._verified_path(question_artifact.artifact_id)
        metadata_path = self._verified_path(metadata_artifact.artifact_id)
        raw_questions = _load_json_lines(question_path)
        raw_metadata = _load_json_lines(metadata_path)
        metadata_by_id: dict[str, list[Mapping[str, object]]] = {}
        for raw in raw_metadata:
            document_id = _required_string(raw, "doc_name")
            metadata_by_id.setdefault(document_id, []).append(raw)

        cases = []
        for raw in raw_questions:
            document_id = _required_string(raw, "doc_name")
            documents = metadata_by_id.get(document_id, [])
            if len(documents) != 1:
                raise ValueError(
                    f"FinanceBench referenced document metadata is ambiguous: {document_id}"
                )
            document = _financebench_document(documents[0])
            evidence_raw = raw.get("evidence")
            if not isinstance(evidence_raw, list) or not evidence_raw:
                raise ValueError("FinanceBench question Evidence is invalid")
            evidence = tuple(_financebench_evidence(item, document_id) for item in evidence_raw)
            if _required_string(raw, "dataset_subset_label") != "OPEN_SOURCE":
                raise ValueError("FinanceBench adapter only accepts the open sample")
            cases.append(
                FinanceBenchCase(
                    input=FinanceBenchInput(
                        dataset_version=self._record.dataset_version,
                        case_id=_required_string(raw, "financebench_id"),
                        question=_required_string(raw, "question"),
                        question_type=_required_string(raw, "question_type"),
                        reasoning_type=_optional_string(raw, "question_reasoning"),
                        company=_required_string(raw, "company"),
                        document=document,
                    ),
                    gold=FinanceBenchGold(
                        answer=_required_string(raw, "answer"),
                        justification=_optional_string(raw, "justification") or "",
                        evidence=evidence,
                    ),
                )
            )
        _require_unique_case_ids(
            (case.input.case_id for case in cases),
            "FinanceBench",
        )
        return tuple(cases)

    def metadata(self) -> tuple[Mapping[str, object], ...]:
        return _load_json_lines(self._verified_path(_FINANCEBENCH_METADATA_ARTIFACT))

    def _verified_path(self, artifact_id: str) -> Path:
        artifact = require_artifact(self._record, artifact_id)
        self._store.verify(self._record, artifact)
        return self._store.path_for(self._record, artifact)


class FinSearchCompAdapter:
    def __init__(self, record: DatasetRecord, store: FixedContextArtifactStore) -> None:
        require_dataset(record, FINSEARCHCOMP_DATASET_ID)
        self._record = record
        self._store = store

    def cases(self) -> tuple[tuple[FinSearchCase, ...], tuple[FinSearchCase, ...]]:
        full_raw = self._load_artifact(_FINSEARCHCOMP_FULL_ARTIFACT)
        akshare_raw = self._load_artifact(_FINSEARCHCOMP_AKSHARE_ARTIFACT)
        akshare_keys = {_finsearch_identity(item) for item in akshare_raw}
        full_cases = tuple(
            _finsearch_case(
                self._record,
                raw,
                artifact_id=_FINSEARCHCOMP_FULL_ARTIFACT,
                akshare_compatible=_finsearch_identity(raw) in akshare_keys,
            )
            for raw in full_raw
        )
        akshare_cases = tuple(
            _finsearch_case(
                self._record,
                raw,
                artifact_id=_FINSEARCHCOMP_AKSHARE_ARTIFACT,
                akshare_compatible=True,
            )
            for raw in akshare_raw
        )
        _require_unique_case_ids((case.input.case_id for case in full_cases), "FinSearchComp full")
        _require_unique_case_ids(
            (case.input.case_id for case in akshare_cases), "FinSearchComp AkShare"
        )
        full_by_id = {case.input.case_id: case for case in full_cases}
        if not set(case.input.case_id for case in akshare_cases) < set(full_by_id):
            raise ValueError("FinSearchComp AkShare release must be a strict full-release subset")
        for case in akshare_cases:
            full_case = full_by_id[case.input.case_id]
            if _finsearch_comparison_payload(full_case) != _finsearch_comparison_payload(case):
                raise ValueError("FinSearchComp AkShare case differs from the full release")
        return full_cases, akshare_cases

    def _load_artifact(self, artifact_id: str) -> tuple[Mapping[str, object], ...]:
        artifact = require_artifact(self._record, artifact_id)
        self._store.verify(self._record, artifact)
        payload = loads_strict_json(
            self._store.path_for(self._record, artifact).read_text(encoding="utf-8")
        )
        if not isinstance(payload, list) or not payload:
            raise ValueError(f"FinSearchComp artifact is not a non-empty list: {artifact_id}")
        if any(not isinstance(item, dict) for item in payload):
            raise ValueError(f"FinSearchComp artifact contains a non-object: {artifact_id}")
        return tuple(cast(Mapping[str, object], item) for item in payload)


def build_financebench_report(
    record: DatasetRecord,
    *,
    root: Path,
) -> FinanceBenchAdapterReport:
    store = FixedContextArtifactStore(root)
    adapter = FinanceBenchAdapter(record, store)
    cases = adapter.cases()
    metadata = adapter.metadata()
    metadata_ids = tuple(_required_string(item, "doc_name") for item in metadata)
    referenced_ids = {case.input.document.document_id for case in cases}
    conflicts = tuple(
        sorted(
            document_id
            for document_id, count in Counter(metadata_ids).items()
            if count > 1 and document_id not in referenced_ids
        )
    )
    return FinanceBenchAdapterReport(
        dataset_version=record.dataset_version,
        artifacts=tuple(store.verify(record, artifact) for artifact in record.artifacts),
        question_count=len(cases),
        referenced_document_count=len(referenced_ids),
        metadata_record_count=len(metadata),
        unique_metadata_document_count=len(set(metadata_ids)),
        evidence_count=sum(len(case.gold.evidence) for case in cases),
        question_type_counts=dict(
            sorted(Counter(case.input.question_type for case in cases).items())
        ),
        case_sha256=_stable_digest(cases),
        conflicting_unreferenced_document_ids=conflicts,
        blockers=(
            "noncommercial_use_only",
            "source_document_rights_not_reviewed",
            "source_documents_not_materialized",
            "human_answer_review_not_executed",
            "owner_license_review_not_complete",
        ),
    )


def build_finsearchcomp_reports(
    record: DatasetRecord,
    *,
    root: Path,
) -> tuple[FinSearchHistoricalReport, FinSearchLiveContractReport]:
    store = FixedContextArtifactStore(root)
    full_cases, akshare_cases = FinSearchCompAdapter(record, store).cases()
    artifacts = tuple(store.verify(record, artifact) for artifact in record.artifacts)
    historical = tuple(case for case in full_cases if case.input.task is not FinSearchTask.DYNAMIC)
    dynamic = tuple(case for case in full_cases if case.input.task is FinSearchTask.DYNAMIC)
    akshare_ids = {case.input.case_id for case in akshare_cases}
    full_by_id = {case.input.case_id: case for case in full_cases}
    historical_overlap = sum(case.input.case_id in akshare_ids for case in historical)
    historical_report = FinSearchHistoricalReport(
        dataset_version=record.dataset_version,
        artifacts=artifacts,
        historical_case_count=len(historical),
        simple_lookup_count=sum(case.input.task is FinSearchTask.HISTORICAL for case in historical),
        investigation_count=sum(
            case.input.task is FinSearchTask.INVESTIGATION for case in historical
        ),
        akshare_overlap_count=historical_overlap,
        case_sha256=_stable_digest(historical),
        blockers=(
            "model_runs_not_executed",
            "official_llm_judge_not_executed",
            "judge_variance_not_measured",
            "owner_review_not_complete",
        ),
    )
    live_report = FinSearchLiveContractReport(
        dataset_version=record.dataset_version,
        artifacts=artifacts,
        dynamic_case_count=len(dynamic),
        akshare_compatible_count=sum(case.input.case_id in akshare_ids for case in dynamic),
        professional_dependency_count=sum(
            case.input.case_id not in akshare_ids for case in dynamic
        ),
        akshare_timestamp_drift_count=sum(
            full_by_id[case.input.case_id].gold.ground_truth_as_of != case.gold.ground_truth_as_of
            for case in akshare_cases
            if case.input.task is FinSearchTask.DYNAMIC
        ),
        case_sha256=_stable_digest(dynamic),
        blockers=(
            "dynamic_data_not_fetched",
            "professional_data_dependencies_unavailable",
            "model_runs_not_executed",
            "official_llm_judge_not_executed",
            "repeated_runs_not_executed",
        ),
    )
    return historical_report, live_report


async def materialize_restricted_external(
    financebench: DatasetRecord,
    finsearchcomp: DatasetRecord,
    *,
    root: Path,
    client: httpx2.AsyncClient | None = None,
) -> None:
    owned_client = client is None
    active_client = client or create_public_egress_http_client()
    try:
        for record in (financebench, finsearchcomp):
            await materialize_registered_artifacts(record, root=root, client=active_client)
    finally:
        if owned_client:
            await active_client.aclose()


def write_restricted_external_schemas(
    *, financebench_output: Path, finsearchcomp_output: Path
) -> None:
    _write_json(financebench_output, FinanceBenchCase.model_json_schema(mode="validation"))
    _write_json(finsearchcomp_output, FinSearchCase.model_json_schema(mode="validation"))


def _load_json_lines(path: Path) -> tuple[Mapping[str, object], ...]:
    rows = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        value = loads_strict_json(raw)
        if not isinstance(value, dict):
            raise ValueError(f"JSONL row is not an object: {path.name}:{line_number}")
        rows.append(cast(Mapping[str, object], value))
    if not rows:
        raise ValueError(f"JSONL artifact is empty: {path.name}")
    return tuple(rows)


def _financebench_document(raw: Mapping[str, object]) -> FinanceBenchDocument:
    expected = {"company", "doc_link", "doc_name", "doc_period", "doc_type", "gics_sector"}
    if set(raw) != expected:
        raise ValueError("FinanceBench document metadata fields changed")
    period = raw.get("doc_period")
    if not isinstance(period, int) or isinstance(period, bool):
        raise ValueError("FinanceBench document period is invalid")
    return FinanceBenchDocument(
        document_id=_required_string(raw, "doc_name"),
        company=_required_string(raw, "company"),
        document_type=_required_string(raw, "doc_type"),
        document_period=period,
        document_url=_required_string(raw, "doc_link"),
        gics_sector=_required_string(raw, "gics_sector"),
    )


def _financebench_evidence(raw: object, document_id: str) -> FinanceBenchEvidence:
    if not isinstance(raw, dict) or set(raw) != {
        "doc_name",
        "evidence_page_num",
        "evidence_text",
        "evidence_text_full_page",
    }:
        raise ValueError("FinanceBench Evidence fields changed")
    typed = cast(Mapping[str, object], raw)
    if _required_string(typed, "doc_name") != document_id:
        raise ValueError("FinanceBench Evidence crosses documents")
    page_index = typed.get("evidence_page_num")
    if not isinstance(page_index, int) or isinstance(page_index, bool):
        raise ValueError("FinanceBench Evidence page is invalid")
    return FinanceBenchEvidence(
        document_id=document_id,
        page_index=page_index,
        evidence_text=_required_string(typed, "evidence_text"),
        full_page_text=_required_string(typed, "evidence_text_full_page"),
    )


def _finsearch_case(
    record: DatasetRecord,
    raw: Mapping[str, object],
    *,
    artifact_id: str,
    akshare_compatible: bool,
) -> FinSearchCase:
    allowed = {
        "akshare_ticker",
        "ground_truth",
        "judge_prompt_template",
        "judge_system_prompt",
        "label",
        "method",
        "prompt",
        "prompt_id",
        "response_reference",
        "response_reference_translate",
        "source",
        "tags",
        "time",
        "wind_ticker",
        "yfinance_ticker",
    }
    if not set(raw) <= allowed:
        raise ValueError("FinSearchComp upstream fields changed")
    prompt_id = _required_string(raw, "prompt_id")
    match = _FINSEARCHCOMP_ID_PATTERN.fullmatch(prompt_id)
    if match is None:
        raise ValueError(f"FinSearchComp prompt id is invalid: {prompt_id}")
    task = FinSearchTask(match.group(1))
    prompt = _required_string(raw, "prompt")
    reference = _optional_string(raw, "response_reference") or _optional_string(
        raw, "response_reference_translate"
    )
    if reference is None:
        raise ValueError(f"FinSearchComp reference answer is missing: {prompt_id}")
    ground_truth = _optional_string(raw, "ground_truth")
    timestamp = _optional_string(raw, "time")
    if task is FinSearchTask.DYNAMIC:
        if ground_truth is None or timestamp is None:
            raise ValueError(f"FinSearchComp dynamic gold is incomplete: {prompt_id}")
        dependency = (
            FinSearchDependency.DYNAMIC_AKSHARE
            if akshare_compatible
            else FinSearchDependency.DYNAMIC_PROFESSIONAL
        )
    else:
        if ground_truth is not None or timestamp is not None:
            raise ValueError(f"FinSearchComp historical case contains dynamic gold: {prompt_id}")
        dependency = FinSearchDependency.HISTORICAL_WEB
    identity = _finsearch_identity(raw)
    case_id = f"finsearchcomp-{hashlib.sha256(chr(0).join(identity).encode()).hexdigest()[:20]}"
    return FinSearchCase(
        input=FinSearchInput(
            dataset_version=record.dataset_version,
            case_id=case_id,
            upstream_prompt_id=prompt_id,
            task=task,
            prompt=prompt,
            dependency=dependency,
            source_artifact_id=artifact_id,
        ),
        gold=FinSearchGold(
            reference_answer=reference,
            judge_system_prompt=_required_string(raw, "judge_system_prompt"),
            judge_prompt_template=_required_string(raw, "judge_prompt_template"),
            frozen_ground_truth=ground_truth,
            ground_truth_as_of=timestamp,
        ),
    )


def _finsearch_identity(raw: Mapping[str, object]) -> tuple[str, str]:
    return (_required_string(raw, "prompt_id"), _required_string(raw, "prompt"))


def _finsearch_comparison_payload(case: FinSearchCase) -> tuple[object, ...]:
    return (
        case.input.case_id,
        case.input.upstream_prompt_id,
        case.input.task,
        case.input.prompt,
        case.gold.reference_answer,
        case.gold.judge_system_prompt,
        case.gold.judge_prompt_template,
        case.gold.frozen_ground_truth,
    )


def _required_string(raw: Mapping[str, object], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Required string field is invalid: {field}")
    return value


def _optional_string(raw: Mapping[str, object], field: str) -> str | None:
    value = raw.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Optional string field is invalid: {field}")
    return value


def _require_unique_case_ids(values: Iterable[str], name: str) -> None:
    case_ids = tuple(values)
    if len(case_ids) != len(set(case_ids)):
        raise ValueError(f"{name} case ids must be unique")


def _stable_digest(values: Iterable[BaseModel]) -> str:
    digest = hashlib.sha256()
    for value in values:
        payload = json.dumps(
            value.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        digest.update(len(payload).to_bytes(8, byteorder="big"))
        digest.update(payload)
    return digest.hexdigest()


def _write_json(path: Path, value: BaseModel | object) -> None:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_markdown(path: Path, title: str, lines: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join((f"# {title}", "", *lines, "")), encoding="utf-8", newline="\n")


async def _run(args: argparse.Namespace) -> None:
    registry = load_dataset_registry(cast(Path, args.registry))
    records = {record.dataset_id: record for record in registry.records}
    financebench = records[FINANCEBENCH_DATASET_ID]
    finsearchcomp = records[FINSEARCHCOMP_DATASET_ID]
    root = cast(Path, args.root)
    await materialize_restricted_external(financebench, finsearchcomp, root=root)
    finance_report = build_financebench_report(financebench, root=root)
    historical_report, live_report = build_finsearchcomp_reports(finsearchcomp, root=root)
    output = cast(Path, args.report_directory)
    _write_json(output / "financebench-adapter-v1.json", finance_report)
    _write_json(output / "finsearchcomp-historical-v1.json", historical_report)
    _write_json(output / "finsearchcomp-live-v1.json", live_report)
    _write_markdown(
        output / "financebench-adapter-v1.md",
        "FinanceBench restricted Adapter validation",
        (
            f"- Questions: {finance_report.question_count}",
            f"- Referenced documents: {finance_report.referenced_document_count}",
            f"- Evidence records: {finance_report.evidence_count}",
            "- Model executed: `false`",
            "- Source documents materialized: `false`",
            "- Official metric scores: `null`",
            "",
            "The CC BY-NC sample is restricted to internal non-commercial evaluation. "
            "Linked source-document rights and human answer review remain unresolved.",
        ),
    )
    _write_markdown(
        output / "finsearchcomp-historical-v1.md",
        "FinSearchComp historical Adapter validation",
        (
            f"- Historical cases: {historical_report.historical_case_count}",
            f"- T2 / T3: {historical_report.simple_lookup_count} / "
            f"{historical_report.investigation_count}",
            "- Model executed: `false`",
            "- Official LLM judge executed: `false`",
            "",
            "Historical conversion is reported separately from dynamic market-data cases.",
        ),
    )
    _write_markdown(
        output / "finsearchcomp-live-v1.md",
        "FinSearchComp dynamic live contract",
        (
            f"- Dynamic cases: {live_report.dynamic_case_count}",
            f"- AkShare compatible: {live_report.akshare_compatible_count}",
            f"- Professional dependency: {live_report.professional_dependency_count}",
            f"- AkShare timestamp drift: {live_report.akshare_timestamp_drift_count}",
            "- Live dependencies executed: `false`",
            "- Repeated runs / pass^k: `0` / `null`",
            "",
            "Dynamic data, model responses, and the upstream LLM judge are not PR hard gates.",
        ),
    )
    write_restricted_external_schemas(
        financebench_output=cast(Path, args.financebench_schema_output),
        finsearchcomp_output=cast(Path, args.finsearchcomp_schema_output),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate restricted FinanceBench and FinSearchComp adapters"
    )
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--report-directory", required=True, type=Path)
    parser.add_argument("--financebench-schema-output", required=True, type=Path)
    parser.add_argument("--finsearchcomp-schema-output", required=True, type=Path)
    args = parser.parse_args(argv)
    asyncio.run(_run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
