import { beforeEach, describe, expect, it, vi } from "vitest";

import type * as ApiModule from "../api/api";

const mocks = vi.hoisted(() => ({
  GET: vi.fn(),
  withAccessToken: vi.fn((request: (accessToken: string) => Promise<unknown>) =>
    request("access-token"),
  ),
}));

vi.mock("../api/api", async (importOriginal) => {
  const original = await importOriginal<typeof ApiModule>();
  return {
    ...original,
    apiClient: mocks,
    withAccessToken: mocks.withAccessToken,
  };
});

import { getVerificationReport } from "./research-api";

const workspaceId = "11111111-1111-4111-8111-111111111111";
const researchRunId = "22222222-2222-4222-8222-222222222222";

describe("research-api", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("loads the formal verification report from the workspace-scoped Research route", async () => {
    const report = { report_id: "33333333-3333-4333-8333-333333333333" };
    mocks.GET.mockResolvedValue({
      data: report,
      response: new Response(null, { status: 200 }),
    });

    expect(await getVerificationReport(workspaceId, researchRunId)).toBe(report);
    expect(mocks.GET).toHaveBeenCalledWith(
      "/api/v1/workspaces/{workspace_id}/research-runs/{research_run_id}/verification-report",
      {
        headers: { Authorization: "Bearer access-token" },
        params: {
          path: { research_run_id: researchRunId, workspace_id: workspaceId },
        },
      },
    );
  });
});
