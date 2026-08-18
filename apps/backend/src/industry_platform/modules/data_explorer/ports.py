"""Dependency boundaries for read-only data access and durable query audit."""

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from industry_platform.modules.data_explorer.domain import (
    ChartArtifact,
    DatabaseRows,
    DataConnectionSummary,
    QueryBudgets,
    QueryExecutionRequest,
    QueryResultArtifact,
    QueryRunResult,
    QueryRunSummary,
    SchemaSnapshot,
    TableSchema,
)
from industry_platform.modules.data_explorer.sql_validator import ValidatedSql
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.workspaces.domain import WorkspaceScope


class ReadOnlyDatabasePort(Protocol):
    @property
    def configured(self) -> bool: ...

    async def probe(self) -> None: ...

    async def discover_schema(
        self,
        *,
        snapshot_id: UUID,
        connection_id: UUID,
        workspace_id: UUID,
    ) -> SchemaSnapshot: ...

    async def browse_rows(
        self,
        table: TableSchema,
        *,
        limit: int,
        offset: int,
    ) -> DatabaseRows: ...

    async def execute(self, validated: ValidatedSql, budgets: QueryBudgets) -> DatabaseRows: ...

    async def close(self) -> None: ...


class DataExplorerRepository(Protocol):
    async def ensure_sample_connection(
        self,
        scope: WorkspaceScope,
        *,
        configured: bool,
        error_code: str | None,
    ) -> DataConnectionSummary: ...

    async def list_connections(
        self, scope: WorkspaceScope
    ) -> tuple[DataConnectionSummary, ...]: ...

    async def get_connection(
        self, scope: WorkspaceScope, connection_id: UUID
    ) -> DataConnectionSummary: ...

    async def save_schema_snapshot(self, snapshot: SchemaSnapshot) -> None: ...

    async def get_agent_trace_id(self, scope: WorkspaceScope, run_id: UUID) -> TraceId: ...

    async def start_query(
        self,
        request: QueryExecutionRequest,
        budgets: QueryBudgets,
    ) -> QueryRunResult: ...

    async def complete_query(
        self,
        request: QueryExecutionRequest,
        *,
        query_run_id: UUID,
        snapshot: SchemaSnapshot,
        validated: ValidatedSql,
        result: DatabaseRows,
        table_artifact: QueryResultArtifact,
        chart_artifact: ChartArtifact | None,
    ) -> QueryRunResult: ...

    async def fail_query(
        self,
        request: QueryExecutionRequest,
        *,
        query_run_id: UUID,
        error_code: str,
        snapshot: SchemaSnapshot | None = None,
        validated: ValidatedSql | None = None,
        plan_cost: float | None = None,
        plan_rows: int | None = None,
    ) -> QueryRunResult: ...

    async def get_query(self, scope: WorkspaceScope, query_run_id: UUID) -> QueryRunResult: ...

    async def list_queries(
        self, scope: WorkspaceScope, *, limit: int
    ) -> Sequence[QueryRunSummary]: ...
