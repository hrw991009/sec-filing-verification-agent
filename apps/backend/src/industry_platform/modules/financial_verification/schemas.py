"""Shared strict Pydantic payloads for Financial Scope and calculations."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from industry_platform.modules.financial_verification.domain import (
    FinancialForm,
    FinancialScope,
)


class FinancialScopePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(ge=1, le=1)
    cik: str = Field(pattern=r"^[0-9]{10}$")
    accession: str = Field(pattern=r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
    form: FinancialForm
    report_period: date
    as_of: datetime
    unit: str = Field(pattern=r"^[A-Z][A-Z0-9_/-]{0,15}$")
    scale: int = Field(ge=-12, le=12)

    @classmethod
    def from_domain(cls, scope: FinancialScope) -> FinancialScopePayload:
        return cls(
            schema_version=scope.schema_version,
            cik=scope.cik,
            accession=scope.accession,
            form=scope.form,
            report_period=scope.report_period,
            as_of=scope.as_of,
            unit=scope.unit,
            scale=scope.scale,
        )

    def to_domain(self) -> FinancialScope:
        return FinancialScope.from_mapping(self.model_dump(mode="json"))
