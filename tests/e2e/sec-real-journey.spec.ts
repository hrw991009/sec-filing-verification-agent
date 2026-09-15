import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";
import type { components } from "../../packages/api-contract/src/schema";

const BASELINE_ACCESSION = "0000320193-23-000106";
const TARGET_ACCESSION = "0000320193-24-000123";
const liveModel = process.env.SEC_BROWSER_LIVE_MODEL === "true";
const requiredStepLabels = liveModel
  ? ["检索申报证据", "读取 XBRL 事实", "计算派生指标", "请求持续监控审批"]
  : [];

async function assertLiveResearchResult(detail: Locator, page: Page): Promise<void> {
  const reportResponse = page.waitForResponse(
    (response) => response.ok() && response.url().endsWith("/verification-report"),
  );
  await page.reload();
  await page.getByRole("button", { exact: true, name: "Research" }).click();
  const response = await reportResponse;
  const report = (await response.json()) as components["schemas"]["VerificationReportResponse"];
  expect(report.verification_status).toBe("verified");
  expect(report.issues.filter((issue) => issue.severity === "error")).toEqual([]);
  expect(report.claims.flatMap((claim) => claim.calculation_refs).length).toBeGreaterThan(0);
  await expect(detail).toContainText(/383[,.]?285|3832\.85|383\.285/u);
  await expect(
    detail.getByRole("button", { name: "反查 Calculation Citation" }).first(),
  ).toBeVisible();
  await expect(detail).toContainText("44.13");
  await detail.getByRole("button", { name: "反查 Calculation Citation" }).first().click();
  const evidence = page.getByRole("region", { name: "Evidence 详情" });
  await expect(evidence).toContainText("financial_calculation_v1");
  await expect(evidence.getByRole("heading", { name: "反向 Trace" })).toBeVisible();
  await page.reload();
  await page.getByRole("button", { exact: true, name: "Research" }).click();
  await expect(detail.getByRole("heading", { name: "Verification Report" })).toBeVisible();
}

async function assertControlledResearchResult(detail: Locator): Promise<void> {
  await expect(
    detail.getByText(
      "Apple 2023 财年净销售额为 3832.85 亿美元 [S1]。经人工批准，SEC Monitor 已创建；本结论仅用于受控链路验证。",
      { exact: true },
    ),
  ).toBeVisible();
}

const assertResearchResult = liveModel ? assertLiveResearchResult : assertControlledResearchResult;

async function selectSecScope(
  page: Page,
  scope: {
    readonly asOf: string;
    readonly periodEnd: string;
    readonly periodStart: string;
  },
): Promise<void> {
  await page.getByLabel("CIK").fill("0000320193");
  await page.getByLabel("报告期开始").fill(scope.periodStart);
  await page.getByLabel("报告期结束").fill(scope.periodEnd);
  await page.getByLabel("截止时间").fill(scope.asOf);
  await page.getByRole("button", { name: "查询申报" }).click();
}

async function importSelectedFiling(page: Page): Promise<void> {
  await page.getByRole("button", { name: "锁定并导入" }).click();
  await expect(page.getByText("可检索", { exact: true })).toBeVisible({ timeout: 90_000 });
}

async function attachJourneyEvidence(
  testInfo: TestInfo,
  apiRequests: readonly string[],
): Promise<void> {
  await testInfo.attach("formal-api-requests.json", {
    body: Buffer.from(JSON.stringify({ api_requests: apiRequests }, null, 2)),
    contentType: "application/json",
  });
}

test("completes the Chinese SEC filing to approved monitor and verified case journey", async ({
  page,
}) => {
  test.setTimeout(liveModel ? 600_000 : 240_000);
  const uniquePart = `${String(Date.now())}-${String(test.info().workerIndex)}`;
  const browserErrors: string[] = [];
  const failedRequests: string[] = [];
  const unexpectedHttpErrors: string[] = [];
  const apiRequests: string[] = [];
  const executionEvidence: unknown[] = [];
  page.on("console", (message) => {
    if (message.type() === "error" && !message.text().startsWith("Failed to load resource:")) {
      browserErrors.push(message.text());
    }
  });
  page.on("pageerror", (error) => {
    browserErrors.push(error.message);
  });
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith("/api/v1/")) {
      apiRequests.push(`${request.method()} ${url.pathname}`);
    }
  });
  page.on("requestfailed", (request) => {
    failedRequests.push(`${request.method()} ${request.url()}`);
  });
  page.on("response", (response) => {
    if (response.status() < 400) return;
    const request = response.request();
    const url = new URL(response.url());
    const expectedUnauthenticatedRefresh =
      response.status() === 401 &&
      request.method() === "POST" &&
      url.pathname === "/api/v1/auth/refresh";
    const expectedPendingVerification =
      response.status() === 404 &&
      request.method() === "GET" &&
      /\/research-runs\/[^/]+\/verification-report$/u.test(url.pathname);
    if (!expectedUnauthenticatedRefresh && !expectedPendingVerification) {
      unexpectedHttpErrors.push(`${String(response.status())} ${request.method()} ${url.pathname}`);
    }
  });
  page.on("response", async (response) => {
    const pathname = new URL(response.url()).pathname;
    if (
      response.ok() &&
      (/\/research-runs(?:\/[^/]+(?:\/(?:verification-report|durability|claims))?)?$/u.test(
        pathname,
      ) ||
        /\/agent-runs\/[^/]+\/trace$/u.test(pathname) ||
        /\/disclosures\/(?:cases|monitors)$/u.test(pathname))
    ) {
      const body: unknown = await response.json().catch(() => null);
      executionEvidence.push({ path: pathname, body });
    }
  });

  await page.goto("/");
  await page.getByRole("button", { exact: true, name: "创建账户" }).click();
  await page.getByLabel("邮箱").fill(`sec-real-${uniquePart}@example.com`);
  await page.getByLabel("密码").fill("Browser!Pass123");
  await page.getByRole("button", { name: "创建账户并进入" }).click();
  await expect(page.getByRole("heading", { name: "Agent 工作台" })).toBeVisible();

  const knowledgeBaseName = `SEC 受控审查 ${uniquePart}`;
  await page.getByRole("button", { name: "知识库" }).click();
  await page.getByRole("button", { name: "新建知识库" }).click();
  await page.getByLabel("名称").fill(knowledgeBaseName);
  await page.getByLabel("描述").fill("真实进程浏览器闭环的 SEC filing Evidence 存储");
  await page.getByRole("button", { exact: true, name: "保存" }).click();
  await expect(page.getByRole("heading", { level: 2, name: knowledgeBaseName })).toBeVisible();

  await page.getByRole("button", { exact: true, name: "SEC" }).click();
  await selectSecScope(page, {
    asOf: "2023-12-01T00:00",
    periodEnd: "2023-12-31",
    periodStart: "2023-01-01",
  });
  await expect(page.getByText(BASELINE_ACCESSION).first()).toBeVisible();
  await importSelectedFiling(page);

  await page.getByRole("tab", { name: "正式核验" }).click();
  await page
    .getByLabel("中文核验问题")
    .fill(
      liveModel
        ? "请核验 Apple 2023 财年净销售额和毛利，使用 finance.calculate 计算毛利率（毛利除以净销售额乘100），保留证据引用。并请求人工审批后持续监控新的 10-K，规则为 new_filing，section_query 为 Item 8，每天北京时间03:00检查。"
        : "请核验 Apple 2023 财年净销售额，并在人工审批后持续监控新的 10-K。 ",
    );
  await page.getByRole("button", { name: "进入正式核验" }).click();
  await expect(page.getByRole("heading", { name: "Research Workbench" })).toBeVisible();
  await expect(page.getByLabel("Research accession")).toHaveValue(BASELINE_ACCESSION);
  await expect(page.getByLabel("Research Knowledge Base")).toHaveValue(/.+/u);
  // This multi-step run reserves both schema framing and accumulated Tool results.
  await page.getByLabel("Research 最大 Token").fill(liveModel ? "100000" : "32768");
  for (const label of requiredStepLabels) {
    await page.getByLabel(label, { exact: true }).check();
  }

  await page.getByRole("button", { name: "确认 Brief 并开始" }).click();
  const researchDetail = page.getByRole("article", { name: "Research 详情" });
  const approval = researchDetail.getByText("SEC Monitor 订阅审批");
  await expect(
    approval
      .or(researchDetail.getByText("失败", { exact: true }))
      .or(researchDetail.getByText("已完成", { exact: true }))
      .first(),
  ).toBeVisible({
    timeout: liveModel ? 360_000 : 90_000,
  });
  await expect(approval).toBeVisible();
  // Reload through the normal session/API path before deciding the persisted approval.
  await page.reload();
  await page.getByRole("button", { exact: true, name: "Research" }).click();
  await expect(researchDetail.getByText("SEC Monitor 订阅审批")).toBeVisible();
  await researchDetail.getByRole("button", { name: "允许并继续" }).click();
  const completedResearch = researchDetail.getByText("已完成", { exact: true }).first();
  await expect(completedResearch.or(researchDetail.getByText("失败", { exact: true }))).toBeVisible(
    {
      timeout: liveModel ? 240_000 : 90_000,
    },
  );
  await expect(completedResearch).toBeVisible();
  await expect(researchDetail.getByRole("heading", { name: "Verification Report" })).toBeVisible();
  await expect(researchDetail.getByText("已核验", { exact: true }).first()).toBeVisible();
  await assertResearchResult(researchDetail, page);
  await expect(researchDetail.getByText("SEC Monitor 订阅审批")).toBeVisible();

  await page.getByRole("button", { exact: true, name: "SEC" }).click();
  await selectSecScope(page, {
    asOf: "2025-01-01T00:00",
    periodEnd: "2024-12-31",
    periodStart: "2023-01-01",
  });
  await page.getByText(TARGET_ACCESSION).first().click();
  await importSelectedFiling(page);

  await page.getByRole("tab", { name: "Monitor" }).click();
  await expect(page.getByText(BASELINE_ACCESSION, { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "立即检查" }).click();
  await expect(page.getByText("new_filing", { exact: true })).toBeVisible({ timeout: 90_000 });
  await expect(page.getByText(`${BASELINE_ACCESSION} → ${TARGET_ACCESSION}`)).toBeVisible();
  await expect(page.getByText("verified", { exact: true })).toBeVisible();
  await page.reload();
  await page.getByRole("button", { exact: true, name: "SEC" }).click();
  await page.getByRole("tab", { name: "Monitor" }).click();
  await expect(page.getByText(`${BASELINE_ACCESSION} → ${TARGET_ACCESSION}`)).toBeVisible();

  await page.screenshot({
    fullPage: true,
    path: test.info().outputPath("sec-real-monitor-desktop.png"),
  });
  await page.setViewportSize({ height: 844, width: 390 });
  await expect(page.getByText(`${BASELINE_ACCESSION} → ${TARGET_ACCESSION}`)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  await page.screenshot({
    fullPage: true,
    path: test.info().outputPath("sec-real-monitor-mobile.png"),
  });
  await attachJourneyEvidence(test.info(), apiRequests);
  await test.info().attach("execution-evidence.json", {
    body: Buffer.from(JSON.stringify(executionEvidence, null, 2)),
    contentType: "application/json",
  });
  expect(apiRequests.some((value) => value.includes("/disclosures/filings"))).toBe(true);
  expect(apiRequests.some((value) => value.includes("/research-runs"))).toBe(true);
  expect(apiRequests.some((value) => /\/disclosures\/monitors\/[^/]+\/runs$/u.test(value))).toBe(
    true,
  );
  expect(browserErrors).toEqual([]);
  expect(failedRequests).toEqual([]);
  expect(unexpectedHttpErrors).toEqual([]);
});
