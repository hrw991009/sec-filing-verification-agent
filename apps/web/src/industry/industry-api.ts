import type { components } from "@sec-filing-verification/api-contract";

import { apiClient, unwrapData, withAccessToken } from "../api/api";

export type Industry = components["schemas"]["IndustryResponse"];
export type IndustryPreference = components["schemas"]["IndustryPreferenceResponse"];
export type ProviderStatus = components["schemas"]["ProviderStatusResponse"];
export type SourceItem = components["schemas"]["SourceItemResponse"];
export type CollectionRun = components["schemas"]["CollectionRunResponse"];
export type CollectionSchedule = components["schemas"]["CollectionScheduleResponse"];
export type SourceKind = components["schemas"]["SourceKind"];

function auth(accessToken: string) {
  return { Authorization: `Bearer ${accessToken}` };
}

export function listIndustries(): Promise<Industry[]> {
  return withAccessToken(async (accessToken) => {
    const response = unwrapData<components["schemas"]["IndustryCollectionResponse"]>(
      await apiClient.GET("/api/v1/industries", {
        headers: auth(accessToken),
        params: { query: {} },
      }),
    );
    return response.industries;
  });
}

export function getIndustryPreference(workspaceId: string): Promise<IndustryPreference | null> {
  return withAccessToken(async (accessToken) =>
    unwrapData<IndustryPreference | null>(
      await apiClient.GET("/api/v1/workspaces/{workspace_id}/industry-preference", {
        headers: auth(accessToken),
        params: { path: { workspace_id: workspaceId } },
      }),
    ),
  );
}

export function setIndustryPreference(
  workspaceId: string,
  industryId: string,
): Promise<IndustryPreference> {
  return withAccessToken(async (accessToken) =>
    unwrapData<IndustryPreference>(
      await apiClient.PATCH("/api/v1/workspaces/{workspace_id}/industry-preference", {
        body: { industry_id: industryId },
        headers: auth(accessToken),
        params: { path: { workspace_id: workspaceId } },
      }),
    ),
  );
}

export function listProviderStatuses(workspaceId: string): Promise<ProviderStatus[]> {
  return withAccessToken(async (accessToken) => {
    const response = unwrapData<components["schemas"]["ProviderStatusCollectionResponse"]>(
      await apiClient.GET("/api/v1/workspaces/{workspace_id}/industry-sources/readiness", {
        headers: auth(accessToken),
        params: { path: { workspace_id: workspaceId } },
      }),
    );
    return response.providers;
  });
}

export function listSourceItems(
  workspaceId: string,
  industryId: string,
  kind: SourceKind,
): Promise<SourceItem[]> {
  return withAccessToken(async (accessToken) => {
    const response = unwrapData<components["schemas"]["SourceItemCollectionResponse"]>(
      await apiClient.GET("/api/v1/workspaces/{workspace_id}/industry-sources/items", {
        headers: auth(accessToken),
        params: {
          path: { workspace_id: workspaceId },
          query: { industry_id: industryId, kind, limit: 40, offset: 0 },
        },
      }),
    );
    return response.items;
  });
}

export function listCollectionRuns(workspaceId: string): Promise<CollectionRun[]> {
  return withAccessToken(async (accessToken) => {
    const response = unwrapData<components["schemas"]["CollectionRunCollectionResponse"]>(
      await apiClient.GET("/api/v1/workspaces/{workspace_id}/industry-collections/runs", {
        headers: auth(accessToken),
        params: { path: { workspace_id: workspaceId }, query: { limit: 20 } },
      }),
    );
    return response.runs;
  });
}

export function listCollectionSchedules(workspaceId: string): Promise<CollectionSchedule[]> {
  return withAccessToken(async (accessToken) => {
    const response = unwrapData<components["schemas"]["CollectionScheduleCollectionResponse"]>(
      await apiClient.GET("/api/v1/workspaces/{workspace_id}/industry-collections/schedules", {
        headers: auth(accessToken),
        params: { path: { workspace_id: workspaceId } },
      }),
    );
    return response.schedules;
  });
}

export function createCollectionSchedule(
  workspaceId: string,
  industryId: string,
  kind: SourceKind,
): Promise<components["schemas"]["CollectionScheduleCreatedResponse"]> {
  return withAccessToken(async (accessToken) =>
    unwrapData<components["schemas"]["CollectionScheduleCreatedResponse"]>(
      await apiClient.POST("/api/v1/workspaces/{workspace_id}/industry-collections/schedules", {
        body: {
          catch_up_window_seconds: 86_400,
          cron_expression: "0 */6 * * *",
          industry_id: industryId,
          kind,
          max_catch_up: 4,
          misfire_policy: "coalesce_latest",
          timezone_name: "Asia/Shanghai",
        },
        headers: auth(accessToken),
        params: { path: { workspace_id: workspaceId } },
      }),
    ),
  );
}

export function triggerCollection(
  workspaceId: string,
  scheduleId: string,
): Promise<components["schemas"]["TriggerCollectionResponse"]> {
  return withAccessToken(async (accessToken) =>
    unwrapData<components["schemas"]["TriggerCollectionResponse"]>(
      await apiClient.POST(
        "/api/v1/workspaces/{workspace_id}/industry-collections/schedules/{schedule_id}/runs",
        {
          body: { trigger_id: crypto.randomUUID() },
          headers: auth(accessToken),
          params: { path: { schedule_id: scheduleId, workspace_id: workspaceId } },
        },
      ),
    ),
  );
}
