import { expect, test, type Page, type Route } from "@playwright/test";

const knowledgeBaseId = "22222222-2222-4222-8222-222222222222";
const accession = "0000320193-23-000106";
const versionId = "33333333-3333-4333-8333-333333333333";
const chunkId = "44444444-4444-4444-8444-444444444444";

function response(route: Route, body: object, status = 200): Promise<void> {
  return route.fulfill({
    body: JSON.stringify(body),
    contentType: "application/json",
    status,
  });
}

async function installSecReplay(page: Page): Promise<void> {
  const imported = {
    accession,
    complete_submission_snapshot_id: "55555555-5555-4555-8555-555555555555",
    created_at: "2026-08-26T08:00:00Z",
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
    updated_at: "2026-08-26T08:01:00Z",
    workspace_id: "11111111-1111-4111-8111-111111111111",
  };
  let importedReady = false;

  await page.route("**/api/v1/workspaces/*/knowledge-bases?*", (route) =>
    response(route, {
      knowledge_bases: [
        {
          created_at: "2026-08-26T08:00:00Z",
          description: "Locked SEC filings",
          document_count: 1,
          id: knowledgeBaseId,
          name: "SEC Research",
          revision: 1,
          updated_at: "2026-08-26T08:01:00Z",
          workspace_id: imported.workspace_id,
        },
      ],
    }),
  );
  await page.route("**/api/v1/workspaces/*/disclosures/filing-imports?*", (route) =>
    response(route, { imports: importedReady ? [imported] : [] }),
  );
  await page.route("**/api/v1/workspaces/*/disclosures/filings?*", (route) =>
    response(route, {
      coverage_version: "sec-coverage-replay-v1",
      error_code: null,
      filings: [
        {
          accession,
          accepted_at: "2023-11-03T18:01:00Z",
          amendment_relation_status: "not_amendment",
          base_accession: null,
          cik: "0000320193",
          content_sha256: "a".repeat(64),
          filed_date: "2023-11-03",
          form: "10-K",
          primary_document: "aapl-20230930.htm",
          public_available_at: "2023-11-03T18:01:00Z",
          report_date: "2023-09-30",
          source_available_at: "2023-11-03T18:01:00Z",
          source_url: "https://data.sec.gov/submissions/CIK0000320193.json",
          source_version: "sec-submissions-current-replay-v1",
        },
      ],
      scope: {},
      sources: [],
      status: "ok",
    }),
  );
  await page.route(`**/disclosures/filings/${accession}/imports`, (route) => {
    importedReady = true;
    return response(route, { ...imported, status: "queued" }, 202);
  });
  await page.route(`**/disclosures/filings/${accession}/search?*`, (route) =>
    response(route, {
      accession,
      error_code: null,
      hits: [
        {
          accession,
          chunk_id: chunkId,
          content_sha256: "c".repeat(64),
          document_version_id: versionId,
          excerpt: "Net sales increased during the fiscal year.",
          page_number: 1,
          score: 0.91,
          section: "Net Sales",
          snapshot_id: imported.primary_snapshot_id,
          source_content_sha256: "d".repeat(64),
          source_url:
            "https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/aapl-20230930.htm",
          source_version: "sec-filing-primary-replay-v1",
          title: `10-K ${accession}`,
        },
      ],
      retrieval_profile_version: "dense-v1",
      status: "ok",
    }),
  );
  await page.route(`**/disclosures/filings/${accession}/sections/${chunkId}?*`, (route) =>
    response(route, {
      accession,
      chunk_id: chunkId,
      content_sha256: "c".repeat(64),
      document_version_id: versionId,
      import_id: imported.id,
      page_number: 1,
      section: "Net Sales",
      snapshot_id: imported.primary_snapshot_id,
      source_content_sha256: "d".repeat(64),
      source_url:
        "https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/aapl-20230930.htm",
      source_version: "sec-filing-primary-replay-v1",
      text: "Net sales increased by eight percent during the fiscal year.",
      title: `10-K ${accession}`,
    }),
  );
  await page.route(`**/disclosures/filings/${accession}/xbrl/sync`, (route) =>
    response(route, {
      accession,
      context_count: 1,
      fact_count: 2,
      source_count: 2,
      source_versions: ["sec-xbrl-companyfacts-replay-v1", "sec-xbrl-raw-instance-replay-v1"],
    }),
  );
  await page.route(`**/disclosures/filings/${accession}/xbrl/facts?*`, (route) =>
    response(route, {
      accession,
      error_code: null,
      facts: [
        {
          accession,
          cik: "0000320193",
          concept: "Revenue",
          context_id: null,
          decimals: null,
          dimensions: {},
          filed_date: "2023-11-03",
          filing_id: imported.filing_id,
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
          retrieved_at: "2026-08-26T08:00:00Z",
          scale: null,
          source_available_at: "2023-11-03T18:01:00Z",
          source_content_sha256: "e".repeat(64),
          source_id: "20202020-2020-4020-8020-202020202020",
          source_kind: "companyfacts_aggregate",
          source_snapshot_id: null,
          source_url: "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
          source_version: "sec-xbrl-companyfacts-replay-v1",
          taxonomy: "us-gaap",
          unavailable_fields: ["context_id", "decimals", "dimensions", "scale"],
          unit: "USD",
          value: "100",
        },
        {
          accession,
          cik: "0000320193",
          concept: "CustomerContractAsset",
          context_id: "D2023",
          decimals: "-6",
          dimensions: { "dei:LegalEntityAxis": "aapl:AppleIncMember" },
          filed_date: "2023-11-03",
          filing_id: imported.filing_id,
          form: "10-K",
          format: null,
          id: "30303030-3030-4030-8030-303030303030",
          is_custom: true,
          locator: {
            accession,
            concept: "CustomerContractAsset",
            context_id: "D2023",
            filing_snapshot_id: "40404040-4040-4040-8040-404040404040",
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
          retrieved_at: "2026-08-26T08:00:00Z",
          scale: null,
          source_available_at: "2023-11-03T18:01:00Z",
          source_content_sha256: "f".repeat(64),
          source_id: "50505050-5050-4050-8050-505050505050",
          source_kind: "raw_instance",
          source_snapshot_id: "40404040-4040-4040-8040-404040404040",
          source_url: "https://www.sec.gov/Archives/edgar/data/320193/000032019323000106/aapl.xml",
          source_version: "sec-xbrl-raw-instance-replay-v1",
          taxonomy: "aapl",
          unavailable_fields: [],
          unit: "iso4217:USD",
          value: "25",
        },
      ],
      status: "ok",
    }),
  );
}

test("navigates a locked SEC filing from accession to exact source chunk", async ({ page }) => {
  const uniquePart = [Date.now(), test.info().workerIndex].join("-");
  const browserErrors: string[] = [];
  const failedRequests: string[] = [];
  await installSecReplay(page);
  await page.goto("/");
  await page.getByRole("button", { exact: true, name: "创建账户" }).click();
  await page.getByLabel("邮箱").fill(`sec-workbench-${uniquePart}@example.com`);
  await page.getByLabel("密码").fill("Browser!Pass123");
  await page.getByRole("button", { name: "创建账户并进入" }).click();
  await expect(page.getByRole("heading", { name: "Agent 工作台" })).toBeVisible();
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("pageerror", (error) => {
    browserErrors.push(error.message);
  });
  page.on("requestfailed", (request) => {
    failedRequests.push(`${request.method()} ${request.url()}`);
  });

  await page.getByRole("button", { exact: true, name: "SEC" }).click();
  await expect(page.getByRole("heading", { name: "SEC 申报审查" })).toBeVisible();
  await expect(page.getByLabel("Knowledge Base")).toHaveValue(knowledgeBaseId);
  await page.getByRole("button", { name: "查询申报" }).click();
  await expect(page.getByText(accession).first()).toBeVisible();
  await page.getByRole("button", { name: "锁定并导入" }).click();
  await expect(page.getByText("可检索", { exact: true })).toBeVisible();

  await page.getByLabel("申报内容检索").fill("net sales");
  await page.getByRole("button", { name: "Dense 检索" }).click();
  await page.getByRole("button", { name: /Net Sales/u }).click();
  await expect(
    page.getByText("Net sales increased by eight percent during the fiscal year."),
  ).toBeVisible();
  await expect(page.getByText("Snapshot bbbbbbbb")).toBeVisible();
  await expect(page.getByText("Source dddddddddddd")).toBeVisible();

  await page.getByRole("tab", { name: "XBRL" }).click();
  await page.getByRole("button", { name: "同步 XBRL" }).click();
  await expect(page.getByText("us-gaap:Revenue").first()).toBeVisible();
  await page.getByRole("button", { name: /aapl:CustomerContractAsset/u }).click();
  await expect(page.getByText("D2023", { exact: true })).toBeVisible();
  await expect(page.getByText("dei:LegalEntityAxis")).toBeVisible();
  await expect(page.getByText("aapl:AppleIncMember")).toBeVisible();
  await expect(page.getByText("Locator 40404040")).toBeVisible();

  await page.screenshot({ fullPage: true, path: test.info().outputPath("sec-xbrl-desktop.png") });
  await page.setViewportSize({ height: 844, width: 390 });
  await expect(page.getByRole("heading", { name: "SEC 申报审查" })).toBeVisible();
  await expect(page.getByText("aapl:CustomerContractAsset").first()).toBeVisible();
  await expect(page.getByText("aapl:AppleIncMember")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  await page.screenshot({ fullPage: true, path: test.info().outputPath("sec-xbrl-mobile.png") });
  expect(browserErrors).toEqual([]);
  expect(failedRequests).toEqual([]);
});
