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

    await expect(page.getByRole("heading", { name: "你的 Workspace" })).toBeVisible();
    await expect(page.getByText(email)).toBeVisible();

    await page.reload();
    await expect(page.getByRole("heading", { name: "你的 Workspace" })).toBeVisible();

    const otherDevice = await browser.newContext({
      baseURL: "https://localhost:5173",
      ignoreHTTPSErrors: true,
    });
    const otherPage = await otherDevice.newPage();
    await otherPage.goto("/");
    await otherPage.getByLabel("邮箱").fill(email);
    await otherPage.getByLabel("密码").fill(initialPassword);
    await otherPage.getByRole("button", { name: "登录 Workspace" }).click();
    await expect(otherPage.getByRole("heading", { name: "你的 Workspace" })).toBeVisible();

    try {
      await page.getByRole("button", { name: "修改密码" }).click();
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
      await expect(page.getByRole("heading", { name: "你的 Workspace" })).toBeVisible();

      await page.getByRole("button", { name: "退出" }).click();
      await expect(page.getByRole("heading", { name: "欢迎回来" })).toBeVisible();
    } finally {
      await otherDevice.close();
    }
  });
});
