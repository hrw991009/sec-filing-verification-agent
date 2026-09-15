"""Controlled skill.read tool; returns reviewed instructions, never financial Evidence."""

import hashlib
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from industry_platform.modules.agent_runtime.context import TrustedRuntimeContext
from industry_platform.modules.skills.registry import (
    SKILL_TOOL_NAME,
    SKILL_TOOL_VERSION,
    InstructionSkillCatalog,
)
from industry_platform.modules.tools.domain import (
    TOOL_OBSERVATION_NORMALIZER_VERSION,
    ToolApprovalPolicy,
    ToolCostClass,
    ToolDefinition,
    ToolObservation,
    ToolReference,
    ToolRetryClassification,
    ToolSideEffectClass,
)
from industry_platform.modules.tools.registry import PydanticToolAdapter, ToolExecutionError
from industry_platform.modules.workspaces.domain import WorkspaceAction


class SkillReadInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class SkillReadOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    receipt: str


class SkillReadTool(PydanticToolAdapter[SkillReadInput, SkillReadOutput]):
    def __init__(self, catalog: InstructionSkillCatalog) -> None:
        self._catalog = catalog
        super().__init__(
            definition=ToolDefinition(
                schema_version=1,
                name=SKILL_TOOL_NAME,
                version=SKILL_TOOL_VERSION,
                description=(
                    "Load one reviewed SKILL.md by exact catalog name and content hash. "
                    "This only loads instructions; continue in the same tool loop."
                ),
                input_schema_version="skill-read-input-v1",
                output_schema_version="skill-read-output-v1",
                input_schema=SkillReadInput.model_json_schema(),
                output_schema=SkillReadOutput.model_json_schema(),
                capability=WorkspaceAction.RUN_TOOL,
                timeout_ms=1_000,
                max_result_bytes=32_000,
                max_cost_micro_usd=1,
                cost_class=ToolCostClass.LOW,
                side_effect_class=ToolSideEffectClass.READ_ONLY,
                retry_classification=ToolRetryClassification.SAFE_READ_ONLY,
                approval_policy=ToolApprovalPolicy.AUTO_ALLOW,
                policy_version="bundled-instruction-read-v1",
            ),
            input_model=SkillReadInput,
            output_model=SkillReadOutput,
        )

    async def invoke(
        self,
        value: SkillReadInput,
        runtime_context: TrustedRuntimeContext,
        *,
        idempotency_key: str | None,
    ) -> tuple[SkillReadOutput, int]:
        del runtime_context
        if idempotency_key is not None:
            raise ToolExecutionError("tool_idempotency_key_unexpected")
        try:
            skill = self._catalog.resolve(value.name, value.content_sha256)
        except ValueError:
            raise ToolExecutionError("skill_not_found_or_changed") from None
        return SkillReadOutput(receipt=skill.receipt), 0

    def normalize(
        self,
        value: SkillReadOutput,
        runtime_context: TrustedRuntimeContext,
        *,
        call_id: UUID,
        run_id: UUID,
        observed_at: datetime,
    ) -> ToolObservation:
        return ToolObservation(
            schema_version=1,
            observation_id=uuid5(NAMESPACE_URL, f"{call_id}:skill-read:v1"),
            call_id=call_id,
            run_id=run_id,
            workspace_id=runtime_context.workspace_scope.workspace_id,
            tool=ToolReference(SKILL_TOOL_NAME, SKILL_TOOL_VERSION),
            normalizer_version=TOOL_OBSERVATION_NORMALIZER_VERSION,
            model_text=value.receipt,
            sources=(),
            observed_at=observed_at,
            content_sha256=hashlib.sha256(value.receipt.encode()).hexdigest(),
        )
