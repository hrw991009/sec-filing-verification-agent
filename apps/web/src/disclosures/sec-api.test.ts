import { beforeEach, describe, expect, it, vi } from "vitest";
import type * as ApiModule from "../api/api";
import { listSecFilings } from "./sec-api";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("../api/api", async (importOriginal) => ({
  ...(await importOriginal<typeof ApiModule>()),
  withAccessToken: (operation: (token: string) => Promise<unknown>) => operation("test-access"),
  apiClient: { GET: mocks.get },
}));

describe("SEC filing coverage", () => {
  beforeEach(() => vi.clearAllMocks());

  it("does not present unavailable historical source coverage as an empty result", async () => {
    mocks.get.mockResolvedValue({
      data: {
        status: "incomplete",
        error_code: "source_version_not_visible_at_as_of",
        filings: [],
      },
      response: new Response(null, { status: 200 }),
    });
    await expect(
      listSecFilings("workspace", {
        asOf: "2024-12-31T00:00:00Z",
        cik: "0000320193",
        forms: ["10-K"],
        reportPeriodStart: "2024-01-01",
        reportPeriodEnd: "2024-12-31",
      }),
    ).rejects.toThrow("来源版本在截止时点不可见");
  });
});
