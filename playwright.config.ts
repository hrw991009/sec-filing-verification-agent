import { defineConfig, devices } from "@playwright/test";

const baseURL = "https://localhost:5173";
const backendUv =
  process.env.CI === "true"
    ? "uv run --locked --package industry-platform-backend"
    : "uv run --env-file .env --locked --package industry-platform-backend";

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
      command: `${backendUv} alembic -c apps/backend/alembic.ini upgrade head && ${backendUv} industry-platform-api`,
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
