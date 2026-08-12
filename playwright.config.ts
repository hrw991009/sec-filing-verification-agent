import { defineConfig, devices } from "@playwright/test";

const baseURL = "https://localhost:5173";

export default defineConfig({
  expect: {
    timeout: 5_000,
  },
  forbidOnly: true,
  fullyParallel: true,
  outputDir: "test-results/playwright",
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        ignoreHTTPSErrors: true,
      },
    },
  ],
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  retries: 0,
  testDir: "./tests/e2e",
  testMatch: "**/*.spec.ts",
  timeout: 30_000,
  use: {
    baseURL,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: "retain-on-failure",
  },
  webServer: [
    {
      command:
        "uv run --package industry-platform-backend alembic -c apps/backend/alembic.ini upgrade head && uv run --package industry-platform-backend industry-platform-api",
      reuseExistingServer: process.env.CI !== "true",
      stderr: "pipe",
      stdout: "pipe",
      timeout: 120_000,
      url: "http://127.0.0.1:8000/health/live",
    },
    {
      command: "pnpm --filter @industry-platform/web run dev",
      ignoreHTTPSErrors: true,
      reuseExistingServer: process.env.CI !== "true",
      stderr: "pipe",
      stdout: "pipe",
      timeout: 120_000,
      url: baseURL,
    },
  ],
});
