"""Fail-closed financial scope and Decimal calculation contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from enum import StrEnum
from types import MappingProxyType
from typing import Final
from uuid import UUID

FINANCIAL_SCOPE_SCHEMA_VERSION: Final = 1
FINANCIAL_CALCULATION_SCHEMA_VERSION: Final = 1
MAX_FINANCIAL_OPERANDS: Final = 8

_CIK_PATTERN = re.compile(r"^[0-9]{10}$")
_ACCESSION_PATTERN = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
_UNIT_PATTERN = re.compile(r"^[A-Z][A-Z0-9_/-]{0,15}$")


class FinancialForm(StrEnum):
    TEN_K = "10-K"
    TEN_Q = "10-Q"


class FinancialOperator(StrEnum):
    ADD = "add"
    SUBTRACT = "subtract"
    RATIO = "ratio"
    PERCENT_CHANGE = "percent_change"


class FinancialRoundingMode(StrEnum):
    HALF_EVEN = "half_even"


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

    def __post_init__(self) -> None:
        if self.evidence_ref.int == 0:
            raise ValueError("Financial operand Evidence ref is invalid")
        try:
            parsed = Decimal(self.value)
        except InvalidOperation:
            raise ValueError("Financial operand value is invalid") from None
        if not parsed.is_finite() or self.value != format(parsed, "f"):
            raise ValueError("Financial operand value must be a canonical finite Decimal")


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

    values = tuple(Decimal(item.value) for item in calculation.operands)
    with localcontext() as context:
        context.prec = 50
        if calculation.operator is FinancialOperator.ADD:
            raw = sum(values, Decimal(0))
            formula = " + ".join(item.value for item in calculation.operands)
            unit, scale = scope.unit, scope.scale
        elif calculation.operator is FinancialOperator.SUBTRACT:
            raw = values[0] - values[1]
            formula = f"{calculation.operands[0].value} - {calculation.operands[1].value}"
            unit, scale = scope.unit, scope.scale
        elif calculation.operator is FinancialOperator.RATIO:
            if values[1] == 0:
                raise ValueError("Financial calculation division by zero")
            raw = values[0] / values[1]
            formula = f"{calculation.operands[0].value} / {calculation.operands[1].value}"
            unit, scale = "RATIO", 0
        else:
            if values[1] == 0:
                raise ValueError("Financial calculation division by zero")
            raw = ((values[0] - values[1]) / values[1]) * Decimal(100)
            formula = (
                f"(({calculation.operands[0].value} - "
                f"{calculation.operands[1].value}) / "
                f"{calculation.operands[1].value}) * 100"
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
