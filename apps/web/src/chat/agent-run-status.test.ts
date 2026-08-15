import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentTrace } from "./chat-api";

const mocks = vi.hoisted(() => ({
  getAgentTrace:
    vi.fn<(workspaceId: string, runId: string, signal?: AbortSignal) => Promise<AgentTrace>>(),
}));

vi.mock("./chat-api", () => ({ getAgentTrace: mocks.getAgentTrace }));

import { confirmedAgentRunTerminal, pollAgentRunTerminal } from "./agent-run-status";

const workspaceId = "22222222-2222-4222-8222-222222222222";
const conversationId = "33333333-3333-4333-8333-333333333333";
const runId = "66666666-6666-4666-8666-666666666666";
const turnId = "44444444-4444-4444-8444-444444444444";

function trace(status: "cancelled" | "completed" | "failed" | "running"): AgentTrace {
  const terminal = status !== "running";
  const stopReason =
    status === "completed"
      ? "final"
      : status === "cancelled"
        ? "cancelled"
        : status === "failed"
          ? "runtime_error"
          : null;
  const terminalEventType: AgentTrace["events"][number]["event_type"] =
    status === "completed"
      ? "agent.run.completed"
      : status === "cancelled"
        ? "agent.run.cancelled"
        : "agent.run.failed";
  return {
    context_manifests: [],
    events: [
      {
        details: { runtime_version: "runtime-v0" },
        event_type: "agent.run.queued",
        occurred_at: "2026-08-14T06:09:59Z",
        schema_version: 1,
        sequence: 1,
      },
      ...(terminal
        ? [
            {
              details: { stop_reason: stopReason ?? "runtime_error" },
              event_type: terminalEventType,
              occurred_at: "2026-08-14T06:10:01Z",
              schema_version: 1 as const,
              sequence: 2,
            },
          ]
        : []),
    ],
    run: {
      conversation_id: conversationId,
      created_at: "2026-08-14T06:09:59Z",
      deadline: "2026-08-14T06:11:00Z",
      event_count: terminal ? 2 : 1,
      event_stream_id: "99999999-9999-4999-8999-999999999999",
      harness_version: "runtime-v0",
      max_cost_micro_usd: 1_000,
      max_steps: 2,
      max_total_tokens: 3_000,
      run_id: runId,
      run_type: "direct_answer",
      runtime_version: "runtime-v0",
      schema_version: 1,
      started_at: "2026-08-14T06:10:00Z",
      state_revision: terminal ? 2 : 1,
      status,
      step_count: 0,
      stop_reason: stopReason,
      terminal_at: terminal ? "2026-08-14T06:10:01Z" : null,
      trace_id: "0123456789abcdef0123456789abcdef",
      turn_id: turnId,
      usage: {
        cached_input_tokens: 0,
        cost_micro_usd: 0,
        input_tokens: 0,
        output_tokens: 0,
      },
      workspace_id: workspaceId,
    },
    schema_version: 1,
    steps: [],
  };
}

beforeEach(() => {
  mocks.getAgentTrace.mockReset();
});

describe("Agent Run terminal status reconciliation", () => {
  it("polls without a real delay and returns only a committed terminal Trace", async () => {
    mocks.getAgentTrace
      .mockResolvedValueOnce(trace("running"))
      .mockResolvedValueOnce(trace("cancelled"));

    const result = await pollAgentRunTerminal(workspaceId, runId, {
      attemptDelaysMs: [0, 0],
    });

    expect(result?.status).toBe("cancelled");
    expect(result?.trace.run.stop_reason).toBe("cancelled");
    expect(mocks.getAgentTrace).toHaveBeenCalledTimes(2);
  });

  it("returns pending after the bounded schedule instead of fabricating cancellation", async () => {
    mocks.getAgentTrace.mockResolvedValue(trace("running"));

    await expect(
      pollAgentRunTerminal(workspaceId, runId, { attemptDelaysMs: [0, 0] }),
    ).resolves.toBeNull();
    expect(mocks.getAgentTrace).toHaveBeenCalledTimes(2);
  });

  it("preserves a completion that won the cancellation race", async () => {
    mocks.getAgentTrace.mockResolvedValue(trace("completed"));

    const result = await pollAgentRunTerminal(workspaceId, runId, {
      attemptDelaysMs: [0],
    });

    expect(result?.status).toBe("completed");
  });

  it("rejects an inconsistent terminal summary and stops immediately when aborted", async () => {
    const inconsistent = trace("cancelled");
    inconsistent.run.terminal_at = null;
    expect(confirmedAgentRunTerminal(inconsistent)).toBeNull();

    const controller = new AbortController();
    mocks.getAgentTrace.mockImplementation(() => {
      controller.abort();
      return Promise.resolve(trace("running"));
    });

    await expect(
      pollAgentRunTerminal(workspaceId, runId, {
        attemptDelaysMs: [0, 60_000],
        signal: controller.signal,
      }),
    ).resolves.toBeNull();
    expect(mocks.getAgentTrace).toHaveBeenCalledOnce();
  });
});
