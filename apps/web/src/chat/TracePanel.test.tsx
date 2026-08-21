import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AgentTrace } from "./chat-api";
import { TracePanel } from "./TracePanel";

const workspaceId = "11111111-1111-4111-8111-111111111111";
const runId = "22222222-2222-4222-8222-222222222222";
const stepId = "33333333-3333-4333-8333-333333333333";

const trace = {
  context_manifests: [
    {
      budget: {
        allowed_output_tokens: 512,
        estimated_input_tokens: 120,
        max_input_tokens: 4096,
        run_max_total_tokens: 8192,
        tokens_used_before_step: 60,
        unreserved_run_tokens: 7520,
      },
      compiler_version: "context-v1",
      created_at: "2026-08-17T08:00:01Z",
      manifest_id: "44444444-4444-4444-8444-444444444444",
      prompt_version: "conversation-web-l2-prompt-v1",
      run_id: runId,
      runtime_projection_version: "runtime-context-projection-v1",
      schema_version: 1,
      sources: [
        {
          decision_reason: "included",
          estimated_token_count: 24,
          feedback_score: null,
          included: true,
          message_role: "user",
          ordinal: 5,
          relevance_score: null,
          source_id: "55555555-5555-4555-8555-555555555555",
          source_kind: "tool_observation",
          source_revision_id: null,
          source_scope: null,
          source_sha256: "a".repeat(64),
          source_version: "tool-observation-v1",
        },
      ],
      step_id: stepId,
      token_counter_version: "utf8-upper-bound-v1", // gitleaks:allow -- version, not a credential
      workspace_id: workspaceId,
    },
  ],
  events: [
    {
      details: {
        call_id: "66666666-6666-4666-8666-666666666666",
        raw_arguments: "secret=must-not-render",
        requested_tool_name: "industry.web_search",
        requested_tool_version: "v1",
      },
      event_type: "agent.tool.requested",
      occurred_at: "2026-08-17T08:00:02Z",
      schema_version: 1,
      sequence: 4,
    },
    {
      details: {
        call_id: "66666666-6666-4666-8666-666666666666",
        cost_micro_usd: 0,
        duration_ms: 12,
        observation_envelope_sha256: "a".repeat(64),
        observation_id: "55555555-5555-4555-8555-555555555555",
      },
      event_type: "agent.tool.completed",
      occurred_at: "2026-08-17T08:00:03Z",
      schema_version: 1,
      sequence: 6,
    },
  ],
  run: {
    conversation_id: "77777777-7777-4777-8777-777777777777",
    created_at: "2026-08-17T08:00:00Z",
    deadline: "2026-08-17T08:05:00Z",
    event_count: 10,
    event_stream_id: "88888888-8888-4888-8888-888888888888",
    harness_version: "harness-v1",
    max_cost_micro_usd: 250000,
    max_steps: 8,
    max_total_tokens: 8192,
    run_id: runId,
    run_type: "tool_loop",
    runtime_version: "tool-l2-runtime-v1",
    schema_version: 1,
    started_at: "2026-08-17T08:00:01Z",
    state_revision: 6,
    status: "completed",
    step_count: 4,
    stop_reason: "final",
    terminal_at: "2026-08-17T08:00:04Z",
    trace_id: "day3-tool-inspector-trace",
    turn_id: "99999999-9999-4999-8999-999999999999",
    usage: {
      cached_input_tokens: 0,
      cost_micro_usd: 80,
      input_tokens: 40,
      output_tokens: 20,
    },
    workspace_id: workspaceId,
  },
  schema_version: 1,
  steps: [],
} satisfies AgentTrace;

describe("Tool Inspector", () => {
  it("shows allowlisted Tool facts and Observation correlation without raw arguments", () => {
    render(
      <TracePanel
        activeRun={null}
        events={trace.events}
        onClose={vi.fn()}
        onOpenMemory={vi.fn()}
        onRetry={undefined}
        trace={trace}
        traceError={null}
        traceState="ready"
      />,
    );

    const inspector = screen.getByRole("region", { name: "Tool Inspector" });
    expect(within(inspector).getByText("industry.web_search")).toBeVisible();
    expect(within(inspector).getByText("模型可见信封摘要")).toBeVisible();
    expect(within(inspector).queryByText(/must-not-render/u)).not.toBeInTheDocument();
    expect(screen.getByText(/摘要 aaaaaaaaaaaa/u)).toBeVisible();
  });
});
