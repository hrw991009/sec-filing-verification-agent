"""Safe Text2SQL Tool registration, execution, and Observation contracts."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from industry_platform.adapters.openai_compatible_schema import validate_supported_schema
from industry_platform.modules.agent_runtime.context import (
    BackgroundRunPrincipal,
    TrustedRuntimeContext,
)
from industry_platform.modules.agent_runtime.domain import RunBudget
from industry_platform.modules.data_explorer.artifacts import create_table_artifact
from industry_platform.modules.data_explorer.domain import (
    ChartRequest,
    QueryRunResult,
    QueryRunStatus,
)
from industry_platform.modules.data_explorer.tool import Text2SqlTool
from industry_platform.modules.identity.domain import AuthenticatedWorkspace
from industry_platform.modules.tools.domain import ToolAction, ToolCall, tool_action_response_schema
from industry_platform.modules.tools.registry import (
    RegistryToolExecutor,
    ToolExecutionError,
    ToolRegistry,
    ToolRequestAudit,
)
from industry_platform.modules.workspaces.domain import WorkspaceAction, WorkspaceScope

CALL_ID = UUID("11111111-1111-4111-8111-111111111111")
RUN_ID = UUID("22222222-2222-4222-8222-222222222222")
STEP_ID = UUID("33333333-3333-4333-8333-333333333333")
WORKSPACE_ID = UUID("44444444-4444-4444-8444-444444444444")
USER_ID = UUID("55555555-5555-4555-8555-555555555555")
CONNECTION_ID = UUID("66666666-6666-4666-8666-666666666666")
QUERY_RUN_ID = UUID("77777777-7777-4777-8777-777777777777")
ARTIFACT_ID = UUID("88888888-8888-4888-8888-888888888888")
SNAPSHOT_ID = UUID("99999999-9999-4999-8999-999999999999")
NOW = datetime(2026, 8, 17, 4, 0, tzinfo=UTC)


def _runtime_context() -> TrustedRuntimeContext:
    return TrustedRuntimeContext(
        principal=BackgroundRunPrincipal(
            user_id=USER_ID,
            workspaces=(
                AuthenticatedWorkspace(
                    workspace_id=WORKSPACE_ID,
                    name="Text2SQL Tool",
                    role="member",
                ),
            ),
        ),
        workspace_scope=WorkspaceScope(WORKSPACE_ID, USER_ID, "member"),
        capabilities=frozenset({WorkspaceAction.VIEW, WorkspaceAction.RUN_TOOL}),
        budget=RunBudget(
            schema_version=1,
            max_steps=4,
            max_total_tokens=4_000,
            max_cost_micro_usd=10,
            deadline=NOW + timedelta(minutes=5),
        ),
    )


@dataclass(slots=True)
class RecordingText2SqlService:
    error_code: str | None = None
    industry_value: str = "energy_power"
    calls: list[tuple[UUID, UUID, str]] = field(default_factory=list)

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
    ) -> QueryRunResult:
        del chart
        self.calls.append((run_id, tool_call_id, generated_sql))
        if self.error_code is not None:
            return QueryRunResult(
                query_run_id=QUERY_RUN_ID,
                connection_id=connection_id,
                workspace_id=scope.workspace_id,
                status=QueryRunStatus.FAILED,
                question=question,
                generated_sql=generated_sql,
                error_code=self.error_code,
                created_at=NOW,
                terminal_at=NOW,
            )
        table = create_table_artifact(
            artifact_id=ARTIFACT_ID,
            query_run_id=QUERY_RUN_ID,
            workspace_id=scope.workspace_id,
            columns=("industry", "total_revenue"),
            rows=((self.industry_value, 142_000_000.0),),
            truncated=False,
            created_at=NOW,
        )
        return QueryRunResult(
            query_run_id=QUERY_RUN_ID,
            connection_id=connection_id,
            workspace_id=scope.workspace_id,
            status=QueryRunStatus.COMPLETED,
            question=question,
            generated_sql=generated_sql,
            validated_sql=(
                'SELECT "sample_company_metrics"."industry" AS "industry", '
                'SUM("sample_company_metrics"."revenue") AS "total_revenue" '
                'FROM "public"."sample_company_metrics" AS "sample_company_metrics" LIMIT 20'
            ),
            schema_snapshot_id=SNAPSHOT_ID,
            row_count=1,
            plan_cost=1.0,
            plan_rows=1,
            table_artifact=table,
            created_at=NOW,
            terminal_at=NOW,
        )


def _prepared_call(tool: Text2SqlTool) -> tuple[ToolRegistry, ToolCall]:
    registry = ToolRegistry((tool,))
    call = registry.prepare(
        ToolRequestAudit(
            call_id=CALL_ID,
            action=ToolAction(
                schema_version=1,
                name=tool.definition.name,
                version=tool.definition.version,
                arguments={
                    "connection_id": str(CONNECTION_ID),
                    "question": "What is revenue by industry?",
                    "generated_sql": (
                        "SELECT industry, SUM(revenue) AS total_revenue "
                        "FROM public.sample_company_metrics GROUP BY industry"
                    ),
                    "chart_type": "table",
                    "x_column": None,
                    "y_column": None,
                    "series_column": None,
                    "title": None,
                },
            ),
        ),
        allowed_tools=(tool.definition.reference,),
        run_id=RUN_ID,
        requested_by_step_id=STEP_ID,
        runtime_context=_runtime_context(),
        requested_at=NOW,
    )
    return registry, call


@pytest.mark.asyncio
async def test_text2sql_tool_executes_through_registry_and_returns_artifact_source() -> None:
    service = RecordingText2SqlService()
    tool = Text2SqlTool(service)
    validate_supported_schema(tool_action_response_schema(tool.definition))
    validate_supported_schema(tool.definition.output_schema)
    registry, call = _prepared_call(tool)

    result = await RegistryToolExecutor(registry, clock=lambda: NOW).execute(
        call,
        _runtime_context(),
    )

    assert service.calls == [(RUN_ID, CALL_ID, call.arguments["generated_sql"])]
    assert result.actual_cost_micro_usd == 0
    assert result.observation.workspace_id == WORKSPACE_ID
    assert str(QUERY_RUN_ID) in result.observation.model_text
    assert "generated_sql" not in result.observation.model_text
    assert len(result.observation.sources) == 1
    source = result.observation.sources[0]
    assert source.locator == (
        f"sql://{CONNECTION_ID}/public.sample_company_metrics/query-runs/{QUERY_RUN_ID}"
    )
    assert len(source.content_sha256) == 64
    assert source.content_sha256 != result.observation.content_sha256


@pytest.mark.asyncio
async def test_rejected_query_becomes_one_stable_tool_error() -> None:
    tool = Text2SqlTool(RecordingText2SqlService(error_code="sql_write_or_command_rejected"))
    registry, call = _prepared_call(tool)

    with pytest.raises(ToolExecutionError) as exc_info:
        await RegistryToolExecutor(registry, clock=lambda: NOW).execute(
            call,
            _runtime_context(),
        )

    assert exc_info.value.code == "sql_write_or_command_rejected"
    assert "write" not in str(exc_info.value).casefold()


@pytest.mark.asyncio
async def test_observation_preview_is_bounded_after_a_large_valid_artifact() -> None:
    tool = Text2SqlTool(RecordingText2SqlService(industry_value="x" * 10_000))
    registry, call = _prepared_call(tool)

    result = await RegistryToolExecutor(registry, clock=lambda: NOW).execute(
        call,
        _runtime_context(),
    )

    assert len(result.observation.model_text.encode("utf-8")) < 50_000
    assert "x" * 1_000 not in result.observation.model_text
    assert '"truncated":true' in result.observation.model_text
