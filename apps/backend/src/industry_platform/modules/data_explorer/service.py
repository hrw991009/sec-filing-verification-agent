"""Workspace-scoped database browsing and audited safe Text2SQL application service."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from industry_platform.modules.data_explorer.artifacts import (
    ArtifactValidationError,
    create_chart_artifact,
    create_table_artifact,
)
from industry_platform.modules.data_explorer.domain import (
    ChartRequest,
    DatabaseRows,
    DataConnectionStatus,
    DataConnectionSummary,
    DataExplorerError,
    DataExplorerPersistenceError,
    QueryBudgets,
    QueryExecutionRequest,
    QueryRunResult,
    QueryRunSummary,
    SchemaSnapshot,
    TableSchema,
)
from industry_platform.modules.data_explorer.ports import (
    DataExplorerRepository,
    ReadOnlyDatabasePort,
)
from industry_platform.modules.data_explorer.sql_validator import (
    SqlValidationError,
    ValidatedSql,
    validate_read_only_sql,
)
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.workspaces.domain import WorkspaceScope


class DataExplorerService:
    """Keep schema, validation, execution, and artifacts on one audited path."""

    def __init__(
        self,
        repository: DataExplorerRepository,
        database: ReadOnlyDatabasePort,
        budgets: QueryBudgets,
    ) -> None:
        self._repository = repository
        self._database = database
        self._budgets = budgets

    async def ensure_sample_connection(self, scope: WorkspaceScope) -> DataConnectionSummary:
        if not self._database.configured:
            return await self._repository.ensure_sample_connection(
                scope,
                configured=False,
                error_code=None,
            )
        try:
            await self._database.probe()
        except DataExplorerError as error:
            return await self._repository.ensure_sample_connection(
                scope,
                configured=True,
                error_code=error.code,
            )
        return await self._repository.ensure_sample_connection(
            scope,
            configured=True,
            error_code=None,
        )

    async def test_connection(
        self,
        scope: WorkspaceScope,
        connection_id: UUID,
    ) -> DataConnectionSummary:
        connection = await self._repository.get_connection(scope, connection_id)
        ensured = await self.ensure_sample_connection(scope)
        if ensured.connection_id != connection.connection_id:
            raise DataExplorerError("data_connection_contract_invalid")
        return ensured

    async def list_connections(
        self,
        scope: WorkspaceScope,
    ) -> tuple[DataConnectionSummary, ...]:
        return await self._repository.list_connections(scope)

    async def refresh_schema(
        self,
        scope: WorkspaceScope,
        connection_id: UUID,
    ) -> SchemaSnapshot:
        await self._require_ready_connection(scope, connection_id)
        snapshot = await self._database.discover_schema(
            snapshot_id=uuid4(),
            connection_id=connection_id,
            workspace_id=scope.workspace_id,
        )
        await self._repository.save_schema_snapshot(snapshot)
        return snapshot

    async def list_tables(
        self,
        scope: WorkspaceScope,
        connection_id: UUID,
    ) -> tuple[TableSchema, ...]:
        return (await self.refresh_schema(scope, connection_id)).tables

    async def get_table_schema(
        self,
        scope: WorkspaceScope,
        connection_id: UUID,
        *,
        schema_name: str,
        table_name: str,
    ) -> TableSchema:
        snapshot = await self.refresh_schema(scope, connection_id)
        try:
            return snapshot.table_by_name[f"{schema_name}.{table_name}"]
        except KeyError:
            raise DataExplorerError("database_table_not_found") from None

    async def browse_rows(
        self,
        scope: WorkspaceScope,
        connection_id: UUID,
        *,
        schema_name: str,
        table_name: str,
        limit: int,
        offset: int,
    ) -> DatabaseRows:
        table = await self.get_table_schema(
            scope,
            connection_id,
            schema_name=schema_name,
            table_name=table_name,
        )
        return await self._database.browse_rows(table, limit=limit, offset=offset)

    async def execute_query(self, request: QueryExecutionRequest) -> QueryRunResult:
        await self._require_ready_connection(request.scope, request.connection_id)
        started = await self._repository.start_query(request, self._budgets)
        snapshot: SchemaSnapshot | None = None
        validated: ValidatedSql | None = None
        database_result: DatabaseRows | None = None
        try:
            snapshot = await self._database.discover_schema(
                snapshot_id=uuid4(),
                connection_id=request.connection_id,
                workspace_id=request.scope.workspace_id,
            )
            await self._repository.save_schema_snapshot(snapshot)
            validated = validate_read_only_sql(
                request.generated_sql,
                snapshot,
                maximum_rows=self._budgets.max_rows,
            )
            database_result = await self._database.execute(validated, self._budgets)
            created_at = datetime.now(UTC)
            table_artifact = create_table_artifact(
                artifact_id=uuid4(),
                query_run_id=started.query_run_id,
                workspace_id=request.scope.workspace_id,
                columns=database_result.columns,
                rows=database_result.rows,
                truncated=database_result.truncated,
                created_at=created_at,
            )
            chart_artifact = create_chart_artifact(
                artifact_id=uuid4(),
                table=table_artifact,
                request=request.chart,
                created_at=created_at,
            )
            return await self._repository.complete_query(
                request,
                query_run_id=started.query_run_id,
                snapshot=snapshot,
                validated=validated,
                result=database_result,
                table_artifact=table_artifact,
                chart_artifact=chart_artifact,
            )
        except DataExplorerPersistenceError:
            raise
        except (SqlValidationError, DataExplorerError, ArtifactValidationError) as error:
            return await self._repository.fail_query(
                request,
                query_run_id=started.query_run_id,
                error_code=error.code,
                snapshot=snapshot,
                validated=validated,
                plan_cost=None if database_result is None else database_result.plan_cost,
                plan_rows=None if database_result is None else database_result.plan_rows,
            )

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
        trace_id = await self._repository.get_agent_trace_id(scope, run_id)
        return await self.execute_query(
            QueryExecutionRequest(
                scope=scope,
                connection_id=connection_id,
                question=question,
                generated_sql=generated_sql,
                chart=chart,
                trace_id=trace_id,
                agent_run_id=run_id,
                tool_call_id=tool_call_id,
            )
        )

    async def execute_direct_query(
        self,
        scope: WorkspaceScope,
        *,
        connection_id: UUID,
        question: str,
        generated_sql: str,
        chart: ChartRequest,
        trace_id: TraceId,
    ) -> QueryRunResult:
        return await self.execute_query(
            QueryExecutionRequest(
                scope=scope,
                connection_id=connection_id,
                question=question,
                generated_sql=generated_sql,
                chart=chart,
                trace_id=trace_id,
            )
        )

    async def get_query(self, scope: WorkspaceScope, query_run_id: UUID) -> QueryRunResult:
        return await self._repository.get_query(scope, query_run_id)

    async def list_queries(
        self,
        scope: WorkspaceScope,
        *,
        limit: int,
    ) -> tuple[QueryRunSummary, ...]:
        return tuple(await self._repository.list_queries(scope, limit=limit))

    async def _require_ready_connection(
        self,
        scope: WorkspaceScope,
        connection_id: UUID,
    ) -> DataConnectionSummary:
        connection = await self._repository.get_connection(scope, connection_id)
        if connection.status is not DataConnectionStatus.READY:
            raise DataExplorerError("data_connection_not_ready")
        return connection
