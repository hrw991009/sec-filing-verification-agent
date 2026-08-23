import { execFile } from "node:child_process";
import { promisify } from "node:util";

import { expect, test } from "@playwright/test";

const execFileAsync = promisify(execFile);
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu;
const BROWSER_SUCCESS_ANSWER_PREFIX = "Day 2 浏览器流式片段已到达。Run: ";
const BROWSER_SUCCESS_ANSWER_SUFFIX = "; 第二段完成, 最终回答已持久化。";
const ANSWER_PREFIX_DAY3 = "Day 3 Web Tool 已完成。Run: ";
const ANSWER_SUFFIX_DAY3 = "; 公共来源结果已引用 [S1]。";

interface StartTurnReceipt {
  readonly agentRunId: string;
  readonly conversationId: string;
  readonly jobId: string;
}

interface StartResearchReceipt extends StartTurnReceipt {
  readonly researchRunId: string;
}

function requireRecord(value: unknown, fieldName: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError(`${fieldName} must be an object.`);
  }
  return value as Record<string, unknown>;
}

function requireUuid(value: Record<string, unknown>, fieldName: string): string {
  const field = value[fieldName];
  if (typeof field !== "string" || !UUID_PATTERN.test(field)) {
    throw new TypeError(`${fieldName} must be a UUID.`);
  }
  return field;
}

function parseStartTurnReceipt(value: unknown): StartTurnReceipt {
  const receipt = requireRecord(value, "Start Turn receipt");
  return {
    agentRunId: requireUuid(receipt, "agent_run_id"),
    conversationId: requireUuid(receipt, "conversation_id"),
    jobId: requireUuid(receipt, "job_id"),
  };
}

function parseStartResearchReceipt(value: unknown): StartResearchReceipt {
  const receipt = requireRecord(value, "Start Research receipt");
  return {
    agentRunId: requireUuid(receipt, "agent_run_id"),
    conversationId: requireUuid(receipt, "conversation_id"),
    jobId: requireUuid(receipt, "job_id"),
    researchRunId: requireUuid(receipt, "research_run_id"),
  };
}

async function executeBrowserCreatedRun(receipt: StartTurnReceipt): Promise<void> {
  const uvArguments = [
    "run",
    ...(process.env.CI === "true" ? [] : ["--env-file", ".env"]),
    "--locked",
    "--package",
    "industry-platform-backend",
    "python",
    "apps/backend/tests/day2_browser_success_driver.py",
    "--run-id",
    receipt.agentRunId,
    "--job-id",
    receipt.jobId,
  ];
  const { stdout } = await execFileAsync("uv", uvArguments, {
    cwd: process.cwd(),
    encoding: "utf8",
    timeout: 60_000,
    windowsHide: true,
  });
  const result = requireRecord(
    JSON.parse(stdout.trim()) as unknown,
    "Browser success driver result",
  );
  if (
    result.schema_version !== 1 ||
    result.run_id !== receipt.agentRunId ||
    result.job_id !== receipt.jobId ||
    result.disposition !== "succeeded" ||
    result.provider_calls !== 1 ||
    typeof result.answer_sha256 !== "string" ||
    !/^[0-9a-f]{64}$/u.test(result.answer_sha256)
  ) {
    throw new Error("The browser success driver returned inconsistent terminal facts.");
  }
}

async function executeBrowserCreatedWebRun(receipt: StartTurnReceipt): Promise<void> {
  const uvArguments = [
    "run",
    ...(process.env.CI === "true" ? [] : ["--env-file", ".env"]),
    "--locked",
    "--package",
    "industry-platform-backend",
    "python",
    "apps/backend/tests/day3_browser_web_tool_driver.py",
    "--run-id",
    receipt.agentRunId,
    "--job-id",
    receipt.jobId,
  ];
  const { stdout } = await execFileAsync("uv", uvArguments, {
    cwd: process.cwd(),
    encoding: "utf8",
    timeout: 60_000,
    windowsHide: true,
  });
  const result = requireRecord(JSON.parse(stdout.trim()) as unknown, "Web Tool driver result");
  if (
    result.schema_version !== 1 ||
    result.run_id !== receipt.agentRunId ||
    result.job_id !== receipt.jobId ||
    result.disposition !== "succeeded" ||
    result.provider_calls !== 2 ||
    result.source_snapshot_count !== 1 ||
    typeof result.answer_sha256 !== "string" ||
    !/^[0-9a-f]{64}$/u.test(result.answer_sha256)
  ) {
    throw new Error("The Web Tool driver returned inconsistent terminal facts.");
  }
}

async function executeBrowserCreatedResearchRun(receipt: StartResearchReceipt): Promise<void> {
  const uvArguments = [
    "run",
    ...(process.env.CI === "true" ? [] : ["--env-file", ".env"]),
    "--locked",
    "--package",
    "industry-platform-backend",
    "python",
    "apps/backend/tests/day4_browser_research_driver.py",
    "--run-id",
    receipt.agentRunId,
    "--research-run-id",
    receipt.researchRunId,
    "--job-id",
    receipt.jobId,
  ];
  const { stdout } = await execFileAsync("uv", uvArguments, {
    cwd: process.cwd(),
    encoding: "utf8",
    timeout: 60_000,
    windowsHide: true,
  });
  const result = requireRecord(JSON.parse(stdout.trim()) as unknown, "Research driver result");
  if (
    result.schema_version !== 1 ||
    result.run_id !== receipt.agentRunId ||
    result.research_run_id !== receipt.researchRunId ||
    result.job_id !== receipt.jobId ||
    result.disposition !== "succeeded" ||
    result.provider_calls !== 2 ||
    result.completed_node_count !== 8 ||
    result.draft_status !== "uncertain_draft" ||
    typeof result.draft_sha256 !== "string" ||
    !/^[0-9a-f]{64}$/u.test(result.draft_sha256)
  ) {
    throw new Error("The Research driver returned inconsistent terminal facts.");
  }
}

test.describe("browser identity lifecycle", () => {
  test.describe.configure({ mode: "serial" });

  test("registers, revokes every old session on password change, and logs out", async ({
    browser,
    page,
  }) => {
    const uniquePart = [Date.now(), test.info().workerIndex].join("-");
    const email = `browser-${uniquePart}@example.com`;
    const initialPassword = "Initial!Pass123";
    const replacementPassword = "Replacement!Pass456";

    await page.goto("/");
    await page.getByRole("button", { exact: true, name: "创建账户" }).click();
    await page.getByLabel("邮箱").fill(email);
    await page.getByLabel("密码").fill(initialPassword);
    await page.getByRole("button", { name: "创建账户并进入" }).click();

    await expect(page.getByRole("heading", { name: "Agent 工作台" })).toBeVisible();
    await expect(page.getByText(email)).toBeVisible();

    await page.reload();
    await expect(page.getByRole("heading", { name: "Agent 工作台" })).toBeVisible();

    const otherDevice = await browser.newContext({
      baseURL: "https://localhost:5173",
      ignoreHTTPSErrors: true,
    });
    const otherPage = await otherDevice.newPage();
    await otherPage.goto("/");
    await otherPage.getByLabel("邮箱").fill(email);
    await otherPage.getByLabel("密码").fill(initialPassword);
    await otherPage.getByRole("button", { name: "登录 Workspace" }).click();
    await expect(otherPage.getByRole("heading", { name: "Agent 工作台" })).toBeVisible();

    try {
      await page.getByRole("button", { name: "账户设置" }).click();
      await page.getByLabel("当前密码").fill(initialPassword);
      await page.getByLabel("新密码").fill(replacementPassword);
      await page.getByRole("button", { name: "更新并撤销全部会话" }).click();
      await expect(page.getByRole("heading", { name: "欢迎回来" })).toBeVisible();

      await otherPage.reload();
      await expect(otherPage.getByRole("heading", { name: "欢迎回来" })).toBeVisible();

      await page.getByLabel("邮箱").fill(email);
      await page.getByLabel("密码").fill(initialPassword);
      await page.getByRole("button", { name: "登录 Workspace" }).click();
      await expect(page.getByRole("alert")).toContainText("The email or password is incorrect.");

      await page.getByLabel("密码").fill(replacementPassword);
      await page.getByRole("button", { name: "登录 Workspace" }).click();
      await expect(page.getByRole("heading", { name: "Agent 工作台" })).toBeVisible();

      await page.getByRole("button", { name: "退出登录" }).click();
      await expect(page.getByRole("heading", { name: "欢迎回来" })).toBeVisible();
    } finally {
      await otherDevice.close();
    }
  });

  test("streams a successful durable answer and restores it after refresh", async ({ page }) => {
    test.setTimeout(75_000);
    const uniquePart = [Date.now(), test.info().workerIndex].join("-");
    const email = `success-${uniquePart}@example.com`;
    const password = "Browser!Pass123";
    const question = `Day 2 成功回答 ${uniquePart}`;

    await page.goto("/");
    await page.getByRole("button", { exact: true, name: "创建账户" }).click();
    await page.getByLabel("邮箱").fill(email);
    await page.getByLabel("密码").fill(password);
    await page.getByRole("button", { name: "创建账户并进入" }).click();
    await expect(page.getByRole("heading", { name: "Agent 工作台" })).toBeVisible();

    const startResponsePromise = page.waitForResponse((response) => {
      const request = response.request();
      const pathname = new URL(response.url()).pathname;
      return (
        request.method() === "POST" &&
        /^\/api\/v1\/workspaces\/[^/]+\/conversations$/u.test(pathname) &&
        response.status() === 202
      );
    });
    const streamResponsePromise = page.waitForResponse((response) => {
      const request = response.request();
      const pathname = new URL(response.url()).pathname;
      return (
        request.method() === "GET" &&
        /^\/api\/v1\/workspaces\/[^/]+\/agent-runs\/[^/]+\/events$/u.test(pathname) &&
        response.status() === 200
      );
    });

    await page.getByLabel("输入问题").fill(question);
    await page.getByRole("button", { name: "发送问题" }).click();
    const startResponse = await startResponsePromise;
    const receipt = parseStartTurnReceipt((await startResponse.json()) as unknown);
    const streamResponse = await streamResponsePromise;
    expect(new URL(streamResponse.url()).pathname).toContain(
      `/agent-runs/${receipt.agentRunId}/events`,
    );

    const firstDelta = `${BROWSER_SUCCESS_ANSWER_PREFIX}${receipt.agentRunId}`;
    const driverPromise = executeBrowserCreatedRun(receipt);
    void driverPromise.catch(() => undefined);
    await expect(page.getByText(firstDelta, { exact: true })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByLabel("正在生成")).toBeVisible();

    await driverPromise;
    const expectedAnswer = `${firstDelta}${BROWSER_SUCCESS_ANSWER_SUFFIX}`;
    await expect(page.getByText(expectedAnswer, { exact: true })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("button", { exact: true, name: "停止" })).toHaveCount(0);

    await page.reload();
    await expect(page.getByRole("heading", { name: "Agent 工作台" })).toBeVisible();
    await page.getByRole("button", { name: new RegExp(question, "u") }).click();
    await expect(page.getByText(question, { exact: true }).last()).toBeVisible();
    await expect(page.getByText(expectedAnswer, { exact: true })).toBeVisible();
  });

  test("creates, restores, governs, and deletes a Memory revision", async ({ page }) => {
    test.setTimeout(75_000);
    const uniquePart = [Date.now(), test.info().workerIndex].join("-");
    const email = `memory-${uniquePart}@example.com`;
    const password = "Browser!Pass123";
    const sourceMessage = `请记住我的回答偏好 ${uniquePart}`;
    const confirmedContent = `默认使用中文和项目符号回答（${uniquePart}）。`;
    const updatedContent = `默认使用中文、项目符号和简短结论回答（${uniquePart}）。`;
    const recallQuestion = `回答应该使用什么语言和格式（${uniquePart}）？`;

    await page.goto("/");
    await page.getByRole("button", { exact: true, name: "创建账户" }).click();
    await page.getByLabel("邮箱").fill(email);
    await page.getByLabel("密码").fill(password);
    await page.getByRole("button", { name: "创建账户并进入" }).click();
    await expect(page.getByRole("heading", { name: "Agent 工作台" })).toBeVisible();

    const startResponsePromise = page.waitForResponse((response) => {
      const request = response.request();
      const pathname = new URL(response.url()).pathname;
      return (
        request.method() === "POST" &&
        /^\/api\/v1\/workspaces\/[^/]+\/conversations$/u.test(pathname) &&
        response.status() === 202
      );
    });

    await page.getByLabel("输入问题").fill(sourceMessage);
    await page.getByRole("button", { name: "发送问题" }).click();
    const startResponse = await startResponsePromise;
    const receipt = parseStartTurnReceipt((await startResponse.json()) as unknown);
    await executeBrowserCreatedRun(receipt);
    await expect(page.getByRole("button", { exact: true, name: "停止" })).toHaveCount(0);

    const sourceCard = page
      .getByText(sourceMessage, { exact: true })
      .locator("xpath=ancestor::article");
    await sourceCard.getByRole("button", { name: "选择为记忆来源" }).click();
    await page.getByRole("button", { name: "生成记忆候选" }).click();
    await expect(page.getByRole("heading", { name: "确认要长期保存的内容" })).toBeVisible();

    await page.getByLabel("最终确认内容").fill(confirmedContent);
    await page.getByLabel("记忆类型").selectOption("preference");
    await page.getByRole("button", { name: "创建新记忆" }).click();
    await expect(page.getByText("Memory 已确认")).toBeVisible();
    await expect(page.getByText(confirmedContent, { exact: true })).toBeVisible();
    await expect(page.getByText(/创建新记忆 · Revision 1/u)).toBeVisible();
    await page.getByRole("button", { name: "完成" }).click();

    await page.reload();
    await expect(page.getByRole("heading", { name: "Agent 工作台" })).toBeVisible();
    await page.getByRole("button", { name: new RegExp(sourceMessage, "u") }).click();
    await page.getByRole("button", { name: "记忆记录 1" }).click();
    await expect(page.getByText("Memory 已确认")).toBeVisible();
    await expect(page.getByText(confirmedContent, { exact: true })).toBeVisible();
    await expect(page.getByText(/创建新记忆 · Revision 1/u)).toBeVisible();
    await page.getByRole("button", { name: "完成" }).click();

    await page.getByRole("button", { name: "新建会话" }).click();
    const recallResponsePromise = page.waitForResponse((response) => {
      const request = response.request();
      const pathname = new URL(response.url()).pathname;
      return (
        request.method() === "POST" &&
        /^\/api\/v1\/workspaces\/[^/]+\/conversations$/u.test(pathname) &&
        response.status() === 202
      );
    });
    await page.getByLabel("输入问题").fill(recallQuestion);
    await page.getByRole("button", { name: "发送问题" }).click();
    const recallReceipt = parseStartTurnReceipt(
      (await (await recallResponsePromise).json()) as unknown,
    );
    await executeBrowserCreatedRun(recallReceipt);
    await expect(page.getByRole("button", { exact: true, name: "停止" })).toHaveCount(0);

    await page.getByRole("button", { name: "打开运行轨迹" }).click();
    const tracePanel = page.getByLabel("Agent 运行轨迹");
    const memorySource = tracePanel
      .getByText("长期 Memory", { exact: true })
      .locator("xpath=ancestor::li");
    await expect(memorySource).toContainText("已送入模型");
    await memorySource.getByRole("button", { name: "查看 Memory revision" }).click();
    await expect(page.getByRole("heading", { name: "Memory 管理" })).toBeVisible();
    await expect(page.getByLabel("当前正文")).toHaveValue(confirmedContent);

    await page.getByLabel("当前正文").fill(updatedContent);
    await page.getByRole("button", { name: "保存新 revision" }).click();
    await expect(page.getByText(/revision 2 · content v2/u)).toBeVisible();
    await expect(page.getByLabel("当前正文")).toHaveValue(updatedContent);

    const feedbackResponse = page.waitForResponse((response) => {
      const request = response.request();
      return request.method() === "POST" && new URL(response.url()).pathname.endsWith("/feedback");
    });
    await page.getByRole("button", { name: "有帮助" }).click();
    expect((await feedbackResponse).status()).toBe(200);

    await page.getByRole("button", { exact: true, name: "停用" }).click();
    const enableButton = page.getByRole("button", { exact: true, name: "恢复启用" });
    await expect(enableButton).toBeEnabled();
    await enableButton.click();
    await expect(page.getByRole("button", { exact: true, name: "停用" })).toBeEnabled();

    page.once("dialog", (dialog) => dialog.accept());
    await page.getByRole("button", { name: "删除" }).click();
    await expect(page.getByText("没有符合条件的 Memory。")).toBeVisible();
    await expect(page.getByText(updatedContent, { exact: true })).toHaveCount(0);
  });

  test("uses the industry-scoped Web Tool and exposes its safe Inspector", async ({ page }) => {
    test.setTimeout(75_000);
    const uniquePart = [Date.now(), test.info().workerIndex].join("-");
    const email = `tool-${uniquePart}@example.com`;
    const password = "Browser!Pass123";
    const question = `Day 3 Web 搜索 ${uniquePart}`;

    await page.goto("/");
    await page.getByRole("button", { exact: true, name: "创建账户" }).click();
    await page.getByLabel("邮箱").fill(email);
    await page.getByLabel("密码").fill(password);
    await page.getByRole("button", { name: "创建账户并进入" }).click();
    await expect(page.getByRole("heading", { name: "Agent 工作台" })).toBeVisible();

    await page.getByRole("button", { name: "行业情报" }).click();
    await expect(page.getByRole("heading", { name: "行业情报" })).toBeVisible();
    await expect(page.getByLabel("当前行业")).toHaveValue("5ae94c40-4441-5e6f-b4cb-0679e8a92f9e");
    await expect(page.getByText("Schedule → Occurrence → Job/Outbox → Worker")).toBeVisible();

    await page.getByRole("button", { name: "Agent" }).click();
    await page.getByLabel("回答模式").selectOption("web");
    await expect(page.getByText(/当前行业：智慧交通/u)).toBeVisible();

    const startResponsePromise = page.waitForResponse((response) => {
      const request = response.request();
      const pathname = new URL(response.url()).pathname;
      return (
        request.method() === "POST" &&
        /^\/api\/v1\/workspaces\/[^/]+\/conversations$/u.test(pathname) &&
        response.status() === 202
      );
    });
    await page.getByLabel("输入问题").fill(question);
    await page.getByRole("button", { name: "发送问题" }).click();
    const receipt = parseStartTurnReceipt((await (await startResponsePromise).json()) as unknown);
    await executeBrowserCreatedWebRun(receipt);

    const answer = `${ANSWER_PREFIX_DAY3}${receipt.agentRunId}${ANSWER_SUFFIX_DAY3}`;
    await expect(page.getByText(answer, { exact: true })).toBeVisible({ timeout: 15_000 });
    await page.getByRole("button", { name: "打开运行轨迹" }).click();
    const inspector = page.getByRole("region", { name: "Tool Inspector" });
    await expect(inspector).toContainText("industry.web_search");
    await expect(inspector).toContainText("模型可见信封摘要");
    await expect(inspector).not.toContainText("transport policy");

    await inspector.getByRole("button", { name: "提升为 Evidence" }).click();
    await expect(page.getByRole("heading", { name: "Evidence Inspector" })).toBeVisible();
    await expect(
      page.getByText("Public transport transition", { exact: true }).first(),
    ).toBeVisible();
    await page.getByRole("button", { name: /Public transport transition/u }).click();
    const evidenceDetail = page.getByRole("region", { name: "Evidence 详情" });
    await expect(evidenceDetail).toContainText(receipt.agentRunId);
    await expect(evidenceDetail).toContainText("evidence-normalizer-v1");
    await expect(evidenceDetail).toContainText("industry_source_v1");

    await page.reload();
    await page.getByRole("button", { name: "Evidence" }).click();
    await expect(
      page.getByText("Public transport transition", { exact: true }).first(),
    ).toBeVisible();

    await page.getByRole("button", { name: "数据库" }).click();
    await expect(page.getByRole("heading", { name: "数据库与安全 Text2SQL" })).toBeVisible();
    await expect(page.getByText(/完整 AST allowlist/u)).toBeVisible();

    await page.reload();
    await page.getByRole("button", { name: "Agent" }).click();
    await page.getByRole("button", { name: new RegExp(question, "u") }).click();
    await expect(page.getByText(answer, { exact: true })).toBeVisible();
  });

  test("creates, explains, links, and restores a Research L3 draft", async ({ page }) => {
    test.setTimeout(75_000);
    const uniquePart = [Date.now(), test.info().workerIndex].join("-");
    const email = `research-${uniquePart}@example.com`;
    const password = "Browser!Pass123";
    const question = `查找智慧交通公共政策更新 ${uniquePart}`;

    await page.goto("/");
    await page.getByRole("button", { exact: true, name: "创建账户" }).click();
    await page.getByLabel("邮箱").fill(email);
    await page.getByLabel("密码").fill(password);
    await page.getByRole("button", { name: "创建账户并进入" }).click();
    await expect(page.getByRole("heading", { name: "Agent 工作台" })).toBeVisible();

    await page.getByRole("button", { name: "Research" }).click();
    await expect(page.getByRole("heading", { name: "Research Workbench" })).toBeVisible();
    await expect(page.getByLabel("Research 行业")).toHaveValue(
      "5ae94c40-4441-5e6f-b4cb-0679e8a92f9e",
    );

    const startResponsePromise = page.waitForResponse((response) => {
      const request = response.request();
      return (
        request.method() === "POST" &&
        /^\/api\/v1\/workspaces\/[^/]+\/research-runs$/u.test(new URL(response.url()).pathname) &&
        response.status() === 202
      );
    });
    await page.getByLabel("Research 原始问题").fill(question);
    await page.getByLabel("Research 已确认范围").fill("智慧交通公共新闻\n政策更新");
    await page.getByLabel("Research 排除项").fill("投资建议");
    await page.getByRole("button", { name: "确认 Brief 并开始" }).click();
    const receipt = parseStartResearchReceipt(
      (await (await startResponsePromise).json()) as unknown,
    );

    await executeBrowserCreatedResearchRun(receipt);
    await page.getByRole("button", { name: "刷新服务端状态" }).click();
    const detail = page.getByRole("article", { name: "Research 详情" });
    await expect(detail.getByRole("heading", { name: question })).toBeVisible();
    await expect(detail.getByText("uncertain_draft")).toBeVisible();
    await expect(detail.getByText("不确定项：uncertain", { exact: true })).toBeVisible();
    await expect(detail.getByText("校验研究范围").first()).toBeVisible();
    await expect(detail.getByText("保存 L3 草稿").last()).toBeVisible();
    await expect(detail.getByText(/coverage 0%/u)).toBeVisible();
    await expect(detail.getByText("$0.000080", { exact: true })).toBeVisible();

    await detail.getByRole("button", { name: "查看完整 Evidence/Claim 图" }).click();
    await expect(page.getByRole("heading", { name: "Evidence Inspector" })).toBeVisible();
    await expect(page.getByText(/public update remains uncertain/u)).toBeVisible();
    await page.getByRole("button", { name: "打开 Research 时间线" }).click();
    await expect(page.getByRole("heading", { name: question })).toBeVisible();

    await page.reload();
    await page.getByRole("button", { name: "Research" }).click();
    await expect(page.getByRole("heading", { name: question })).toBeVisible();
    await expect(page.getByText("uncertain_draft")).toBeVisible();
  });

  test("creates, stops, and restores a real Day 2 conversation", async ({ page }) => {
    test.setTimeout(45_000);
    const uniquePart = [Date.now(), test.info().workerIndex].join("-");
    const email = `chat-${uniquePart}@example.com`;
    const password = "Browser!Pass123";
    const question = `Day 2 浏览器会话 ${uniquePart}`;
    const attachmentQuestion = `${question} 附件`;
    const discardedAttachment = `discarded-${uniquePart}.txt`;
    const attachedDocument = `evidence-${uniquePart}.md`;

    await page.goto("/");
    await page.getByRole("button", { exact: true, name: "创建账户" }).click();
    await page.getByLabel("邮箱").fill(email);
    await page.getByLabel("密码").fill(password);
    await page.getByRole("button", { name: "创建账户并进入" }).click();

    await expect(page.getByRole("heading", { name: "Agent 工作台" })).toBeVisible();
    const attachmentPicker = page.getByLabel("选择附件");
    await attachmentPicker.setInputFiles({
      name: discardedAttachment,
      mimeType: "text/plain",
      buffer: Buffer.from("This staging object must be deleted before the Turn is submitted."),
    });
    await expect(page.getByLabel("正在上传")).toHaveCount(0, { timeout: 10_000 });
    await page.getByRole("button", { name: `移除 ${discardedAttachment}` }).click();
    await expect(page.getByText(discardedAttachment, { exact: true })).toHaveCount(0);

    await page.getByLabel("输入问题").fill(question);
    await page.getByRole("button", { name: "发送问题" }).click();

    await expect(page.getByText(question, { exact: true }).last()).toBeVisible();
    await expect(page.getByRole("button", { exact: true, name: "停止" })).toBeVisible();
    await page.getByRole("button", { exact: true, name: "停止" }).click();
    await expect(page.getByText(/本次回答已停止，已经生成的片段仍然保留/u)).toBeVisible();
    await expect(page.getByRole("button", { exact: true, name: "停止" })).toHaveCount(0);

    await page.reload();
    await expect(page.getByRole("heading", { name: "Agent 工作台" })).toBeVisible();
    await page.getByRole("button", { name: new RegExp(question, "u") }).click();
    await expect(page.getByText(question, { exact: true }).last()).toBeVisible();
    await expect(page.getByText("直接回答").first()).toBeVisible();
    await expect(page.getByText(/本次回答已停止，已经生成的片段仍然保留/u)).toBeVisible();

    await page.getByRole("button", { name: "重新提问" }).click();
    await expect(page.getByLabel("输入问题")).toHaveValue(question);
    await page.getByRole("button", { name: "发送问题" }).click();
    await expect(page.getByRole("button", { exact: true, name: "停止" })).toBeVisible();
    await page.getByRole("button", { exact: true, name: "停止" }).click();
    await expect(page.getByText(/本次回答已停止，已经生成的片段仍然保留/u)).toBeVisible();

    await attachmentPicker.setInputFiles({
      name: attachedDocument,
      mimeType: "text/markdown",
      buffer: Buffer.from(
        "# Day 2\n\nThis is untrusted attachment data for the real browser journey.",
      ),
    });
    await expect(page.getByLabel("正在上传")).toHaveCount(0, { timeout: 10_000 });
    await expect(page.getByText(attachedDocument, { exact: true })).toBeVisible();
    await page.getByLabel("输入问题").fill(attachmentQuestion);
    await page.getByRole("button", { name: "发送问题" }).click();
    await expect(page.getByRole("button", { exact: true, name: "停止" })).toBeVisible();
    await page.getByRole("button", { exact: true, name: "停止" }).click();
    await expect(page.getByText(/本次回答已停止，已经生成的片段仍然保留/u)).toBeVisible();

    await page.reload();
    await expect(page.getByRole("heading", { name: "Agent 工作台" })).toBeVisible();
    await page.getByRole("button", { name: new RegExp(question, "u") }).click();
    await expect(page.getByText(attachmentQuestion, { exact: true })).toBeVisible();
    await expect(
      page.getByRole("button", { name: new RegExp(attachedDocument, "u") }),
    ).toBeVisible();

    const renamedTitle = `已收口会话 ${uniquePart}`;
    await page.getByRole("button", { name: "重命名会话" }).click();
    await page.getByLabel("会话标题").fill(renamedTitle);
    await page.getByLabel("会话标题").press("Enter");
    await expect(page.getByRole("heading", { name: renamedTitle })).toBeVisible();

    await page.getByRole("button", { name: "删除会话" }).click();
    await expect(page.getByRole("dialog", { name: "删除这段会话？" })).toBeVisible();
    await page.getByRole("button", { name: "确认删除" }).click();
    await expect(page.getByText(question, { exact: true })).toHaveCount(0);
    await expect(page.getByText(/还没有匹配的会话/u)).toBeVisible();
  });
});
