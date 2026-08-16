import { describe, expect, it } from "vitest";

import type { ConversationMessage } from "./chat-api";
import {
  hasPersistedAssistantMessage,
  newestUnfinishedRun,
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

describe("chat workbench message model", () => {
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
});
