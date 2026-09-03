import type { components } from "@sec-filing-verification/api-contract";

import { apiClient, unwrapData, withAccessToken } from "../api/api";

export type Evidence = components["schemas"]["EvidenceResponse"];
export type EvidenceNormalization = components["schemas"]["EvidenceNormalizationResponse"];
export type ResearchClaim = components["schemas"]["ResearchClaimResponse"];
export type ClaimGraph = components["schemas"]["ClaimGraphResponse"];
export type EvidenceStatus = components["schemas"]["EvidenceStatus"];
export type EvidenceKind = components["schemas"]["EvidenceKind"];

export interface EvidenceListOptions {
  readonly kind?: EvidenceKind;
  readonly limit?: number;
  readonly originRunId?: string;
  readonly status?: EvidenceStatus;
}

function auth(accessToken: string) {
  return { Authorization: `Bearer ${accessToken}` };
}

function pageSize(limit = 20): number {
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 100) {
    throw new RangeError("The Evidence page size must be between 1 and 100.");
  }
  return limit;
}

export function normalizeObservation(
  workspaceId: string,
  request: components["schemas"]["NormalizeObservationRequest"],
): Promise<EvidenceNormalization> {
  return withAccessToken(async (accessToken) =>
    unwrapData<EvidenceNormalization>(
      await apiClient.POST("/api/v1/workspaces/{workspace_id}/evidence/normalizations", {
        body: request,
        headers: auth(accessToken),
        params: { path: { workspace_id: workspaceId } },
      }),
    ),
  );
}

export function listEvidence(
  workspaceId: string,
  options: EvidenceListOptions = {},
): Promise<Evidence[]> {
  return withAccessToken(async (accessToken) => {
    const response = unwrapData<components["schemas"]["EvidenceCollectionResponse"]>(
      await apiClient.GET("/api/v1/workspaces/{workspace_id}/evidence", {
        headers: auth(accessToken),
        params: {
          path: { workspace_id: workspaceId },
          query: {
            kind: options.kind ?? null,
            limit: pageSize(options.limit),
            origin_run_id: options.originRunId ?? null,
            status: options.status ?? null,
          },
        },
      }),
    );
    return response.evidence;
  });
}

export function getEvidence(workspaceId: string, evidenceId: string): Promise<Evidence> {
  return withAccessToken(async (accessToken) =>
    unwrapData<Evidence>(
      await apiClient.GET("/api/v1/workspaces/{workspace_id}/evidence/{evidence_id}", {
        headers: auth(accessToken),
        params: { path: { evidence_id: evidenceId, workspace_id: workspaceId } },
      }),
    ),
  );
}

export function invalidateEvidence(
  workspaceId: string,
  evidenceId: string,
  revision: number,
  request: components["schemas"]["InvalidateEvidenceRequest"],
): Promise<Evidence> {
  if (!Number.isSafeInteger(revision) || revision < 1) {
    throw new RangeError("The Evidence revision is invalid.");
  }
  return withAccessToken(async (accessToken) =>
    unwrapData<Evidence>(
      await apiClient.POST("/api/v1/workspaces/{workspace_id}/evidence/{evidence_id}/invalidate", {
        body: request,
        headers: auth(accessToken),
        params: {
          header: { "If-Match": `"${String(revision)}"` },
          path: { evidence_id: evidenceId, workspace_id: workspaceId },
        },
      }),
    ),
  );
}

export function listResearchClaims(
  workspaceId: string,
  researchRunId: string,
  limit = 20,
): Promise<ResearchClaim[]> {
  return withAccessToken(async (accessToken) => {
    const response = unwrapData<components["schemas"]["ResearchClaimCollectionResponse"]>(
      await apiClient.GET(
        "/api/v1/workspaces/{workspace_id}/research-runs/{research_run_id}/claims",
        {
          headers: auth(accessToken),
          params: {
            path: { research_run_id: researchRunId, workspace_id: workspaceId },
            query: { limit: pageSize(limit) },
          },
        },
      ),
    );
    return response.claims;
  });
}

export function getClaimGraph(workspaceId: string, researchRunId: string): Promise<ClaimGraph> {
  return withAccessToken(async (accessToken) =>
    unwrapData<ClaimGraph>(
      await apiClient.GET(
        "/api/v1/workspaces/{workspace_id}/research-runs/{research_run_id}/graph",
        {
          headers: auth(accessToken),
          params: { path: { research_run_id: researchRunId, workspace_id: workspaceId } },
        },
      ),
    ),
  );
}
