"""Deterministic typed Fake Tool used by Day 3 Harness scenarios."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from industry_platform.modules.agent_runtime.context import TrustedRuntimeContext
from industry_platform.modules.agent_runtime.domain import AGENT_RUNTIME_SCHEMA_VERSION
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

FAKE_LOOKUP_TOOL_NAME = "fake.industry_lookup"
FAKE_LOOKUP_TOOL_VERSION = "v1"
FAKE_DATABASE_TOOL_NAME = "fake.database_lookup"
FAKE_DATABASE_TOOL_VERSION = "v1"


class FakeLookupInput(BaseModel):
    """Strict deterministic lookup arguments."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1, max_length=200, pattern=r"^[^\r\n]+$")


class FakeLookupOutput(BaseModel):
    """Strict output produced by the Fake implementation before normalization."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1, max_length=10_000)
    locator: str = Field(min_length=1, max_length=2_048, pattern=r"^[^\r\n]+$")
    source_version: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9].*$")


@dataclass(frozen=True, slots=True)
class FakeLookupRecord:
    """One local fixture; it is not authorization or Runtime context."""

    text: str = field(repr=False)
    locator: str = field(repr=False)
    source_version: str


def fake_lookup_definition(
    *,
    approval_policy: ToolApprovalPolicy = ToolApprovalPolicy.AUTO_ALLOW,
    timeout_ms: int = 1_000,
) -> ToolDefinition:
    """Build the exact definition whose schemas come from the typed models."""

    return ToolDefinition(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        name=FAKE_LOOKUP_TOOL_NAME,
        version=FAKE_LOOKUP_TOOL_VERSION,
        description="Return one deterministic industry fixture for Tool contract tests.",
        input_schema_version="fake-lookup-input-v1",
        output_schema_version="fake-lookup-output-v1",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["query"],
            "properties": {"query": {"type": "string"}},
        },
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["text", "locator", "source_version"],
            "properties": {
                "text": {"type": "string"},
                "locator": {"type": "string"},
                "source_version": {"type": "string"},
            },
        },
        capability=WorkspaceAction.RUN_TOOL,
        timeout_ms=timeout_ms,
        max_result_bytes=20_000,
        max_cost_micro_usd=1_000,
        cost_class=ToolCostClass.LOW,
        side_effect_class=ToolSideEffectClass.READ_ONLY,
        retry_classification=ToolRetryClassification.NEVER,
        approval_policy=approval_policy,
        policy_version="fake-static-policy-v1",
    )


def fake_database_definition() -> ToolDefinition:
    """Build a second exact Tool surface for deterministic selection scoring."""

    definition = fake_lookup_definition()
    return ToolDefinition(
        schema_version=definition.schema_version,
        name=FAKE_DATABASE_TOOL_NAME,
        version=FAKE_DATABASE_TOOL_VERSION,
        description="Return one deterministic database Artifact fixture for selection tests.",
        input_schema_version="fake-database-input-v1",
        output_schema_version="fake-database-output-v1",
        input_schema=definition.input_schema,
        output_schema=definition.output_schema,
        capability=definition.capability,
        timeout_ms=definition.timeout_ms,
        max_result_bytes=definition.max_result_bytes,
        max_cost_micro_usd=definition.max_cost_micro_usd,
        cost_class=definition.cost_class,
        side_effect_class=definition.side_effect_class,
        retry_classification=definition.retry_classification,
        approval_policy=definition.approval_policy,
        policy_version="fake-database-static-policy-v1",
    )


class FakeIndustryLookupTool(PydanticToolAdapter[FakeLookupInput, FakeLookupOutput]):
    """Resolve an exact fixture and expose no network, shell, or Secret capability."""

    def __init__(
        self,
        records: Mapping[str, FakeLookupRecord],
        *,
        approval_policy: ToolApprovalPolicy = ToolApprovalPolicy.AUTO_ALLOW,
        timeout_ms: int = 1_000,
    ) -> None:
        super().__init__(
            definition=fake_lookup_definition(
                approval_policy=approval_policy,
                timeout_ms=timeout_ms,
            ),
            input_model=FakeLookupInput,
            output_model=FakeLookupOutput,
        )
        self._records = dict(records)
        if not self._records:
            raise ValueError("Fake lookup Tool requires at least one fixture")
        self.invocations: list[FakeLookupInput] = []

    async def invoke(
        self,
        value: FakeLookupInput,
        runtime_context: TrustedRuntimeContext,
        *,
        idempotency_key: str | None,
    ) -> tuple[FakeLookupOutput, int]:
        del runtime_context
        if idempotency_key is not None:
            raise ToolExecutionError("fake_tool_idempotency_key_unexpected")
        self.invocations.append(value)
        record = self._records.get(value.query)
        if record is None:
            raise ToolExecutionError("tool_fixture_not_found")
        return (
            FakeLookupOutput(
                text=record.text,
                locator=record.locator,
                source_version=record.source_version,
            ),
            0,
        )

    def normalize(
        self,
        value: FakeLookupOutput,
        runtime_context: TrustedRuntimeContext,
        *,
        call_id: UUID,
        run_id: UUID,
        observed_at: datetime,
    ) -> ToolObservation:
        text = value.text.replace("\r\n", "\n").replace("\r", "\n").strip()
        content_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return ToolObservation(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            observation_id=uuid5(NAMESPACE_URL, f"{call_id}:observation:v1"),
            call_id=call_id,
            run_id=run_id,
            workspace_id=runtime_context.workspace_scope.workspace_id,
            tool=ToolReference(self.definition.name, self.definition.version),
            normalizer_version=TOOL_OBSERVATION_NORMALIZER_VERSION,
            model_text=text,
            sources=(
                ToolSource(
                    source_type="fake_fixture",
                    source_version=value.source_version,
                    locator=value.locator,
                    observed_at=observed_at,
                    content_sha256=content_sha256,
                ),
            ),
            observed_at=observed_at,
            content_sha256=content_sha256,
        )


class FakeDatabaseLookupTool(PydanticToolAdapter[FakeLookupInput, FakeLookupOutput]):
    """Second deterministic adapter; its locator represents a persisted Artifact."""

    def __init__(self, records: Mapping[str, FakeLookupRecord]) -> None:
        super().__init__(
            definition=fake_database_definition(),
            input_model=FakeLookupInput,
            output_model=FakeLookupOutput,
        )
        self._records = dict(records)
        if not self._records:
            raise ValueError("Fake database Tool requires at least one fixture")
        self.invocations: list[FakeLookupInput] = []

    async def invoke(
        self,
        value: FakeLookupInput,
        runtime_context: TrustedRuntimeContext,
        *,
        idempotency_key: str | None,
    ) -> tuple[FakeLookupOutput, int]:
        del runtime_context
        if idempotency_key is not None:
            raise ToolExecutionError("fake_tool_idempotency_key_unexpected")
        self.invocations.append(value)
        record = self._records.get(value.query)
        if record is None:
            raise ToolExecutionError("tool_fixture_not_found")
        return (
            FakeLookupOutput(
                text=record.text,
                locator=record.locator,
                source_version=record.source_version,
            ),
            0,
        )

    def normalize(
        self,
        value: FakeLookupOutput,
        runtime_context: TrustedRuntimeContext,
        *,
        call_id: UUID,
        run_id: UUID,
        observed_at: datetime,
    ) -> ToolObservation:
        text = value.text.replace("\r\n", "\n").replace("\r", "\n").strip()
        content_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return ToolObservation(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            observation_id=uuid5(NAMESPACE_URL, f"{call_id}:database-observation:v1"),
            call_id=call_id,
            run_id=run_id,
            workspace_id=runtime_context.workspace_scope.workspace_id,
            tool=ToolReference(self.definition.name, self.definition.version),
            normalizer_version=TOOL_OBSERVATION_NORMALIZER_VERSION,
            model_text=text,
            sources=(
                ToolSource(
                    source_type="fake_fixture",
                    source_version=value.source_version,
                    locator=value.locator,
                    observed_at=observed_at,
                    content_sha256=content_sha256,
                ),
            ),
            observed_at=observed_at,
            content_sha256=content_sha256,
        )
