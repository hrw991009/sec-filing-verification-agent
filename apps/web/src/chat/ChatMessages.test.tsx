import { createRef } from "react";

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ConversationMessage } from "./chat-api";
import type { ActiveRun } from "./chat-workbench-model";
import { ChatMessages } from "./ChatMessages";

const runId = "55555555-5555-4555-8555-555555555555";

function message(
  role: ConversationMessage["role"],
  status: ConversationMessage["status"],
): ConversationMessage {
  return {
    agent_run_id: runId,
    attachments: [],
    content_markdown: role === "user" ? "解释这个问题" : "已经收到的 **回答片段**",
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

const failedRun: ActiveRun = {
  cancelRequested: false,
  connection: "closed",
  conversationId: "99999999-9999-4999-8999-999999999999",
  error: "模型连接中断。",
  events: [],
  partialMarkdown: "已经收到的 **回答片段**",
  runId,
  status: "failed",
};

function renderMessages(
  messages: readonly ConversationMessage[],
  activeRun: ActiveRun | null,
  onOpenTrace = vi.fn<(selectedRunId: string) => void>(),
  onRetryLastQuestion = vi.fn<(question: string) => void>(),
  canCompose = true,
): {
  readonly onOpenTrace: ReturnType<typeof vi.fn<(selectedRunId: string) => void>>;
  readonly onRetryLastQuestion: ReturnType<typeof vi.fn<(question: string) => void>>;
} {
  render(
    <ChatMessages
      activeRun={activeRun}
      canCompose={canCompose}
      messages={messages}
      messagesError={null}
      messagesState="ready"
      onDownload={vi.fn()}
      onOpenTrace={onOpenTrace}
      onRetryLastQuestion={onRetryLastQuestion}
      onRetryMessageLoad={vi.fn()}
      onSelectPrompt={vi.fn()}
      threadEndRef={createRef<HTMLDivElement>()}
    />,
  );
  return { onOpenTrace, onRetryLastQuestion };
}

describe("ChatMessages", () => {
  it("explains a persisted partial answer and does not duplicate its streaming bubble", async () => {
    const { onOpenTrace, onRetryLastQuestion } = renderMessages(
      [message("user", "committed"), message("assistant", "partial")],
      failedRun,
    );

    expect(screen.getByText("回答片段")).toBeVisible();
    expect(
      screen.getByText("这次回答未完整结束，已保留已提交片段；查看运行轨迹了解原因。"),
    ).toBeVisible();
    expect(screen.getAllByText("回答片段")).toHaveLength(1);
    expect(screen.queryByText("模型连接中断。")).not.toBeInTheDocument();

    const partialCard = screen.getByText("部分回答").closest("article");
    if (partialCard === null) throw new Error("部分回答不在消息区域内");
    await userEvent
      .setup()
      .click(within(partialCard).getByRole("button", { name: "查看运行轨迹" }));
    expect(onOpenTrace).toHaveBeenCalledWith(runId);

    await userEvent.setup().click(within(partialCard).getByRole("button", { name: "重新提问" }));
    expect(onRetryLastQuestion).toHaveBeenCalledWith("解释这个问题");
  });

  it("opens the Run Trace from a user message even when no assistant delta exists", async () => {
    const { onOpenTrace } = renderMessages([message("user", "committed")], null);

    await userEvent.setup().click(screen.getByRole("button", { name: "查看本轮运行轨迹" }));

    expect(onOpenTrace).toHaveBeenCalledWith(runId);
  });

  it.each([
    {
      activeRun: {
        ...failedRun,
        runId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        status: "running" as const,
      },
      canCompose: true,
    },
    { activeRun: null, canCompose: false },
  ])("disables partial retry while the workbench is busy", ({ activeRun, canCompose }) => {
    renderMessages(
      [message("user", "committed"), message("assistant", "partial")],
      activeRun,
      undefined,
      undefined,
      canCompose,
    );

    expect(screen.getByRole("button", { name: "重新提问" })).toBeDisabled();
  });

  it("disables partial retry when replaying it would silently omit an attachment", () => {
    const userMessage = {
      ...message("user", "committed"),
      attachments: [
        {
          actual_size: 42,
          detected_media_type: "text/plain" as const,
          file_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          height: null,
          kind: "text" as const,
          original_name: "evidence.txt",
          status: "ready" as const,
          width: null,
        },
      ],
    };

    renderMessages([userMessage, message("assistant", "partial")], null);

    expect(screen.getByRole("button", { name: "重新提问" })).toBeDisabled();
  });
});
