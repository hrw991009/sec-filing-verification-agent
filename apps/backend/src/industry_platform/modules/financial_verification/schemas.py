"""Shared strict Pydantic payloads for Financial Scope and calculations."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from industry_platform.modules.financial_verification.domain import (
    FINANCIAL_RECONCILIATION_VERSION,
    FinancialEvidenceOperand,
    FinancialForm,
    FinancialPeriodKind,
    FinancialReconciliationIssue,
    FinancialReconciliationIssueCode,
    FinancialReconciliationResult,
    FinancialReconciliationStatus,
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


class FinancialEvidenceOperandPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

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
    dimensions: dict[str, str]
    taxonomy: str
    concept: str
    is_custom: bool
    source_kind: str
    source_version: str
    source_available_at: datetime
    amendment_relation_status: str
    base_accession: str | None

    @classmethod
    def from_domain(cls, operand: FinancialEvidenceOperand) -> FinancialEvidenceOperandPayload:
        return cls(
            evidence_ref=operand.evidence_ref,
            source_fact_id=operand.source_fact_id,
            value=operand.value,
            cik=operand.cik,
            accession=operand.accession,
            form=operand.form,
            report_period=operand.report_period,
            unit=operand.unit,
            scale=operand.scale,
            period_kind=operand.period_kind,
            instant=operand.instant,
            start_date=operand.start_date,
            end_date=operand.end_date,
            context_id=operand.context_id,
            dimensions=dict(operand.dimensions),
            taxonomy=operand.taxonomy,
            concept=operand.concept,
            is_custom=operand.is_custom,
            source_kind=operand.source_kind,
            source_version=operand.source_version,
            source_available_at=operand.source_available_at,
            amendment_relation_status=operand.amendment_relation_status,
            base_accession=operand.base_accession,
        )

    def to_domain(self) -> FinancialEvidenceOperand:
        return FinancialEvidenceOperand(
            evidence_ref=self.evidence_ref,
            source_fact_id=self.source_fact_id,
            value=self.value,
            cik=self.cik,
            accession=self.accession,
            form=self.form,
            report_period=self.report_period,
            unit=self.unit,
            scale=self.scale,
            period_kind=self.period_kind,
            instant=self.instant,
            start_date=self.start_date,
            end_date=self.end_date,
            context_id=self.context_id,
            dimensions=tuple(self.dimensions.items()),
            taxonomy=self.taxonomy,
            concept=self.concept,
            is_custom=self.is_custom,
            source_kind=self.source_kind,
            source_version=self.source_version,
            source_available_at=self.source_available_at,
            amendment_relation_status=self.amendment_relation_status,
            base_accession=self.base_accession,
        )


class FinancialReconciliationIssuePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: FinancialReconciliationIssueCode
    evidence_refs: list[UUID]

    @classmethod
    def from_domain(
        cls, issue: FinancialReconciliationIssue
    ) -> FinancialReconciliationIssuePayload:
        return cls(code=issue.code, evidence_refs=list(issue.evidence_refs))


class FinancialReconciliationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    status: FinancialReconciliationStatus
    issues: list[FinancialReconciliationIssuePayload]
    evidence_refs: list[UUID]

    @classmethod
    def from_domain(cls, result: FinancialReconciliationResult) -> FinancialReconciliationPayload:
        return cls(
            version=result.version,
            status=result.status,
            issues=[
                FinancialReconciliationIssuePayload.from_domain(item) for item in result.issues
            ],
            evidence_refs=list(result.evidence_refs),
        )

    def to_domain(self) -> FinancialReconciliationResult:
        if self.version != FINANCIAL_RECONCILIATION_VERSION:
            raise ValueError("Financial reconciliation version is unsupported")
        return FinancialReconciliationResult(
            version=self.version,
            status=self.status,
            issues=tuple(
                FinancialReconciliationIssue(
                    code=item.code,
                    evidence_refs=tuple(item.evidence_refs),
                )
                for item in self.issues
            ),
            evidence_refs=tuple(self.evidence_refs),
        )
