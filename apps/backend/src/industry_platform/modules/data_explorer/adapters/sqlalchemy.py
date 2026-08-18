"""Workspace-scoped PostgreSQL persistence for query audit and artifacts."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import MappingProxyType
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from industry_platform.core.database import AsyncSessionFactory, safe_sqlstate
from industry_platform.modules.agent_runtime.domain import RunArtifactKind
from industry_platform.modules.agent_runtime.models import AgentRunRecord, RunArtifactRecord
from industry_platform.modules.data_explorer.domain import (
    DATA_EXPLORER_SCHEMA_VERSION,
    QUERY_VALIDATOR_VERSION,
    TEXT2SQL_SECRET_REFERENCE,
    ChartArtifact,
    DatabaseRows,
    DataConnectionNotFoundError,
    DataConnectionStatus,
    DataConnectionSummary,
    DataExplorerPersistenceError,
    QueryBudgets,
    QueryExecutionRequest,
    QueryResultArtifact,
    QueryRunNotFoundError,
    QueryRunResult,
    QueryRunStatus,
    QueryRunSummary,
    SchemaSnapshot,
    table_schema_document,
)
from industry_platform.modules.data_explorer.models import (
    ChartSpecRecord,
    DataConnectionRecord,
    QueryResultRecord,
    QueryRunRecord,
    SchemaSnapshotRecord,
)
from industry_platform.modules.data_explorer.sql_validator import ValidatedSql
from industry_platform.modules.identity.domain import TraceId
from industry_platform.modules.tools.models import ToolCallRecord
from industry_platform.modules.workspaces.domain import WorkspaceScope

_SAMPLE_CONNECTION_NAME = "Read-only company metrics sample"
_ALLOWED_TABLES = ["public.sample_company_metrics"]


def sample_connection_id(workspace_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"iip:{workspace_id}:text2sql:sample:v1")


class SqlAlchemyDataExplorerRepository:
    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        self._session_factory = session_factory

    async def ensure_sample_connection(
        self,
        scope: WorkspaceScope,
        *,
        configured: bool,
        error_code: str | None,
    ) -> DataConnectionSummary:
        connection_id = sample_connection_id(scope.workspace_id)
        status = (
            DataConnectionStatus.ERROR
            if error_code is not None
            else DataConnectionStatus.READY
            if configured
            else DataConnectionStatus.CONFIGURATION_REQUIRED
        )
        try:
            async with self._session_factory.begin() as session:
                now = await _database_now(session)
                statement = (
                    insert(DataConnectionRecord)
                    .values(
                        id=connection_id,
                        workspace_id=scope.workspace_id,
                        created_by_user_id=scope.user_id,
                        name=_SAMPLE_CONNECTION_NAME,
                        dialect="postgres",
                        secret_reference=TEXT2SQL_SECRET_REFERENCE,
                        allowed_tables=_ALLOWED_TABLES,
                        status=status,
                        last_error_code=error_code,
                        created_at=now,
                        updated_at=now,
                    )
                    .on_conflict_do_update(
                        index_elements=(DataConnectionRecord.id,),
                        set_={"status": status, "last_error_code": error_code, "updated_at": now},
                    )
                    .returning(DataConnectionRecord)
                )
                record = (await session.execute(statement)).scalar_one()
        except SQLAlchemyError as error:
            raise DataExplorerPersistenceError(sqlstate=safe_sqlstate(error)) from None
        return _connection_summary(record)

    async def list_connections(self, scope: WorkspaceScope) -> tuple[DataConnectionSummary, ...]:
        try:
            async with self._session_factory() as session:
                records = tuple(
                    await session.scalars(
                        select(DataConnectionRecord)
                        .where(DataConnectionRecord.workspace_id == scope.workspace_id)
                        .order_by(DataConnectionRecord.created_at, DataConnectionRecord.id)
                    )
                )
        except SQLAlchemyError as error:
            raise DataExplorerPersistenceError(sqlstate=safe_sqlstate(error)) from None
        return tuple(_connection_summary(record) for record in records)

    async def get_connection(
        self, scope: WorkspaceScope, connection_id: UUID
    ) -> DataConnectionSummary:
        try:
            async with self._session_factory() as session:
                record = await session.scalar(
                    select(DataConnectionRecord).where(
                        DataConnectionRecord.id == connection_id,
                        DataConnectionRecord.workspace_id == scope.workspace_id,
                    )
                )
        except SQLAlchemyError as error:
            raise DataExplorerPersistenceError(sqlstate=safe_sqlstate(error)) from None
        if record is None:
            raise DataConnectionNotFoundError
        return _connection_summary(record)

    async def save_schema_snapshot(self, snapshot: SchemaSnapshot) -> None:
        try:
            async with self._session_factory.begin() as session:
                session.add(
                    SchemaSnapshotRecord(
                        id=snapshot.snapshot_id,
                        workspace_id=snapshot.workspace_id,
                        connection_id=snapshot.connection_id,
                        schema_version=DATA_EXPLORER_SCHEMA_VERSION,
                        version=snapshot.version,
                        tables=[table_schema_document(table) for table in snapshot.tables],
                        content_sha256=snapshot.content_sha256,
                        captured_at=snapshot.captured_at,
                    )
                )
        except SQLAlchemyError as error:
            raise DataExplorerPersistenceError(sqlstate=safe_sqlstate(error)) from None

    async def get_agent_trace_id(self, scope: WorkspaceScope, run_id: UUID) -> TraceId:
        try:
            async with self._session_factory() as session:
                trace_id = await session.scalar(
                    select(AgentRunRecord.trace_id).where(
                        AgentRunRecord.id == run_id,
                        AgentRunRecord.workspace_id == scope.workspace_id,
                        AgentRunRecord.user_id == scope.user_id,
                    )
                )
        except SQLAlchemyError as error:
            raise DataExplorerPersistenceError(sqlstate=safe_sqlstate(error)) from None
        if trace_id is None:
            raise QueryRunNotFoundError
        return TraceId(trace_id)

    async def start_query(
        self,
        request: QueryExecutionRequest,
        budgets: QueryBudgets,
    ) -> QueryRunResult:
        query_run_id = uuid4()
        started_at = datetime.now(UTC)
        record = QueryRunRecord(
            id=query_run_id,
            workspace_id=request.scope.workspace_id,
            connection_id=request.connection_id,
            schema_snapshot_id=None,
            actor_user_id=request.scope.user_id,
            agent_run_id=request.agent_run_id,
            tool_call_id=request.tool_call_id,
            question=request.question,
            generated_sql=request.generated_sql,
            validated_sql=None,
            validator_version=QUERY_VALIDATOR_VERSION,
            status=QueryRunStatus.RUNNING,
            statement_timeout_ms=budgets.statement_timeout_ms,
            max_rows=budgets.max_rows,
            max_plan_cost=budgets.max_plan_cost,
            max_plan_rows=budgets.max_plan_rows,
            plan_cost=None,
            plan_rows=None,
            row_count=0,
            result_content_sha256=None,
            error_code=None,
            trace_id=str(request.trace_id),
            started_at=started_at,
            terminal_at=None,
            created_at=started_at,
            updated_at=started_at,
        )
        try:
            async with self._session_factory.begin() as session:
                session.add(record)
        except SQLAlchemyError as error:
            raise DataExplorerPersistenceError(sqlstate=safe_sqlstate(error)) from None
        return _query_result(record, table=None, chart=None)

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
    ) -> QueryRunResult:
        try:
            async with self._session_factory.begin() as session:
                record = await session.scalar(
                    select(QueryRunRecord)
                    .where(
                        QueryRunRecord.id == query_run_id,
                        QueryRunRecord.workspace_id == request.scope.workspace_id,
                    )
                    .with_for_update()
                )
                record = _require_running_query(record, request)
                session.add(
                    QueryResultRecord(
                        id=table_artifact.artifact_id,
                        query_run_id=query_run_id,
                        workspace_id=request.scope.workspace_id,
                        schema_version=DATA_EXPLORER_SCHEMA_VERSION,
                        columns=list(table_artifact.columns),
                        rows=[list(row) for row in table_artifact.rows],
                        truncated=table_artifact.truncated,
                        content_sha256=table_artifact.content_sha256,
                        created_at=table_artifact.created_at,
                    )
                )
                if chart_artifact is not None:
                    session.add(
                        ChartSpecRecord(
                            id=chart_artifact.artifact_id,
                            query_run_id=query_run_id,
                            workspace_id=request.scope.workspace_id,
                            schema_version=DATA_EXPLORER_SCHEMA_VERSION,
                            chart_type=chart_artifact.chart_type,
                            option=dict(chart_artifact.option),
                            content_sha256=chart_artifact.content_sha256,
                            created_at=chart_artifact.created_at,
                        )
                    )
                if request.agent_run_id is not None:
                    originating_step_id = await session.scalar(
                        select(ToolCallRecord.execution_step_id).where(
                            ToolCallRecord.id == request.tool_call_id,
                            ToolCallRecord.run_id == request.agent_run_id,
                            ToolCallRecord.workspace_id == request.scope.workspace_id,
                        )
                    )
                    if originating_step_id is None:
                        raise DataExplorerPersistenceError()
                    session.add(
                        RunArtifactRecord(
                            id=table_artifact.artifact_id,
                            workspace_id=request.scope.workspace_id,
                            run_id=request.agent_run_id,
                            originating_step_id=originating_step_id,
                            kind=RunArtifactKind.TABLE,
                            resource_ref=f"query-results/{table_artifact.artifact_id}",
                            content_sha256=table_artifact.content_sha256,
                            version=1,
                        )
                    )
                    if chart_artifact is not None:
                        session.add(
                            RunArtifactRecord(
                                id=chart_artifact.artifact_id,
                                workspace_id=request.scope.workspace_id,
                                run_id=request.agent_run_id,
                                originating_step_id=originating_step_id,
                                kind=RunArtifactKind.CHART,
                                resource_ref=f"chart-specs/{chart_artifact.artifact_id}",
                                content_sha256=chart_artifact.content_sha256,
                                version=1,
                            )
                        )
                terminal_at = datetime.now(UTC)
                record.schema_snapshot_id = snapshot.snapshot_id
                record.validated_sql = validated.sql
                record.status = QueryRunStatus.COMPLETED
                record.plan_cost = Decimal(str(result.plan_cost))
                record.plan_rows = result.plan_rows
                record.row_count = len(result.rows)
                record.result_content_sha256 = table_artifact.content_sha256
                record.error_code = None
                record.terminal_at = terminal_at
                record.updated_at = terminal_at
        except SQLAlchemyError as error:
            raise DataExplorerPersistenceError(sqlstate=safe_sqlstate(error)) from None
        return _query_result(record, table=table_artifact, chart=chart_artifact)

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
    ) -> QueryRunResult:
        try:
            async with self._session_factory.begin() as session:
                record = await session.scalar(
                    select(QueryRunRecord)
                    .where(
                        QueryRunRecord.id == query_run_id,
                        QueryRunRecord.workspace_id == request.scope.workspace_id,
                    )
                    .with_for_update()
                )
                record = _require_running_query(record, request)
                terminal_at = datetime.now(UTC)
                record.schema_snapshot_id = None if snapshot is None else snapshot.snapshot_id
                record.validated_sql = None if validated is None else validated.sql
                record.status = QueryRunStatus.FAILED
                record.plan_cost = None if plan_cost is None else Decimal(str(plan_cost))
                record.plan_rows = plan_rows
                record.error_code = error_code
                record.terminal_at = terminal_at
                record.updated_at = terminal_at
        except SQLAlchemyError as error:
            raise DataExplorerPersistenceError(sqlstate=safe_sqlstate(error)) from None
        return _query_result(record, table=None, chart=None)

    async def get_query(self, scope: WorkspaceScope, query_run_id: UUID) -> QueryRunResult:
        try:
            async with self._session_factory() as session:
                record = await session.scalar(
                    select(QueryRunRecord).where(
                        QueryRunRecord.id == query_run_id,
                        QueryRunRecord.workspace_id == scope.workspace_id,
                    )
                )
                if record is None:
                    raise QueryRunNotFoundError
                table = await session.scalar(
                    select(QueryResultRecord).where(
                        QueryResultRecord.query_run_id == query_run_id,
                        QueryResultRecord.workspace_id == scope.workspace_id,
                    )
                )
                chart = await session.scalar(
                    select(ChartSpecRecord).where(
                        ChartSpecRecord.query_run_id == query_run_id,
                        ChartSpecRecord.workspace_id == scope.workspace_id,
                    )
                )
        except QueryRunNotFoundError:
            raise
        except SQLAlchemyError as error:
            raise DataExplorerPersistenceError(sqlstate=safe_sqlstate(error)) from None
        return _query_result(
            record,
            table=None if table is None else _table_artifact(table),
            chart=None if chart is None else _chart_artifact(chart),
        )

    async def list_queries(
        self, scope: WorkspaceScope, *, limit: int
    ) -> tuple[QueryRunSummary, ...]:
        try:
            async with self._session_factory() as session:
                records = tuple(
                    await session.scalars(
                        select(QueryRunRecord)
                        .where(QueryRunRecord.workspace_id == scope.workspace_id)
                        .order_by(QueryRunRecord.created_at.desc(), QueryRunRecord.id.desc())
                        .limit(limit)
                    )
                )
        except SQLAlchemyError as error:
            raise DataExplorerPersistenceError(sqlstate=safe_sqlstate(error)) from None
        return tuple(_query_summary(record) for record in records)


def _require_running_query(
    record: QueryRunRecord | None,
    request: QueryExecutionRequest,
) -> QueryRunRecord:
    if record is None:
        raise QueryRunNotFoundError
    if (
        record.status is not QueryRunStatus.RUNNING
        or record.connection_id != request.connection_id
        or record.actor_user_id != request.scope.user_id
        or record.agent_run_id != request.agent_run_id
        or record.tool_call_id != request.tool_call_id
    ):
        raise DataExplorerPersistenceError()
    return record


def _connection_summary(record: DataConnectionRecord) -> DataConnectionSummary:
    return DataConnectionSummary(
        connection_id=record.id,
        workspace_id=record.workspace_id,
        name=record.name,
        dialect=record.dialect,
        secret_reference=record.secret_reference,
        status=record.status,
        last_error_code=record.last_error_code,
        created_at=record.created_at.astimezone(UTC),
        updated_at=record.updated_at.astimezone(UTC),
    )


def _table_artifact(record: QueryResultRecord) -> QueryResultArtifact:
    return QueryResultArtifact(
        artifact_id=record.id,
        query_run_id=record.query_run_id,
        workspace_id=record.workspace_id,
        columns=tuple(record.columns),
        rows=tuple(tuple(row) for row in record.rows),
        truncated=record.truncated,
        content_sha256=record.content_sha256,
        created_at=record.created_at.astimezone(UTC),
    )


def _chart_artifact(record: ChartSpecRecord) -> ChartArtifact:
    return ChartArtifact(
        artifact_id=record.id,
        query_run_id=record.query_run_id,
        workspace_id=record.workspace_id,
        chart_type=record.chart_type,
        option=MappingProxyType(dict(record.option)),
        content_sha256=record.content_sha256,
        created_at=record.created_at.astimezone(UTC),
    )


def _query_result(
    record: QueryRunRecord,
    *,
    table: QueryResultArtifact | None,
    chart: ChartArtifact | None,
) -> QueryRunResult:
    return QueryRunResult(
        query_run_id=record.id,
        connection_id=record.connection_id,
        workspace_id=record.workspace_id,
        status=record.status,
        question=record.question,
        generated_sql=record.generated_sql,
        validated_sql=record.validated_sql,
        schema_snapshot_id=record.schema_snapshot_id,
        row_count=record.row_count,
        plan_cost=None if record.plan_cost is None else float(record.plan_cost),
        plan_rows=record.plan_rows,
        error_code=record.error_code,
        table_artifact=table,
        chart_artifact=chart,
        created_at=record.created_at.astimezone(UTC),
        terminal_at=None if record.terminal_at is None else record.terminal_at.astimezone(UTC),
    )


def _query_summary(record: QueryRunRecord) -> QueryRunSummary:
    return QueryRunSummary(
        query_run_id=record.id,
        connection_id=record.connection_id,
        workspace_id=record.workspace_id,
        status=record.status,
        row_count=record.row_count,
        error_code=record.error_code,
        created_at=record.created_at.astimezone(UTC),
        terminal_at=None if record.terminal_at is None else record.terminal_at.astimezone(UTC),
    )


async def _database_now(session: AsyncSession) -> datetime:
    value = await session.scalar(select(func.now()))
    if value is None:
        raise DataExplorerPersistenceError()
    return value.astimezone(UTC)
