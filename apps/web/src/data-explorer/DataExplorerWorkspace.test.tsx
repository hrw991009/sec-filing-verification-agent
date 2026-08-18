import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

const connection = {
  created_at: "2026-08-17T08:00:00Z",
  dialect: "postgresql",
  id: "connection-1",
  last_error_code: null,
  name: "只读合成样例",
  status: "ready" as const,
  updated_at: "2026-08-17T08:00:00Z",
};

const mocks = vi.hoisted(() => ({
  browseTableRows: vi.fn(() =>
    Promise.resolve({
      columns: ["company_name", "industry"],
      limit: 20,
      offset: 0,
      rows: [["Transit Co", "smart_transport"]],
      truncated: false,
    }),
  ),
  ensureSampleConnection: vi.fn(() => Promise.resolve(connection)),
  executeQuery: vi.fn(() =>
    Promise.resolve({
      chart_artifact: {
        chart_type: "bar" as const,
        content_sha256: "b".repeat(64),
        created_at: "2026-08-17T08:01:00Z",
        id: "chart-1",
        option: { series: [{ type: "bar" }] },
      },
      connection_id: "connection-1",
      created_at: "2026-08-17T08:01:00Z",
      error_code: null,
      generated_sql: "SELECT industry, SUM(revenue) AS total_revenue FROM sample",
      id: "query-1",
      plan_cost: 12,
      plan_rows: 4,
      question: "按行业汇总样例公司收入",
      row_count: 1,
      schema_snapshot_id: "snapshot-1",
      status: "completed" as const,
      table_artifact: {
        columns: ["industry", "total_revenue"],
        content_sha256: "a".repeat(64),
        created_at: "2026-08-17T08:01:00Z",
        id: "table-1",
        rows: [["smart_transport", 120]],
        truncated: false,
      },
      terminal_at: "2026-08-17T08:01:01Z",
      validated_sql:
        "SELECT industry, SUM(revenue) AS total_revenue FROM public.sample_company_metrics GROUP BY industry LIMIT 20",
    }),
  ),
  listDataConnections: vi.fn(() => Promise.resolve([connection])),
  listQueryRuns: vi.fn(() => Promise.resolve([])),
  listTables: vi.fn(() =>
    Promise.resolve([
      {
        columns: [
          { data_type: "text", name: "company_name", nullable: false, ordinal: 1 },
          { data_type: "text", name: "industry", nullable: false, ordinal: 2 },
        ],
        estimated_rows: 4,
        indexes: [
          {
            columns: ["company_name"],
            name: "sample_company_metrics_pkey",
            primary: true,
            unique: true,
          },
        ],
        schema_name: "public",
        table_name: "sample_company_metrics",
        total_bytes: 8192,
      },
    ]),
  ),
  testDataConnection: vi.fn(() => Promise.resolve(connection)),
}));

vi.mock("./data-explorer-api", () => mocks);
vi.mock("./SafeChart", () => ({
  SafeChart: ({ title }: { readonly title: string }) => (
    <div aria-label={`图表 ${title}`} role="img" />
  ),
}));

import { DataExplorerWorkspace } from "./DataExplorerWorkspace";

describe("DataExplorerWorkspace", () => {
  it("browses the formal schema and renders a validated query Artifact", async () => {
    const user = userEvent.setup();
    render(<DataExplorerWorkspace canManage workspaceId="workspace-1" />);

    expect(await screen.findByText("public.sample_company_metrics")).toBeVisible();
    expect(await screen.findByText("Transit Co")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "执行安全查询" }));

    const summary = await screen.findByRole("region", { name: "QueryRun 结果" });
    expect(summary).toBeVisible();
    expect(within(summary).getByText(/SELECT industry, SUM\(revenue\)/u)).toBeVisible();
    expect(screen.getByRole("region", { name: "查询 Artifact" })).toBeVisible();
    expect(await screen.findByRole("img", { name: "图表 bar" })).toBeVisible();
    expect(mocks.executeQuery).toHaveBeenCalledWith(
      "workspace-1",
      expect.objectContaining({ connection_id: "connection-1" }),
    );
  });

  it("tests the selected connection through the server boundary", async () => {
    const user = userEvent.setup();
    render(<DataExplorerWorkspace canManage workspaceId="workspace-1" />);
    await screen.findByText("public.sample_company_metrics");
    await user.click(screen.getByRole("button", { name: "测试连接" }));
    await waitFor(() => {
      expect(mocks.testDataConnection).toHaveBeenCalledWith("workspace-1", "connection-1");
    });
  });
});
