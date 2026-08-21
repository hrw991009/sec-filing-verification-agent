import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { CurrentUser } from "../auth/auth-context";
import type {
  AgentStreamConnectionState,
  AgentStreamEvent,
  AgentTrace,
  ConversationMessage,
  MemoryCandidate,
  MemoryResolution,
} from "./chat-api";
import type { ConfirmedAgentRunTerminal } from "./agent-run-status";

interface CapturedStreamOptions {
  readonly onConnectionState: (state: AgentStreamConnectionState) => void;
  readonly onEvent: (event: AgentStreamEvent) => void | Promise<void>;
  readonly runId: string;
  readonly signal?: AbortSignal;
  readonly workspaceId: string;
}

const mocks = vi.hoisted(() => ({
  cancelRun: vi.fn<(workspaceId: string, runId: string) => Promise<void>>(),
  confirmMemoryCandidate: vi.fn<() => Promise<unknown>>(),
  createMemoryCandidate: vi.fn<() => Promise<unknown>>(),
  deleteConversation: vi.fn<() => Promise<void>>(),
  deleteFile: vi.fn<() => Promise<unknown>>(),
  followAgentRunEvents: vi.fn<(options: CapturedStreamOptions) => Promise<number>>(),
  getAgentTrace: vi.fn<(workspaceId: string, runId: string) => Promise<AgentTrace>>(),
  getDownloadUrl: vi.fn<() => Promise<unknown>>(),
  getMemory: vi.fn<() => Promise<unknown>>(),
  listConversations: vi.fn<() => Promise<unknown>>(),
  listMemories: vi.fn<() => Promise<unknown>>(),
  listMemoryCandidates: vi.fn<() => Promise<unknown>>(),
  listMessages: vi.fn<() => Promise<unknown>>(),
  pollAgentRunTerminal:
    vi.fn<
      (
        workspaceId: string,
        runId: string,
        options?: { readonly signal?: AbortSignal },
      ) => Promise<ConfirmedAgentRunTerminal | null>
    >(),
  renameConversation: vi.fn<() => Promise<unknown>>(),
  rejectMemoryCandidate: vi.fn<() => Promise<unknown>>(),
  startTurn: vi.fn<() => Promise<unknown>>(),
  uploadFile: vi.fn<() => Promise<unknown>>(),
}));

const industryMocks = vi.hoisted(() => ({
  getIndustryPreference: vi.fn<() => Promise<unknown>>(),
  listIndustries: vi.fn<() => Promise<unknown>>(),
}));

vi.mock("./chat-api", () => mocks);
vi.mock("../industry/industry-api", () => industryMocks);
vi.mock("./agent-run-status", () => ({
  pollAgentRunTerminal: mocks.pollAgentRunTerminal,
}));

import { ChatWorkbench } from "./ChatWorkbench";

const workspaceId = "22222222-2222-4222-8222-222222222222";
const conversationId = "33333333-3333-4333-8333-333333333333";
const turnId = "44444444-4444-4444-8444-444444444444";
const historicalRunId = "55555555-5555-4555-8555-555555555555";
const activeRunId = "66666666-6666-4666-8666-666666666666";
const secondWorkspaceId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const historicalUserMessageId = "77777777-7777-4777-8777-777777777777";

const currentUser: CurrentUser = {
  user: {
    email: "learner@example.com",
    id: "11111111-1111-4111-8111-111111111111",
  },
  workspaces: [
    {
      id: workspaceId,
      name: "行业研究 Workspace",
      role: "owner",
    },
  ],
};

const multiWorkspaceUser: CurrentUser = {
  ...currentUser,
  workspaces: [
    ...currentUser.workspaces,
    {
      id: secondWorkspaceId,
      name: "第二个 Workspace",
      role: "owner",
    },
  ],
};

const conversation = {
  created_at: "2026-08-14T05:00:00Z",
  id: conversationId,
  title: "新能源汽车季度分析",
  updated_at: "2026-08-14T06:00:00Z",
};

function message(
  overrides: Partial<ConversationMessage> &
    Pick<ConversationMessage, "content_markdown" | "id" | "role" | "status">,
): ConversationMessage {
  return {
    agent_run_id: historicalRunId,
    attachments: [],
    created_at: "2026-08-14T05:30:00Z",
    industry_id: null,
    knowledge_base_ids: [],
    search_mode: "none",
    turn_id: turnId,
    ...overrides,
  };
}

const historicalMessages: ConversationMessage[] = [
  message({
    content_markdown: "分析这份材料",
    id: historicalUserMessageId,
    role: "user",
    search_mode: "web",
    status: "committed",
  }),
  message({
    content_markdown:
      "## 安全答案\n[安全链接](https://example.com/report)\n[危险链接](javascript:boom)\n<script>alert('x')</script>",
    id: "88888888-8888-4888-8888-888888888888",
    role: "assistant",
    status: "final",
  }),
];

const memoryCandidate: MemoryCandidate = {
  confidence: 0.95,
  conversation_id: conversationId,
  created_at: "2026-08-20T08:00:00Z",
  id: "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa",
  policy_decision: "allowed",
  policy_reason: "user_authored",
  resolved_memory_id: null,
  revision: 1,
  source_message_ids: [historicalUserMessageId],
  status: "candidate",
  suggested_content: "分析这份材料",
  suggested_expires_at: null,
  suggested_scope: "user",
  updated_at: "2026-08-20T08:00:00Z",
  write_reason: "user_selected_conversation_messages",
};

const memoryResolution: MemoryResolution = {
  action: "create",
  created: true,
  memory: {
    current_revision: {
      content: "默认使用中文回答。",
      created_at: "2026-08-20T08:01:00Z",
      editor_user_id: currentUser.user.id,
      id: "bbbbbbbb-1111-4111-8111-bbbbbbbbbbbb",
      kind: "preference",
      policy_decision: "allowed",
      scope: "user",
      source_message_ids: memoryCandidate.source_message_ids,
      validity: "valid",
      version: 1,
      write_action: "create",
      write_reason: "user_selected_conversation_messages",
    },
    memory: {
      confidence: 0.95,
      created_at: "2026-08-20T08:01:00Z",
      current_revision_id: "bbbbbbbb-1111-4111-8111-bbbbbbbbbbbb",
      current_version: 1,
      expires_at: null,
      id: "cccccccc-1111-4111-8111-cccccccccccc",
      kind: "preference",
      owner_user_id: currentUser.user.id,
      scope: "user",
      source_conversation_id: conversationId,
      status: "confirmed",
      updated_at: "2026-08-20T08:01:00Z",
    },
    revisions: [],
  },
};

function streamEvent(
  sequence: number,
  type: AgentStreamEvent["type"],
  payload: Record<string, string | number | boolean | null> = {},
): AgentStreamEvent {
  return {
    occurred_at: "2026-08-14T06:10:00Z",
    payload,
    schema_version: 1,
    sequence,
    stream_id: "99999999-9999-4999-8999-999999999999",
    trace_id: "0123456789abcdef0123456789abcdef",
    type,
  };
}

function cancelledTrace(): AgentTrace {
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
      {
        details: { stop_reason: "cancelled" },
        event_type: "agent.run.cancelled",
        occurred_at: "2026-08-14T06:10:01Z",
        schema_version: 1,
        sequence: 2,
      },
    ],
    run: {
      conversation_id: conversationId,
      created_at: "2026-08-14T06:09:59Z",
      deadline: "2026-08-14T06:11:00Z",
      event_count: 2,
      event_stream_id: "99999999-9999-4999-8999-999999999999",
      harness_version: "harness-v0",
      max_cost_micro_usd: 1000,
      max_steps: 2,
      max_total_tokens: 3000,
      run_id: activeRunId,
      run_type: "direct_answer",
      runtime_version: "runtime-v0",
      schema_version: 1,
      started_at: "2026-08-14T06:10:00Z",
      state_revision: 2,
      status: "cancelled",
      step_count: 0,
      stop_reason: "cancelled",
      terminal_at: "2026-08-14T06:10:01Z",
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

const scrollIntoViewDescriptor = Object.getOwnPropertyDescriptor(
  Element.prototype,
  "scrollIntoView",
);

beforeAll(() => {
  Object.defineProperty(Element.prototype, "scrollIntoView", {
    configurable: true,
    value: vi.fn(),
  });
});

afterAll(() => {
  if (scrollIntoViewDescriptor === undefined) {
    Reflect.deleteProperty(Element.prototype, "scrollIntoView");
    return;
  }
  Object.defineProperty(Element.prototype, "scrollIntoView", scrollIntoViewDescriptor);
});

beforeEach(() => {
  vi.clearAllMocks();
  mocks.cancelRun.mockResolvedValue();
  mocks.confirmMemoryCandidate.mockRejectedValue(new Error("No Memory confirmation requested"));
  mocks.createMemoryCandidate.mockRejectedValue(new Error("No Memory candidate requested"));
  mocks.deleteConversation.mockResolvedValue();
  mocks.deleteFile.mockResolvedValue(undefined);
  mocks.followAgentRunEvents.mockResolvedValue(0);
  mocks.getAgentTrace.mockResolvedValue(cancelledTrace());
  mocks.getDownloadUrl.mockRejectedValue(new Error("No attachment selected"));
  mocks.getMemory.mockRejectedValue(new Error("No Memory selected"));
  industryMocks.getIndustryPreference.mockResolvedValue({
    industry: {
      code: "smart_transport",
      default_query: "transport",
      default_symbol: "TRANSPORT",
      id: "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa",
      name: "新能源汽车",
    },
    updated_at: "2026-08-17T08:00:00Z",
    user_id: currentUser.user.id,
    workspace_id: workspaceId,
  });
  industryMocks.listIndustries.mockResolvedValue([
    {
      code: "new-energy-vehicles",
      default_query: "transport",
      default_symbol: "TRANSPORT",
      id: "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa",
      name: "新能源汽车",
    },
  ]);
  mocks.listConversations.mockResolvedValue({ conversations: [conversation], next_cursor: null });
  mocks.listMemories.mockResolvedValue([]);
  mocks.listMemoryCandidates.mockResolvedValue([]);
  mocks.listMessages.mockResolvedValue({ messages: historicalMessages, next_cursor: null });
  mocks.pollAgentRunTerminal.mockImplementation(
    () => new Promise<ConfirmedAgentRunTerminal | null>(() => undefined),
  );
  mocks.renameConversation.mockRejectedValue(new Error("No rename requested"));
  mocks.rejectMemoryCandidate.mockRejectedValue(new Error("No Memory rejection requested"));
  mocks.startTurn.mockRejectedValue(new Error("No Turn submitted"));
  mocks.uploadFile.mockRejectedValue(new Error("No file selected"));
});

function renderWorkbench(user: CurrentUser = currentUser): void {
  render(
    <ChatWorkbench
      currentUser={user}
      onLogout={() => Promise.resolve()}
      onOpenSettings={vi.fn()}
    />,
  );
}

async function startActiveRun(
  followImplementation?: (options: CapturedStreamOptions) => Promise<number>,
  workbenchUser: CurrentUser = currentUser,
): Promise<CapturedStreamOptions> {
  const capture: { options: CapturedStreamOptions | null } = { options: null };
  const activeUserMessage = message({
    agent_run_id: activeRunId,
    content_markdown: "请给我实时回答",
    created_at: "2026-08-14T06:10:00Z",
    id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    role: "user",
    status: "committed",
  });
  mocks.listMessages
    .mockResolvedValueOnce({ messages: historicalMessages, next_cursor: null })
    .mockResolvedValue({
      messages: [...historicalMessages, activeUserMessage],
      next_cursor: null,
    });
  mocks.startTurn.mockResolvedValue({
    agent_run_id: activeRunId,
    conversation_id: conversationId,
    created: false,
    job_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    turn_id: turnId,
    user_message_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  });
  mocks.followAgentRunEvents.mockImplementation((options) => {
    capture.options = options;
    options.onConnectionState("open");
    return followImplementation?.(options) ?? new Promise<number>(() => undefined);
  });
  const user = userEvent.setup();
  renderWorkbench(workbenchUser);
  await user.click(await screen.findByRole("button", { name: /新能源汽车季度分析/u }));
  await screen.findByText("安全答案");
  await user.type(screen.getByLabelText("输入问题"), "请给我实时回答");
  await user.click(screen.getByRole("button", { name: "发送问题" }));
  await waitFor(() => {
    expect(mocks.followAgentRunEvents).toHaveBeenCalledOnce();
  });
  if (capture.options === null) {
    throw new Error("The component did not start following its Agent Run.");
  }
  return capture.options;
}

describe("Chat Workbench", () => {
  it("loads and selects a conversation while rendering its mode snapshot and untrusted Markdown safely", async () => {
    const user = userEvent.setup();
    renderWorkbench();

    await user.click(await screen.findByRole("button", { name: /新能源汽车季度分析/u }));

    expect(
      await screen.findByRole("heading", { level: 1, name: "新能源汽车季度分析" }),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: /新能源汽车季度分析/u })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByText("Web 搜索")).toBeVisible();
    expect(screen.getByRole("heading", { name: "安全答案" })).toBeVisible();
    const safeLink = screen.getByRole("link", { name: "安全链接" });
    expect(safeLink).toHaveAttribute("href", "https://example.com/report");
    expect(safeLink).toHaveAttribute("rel", "noreferrer noopener");
    expect(screen.queryByRole("link", { name: "危险链接" })).not.toBeInTheDocument();
    expect(screen.getByText("危险链接")).toBeVisible();
    expect(screen.getByText("<script>alert('x')</script>")).toBeVisible();
    expect(document.querySelector(".safe-markdown script")).toBeNull();
    expect(mocks.listMessages).toHaveBeenCalledWith(workspaceId, conversationId, { limit: 100 });
    expect(mocks.followAgentRunEvents).not.toHaveBeenCalled();
  });

  it("submits Web mode with the persisted current industry through the production turn contract", async () => {
    const user = userEvent.setup();
    mocks.startTurn.mockResolvedValue({
      agent_run_id: activeRunId,
      conversation_id: conversationId,
      created: false,
      job_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      turn_id: turnId,
      user_message_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    });
    renderWorkbench();
    await user.click(await screen.findByRole("button", { name: /新能源汽车季度分析/u }));
    await screen.findByText("安全答案");
    await user.selectOptions(screen.getByLabelText("回答模式"), "web");
    expect(screen.getByText(/当前行业：新能源汽车/u)).toBeVisible();
    await user.type(screen.getByLabelText("输入问题"), "检索最新政策");
    await user.click(screen.getByRole("button", { name: "发送问题" }));

    await waitFor(() => {
      expect(mocks.startTurn).toHaveBeenCalledWith(
        workspaceId,
        expect.objectContaining({
          conversation_id: conversationId,
          industry_id: "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa",
          mode: "web",
          question: "检索最新政策",
        }),
        expect.stringMatching(/^web-/u),
      );
    });
  });

  it("never shows one conversation's messages underneath another conversation's title", async () => {
    const user = userEvent.setup();
    const otherConversation = {
      ...conversation,
      id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
      title: "另一段会话",
    };
    mocks.listConversations.mockResolvedValue({
      conversations: [conversation, otherConversation],
      next_cursor: null,
    });
    mocks.listMessages
      .mockResolvedValueOnce({ messages: historicalMessages, next_cursor: null })
      .mockRejectedValueOnce(new Error("second conversation unavailable"));
    renderWorkbench();

    await user.click(await screen.findByRole("button", { name: /新能源汽车季度分析/u }));
    expect(await screen.findByText("安全答案")).toBeVisible();

    await user.click(screen.getByRole("button", { name: /另一段会话/u }));
    expect(await screen.findByRole("heading", { level: 1, name: "另一段会话" })).toBeVisible();
    expect(await screen.findByText("second conversation unavailable")).toBeVisible();
    expect(screen.queryByText("安全答案")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新加载消息" })).toBeVisible();
  });

  it("streams a partial answer, requests cancellation, and renders the committed terminal state", async () => {
    const user = userEvent.setup();
    mocks.listMessages
      .mockRejectedValue(new Error("message refresh unavailable"))
      .mockResolvedValueOnce({ messages: historicalMessages, next_cursor: null });
    mocks.startTurn.mockResolvedValue({
      agent_run_id: activeRunId,
      conversation_id: conversationId,
      created: false,
      job_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      turn_id: turnId,
      user_message_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    });
    const streamCapture: { options: CapturedStreamOptions | null } = { options: null };
    let settleStream: ((sequence: number) => void) | null = null;
    mocks.followAgentRunEvents.mockImplementation((options) => {
      streamCapture.options = options;
      options.onConnectionState("open");
      return new Promise<number>((resolve) => {
        settleStream = resolve;
      });
    });
    renderWorkbench();
    await user.click(await screen.findByRole("button", { name: /新能源汽车季度分析/u }));
    await screen.findByText("安全答案");

    await user.type(screen.getByLabelText("输入问题"), "请给我实时回答");
    await user.click(screen.getByRole("button", { name: "发送问题" }));

    await waitFor(() => {
      expect(mocks.startTurn).toHaveBeenCalledOnce();
      expect(mocks.followAgentRunEvents).toHaveBeenCalledOnce();
    });
    expect(await screen.findByText("message refresh unavailable")).toBeVisible();
    expect(mocks.startTurn).toHaveBeenCalledWith(
      workspaceId,
      expect.objectContaining({
        attachment_ids: [],
        conversation_id: conversationId,
        mode: "none",
        question: "请给我实时回答",
      }),
      expect.stringMatching(/^web-/u),
    );

    const captured = streamCapture.options;
    if (captured === null) {
      throw new Error("The component did not start following its Agent Run.");
    }
    await act(async () => {
      await captured.onEvent(streamEvent(1, "agent.model.delta", { delta: "**实时片段**" }));
    });
    expect(screen.getByText("实时片段")).toBeVisible();
    expect(screen.getByText("正在回答")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "停止" }));
    await waitFor(() => {
      expect(mocks.cancelRun).toHaveBeenCalledWith(workspaceId, activeRunId);
    });
    expect(screen.getByRole("button", { name: "正在停止" })).toBeDisabled();

    await act(async () => {
      await captured.onEvent(streamEvent(2, "agent.run.cancelled", { stop_reason: "cancelled" }));
      settleStream?.(2);
    });

    const stoppedMessage = await screen.findByText(/本次回答已停止，已经生成的片段仍然保留/u);
    const streamingRegion = stoppedMessage.closest("article");
    if (streamingRegion === null) throw new Error("停止状态不在 Agent 消息区域内");
    expect(within(streamingRegion).getByText("已停止")).toBeVisible();
    expect(within(screen.getByLabelText("Agent 运行轨迹")).getByText("已停止")).toBeVisible();
    expect(screen.queryByRole("button", { name: "停止" })).not.toBeInTheDocument();
    expect(mocks.getAgentTrace).toHaveBeenCalledWith(workspaceId, activeRunId);
    expect(screen.getAllByText("cancelled").length).toBeGreaterThan(0);
  });

  it("confirms cancellation from Trace when SSE has not delivered a terminal Event", async () => {
    let confirm: ((terminal: ConfirmedAgentRunTerminal | null) => void) | null = null;
    mocks.pollAgentRunTerminal.mockImplementation(
      () =>
        new Promise((resolve) => {
          confirm = resolve;
        }),
    );
    const captured = await startActiveRun();
    await act(async () => {
      await captured.onEvent(streamEvent(1, "agent.model.delta", { delta: "保留的片段" }));
    });

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "停止" }));
    expect(screen.getByRole("button", { name: "正在停止" })).toBeDisabled();
    expect(mocks.pollAgentRunTerminal).toHaveBeenCalledWith(
      workspaceId,
      activeRunId,
      expect.objectContaining({ signal: captured.signal }),
    );

    act(() => {
      confirm?.({ status: "cancelled", trace: cancelledTrace() });
    });

    expect(await screen.findByText("保留的片段")).toBeVisible();
    expect(await screen.findByText(/本次回答已停止，已经生成的片段仍然保留/u)).toBeVisible();
    expect(captured.signal?.aborted).toBe(true);
    expect(screen.queryByRole("button", { name: "正在停止" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "重新提问" }));
    expect(screen.getByLabelText("输入问题")).toHaveValue("请给我实时回答");
  });

  it("restores content and terminal status from an authoritative snapshot alone", async () => {
    const captured = await startActiveRun();

    await act(async () => {
      await captured.onEvent(
        streamEvent(257, "stream.snapshot", {
          cached_input_tokens: 0,
          content_markdown: "**从快照恢复的完整片段**",
          cost_micro_usd: 0,
          input_tokens: 0,
          output_tokens: 0,
          run_id: activeRunId,
          status: "cancelled",
          stop_reason: "cancelled",
          terminal: true,
        }),
      );
    });

    expect(await screen.findByText("从快照恢复的完整片段")).toBeVisible();
    expect(await screen.findByText(/本次回答已停止，已经生成的片段仍然保留/u)).toBeVisible();
    expect(screen.queryByRole("button", { name: "停止" })).not.toBeInTheDocument();
    expect(mocks.getAgentTrace).toHaveBeenCalledWith(workspaceId, activeRunId);
  });

  it("creates, edits, and confirms a Memory candidate from a persisted message", async () => {
    mocks.createMemoryCandidate.mockResolvedValue({ ...memoryCandidate, created: true });
    mocks.confirmMemoryCandidate.mockResolvedValue(memoryResolution);
    mocks.listMemories.mockResolvedValue([]);
    const user = userEvent.setup();
    renderWorkbench();

    await user.click(await screen.findByRole("button", { name: /新能源汽车季度分析/u }));
    const sourceCard = (await screen.findByText("分析这份材料")).closest("article");
    if (sourceCard === null) throw new Error("记忆来源消息不在消息卡片内");
    await user.click(within(sourceCard).getByRole("button", { name: "选择为记忆来源" }));
    await user.click(screen.getByRole("button", { name: "生成记忆候选" }));

    expect(await screen.findByRole("heading", { name: "确认要长期保存的内容" })).toBeVisible();
    const editor = screen.getByLabelText("最终确认内容");
    expect(editor).toHaveValue("分析这份材料");
    await user.clear(editor);
    await user.type(editor, "默认使用中文回答。");
    await user.selectOptions(screen.getByLabelText("记忆类型"), "preference");
    await user.click(screen.getByRole("button", { name: "创建新记忆" }));

    expect(await screen.findByText("Memory 已确认")).toBeVisible();
    expect(screen.getByText("默认使用中文回答。")).toBeVisible();
    expect(mocks.createMemoryCandidate).toHaveBeenCalledWith(
      workspaceId,
      {
        conversation_id: conversationId,
        message_ids: [historicalUserMessageId],
        scope: "user",
      },
      expect.stringMatching(/^memory-/u),
    );
    expect(mocks.confirmMemoryCandidate).toHaveBeenCalledWith(
      workspaceId,
      memoryCandidate.id,
      1,
      expect.objectContaining({
        action: "create",
        content: "默认使用中文回答。",
        kind: "preference",
        scope: "user",
      }),
    );
  });

  it("reopens a confirmed Memory revision from the persisted candidate after refresh", async () => {
    mocks.listMemoryCandidates.mockResolvedValue([
      {
        ...memoryCandidate,
        resolved_memory_id: memoryResolution.memory.memory.id,
        revision: 2,
        status: "confirmed",
      },
    ]);
    mocks.listMemories.mockResolvedValue([memoryResolution.memory.memory]);
    mocks.getMemory.mockResolvedValue(memoryResolution.memory);
    const user = userEvent.setup();
    renderWorkbench();

    await user.click(await screen.findByRole("button", { name: /新能源汽车季度分析/u }));
    await user.click(await screen.findByRole("button", { name: "记忆记录 1" }));

    expect(await screen.findByText("Memory 已确认")).toBeVisible();
    expect(screen.getByText("默认使用中文回答。")).toBeVisible();
    expect(mocks.getMemory).toHaveBeenCalledWith(workspaceId, memoryResolution.memory.memory.id);
  });

  it("keeps an unconfirmed cancellation busy while allowing an idempotent status retry", async () => {
    mocks.pollAgentRunTerminal.mockResolvedValue(null);
    await startActiveRun();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "停止" }));

    expect(await screen.findByText(/停止请求已提交，但服务尚未确认运行终态/u)).toBeVisible();
    expect(screen.getByRole("button", { name: "停止" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "重新提问" })).not.toBeInTheDocument();
    expect(screen.queryByText("已停止")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("输入问题"), "不能并发提交的新问题");
    expect(screen.getByRole("button", { name: "发送问题" })).toBeDisabled();
  });

  it("ignores a cancellation confirmation after the user changes Workspace", async () => {
    let confirm: ((terminal: ConfirmedAgentRunTerminal | null) => void) | null = null;
    mocks.pollAgentRunTerminal.mockImplementation(
      () =>
        new Promise((resolve) => {
          confirm = resolve;
        }),
    );
    const captured = await startActiveRun(undefined, multiWorkspaceUser);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "停止" }));
    expect(screen.getByRole("button", { name: "正在停止" })).toBeDisabled();

    await user.selectOptions(screen.getByLabelText("Workspace"), secondWorkspaceId);
    expect(screen.getByLabelText("Workspace")).toHaveValue(secondWorkspaceId);
    expect(captured.signal?.aborted).toBe(true);

    await act(async () => {
      confirm?.({ status: "cancelled", trace: cancelledTrace() });
      await Promise.resolve();
    });

    expect(screen.queryByText(/本次回答已停止/u)).not.toBeInTheDocument();
    expect(screen.queryByText("已停止")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "正在停止" })).not.toBeInTheDocument();
  });

  it("does not turn an SSE delivery failure into a business Run failure", async () => {
    await startActiveRun(() => Promise.reject(new Error("stream contract failed")));
    const user = userEvent.setup();

    expect(await screen.findByText(/流式连接中断：stream contract failed/u)).toBeVisible();
    expect(screen.getByRole("button", { name: "停止" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "重新提问" })).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("输入问题"), "仍不能并发提交");
    expect(screen.getByRole("button", { name: "发送问题" })).toBeDisabled();
  });
});
