"""TAT-QA fixed-context adapter and pinned official-metric scorer."""

from __future__ import annotations

import math
import re
import string
from collections.abc import Iterator, Sequence
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from industry_platform.modules.evaluation.fixed_context import (
    ContextText,
    ContextTextKind,
    EvidenceKind,
    EvidenceLocator,
    FixedContextArtifactStore,
    FixedContextCase,
    FixedContextGold,
    FixedContextInput,
    FixedContextPrediction,
    evidence_metrics,
    index_predictions,
    require_artifact,
    require_dataset,
)
from industry_platform.modules.evaluation.release import (
    DatasetArtifact,
    DatasetRecord,
    load_strict_json,
)

TATQA_DATASET_ID: Final = "tat-qa"
TATQA_SCORER_VERSION: Final = (
    "tatqa-metric-870accc41953dcde885aabeb963d94aabdc0fbc3+derivation-source-v1"
)
TATQA_OFFICIAL_METRIC_SHA256: Final = (
    "2aeeac479f89f8c76300af1cc0e8d098eb86af84bc386b38b6ab4af484a6dea8"
)
TATQA_OFFICIAL_UTILS_SHA256: Final = (
    "a84bb2f960737cf0a53733637a674cc4b20ef030a2be6a4b21dc2c4356f415ec"
)

_EXCLUDE_IN_NUMBER: Final = "'\"\\$€£¥%(),[]"
_PUNCTUATION: Final = frozenset(string.punctuation)
_NUMBER_PATTERN: Final = re.compile(r"([+-]?\d+(\.\d+)?)|([+-]?\.\d+)")
_PARENTHETICAL_NUMBER_PATTERN: Final = re.compile(r"(\([\d.\s]+\))")
_PERCENT_PATTERN: Final = re.compile(r"([\d.\s]+%)")
_WORD_SCALE_PATTERN: Final = re.compile(r"([\d.]+\s?[a-zA-Z]+)")
_ARTICLE_PATTERN: Final = re.compile(r"\b(a|an|the)\b", re.UNICODE)
_DERIVATION_SPACE_PATTERN: Final = re.compile(r"\s+")


class _SourceModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)


class _TatQaParagraph(_SourceModel):
    order: int = Field(ge=0)
    text: str = Field(min_length=1)


class _TatQaTable(_SourceModel):
    uid: str = Field(min_length=1)
    table: list[list[str]]


class _TatQaInputQuestion(_SourceModel):
    uid: str = Field(min_length=1)
    order: int = Field(ge=0)
    question: str = Field(min_length=1)


class _TatQaGoldQuestion(_TatQaInputQuestion):
    answer: list[str] | str | int | float
    derivation: str
    answer_type: str = Field(min_length=1)
    answer_from: str = Field(min_length=1)
    rel_paragraphs: list[str]
    scale: str
    mappings: list[dict[str, list[int]]] | None = None


class _TatQaInputDocument(_SourceModel):
    table: _TatQaTable
    paragraphs: list[_TatQaParagraph]
    questions: list[_TatQaInputQuestion]

    @model_validator(mode="after")
    def _validate_document(self) -> Self:
        _validate_context(self.table.table, self.paragraphs, self.questions)
        return self


class _TatQaGoldDocument(_SourceModel):
    table: _TatQaTable
    paragraphs: list[_TatQaParagraph]
    questions: list[_TatQaGoldQuestion]

    @model_validator(mode="after")
    def _validate_document(self) -> Self:
        _validate_context(self.table.table, self.paragraphs, self.questions)
        return self


class TatQaCaseScore(_SourceModel):
    case_id: str
    answer_em: float = Field(ge=0.0, le=1.0)
    answer_f1: float = Field(ge=0.0, le=1.0)
    scale_correct: float = Field(ge=0.0, le=1.0)
    derivation_exact: float | None = Field(default=None, ge=0.0, le=1.0)
    source_exact: float | None = Field(default=None, ge=0.0, le=1.0)
    source_f1: float | None = Field(default=None, ge=0.0, le=1.0)


class TatQaScoreReport(_SourceModel):
    scorer_version: str = TATQA_SCORER_VERSION
    official_metric_sha256: str = TATQA_OFFICIAL_METRIC_SHA256
    official_utils_sha256: str = TATQA_OFFICIAL_UTILS_SHA256
    case_count: int = Field(ge=1)
    answer_em: float = Field(ge=0.0, le=1.0)
    answer_f1: float = Field(ge=0.0, le=1.0)
    scale_accuracy: float = Field(ge=0.0, le=1.0)
    derivation_case_count: int = Field(ge=0)
    derivation_exact: float | None = Field(default=None, ge=0.0, le=1.0)
    source_case_count: int = Field(ge=0)
    source_exact: float | None = Field(default=None, ge=0.0, le=1.0)
    source_f1: float | None = Field(default=None, ge=0.0, le=1.0)
    cases: tuple[TatQaCaseScore, ...]

    @model_validator(mode="after")
    def _validate_report(self) -> Self:
        if (
            self.scorer_version != TATQA_SCORER_VERSION
            or self.official_metric_sha256 != TATQA_OFFICIAL_METRIC_SHA256
            or self.official_utils_sha256 != TATQA_OFFICIAL_UTILS_SHA256
            or len(self.cases) != self.case_count
        ):
            raise ValueError("TAT-QA scorer report identity or denominator is invalid")
        for count, value in (
            (self.derivation_case_count, self.derivation_exact),
            (self.source_case_count, self.source_exact),
            (self.source_case_count, self.source_f1),
        ):
            if (count == 0) != (value is None):
                raise ValueError("TAT-QA auxiliary metric denominator is invalid")
        return self


class TatQaAdapter:
    def __init__(self, record: DatasetRecord, store: FixedContextArtifactStore) -> None:
        require_dataset(record, TATQA_DATASET_ID)
        self._record = record
        self._store = store

    def iter_split(self, split: str) -> Iterator[FixedContextCase]:
        if split == "test":
            yield from self._iter_test()
            return
        if split not in {"train", "dev"}:
            raise ValueError(f"Unsupported TAT-QA split: {split}")
        artifact = require_artifact(self._record, f"tatqa-{split}")
        documents = self._load_gold_documents(artifact)
        self._validate_split_counts(
            artifact,
            documents=len(documents),
            questions=sum(len(document.questions) for document in documents),
        )
        seen: set[str] = set()
        for document_index, document in enumerate(documents):
            for question in document.questions:
                _require_unique_question(question.uid, seen)
                yield self._convert(
                    document=document,
                    document_id=f"tatqa:{split}:document:{document_index}",
                    question=question,
                    split=split,
                    source_artifact_ids=(artifact.artifact_id,),
                )

    def validate_unscored_test_input(self) -> None:
        artifact = require_artifact(self._record, "tatqa-test")
        documents = self._load_input_documents(artifact)
        self._validate_split_counts(
            artifact,
            documents=len(documents),
            questions=sum(len(document.questions) for document in documents),
        )
        seen: set[str] = set()
        for document in documents:
            for question in document.questions:
                _require_unique_question(question.uid, seen)

    def _iter_test(self) -> Iterator[FixedContextCase]:
        gold_artifact = require_artifact(self._record, "tatqa-test-gold")
        gold_documents = self._load_gold_documents(gold_artifact)
        self._validate_split_counts(
            gold_artifact,
            documents=len(gold_documents),
            questions=sum(len(document.questions) for document in gold_documents),
        )

        seen: set[str] = set()
        for document_index, gold_document in enumerate(gold_documents):
            for question in gold_document.questions:
                _require_unique_question(question.uid, seen)
                yield self._convert(
                    document=gold_document,
                    document_id=f"tatqa:test-gold:document:{document_index}",
                    question=question,
                    split="test",
                    source_artifact_ids=(gold_artifact.artifact_id,),
                )

    def _load_input_documents(self, artifact: DatasetArtifact) -> list[_TatQaInputDocument]:
        self._store.verify(self._record, artifact)
        payload = load_strict_json(self._store.path_for(self._record, artifact))
        if not isinstance(payload, list):
            raise ValueError("TAT-QA artifact root must be an array")
        return [_TatQaInputDocument.model_validate(item) for item in payload]

    def _load_gold_documents(self, artifact: DatasetArtifact) -> list[_TatQaGoldDocument]:
        self._store.verify(self._record, artifact)
        payload = load_strict_json(self._store.path_for(self._record, artifact))
        if not isinstance(payload, list):
            raise ValueError("TAT-QA artifact root must be an array")
        return [_TatQaGoldDocument.model_validate(item) for item in payload]

    def _convert(
        self,
        *,
        document: _TatQaGoldDocument,
        document_id: str,
        question: _TatQaGoldQuestion,
        split: str,
        source_artifact_ids: tuple[str, ...],
    ) -> FixedContextCase:
        evidence, evidence_complete = _tatqa_evidence(document, question)
        raw_answers = question.answer if isinstance(question.answer, list) else [question.answer]
        answers = tuple(str(answer) for answer in raw_answers if str(answer).strip())
        return FixedContextCase(
            input=FixedContextInput(
                dataset_id=self._record.dataset_id,
                dataset_version=self._record.dataset_version,
                split=split,
                case_id=f"tatqa:{question.uid}",
                document_id=document_id,
                source_artifact_ids=source_artifact_ids,
                question=question.question,
                texts=tuple(
                    ContextText(
                        kind=ContextTextKind.PARAGRAPH,
                        index=paragraph.order,
                        text=paragraph.text,
                    )
                    for paragraph in document.paragraphs
                ),
                table=tuple(tuple(cell for cell in row) for row in document.table.table),
            ),
            gold=FixedContextGold(
                answers=answers,
                answer_type=question.answer_type,
                answer_source=question.answer_from,
                scale=question.scale,
                derivation=question.derivation,
                evidence=evidence,
                evidence_complete=evidence_complete,
            ),
        )

    @staticmethod
    def _validate_split_counts(
        artifact: DatasetArtifact,
        *,
        documents: int,
        questions: int,
    ) -> None:
        if artifact.document_count != documents or artifact.question_count != questions:
            raise ValueError(f"TAT-QA registered split counts do not match: {artifact.artifact_id}")


def score_tatqa(
    cases: tuple[FixedContextCase, ...],
    predictions: tuple[FixedContextPrediction, ...],
) -> TatQaScoreReport:
    if not cases:
        raise ValueError("TAT-QA scoring requires cases")
    if any(case.input.dataset_id != TATQA_DATASET_ID for case in cases):
        raise ValueError("TAT-QA scorer received a case from another dataset")
    indexed = index_predictions(cases, predictions)
    scores: list[TatQaCaseScore] = []
    for case in cases:
        prediction = indexed[case.input.case_id]
        answer_em, answer_f1 = _tatqa_answer_metrics(case.gold, prediction)
        has_answer = bool(prediction.answers)
        scale_correct = float(has_answer and prediction.scale == case.gold.scale)
        derivation_exact = (
            float(
                prediction.derivation is not None
                and _normalize_derivation(prediction.derivation)
                == _normalize_derivation(case.gold.derivation or "")
            )
            if case.gold.derivation
            else None
        )
        source_exact: float | None = None
        source_f1: float | None = None
        if case.gold.evidence_complete:
            source_exact, source_f1 = evidence_metrics(prediction.evidence, case.gold.evidence)
        scores.append(
            TatQaCaseScore(
                case_id=case.input.case_id,
                answer_em=answer_em,
                answer_f1=answer_f1,
                scale_correct=scale_correct,
                derivation_exact=derivation_exact,
                source_exact=source_exact,
                source_f1=source_f1,
            )
        )

    denominator = len(scores)
    derivation_scores = tuple(
        score.derivation_exact for score in scores if score.derivation_exact is not None
    )
    source_exact_scores = tuple(
        score.source_exact for score in scores if score.source_exact is not None
    )
    source_f1_scores = tuple(score.source_f1 for score in scores if score.source_f1 is not None)
    return TatQaScoreReport(
        case_count=denominator,
        answer_em=sum(score.answer_em for score in scores) / denominator,
        answer_f1=sum(score.answer_f1 for score in scores) / denominator,
        scale_accuracy=sum(score.scale_correct for score in scores) / denominator,
        derivation_case_count=len(derivation_scores),
        derivation_exact=_mean_or_none(derivation_scores),
        source_case_count=len(source_exact_scores),
        source_exact=_mean_or_none(source_exact_scores),
        source_f1=_mean_or_none(source_f1_scores),
        cases=tuple(scores),
    )


def _tatqa_answer_metrics(
    gold: FixedContextGold,
    prediction: FixedContextPrediction,
) -> tuple[float, float]:
    if not prediction.answers:
        return 0.0, 0.0
    gold_strings = _answer_strings(gold.answers, gold.scale)
    prediction_strings = list(_answer_strings(prediction.answers, prediction.scale))
    if (
        len(prediction.answers) == 1
        and not prediction.scale
        and "%" not in prediction.answers[0]
        and _is_number(prediction.answers[0])
    ):
        number = _to_number(prediction.answers[0])
        if number is not None:
            prediction_strings.append(f"{number:.4f}")
    metrics = tuple(
        _drop_metrics(predicted, expected)
        for predicted in prediction_strings
        for expected in gold_strings
    )
    exact_match, f1 = max(metrics) if metrics else (0.0, 0.0)
    if gold.answer_type in {"arithmetic", "count"}:
        f1 = exact_match
    return exact_match, f1


def _answer_strings(answers: Sequence[str], scale: str) -> tuple[str, ...]:
    normalized: list[str] = []
    for answer in sorted(answers):
        answer_string = str(answer)
        if _is_number(answer_string):
            number = _to_number(answer_string)
            if number is None:
                if scale:
                    answer_string = f"{answer_string} {scale}"
            elif "%" in answer_string:
                answer_string = f"{number:.4f}"
            else:
                answer_string = f"{round(number, 2) * _scale_to_number(scale):.4f}"
        elif scale:
            answer_string = f"{answer_string} {scale}"
        normalized.append(answer_string)
    return (" ".join(normalized),)


def _drop_metrics(predicted: str, gold: str) -> tuple[float, float]:
    predicted_spans, predicted_bags = _answer_to_bags(predicted)
    gold_spans, gold_bags = _answer_to_bags(gold)
    exact_match = float(
        set(predicted_spans) == set(gold_spans) and len(predicted_spans) == len(gold_spans)
    )
    score_matrix = tuple(
        tuple(_token_f1(predicted_bag, gold_bag) for predicted_bag in predicted_bags)
        for gold_bag in gold_bags
    )
    size = max(len(gold_bags), len(predicted_bags))
    f1 = round(_maximum_assignment_score(score_matrix) / size, 2) if size else 0.0
    return exact_match, f1


def _answer_to_bags(answer: str | Sequence[str]) -> tuple[list[str], list[set[str]]]:
    raw_spans = [answer] if isinstance(answer, str) else list(answer)
    normalized_spans = [_normalize_answer(str(span)) for span in raw_spans]
    return normalized_spans, [set(span.split()) for span in normalized_spans]


def _token_f1(predicted: set[str], gold: set[str]) -> float:
    intersection = len(gold & predicted)
    precision = intersection / len(predicted) if predicted else 1.0
    recall = intersection / len(gold) if gold else 1.0
    if precision == 0.0 and recall == 0.0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _maximum_assignment_score(scores: tuple[tuple[float, ...], ...]) -> float:
    rows = len(scores)
    columns = max((len(row) for row in scores), default=0)
    size = max(rows, columns)
    if size == 0:
        return 0.0
    matrix = [
        [
            -(scores[row][column] if row < rows and column < len(scores[row]) else 0.0)
            for column in range(size)
        ]
        for row in range(size)
    ]
    potentials_row = [0.0] * (size + 1)
    potentials_column = [0.0] * (size + 1)
    matching = [0] * (size + 1)
    previous = [0] * (size + 1)
    for row in range(1, size + 1):
        matching[0] = row
        column_zero = 0
        minimum = [math.inf] * (size + 1)
        used = [False] * (size + 1)
        while True:
            used[column_zero] = True
            row_zero = matching[column_zero]
            delta = math.inf
            column_one = 0
            for column in range(1, size + 1):
                if used[column]:
                    continue
                current = (
                    matrix[row_zero - 1][column - 1]
                    - potentials_row[row_zero]
                    - potentials_column[column]
                )
                if current < minimum[column]:
                    minimum[column] = current
                    previous[column] = column_zero
                if minimum[column] < delta:
                    delta = minimum[column]
                    column_one = column
            for column in range(size + 1):
                if used[column]:
                    potentials_row[matching[column]] += delta
                    potentials_column[column] -= delta
                else:
                    minimum[column] -= delta
            column_zero = column_one
            if matching[column_zero] == 0:
                break
        while True:
            column_one = previous[column_zero]
            matching[column_zero] = matching[column_one]
            column_zero = column_one
            if column_zero == 0:
                break
    return potentials_column[0]


def _normalize_answer(text: str) -> str:
    parts = [
        _white_space_fix(
            _ARTICLE_PATTERN.sub(
                " ",
                _normalize_number(_remove_punctuation(token.lower())),
            )
        )
        for token in text.split(" ")
    ]
    return " ".join(part for part in parts if part.strip()).strip()


def _remove_punctuation(text: str) -> str:
    return text if _is_number(text) else "".join(ch for ch in text if ch not in _PUNCTUATION)


def _normalize_number(text: str) -> str:
    number = _to_number(text) if _is_number(text) else None
    return str(number) if number is not None else text


def _is_number(text: str) -> bool:
    try:
        words = " ".join(_clean_number(word) for word in text.split()).split()
        if not words:
            return False
        number = float(words[0])
        if math.isnan(number):
            return False
        return len(words) < 2 or _scale_to_number(words[1]) != 1
    except ValueError:
        return False


def _to_number(text: str) -> float | None:
    cleaned = _clean_number(text)
    match = _NUMBER_PATTERN.search(cleaned)
    if match is None or not match.group(0):
        return None
    number = float(match.group(0))
    scale = 1.0
    scale_match = _WORD_SCALE_PATTERN.search(text)
    if scale_match is not None:
        scale = _scale_to_number(scale_match.group(0).lower())
    sign = -1.0 if _PARENTHETICAL_NUMBER_PATTERN.search(text.strip()) else 1.0
    percent = 0.01 if _PERCENT_PATTERN.search(text.strip()) else 1.0
    result = round(number * scale * sign * percent, 4)
    return result if math.isfinite(result) else None


def _scale_to_number(scale: str) -> float:
    normalized = scale.lower()
    if "hundred" in normalized:
        return 100.0
    if "thousand" in normalized:
        return 1_000.0
    if "million" in normalized:
        return 1_000_000.0
    if "billion" in normalized:
        return 1_000_000_000.0
    if "percent" in normalized:
        return 0.01
    return 1.0


def _clean_number(text: str) -> str:
    return "".join(character for character in str(text) if character not in _EXCLUDE_IN_NUMBER)


def _white_space_fix(text: str) -> str:
    return " ".join(text.split())


def _normalize_derivation(value: str) -> str:
    return _DERIVATION_SPACE_PATTERN.sub("", value).lower()


def _tatqa_evidence(
    document: _TatQaGoldDocument,
    question: _TatQaGoldQuestion,
) -> tuple[tuple[EvidenceLocator, ...], bool]:
    paragraphs = {paragraph.order: paragraph for paragraph in document.paragraphs}
    if question.mappings:
        locators: list[EvidenceLocator] = []
        for mapping in question.mappings:
            if len(mapping) != 1:
                raise ValueError(f"TAT-QA mapping is ambiguous: {question.uid}")
            key, coordinates = next(iter(mapping.items()))
            if key == "table":
                if len(coordinates) != 2:
                    raise ValueError(f"TAT-QA table mapping is invalid: {question.uid}")
                row, column = coordinates
                if row >= len(document.table.table) or column >= len(document.table.table[row]):
                    raise ValueError(f"TAT-QA table mapping is out of range: {question.uid}")
                locators.append(
                    EvidenceLocator(kind=EvidenceKind.TABLE_CELL, row=row, column=column)
                )
                continue
            prefix, separator, raw_index = key.rpartition("_")
            if prefix != "paragraph" or not separator or not raw_index.isdigit():
                raise ValueError(f"TAT-QA paragraph mapping is invalid: {question.uid}")
            paragraph_index = int(raw_index)
            paragraph = paragraphs.get(paragraph_index)
            if paragraph is None or len(coordinates) != 2:
                raise ValueError(f"TAT-QA paragraph mapping is out of range: {question.uid}")
            start, end = coordinates
            if start < 0 or end > len(paragraph.text) or start >= end:
                raise ValueError(f"TAT-QA paragraph span is invalid: {question.uid}")
            locators.append(
                EvidenceLocator(
                    kind=EvidenceKind.TEXT_SPAN,
                    index=paragraph_index,
                    start=start,
                    end=end,
                )
            )
        return tuple(dict.fromkeys(locators)), True

    locators = []
    for raw_index in question.rel_paragraphs:
        if not raw_index.isdigit() or int(raw_index) not in paragraphs:
            raise ValueError(f"TAT-QA related paragraph is invalid: {question.uid}")
        locators.append(EvidenceLocator(kind=EvidenceKind.TEXT, index=int(raw_index)))
    return tuple(dict.fromkeys(locators)), False


def _validate_context(
    table: list[list[str]],
    paragraphs: list[_TatQaParagraph],
    questions: Sequence[_TatQaInputQuestion],
) -> None:
    if not table or not table[0] or not questions:
        raise ValueError("TAT-QA context and questions must be non-empty")
    width = len(table[0])
    if any(len(row) != width for row in table):
        raise ValueError("TAT-QA table must be rectangular")
    paragraph_orders = tuple(paragraph.order for paragraph in paragraphs)
    question_ids = tuple(question.uid for question in questions)
    if len(set(paragraph_orders)) != len(paragraph_orders):
        raise ValueError("TAT-QA paragraph orders must be unique")
    if len(set(question_ids)) != len(question_ids):
        raise ValueError("TAT-QA document question ids must be unique")


def _require_unique_question(uid: str, seen: set[str]) -> None:
    if uid in seen:
        raise ValueError(f"TAT-QA split contains duplicate uid: {uid}")
    seen.add(uid)


def _mean_or_none(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None
