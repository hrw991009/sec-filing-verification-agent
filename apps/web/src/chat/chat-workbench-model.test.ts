import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiProblem } from "../api/api";
import type { AgentStreamEvent, ConversationMessage } from "./chat-api";
import {
  attachmentKind,
  eventNames,
  formatBytes,
  formatCost,
  hasPersistedAssistantMessage,
  idempotencyKey,
  isTerminalEvent,
  latestUserMessage,
  newestUnfinishedRun,
  payloadString,
  publicError,
  relativeTime,
  runFailureMessage,
  sourceNames,
  terminalStatus,
  userMessageForRun,
} from "./chat-workbench-model";

const runId = "55555555-5555-4555-8555-555555555555";

function message(
  role: ConversationMessage["role"],
  status: ConversationMessage["status"],
): ConversationMessage {
  return {
    agent_run_id: runId,
    attachments: [],
    content_markdown: role === "user" ? "问题" : "已提交的回答片段",
    created_at: "2026-08-15T06:00:00Z",
    id:
      role === "user"
        ? "66666666-6666-4666-8666-666666666666"
        : "77777777-7777-4777-8777-777777777777",
    industry_id: null,
    knowledge_base_ids: [],
    role,
    search_mode: "none",
    status,
    turn_id: "88888888-8888-4888-8888-888888888888",
  };
}

function streamEvent(
  type: AgentStreamEvent["type"],
  payload: AgentStreamEvent["payload"] = {},
): AgentStreamEvent {
  return {
    occurred_at: "2026-08-15T06:00:00Z",
    payload,
    schema_version: 1,
    sequence: 1,
    stream_id: "44444444-4444-4444-8444-444444444444",
    trace_id: "0123456789abcdef0123456789abcdef",
    type,
  };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("chat workbench message model", () => {
  it("labels Tool Trace entries for the Chinese workbench", () => {
    expect(sourceNames.tool_observation).toBe("工具观察结果");
    expect(eventNames["agent.tool.approval_required"]).toBe("工具调用等待批准");
    expect(eventNames["agent.tool.completed"]).toBe("工具调用完成");
    expect(eventNames["agent.research.node_completed"]).toBe("Research 节点完成");
  });

  it("resumes a Run when only its committed user message exists", () => {
    expect(newestUnfinishedRun([message("user", "committed")])).toBe(runId);
  });

  it.each(["final", "partial"] as const)(
    "does not resume a Run with a persisted %s assistant message",
    (status) => {
      const messages = [message("user", "committed"), message("assistant", status)];

      expect(newestUnfinishedRun(messages)).toBeNull();
      expect(hasPersistedAssistantMessage(messages, runId)).toBe(true);
    },
  );

  it("finds the user question that belongs to a partial answer's Run", () => {
    const anotherRun = "99999999-9999-4999-8999-999999999999";
    const newerQuestion = {
      ...message("user", "committed"),
      agent_run_id: anotherRun,
      content_markdown: "另一个 Run 的问题",
      id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    };

    expect(
      userMessageForRun([message("user", "committed"), newerQuestion], runId)?.content_markdown,
    ).toBe("问题");
  });

  it("formats public errors without exposing unknown objects", () => {
    expect(publicError(new ApiProblem(403, { detail: "无权访问", trace_id: "trace-1" }))).toBe(
      "无权访问（追踪号 trace-1）",
    );
    expect(publicError(new ApiProblem(400, { detail: "请求错误" }))).toBe("请求错误");
    expect(publicError(new Error("网络中断"))).toBe("网络中断");
    expect(publicError(new Error(""))).toBe("服务暂时不可用，请稍后重试。");
    expect(publicError({ message: "untrusted" })).toBe("服务暂时不可用，请稍后重试。");
  });

  it("formats relative time across every display boundary", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-22T12:00:00Z"));

    expect(relativeTime("invalid")).toBe("刚刚");
    expect(relativeTime("2026-08-22T12:01:00Z")).toBe("刚刚");
    expect(relativeTime("2026-08-22T11:59:40Z")).toBe("刚刚");
    expect(relativeTime("2026-08-22T11:30:00Z")).toBe("30 分钟前");
    expect(relativeTime("2026-08-22T09:00:00Z")).toBe("3 小时前");
    expect(relativeTime("2026-08-19T12:00:00Z")).toBe("3 天前");
    expect(relativeTime("2026-08-01T12:00:00Z")).not.toBe("刚刚");
  });

  it("formats byte, cost, payload, key, and attachment helpers", () => {
    expect(formatBytes(999)).toBe("999 B");
    expect(formatBytes(1_500)).toBe("1.5 KB");
    expect(formatBytes(2_500_000)).toBe("2.5 MB");
    expect(formatCost(0)).toBe("$0");
    expect(formatCost(80)).toBe("$0.000080");
    expect(payloadString(streamEvent("agent.run.started", { value: "ok" }), "value")).toBe("ok");
    expect(payloadString(streamEvent("agent.run.started", { value: 1 }), "value")).toBeNull();
    expect(idempotencyKey()).toMatch(/^web-[0-9a-f-]+$/u);
    expect(attachmentKind(new File(["x"], "scan.bin", { type: "image/png" }))).toBe("image");
    expect(
      attachmentKind(new File(["x"], "photo.JPEG", { type: "application/octet-stream" })),
    ).toBe("image");
    expect(attachmentKind(new File(["x"], "brief.pdf", { type: "application/pdf" }))).toBe(
      "document",
    );
  });

  it("derives terminal status from business events and snapshots", () => {
    expect(terminalStatus(streamEvent("agent.run.completed"))).toBe("completed");
    expect(terminalStatus(streamEvent("agent.run.cancelled"))).toBe("cancelled");
    expect(terminalStatus(streamEvent("agent.run.failed"))).toBe("failed");
    expect(
      terminalStatus(streamEvent("stream.snapshot", { status: "failed", terminal: true })),
    ).toBe("failed");
    expect(
      terminalStatus(streamEvent("stream.snapshot", { status: "running", terminal: true })),
    ).toBeNull();
    expect(terminalStatus(streamEvent("stream.snapshot", { status: "completed" }))).toBeNull();
    expect(isTerminalEvent(streamEvent("agent.run.started"))).toBe(false);
    expect(isTerminalEvent(streamEvent("agent.run.completed"))).toBe(true);
  });

  it.each([
    ["provider_timeout", "模型响应超时"],
    ["provider_rate_limited", "模型服务当前繁忙"],
    ["cancelled", "本次回答已停止"],
    ["incomplete_provider_response", "模型连接在完成前中断"],
    ["tool_error", "本次回答未完成（tool_error）"],
    [null, "本次回答未完成。"],
  ] as const)("explains the %s failure reason", (reason, expected) => {
    expect(runFailureMessage(reason)).toContain(expected);
  });

  it("handles empty and mismatched message histories", () => {
    const otherRun = "99999999-9999-4999-8999-999999999999";
    const assistant = message("assistant", "final");
    const otherUser = { ...message("user", "committed"), agent_run_id: otherRun };

    expect(newestUnfinishedRun([])).toBeNull();
    expect(hasPersistedAssistantMessage([assistant], otherRun)).toBe(false);
    expect(userMessageForRun([assistant], runId)).toBeNull();
    expect(latestUserMessage([assistant])).toBeNull();
    expect(latestUserMessage([message("user", "committed"), otherUser])?.agent_run_id).toBe(
      otherRun,
    );
  });
});
