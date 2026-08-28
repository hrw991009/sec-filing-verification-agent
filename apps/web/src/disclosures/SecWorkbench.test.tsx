import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { KnowledgeBase } from "../knowledge/knowledge-api";
import type {
  SecFiling,
  SecFilingDiff,
  SecFilingImport,
  SecFilingSearch,
  SecFilingSection,
  SecXbrlFactCollection,
} from "./sec-api";

const workspaceId = "11111111-1111-4111-8111-111111111111";
const knowledgeBaseId = "22222222-2222-4222-8222-222222222222";
const accession = "0000320193-23-000106";
const comparisonAccession = "0000320193-22-000108";
const versionId = "33333333-3333-4333-8333-333333333333";
const chunkId = "44444444-4444-4444-8444-444444444444";

const mocks = vi.hoisted(() => ({
  diffSecFilings: vi.fn(),
  getSecXbrlFacts: vi.fn(),
  importSecFiling: vi.fn(),
  listKnowledgeBases: vi.fn(),
  listSecFilingImports: vi.fn(),
  listSecFilings: vi.fn(),
  readSecFilingSection: vi.fn(),
  searchSecFiling: vi.fn(),
  syncSecXbrl: vi.fn(),
}));

vi.mock("../knowledge/knowledge-api", () => ({
  listKnowledgeBases: mocks.listKnowledgeBases,
}));

vi.mock("./sec-api", () => ({
  diffSecFilings: mocks.diffSecFilings,
  getSecXbrlFacts: mocks.getSecXbrlFacts,
  importSecFiling: mocks.importSecFiling,
  listSecFilingImports: mocks.listSecFilingImports,
  listSecFilings: mocks.listSecFilings,
  readSecFilingSection: mocks.readSecFilingSection,
  searchSecFiling: mocks.searchSecFiling,
  syncSecXbrl: mocks.syncSecXbrl,
}));

import { SecWorkbench } from "./SecWorkbench";

const knowledgeBase: KnowledgeBase = {
  created_at: "2026-08-26T00:00:00Z",
  description: "SEC filings",
  document_count: 0,
  id: knowledgeBaseId,
  name: "SEC Research",
  revision: 1,
  updated_at: "2026-08-26T00:00:00Z",
  workspace_id: workspaceId,
};

const filing: SecFiling = {
  accession,
  accepted_at: "2023-11-03T06:01:00Z",
  amendment_relation_status: "not_amendment",
  base_accession: null,
  cik: "0000320193",
  content_sha256: "a".repeat(64),
  filed_date: "2023-11-03",
  form: "10-K",
  primary_document: "aapl-20230930.htm",
  public_available_at: "2023-11-03T06:01:00Z",
  report_date: "2023-09-30",
  source_available_at: "2023-11-03T06:01:00Z",
  source_url: "https://data.sec.gov/submissions/CIK0000320193.json",
  source_version: "sec-submissions-v1",
};

const imported: SecFilingImport = {
  accession,
  complete_submission_snapshot_id: "55555555-5555-4555-8555-555555555555",
  created_at: "2026-08-26T00:00:00Z",
  document_id: "66666666-6666-4666-8666-666666666666",
  document_version_id: versionId,
  error_code: null,
  file_id: "77777777-7777-4777-8777-777777777777",
  filing_id: "88888888-8888-4888-8888-888888888888",
  id: "99999999-9999-4999-8999-999999999999",
  ingestion_job_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  knowledge_base_id: knowledgeBaseId,
  primary_snapshot_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  status: "ready",
  updated_at: "2026-08-26T00:01:00Z",
  workspace_id: workspaceId,
};

const searchResult: SecFilingSearch = {
  accession,
  error_code: null,
  hits: [
    {
      accession,
      chunk_id: chunkId,
      content_sha256: "c".repeat(64),
      dense_rank: 1,
      document_version_id: versionId,
      excerpt: "Net sales increased during the fiscal year.",
      index_version: "knowledge-index-v1",
      lexical_rank: null,
      page_number: 1,
      rerank_score: null,
      retrieval_channels: ["dense"],
      rrf_score: null,
      score: 0.91,
      section: "Net Sales",
      snapshot_id: imported.primary_snapshot_id,
      source_content_sha256: "d".repeat(64),
      source_url:
        "https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/aapl-20230930.htm",
      source_version: "sec-filing-content-v1:etag",
      title: "10-K 0000320193-23-000106",
    },
  ],
  retrieval_profile_version: "dense-v1",
  retrieval_trace: null,
  status: "ok",
};

const section: SecFilingSection = {
  accession,
  chunk_id: chunkId,
  content_sha256: "c".repeat(64),
  document_version_id: versionId,
  import_id: imported.id,
  page_number: 1,
  section: "Net Sales",
  snapshot_id: imported.primary_snapshot_id,
  source_content_sha256: "d".repeat(64),
  source_url: searchResult.hits[0]?.source_url ?? "",
  source_version: "sec-filing-content-v1:etag",
  text: "Net sales increased by eight percent during the fiscal year.",
  title: "10-K 0000320193-23-000106",
};

const xbrlResult: SecXbrlFactCollection = {
  accession,
  error_code: null,
  facts: [
    {
      accession,
      cik: "0000320193",
      concept: "Revenue",
      content_sha256: "1".repeat(64),
      context_id: null,
      decimals: null,
      dimensions: {},
      filed_date: "2023-11-03",
      filing_id: filing.content_sha256.slice(0, 8) + "-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      form: "10-K",
      format: null,
      id: "10101010-1010-4010-8010-101010101010",
      is_custom: false,
      locator: {
        accession,
        concept: "Revenue",
        endpoint_snapshot_id: "20202020-2020-4020-8020-202020202020",
        ordinal: 0,
        period: {
          end_date: "2023-09-30",
          instant: null,
          kind: "duration",
          start_date: "2022-09-25",
        },
        source_kind: "companyfacts_aggregate",
        taxonomy: "us-gaap",
        unit: "USD",
      },
      period: {
        end_date: "2023-09-30",
        instant: null,
        kind: "duration",
        start_date: "2022-09-25",
      },
      retrieved_at: "2026-08-26T04:00:00Z",
      scale: null,
      source_available_at: "2023-11-03T06:01:00Z",
      source_content_sha256: "e".repeat(64),
      source_id: "20202020-2020-4020-8020-202020202020",
      source_kind: "companyfacts_aggregate",
      source_snapshot_id: null,
      source_url: "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
      source_version: "sec-xbrl-companyfacts-v1",
      taxonomy: "us-gaap",
      unavailable_fields: ["context_id", "decimals", "dimensions", "scale"],
      unit: "USD",
      value: "100",
    },
    {
      accession,
      cik: "0000320193",
      concept: "CustomerContractAsset",
      content_sha256: "2".repeat(64),
      context_id: "D2023",
      decimals: "-6",
      dimensions: { "dei:LegalEntityAxis": "aapl:AppleIncMember" },
      filed_date: "2023-11-03",
      filing_id: "30303030-3030-4030-8030-303030303030",
      form: "10-K",
      format: null,
      id: "40404040-4040-4040-8040-404040404040",
      is_custom: true,
      locator: {
        accession,
        concept: "CustomerContractAsset",
        context_id: "D2023",
        filing_snapshot_id: "50505050-5050-4050-8050-505050505050",
        ordinal: 1,
        source_kind: "raw_instance",
        taxonomy: "aapl",
      },
      period: {
        end_date: "2023-09-30",
        instant: null,
        kind: "duration",
        start_date: "2022-09-25",
      },
      retrieved_at: "2026-08-26T04:00:00Z",
      scale: null,
      source_available_at: "2023-11-03T06:01:00Z",
      source_content_sha256: "f".repeat(64),
      source_id: "60606060-6060-4060-8060-606060606060",
      source_kind: "raw_instance",
      source_snapshot_id: "50505050-5050-4050-8050-505050505050",
      source_url: "https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/aapl.xml",
      source_version: "sec-xbrl-raw-instance-v1",
      taxonomy: "aapl",
      unavailable_fields: [],
      unit: "iso4217:USD",
      value: "25",
    },
  ],
  status: "ok",
};
const revenueFact = xbrlResult.facts[0];
const netSalesHit = searchResult.hits[0];
if (revenueFact === undefined || netSalesHit === undefined) {
  throw new Error("SEC Workbench fixtures are incomplete");
}

const diffResult: SecFilingDiff = {
  baseline: {
    accession: comparisonAccession,
    amendment_relation_status: "not_amendment",
    base_accession: null,
    cik: "0000320193",
    filed_date: "2022-10-28",
    form: "10-K",
    import_id: "12121212-1212-4212-8212-121212121212",
    knowledge_base_id: knowledgeBaseId,
    public_available_at: "2022-10-28T06:01:00Z",
    report_date: "2022-10-01",
  },
  baseline_retrieval_trace: null,
  comparison_accession: comparisonAccession,
  error_code: null,
  fact_changes: [
    {
      baseline: {
        ...revenueFact,
        accession: comparisonAccession,
        value: "90",
      },
      change_kind: "changed",
      concept: "Revenue",
      dimensions: {},
      is_custom: false,
      period_bucket: "annual",
      period_kind: "duration",
      target: revenueFact,
      taxonomy: "us-gaap",
      unit: "USD",
    },
  ],
  relationship: "adjacent_period",
  requested_accession: accession,
  section_change: {
    baseline: {
      ...netSalesHit,
      accession: comparisonAccession,
      chunk_id: "13131313-1313-4313-8313-131313131313",
      content_sha256: "3".repeat(64),
      excerpt: "Prior-year net sales disclosure.",
    },
    change_kind: "changed",
    section: "Net Sales",
    target: netSalesHit,
  },
  status: "ok",
  target: {
    accession,
    amendment_relation_status: "not_amendment",
    base_accession: null,
    cik: "0000320193",
    filed_date: "2023-11-03",
    form: "10-K",
    import_id: imported.id,
    knowledge_base_id: knowledgeBaseId,
    public_available_at: "2023-11-03T06:01:00Z",
    report_date: "2023-09-30",
  },
  target_retrieval_trace: null,
  unchanged_fact_count: 4,
  version: "sec-filing-diff-v1",
};

describe("SecWorkbench", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.listKnowledgeBases.mockResolvedValue([knowledgeBase]);
    mocks.listSecFilingImports.mockResolvedValueOnce([]).mockResolvedValue([imported]);
    mocks.listSecFilings.mockResolvedValue([filing]);
    mocks.importSecFiling.mockResolvedValue({ ...imported, status: "queued" });
    mocks.searchSecFiling.mockResolvedValue(searchResult);
    mocks.readSecFilingSection.mockResolvedValue(section);
    mocks.syncSecXbrl.mockResolvedValue({
      accession,
      context_count: 1,
      fact_count: 2,
      source_count: 2,
      source_versions: ["sec-xbrl-companyfacts-v1", "sec-xbrl-raw-instance-v1"],
    });
    mocks.getSecXbrlFacts.mockResolvedValue(xbrlResult);
    mocks.diffSecFilings.mockResolvedValue(diffResult);
  });

  it("runs the CIK to locked snapshot to chunk reading journey", async () => {
    const user = userEvent.setup();
    render(<SecWorkbench canManage workspaceId={workspaceId} />);

    await screen.findByRole("option", { name: "SEC Research" });
    await user.click(screen.getByRole("button", { name: "查询申报" }));
    expect(await screen.findAllByText(accession)).toHaveLength(2);

    await user.click(screen.getByRole("button", { name: "锁定并导入" }));
    await waitFor(() => {
      expect(mocks.importSecFiling).toHaveBeenCalledWith(
        workspaceId,
        accession,
        knowledgeBaseId,
        expect.any(String),
      );
    });
    expect(await screen.findByText("可检索")).toBeInTheDocument();

    await user.type(screen.getByLabelText("申报内容检索"), "net sales");
    await user.click(screen.getByRole("button", { name: "Hybrid 检索" }));
    await user.click(await screen.findByRole("button", { name: /Net Sales/ }));

    expect(
      await screen.findByText("Net sales increased by eight percent during the fiscal year."),
    ).toBeInTheDocument();
    expect(mocks.readSecFilingSection).toHaveBeenCalledWith(
      workspaceId,
      accession,
      knowledgeBaseId,
      expect.any(String),
      versionId,
      chunkId,
    );
  });

  it("shows standard aggregate and raw custom facts with distinct locators", async () => {
    const user = userEvent.setup();
    render(<SecWorkbench canManage workspaceId={workspaceId} />);

    await screen.findByRole("option", { name: "SEC Research" });
    await user.click(screen.getByRole("button", { name: "查询申报" }));
    await user.click(screen.getByRole("button", { name: "锁定并导入" }));
    await screen.findByText("可检索");
    await user.click(screen.getByRole("tab", { name: "XBRL" }));
    await user.click(screen.getByRole("button", { name: "同步 XBRL" }));

    expect(await screen.findAllByText("us-gaap:Revenue")).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: /aapl:CustomerContractAsset/ }));
    expect(screen.getByText("D2023")).toBeInTheDocument();
    expect(screen.getByText("dei:LegalEntityAxis")).toBeInTheDocument();
    expect(screen.getByText("aapl:AppleIncMember")).toBeInTheDocument();
    expect(mocks.syncSecXbrl).toHaveBeenCalledWith(workspaceId, accession, knowledgeBaseId);
    expect(mocks.getSecXbrlFacts).toHaveBeenCalledWith(
      workspaceId,
      accession,
      knowledgeBaseId,
      expect.any(String),
      {
        concept: null,
        sourceKinds: ["companyfacts_aggregate", "raw_inline", "raw_instance"],
        taxonomy: null,
      },
    );
  });

  it("runs a formal filing diff and exposes both source locators", async () => {
    const user = userEvent.setup();
    render(<SecWorkbench canManage workspaceId={workspaceId} />);

    await screen.findByRole("option", { name: "SEC Research" });
    await user.click(screen.getByRole("button", { name: "查询申报" }));
    await user.click(screen.getByRole("button", { name: "锁定并导入" }));
    await screen.findByText("可检索");
    await user.click(screen.getByRole("tab", { name: "Filing Diff" }));
    await user.type(screen.getByLabelText("对比 Accession"), comparisonAccession);
    await user.clear(screen.getByLabelText("Unit"));
    await user.type(screen.getByLabelText("Unit"), "EUR");
    await user.clear(screen.getByLabelText("Scale"));
    await user.type(screen.getByLabelText("Scale"), "6");
    await user.click(screen.getByRole("button", { name: "运行正式 Diff" }));

    expect(await screen.findByText("adjacent_period")).toBeInTheDocument();
    expect(screen.getByText("Prior-year net sales disclosure.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Baseline citation" })).toHaveAttribute(
      "href",
      xbrlResult.facts[0]?.source_url,
    );
    expect(mocks.diffSecFilings).toHaveBeenCalledWith(
      workspaceId,
      accession,
      knowledgeBaseId,
      expect.objectContaining({
        cik: "0000320193",
        form: "10-K",
        reportPeriod: "2023-09-30",
        scale: 6,
        unit: "EUR",
      }),
      comparisonAccession,
      "risk factors",
    );
  });
});
