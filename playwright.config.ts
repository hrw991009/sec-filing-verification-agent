import { defineConfig, devices } from "@playwright/test";

const baseURL = "https://localhost:5173";
const backendUv =
  process.env.CI === "true"
    ? "uv run --locked --package sec-filing-verification-agent-backend"
    : "uv run --env-file .env --locked --package sec-filing-verification-agent-backend";
const realSecJourney = process.env.SEC_REAL_BROWSER_E2E === "true";

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
      testIgnore: "**/sec-real-journey.spec.ts",
      use: {
        ...devices["Desktop Chrome"],
        ignoreHTTPSErrors: true,
      },
    },
    ...(realSecJourney
      ? [
          {
            name: "sec-real-journey",
            testMatch: "**/sec-real-journey.spec.ts",
            use: {
              ...devices["Desktop Chrome"],
              ignoreHTTPSErrors: true,
            },
          },
        ]
      : []),
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
      command: `${backendUv} alembic -c apps/backend/alembic.ini upgrade head && ${backendUv} sec-filing-verification-api`,
      reuseExistingServer: process.env.CI !== "true",
      stderr: "pipe",
      stdout: "pipe",
      timeout: 120_000,
      url: "http://127.0.0.1:8000/health/live",
    },
    {
      command: "pnpm --filter @sec-filing-verification/web run dev",
      ignoreHTTPSErrors: true,
      reuseExistingServer: process.env.CI !== "true",
      stderr: "pipe",
      stdout: "pipe",
      timeout: 120_000,
      url: baseURL,
    },
  ],
});
