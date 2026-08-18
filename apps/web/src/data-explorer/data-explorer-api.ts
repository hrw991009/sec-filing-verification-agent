import type { components } from "@industry-platform/api-contract";

import { apiClient, unwrapData, withAccessToken } from "../api/api";

export type DataConnection = components["schemas"]["DataConnectionResponse"];
export type TableSchema = components["schemas"]["TableSchemaResponse"];
export type DatabaseRows = components["schemas"]["DatabaseRowsResponse"];
export type QueryRun = components["schemas"]["QueryRunResponse"];
export type QueryRunSummary = components["schemas"]["QueryRunSummaryResponse"];
export type ChartType = components["schemas"]["ChartType"];

function auth(accessToken: string) {
  return { Authorization: `Bearer ${accessToken}` };
}

export function ensureSampleConnection(workspaceId: string): Promise<DataConnection> {
  return withAccessToken(async (accessToken) =>
    unwrapData<DataConnection>(
      await apiClient.POST("/api/v1/workspaces/{workspace_id}/data-connections/sample", {
        headers: auth(accessToken),
        params: { path: { workspace_id: workspaceId } },
      }),
    ),
  );
}

export function listDataConnections(workspaceId: string): Promise<DataConnection[]> {
  return withAccessToken(async (accessToken) => {
    const response = unwrapData<components["schemas"]["DataConnectionCollectionResponse"]>(
      await apiClient.GET("/api/v1/workspaces/{workspace_id}/data-connections", {
        headers: auth(accessToken),
        params: { path: { workspace_id: workspaceId } },
      }),
    );
    return response.connections;
  });
}

export function testDataConnection(
  workspaceId: string,
  connectionId: string,
): Promise<DataConnection> {
  return withAccessToken(async (accessToken) =>
    unwrapData<DataConnection>(
      await apiClient.POST(
        "/api/v1/workspaces/{workspace_id}/data-connections/{connection_id}/test",
        {
          headers: auth(accessToken),
          params: { path: { connection_id: connectionId, workspace_id: workspaceId } },
        },
      ),
    ),
  );
}

export function listTables(workspaceId: string, connectionId: string): Promise<TableSchema[]> {
  return withAccessToken(async (accessToken) => {
    const response = unwrapData<components["schemas"]["TableCollectionResponse"]>(
      await apiClient.GET(
        "/api/v1/workspaces/{workspace_id}/data-connections/{connection_id}/tables",
        {
          headers: auth(accessToken),
          params: { path: { connection_id: connectionId, workspace_id: workspaceId } },
        },
      ),
    );
    return response.tables;
  });
}

export function browseTableRows(
  workspaceId: string,
  connectionId: string,
  table: TableSchema,
  offset: number,
): Promise<DatabaseRows> {
  return withAccessToken(async (accessToken) =>
    unwrapData<DatabaseRows>(
      await apiClient.GET(
        "/api/v1/workspaces/{workspace_id}/data-connections/{connection_id}/tables/{schema_name}/{table_name}/rows",
        {
          headers: auth(accessToken),
          params: {
            path: {
              connection_id: connectionId,
              schema_name: table.schema_name,
              table_name: table.table_name,
              workspace_id: workspaceId,
            },
            query: { limit: 20, offset },
          },
        },
      ),
    ),
  );
}

export function executeQuery(
  workspaceId: string,
  body: components["schemas"]["ExecuteQueryRequest"],
): Promise<QueryRun> {
  return withAccessToken(async (accessToken) =>
    unwrapData<QueryRun>(
      await apiClient.POST("/api/v1/workspaces/{workspace_id}/query-runs", {
        body,
        headers: auth(accessToken),
        params: { path: { workspace_id: workspaceId } },
      }),
    ),
  );
}

export function listQueryRuns(workspaceId: string): Promise<QueryRunSummary[]> {
  return withAccessToken(async (accessToken) => {
    const response = unwrapData<components["schemas"]["QueryRunCollectionResponse"]>(
      await apiClient.GET("/api/v1/workspaces/{workspace_id}/query-runs", {
        headers: auth(accessToken),
        params: { path: { workspace_id: workspaceId }, query: { limit: 20 } },
      }),
    );
    return response.query_runs;
  });
}

export function getQueryRun(workspaceId: string, queryRunId: string): Promise<QueryRun> {
  return withAccessToken(async (accessToken) =>
    unwrapData<QueryRun>(
      await apiClient.GET("/api/v1/workspaces/{workspace_id}/query-runs/{query_run_id}", {
        headers: auth(accessToken),
        params: { path: { query_run_id: queryRunId, workspace_id: workspaceId } },
      }),
    ),
  );
}
