import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { KnowledgeBase } from "../knowledge/knowledge-api";
import type { SecReviewDraft } from "./sec-review-navigation";
import type {
  SecFiling,
  SecFilingDiff,
  SecFilingImport,
  SecFilingSearch,
  SecFilingSection,
  SecDisclosureCase,
  SecMonitor,
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
  deleteSecMonitor: vi.fn(),
  getSecXbrlFacts: vi.fn(),
  importSecFiling: vi.fn(),
  listKnowledgeBases: vi.fn(),
  listSecFilingImports: vi.fn(),
  listSecFilings: vi.fn(),
  listSecDisclosureCases: vi.fn(),
  listSecMonitors: vi.fn(),
  changeSecMonitorStatus: vi.fn(),
  readSecFilingSection: vi.fn(),
  searchSecFiling: vi.fn(),
  syncSecXbrl: vi.fn(),
  triggerSecMonitorRun: vi.fn(),
}));

vi.mock("../knowledge/knowledge-api", () => ({
  listKnowledgeBases: mocks.listKnowledgeBases,
}));

vi.mock("./sec-api", () => ({
  deleteSecMonitor: mocks.deleteSecMonitor,
  changeSecMonitorStatus: mocks.changeSecMonitorStatus,
  diffSecFilings: mocks.diffSecFilings,
  getSecXbrlFacts: mocks.getSecXbrlFacts,
  importSecFiling: mocks.importSecFiling,
  listSecFilingImports: mocks.listSecFilingImports,
  listSecFilings: mocks.listSecFilings,
  listSecDisclosureCases: mocks.listSecDisclosureCases,
  listSecMonitors: mocks.listSecMonitors,
  readSecFilingSection: mocks.readSecFilingSection,
  searchSecFiling: mocks.searchSecFiling,
  syncSecXbrl: mocks.syncSecXbrl,
  triggerSecMonitorRun: mocks.triggerSecMonitorRun,
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

const monitor: SecMonitor = {
  allowed_forms: ["10-K", "10-K/A"],
  canonical_name: "Apple Inc.",
  cik: "0000320193",
  created_at: "2026-08-29T01:00:00Z",
  created_from_approval_id: "14141414-1414-4414-8414-141414141414",
  cron_expression: "0 3 * * *",
  knowledge_base_id: knowledgeBaseId,
  monitor_id: "15151515-1515-4515-8515-151515151515",
  owner_user_id: "16161616-1616-4616-8616-161616161616",
  revision: 1,
  rules: [
    {
      comparator: null,
      concept: null,
      kind: "new_filing",
      rule_id: "17171717-1717-4717-8717-171717171717",
      rule_version: "sec-monitor-rules-v1",
      section_query: "management discussion and analysis",
      taxonomy: null,
      threshold: null,
      unit: null,
    },
  ],
  schedule_id: "18181818-1818-4818-8818-181818181818",
  status: "active",
  timezone_name: "Asia/Shanghai",
  updated_at: "2026-08-29T01:00:00Z",
  watermark_accepted_at: null,
  watermark_accession: null,
  watermark_coverage_version: "sec-monitor-initial-v1",
  watermark_revision: 1,
  workspace_id: workspaceId,
};

const disclosureCase: SecDisclosureCase = {
  baseline_accession: comparisonAccession,
  case_id: "19191919-1919-4919-8919-191919191919",
  created_at: "2026-08-29T02:00:00Z",
  diff_payload: { relationship: "adjacent_period" },
  diff_sha256: "a".repeat(64),
  diff_version: "sec-filing-diff-v1",
  evidence: [
    { evidence_id: "20202020-2020-4020-8020-202020202020", side: "baseline" },
    { evidence_id: "21212121-2121-4121-8121-212121212121", side: "target" },
  ],
  monitor_id: monitor.monitor_id,
  monitor_run_id: "22222222-2222-4222-8222-222222222220",
  notification_status: "pending",
  rule_id: monitor.rules[0]?.rule_id ?? "",
  source_coverage_version: "sec-filings-v2",
  target_accession: accession,
  trigger_kind: "new_filing",
  updated_at: "2026-08-29T02:00:00Z",
  verification_status: "verified",
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
    mocks.listSecMonitors.mockResolvedValue([monitor]);
    mocks.listSecDisclosureCases.mockResolvedValue([disclosureCase]);
    mocks.changeSecMonitorStatus.mockResolvedValue({ ...monitor, revision: 2, status: "paused" });
    mocks.triggerSecMonitorRun.mockResolvedValue({
      created: true,
      job_id: "33333333-3333-4333-8333-333333333334",
      occurrence_id: "33333333-3333-4333-8333-333333333335",
    });
  });

  it("rebuilds Monitor and Case state from formal APIs and pauses with its revision", async () => {
    const user = userEvent.setup();
    const onOpenEvidence = vi.fn();
    render(
      <SecWorkbench
        canManage
        onOpenEvidence={onOpenEvidence}
        onOpenResearch={vi.fn()}
        workspaceId={workspaceId}
      />,
    );

    await user.click(await screen.findByRole("tab", { name: "Monitor / Case" }));
    expect(await screen.findByRole("heading", { name: "Apple Inc." })).toBeInTheDocument();
    expect(screen.getByText(`${comparisonAccession} → ${accession}`)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "立即检查" }));
    await waitFor(() => {
      expect(mocks.triggerSecMonitorRun).toHaveBeenCalledWith(
        workspaceId,
        monitor.monitor_id,
        monitor.revision,
      );
    });

    await user.click(screen.getByRole("button", { name: "baseline:20202020" }));
    expect(onOpenEvidence).toHaveBeenCalledWith(disclosureCase.evidence[0]?.evidence_id);

    await user.click(screen.getByRole("button", { name: "暂停" }));
    await waitFor(() => {
      expect(mocks.changeSecMonitorStatus).toHaveBeenCalledWith(
        workspaceId,
        monitor.monitor_id,
        1,
        "paused",
      );
    });
  });

  it("runs the CIK to locked snapshot to chunk reading journey", async () => {
    const user = userEvent.setup();
    render(
      <SecWorkbench
        canManage
        onOpenEvidence={vi.fn()}
        onOpenResearch={vi.fn()}
        workspaceId={workspaceId}
      />,
    );

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

  it("hands a locked FinancialScope and Chinese question to the formal Research route", async () => {
    const user = userEvent.setup();
    const onOpenResearch = vi.fn<(draft: SecReviewDraft) => void>();
    render(
      <SecWorkbench
        canManage
        onOpenEvidence={vi.fn()}
        onOpenResearch={onOpenResearch}
        workspaceId={workspaceId}
      />,
    );

    await screen.findByRole("option", { name: "SEC Research" });
    await user.click(screen.getByRole("button", { name: "查询申报" }));
    await user.click(screen.getByRole("button", { name: "锁定并导入" }));
    await screen.findByText("可检索");
    await user.click(screen.getByRole("tab", { name: "正式核验" }));
    await user.clear(screen.getByLabelText("中文核验问题"));
    await user.type(screen.getByLabelText("中文核验问题"), "请核验营业收入及同比变化。");
    await user.click(screen.getByRole("button", { name: "进入正式核验" }));

    expect(onOpenResearch).toHaveBeenCalledTimes(1);
    const draft = onOpenResearch.mock.calls[0]?.[0];
    expect(draft).toMatchObject({
      accession,
      cik: filing.cik,
      form: filing.form,
      knowledgeBaseId,
      question: "请核验营业收入及同比变化。",
      reportPeriod: filing.report_date,
      scale: 6,
      unit: "USD",
    });
    expect(typeof draft?.asOf).toBe("string");
  });

  it("does not silently coerce an amendment into a base-form FinancialScope", async () => {
    const user = userEvent.setup();
    mocks.listSecFilings.mockResolvedValue([{ ...filing, form: "10-K/A" }]);
    render(
      <SecWorkbench
        canManage
        onOpenEvidence={vi.fn()}
        onOpenResearch={vi.fn()}
        workspaceId={workspaceId}
      />,
    );

    await screen.findByRole("option", { name: "SEC Research" });
    await user.click(screen.getByRole("button", { name: "查询申报" }));
    await user.click(screen.getByRole("button", { name: "锁定并导入" }));
    await screen.findByText("可检索");
    await user.click(screen.getByRole("tab", { name: "正式核验" }));

    expect(screen.getByRole("button", { name: "进入正式核验" })).toBeDisabled();
  });

  it("shows standard aggregate and raw custom facts with distinct locators", async () => {
    const user = userEvent.setup();
    render(
      <SecWorkbench
        canManage
        onOpenEvidence={vi.fn()}
        onOpenResearch={vi.fn()}
        workspaceId={workspaceId}
      />,
    );

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
    render(
      <SecWorkbench
        canManage
        onOpenEvidence={vi.fn()}
        onOpenResearch={vi.fn()}
        workspaceId={workspaceId}
      />,
    );

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
