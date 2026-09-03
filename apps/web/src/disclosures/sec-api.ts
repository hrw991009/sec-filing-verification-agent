import type { components } from "@sec-filing-verification/api-contract";

import { apiClient, unwrapData, withAccessToken } from "../api/api";

export type SecFiling = components["schemas"]["SecFilingCandidateResponse"];
export type SecFilerResolution = components["schemas"]["SecFilerResolutionResponse"];
export type SecFilingDiff = components["schemas"]["SecFilingDiffResponse"];
export type SecFilingImport = components["schemas"]["SecWorkspaceFilingImportResponse"];
export type SecFilingSearch = components["schemas"]["SecFilingSearchResponse"];
export type SecFilingSection = components["schemas"]["SecFilingSectionResponse"];
export type SecXbrlFact = components["schemas"]["SecXbrlFactResponse"];
export type SecXbrlFactCollection = components["schemas"]["SecXbrlFactCollectionResponse"];
export type SecXbrlSourceKind = components["schemas"]["SecXbrlSourceKind"];
export type SecXbrlSync = components["schemas"]["SecXbrlSyncResponse"];
export type SecMonitor = components["schemas"]["SecMonitorResponse"];
export type SecMonitorRun = components["schemas"]["TriggerSecMonitorRunResponse"];
export type SecDisclosureCase = components["schemas"]["SecDisclosureCaseResponse"];

export interface FilingSelection {
  readonly cik: string;
  readonly forms: readonly ("10-K" | "10-Q")[];
  readonly reportPeriodStart: string;
  readonly reportPeriodEnd: string;
  readonly asOf: string;
}

function authorization(accessToken: string) {
  return { Authorization: `Bearer ${accessToken}` };
}

export function resolveSecFiler(workspaceId: string, query: string): Promise<SecFilerResolution> {
  return withAccessToken(async (accessToken) =>
    unwrapData<SecFilerResolution>(
      await apiClient.GET("/api/v1/workspaces/{workspace_id}/disclosures/filers/resolve", {
        headers: authorization(accessToken),
        params: {
          path: { workspace_id: workspaceId },
          query: { limit: 1, query },
        },
      }),
    ),
  );
}

export function listSecFilings(
  workspaceId: string,
  selection: FilingSelection,
): Promise<SecFiling[]> {
  return withAccessToken(async (accessToken) => {
    const response = unwrapData<components["schemas"]["SecFilingSelectionResponse"]>(
      await apiClient.GET("/api/v1/workspaces/{workspace_id}/disclosures/filings", {
        headers: authorization(accessToken),
        params: {
          path: { workspace_id: workspaceId },
          query: {
            amendment_policy: "as_filed",
            as_of: selection.asOf,
            cik: selection.cik,
            forms: [...selection.forms],
            report_period_end: selection.reportPeriodEnd,
            report_period_start: selection.reportPeriodStart,
          },
        },
      }),
    );
    return response.filings;
  });
}

export function importSecFiling(
  workspaceId: string,
  accession: string,
  knowledgeBaseId: string,
  asOf: string,
): Promise<SecFilingImport> {
  return withAccessToken(async (accessToken) =>
    unwrapData<SecFilingImport>(
      await apiClient.POST(
        "/api/v1/workspaces/{workspace_id}/disclosures/filings/{accession}/imports",
        {
          body: { as_of: asOf, knowledge_base_id: knowledgeBaseId },
          headers: authorization(accessToken),
          params: { path: { accession, workspace_id: workspaceId } },
        },
      ),
    ),
  );
}

export function listSecFilingImports(workspaceId: string): Promise<SecFilingImport[]> {
  return withAccessToken(async (accessToken) => {
    const response = unwrapData<components["schemas"]["SecFilingImportCollectionResponse"]>(
      await apiClient.GET("/api/v1/workspaces/{workspace_id}/disclosures/filing-imports", {
        headers: authorization(accessToken),
        params: { path: { workspace_id: workspaceId }, query: { limit: 100 } },
      }),
    );
    return response.imports;
  });
}

export function searchSecFiling(
  workspaceId: string,
  accession: string,
  knowledgeBaseId: string,
  asOf: string,
  query: string,
): Promise<SecFilingSearch> {
  return withAccessToken(async (accessToken) =>
    unwrapData<SecFilingSearch>(
      await apiClient.GET(
        "/api/v1/workspaces/{workspace_id}/disclosures/filings/{accession}/search",
        {
          headers: authorization(accessToken),
          params: {
            path: { accession, workspace_id: workspaceId },
            query: { as_of: asOf, knowledge_base_id: knowledgeBaseId, query },
          },
        },
      ),
    ),
  );
}

export function readSecFilingSection(
  workspaceId: string,
  accession: string,
  knowledgeBaseId: string,
  asOf: string,
  documentVersionId: string,
  chunkId: string,
): Promise<SecFilingSection> {
  return withAccessToken(async (accessToken) =>
    unwrapData<SecFilingSection>(
      await apiClient.GET(
        "/api/v1/workspaces/{workspace_id}/disclosures/filings/{accession}/sections/{chunk_id}",
        {
          headers: authorization(accessToken),
          params: {
            path: { accession, chunk_id: chunkId, workspace_id: workspaceId },
            query: {
              as_of: asOf,
              document_version_id: documentVersionId,
              knowledge_base_id: knowledgeBaseId,
            },
          },
        },
      ),
    ),
  );
}

export function syncSecXbrl(
  workspaceId: string,
  accession: string,
  knowledgeBaseId: string,
): Promise<SecXbrlSync> {
  return withAccessToken(async (accessToken) =>
    unwrapData<SecXbrlSync>(
      await apiClient.POST(
        "/api/v1/workspaces/{workspace_id}/disclosures/filings/{accession}/xbrl/sync",
        {
          body: { knowledge_base_id: knowledgeBaseId },
          headers: authorization(accessToken),
          params: { path: { accession, workspace_id: workspaceId } },
        },
      ),
    ),
  );
}

export function getSecXbrlFacts(
  workspaceId: string,
  accession: string,
  knowledgeBaseId: string,
  asOf: string,
  filters: {
    readonly concept: string | null;
    readonly sourceKinds: readonly SecXbrlSourceKind[];
    readonly taxonomy: string | null;
  },
): Promise<SecXbrlFactCollection> {
  return withAccessToken(async (accessToken) =>
    unwrapData<SecXbrlFactCollection>(
      await apiClient.GET(
        "/api/v1/workspaces/{workspace_id}/disclosures/filings/{accession}/xbrl/facts",
        {
          headers: authorization(accessToken),
          params: {
            path: { accession, workspace_id: workspaceId },
            query: {
              as_of: asOf,
              concept: filters.concept,
              knowledge_base_id: knowledgeBaseId,
              limit: 100,
              source_kinds: [...filters.sourceKinds],
              taxonomy: filters.taxonomy,
            },
          },
        },
      ),
    ),
  );
}

export function diffSecFilings(
  workspaceId: string,
  accession: string,
  knowledgeBaseId: string,
  scope: {
    readonly asOf: string;
    readonly cik: string;
    readonly form: "10-K" | "10-K/A" | "10-Q" | "10-Q/A";
    readonly reportPeriod: string;
    readonly scale: number;
    readonly unit: string;
  },
  comparisonAccession: string,
  sectionQuery: string,
): Promise<SecFilingDiff> {
  return withAccessToken(async (accessToken) =>
    unwrapData<SecFilingDiff>(
      await apiClient.GET(
        "/api/v1/workspaces/{workspace_id}/disclosures/filings/{accession}/diff",
        {
          headers: authorization(accessToken),
          params: {
            path: { accession, workspace_id: workspaceId },
            query: {
              as_of: scope.asOf,
              cik: scope.cik,
              comparison_accession: comparisonAccession,
              concept: null,
              fact_limit: 10,
              form: scope.form,
              knowledge_base_id: knowledgeBaseId,
              report_period: scope.reportPeriod,
              scale: scope.scale,
              section_query: sectionQuery,
              taxonomy: null,
              unit: scope.unit,
            },
          },
        },
      ),
    ),
  );
}

export function listSecMonitors(workspaceId: string): Promise<SecMonitor[]> {
  return withAccessToken(async (accessToken) => {
    const response = unwrapData<components["schemas"]["SecMonitorCollectionResponse"]>(
      await apiClient.GET("/api/v1/workspaces/{workspace_id}/disclosures/monitors", {
        headers: authorization(accessToken),
        params: { path: { workspace_id: workspaceId } },
      }),
    );
    return response.monitors;
  });
}

export function changeSecMonitorStatus(
  workspaceId: string,
  monitorId: string,
  revision: number,
  status: "active" | "paused",
): Promise<SecMonitor> {
  const path =
    status === "active"
      ? "/api/v1/workspaces/{workspace_id}/disclosures/monitors/{monitor_id}/resume"
      : "/api/v1/workspaces/{workspace_id}/disclosures/monitors/{monitor_id}/pause";
  return withAccessToken(async (accessToken) =>
    unwrapData<SecMonitor>(
      await apiClient.POST(path, {
        body: { expected_revision: revision },
        headers: authorization(accessToken),
        params: { path: { monitor_id: monitorId, workspace_id: workspaceId } },
      }),
    ),
  );
}

export function triggerSecMonitorRun(
  workspaceId: string,
  monitorId: string,
  revision: number,
): Promise<SecMonitorRun> {
  return withAccessToken(async (accessToken) =>
    unwrapData<SecMonitorRun>(
      await apiClient.POST(
        "/api/v1/workspaces/{workspace_id}/disclosures/monitors/{monitor_id}/runs",
        {
          body: { expected_revision: revision, trigger_id: crypto.randomUUID() },
          headers: authorization(accessToken),
          params: { path: { monitor_id: monitorId, workspace_id: workspaceId } },
        },
      ),
    ),
  );
}

export function deleteSecMonitor(
  workspaceId: string,
  monitorId: string,
  revision: number,
): Promise<SecMonitor> {
  return withAccessToken(async (accessToken) =>
    unwrapData<SecMonitor>(
      await apiClient.DELETE(
        "/api/v1/workspaces/{workspace_id}/disclosures/monitors/{monitor_id}",
        {
          body: { expected_revision: revision },
          headers: authorization(accessToken),
          params: { path: { monitor_id: monitorId, workspace_id: workspaceId } },
        },
      ),
    ),
  );
}

export function listSecDisclosureCases(
  workspaceId: string,
  monitorId: string | null = null,
): Promise<SecDisclosureCase[]> {
  return withAccessToken(async (accessToken) => {
    const response = unwrapData<components["schemas"]["SecDisclosureCaseCollectionResponse"]>(
      await apiClient.GET("/api/v1/workspaces/{workspace_id}/disclosures/cases", {
        headers: authorization(accessToken),
        params: {
          path: { workspace_id: workspaceId },
          query: { monitor_id: monitorId },
        },
      }),
    );
    return response.cases;
  });
}
