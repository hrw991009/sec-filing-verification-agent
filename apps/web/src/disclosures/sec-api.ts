import type { components } from "@industry-platform/api-contract";

import { apiClient, unwrapData, withAccessToken } from "../api/api";

export type SecFiling = components["schemas"]["SecFilingCandidateResponse"];
export type SecFilingImport = components["schemas"]["SecWorkspaceFilingImportResponse"];
export type SecFilingSearch = components["schemas"]["SecFilingSearchResponse"];
export type SecFilingSection = components["schemas"]["SecFilingSectionResponse"];
export type SecXbrlFact = components["schemas"]["SecXbrlFactResponse"];
export type SecXbrlFactCollection = components["schemas"]["SecXbrlFactCollectionResponse"];
export type SecXbrlSourceKind = components["schemas"]["SecXbrlSourceKind"];
export type SecXbrlSync = components["schemas"]["SecXbrlSyncResponse"];

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
