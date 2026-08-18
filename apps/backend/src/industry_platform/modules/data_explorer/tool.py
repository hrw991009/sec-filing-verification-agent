"""Typed read-only Text2SQL Tool over the audited Data Explorer service."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime
from typing import Protocol, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from industry_platform.modules.agent_runtime.context import TrustedRuntimeContext
from industry_platform.modules.agent_runtime.domain import AGENT_RUNTIME_SCHEMA_VERSION
from industry_platform.modules.data_explorer.domain import (
    TEXT2SQL_TOOL_NAME,
    TEXT2SQL_TOOL_VERSION,
    ChartRequest,
    ChartType,
    QueryRunResult,
    QueryRunStatus,
    canonical_json_bytes,
)
from industry_platform.modules.tools.domain import (
    TOOL_OBSERVATION_NORMALIZER_VERSION,
    ToolApprovalOutcome,
    ToolApprovalPolicy,
    ToolCostClass,
    ToolDefinition,
    ToolObservation,
    ToolReference,
    ToolRetryClassification,
    ToolSideEffectClass,
    ToolSource,
)
from industry_platform.modules.tools.registry import (
    ToolExecutionError,
    ToolPreparationError,
)
from industry_platform.modules.workspaces.domain import WorkspaceAction, WorkspaceScope

_CONNECTION_ID_PATTERN = (
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


class Text2SqlInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    connection_id: str = Field(pattern=_CONNECTION_ID_PATTERN)
    question: str = Field(min_length=1, max_length=2_000)
    generated_sql: str = Field(min_length=1, max_length=20_000)
    chart_type: str = Field(pattern=r"^(table|line|bar|pie|scatter)$")
    x_column: str | None
    y_column: str | None
    series_column: str | None
    title: str | None

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        if value != value.strip() or any(
            ord(character) < 32 and character not in {"\n", "\t"} for character in value
        ):
            raise ValueError("Question is invalid")
        return value

    @field_validator("generated_sql")
    @classmethod
    def validate_generated_sql(cls, value: str) -> str:
        if value != value.strip() or "\x00" in value:
            raise ValueError("Generated SQL is invalid")
        return value


class Text2SqlUseCase(Protocol):
    async def execute_tool_query(
        self,
        scope: WorkspaceScope,
        *,
        run_id: UUID,
        tool_call_id: UUID,
        connection_id: UUID,
        question: str,
        generated_sql: str,
        chart: ChartRequest,
    ) -> QueryRunResult: ...


def _nullable_string_schema() -> dict[str, object]:
    return {"anyOf": [{"type": "string"}, {"type": "null"}]}


def _json_scalar_schema() -> dict[str, object]:
    return {
        "anyOf": [
            {"type": "null"},
            {"type": "boolean"},
            {"type": "integer"},
            {"type": "number"},
            {"type": "string"},
        ]
    }


def text2sql_definition() -> ToolDefinition:
    nullable_string = _nullable_string_schema()
    return ToolDefinition(
        schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
        name=TEXT2SQL_TOOL_NAME,
        version=TEXT2SQL_TOOL_VERSION,
        description=(
            "Validate and execute one bounded PostgreSQL SELECT against an allowlisted "
            "read-only connection, then return table and optional chart Artifact references."
        ),
        input_schema_version="text2sql-input-v1",
        output_schema_version="text2sql-observation-v1",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": [
                "connection_id",
                "question",
                "generated_sql",
                "chart_type",
                "x_column",
                "y_column",
                "series_column",
                "title",
            ],
            "properties": {
                "connection_id": {"type": "string"},
                "question": {"type": "string"},
                "generated_sql": {"type": "string"},
                "chart_type": {
                    "type": "string",
                    "enum": [member.value for member in ChartType],
                },
                "x_column": nullable_string,
                "y_column": nullable_string,
                "series_column": nullable_string,
                "title": nullable_string,
            },
        },
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": [
                "query_run_id",
                "table_artifact_id",
                "chart_artifact_id",
                "columns",
                "rows",
                "row_count",
                "truncated",
            ],
            "properties": {
                "query_run_id": {"type": "string"},
                "table_artifact_id": {"type": "string"},
                "chart_artifact_id": _nullable_string_schema(),
                "columns": {"type": "array", "items": {"type": "string"}},
                "rows": {
                    "type": "array",
                    "items": {"type": "array", "items": _json_scalar_schema()},
                },
                "row_count": {"type": "integer"},
                "truncated": {"type": "boolean"},
            },
        },
        capability=WorkspaceAction.RUN_TOOL,
        timeout_ms=30_000,
        max_result_bytes=50_000,
        max_cost_micro_usd=1,
        cost_class=ToolCostClass.LOW,
        side_effect_class=ToolSideEffectClass.READ_ONLY,
        retry_classification=ToolRetryClassification.SAFE_READ_ONLY,
        approval_policy=ToolApprovalPolicy.AUTO_ALLOW,
        policy_version="text2sql-read-only-policy-v1",
    )


class Text2SqlTool:
    def __init__(self, service: Text2SqlUseCase) -> None:
        self._service = service
        self._definition = text2sql_definition()

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    def validate_arguments(self, arguments: Mapping[str, object]) -> Mapping[str, object]:
        try:
            value = Text2SqlInput.model_validate(dict(arguments), strict=True)
            _chart_request(value)
            UUID(value.connection_id)
        except (ValidationError, ValueError):
            raise ToolPreparationError(
                "tool_arguments_invalid",
                outcome=ToolApprovalOutcome.DENY,
                definition=self.definition,
            ) from None
        return cast(Mapping[str, object], value.model_dump(mode="json"))

    async def execute(
        self,
        arguments: Mapping[str, object],
        runtime_context: TrustedRuntimeContext,
        *,
        call_id: UUID,
        run_id: UUID,
        observed_at: datetime,
        idempotency_key: str | None,
    ) -> tuple[ToolObservation, int]:
        if idempotency_key is not None:
            raise ToolExecutionError("tool_idempotency_key_unexpected")
        try:
            value = Text2SqlInput.model_validate(dict(arguments), strict=True)
            connection_id = UUID(value.connection_id)
            chart = _chart_request(value)
        except (ValidationError, ValueError):
            raise ToolExecutionError("tool_arguments_invalid") from None
        result = await self._service.execute_tool_query(
            runtime_context.workspace_scope,
            run_id=run_id,
            tool_call_id=call_id,
            connection_id=connection_id,
            question=value.question,
            generated_sql=value.generated_sql,
            chart=chart,
        )
        if result.status is not QueryRunStatus.COMPLETED:
            raise ToolExecutionError(result.error_code or "database_query_failed")
        table = result.table_artifact
        if table is None:
            raise ToolExecutionError("query_artifact_missing")
        preview_columns = table.columns[:16]
        preview_rows: list[list[object]] = []
        preview_truncated = table.truncated or len(table.rows) > 3 or len(table.columns) > 16
        for row in table.rows[:3]:
            preview_row: list[object] = []
            for cell_value in row[:16]:
                preview_value, value_truncated = _preview_cell(cell_value)
                preview_row.append(preview_value)
                preview_truncated = preview_truncated or value_truncated
            preview_rows.append(preview_row)
        document: dict[str, object] = {
            "query_run_id": str(result.query_run_id),
            "table_artifact_id": str(table.artifact_id),
            "chart_artifact_id": (
                None if result.chart_artifact is None else str(result.chart_artifact.artifact_id)
            ),
            "columns": list(preview_columns),
            "rows": preview_rows,
            "row_count": result.row_count,
            "truncated": preview_truncated,
        }
        model_bytes = canonical_json_bytes(document)
        if len(model_bytes) > self.definition.max_result_bytes:
            raise ToolExecutionError("tool_result_too_large")
        model_text = model_bytes.decode("utf-8")
        observation = ToolObservation(
            schema_version=AGENT_RUNTIME_SCHEMA_VERSION,
            observation_id=uuid5(NAMESPACE_URL, f"{call_id}:text2sql-observation:v1"),
            call_id=call_id,
            run_id=run_id,
            workspace_id=runtime_context.workspace_scope.workspace_id,
            tool=ToolReference(self.definition.name, self.definition.version),
            normalizer_version=TOOL_OBSERVATION_NORMALIZER_VERSION,
            model_text=model_text,
            sources=(
                ToolSource(
                    source_type="sql_query_result",
                    source_version="query-table-v1",
                    locator=(
                        f"sql://{connection_id}/public.sample_company_metrics/"
                        f"query-runs/{result.query_run_id}"
                    ),
                    observed_at=observed_at,
                    content_sha256=table.content_sha256,
                ),
            ),
            observed_at=observed_at,
            content_sha256=hashlib.sha256(model_bytes).hexdigest(),
        )
        return observation, 0


def _chart_request(value: Text2SqlInput) -> ChartRequest:
    return ChartRequest(
        chart_type=ChartType(value.chart_type),
        x_column=value.x_column,
        y_column=value.y_column,
        series_column=value.series_column,
        title=value.title,
    )


def _preview_cell(value: object) -> tuple[object, bool]:
    if isinstance(value, str) and len(value) > 128:
        return value[:125] + "...", True
    return value, False
