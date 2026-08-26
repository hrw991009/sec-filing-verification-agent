import type { components } from "@industry-platform/api-contract";

import { apiClient, unwrapData, withAccessToken } from "../api/api";

export type ResearchRun = components["schemas"]["ResearchRunDetailResponse"];
export type ResearchApproval = components["schemas"]["ResearchApprovalResponse"];
export type ResearchDurability = components["schemas"]["ResearchDurabilityTimelineResponse"];
export type StartResearchRequest = components["schemas"]["StartResearchRequest"];
export type StartResearchResponse = components["schemas"]["StartResearchResponse"];
export type ResumeResearchResponse = components["schemas"]["ResumeResearchResponse"];

function authorization(accessToken: string) {
  return { Authorization: `Bearer ${accessToken}` };
}

function pageSize(limit = 20): number {
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 100) {
    throw new RangeError("The Research page size must be between 1 and 100.");
  }
  return limit;
}

export function startResearch(
  workspaceId: string,
  request: StartResearchRequest,
  idempotencyKey: string,
): Promise<StartResearchResponse> {
  if (!idempotencyKey.trim()) {
    throw new TypeError("A Research idempotency key is required.");
  }
  return withAccessToken(async (accessToken) =>
    unwrapData<StartResearchResponse>(
      await apiClient.POST("/api/v1/workspaces/{workspace_id}/research-runs", {
        body: request,
        headers: authorization(accessToken),
        params: {
          header: { "Idempotency-Key": idempotencyKey },
          path: { workspace_id: workspaceId },
        },
      }),
    ),
  );
}

export function listResearchRuns(workspaceId: string, limit = 20): Promise<ResearchRun[]> {
  return withAccessToken(async (accessToken) => {
    const response = unwrapData<components["schemas"]["ResearchRunCollectionResponse"]>(
      await apiClient.GET("/api/v1/workspaces/{workspace_id}/research-runs", {
        headers: authorization(accessToken),
        params: { path: { workspace_id: workspaceId }, query: { limit: pageSize(limit) } },
      }),
    );
    return response.research_runs;
  });
}

export function getResearchRun(workspaceId: string, researchRunId: string): Promise<ResearchRun> {
  return withAccessToken(async (accessToken) =>
    unwrapData<ResearchRun>(
      await apiClient.GET("/api/v1/workspaces/{workspace_id}/research-runs/{research_run_id}", {
        headers: authorization(accessToken),
        params: {
          path: { research_run_id: researchRunId, workspace_id: workspaceId },
        },
      }),
    ),
  );
}

export function getResearchDurability(
  workspaceId: string,
  researchRunId: string,
): Promise<ResearchDurability> {
  return withAccessToken(async (accessToken) =>
    unwrapData<ResearchDurability>(
      await apiClient.GET(
        "/api/v1/workspaces/{workspace_id}/research-runs/{research_run_id}/durability",
        {
          headers: authorization(accessToken),
          params: { path: { research_run_id: researchRunId, workspace_id: workspaceId } },
        },
      ),
    ),
  );
}

export function decideResearchApproval(
  workspaceId: string,
  researchRunId: string,
  request: components["schemas"]["DecideResearchApprovalRequest"],
): Promise<ResearchApproval> {
  return withAccessToken(async (accessToken) =>
    unwrapData<ResearchApproval>(
      await apiClient.POST(
        "/api/v1/workspaces/{workspace_id}/research-runs/{research_run_id}/approval-decisions",
        {
          body: request,
          headers: authorization(accessToken),
          params: { path: { research_run_id: researchRunId, workspace_id: workspaceId } },
        },
      ),
    ),
  );
}

export function resumeResearch(
  workspaceId: string,
  researchRunId: string,
  request: components["schemas"]["ResumeResearchRequest"],
): Promise<ResumeResearchResponse> {
  return withAccessToken(async (accessToken) =>
    unwrapData<ResumeResearchResponse>(
      await apiClient.POST(
        "/api/v1/workspaces/{workspace_id}/research-runs/{research_run_id}/resume",
        {
          body: request,
          headers: authorization(accessToken),
          params: { path: { research_run_id: researchRunId, workspace_id: workspaceId } },
        },
      ),
    ),
  );
}
