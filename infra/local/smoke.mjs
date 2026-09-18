// Real browser journey against the Compose deployment. No HTTP interception or DB writes.
import { chromium, expect } from "@playwright/test";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { randomBytes } from "node:crypto";
import { fileURLToPath } from "node:url";

const root = new URL("../../", import.meta.url);
const state = new URL(".data/local/browser.json", root);
const shots = new URL("docs/assets/screenshots/", root);
const phase = process.argv[2] ?? "setup";
if (!["setup", "sec", "research", "chat", "capture", "restore"].includes(phase)) {
  throw new Error("Phase must be setup, sec, research, chat, capture or restore");
}
const asOf = new Date().toISOString().slice(0, 16);
await mkdir(shots, { recursive: true });
const browser = await chromium.launch();
const context = await browser.newContext({
  baseURL: "https://localhost:8443",
  ignoreHTTPSErrors: true,
  viewport: { width: 1600, height: 1080 },
  locale: "zh-CN",
  timezoneId: "UTC",
  ...(phase === "setup" ? {} : { storageState: JSON.parse(await readFile(state, "utf8")) }),
});
const page = await context.newPage();
page.setDefaultTimeout(30_000);
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
page.on("response", async (response) => {
  if (response.status() >= 400)
    console.log("HTTP", response.status(), new URL(response.url()).pathname);
  if (
    new URL(response.url()).pathname.endsWith("/dupont/prepare") ||
    new URL(response.url()).pathname.endsWith("/disclosures/filings")
  )
    console.log("SEC result", await response.json());
  if (response.status() === 403 && new URL(response.url()).pathname === "/sec-filing-private/")
    console.log(await response.text());
});
async function screenshot(name, locator = page) {
  await locator.screenshot({
    path: fileURLToPath(new URL(name + ".png", shots)),
    animations: "disabled",
  });
}
try {
  await page.goto("/");
  if (phase !== "setup") {
    if (phase === "restore") {
      await expect(page.getByRole("heading", { name: "Agent 工作台" })).toBeVisible({
        timeout: 30_000,
      });
    }
    await expect(
      page.getByRole("heading", { name: "Agent 工作台" }).or(page.getByLabel("邮箱")),
    ).toBeVisible({ timeout: 30_000 });
    if (await page.getByLabel("邮箱").isVisible()) {
      const account = JSON.parse(
        await readFile(new URL(".data/local/demo-account.json", root), "utf8"),
      );
      await page.getByLabel("邮箱").fill(account.email);
      await page.getByLabel("密码").fill(account.password);
      await page.getByRole("button", { name: "登录 Workspace" }).click();
      await expect(page.getByRole("heading", { name: "Agent 工作台" })).toBeVisible();
    }
  }
  if (phase === "setup") {
    await page.getByRole("button", { exact: true, name: "创建账户" }).click();
    const email = `local-demo-${Date.now()}@example.com`;
    const password = randomBytes(18).toString("base64url") + "!Aa1";
    await writeFile(
      new URL(".data/local/demo-account.json", root),
      JSON.stringify({ email, password }),
      { mode: 0o600 },
    );
    await page.getByLabel("邮箱").fill(email);
    await page.getByLabel("密码").fill(password);
    await page.getByRole("button", { name: "创建账户并进入" }).click();
    await expect(page.getByRole("heading", { name: "Agent 工作台" })).toBeVisible();
    await context.storageState({ path: fileURLToPath(state) });
    await page.getByRole("button", { name: "知识库", exact: true }).click();
    await page.getByRole("button", { name: "新建知识库" }).click();
    await page.getByLabel("名称", { exact: true }).fill("Apple · 财务研究档案");
    await page.getByLabel("描述").fill("SEC 官方年报、XBRL 与可重算财务证据");
    await page.getByRole("button", { exact: true, name: "保存" }).click();
    await expect(
      page.getByRole("heading", { name: "Apple · 财务研究档案", level: 2 }),
    ).toBeVisible();
    await page.getByRole("button", { name: "上传文档" }).click();
    await page.getByLabel("文件", { exact: true }).setInputFiles({
      name: "research-notes.txt",
      mimeType: "text/plain",
      buffer: Buffer.from(
        "研究方法：以 SEC 10-K 与 XBRL 为来源，核对期间、单位、平均资产及平均权益。所有派生指标保留计算证据。本文件是研究笔记，不是财务数据。",
      ),
    });
    await page.getByLabel("文档标题").fill("财务核验方法说明");
    await page.getByRole("button", { name: "上传", exact: true }).click();
    await expect(page.getByText("财务核验方法说明", { exact: true })).toBeVisible();
    await expect(async () => {
      await page.getByRole("button", { name: "刷新文档状态" }).click();
      await expect(page.getByText("可用", { exact: true })).toBeVisible();
    }).toPass({ timeout: 120_000, intervals: [2000] });
    console.log("PASS registration, secure session, signed upload, worker ingestion");
  }
  if (phase === "setup" || phase === "sec") {
    await page.getByRole("button", { exact: true, name: "知识库" }).click();
    await expect(page.getByText("可用", { exact: true }).first()).toBeVisible();
    await page.getByRole("button", { exact: true, name: "SEC" }).click();
    await page.getByLabel("CIK", { exact: true }).fill("0000320193");
    await page.getByRole("checkbox", { name: "10-Q", exact: true }).uncheck();
    await page.getByLabel("报告期开始").fill("2024-01-01");
    await page.getByLabel("报告期结束").fill("2024-12-31");
    await page.getByLabel("截止时间").fill(asOf);
    await page.getByRole("button", { name: "查询申报" }).click();
    await expect(page.getByText("0000320193-24-000123").first()).toBeVisible({ timeout: 120_000 });
    await page.getByText("0000320193-24-000123", { exact: true }).first().click();
    const importButton = page.getByRole("button", { name: "锁定并导入" });
    if (await importButton.isEnabled()) await importButton.click();
    await expect(page.getByText("可检索", { exact: true })).toBeVisible({ timeout: 180_000 });
    await page
      .getByLabel("申报内容检索")
      .fill("net sales net income total assets shareholders equity");
    await page.getByRole("button", { name: "Hybrid 检索" }).click();
    await expect(page.locator(".sec-hit-row").first()).toBeVisible({ timeout: 60_000 });
    await page.locator(".sec-hit-row").first().click();
    await expect(page.locator(".sec-section-reader pre")).toBeVisible();
    await page.evaluate(() => globalThis.scrollTo(0, 0));
    await screenshot("sec-workspace");
    console.log("PASS SEC search, immutable import, indexing");
  }
  if (phase === "chat") {
    const note =
      "杜邦分析将 ROE 分解为净利率、总资产周转率与权益乘数。这里是部署测试笔记，不含投资建议。";
    await page.getByLabel("选择附件").setInputFiles({
      name: "dupont-notes.txt",
      mimeType: "text/plain",
      buffer: Buffer.from(note),
    });
    await expect(page.getByLabel("正在上传")).toHaveCount(0, { timeout: 30_000 });
    await page
      .getByLabel("输入问题")
      .fill("请用三句话简要解释杜邦分析的三个因子，不需要查询真实财务数字。");
    const stream = page.waitForResponse(
      (r) => r.headers()["content-type"]?.includes("text/event-stream"),
      { timeout: 60_000 },
    );
    await page.getByRole("button", { name: "发送问题" }).click();
    expect((await stream).ok()).toBe(true);
    await expect(page.getByRole("button", { exact: true, name: "停止" })).toBeVisible();
    await expect(page.getByRole("button", { exact: true, name: "停止" })).toHaveCount(0, {
      timeout: 180_000,
    });
    const ticket = page.waitForResponse((r) => r.url().endsWith("/download-url") && r.ok());
    await page.getByRole("button", { name: /dupont-notes.txt/ }).click();
    const { url } = await (await ticket).json();
    expect(new URL(url).origin).toBe("https://localhost:8443");
    const download = await context.request.get(url);
    expect(download.ok()).toBe(true);
    expect(await download.text()).toBe(note);
    expect((await context.request.get(url.split("?")[0])).status()).toBe(403);
    await screenshot("agent-workspace");
    console.log("PASS real model SSE, signed download bytes, anonymous object denied");
  }
  if (phase === "research") {
    await page.getByRole("button", { exact: true, name: "Research" }).click();
    await page.getByRole("button", { exact: true, name: "SEC Filing" }).click();
    await page.getByLabel(/^研究任务/).selectOption("sec.dupont-analysis");
    await page.getByLabel(/^分析年数/).selectOption("3");
    await page
      .getByLabel("Research Knowledge Base")
      .selectOption({ label: "Apple · 财务研究档案" });
    await page.getByLabel("Research report period").fill("2024-09-28");
    await page.getByLabel("Research as of").fill(asOf);
    await page.getByRole("button", { name: "补齐杜邦分析数据", exact: true }).click();
    await expect(page.getByText("所选年度输入已齐备，可以开始分析。")).toBeVisible({
      timeout: 300_000,
    });
    await page
      .getByLabel("Research 已确认范围")
      .fill(
        "Apple 2022—2024 三个财年；仅使用截止 2026-09-18 已公开的 SEC 10-K 与 XBRL。三因素杜邦分析，不进行估值或投资建议。",
      );
    await page.getByRole("button", { name: "确认 Brief 并开始" }).click();
    const detail = page.getByRole("article", { name: "Research 详情" });
    await expect(
      detail
        .getByText("已完成", { exact: true })
        .first()
        .or(detail.getByText("失败", { exact: true }).first()),
    ).toBeVisible({ timeout: 1_200_000 });
    await expect(detail.getByText("已完成", { exact: true }).first()).toBeVisible();
    console.log("PASS real model research completed");
  }
  if (phase === "research" || phase === "capture" || phase === "restore") {
    await page.getByRole("button", { exact: true, name: "Research" }).click();
    const results = page.getByRole("region", { name: "研究结果展示" });
    await expect(results.getByRole("heading", { name: "杜邦分析结果" })).toBeVisible({
      timeout: 60_000,
    });
    await expect(results.getByRole("img", { name: /跨年度柱状图/u })).toHaveCount(4);
    await screenshot("dupont-results", results);
    await page.evaluate(() => globalThis.scrollTo(0, 0));
    await screenshot("research-workbench");
    await results
      .getByRole("button", { name: /来源 1$/ })
      .first()
      .click();
    await expect(page.getByRole("region", { name: "Evidence 详情" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Evidence 详情" })).toContainText(
      "financial_calculation_v1",
    );
    await screenshot("evidence-detail", page.getByRole("region", { name: "Evidence 详情" }));
    await page.getByRole("button", { exact: true, name: "知识库" }).click();
    await expect(page.getByText("财务核验方法说明", { exact: true })).toBeVisible();
    await screenshot("knowledge-workspace");
    console.log("PASS persisted result cards, charts, decomposition, table and knowledge");
  }
  expect(errors).toEqual([]);
  await context.storageState({ path: fileURLToPath(state) });
} catch (error) {
  console.error(await page.locator("body").innerText());
  await page.screenshot({ path: ".data/local/browser-failure.png", fullPage: true });
  throw error;
} finally {
  await context.storageState({ path: fileURLToPath(state) });
  await browser.close();
}
