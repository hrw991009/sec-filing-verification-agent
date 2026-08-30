"""FinQA fixed-context adapter and pinned official-metric scorer."""

from __future__ import annotations

import math
from collections.abc import Iterator
from typing import Final, Self, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sympy import simplify

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

FINQA_DATASET_ID: Final = "finqa"
FINQA_SCORER_VERSION: Final = (
    "finqa-evaluate-0f16e2867befa6840783e58be38c9efb9229d742+supporting-facts-v1"
)
FINQA_OFFICIAL_SCORER_SHA256: Final = (
    "845cd131cab843eceff256cf6d392978cc470a7da4a80107beb56027fdca5c13"
)

_ARITHMETIC_OPERATIONS: Final = frozenset(
    {"add", "subtract", "multiply", "divide", "exp", "greater"}
)
_TABLE_OPERATIONS: Final = frozenset({"table_max", "table_min", "table_sum", "table_average"})
_OPERATIONS: Final = _ARITHMETIC_OPERATIONS | _TABLE_OPERATIONS


class _SourceModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)


class _FinQaAnnotation(_SourceModel):
    question: str = Field(min_length=1)
    answer: str | int | float
    program: str = Field(min_length=1)
    gold_inds: dict[str, str]
    exe_ans: str | int | float


class _FinQaDocument(_SourceModel):
    pre_text: list[str]
    post_text: list[str]
    table: list[list[str]]
    qa: _FinQaAnnotation
    id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_document(self) -> Self:
        if not self.table or not self.table[0]:
            raise ValueError("FinQA table must be non-empty")
        width = len(self.table[0])
        if any(len(row) != width for row in self.table):
            raise ValueError("FinQA table must be rectangular")
        return self


class FinQaCaseScore(_SourceModel):
    case_id: str
    execution_correct: float = Field(ge=0.0, le=1.0)
    program_correct: float = Field(ge=0.0, le=1.0)
    supporting_fact_exact: float | None = Field(default=None, ge=0.0, le=1.0)
    supporting_fact_f1: float | None = Field(default=None, ge=0.0, le=1.0)
    invalid_program: bool


class FinQaScoreReport(_SourceModel):
    scorer_version: str = FINQA_SCORER_VERSION
    official_scorer_sha256: str = FINQA_OFFICIAL_SCORER_SHA256
    case_count: int = Field(ge=1)
    execution_accuracy: float = Field(ge=0.0, le=1.0)
    program_accuracy: float = Field(ge=0.0, le=1.0)
    supporting_fact_case_count: int = Field(ge=0)
    supporting_fact_exact: float | None = Field(default=None, ge=0.0, le=1.0)
    supporting_fact_f1: float | None = Field(default=None, ge=0.0, le=1.0)
    invalid_program_rate: float = Field(ge=0.0, le=1.0)
    cases: tuple[FinQaCaseScore, ...]

    @model_validator(mode="after")
    def _validate_report(self) -> Self:
        if (
            self.scorer_version != FINQA_SCORER_VERSION
            or self.official_scorer_sha256 != FINQA_OFFICIAL_SCORER_SHA256
            or len(self.cases) != self.case_count
        ):
            raise ValueError("FinQA scorer report identity or denominator is invalid")
        for value in (self.supporting_fact_exact, self.supporting_fact_f1):
            if (self.supporting_fact_case_count == 0) != (value is None):
                raise ValueError("FinQA supporting-fact denominator is invalid")
        return self


class FinQaAdapter:
    def __init__(self, record: DatasetRecord, store: FixedContextArtifactStore) -> None:
        require_dataset(record, FINQA_DATASET_ID)
        self._record = record
        self._store = store

    def iter_split(self, split: str) -> Iterator[FixedContextCase]:
        artifact = require_artifact(self._record, f"finqa-{split}")
        self._store.verify(self._record, artifact)
        payload = load_strict_json(self._store.path_for(self._record, artifact))
        if not isinstance(payload, list):
            raise ValueError("FinQA artifact root must be an array")
        self._validate_split_counts(artifact, documents=len(payload), questions=len(payload))

        seen: set[str] = set()
        for raw_document in payload:
            document = _FinQaDocument.model_validate(raw_document)
            if document.id in seen:
                raise ValueError(f"FinQA split contains duplicate id: {document.id}")
            seen.add(document.id)
            yield self._convert(document, artifact)

    def _convert(self, document: _FinQaDocument, artifact: DatasetArtifact) -> FixedContextCase:
        answer = str(document.qa.answer)
        if not answer.strip():
            answer = str(document.qa.exe_ans)
        texts = tuple(
            ContextText(kind=ContextTextKind.PRE_TEXT, index=index, text=text)
            for index, text in enumerate(document.pre_text)
        ) + tuple(
            ContextText(
                kind=ContextTextKind.POST_TEXT,
                index=len(document.pre_text) + index,
                text=text,
            )
            for index, text in enumerate(document.post_text)
        )
        evidence, evidence_complete = self._convert_evidence(document)
        return FixedContextCase(
            input=FixedContextInput(
                dataset_id=self._record.dataset_id,
                dataset_version=self._record.dataset_version,
                split=artifact.split,
                case_id=f"finqa:{document.id}",
                document_id=document.id,
                source_artifact_ids=(artifact.artifact_id,),
                question=document.qa.question,
                texts=texts,
                table=tuple(tuple(cell for cell in row) for row in document.table),
            ),
            gold=FixedContextGold(
                answers=(answer,),
                answer_type="numerical_reasoning",
                program=document.qa.program,
                execution_answer=str(document.qa.exe_ans),
                evidence=evidence,
                evidence_complete=evidence_complete,
            ),
        )

    @staticmethod
    def _convert_evidence(
        document: _FinQaDocument,
    ) -> tuple[tuple[EvidenceLocator, ...], bool]:
        combined_text = document.pre_text + document.post_text
        evidence: list[EvidenceLocator] = []
        complete = True
        for key, expected_text in document.qa.gold_inds.items():
            if key == "text_-1":
                complete = False
                continue
            prefix, separator, raw_index = key.rpartition("_")
            if not separator or not raw_index.isdigit():
                raise ValueError(f"FinQA supporting-fact key is invalid: {key}")
            index = int(raw_index)
            if prefix == "text":
                if index >= len(combined_text) or combined_text[index] != expected_text:
                    raise ValueError(f"FinQA text supporting fact does not match context: {key}")
                evidence.append(EvidenceLocator(kind=EvidenceKind.TEXT, index=index))
            elif prefix == "table":
                if index >= len(document.table):
                    raise ValueError(f"FinQA table supporting fact is out of range: {key}")
                evidence.append(EvidenceLocator(kind=EvidenceKind.TABLE_ROW, row=index))
            else:
                raise ValueError(f"FinQA supporting-fact kind is invalid: {key}")
        return tuple(evidence), complete and bool(evidence)

    @staticmethod
    def _validate_split_counts(
        artifact: DatasetArtifact,
        *,
        documents: int,
        questions: int,
    ) -> None:
        if artifact.document_count != documents or artifact.question_count != questions:
            raise ValueError(f"FinQA registered split counts do not match: {artifact.artifact_id}")


def score_finqa(
    cases: tuple[FixedContextCase, ...],
    predictions: tuple[FixedContextPrediction, ...],
) -> FinQaScoreReport:
    if not cases:
        raise ValueError("FinQA scoring requires cases")
    if any(case.input.dataset_id != FINQA_DATASET_ID for case in cases):
        raise ValueError("FinQA scorer received a case from another dataset")
    indexed = index_predictions(cases, predictions)
    scores: list[FinQaCaseScore] = []
    for case in cases:
        prediction = indexed[case.input.case_id]
        execution_result = evaluate_finqa_program(prediction.program, case.input.table)
        execution_correct = float(
            execution_result is not None
            and _execution_matches(execution_result, case.gold.execution_answer)
        )
        program_correct = float(
            prediction.program is not None
            and case.gold.program is not None
            and finqa_programs_equal(case.gold.program, prediction.program)
        )
        supporting_exact: float | None = None
        supporting_f1: float | None = None
        if case.gold.evidence_complete:
            supporting_exact, supporting_f1 = evidence_metrics(
                prediction.evidence, case.gold.evidence
            )
        scores.append(
            FinQaCaseScore(
                case_id=case.input.case_id,
                execution_correct=execution_correct,
                program_correct=program_correct,
                supporting_fact_exact=supporting_exact,
                supporting_fact_f1=supporting_f1,
                invalid_program=execution_result is None,
            )
        )
    denominator = len(scores)
    supporting_exact_scores = tuple(
        score.supporting_fact_exact for score in scores if score.supporting_fact_exact is not None
    )
    supporting_f1_scores = tuple(
        score.supporting_fact_f1 for score in scores if score.supporting_fact_f1 is not None
    )
    return FinQaScoreReport(
        case_count=denominator,
        execution_accuracy=sum(score.execution_correct for score in scores) / denominator,
        program_accuracy=sum(score.program_correct for score in scores) / denominator,
        supporting_fact_case_count=len(supporting_exact_scores),
        supporting_fact_exact=_mean_or_none(supporting_exact_scores),
        supporting_fact_f1=_mean_or_none(supporting_f1_scores),
        invalid_program_rate=sum(score.invalid_program for score in scores) / denominator,
        cases=tuple(scores),
    )


def evaluate_finqa_program(
    program: str | None,
    table: tuple[tuple[str, ...], ...],
) -> float | str | None:
    if program is None:
        return None
    operations = _parse_program(program)
    if operations is None:
        return None
    results: list[float | str] = []
    rows = {row[0]: row[1:] for row in table}
    try:
        for operation, raw_left, raw_right in operations:
            if operation in _ARITHMETIC_OPERATIONS:
                left = _resolve_number(raw_left, results)
                right = _resolve_number(raw_right, results)
                if left is None or right is None:
                    return None
                if operation == "add":
                    result: float | str = left + right
                elif operation == "subtract":
                    result = left - right
                elif operation == "multiply":
                    result = left * right
                elif operation == "divide":
                    result = left / right
                elif operation == "exp":
                    result = left**right
                else:
                    result = "yes" if left > right else "no"
            else:
                if raw_left.startswith("#") or raw_left not in rows:
                    return None
                values = tuple(_table_number(value) for value in rows[raw_left])
                if not values or any(value is None for value in values):
                    return None
                numbers = cast(tuple[float, ...], values)
                if operation == "table_max":
                    result = max(numbers)
                elif operation == "table_min":
                    result = min(numbers)
                elif operation == "table_sum":
                    result = sum(numbers)
                else:
                    result = sum(numbers) / len(numbers)
            if isinstance(result, float) and not math.isfinite(result):
                return None
            results.append(result)
    except (ArithmeticError, OverflowError, ValueError):
        return None
    final = results[-1]
    return round(final, 5) if isinstance(final, float) else final


def finqa_programs_equal(gold_program: str, predicted_program: str) -> bool:
    gold = _parse_program(gold_program)
    predicted = _parse_program(predicted_program)
    if gold is None or predicted is None:
        return False

    symbols: dict[tuple[str, ...], str] = {}
    symbol_index = 0
    for operation, left, right in gold:
        operands = (
            (("table", operation, left, right),)
            if operation in _TABLE_OPERATIONS
            else tuple(("literal", value) for value in (left, right) if not value.startswith("#"))
        )
        for operand in operands:
            if operand not in symbols:
                symbols[operand] = f"a{symbol_index}"
                symbol_index += 1

    gold_expression = _symbolic_expression(gold, symbols)
    predicted_expression = _symbolic_expression(predicted, symbols)
    if gold_expression is None or predicted_expression is None:
        return False
    try:
        return bool(
            simplify(gold_expression, evaluate=False)
            == simplify(predicted_expression, evaluate=False)
        )
    except (ArithmeticError, TypeError, ValueError):
        return False


def _symbolic_expression(
    operations: tuple[tuple[str, str, str], ...],
    symbols: dict[tuple[str, ...], str],
) -> str | None:
    expressions: list[str] = []
    for index, (operation, left, right) in enumerate(operations):
        if operation in _TABLE_OPERATIONS:
            expression = symbols.get(("table", operation, left, right))
            if expression is None:
                return None
        else:
            left_expression = _symbolic_operand(left, expressions, symbols, index)
            right_expression = _symbolic_operand(right, expressions, symbols, index)
            if left_expression is None or right_expression is None:
                return None
            operator = {
                "add": "+",
                "subtract": "-",
                "multiply": "*",
                "divide": "/",
                "exp": "**",
                "greater": ">",
            }[operation]
            expression = f"({left_expression} {operator} {right_expression})"
        expressions.append(expression)
    return expressions[-1] if expressions else None


def _symbolic_operand(
    value: str,
    expressions: list[str],
    symbols: dict[tuple[str, ...], str],
    current_index: int,
) -> str | None:
    if value.startswith("#"):
        raw_index = value[1:]
        if not raw_index.isdigit() or int(raw_index) >= current_index:
            return None
        return expressions[int(raw_index)]
    return symbols.get(("literal", value))


def _parse_program(program: str) -> tuple[tuple[str, str, str], ...] | None:
    tokens: list[str] = []
    for raw_token in program.split(", "):
        current = ""
        for character in raw_token:
            if character == ")" and current:
                tokens.append(current)
                current = ""
            current += character
            if character in {"(", ")"}:
                tokens.append(current)
                current = ""
        if current:
            tokens.append(current)
    if not tokens or len(tokens) % 4 != 0:
        return None
    operations: list[tuple[str, str, str]] = []
    for index in range(0, len(tokens), 4):
        operation_token, left, right, closing = tokens[index : index + 4]
        if not operation_token.endswith("(") or closing != ")":
            return None
        operation = operation_token[:-1]
        if operation not in _OPERATIONS or not left.strip() or not right.strip():
            return None
        operations.append((operation, left.strip(), right.strip()))
    return tuple(operations)


def _resolve_number(value: str, results: list[float | str]) -> float | None:
    if value.startswith("#"):
        raw_index = value[1:]
        if not raw_index.isdigit() or int(raw_index) >= len(results):
            return None
        result = results[int(raw_index)]
        return result if isinstance(result, float) else None
    return _to_number(value)


def _to_number(value: str) -> float | None:
    normalized = value.replace(",", "")
    if normalized.startswith("const_"):
        normalized = normalized.removeprefix("const_")
        if normalized == "m1":
            normalized = "-1"
    percentage = normalized.endswith("%")
    if percentage:
        normalized = normalized[:-1]
    try:
        number = float(normalized)
    except ValueError:
        return None
    if percentage:
        number /= 100.0
    return number if math.isfinite(number) else None


def _table_number(value: str) -> float | None:
    normalized = value.replace("$", "").strip().split("(", maxsplit=1)[0].strip()
    return _to_number(normalized)


def _execution_matches(result: float | str, gold: str | None) -> bool:
    if gold is None:
        return False
    if isinstance(result, str):
        return result == gold
    expected = _to_number(gold)
    return expected is not None and result == expected


def _mean_or_none(values: tuple[float, ...]) -> float | None:
    return sum(values) / len(values) if values else None
