// Open the real, previously seeded local workspace in a visible browser.
import { chromium, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";

const account = JSON.parse(
  await readFile(new URL("../../.data/local/demo-account.json", import.meta.url), "utf8"),
);
const browser = await chromium.launch({ headless: false });
const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: null });
const page = await context.newPage();
try {
  await page.goto("https://localhost:8443");
  await page.getByLabel("邮箱").fill(account.email);
  await page.getByLabel("密码").fill(account.password);
  await page.getByRole("button", { name: "登录 Workspace" }).click();
  await expect(page.getByRole("heading", { name: "Agent 工作台" })).toBeVisible({
    timeout: 30_000,
  });
  await page.getByRole("button", { name: "Research", exact: true }).click();
  await expect(page.getByRole("heading", { name: "杜邦分析结果" })).toBeVisible({
    timeout: 30_000,
  });
  await page.getByRole("heading", { name: "杜邦分析结果" }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: ".data/local/opened-app.png", animations: "disabled" });
  await context.waitForEvent("close", { timeout: 0 });
} catch (error) {
  await browser.close();
  throw error;
}
