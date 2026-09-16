import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ResearchResultPanel, ResearchResults } from "./ResearchResultPanel";
import type { ResearchResultView } from "./research-api";

const mocks = vi.hoisted(() => ({ getResearchResultView: vi.fn() }));
vi.mock("./research-api", () => mocks);
vi.mock("../data-explorer/SafeChart", () => ({
  SafeChart: ({
    title,
    option,
    onDataClick,
  }: {
    title: string;
    option: Record<string, unknown>;
    onDataClick: (index: number) => void;
  }) => (
    <button
      type="button"
      aria-label={title}
      data-option={JSON.stringify(option)}
      onClick={() => {
        onDataClick(0);
      }}
    >
      图表
    </button>
  ),
}));

function fixture(): ResearchResultView {
  const keys = [
    "revenue",
    "net_income",
    "opening_assets",
    "closing_assets",
    "opening_equity",
    "closing_equity",
    "average_assets",
    "average_equity",
    "net_profit_margin_percent",
    "asset_turnover",
    "equity_multiplier",
    "roe_percent",
  ];
  const labels = [
    "营业收入",
    "归母净利润",
    "期初总资产",
    "期末总资产",
    "期初归母权益",
    "期末归母权益",
    "平均总资产",
    "平均归母权益",
    "净利率",
    "总资产周转率",
    "权益乘数",
    "ROE",
  ];
  return {
    schema_version: 1,
    research_run_id: "run",
    draft_id: "draft",
    draft_revision: 2,
    verification_report_id: "report",
    verification_status: "verified",
    status: "ready",
    periods: ["2023-06-30", "2024-06-30", "2025-06-30"],
    limitations: ["明确限制"],
    rows: keys.map((key, index) => ({
      key,
      label: labels[index] ?? key,
      unit: key.endsWith("percent") ? "%" : index < 8 ? "USD × 10^6" : "倍",
      change_unit: key.endsWith("percent") ? "百分点" : "倍",
      values: [0, 1, 2].map((position) => ({
        value: `${String(10 + position)}.1234`,
        change: position === 0 ? null : "1.0000",
        evidence_refs: [`${key}-${String(position)}`],
      })),
    })),
  };
}

describe("ResearchResults", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });
  it("renders exactly four bars, metric cards, input table and year-selected decomposition", async () => {
    const user = userEvent.setup();
    const open = vi.fn();
    render(<ResearchResults result={fixture()} onOpenEvidence={open} />);
    expect(screen.getByText("已核验")).toBeVisible();
    expect(screen.getAllByText("较上年变化 1.0000 百分点")).toHaveLength(2);
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(13);
    expect(screen.getByLabelText("杜邦分解图")).toHaveTextContent("ROE = 净利率");
    const chart = await screen.findByRole("button", { name: "ROE跨年度柱状图" });
    await user.click(chart);
    expect(open).toHaveBeenLastCalledWith("roe_percent-0");
    expect(screen.getAllByRole("button", { name: /跨年度柱状图/ })).toHaveLength(4);
    expect(chart.dataset.option).toContain('"type":"bar"');
    expect(chart.dataset.option).not.toContain('"type":"line"');
    await user.selectOptions(screen.getByLabelText("展示财年"), "2023-06-30");
    expect(screen.getAllByText("无上年可比值")).toHaveLength(4);
    await user.click(within(table).getByRole("button", { name: "2023-06-30 营业收入 来源 1" }));
    expect(open).toHaveBeenLastCalledWith("revenue-0");
  });
  it("does not disguise unavailable data as zero or unverified data as verified", () => {
    const { rerender } = render(
      <ResearchResults
        result={{ ...fixture(), verification_status: null }}
        onOpenEvidence={vi.fn()}
      />,
    );
    expect(screen.getByText("尚未核验当前版本")).toBeVisible();
    rerender(
      <ResearchResults
        result={{ ...fixture(), status: "unavailable", rows: [], periods: [] }}
        onOpenEvidence={vi.fn()}
      />,
    );
    expect(screen.getByText("研究结果暂不可展示")).toBeVisible();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
  it("rejects stale report versions and ignores late responses after switching runs", async () => {
    let resolveOld: (value: ResearchResultView) => void = vi.fn();
    mocks.getResearchResultView.mockReturnValueOnce(
      new Promise<ResearchResultView>((resolve) => {
        resolveOld = resolve;
      }),
    );
    const props = {
      workspaceId: "ws",
      researchRunId: "run",
      draftId: "draft",
      draftRevision: 2,
      onOpenEvidence: vi.fn(),
    };
    const { rerender } = render(<ResearchResultPanel {...props} />);
    mocks.getResearchResultView.mockResolvedValue({
      ...fixture(),
      research_run_id: "new",
      draft_revision: 1,
    });
    rerender(<ResearchResultPanel {...props} researchRunId="new" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("报告版本已变化");
    await act(async () => {
      resolveOld(fixture());
      await Promise.resolve();
    });
    expect(screen.queryByText("已核验")).not.toBeInTheDocument();
    mocks.getResearchResultView.mockResolvedValue({ ...fixture(), research_run_id: "new" });
    await userEvent.click(screen.getByRole("button", { name: "重试读取结果" }));
    await waitFor(() => {
      expect(screen.getByText("已核验")).toBeVisible();
    });
  });
});
