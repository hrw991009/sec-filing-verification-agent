"""Deterministic Decimal calculator Tool with Knowledge Evidence references."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from industry_platform.modules.agent_runtime.context import TrustedRuntimeContext
from industry_platform.modules.agent_runtime.domain import AGENT_RUNTIME_SCHEMA_VERSION
from industry_platform.modules.financial_verification.domain import (
    FinancialCalculation,
    FinancialEvidenceOperand,
    FinancialOperand,
    FinancialOperator,
    FinancialReconciliationStatus,
    FinancialRoundingMode,
    calculate_financial_result,
    reconcile_financial_operands,
)
from industry_platform.modules.financial_verification.ports import (
    FinancialOperandReference,
    FinancialOperandRepository,
    FinancialOperandResolutionStatus,
)
from industry_platform.modules.financial_verification.schemas import (
    FinancialEvidenceOperandPayload,
    FinancialReconciliationPayload,
    FinancialScopePayload,
)
from industry_platform.modules.retrieval.domain import KnowledgeSearchStatus
from industry_platform.modules.retrieval.fixtures import SecFixtureCatalog
from industry_platform.modules.retrieval.ports import KnowledgeCandidateRepository
from industry_platform.modules.tools.domain import (
    TOOL_OBSERVATION_NORMALIZER_VERSION,
    ToolApprovalPolicy,
    ToolCostClass,
    ToolDefinition,
    ToolObservation,
    ToolReference,
    ToolRetryClassification,
    ToolSideEffectClass,
    ToolSource,
)
from industry_platform.modules.tools.registry import PydanticToolAdapter, ToolExecutionError
from industry_platform.modules.workspaces.domain import WorkspaceAction

FINANCE_CALCULATE_TOOL_NAME = "finance.calculate"
FINANCE_CALCULATE_TOOL_VERSION = "v1"
FINANCE_CALCULATION_SOURCE_TYPE = "finance_calculation"
FINANCE_CALCULATION_SOURCE_VERSION = "financial-calculation-v1"


class FinanceOperandPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    value: str = Field(pattern=r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
    evidence_ref: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    )
    source_fact_id: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    )


class FinanceCalculateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operator: str = Field(pattern=r"^(add|subtract|ratio|percentage|percent_change)$")
    operands: list[FinanceOperandPayload] = Field(min_length=2, max_length=8)
    decimal_places: int = Field(default=2, ge=0, le=12)
    rounding_mode: str = Field(default="half_even", pattern=r"^half_even$")


class FinanceCalculateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: KnowledgeSearchStatus
    financial_scope: FinancialScopePayload
    operator: FinancialOperator
    operands: list[FinanceOperandPayload]
    decimal_places: int
    rounding_mode: FinancialRoundingMode
    result: str | None
    formula: str | None
    unit: str | None
    scale: int | None
    evidence_refs: list[UUID]
    error_code: str | None = None
    operand_source: Literal["legacy_fixture", "sec_xbrl_evidence"] | None = None
    resolved_operands: list[FinancialEvidenceOperandPayload] = Field(default_factory=list)
    reconciliation: FinancialReconciliationPayload | None = None


def finance_calculate_definition() -> ToolDefinition:
    return ToolDefinition(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        name=FINANCE_CALCULATE_TOOL_NAME,
        version=FINANCE_CALCULATE_TOOL_VERSION,
        description=(
            "Calculate one allowlisted Decimal formula using authorized filing Evidence refs."
        ),
        input_schema_version="finance-calculate-input-v1",
        output_schema_version="finance-calculate-output-v1",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["operator", "operands", "decimal_places", "rounding_mode"],
            "properties": {
                "operator": {
                    "type": "string",
                    "enum": [item.value for item in FinancialOperator],
                },
                "operands": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["value", "evidence_ref"],
                        "properties": {
                            "value": {"type": "string"},
                            "evidence_ref": {"type": "string", "format": "uuid"},
                            "source_fact_id": {
                                "type": ["string", "null"],
                                "format": "uuid",
                            },
                        },
                    },
                },
                "decimal_places": {"type": "integer", "minimum": 0, "maximum": 12},
                "rounding_mode": {"type": "string", "const": "half_even"},
            },
        },
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": [
                "status",
                "financial_scope",
                "operator",
                "operands",
                "decimal_places",
                "rounding_mode",
                "result",
                "formula",
                "unit",
                "scale",
                "evidence_refs",
                "error_code",
            ],
            "properties": {
                "status": {"type": "string"},
                "financial_scope": {"type": "object"},
                "operator": {"type": "string"},
                "operands": {"type": "array"},
                "decimal_places": {"type": "integer"},
                "rounding_mode": {"type": "string"},
                "result": {"type": ["string", "null"]},
                "formula": {"type": ["string", "null"]},
                "unit": {"type": ["string", "null"]},
                "scale": {"type": ["integer", "null"]},
                "evidence_refs": {"type": "array"},
                "error_code": {"type": ["string", "null"]},
                "operand_source": {"type": ["string", "null"]},
                "resolved_operands": {"type": "array"},
                "reconciliation": {"type": ["object", "null"]},
            },
        },
        capability=WorkspaceAction.RUN_TOOL,
        timeout_ms=10_000,
        max_result_bytes=100_000,
        max_cost_micro_usd=1,
        cost_class=ToolCostClass.LOW,
        side_effect_class=ToolSideEffectClass.READ_ONLY,
        retry_classification=ToolRetryClassification.SAFE_READ_ONLY,
        approval_policy=ToolApprovalPolicy.AUTO_ALLOW,
        policy_version="financial-evidence-calculation-v1",
    )


class FinanceCalculateTool(PydanticToolAdapter[FinanceCalculateInput, FinanceCalculateOutput]):
    def __init__(
        self,
        repository: KnowledgeCandidateRepository,
        catalog: SecFixtureCatalog,
        operand_repository: FinancialOperandRepository | None = None,
    ) -> None:
        super().__init__(
            definition=finance_calculate_definition(),
            input_model=FinanceCalculateInput,
            output_model=FinanceCalculateOutput,
        )
        self._repository = repository
        self._catalog = catalog
        self._operand_repository = operand_repository

    async def invoke(
        self,
        value: FinanceCalculateInput,
        runtime_context: TrustedRuntimeContext,
        *,
        idempotency_key: str | None,
    ) -> tuple[FinanceCalculateOutput, int]:
        if idempotency_key is not None:
            raise ToolExecutionError("tool_idempotency_key_unexpected")
        financial_scope = runtime_context.financial_scope
        if financial_scope is None or not runtime_context.knowledge_base_ids:
            raise ToolExecutionError("financial_scope_not_configured")
        fixture = self._catalog.select(financial_scope)
        operator = FinancialOperator(value.operator)
        rounding_mode = FinancialRoundingMode(value.rounding_mode)
        resolved_operands: tuple[FinancialEvidenceOperand, ...] = ()
        reconciliation_payload: FinancialReconciliationPayload | None = None
        operand_source: Literal["legacy_fixture", "sec_xbrl_evidence"] = "legacy_fixture"
        formal_resolution_status = FinancialOperandResolutionStatus.NO_RESULT
        formal_requested = any(item.source_fact_id is not None for item in value.operands)
        if formal_requested and (
            self._operand_repository is None
            or not all(item.source_fact_id is not None for item in value.operands)
        ):
            return (
                FinanceCalculateOutput(
                    status=KnowledgeSearchStatus.NO_RESULT,
                    financial_scope=FinancialScopePayload.from_domain(financial_scope),
                    operator=operator,
                    operands=value.operands,
                    decimal_places=value.decimal_places,
                    rounding_mode=rounding_mode,
                    result=None,
                    formula=None,
                    unit=None,
                    scale=None,
                    evidence_refs=[],
                    error_code="financial_operand_reference_incomplete",
                    operand_source="sec_xbrl_evidence",
                ),
                0,
            )
        if formal_requested:
            if self._operand_repository is None:
                raise AssertionError("Formal operand repository check was bypassed")
            resolution = await self._operand_repository.resolve(
                runtime_context.workspace_scope,
                knowledge_base_ids=runtime_context.knowledge_base_ids,
                financial_scope=financial_scope,
                references=tuple(
                    FinancialOperandReference(
                        evidence_ref=UUID(item.evidence_ref),
                        source_fact_id=UUID(str(item.source_fact_id)),
                        value=item.value,
                    )
                    for item in value.operands
                ),
            )
            formal_resolution_status = resolution.status
            if resolution.status is FinancialOperandResolutionStatus.DEPENDENCY_FAILED:
                return (
                    FinanceCalculateOutput(
                        status=KnowledgeSearchStatus.DEPENDENCY_FAILED,
                        financial_scope=FinancialScopePayload.from_domain(financial_scope),
                        operator=operator,
                        operands=value.operands,
                        decimal_places=value.decimal_places,
                        rounding_mode=rounding_mode,
                        result=None,
                        formula=None,
                        unit=None,
                        scale=None,
                        evidence_refs=[],
                        error_code="financial_operand_dependency_failed",
                        operand_source="sec_xbrl_evidence",
                    ),
                    0,
                )
            if resolution.status is FinancialOperandResolutionStatus.OK:
                resolved_operands = resolution.operands
                operand_source = "sec_xbrl_evidence"
                reconciliation = reconcile_financial_operands(
                    financial_scope,
                    operator,
                    resolved_operands,
                )
                reconciliation_payload = FinancialReconciliationPayload.from_domain(reconciliation)
                if reconciliation.status is not FinancialReconciliationStatus.CONSISTENT:
                    return (
                        FinanceCalculateOutput(
                            status=KnowledgeSearchStatus.NO_RESULT,
                            financial_scope=FinancialScopePayload.from_domain(financial_scope),
                            operator=operator,
                            operands=value.operands,
                            decimal_places=value.decimal_places,
                            rounding_mode=rounding_mode,
                            result=None,
                            formula=None,
                            unit=None,
                            scale=None,
                            evidence_refs=list(reconciliation.evidence_refs),
                            error_code=(f"financial_reconciliation_{reconciliation.status.value}"),
                            operand_source=operand_source,
                            resolved_operands=[
                                FinancialEvidenceOperandPayload.from_domain(item)
                                for item in resolved_operands
                            ],
                            reconciliation=reconciliation_payload,
                        ),
                        0,
                    )
            else:
                return (
                    FinanceCalculateOutput(
                        status=KnowledgeSearchStatus.NO_RESULT,
                        financial_scope=FinancialScopePayload.from_domain(financial_scope),
                        operator=operator,
                        operands=value.operands,
                        decimal_places=value.decimal_places,
                        rounding_mode=rounding_mode,
                        result=None,
                        formula=None,
                        unit=None,
                        scale=None,
                        evidence_refs=[],
                        error_code="financial_operand_not_authorized",
                        operand_source="sec_xbrl_evidence",
                    ),
                    0,
                )
        if formal_resolution_status is FinancialOperandResolutionStatus.OK:
            status = KnowledgeSearchStatus.OK
        else:
            status = await self._repository.validate_operands(
                runtime_context.workspace_scope,
                knowledge_base_ids=runtime_context.knowledge_base_ids,
                financial_scope=financial_scope,
                evidence_values=tuple(
                    (UUID(item.evidence_ref), item.value) for item in value.operands
                ),
                fixture=fixture,
            )
        scope_payload = FinancialScopePayload.from_domain(financial_scope)
        if status is not KnowledgeSearchStatus.OK:
            return (
                FinanceCalculateOutput(
                    status=status,
                    financial_scope=scope_payload,
                    operator=operator,
                    operands=value.operands,
                    decimal_places=value.decimal_places,
                    rounding_mode=rounding_mode,
                    result=None,
                    formula=None,
                    unit=None,
                    scale=None,
                    evidence_refs=[],
                    error_code=status.value,
                    operand_source=operand_source,
                ),
                0,
            )
        try:
            calculation = FinancialCalculation(
                operator=operator,
                operands=(
                    tuple(
                        FinancialOperand(
                            value=item.value,
                            evidence_ref=item.evidence_ref,
                            unit=item.unit,
                            scale=item.scale,
                        )
                        for item in resolved_operands
                    )
                    if resolved_operands
                    else tuple(
                        FinancialOperand(value=item.value, evidence_ref=UUID(item.evidence_ref))
                        for item in value.operands
                    )
                ),
                decimal_places=value.decimal_places,
                rounding_mode=rounding_mode,
            )
            result = calculate_financial_result(financial_scope, calculation)
        except ValueError as error:
            code = "division_by_zero" if "division by zero" in str(error) else "calculation_invalid"
            raise ToolExecutionError(code) from None
        return (
            FinanceCalculateOutput(
                status=KnowledgeSearchStatus.OK,
                financial_scope=scope_payload,
                operator=operator,
                operands=value.operands,
                decimal_places=value.decimal_places,
                rounding_mode=rounding_mode,
                result=result.value,
                formula=result.formula,
                unit=result.unit,
                scale=result.scale,
                evidence_refs=list(result.evidence_refs),
                error_code=None,
                operand_source=operand_source,
                resolved_operands=[
                    FinancialEvidenceOperandPayload.from_domain(item) for item in resolved_operands
                ],
                reconciliation=reconciliation_payload,
            ),
            0,
        )

    def normalize(
        self,
        value: FinanceCalculateOutput,
        runtime_context: TrustedRuntimeContext,
        *,
        call_id: UUID,
        run_id: UUID,
        observed_at: datetime,
    ) -> ToolObservation:
        model_text = json.dumps(
            value.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        content_sha256 = hashlib.sha256(model_text.encode("utf-8")).hexdigest()
        sources = (
            (
                ToolSource(
                    source_type=FINANCE_CALCULATION_SOURCE_TYPE,
                    source_version=FINANCE_CALCULATION_SOURCE_VERSION,
                    locator=(
                        f"sec://financial-calculations/{content_sha256}"
                        if value.operand_source == "sec_xbrl_evidence"
                        else f"fixture://finance-calculations/{content_sha256}"
                    ),
                    observed_at=observed_at,
                    content_sha256=content_sha256,
                ),
            )
            if value.status is KnowledgeSearchStatus.OK
            else ()
        )
        return ToolObservation(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            observation_id=uuid5(NAMESPACE_URL, f"{call_id}:finance-calculate:v1"),
            call_id=call_id,
            run_id=run_id,
            workspace_id=runtime_context.workspace_scope.workspace_id,
            tool=ToolReference(self.definition.name, self.definition.version),
            normalizer_version=TOOL_OBSERVATION_NORMALIZER_VERSION,
            model_text=model_text,
            sources=sources,
            observed_at=observed_at,
            content_sha256=content_sha256,
        )
