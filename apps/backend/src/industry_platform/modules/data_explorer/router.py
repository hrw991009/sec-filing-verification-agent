"""Authenticated Data Explorer and safe Text2SQL delivery."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status

from industry_platform.core.http import (
    get_trace_id,
    problem_openapi_response,
    set_no_store_headers,
)
from industry_platform.modules.data_explorer.domain import (
    ChartRequest,
    DatabaseRows,
    DataConnectionSummary,
    QueryRunResult,
    QueryRunSummary,
    TableSchema,
)
from industry_platform.modules.data_explorer.resources import (
    DataExplorerResources,
    get_data_explorer_resources,
)
from industry_platform.modules.data_explorer.schemas import (
    ChartArtifactResponse,
    ColumnSchemaResponse,
    DatabaseRowsResponse,
    DataConnectionCollectionResponse,
    DataConnectionResponse,
    ExecuteQueryRequest,
    IndexSchemaResponse,
    QueryRunCollectionResponse,
    QueryRunResponse,
    QueryRunSummaryResponse,
    TableArtifactResponse,
    TableCollectionResponse,
    TableSchemaResponse,
)
from industry_platform.modules.identity.domain import AuthenticatedPrincipal, TraceId
from industry_platform.modules.identity.http_auth import require_authenticated_principal
from industry_platform.modules.workspaces.domain import (
    WorkspaceAccessDeniedError,
    WorkspaceAction,
    WorkspaceScope,
)
from industry_platform.modules.workspaces.policy import scope_allows

router = APIRouter(tags=["data-explorer"])
type OpenApiResponses = dict[int | str, dict[str, Any]]

_RESPONSES: OpenApiResponses = {
    status.HTTP_401_UNAUTHORIZED: problem_openapi_response("Invalid authenticated session"),
    status.HTTP_403_FORBIDDEN: problem_openapi_response("Workspace access denied"),
    status.HTTP_404_NOT_FOUND: problem_openapi_response("Data Explorer resource not found"),
    status.HTTP_422_UNPROCESSABLE_CONTENT: problem_openapi_response("Request validation failed"),
    status.HTTP_503_SERVICE_UNAVAILABLE: problem_openapi_response(
        "Data Explorer temporarily unavailable"
    ),
}


@router.post(
    "/workspaces/{workspace_id}/data-connections/sample",
    response_model=DataConnectionResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_RESPONSES,
)
async def ensure_sample_connection(
    workspace_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[DataExplorerResources, Depends(get_data_explorer_resources)],
) -> DataConnectionResponse:
    scope = _authorized_scope(principal, workspace_id, WorkspaceAction.CREATE_RESOURCE)
    connection = await resources.service.ensure_sample_connection(scope)
    set_no_store_headers(response)
    return _connection_response(connection)


@router.get(
    "/workspaces/{workspace_id}/data-connections",
    response_model=DataConnectionCollectionResponse,
    responses=_RESPONSES,
)
async def list_connections(
    workspace_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[DataExplorerResources, Depends(get_data_explorer_resources)],
) -> DataConnectionCollectionResponse:
    scope = _authorized_scope(principal, workspace_id, WorkspaceAction.VIEW)
    connections = await resources.service.list_connections(scope)
    set_no_store_headers(response)
    return DataConnectionCollectionResponse(
        connections=[_connection_response(connection) for connection in connections]
    )


@router.post(
    "/workspaces/{workspace_id}/data-connections/{connection_id}/test",
    response_model=DataConnectionResponse,
    responses=_RESPONSES,
)
async def test_connection(
    workspace_id: UUID,
    connection_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[DataExplorerResources, Depends(get_data_explorer_resources)],
) -> DataConnectionResponse:
    scope = _authorized_scope(principal, workspace_id, WorkspaceAction.CREATE_RESOURCE)
    connection = await resources.service.test_connection(scope, connection_id)
    set_no_store_headers(response)
    return _connection_response(connection)


@router.get(
    "/workspaces/{workspace_id}/data-connections/{connection_id}/tables",
    response_model=TableCollectionResponse,
    responses=_RESPONSES,
)
async def list_tables(
    workspace_id: UUID,
    connection_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[DataExplorerResources, Depends(get_data_explorer_resources)],
) -> TableCollectionResponse:
    scope = _authorized_scope(principal, workspace_id, WorkspaceAction.VIEW)
    tables = await resources.service.list_tables(scope, connection_id)
    set_no_store_headers(response)
    return TableCollectionResponse(tables=[_table_response(table) for table in tables])


@router.get(
    "/workspaces/{workspace_id}/data-connections/{connection_id}/tables/"
    "{schema_name}/{table_name}/schema",
    response_model=TableSchemaResponse,
    responses=_RESPONSES,
)
async def get_table_schema(
    workspace_id: UUID,
    connection_id: UUID,
    schema_name: str,
    table_name: str,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[DataExplorerResources, Depends(get_data_explorer_resources)],
) -> TableSchemaResponse:
    scope = _authorized_scope(principal, workspace_id, WorkspaceAction.VIEW)
    table = await resources.service.get_table_schema(
        scope,
        connection_id,
        schema_name=schema_name,
        table_name=table_name,
    )
    set_no_store_headers(response)
    return _table_response(table)


@router.get(
    "/workspaces/{workspace_id}/data-connections/{connection_id}/tables/"
    "{schema_name}/{table_name}/rows",
    response_model=DatabaseRowsResponse,
    responses=_RESPONSES,
)
async def browse_rows(
    workspace_id: UUID,
    connection_id: UUID,
    schema_name: str,
    table_name: str,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[DataExplorerResources, Depends(get_data_explorer_resources)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> DatabaseRowsResponse:
    scope = _authorized_scope(principal, workspace_id, WorkspaceAction.VIEW)
    rows = await resources.service.browse_rows(
        scope,
        connection_id,
        schema_name=schema_name,
        table_name=table_name,
        limit=limit,
        offset=offset,
    )
    set_no_store_headers(response)
    return _rows_response(rows, limit=limit, offset=offset)


@router.post(
    "/workspaces/{workspace_id}/query-runs",
    response_model=QueryRunResponse,
    responses=_RESPONSES,
)
async def execute_query(
    workspace_id: UUID,
    payload: ExecuteQueryRequest,
    request: Request,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[DataExplorerResources, Depends(get_data_explorer_resources)],
) -> QueryRunResponse:
    scope = _authorized_scope(principal, workspace_id, WorkspaceAction.RUN_TOOL)
    result = await resources.service.execute_direct_query(
        scope,
        connection_id=payload.connection_id,
        question=payload.question,
        generated_sql=payload.generated_sql,
        chart=ChartRequest(
            chart_type=payload.chart.chart_type,
            x_column=payload.chart.x_column,
            y_column=payload.chart.y_column,
            series_column=payload.chart.series_column,
            title=payload.chart.title,
        ),
        trace_id=TraceId(get_trace_id(request)),
    )
    set_no_store_headers(response)
    return _query_response(result)


@router.get(
    "/workspaces/{workspace_id}/query-runs",
    response_model=QueryRunCollectionResponse,
    responses=_RESPONSES,
)
async def list_query_runs(
    workspace_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[DataExplorerResources, Depends(get_data_explorer_resources)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> QueryRunCollectionResponse:
    scope = _authorized_scope(principal, workspace_id, WorkspaceAction.VIEW)
    query_runs = await resources.service.list_queries(scope, limit=limit)
    set_no_store_headers(response)
    return QueryRunCollectionResponse(
        query_runs=[_query_summary_response(query_run) for query_run in query_runs]
    )


@router.get(
    "/workspaces/{workspace_id}/query-runs/{query_run_id}",
    response_model=QueryRunResponse,
    responses=_RESPONSES,
)
async def get_query_run(
    workspace_id: UUID,
    query_run_id: UUID,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_authenticated_principal)],
    resources: Annotated[DataExplorerResources, Depends(get_data_explorer_resources)],
) -> QueryRunResponse:
    scope = _authorized_scope(principal, workspace_id, WorkspaceAction.VIEW)
    result = await resources.service.get_query(scope, query_run_id)
    set_no_store_headers(response)
    return _query_response(result)


def _authorized_scope(
    principal: AuthenticatedPrincipal,
    workspace_id: UUID,
    action: WorkspaceAction,
) -> WorkspaceScope:
    workspace = next(
        (candidate for candidate in principal.workspaces if candidate.workspace_id == workspace_id),
        None,
    )
    if workspace is None:
        raise WorkspaceAccessDeniedError
    scope = WorkspaceScope(
        workspace_id=workspace_id,
        user_id=principal.user_id,
        role=workspace.role,
    )
    if not scope_allows(scope, action):
        raise WorkspaceAccessDeniedError
    return scope


def _connection_response(connection: DataConnectionSummary) -> DataConnectionResponse:
    return DataConnectionResponse(
        id=connection.connection_id,
        name=connection.name,
        dialect=connection.dialect,
        status=connection.status,
        last_error_code=connection.last_error_code,
        created_at=connection.created_at,
        updated_at=connection.updated_at,
    )


def _table_response(table: TableSchema) -> TableSchemaResponse:
    return TableSchemaResponse(
        schema_name=table.schema_name,
        table_name=table.table_name,
        columns=[
            ColumnSchemaResponse(
                name=column.name,
                data_type=column.data_type,
                nullable=column.nullable,
                ordinal=column.ordinal,
            )
            for column in table.columns
        ],
        indexes=[
            IndexSchemaResponse(
                name=index.name,
                columns=list(index.columns),
                unique=index.unique,
                primary=index.primary,
            )
            for index in table.indexes
        ],
        estimated_rows=table.estimated_rows,
        total_bytes=table.total_bytes,
    )


def _rows_response(rows: DatabaseRows, *, limit: int, offset: int) -> DatabaseRowsResponse:
    return DatabaseRowsResponse(
        columns=list(rows.columns),
        rows=[list(row) for row in rows.rows],
        truncated=rows.truncated,
        limit=limit,
        offset=offset,
    )


def _query_response(result: QueryRunResult) -> QueryRunResponse:
    table = result.table_artifact
    chart = result.chart_artifact
    return QueryRunResponse(
        id=result.query_run_id,
        connection_id=result.connection_id,
        status=result.status,
        question=result.question,
        generated_sql=result.generated_sql,
        validated_sql=result.validated_sql,
        schema_snapshot_id=result.schema_snapshot_id,
        row_count=result.row_count,
        plan_cost=result.plan_cost,
        plan_rows=result.plan_rows,
        error_code=result.error_code,
        table_artifact=(
            None
            if table is None
            else TableArtifactResponse(
                id=table.artifact_id,
                columns=list(table.columns),
                rows=[list(row) for row in table.rows],
                truncated=table.truncated,
                content_sha256=table.content_sha256,
                created_at=table.created_at,
            )
        ),
        chart_artifact=(
            None
            if chart is None
            else ChartArtifactResponse(
                id=chart.artifact_id,
                chart_type=chart.chart_type,
                option=dict(chart.option),
                content_sha256=chart.content_sha256,
                created_at=chart.created_at,
            )
        ),
        created_at=result.created_at,
        terminal_at=result.terminal_at,
    )


def _query_summary_response(result: QueryRunSummary) -> QueryRunSummaryResponse:
    return QueryRunSummaryResponse(
        id=result.query_run_id,
        connection_id=result.connection_id,
        status=result.status,
        row_count=result.row_count,
        error_code=result.error_code,
        created_at=result.created_at,
        terminal_at=result.terminal_at,
    )
