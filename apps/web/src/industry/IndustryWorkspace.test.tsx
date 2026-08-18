import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  createCollectionSchedule: vi.fn(() => Promise.resolve({ created: true, id: "schedule-1" })),
  listCollectionRuns: vi.fn(() => Promise.resolve([])),
  listCollectionSchedules: vi.fn(() =>
    Promise.resolve([
      {
        cron_expression: "0 */6 * * *",
        enabled: true,
        id: "schedule-1",
        industry_id: "industry-1",
        kind: "news" as const,
        last_fired_at: null,
        misfire_error_code: null,
        misfire_policy: "coalesce_latest" as const,
        next_due_at: "2026-08-17T12:00:00Z",
        timezone_name: "Asia/Shanghai",
      },
    ]),
  ),
  listProviderStatuses: vi.fn(() =>
    Promise.resolve([
      {
        kind: "news" as const,
        provider: "world_bank_news" as const,
        readiness: "ready" as const,
        reason_code: null,
      },
    ]),
  ),
  listSourceItems: vi.fn(() =>
    Promise.resolve([
      {
        collected_at: "2026-08-17T08:01:00Z",
        content_sha256: "a".repeat(64),
        external_id: "source-1",
        id: "source-1",
        industry_id: "industry-1",
        kind: "news" as const,
        locator: "javascript:alert(1)",
        metadata: {},
        provider: "world_bank_news" as const,
        published_at: "2026-08-17T08:00:00Z",
        summary: "持久化的来源摘要。",
        title: "公共交通动态",
      },
    ]),
  ),
  setIndustryPreference: vi.fn(() =>
    Promise.resolve({
      industry: {
        code: "fintech",
        default_query: "fintech",
        default_symbol: "FIN",
        id: "industry-2",
        name: "金融科技",
      },
      updated_at: "2026-08-17T08:00:00Z",
      user_id: "user-1",
      workspace_id: "workspace-1",
    }),
  ),
  triggerCollection: vi.fn(() =>
    Promise.resolve({ created: true, job_id: "job-1", occurrence_id: "occurrence-1" }),
  ),
}));

vi.mock("./industry-api", () => mocks);

import { IndustryWorkspace } from "./IndustryWorkspace";

const industries = [
  {
    code: "smart_transport",
    default_query: "transport",
    default_symbol: "TRANSPORT",
    id: "industry-1",
    name: "智慧交通",
  },
  {
    code: "fintech",
    default_query: "fintech",
    default_symbol: "FIN",
    id: "industry-2",
    name: "金融科技",
  },
];

describe("IndustryWorkspace", () => {
  it("renders durable source/readiness facts, persists industry, and rejects an unsafe locator", async () => {
    const user = userEvent.setup();
    const onSelectIndustry = vi.fn();
    render(
      <IndustryWorkspace
        canManage
        industries={industries}
        onSelectIndustry={onSelectIndustry}
        selectedIndustryId="industry-1"
        workspaceId="workspace-1"
      />,
    );

    expect(await screen.findByText("公共交通动态")).toBeVisible();
    expect(screen.getAllByText("world_bank_news")).toHaveLength(2);
    expect(screen.getByText("来源地址未通过校验")).toBeVisible();
    expect(screen.queryByRole("link", { name: "查看原始来源" })).not.toBeInTheDocument();
    expect(screen.getByText(/Asia\/Shanghai/u)).toBeVisible();

    await user.selectOptions(screen.getByLabelText("当前行业"), "industry-2");
    await waitFor(() => {
      expect(mocks.setIndustryPreference).toHaveBeenCalledWith("workspace-1", "industry-2");
      expect(onSelectIndustry).toHaveBeenCalledWith("industry-2");
    });
  });

  it("uses the formal manual collection trigger for an existing schedule", async () => {
    const user = userEvent.setup();
    render(
      <IndustryWorkspace
        canManage
        industries={industries}
        onSelectIndustry={vi.fn()}
        selectedIndustryId="industry-1"
        workspaceId="workspace-1"
      />,
    );
    await screen.findByText(/Asia\/Shanghai/u);
    await user.click(screen.getByRole("button", { name: "立即运行" }));
    await waitFor(() => {
      expect(mocks.triggerCollection).toHaveBeenCalledWith("workspace-1", "schedule-1");
    });
  });
});
