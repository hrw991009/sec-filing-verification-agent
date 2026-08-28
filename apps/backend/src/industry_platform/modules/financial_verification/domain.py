"""Fail-closed financial scope and Decimal calculation contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from enum import StrEnum
from types import MappingProxyType
from typing import Final
from uuid import NAMESPACE_URL, UUID, uuid5

FINANCIAL_SCOPE_SCHEMA_VERSION: Final = 1
FINANCIAL_CALCULATION_SCHEMA_VERSION: Final = 1
FINANCIAL_RECONCILIATION_VERSION: Final = "financial-reconciliation-v1"
MAX_FINANCIAL_OPERANDS: Final = 8

_CIK_PATTERN = re.compile(r"^[0-9]{10}$")
_ACCESSION_PATTERN = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
_UNIT_PATTERN = re.compile(r"^[A-Z][A-Z0-9_/-]{0,15}$")


class FinancialForm(StrEnum):
    TEN_K = "10-K"
    TEN_K_AMENDMENT = "10-K/A"
    TEN_Q = "10-Q"
    TEN_Q_AMENDMENT = "10-Q/A"


class FinancialOperator(StrEnum):
    ADD = "add"
    SUBTRACT = "subtract"
    RATIO = "ratio"
    PERCENTAGE = "percentage"
    PERCENT_CHANGE = "percent_change"


class FinancialRoundingMode(StrEnum):
    HALF_EVEN = "half_even"


class FinancialPeriodKind(StrEnum):
    INSTANT = "instant"
    DURATION = "duration"
    FOREVER = "forever"


class FinancialReconciliationStatus(StrEnum):
    CONSISTENT = "consistent"
    CONFLICT = "conflict"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NOT_COMPARABLE = "not_comparable"


class FinancialReconciliationIssueCode(StrEnum):
    SCOPE_MISMATCH = "scope_mismatch"
    FUTURE_SOURCE = "future_source"
    UNIT_MISSING = "unit_missing"
    UNIT_MISMATCH = "unit_mismatch"
    REPORT_PERIOD_MISMATCH = "report_period_mismatch"
    PERIOD_KIND_MISMATCH = "period_kind_mismatch"
    PERIOD_NOT_COMPARABLE = "period_not_comparable"
    DIMENSIONS_MISMATCH = "dimensions_mismatch"
    CONCEPT_MISMATCH = "concept_mismatch"
    AMENDMENT_UNRESOLVED = "amendment_unresolved"


@dataclass(frozen=True, slots=True)
class FinancialScope:
    cik: str
    accession: str
    form: FinancialForm
    report_period: date
    as_of: datetime
    unit: str
    scale: int
    schema_version: int = FINANCIAL_SCOPE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != FINANCIAL_SCOPE_SCHEMA_VERSION:
            raise ValueError("Financial Scope schema version is unsupported")
        if not _CIK_PATTERN.fullmatch(self.cik):
            raise ValueError("Financial Scope CIK is invalid")
        if not _ACCESSION_PATTERN.fullmatch(self.accession):
            raise ValueError("Financial Scope accession is invalid")
        if not isinstance(self.form, FinancialForm):
            raise ValueError("Financial Scope form is invalid")
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("Financial Scope as_of must be timezone-aware")
        if not _UNIT_PATTERN.fullmatch(self.unit):
            raise ValueError("Financial Scope unit is invalid")
        if isinstance(self.scale, bool) or not -12 <= self.scale <= 12:
            raise ValueError("Financial Scope scale is invalid")

    def to_mapping(self) -> MappingProxyType[str, object]:
        return MappingProxyType(
            {
                "schema_version": self.schema_version,
                "cik": self.cik,
                "accession": self.accession,
                "form": self.form.value,
                "report_period": self.report_period.isoformat(),
                "as_of": self.as_of.isoformat(),
                "unit": self.unit,
                "scale": self.scale,
            }
        )

    @classmethod
    def from_mapping(cls, value: dict[str, object]) -> FinancialScope:
        expected = {
            "schema_version",
            "cik",
            "accession",
            "form",
            "report_period",
            "as_of",
            "unit",
            "scale",
        }
        if set(value) != expected:
            raise ValueError("Financial Scope fields are invalid")
        schema_version = value["schema_version"]
        scale = value["scale"]
        if (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or isinstance(scale, bool)
            or not isinstance(scale, int)
        ):
            raise ValueError("Financial Scope numeric fields are invalid")
        try:
            return cls(
                schema_version=schema_version,
                cik=str(value["cik"]),
                accession=str(value["accession"]),
                form=FinancialForm(str(value["form"])),
                report_period=date.fromisoformat(str(value["report_period"])),
                as_of=datetime.fromisoformat(str(value["as_of"])),
                unit=str(value["unit"]),
                scale=scale,
            )
        except (TypeError, ValueError):
            raise ValueError("Financial Scope is invalid") from None


@dataclass(frozen=True, slots=True)
class FinancialOperand:
    value: str
    evidence_ref: UUID
    unit: str | None = None
    scale: int | None = None

    def __post_init__(self) -> None:
        if self.evidence_ref.int == 0:
            raise ValueError("Financial operand Evidence ref is invalid")
        try:
            parsed = Decimal(self.value)
        except InvalidOperation:
            raise ValueError("Financial operand value is invalid") from None
        if not parsed.is_finite() or self.value != format(parsed, "f"):
            raise ValueError("Financial operand value must be a canonical finite Decimal")
        if self.unit is not None and not _UNIT_PATTERN.fullmatch(self.unit):
            raise ValueError("Financial operand unit is invalid")
        if self.scale is not None and (isinstance(self.scale, bool) or not -12 <= self.scale <= 12):
            raise ValueError("Financial operand scale is invalid")


@dataclass(frozen=True, slots=True)
class FinancialEvidenceOperand:
    evidence_ref: UUID
    source_fact_id: UUID
    value: str
    cik: str
    accession: str
    form: FinancialForm
    report_period: date
    unit: str | None
    scale: int
    period_kind: FinancialPeriodKind
    instant: date | None
    start_date: date | None
    end_date: date | None
    context_id: str | None
    dimensions: tuple[tuple[str, str], ...]
    taxonomy: str
    concept: str
    is_custom: bool
    source_kind: str
    source_version: str
    source_available_at: datetime
    amendment_relation_status: str
    base_accession: str | None

    def __post_init__(self) -> None:
        FinancialOperand(
            value=self.value,
            evidence_ref=self.evidence_ref,
            unit=self.unit,
            scale=self.scale,
        )
        if self.source_fact_id.int == 0:
            raise ValueError("Financial Evidence source fact is invalid")
        if not _CIK_PATTERN.fullmatch(self.cik) or not _ACCESSION_PATTERN.fullmatch(self.accession):
            raise ValueError("Financial Evidence filing identity is invalid")
        if not isinstance(self.form, FinancialForm):
            raise ValueError("Financial Evidence form is invalid")
        if not isinstance(self.period_kind, FinancialPeriodKind):
            raise ValueError("Financial Evidence period kind is invalid")
        if self.period_kind is FinancialPeriodKind.INSTANT:
            period_valid = (
                self.instant is not None and self.start_date is None and self.end_date is None
            )
        elif self.period_kind is FinancialPeriodKind.DURATION:
            period_valid = (
                self.instant is None
                and self.start_date is not None
                and self.end_date is not None
                and self.end_date >= self.start_date
            )
        else:
            period_valid = (
                self.instant is None and self.start_date is None and self.end_date is None
            )
        if not period_valid:
            raise ValueError("Financial Evidence period is invalid")
        dimensions = tuple(sorted(self.dimensions))
        if len(dimensions) > 64 or len({name for name, _value in dimensions}) != len(dimensions):
            raise ValueError("Financial Evidence dimensions are invalid")
        if any(not name.strip() or not value.strip() for name, value in dimensions):
            raise ValueError("Financial Evidence dimension value is invalid")
        for value, field_name, maximum in (
            (self.taxonomy, "taxonomy", 128),
            (self.concept, "concept", 256),
            (self.source_kind, "source kind", 64),
            (self.source_version, "source version", 128),
        ):
            if not value.strip() or len(value) > maximum:
                raise ValueError(f"Financial Evidence {field_name} is invalid")
        if self.context_id is not None and (
            not self.context_id.strip() or len(self.context_id) > 255
        ):
            raise ValueError("Financial Evidence context is invalid")
        if self.source_available_at.tzinfo is None or self.source_available_at.utcoffset() is None:
            raise ValueError("Financial Evidence source availability must be timezone-aware")
        if self.amendment_relation_status not in {
            "not_amendment",
            "resolved",
            "unresolved",
        }:
            raise ValueError("Financial Evidence amendment status is invalid")
        if self.amendment_relation_status == "resolved":
            if self.base_accession is None or not _ACCESSION_PATTERN.fullmatch(self.base_accession):
                raise ValueError("Financial Evidence base accession is invalid")
        elif self.base_accession is not None:
            raise ValueError("Financial Evidence base accession is unexpected")
        object.__setattr__(self, "dimensions", dimensions)

    @property
    def period_key(self) -> str:
        if self.period_kind is FinancialPeriodKind.INSTANT:
            if self.instant is None:
                raise AssertionError("Validated instant operand lost its date")
            return f"instant:{self.instant.isoformat()}"
        if self.period_kind is FinancialPeriodKind.DURATION:
            if self.start_date is None or self.end_date is None:
                raise AssertionError("Validated duration operand lost its dates")
            return f"duration:{self.start_date.isoformat()}:{self.end_date.isoformat()}"
        return "forever"

    @property
    def period_anchor(self) -> date | None:
        return self.instant if self.period_kind is FinancialPeriodKind.INSTANT else self.end_date


@dataclass(frozen=True, slots=True)
class FinancialReconciliationIssue:
    code: FinancialReconciliationIssueCode
    evidence_refs: tuple[UUID, ...]

    def __post_init__(self) -> None:
        references = tuple(dict.fromkeys(self.evidence_refs))
        if not references or any(reference.int == 0 for reference in references):
            raise ValueError("Financial reconciliation issue Evidence refs are invalid")
        object.__setattr__(self, "evidence_refs", references)


@dataclass(frozen=True, slots=True)
class FinancialReconciliationResult:
    status: FinancialReconciliationStatus
    issues: tuple[FinancialReconciliationIssue, ...]
    evidence_refs: tuple[UUID, ...]
    version: str = FINANCIAL_RECONCILIATION_VERSION

    def __post_init__(self) -> None:
        issues = tuple(self.issues)
        references = tuple(dict.fromkeys(self.evidence_refs))
        if self.version != FINANCIAL_RECONCILIATION_VERSION:
            raise ValueError("Financial reconciliation version is unsupported")
        if not references or any(reference.int == 0 for reference in references):
            raise ValueError("Financial reconciliation Evidence refs are invalid")
        if (self.status is FinancialReconciliationStatus.CONSISTENT) != (not issues):
            raise ValueError("Financial reconciliation status is inconsistent")
        object.__setattr__(self, "issues", issues)
        object.__setattr__(self, "evidence_refs", references)


def sec_xbrl_evidence_ref(
    *,
    workspace_id: UUID,
    fact_id: UUID,
    as_of: datetime,
    authorization_role: str,
) -> UUID:
    if (
        workspace_id.int == 0
        or fact_id.int == 0
        or as_of.tzinfo is None
        or as_of.utcoffset() is None
        or not authorization_role.strip()
        or len(authorization_role) > 64
    ):
        raise ValueError("SEC XBRL Evidence identity is invalid")
    return uuid5(
        NAMESPACE_URL,
        (f"{workspace_id}:sec-xbrl-fact-v1:{fact_id}:{as_of.isoformat()}:{authorization_role}"),
    )


def reconcile_financial_operands(
    scope: FinancialScope,
    operator: FinancialOperator,
    operands: tuple[FinancialEvidenceOperand, ...],
) -> FinancialReconciliationResult:
    """Compare authorized SEC operands before any arithmetic is attempted."""

    selected = tuple(operands)
    if not 2 <= len(selected) <= MAX_FINANCIAL_OPERANDS:
        raise ValueError("Financial reconciliation operands are invalid")
    references = tuple(item.evidence_ref for item in selected)
    issues: list[FinancialReconciliationIssue] = []

    def add_issue(code: FinancialReconciliationIssueCode, affected: tuple[UUID, ...]) -> None:
        issue = FinancialReconciliationIssue(code=code, evidence_refs=affected)
        if issue not in issues:
            issues.append(issue)

    for item in selected:
        if (
            item.cik != scope.cik
            or item.accession != scope.accession
            or item.form is not scope.form
            or item.report_period != scope.report_period
        ):
            add_issue(FinancialReconciliationIssueCode.SCOPE_MISMATCH, (item.evidence_ref,))
        if item.source_available_at > scope.as_of:
            add_issue(FinancialReconciliationIssueCode.FUTURE_SOURCE, (item.evidence_ref,))
        if item.unit is None:
            add_issue(FinancialReconciliationIssueCode.UNIT_MISSING, (item.evidence_ref,))
        elif item.unit != scope.unit:
            add_issue(FinancialReconciliationIssueCode.UNIT_MISMATCH, (item.evidence_ref,))
        if item.amendment_relation_status == "unresolved" or (
            item.form in {FinancialForm.TEN_K_AMENDMENT, FinancialForm.TEN_Q_AMENDMENT}
            and item.amendment_relation_status != "resolved"
        ):
            add_issue(
                FinancialReconciliationIssueCode.AMENDMENT_UNRESOLVED,
                (item.evidence_ref,),
            )

    first = selected[0]
    if first.period_anchor != scope.report_period:
        add_issue(
            FinancialReconciliationIssueCode.REPORT_PERIOD_MISMATCH,
            (first.evidence_ref,),
        )
    if any(item.period_kind is FinancialPeriodKind.FOREVER for item in selected):
        add_issue(FinancialReconciliationIssueCode.PERIOD_NOT_COMPARABLE, references)
    if len({item.period_kind for item in selected}) != 1:
        add_issue(FinancialReconciliationIssueCode.PERIOD_KIND_MISMATCH, references)
    if len({item.dimensions for item in selected}) != 1:
        add_issue(FinancialReconciliationIssueCode.DIMENSIONS_MISMATCH, references)

    period_keys = {item.period_key for item in selected}
    if operator is FinancialOperator.PERCENT_CHANGE:
        concepts = {(item.taxonomy, item.concept, item.is_custom) for item in selected}
        if len(concepts) != 1:
            add_issue(FinancialReconciliationIssueCode.CONCEPT_MISMATCH, references)
        if len(period_keys) != len(selected):
            add_issue(FinancialReconciliationIssueCode.PERIOD_NOT_COMPARABLE, references)
        if all(item.period_kind is FinancialPeriodKind.DURATION for item in selected):
            durations = {
                (item.end_date - item.start_date).days
                for item in selected
                if item.start_date is not None and item.end_date is not None
            }
            if len(durations) != 1:
                add_issue(FinancialReconciliationIssueCode.PERIOD_NOT_COMPARABLE, references)
    elif len(period_keys) != 1:
        add_issue(FinancialReconciliationIssueCode.PERIOD_NOT_COMPARABLE, references)

    conflict_codes = {
        FinancialReconciliationIssueCode.SCOPE_MISMATCH,
        FinancialReconciliationIssueCode.FUTURE_SOURCE,
        FinancialReconciliationIssueCode.UNIT_MISMATCH,
        FinancialReconciliationIssueCode.REPORT_PERIOD_MISMATCH,
        FinancialReconciliationIssueCode.AMENDMENT_UNRESOLVED,
    }
    insufficient_codes = {FinancialReconciliationIssueCode.UNIT_MISSING}
    codes = {issue.code for issue in issues}
    if not issues:
        status = FinancialReconciliationStatus.CONSISTENT
    elif codes & conflict_codes:
        status = FinancialReconciliationStatus.CONFLICT
    elif codes & insufficient_codes:
        status = FinancialReconciliationStatus.INSUFFICIENT_EVIDENCE
    else:
        status = FinancialReconciliationStatus.NOT_COMPARABLE
    return FinancialReconciliationResult(
        status=status,
        issues=tuple(issues),
        evidence_refs=references,
    )


@dataclass(frozen=True, slots=True)
class FinancialCalculation:
    operator: FinancialOperator
    operands: tuple[FinancialOperand, ...]
    decimal_places: int
    rounding_mode: FinancialRoundingMode = FinancialRoundingMode.HALF_EVEN
    schema_version: int = FINANCIAL_CALCULATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != FINANCIAL_CALCULATION_SCHEMA_VERSION:
            raise ValueError("Financial calculation schema version is unsupported")
        if not isinstance(self.operator, FinancialOperator):
            raise ValueError("Financial calculation operator is invalid")
        operands = tuple(self.operands)
        expected_count = 2 if self.operator is not FinancialOperator.ADD else None
        if not 2 <= len(operands) <= MAX_FINANCIAL_OPERANDS or (
            expected_count is not None and len(operands) != expected_count
        ):
            raise ValueError("Financial calculation operands are invalid")
        if isinstance(self.decimal_places, bool) or not 0 <= self.decimal_places <= 12:
            raise ValueError("Financial calculation decimal places are invalid")
        if self.rounding_mode is not FinancialRoundingMode.HALF_EVEN:
            raise ValueError("Financial calculation rounding mode is unsupported")
        object.__setattr__(self, "operands", operands)


@dataclass(frozen=True, slots=True)
class FinancialCalculationResult:
    value: str
    formula: str
    unit: str
    scale: int
    evidence_refs: tuple[UUID, ...]


def calculate_financial_result(
    scope: FinancialScope,
    calculation: FinancialCalculation,
) -> FinancialCalculationResult:
    """Apply one allowlisted Decimal operation with deterministic rounding."""

    for item in calculation.operands:
        if item.unit is not None and item.unit != scope.unit:
            raise ValueError("Financial calculation operand unit conflicts with scope")
    values = tuple(
        Decimal(item.value).scaleb((item.scale or 0) - scope.scale)
        if item.scale is not None
        else Decimal(item.value)
        for item in calculation.operands
    )
    rendered_values = tuple(format(item, "f") for item in values)
    with localcontext() as context:
        context.prec = 50
        if calculation.operator is FinancialOperator.ADD:
            raw = sum(values, Decimal(0))
            formula = " + ".join(rendered_values)
            unit, scale = scope.unit, scope.scale
        elif calculation.operator is FinancialOperator.SUBTRACT:
            raw = values[0] - values[1]
            formula = f"{rendered_values[0]} - {rendered_values[1]}"
            unit, scale = scope.unit, scope.scale
        elif calculation.operator is FinancialOperator.RATIO:
            if values[1] == 0:
                raise ValueError("Financial calculation division by zero")
            raw = values[0] / values[1]
            formula = f"{rendered_values[0]} / {rendered_values[1]}"
            unit, scale = "RATIO", 0
        elif calculation.operator is FinancialOperator.PERCENTAGE:
            if values[1] == 0:
                raise ValueError("Financial calculation division by zero")
            raw = (values[0] / values[1]) * Decimal(100)
            formula = f"({rendered_values[0]} / {rendered_values[1]}) * 100"
            unit, scale = "PERCENT", 0
        else:
            if values[1] == 0:
                raise ValueError("Financial calculation division by zero")
            raw = ((values[0] - values[1]) / values[1]) * Decimal(100)
            formula = (
                f"(({rendered_values[0]} - {rendered_values[1]}) / {rendered_values[1]}) * 100"
            )
            unit, scale = "PERCENT", 0
        quantum = Decimal(1).scaleb(-calculation.decimal_places)
        rounded = raw.quantize(quantum, rounding=ROUND_HALF_EVEN)
    return FinancialCalculationResult(
        value=format(rounded, "f"),
        formula=formula,
        unit=unit,
        scale=scale,
        evidence_refs=tuple(item.evidence_ref for item in calculation.operands),
    )
