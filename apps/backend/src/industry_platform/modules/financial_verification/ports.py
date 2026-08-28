"""Ports for resolving authorized SEC operands before deterministic calculation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from industry_platform.modules.financial_verification.domain import (
    FinancialEvidenceOperand,
    FinancialScope,
)
from industry_platform.modules.workspaces.domain import WorkspaceScope


class FinancialOperandResolutionStatus(StrEnum):
    OK = "ok"
    NO_RESULT = "no_result"
    DEPENDENCY_FAILED = "dependency_failed"


@dataclass(frozen=True, slots=True)
class FinancialOperandReference:
    evidence_ref: UUID
    source_fact_id: UUID
    value: str

    def __post_init__(self) -> None:
        if self.evidence_ref.int == 0 or self.source_fact_id.int == 0:
            raise ValueError("Financial operand reference identity is invalid")
        if not self.value:
            raise ValueError("Financial operand reference value is invalid")


@dataclass(frozen=True, slots=True)
class FinancialOperandResolution:
    status: FinancialOperandResolutionStatus
    operands: tuple[FinancialEvidenceOperand, ...] = ()

    def __post_init__(self) -> None:
        operands = tuple(self.operands)
        if (self.status is FinancialOperandResolutionStatus.OK) != bool(operands):
            raise ValueError("Financial operand resolution is inconsistent")
        object.__setattr__(self, "operands", operands)


class FinancialOperandRepository(Protocol):
    async def resolve(
        self,
        scope: WorkspaceScope,
        *,
        knowledge_base_ids: tuple[UUID, ...],
        financial_scope: FinancialScope,
        references: tuple[FinancialOperandReference, ...],
    ) -> FinancialOperandResolution: ...
