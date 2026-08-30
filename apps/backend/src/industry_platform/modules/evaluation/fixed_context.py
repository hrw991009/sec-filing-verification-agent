"""Shared contracts for fixed-context public benchmark adapters."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Final, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from industry_platform.modules.evaluation.release import DatasetArtifact, DatasetRecord

FIXED_CONTEXT_SCHEMA_VERSION: Final = 1
FIXED_CONTEXT_ADAPTER_VERSION: Final = "fixed-context-adapter-v1"


class ContextTextKind(StrEnum):
    PRE_TEXT = "pre_text"
    POST_TEXT = "post_text"
    PARAGRAPH = "paragraph"


class EvidenceKind(StrEnum):
    TEXT = "text"
    TABLE_ROW = "table_row"
    TABLE_CELL = "table_cell"
    TEXT_SPAN = "text_span"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ContextText(_FrozenModel):
    kind: ContextTextKind
    index: int = Field(ge=0)
    text: str = Field(min_length=1)


class EvidenceLocator(_FrozenModel):
    kind: EvidenceKind
    index: int | None = Field(default=None, ge=0)
    row: int | None = Field(default=None, ge=0)
    column: int | None = Field(default=None, ge=0)
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _validate_shape(self) -> Self:
        fields = (self.index, self.row, self.column, self.start, self.end)
        if self.kind is EvidenceKind.TEXT:
            valid = self.index is not None and all(value is None for value in fields[1:])
        elif self.kind is EvidenceKind.TABLE_ROW:
            valid = self.row is not None and all(
                value is None for value in (self.index, self.column, self.start, self.end)
            )
        elif self.kind is EvidenceKind.TABLE_CELL:
            valid = (
                self.row is not None
                and self.column is not None
                and all(value is None for value in (self.index, self.start, self.end))
            )
        else:
            valid = (
                self.index is not None
                and self.start is not None
                and self.end is not None
                and self.start < self.end
                and self.row is None
                and self.column is None
            )
        if not valid:
            raise ValueError("Evidence locator fields do not match its kind")
        return self


class FixedContextInput(_FrozenModel):
    schema_version: int = FIXED_CONTEXT_SCHEMA_VERSION
    adapter_version: str = FIXED_CONTEXT_ADAPTER_VERSION
    dataset_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    dataset_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    split: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    case_id: str = Field(min_length=1, max_length=256)
    document_id: str = Field(min_length=1, max_length=256)
    source_artifact_ids: tuple[str, ...]
    question: str = Field(min_length=1)
    texts: tuple[ContextText, ...]
    table: tuple[tuple[str, ...], ...]

    @model_validator(mode="after")
    def _validate_input(self) -> Self:
        if self.schema_version != FIXED_CONTEXT_SCHEMA_VERSION:
            raise ValueError("Fixed-context schema version is invalid")
        if self.adapter_version != FIXED_CONTEXT_ADAPTER_VERSION:
            raise ValueError("Fixed-context adapter version is invalid")
        if not self.question.strip() or not self.source_artifact_ids:
            raise ValueError("Fixed-context question and source artifact ids are required")
        if len(set(self.source_artifact_ids)) != len(self.source_artifact_ids):
            raise ValueError("Fixed-context source artifact ids must be unique")
        text_keys = tuple((block.kind, block.index) for block in self.texts)
        if len(set(text_keys)) != len(text_keys):
            raise ValueError("Fixed-context text locators must be unique")
        if not self.table or not self.table[0]:
            raise ValueError("Fixed-context table must be non-empty")
        width = len(self.table[0])
        if any(len(row) != width for row in self.table):
            raise ValueError("Fixed-context table must be rectangular")
        return self


class FixedContextGold(_FrozenModel):
    answers: tuple[str, ...]
    answer_type: str = Field(min_length=1)
    answer_source: str = ""
    scale: str = ""
    program: str | None = None
    execution_answer: str | None = None
    derivation: str | None = None
    evidence: tuple[EvidenceLocator, ...] = ()
    evidence_complete: bool = False

    @model_validator(mode="after")
    def _validate_gold(self) -> Self:
        if not self.answers or any(not answer.strip() for answer in self.answers):
            raise ValueError("Fixed-context gold answers must be non-empty")
        if self.evidence_complete and not self.evidence:
            raise ValueError("Complete fixed-context Evidence cannot be empty")
        if len(set(self.evidence)) != len(self.evidence):
            raise ValueError("Fixed-context gold Evidence must be unique")
        return self


class FixedContextCase(_FrozenModel):
    input: FixedContextInput
    gold: FixedContextGold


class FixedContextPrediction(_FrozenModel):
    case_id: str = Field(min_length=1, max_length=256)
    answers: tuple[Annotated[str, Field(max_length=4096)], ...] = Field(default=(), max_length=32)
    scale: str = Field(default="", max_length=32)
    program: str | None = Field(default=None, max_length=4096)
    derivation: str | None = Field(default=None, max_length=4096)
    evidence: tuple[EvidenceLocator, ...] = Field(default=(), max_length=128)

    @field_validator("answers")
    @classmethod
    def _validate_answers(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("Prediction answers must be non-empty strings")
        return values

    @model_validator(mode="after")
    def _validate_prediction(self) -> Self:
        if len(set(self.evidence)) != len(self.evidence):
            raise ValueError("Prediction Evidence must be unique")
        return self


class VerifiedArtifact(_FrozenModel):
    artifact_id: str
    path: str
    byte_size: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class FixedContextSplitSummary(_FrozenModel):
    split: str
    input_document_count: int = Field(ge=1)
    input_question_count: int = Field(ge=1)
    scorable_case_count: int = Field(ge=1)
    excluded_question_count: int = Field(ge=0)
    case_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def _validate_counts(self) -> Self:
        if self.scorable_case_count + self.excluded_question_count != self.input_question_count:
            raise ValueError("Fixed-context split counts do not reconcile")
        return self


class AdapterValidationReport(_FrozenModel):
    schema_version: int = 1
    report_kind: str = "adapter_contract"
    adapter_version: str = FIXED_CONTEXT_ADAPTER_VERSION
    dataset_id: str
    dataset_version: str
    model_executed: bool = False
    official_metric_scores: None = None
    artifacts: tuple[VerifiedArtifact, ...]
    splits: tuple[FixedContextSplitSummary, ...]
    limitations: tuple[str, ...] = (
        "This report validates artifact integrity, conversion, and scorer contracts only.",
        "It is not an offline-capability or live-model benchmark result.",
    )

    @model_validator(mode="after")
    def _validate_report(self) -> Self:
        if (
            self.schema_version != 1
            or self.report_kind != "adapter_contract"
            or self.adapter_version != FIXED_CONTEXT_ADAPTER_VERSION
            or self.model_executed
            or self.official_metric_scores is not None
        ):
            raise ValueError("Adapter validation report cannot claim model execution or scores")
        if not self.artifacts or not self.splits:
            raise ValueError("Adapter validation report requires artifacts and splits")
        return self


class FixedContextArtifactStore:
    """Resolve and verify registered artifacts before an adapter reads them."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def path_for(self, record: DatasetRecord, artifact: DatasetArtifact) -> Path:
        relative = Path(*artifact.relative_path.split("/"))
        candidate = (self._root / record.dataset_id / record.upstream_revision / relative).resolve()
        if not candidate.is_relative_to(self._root):
            raise ValueError("Materialized artifact path escapes the configured root")
        return candidate

    def verify(self, record: DatasetRecord, artifact: DatasetArtifact) -> VerifiedArtifact:
        path = self.path_for(record, artifact)
        try:
            stat = path.stat()
        except FileNotFoundError as error:
            raise ValueError(f"Materialized artifact is missing: {artifact.artifact_id}") from error
        if not path.is_file() or stat.st_size != artifact.byte_size:
            raise ValueError(f"Materialized artifact size mismatch: {artifact.artifact_id}")
        digest = _file_sha256(path)
        if digest != artifact.sha256:
            raise ValueError(f"Materialized artifact checksum mismatch: {artifact.artifact_id}")
        return VerifiedArtifact(
            artifact_id=artifact.artifact_id,
            path=path.relative_to(self._root).as_posix(),
            byte_size=stat.st_size,
            sha256=digest,
        )


def require_dataset(record: DatasetRecord, expected_id: str) -> None:
    if record.dataset_id != expected_id:
        raise ValueError(f"Adapter requires dataset record: {expected_id}")


def require_artifact(record: DatasetRecord, artifact_id: str) -> DatasetArtifact:
    artifact = next(
        (candidate for candidate in record.artifacts if candidate.artifact_id == artifact_id),
        None,
    )
    if artifact is None:
        raise ValueError(f"Dataset artifact is not registered: {artifact_id}")
    return artifact


def stable_case_digest(cases: Iterable[FixedContextCase]) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    for case in cases:
        payload = json.dumps(
            case.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        digest.update(len(payload).to_bytes(8, byteorder="big"))
        digest.update(payload)
        count += 1
    return count, digest.hexdigest()


def evidence_metrics(
    predicted: tuple[EvidenceLocator, ...],
    gold: tuple[EvidenceLocator, ...],
) -> tuple[float, float]:
    predicted_set = set(predicted)
    gold_set = set(gold)
    exact = float(predicted_set == gold_set)
    intersection = len(predicted_set & gold_set)
    precision = intersection / len(predicted_set) if predicted_set else 0.0
    recall = intersection / len(gold_set) if gold_set else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision > 0.0 and recall > 0.0 else 0.0
    return exact, f1


def index_predictions(
    cases: tuple[FixedContextCase, ...],
    predictions: tuple[FixedContextPrediction, ...],
) -> dict[str, FixedContextPrediction]:
    case_ids = tuple(case.input.case_id for case in cases)
    prediction_ids = tuple(prediction.case_id for prediction in predictions)
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("Fixed-context cases contain duplicate ids")
    if len(set(prediction_ids)) != len(prediction_ids):
        raise ValueError("Fixed-context predictions contain duplicate ids")
    missing = sorted(set(case_ids) - set(prediction_ids))
    unexpected = sorted(set(prediction_ids) - set(case_ids))
    if missing or unexpected:
        raise ValueError(
            f"Prediction coverage mismatch: missing={missing[:3]!r}, unexpected={unexpected[:3]!r}"
        )
    return {prediction.case_id: prediction for prediction in predictions}


def write_adapter_report(report: AdapterValidationReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
