import { expect, test } from "@playwright/test";

test.describe("browser identity lifecycle", () => {
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

  test("creates, stops, and restores a real Day 2 conversation", async ({ page }) => {
    const uniquePart = [Date.now(), test.info().workerIndex].join("-");
    const email = `chat-${uniquePart}@example.com`;
    const password = "Browser!Pass123";
    const question = `Day 2 浏览器会话 ${uniquePart}`;

    await page.goto("/");
    await page.getByRole("button", { exact: true, name: "创建账户" }).click();
    await page.getByLabel("邮箱").fill(email);
    await page.getByLabel("密码").fill(password);
    await page.getByRole("button", { name: "创建账户并进入" }).click();

    await expect(page.getByRole("heading", { name: "Agent 工作台" })).toBeVisible();
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
  });
});
