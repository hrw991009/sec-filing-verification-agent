import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentTrace } from "../chat/chat-api";
import type { Industry } from "../industry/industry-api";
import type { ResearchDurability, ResearchRun } from "./research-api";

const workspaceId = "11111111-1111-4111-8111-111111111111";
const researchRunId = "22222222-2222-4222-8222-222222222222";
const agentRunId = "33333333-3333-4333-8333-333333333333";
const industryId = "5ae94c40-4441-5e6f-b4cb-0679e8a92f9e";
const knowledgeBaseId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";

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
    approval_reason: null,
    confirmed_at: "2026-08-22T08:00:00Z",
    confirmed_by_user_id: "44444444-4444-4444-8444-444444444444",
    confirmed_scope: ["Public smart transport news"],
    exclusions: ["Investment advice"],
    financial_scope: null,
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
  graph_version: "research-l4-graph-v1",
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
const traceStep = trace.steps[0];
if (traceStep === undefined) throw new Error("Research trace fixture is incomplete");

const durability: ResearchDurability = {
  approvals: [],
  checkpoints: [
    {
      checkpoint_id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
      next_node: "research_loop",
      node: "plan",
      revision: 2,
      run_state_revision: 7,
      saved_at: "2026-08-22T08:00:02Z",
      state_diff: { approval_status: "not_required" },
    },
  ],
  duplicate_side_effect_count: 0,
};

const researchMocks = vi.hoisted(() => ({
  decideResearchApproval: vi.fn(),
  getResearchDurability: vi.fn(),
  getResearchRun: vi.fn(),
  listResearchRuns: vi.fn(),
  resumeResearch: vi.fn(),
  startResearch: vi.fn(),
}));
const chatMocks = vi.hoisted(() => ({ cancelRun: vi.fn(), getAgentTrace: vi.fn() }));
const evidenceMocks = vi.hoisted(() => ({ listResearchClaims: vi.fn() }));
const knowledgeMocks = vi.hoisted(() => ({ listKnowledgeBases: vi.fn() }));

vi.mock("./research-api", () => researchMocks);
vi.mock("../chat/chat-api", () => chatMocks);
vi.mock("../evidence/evidence-api", () => evidenceMocks);
vi.mock("../knowledge/knowledge-api", () => knowledgeMocks);

import { ResearchWorkspace } from "./ResearchWorkspace";

describe("ResearchWorkspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    researchMocks.listResearchRuns.mockResolvedValue([researchRun]);
    researchMocks.getResearchRun.mockResolvedValue(researchRun);
    researchMocks.getResearchDurability.mockResolvedValue(durability);
    knowledgeMocks.listKnowledgeBases.mockResolvedValue([
      {
        created_at: "2026-08-25T08:00:00Z",
        description: "SEC fixture filings",
        document_count: 1,
        id: knowledgeBaseId,
        name: "SEC Filings",
        revision: 1,
        updated_at: "2026-08-25T08:00:00Z",
        workspace_id: workspaceId,
      },
    ]);
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

  it("shows context exclusions, calculation reconciliation, diff and citation drilldown", async () => {
    const onOpenEvidence = vi.fn();
    chatMocks.getAgentTrace.mockResolvedValue({
      ...trace,
      context_manifests: [
        {
          budget: {
            allowed_output_tokens: 256,
            estimated_input_tokens: 100,
            max_input_tokens: 4096,
            run_max_total_tokens: 12000,
            tokens_used_before_step: 20,
            unreserved_run_tokens: 11624,
          },
          compiler_version: "financial-context-v1",
          created_at: "2026-08-22T08:00:02Z",
          manifest_id: "14141414-1414-4414-8414-141414141414",
          prompt_version: "sec-l4-prompt-v1",
          run_id: agentRunId,
          runtime_projection_version: "runtime-context-v1",
          schema_version: 1,
          sources: [
            {
              decision_reason: "excluded_scope_mismatch",
              estimated_token_count: 12,
              feedback_score: null,
              included: false,
              message_role: null,
              ordinal: 1,
              relevance_score: null,
              source_id: "future-fact",
              source_identity: null,
              source_kind: "tool_observation",
              source_revision_id: null,
              source_scope: null,
              source_sha256: "a".repeat(64),
              source_version: "sec-xbrl-v1",
            },
          ],
          step_id: traceStep.step_id,
          token_counter_version: "utf8-upper-bound-v1", // gitleaks:allow -- version, not a credential
          workspace_id: workspaceId,
        },
      ],
      events: [
        ...trace.events,
        {
          details: {
            call_id: "15151515-1515-4515-8515-151515151515",
            requested_tool_name: "sec.diff_filings",
          },
          event_type: "agent.tool.requested",
          occurred_at: "2026-08-22T08:00:03Z",
          schema_version: 1,
          sequence: 32,
        },
        {
          details: { call_id: "15151515-1515-4515-8515-151515151515" },
          event_type: "agent.tool.completed",
          occurred_at: "2026-08-22T08:00:04Z",
          schema_version: 1,
          sequence: 33,
        },
      ],
    });
    evidenceMocks.listResearchClaims.mockResolvedValue([
      {
        confidence: 1,
        conflict: false,
        coverage: 1,
        created_at: researchRun.created_at,
        id: "16161616-1616-4616-8616-161616161616",
        relations: [
          {
            evidence: {
              id: "17171717-1717-4717-8717-171717171717",
              locator: {
                formula: "(120 - 100) / 100 * 100",
                input_evidence_refs: [
                  "18181818-1818-4818-8818-181818181818",
                  "19191919-1919-4919-8919-191919191919",
                ],
                locator_type: "financial_calculation_v1",
                operator: "percent_change",
                reconciliation_status: "consistent",
                result: "20.00",
                scale: 0,
                unit: "PERCENT",
              },
              status: "active",
              title: "Financial calculation: percent_change",
            },
            relation: "supports",
          },
          {
            evidence: {
              id: "20202020-2020-4020-8020-202020202020",
              locator: { locator_type: "sec_xbrl_fact_v1" },
              status: "active",
              title: "us-gaap:Revenue",
            },
            relation: "supports",
          },
        ],
        research_run_id: researchRunId,
        revision: 1,
        statement: "Revenue increased by 20 percent.",
        updated_at: researchRun.updated_at,
        verification_status: "supported",
        workspace_id: workspaceId,
      },
    ]);

    render(
      <ResearchWorkspace
        canManage
        focusedResearchRunId={researchRunId}
        industries={[industry]}
        onOpenAgent={vi.fn()}
        onOpenEvidence={onOpenEvidence}
        onSelectIndustry={vi.fn()}
        selectedIndustryId={industryId}
        workspaceId={workspaceId}
      />,
    );

    expect(await screen.findByText("excluded_scope_mismatch")).toBeInTheDocument();
    expect(screen.getByText("(120 - 100) / 100 * 100")).toBeInTheDocument();
    expect(screen.getByText("consistent")).toBeInTheDocument();
    expect(screen.getByText("sec.diff_filings@v1")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "反查 Calculation Citation" }));
    expect(onOpenEvidence).toHaveBeenCalledWith("17171717-1717-4717-8717-171717171717");
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
      mode: "web",
      original_question: "Find a transport update.",
    });
    expect(researchMocks.startResearch.mock.calls[0]?.[2]).toEqual(expect.any(String));
  });

  it("submits a pinned SEC fixture scope and Knowledge Base", async () => {
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
    await user.click(screen.getByRole("button", { name: "SEC Filing" }));
    expect(await screen.findByLabelText("Research Knowledge Base")).toHaveValue(knowledgeBaseId);
    await user.click(screen.getByLabelText("公司或期间存在歧义，计划后暂停确认"));
    await user.type(
      screen.getByLabelText("Research 原始问题"),
      "Calculate Apple net sales change.",
    );
    await user.type(screen.getByLabelText("Research 已确认范围"), "Apple 2023 Form 10-K");
    await user.click(screen.getByRole("button", { name: "确认 Brief 并开始" }));

    await waitFor(() => {
      expect(researchMocks.startResearch).toHaveBeenCalledTimes(1);
    });
    expect(researchMocks.startResearch.mock.calls[0]?.[1]).toMatchObject({
      approval_reason: "company_or_period_ambiguity",
      financial_scope: {
        accession: "0000320193-23-000106",
        cik: "0000320193",
        form: "10-K",
        report_period: "2023-09-30",
        scale: 6,
        schema_version: 1,
        unit: "USD",
      },
      knowledge_base_ids: [knowledgeBaseId],
      mode: "local",
    });
    expect(researchMocks.startResearch.mock.calls[0]?.[1]).not.toHaveProperty("industry_id");
  });

  it("persists an approval decision before creating the resume job", async () => {
    const user = userEvent.setup();
    const resumeProof = "r".repeat(43);
    const checkpoint = durability.checkpoints[0];
    if (checkpoint === undefined) throw new Error("Test durability fixture is incomplete");
    const pausedRun: ResearchRun = {
      ...researchRun,
      agent_status: "paused",
      brief: {
        ...researchRun.brief,
        approval_reason: "company_or_period_ambiguity",
      },
      current_node: "plan",
      draft: null,
      status: "paused",
      stop_reason: null,
    };
    const pendingApproval = {
      approval_request_id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
      checkpoint_id: checkpoint.checkpoint_id,
      checkpoint_revision: 2,
      created_at: "2026-08-22T08:00:02Z",
      decided_at: null,
      decided_by_user_id: null,
      expires_at: "2026-08-22T08:15:02Z",
      reason: "company_or_period_ambiguity" as const,
      requested_by_user_id: researchRun.owner_user_id,
      resume_claimed: false,
      resume_job_id: null,
      resume_token: resumeProof,
      resumed_at: null,
      run_id: agentRunId,
      status: "pending" as const,
    };
    researchMocks.listResearchRuns.mockResolvedValue([pausedRun]);
    researchMocks.getResearchRun.mockResolvedValue(pausedRun);
    researchMocks.getResearchDurability.mockResolvedValue({
      ...durability,
      approvals: [pendingApproval],
    });
    researchMocks.decideResearchApproval.mockResolvedValue({
      ...pendingApproval,
      decided_at: "2026-08-22T08:01:00Z",
      decided_by_user_id: researchRun.owner_user_id,
      status: "allowed",
    });
    researchMocks.resumeResearch.mockResolvedValue({
      agent_run_id: agentRunId,
      created: true,
      job_id: "ffffffff-ffff-4fff-8fff-ffffffffffff",
    });

    render(
      <ResearchWorkspace
        canManage
        focusedResearchRunId={researchRunId}
        industries={[industry]}
        onOpenAgent={vi.fn()}
        onOpenEvidence={vi.fn()}
        onSelectIndustry={vi.fn()}
        selectedIndustryId={industryId}
        workspaceId={workspaceId}
      />,
    );

    await user.click(await screen.findByRole("button", { name: "允许并继续" }));

    await waitFor(() => {
      expect(researchMocks.resumeResearch).toHaveBeenCalledTimes(1);
    });
    expect(researchMocks.decideResearchApproval).toHaveBeenCalledWith(workspaceId, researchRunId, {
      approval_request_id: pendingApproval.approval_request_id,
      checkpoint_revision: 2,
      outcome: "allow",
    });
    expect(researchMocks.resumeResearch).toHaveBeenCalledWith(workspaceId, researchRunId, {
      approval_request_id: pendingApproval.approval_request_id,
      checkpoint_revision: 2,
      resume_token: resumeProof,
    });
    expect(researchMocks.decideResearchApproval.mock.invocationCallOrder[0]).toBeLessThan(
      researchMocks.resumeResearch.mock.invocationCallOrder[0] ?? -1,
    );
  });
});
