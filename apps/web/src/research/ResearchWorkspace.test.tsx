import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentTrace } from "../chat/chat-api";
import type { Industry } from "../industry/industry-api";
import type { ResearchRun } from "./research-api";

const workspaceId = "11111111-1111-4111-8111-111111111111";
const researchRunId = "22222222-2222-4222-8222-222222222222";
const agentRunId = "33333333-3333-4333-8333-333333333333";
const industryId = "5ae94c40-4441-5e6f-b4cb-0679e8a92f9e";

const industry: Industry = {
  code: "smart_transport",
  default_query: "transport policy",
  default_symbol: "",
  id: industryId,
  name: "智慧交通",
};

const researchRun: ResearchRun = {
  agent_run_id: agentRunId,
  agent_status: "completed",
  brief: {
    budget: {
      deadline: "2026-08-22T08:10:00Z",
      max_cost_micro_usd: 300_000,
      max_steps: 20,
      max_total_tokens: 12_000,
    },
    completion_criteria: ["Produce an attributable L3 draft"],
    confirmed_at: "2026-08-22T08:00:00Z",
    confirmed_by_user_id: "44444444-4444-4444-8444-444444444444",
    confirmed_scope: ["Public smart transport news"],
    exclusions: ["Investment advice"],
    id: "55555555-5555-4555-8555-555555555555",
    original_question: "Find a public transport policy update.",
    revision: 1,
  },
  cost_micro_usd: 80,
  created_at: "2026-08-22T08:00:00Z",
  current_node: "draft",
  draft: {
    claim_refs: ["66666666-6666-4666-8666-666666666666"],
    content_markdown: "## Finding\n\nEvidence is incomplete; this is not a verified report.",
    created_at: "2026-08-22T08:00:05Z",
    evidence_refs: [],
    id: "77777777-7777-4777-8777-777777777777",
    outline: ["Finding", "Limitations"],
    status: "uncertain_draft",
    uncertainty_summary: "No source passed the immutable snapshot gate.",
    updated_at: "2026-08-22T08:00:05Z",
  },
  event_count: 32,
  graph_version: "research-l3-graph-v1",
  id: researchRunId,
  input_tokens_used: 40,
  output_tokens_used: 20,
  owner_user_id: "44444444-4444-4444-8444-444444444444",
  plan: {
    actions: [
      {
        allowed_tool_names: ["industry.web_search"],
        objective: "Collect bounded public evidence for the confirmed scope.",
        ordinal: 1,
      },
    ],
    brief_revision: 1,
    created_at: "2026-08-22T08:00:01Z",
    id: "88888888-8888-4888-8888-888888888888",
    planner_summary: "One bounded research action using the trusted Tool surface.",
    revision: 1,
  },
  revision: 10,
  state_schema_version: 1,
  status: "completed",
  step_count: 4,
  stop_reason: "final",
  updated_at: "2026-08-22T08:00:05Z",
  workspace_id: workspaceId,
};

const trace = {
  context_manifests: [],
  events: [
    {
      details: { node: "clarify_scope" },
      event_type: "agent.research.node_completed",
      occurred_at: "2026-08-22T08:00:01Z",
      schema_version: 1,
      sequence: 4,
    },
    {
      details: { node: "draft" },
      event_type: "agent.research.node_completed",
      occurred_at: "2026-08-22T08:00:05Z",
      schema_version: 1,
      sequence: 31,
    },
  ],
  run: {
    conversation_id: "99999999-9999-4999-8999-999999999999",
    created_at: "2026-08-22T08:00:00Z",
    deadline: researchRun.brief.budget.deadline,
    event_count: 32,
    event_stream_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    harness_version: "research-harness-v1",
    max_cost_micro_usd: 300_000,
    max_steps: 20,
    max_total_tokens: 12_000,
    run_id: agentRunId,
    run_type: "research",
    runtime_version: "research-l3-runtime-v1",
    schema_version: 1,
    started_at: "2026-08-22T08:00:01Z",
    state_revision: 10,
    status: "completed",
    step_count: 4,
    stop_reason: "final",
    terminal_at: "2026-08-22T08:00:05Z",
    trace_id: "research-workbench-test",
    turn_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    usage: {
      cached_input_tokens: 0,
      cost_micro_usd: 80,
      input_tokens: 40,
      output_tokens: 20,
    },
    workspace_id: workspaceId,
  },
  schema_version: 1,
  steps: [
    {
      completed_at: "2026-08-22T08:00:02Z",
      error_code: null,
      kind: "model",
      last_event_sequence: 8,
      sequence: 1,
      started_at: "2026-08-22T08:00:01Z",
      status: "completed",
      step_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      usage: {
        cached_input_tokens: 0,
        cost_micro_usd: 40,
        input_tokens: 20,
        output_tokens: 10,
      },
    },
  ],
} satisfies AgentTrace;

const researchMocks = vi.hoisted(() => ({
  getResearchRun: vi.fn(),
  listResearchRuns: vi.fn(),
  startResearch: vi.fn(),
}));
const chatMocks = vi.hoisted(() => ({ cancelRun: vi.fn(), getAgentTrace: vi.fn() }));
const evidenceMocks = vi.hoisted(() => ({ listResearchClaims: vi.fn() }));

vi.mock("./research-api", () => researchMocks);
vi.mock("../chat/chat-api", () => chatMocks);
vi.mock("../evidence/evidence-api", () => evidenceMocks);

import { ResearchWorkspace } from "./ResearchWorkspace";

describe("ResearchWorkspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    researchMocks.listResearchRuns.mockResolvedValue([researchRun]);
    researchMocks.getResearchRun.mockResolvedValue(researchRun);
    chatMocks.getAgentTrace.mockResolvedValue(trace);
    evidenceMocks.listResearchClaims.mockResolvedValue([
      {
        confidence: 0,
        conflict: false,
        coverage: 0,
        created_at: researchRun.created_at,
        id: "66666666-6666-4666-8666-666666666666",
        relations: [],
        research_run_id: researchRunId,
        revision: 1,
        statement: "The public transport update lacks immutable supporting evidence.",
        updated_at: researchRun.updated_at,
        verification_status: "uncertain",
        workspace_id: workspaceId,
      },
    ]);
  });

  it("rebuilds the Brief, timeline, uncertain Claim and draft from formal APIs", async () => {
    const user = userEvent.setup();
    const onOpenAgent = vi.fn();
    const onOpenEvidence = vi.fn();
    render(
      <ResearchWorkspace
        canManage
        focusedResearchRunId={researchRunId}
        industries={[industry]}
        onOpenAgent={onOpenAgent}
        onOpenEvidence={onOpenEvidence}
        onSelectIndustry={vi.fn()}
        selectedIndustryId={industryId}
        workspaceId={workspaceId}
      />,
    );

    expect(
      await screen.findByRole("heading", { name: researchRun.brief.original_question }),
    ).toBeVisible();
    expect(screen.getByText("校验研究范围")).toBeVisible();
    expect(screen.getAllByText("保存 L3 草稿")).toHaveLength(2);
    expect(screen.getByText("uncertain_draft")).toBeVisible();
    expect(screen.getByText(/coverage 0%/u)).toBeVisible();
    expect(screen.getByText(/No source passed/u)).toBeVisible();

    await user.click(screen.getByRole("button", { name: "准备 L0" }));
    expect(onOpenAgent).toHaveBeenCalledWith(researchRun.brief.original_question, "none");
    await user.click(screen.getByRole("button", { name: "准备 L2" }));
    expect(onOpenAgent).toHaveBeenCalledWith(researchRun.brief.original_question, "web");
    await user.click(screen.getByRole("button", { name: "查看完整 Evidence/Claim 图" }));
    expect(onOpenEvidence).toHaveBeenCalledWith(null);

    await user.click(screen.getByRole("button", { name: "刷新服务端状态" }));
    await waitFor(() => {
      expect(researchMocks.listResearchRuns).toHaveBeenCalledTimes(2);
    });
  });

  it("submits the user-confirmed Brief with bounded values", async () => {
    const user = userEvent.setup();
    researchMocks.listResearchRuns.mockResolvedValueOnce([]).mockResolvedValueOnce([researchRun]);
    researchMocks.startResearch.mockResolvedValue({
      agent_run_id: agentRunId,
      conversation_id: "99999999-9999-4999-8999-999999999999",
      created: true,
      job_id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
      research_run_id: researchRunId,
      turn_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    });

    render(
      <ResearchWorkspace
        canManage
        focusedResearchRunId={null}
        industries={[industry]}
        onOpenAgent={vi.fn()}
        onOpenEvidence={vi.fn()}
        onSelectIndustry={vi.fn()}
        selectedIndustryId={industryId}
        workspaceId={workspaceId}
      />,
    );

    await screen.findByText("尚无 Research Run。");
    await user.type(screen.getByLabelText("Research 原始问题"), "Find a transport update.");
    await user.type(screen.getByLabelText("Research 已确认范围"), "Public news");
    await user.type(screen.getByLabelText("Research 排除项"), "Investment advice");
    await user.click(screen.getByRole("button", { name: "确认 Brief 并开始" }));

    await waitFor(() => {
      expect(researchMocks.startResearch).toHaveBeenCalledTimes(1);
    });
    expect(researchMocks.startResearch.mock.calls[0]?.[0]).toBe(workspaceId);
    expect(researchMocks.startResearch.mock.calls[0]?.[1]).toMatchObject({
      confirmed_scope: ["Public news"],
      exclusions: ["Investment advice"],
      industry_id: industryId,
      max_steps: 20,
      original_question: "Find a transport update.",
    });
    expect(researchMocks.startResearch.mock.calls[0]?.[2]).toEqual(expect.any(String));
  });
});
