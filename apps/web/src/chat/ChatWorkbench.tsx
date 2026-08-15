import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type KeyboardEvent,
  type SubmitEvent,
} from "react";

import { ApiProblem } from "../api/api";
import type { CurrentUser } from "../auth/auth-context";
import {
  cancelRun,
  deleteConversation,
  deleteFile,
  followAgentRunEvents,
  getAgentTrace,
  getDownloadUrl,
  listConversations,
  listMessages,
  renameConversation,
  startTurn,
  uploadFile,
  type AgentStreamConnectionState,
  type AgentStreamEvent,
  type AgentTrace,
  type ConversationMessage,
  type ConversationSummary,
  type FileSnapshot,
} from "./chat-api";
import "./chat.css";
import { pollAgentRunTerminal, type ConfirmedAgentRunTerminal } from "./agent-run-status";
import { Icon } from "./icons";
import { SafeMarkdown } from "./SafeMarkdown";

const MAX_ATTACHMENTS = 4;
const MESSAGE_PAGE_SIZE = 100;

type LoadState = "error" | "loading" | "ready";
type RunStatus = "cancelled" | "completed" | "failed" | "running";

interface ComposerAttachment {
  readonly key: string;
  readonly workspaceId: string;
  readonly name: string;
  readonly kind: "document" | "image";
  readonly status: "error" | "ready" | "uploading";
  readonly snapshot?: FileSnapshot;
  readonly error?: string;
}

interface ActiveRun {
  readonly conversationId: string;
  readonly runId: string;
  readonly status: RunStatus;
  readonly connection: AgentStreamConnectionState;
  readonly partialMarkdown: string;
  readonly events: readonly AgentStreamEvent[];
  readonly error: string | null;
  readonly cancelRequested: boolean;
}

interface ChatWorkbenchProps {
  readonly currentUser: CurrentUser;
  readonly onLogout: () => Promise<void>;
  readonly onOpenSettings: () => void;
}

const promptSuggestions = [
  "用三点概括新能源汽车供应链目前最值得关注的变化。",
  "给我一个评估新行业机会时可以复用的分析框架。",
  "解释一家公司毛利率下降时应该优先检查哪些因素。",
  "把我上传的材料整理成清晰的管理层摘要。",
] as const;

const modeNames = {
  both: "Web + 知识库",
  local: "知识库",
  none: "直接回答",
  web: "Web 搜索",
} as const;

const roleNames = {
  admin: "管理员",
  member: "成员",
  owner: "所有者",
  viewer: "观察者",
} as const;

const runStatusNames: Record<string, string> = {
  cancelled: "已停止",
  completed: "已完成",
  failed: "失败",
  queued: "排队中",
  running: "运行中",
};

const eventNames: Record<string, string> = {
  "agent.model.completed": "模型响应完成",
  "agent.model.delta": "收到流式片段",
  "agent.model.started": "模型调用开始",
  "agent.run.cancelled": "运行已取消",
  "agent.run.completed": "运行已完成",
  "agent.run.failed": "运行失败",
  "agent.run.queued": "运行已排队",
  "agent.run.resumed": "运行已恢复",
  "agent.run.started": "运行已开始",
  "agent.step.completed": "步骤完成",
  "agent.step.failed": "步骤失败",
  "agent.step.started": "步骤开始",
};

const sourceNames: Record<string, string> = {
  attachment: "附件",
  conversation_summary: "会话摘要",
  runtime_context_projection: "Workspace 安全投影",
  system_instructions: "系统指令",
  user_question: "当前问题",
};

function publicError(error: unknown): string {
  if (error instanceof ApiProblem) {
    return `${error.message}${error.traceId === null ? "" : `（追踪号 ${error.traceId}）`}`;
  }
  return error instanceof Error && error.message ? error.message : "服务暂时不可用，请稍后重试。";
}

function relativeTime(value: string): string {
  const timestamp = new Date(value).getTime();
  const elapsed = Date.now() - timestamp;
  if (!Number.isFinite(timestamp) || elapsed < 0) return "刚刚";
  const minutes = Math.floor(elapsed / 60_000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${String(minutes)} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${String(hours)} 小时前`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${String(days)} 天前`;
  return new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric" }).format(timestamp);
}

function formatBytes(value: number): string {
  if (value < 1_000) return `${String(value)} B`;
  if (value < 1_000_000) return `${(value / 1_000).toFixed(1)} KB`;
  return `${(value / 1_000_000).toFixed(1)} MB`;
}

function formatCost(microUsd: number): string {
  if (microUsd === 0) return "$0";
  return `$${(microUsd / 1_000_000).toFixed(6)}`;
}

function payloadString(event: AgentStreamEvent, field: string): string | null {
  const value = event.payload[field];
  return typeof value === "string" ? value : null;
}

function isTerminalEvent(event: AgentStreamEvent): boolean {
  return (
    event.type === "agent.run.completed" ||
    event.type === "agent.run.failed" ||
    event.type === "agent.run.cancelled"
  );
}

function terminalStatus(event: AgentStreamEvent): RunStatus | null {
  if (event.type === "agent.run.completed") return "completed";
  if (event.type === "agent.run.cancelled") return "cancelled";
  if (event.type === "agent.run.failed") return "failed";
  return null;
}

function runFailureMessage(reason: string | null): string {
  if (reason === "provider_timeout") return "模型响应超时。你的问题已经保存，可以重新提问。";
  if (reason === "provider_rate_limited")
    return "模型服务当前繁忙。你的问题已经保存，可以稍后重新提问。";
  if (reason === "cancelled") return "本次回答已停止，已经生成的片段仍然保留。";
  if (reason === "incomplete_provider_response") return "模型连接在完成前中断，已保留收到的内容。";
  return `本次回答未完成${reason === null ? "" : `（${reason}）`}。你的问题没有丢失。`;
}

function newestUnfinishedRun(messages: readonly ConversationMessage[]): string | null {
  const latest = messages.at(-1);
  if (latest === undefined) return null;
  const hasFinal = messages.some(
    (message) =>
      message.agent_run_id === latest.agent_run_id &&
      message.role === "assistant" &&
      message.status === "final",
  );
  return hasFinal ? null : latest.agent_run_id;
}

function latestUserMessage(messages: readonly ConversationMessage[]): ConversationMessage | null {
  return messages.findLast((message) => message.role === "user") ?? null;
}

function idempotencyKey(): string {
  return `web-${crypto.randomUUID()}`;
}

function attachmentKind(file: File): "document" | "image" {
  return file.type.startsWith("image/") || /\.(?:jpe?g|png|webp)$/iu.test(file.name)
    ? "image"
    : "document";
}

export function ChatWorkbench({ currentUser, onLogout, onOpenSettings }: ChatWorkbenchProps) {
  const initialWorkspaceId = currentUser.workspaces[0]?.id ?? "";
  const [workspaceId, setWorkspaceId] = useState(initialWorkspaceId);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversationCursor, setConversationCursor] = useState<string | null>(null);
  const [conversationState, setConversationState] = useState<LoadState>("loading");
  const [conversationError, setConversationError] = useState<string | null>(null);
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);
  const [creatingNew, setCreatingNew] = useState(false);
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [messagesState, setMessagesState] = useState<LoadState>("ready");
  const [messagesError, setMessagesError] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [composerAttachments, setComposerAttachments] = useState<ComposerAttachment[]>([]);
  const [composerError, setComposerError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [activeRun, setActiveRun] = useState<ActiveRun | null>(null);
  const [trace, setTrace] = useState<AgentTrace | null>(null);
  const [traceState, setTraceState] = useState<LoadState>("ready");
  const [traceError, setTraceError] = useState<string | null>(null);
  const [traceRunId, setTraceRunId] = useState<string | null>(null);
  const [traceOpen, setTraceOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [renameTitle, setRenameTitle] = useState("");
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const streamAbortRef = useRef<AbortController | null>(null);
  const workspaceIdRef = useRef(workspaceId);
  const workspaceGenerationRef = useRef({ value: 0, workspaceId });
  const selectedConversationIdRef = useRef<string | null>(selectedConversationId);
  const activeRunIdRef = useRef<string | null>(null);
  const previousWorkspaceIdRef = useRef(workspaceId);
  const composerAttachmentsRef = useRef<ComposerAttachment[]>([]);
  const conversationListRequestRef = useRef(0);
  const messageRequestRef = useRef(0);
  const traceRequestRef = useRef(0);
  const settledRunIdsRef = useRef(new Set<string>());
  const submitAttemptRef = useRef<{ readonly fingerprint: string; readonly key: string } | null>(
    null,
  );
  const threadEndRef = useRef<HTMLDivElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const renameInputRef = useRef<HTMLInputElement | null>(null);

  const workspace = currentUser.workspaces.find((candidate) => candidate.id === workspaceId);
  const canCompose = workspace !== undefined && workspace.role !== "viewer";
  const selectedConversation = conversations.find(
    (conversation) => conversation.id === selectedConversationId,
  );
  const filteredConversations = useMemo(() => {
    const query = search.trim().toLocaleLowerCase("zh-CN");
    return query
      ? conversations.filter((conversation) =>
          conversation.title.toLocaleLowerCase("zh-CN").includes(query),
        )
      : conversations;
  }, [conversations, search]);

  workspaceIdRef.current = workspaceId;
  selectedConversationIdRef.current = selectedConversationId;
  activeRunIdRef.current = activeRun?.runId ?? null;
  composerAttachmentsRef.current = composerAttachments;

  const loadConversationList = useCallback(
    async (cursor: string | null, append: boolean): Promise<void> => {
      if (!workspaceId) return;
      const requestedWorkspaceId = workspaceId;
      const requestNumber = conversationListRequestRef.current + 1;
      conversationListRequestRef.current = requestNumber;
      setConversationError(null);
      if (!append) setConversationState("loading");
      try {
        const page = await listConversations(
          workspaceId,
          append && cursor !== null ? { cursor, limit: 40 } : { limit: 40 },
        );
        if (
          workspaceIdRef.current !== requestedWorkspaceId ||
          conversationListRequestRef.current !== requestNumber
        ) {
          return;
        }
        setConversations((current) =>
          append
            ? [
                ...current,
                ...page.conversations.filter((item) => !current.some((old) => old.id === item.id)),
              ]
            : page.conversations,
        );
        setConversationCursor(page.next_cursor);
        setConversationState("ready");
      } catch (error: unknown) {
        if (
          workspaceIdRef.current !== requestedWorkspaceId ||
          conversationListRequestRef.current !== requestNumber
        ) {
          return;
        }
        setConversationState("error");
        setConversationError(publicError(error));
      }
    },
    [workspaceId],
  );

  const loadAllMessages = useCallback(
    async (conversationId: string): Promise<ConversationMessage[]> => {
      const collected: ConversationMessage[] = [];
      let cursor: string | null = null;
      do {
        const page = await listMessages(
          workspaceId,
          conversationId,
          cursor === null ? { limit: MESSAGE_PAGE_SIZE } : { cursor, limit: MESSAGE_PAGE_SIZE },
        );
        collected.push(...page.messages);
        cursor = page.next_cursor;
      } while (cursor !== null);
      return collected;
    },
    [workspaceId],
  );

  const refreshMessages = useCallback(
    async (conversationId: string): Promise<ConversationMessage[]> => {
      const requestedWorkspaceId = workspaceId;
      const requestedWorkspaceGeneration = workspaceGenerationRef.current.value;
      const requestNumber = messageRequestRef.current + 1;
      messageRequestRef.current = requestNumber;
      try {
        const loaded = await loadAllMessages(conversationId);
        if (
          workspaceIdRef.current === requestedWorkspaceId &&
          workspaceGenerationRef.current.value === requestedWorkspaceGeneration &&
          selectedConversationIdRef.current === conversationId &&
          messageRequestRef.current === requestNumber
        ) {
          setMessages(loaded);
          setMessagesState("ready");
          setMessagesError(null);
        }
        return loaded;
      } catch (error: unknown) {
        if (
          workspaceIdRef.current !== requestedWorkspaceId ||
          workspaceGenerationRef.current.value !== requestedWorkspaceGeneration ||
          selectedConversationIdRef.current !== conversationId ||
          messageRequestRef.current !== requestNumber
        ) {
          return [];
        }
        setMessagesState("error");
        setMessagesError(publicError(error));
        throw error;
      }
    },
    [loadAllMessages, workspaceId],
  );

  const loadTrace = useCallback(
    async (runId: string, openPanel: boolean): Promise<void> => {
      const requestedWorkspaceId = workspaceId;
      const requestNumber = traceRequestRef.current + 1;
      traceRequestRef.current = requestNumber;
      if (openPanel) setTraceOpen(true);
      setTraceRunId(runId);
      setTrace(null);
      setTraceState("loading");
      setTraceError(null);
      try {
        const loaded = await getAgentTrace(workspaceId, runId);
        if (
          workspaceIdRef.current !== requestedWorkspaceId ||
          traceRequestRef.current !== requestNumber
        ) {
          return;
        }
        setTrace(loaded);
        setTraceState("ready");
      } catch (error: unknown) {
        if (
          workspaceIdRef.current !== requestedWorkspaceId ||
          traceRequestRef.current !== requestNumber
        ) {
          return;
        }
        setTrace(null);
        setTraceState("error");
        setTraceError(publicError(error));
      }
    },
    [workspaceId],
  );

  const applyConfirmedTerminal = useCallback(
    async (
      runId: string,
      conversationId: string,
      requestedWorkspaceId: string,
      requestedWorkspaceGeneration: number,
      controller: AbortController | null,
      terminal: ConfirmedAgentRunTerminal,
    ): Promise<void> => {
      if (
        controller?.signal.aborted === true ||
        workspaceIdRef.current !== requestedWorkspaceId ||
        workspaceGenerationRef.current.value !== requestedWorkspaceGeneration ||
        selectedConversationIdRef.current !== conversationId ||
        activeRunIdRef.current !== runId ||
        settledRunIdsRef.current.has(runId)
      ) {
        return;
      }

      settledRunIdsRef.current.add(runId);
      controller?.abort();
      traceRequestRef.current += 1;
      setTraceRunId(runId);
      setTrace(terminal.trace);
      setTraceState("ready");
      setTraceError(null);
      setActiveRun((current) =>
        current?.runId === runId && current.conversationId === conversationId
          ? {
              ...current,
              cancelRequested: false,
              connection: "closed",
              error:
                terminal.status === "completed"
                  ? null
                  : runFailureMessage(terminal.trace.run.stop_reason),
              status: terminal.status,
            }
          : current,
      );

      await Promise.allSettled([
        refreshMessages(conversationId).catch((error: unknown) => {
          if (
            workspaceIdRef.current === requestedWorkspaceId &&
            workspaceGenerationRef.current.value === requestedWorkspaceGeneration &&
            selectedConversationIdRef.current === conversationId
          ) {
            setMessagesError(
              `运行已结束，但消息刷新失败：${publicError(error)}。你可以重新加载消息。`,
            );
          }
        }),
        loadConversationList(null, false),
      ]);
    },
    [loadConversationList, refreshMessages],
  );

  const followRun = useCallback(
    (runId: string, conversationId: string): void => {
      streamAbortRef.current?.abort();
      const controller = new AbortController();
      streamAbortRef.current = controller;
      settledRunIdsRef.current.delete(runId);
      traceRequestRef.current += 1;
      setTrace(null);
      setTraceRunId(runId);
      setTraceError(null);
      setTraceState("ready");
      setActiveRun({
        cancelRequested: false,
        connection: "connecting",
        conversationId,
        error: null,
        events: [],
        partialMarkdown: "",
        runId,
        status: "running",
      });
      void followAgentRunEvents({
        onConnectionState(connection) {
          setActiveRun((current) =>
            current?.runId === runId ? { ...current, connection } : current,
          );
        },
        async onEvent(event) {
          let terminal: RunStatus | null = null;
          setActiveRun((current) => {
            if (current?.runId !== runId) return current;
            const delta = event.type === "agent.model.delta" ? payloadString(event, "delta") : null;
            const finalMarkdown =
              event.type === "agent.step.completed"
                ? payloadString(event, "content_markdown")
                : null;
            terminal = terminalStatus(event);
            return {
              ...current,
              cancelRequested: terminal === null ? current.cancelRequested : false,
              error:
                event.type === "agent.run.failed" || event.type === "agent.run.cancelled"
                  ? runFailureMessage(payloadString(event, "stop_reason"))
                  : terminal === "completed"
                    ? null
                    : current.error,
              events: [...current.events, event],
              partialMarkdown: finalMarkdown ?? `${current.partialMarkdown}${delta ?? ""}`,
              status: terminal ?? current.status,
            };
          });
          if (isTerminalEvent(event)) {
            settledRunIdsRef.current.add(runId);
            await Promise.allSettled([
              refreshMessages(conversationId).catch((error: unknown) => {
                if (
                  workspaceIdRef.current === workspaceId &&
                  selectedConversationIdRef.current === conversationId
                ) {
                  setMessagesError(
                    `运行已结束，但消息刷新失败：${publicError(error)}。你可以重新加载消息。`,
                  );
                }
              }),
              loadConversationList(null, false),
              loadTrace(runId, false),
            ]);
          }
        },
        runId,
        signal: controller.signal,
        workspaceId,
      }).catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setActiveRun((current) =>
          current?.runId === runId
            ? {
                ...current,
                connection: "closed",
                error: `流式连接中断：${publicError(error)}。刷新页面会从已保存的位置继续。`,
              }
            : current,
        );
      });
    },
    [loadConversationList, loadTrace, refreshMessages, workspaceId],
  );

  useEffect(() => {
    if (workspaceGenerationRef.current.workspaceId !== workspaceId) {
      workspaceGenerationRef.current = {
        value: workspaceGenerationRef.current.value + 1,
        workspaceId,
      };
    }
    const previousWorkspaceId = previousWorkspaceIdRef.current;
    previousWorkspaceIdRef.current = workspaceId;
    if (previousWorkspaceId !== workspaceId) {
      for (const attachment of composerAttachmentsRef.current) {
        if (attachment.snapshot !== undefined) {
          void deleteFile(attachment.workspaceId, attachment.snapshot.id).catch(() => {
            // The server-side expiry/reconciliation path retains ownership if
            // best-effort draft cleanup cannot reach private storage.
          });
        }
      }
    }
    streamAbortRef.current?.abort();
    conversationListRequestRef.current += 1;
    messageRequestRef.current += 1;
    traceRequestRef.current += 1;
    setSelectedConversationId(null);
    selectedConversationIdRef.current = null;
    setCreatingNew(false);
    setMessages([]);
    setMessagesError(null);
    setActiveRun(null);
    setTrace(null);
    setTraceRunId(null);
    setTraceError(null);
    setTraceState("ready");
    setConversationCursor(null);
    setConversations([]);
    setConversationState("loading");
    setQuestion("");
    setComposerAttachments([]);
    setComposerError(null);
    submitAttemptRef.current = null;
  }, [workspaceId]);

  useEffect(() => {
    void loadConversationList(null, false);
  }, [loadConversationList]);

  useEffect(() => {
    if (selectedConversationId === null) {
      setMessages([]);
      setMessagesState("ready");
      return;
    }
    let active = true;
    const requestedWorkspaceId = workspaceId;
    const requestedWorkspaceGeneration = workspaceGenerationRef.current.value;
    const requestNumber = messageRequestRef.current + 1;
    messageRequestRef.current = requestNumber;
    setMessagesState("loading");
    setMessages([]);
    setMessagesError(null);
    setComposerError(null);
    void loadAllMessages(selectedConversationId)
      .then((loaded) => {
        if (
          !active ||
          workspaceIdRef.current !== requestedWorkspaceId ||
          workspaceGenerationRef.current.value !== requestedWorkspaceGeneration ||
          selectedConversationIdRef.current !== selectedConversationId ||
          messageRequestRef.current !== requestNumber
        ) {
          return;
        }
        setMessages(loaded);
        setMessagesState("ready");
        const unfinishedRun = newestUnfinishedRun(loaded);
        if (unfinishedRun !== null && activeRun?.runId !== unfinishedRun) {
          followRun(unfinishedRun, selectedConversationId);
        } else if (unfinishedRun === null) {
          setActiveRun(null);
        }
      })
      .catch((error: unknown) => {
        if (
          !active ||
          workspaceIdRef.current !== requestedWorkspaceId ||
          workspaceGenerationRef.current.value !== requestedWorkspaceGeneration ||
          selectedConversationIdRef.current !== selectedConversationId ||
          messageRequestRef.current !== requestNumber
        ) {
          return;
        }
        setMessagesState("error");
        setMessagesError(publicError(error));
      });
    return () => {
      active = false;
    };
  }, [activeRun?.runId, followRun, loadAllMessages, selectedConversationId, workspaceId]);

  useEffect(
    () => () => {
      streamAbortRef.current?.abort();
    },
    [],
  );

  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ block: "end" });
  }, [activeRun?.partialMarkdown, messages]);

  useEffect(() => {
    if (renaming) renameInputRef.current?.focus();
  }, [renaming]);

  function changeWorkspace(nextWorkspaceId: string): void {
    if (nextWorkspaceId === workspaceId || submitting) return;
    workspaceGenerationRef.current = {
      value: workspaceGenerationRef.current.value + 1,
      workspaceId: nextWorkspaceId,
    };
    workspaceIdRef.current = nextWorkspaceId;
    setWorkspaceId(nextWorkspaceId);
  }

  function selectConversation(conversationId: string): void {
    if (submitting) return;
    if (conversationId === selectedConversationIdRef.current) {
      setSidebarOpen(false);
      retryMessageLoad();
      return;
    }
    streamAbortRef.current?.abort();
    messageRequestRef.current += 1;
    traceRequestRef.current += 1;
    setCreatingNew(false);
    selectedConversationIdRef.current = conversationId;
    setSelectedConversationId(conversationId);
    setMessages([]);
    setMessagesState("loading");
    setMessagesError(null);
    setActiveRun(null);
    setTrace(null);
    setTraceError(null);
    setTraceState("ready");
    setTraceRunId(null);
    setSidebarOpen(false);
    setRenaming(false);
  }

  function retryMessageLoad(): void {
    const conversationId = selectedConversationIdRef.current;
    if (conversationId === null) return;
    setMessagesState("loading");
    setMessagesError(null);
    void refreshMessages(conversationId).catch(() => {
      // refreshMessages owns the visible, request-epoch-scoped error state.
    });
  }

  function startNewConversation(): void {
    if (submitting) return;
    streamAbortRef.current?.abort();
    messageRequestRef.current += 1;
    traceRequestRef.current += 1;
    setCreatingNew(true);
    selectedConversationIdRef.current = null;
    setSelectedConversationId(null);
    setMessages([]);
    setMessagesError(null);
    setQuestion("");
    setActiveRun(null);
    setTrace(null);
    setTraceError(null);
    setTraceState("ready");
    setTraceRunId(null);
    setSidebarOpen(false);
    setComposerError(null);
  }

  async function addFiles(event: ChangeEvent<HTMLInputElement>): Promise<void> {
    const selected = [...(event.currentTarget.files ?? [])];
    const uploadWorkspaceId = workspaceId;
    const uploadWorkspaceGeneration = workspaceGenerationRef.current.value;
    event.currentTarget.value = "";
    if (!canCompose || selected.length === 0) return;
    if (composerAttachments.length + selected.length > MAX_ATTACHMENTS) {
      setComposerError(`每次最多选择 ${String(MAX_ATTACHMENTS)} 个附件。`);
      return;
    }
    setComposerError(null);
    for (const file of selected) {
      const key = crypto.randomUUID();
      const pending: ComposerAttachment = {
        key,
        kind: attachmentKind(file),
        name: file.name,
        status: "uploading",
        workspaceId: uploadWorkspaceId,
      };
      setComposerAttachments((current) => [...current, pending]);
      try {
        const snapshot = await uploadFile(uploadWorkspaceId, file);
        if (
          workspaceIdRef.current !== uploadWorkspaceId ||
          workspaceGenerationRef.current.value !== uploadWorkspaceGeneration
        ) {
          await deleteFile(uploadWorkspaceId, snapshot.id).catch(() => {
            // Expiry/reconciliation is the fallback for an upload that finished
            // after the user left its Workspace.
          });
          continue;
        }
        setComposerAttachments((current) =>
          current.map((item) =>
            item.key === key
              ? { ...item, name: snapshot.original_name, snapshot, status: "ready" }
              : item,
          ),
        );
      } catch (error: unknown) {
        if (
          workspaceIdRef.current !== uploadWorkspaceId ||
          workspaceGenerationRef.current.value !== uploadWorkspaceGeneration
        ) {
          continue;
        }
        setComposerAttachments((current) =>
          current.map((item) =>
            item.key === key ? { ...item, error: publicError(error), status: "error" } : item,
          ),
        );
      }
    }
  }

  async function removeComposerAttachment(item: ComposerAttachment): Promise<void> {
    setComposerAttachments((current) => current.filter((candidate) => candidate.key !== item.key));
    if (item.snapshot !== undefined) {
      try {
        await deleteFile(item.workspaceId, item.snapshot.id);
      } catch (error: unknown) {
        if (workspaceIdRef.current === item.workspaceId) {
          setComposerError(`附件清理失败：${publicError(error)}`);
        }
      }
    }
  }

  async function submitQuestion(event?: SubmitEvent<HTMLFormElement>): Promise<void> {
    event?.preventDefault();
    const normalizedQuestion = question.trim();
    const readyAttachments = composerAttachments.filter(
      (item): item is ComposerAttachment & { readonly snapshot: FileSnapshot } =>
        item.status === "ready" && item.snapshot !== undefined,
    );
    if (
      !canCompose ||
      !normalizedQuestion ||
      submitting ||
      activeRun?.status === "running" ||
      readyAttachments.length !== composerAttachments.length
    ) {
      return;
    }
    const fingerprint = JSON.stringify({
      attachmentIds: readyAttachments.map((item) => item.snapshot.id),
      conversationId: selectedConversationId,
      question: normalizedQuestion,
      workspaceId,
    });
    const existingAttempt = submitAttemptRef.current;
    const attempt =
      existingAttempt?.fingerprint === fingerprint
        ? existingAttempt
        : { fingerprint, key: idempotencyKey() };
    submitAttemptRef.current = attempt;
    const submittedWorkspaceId = workspaceId;
    const submittedWorkspaceGeneration = workspaceGenerationRef.current.value;
    const submittedConversationId = selectedConversationId;
    setSubmitting(true);
    setComposerError(null);
    try {
      const receipt = await startTurn(
        submittedWorkspaceId,
        {
          attachment_ids: readyAttachments.map((item) => item.snapshot.id),
          conversation_id: submittedConversationId,
          knowledge_base_ids: [],
          mode: "none",
          question: normalizedQuestion,
        },
        attempt.key,
      );
      submitAttemptRef.current = null;
      if (
        workspaceIdRef.current !== submittedWorkspaceId ||
        workspaceGenerationRef.current.value !== submittedWorkspaceGeneration ||
        selectedConversationIdRef.current !== submittedConversationId
      ) {
        return;
      }
      setQuestion("");
      setComposerAttachments([]);
      setCreatingNew(false);
      selectedConversationIdRef.current = receipt.conversation_id;
      setSelectedConversationId(receipt.conversation_id);
      setMessagesState("loading");
      setMessagesError(null);
      followRun(receipt.agent_run_id, receipt.conversation_id);
      void loadConversationList(null, false);
    } catch (error: unknown) {
      setComposerError(publicError(error));
    } finally {
      setSubmitting(false);
    }
  }

  function composerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>): void {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void submitQuestion();
    }
  }

  async function requestCancellation(): Promise<void> {
    if (activeRun?.status !== "running" || activeRun.cancelRequested) return;
    const requestedRunId = activeRun.runId;
    const requestedConversationId = activeRun.conversationId;
    const requestedWorkspaceId = workspaceId;
    const requestedWorkspaceGeneration = workspaceGenerationRef.current.value;
    const controller = streamAbortRef.current;
    setActiveRun((current) =>
      current?.runId === requestedRunId &&
      current.conversationId === requestedConversationId &&
      current.status === "running"
        ? { ...current, cancelRequested: true }
        : current,
    );
    try {
      await cancelRun(requestedWorkspaceId, requestedRunId);
    } catch (error: unknown) {
      setActiveRun((current) =>
        current?.runId === requestedRunId &&
        current.status === "running" &&
        workspaceIdRef.current === requestedWorkspaceId &&
        workspaceGenerationRef.current.value === requestedWorkspaceGeneration
          ? { ...current, cancelRequested: false, error: publicError(error) }
          : current,
      );
      return;
    }

    if (
      workspaceIdRef.current !== requestedWorkspaceId ||
      workspaceGenerationRef.current.value !== requestedWorkspaceGeneration ||
      selectedConversationIdRef.current !== requestedConversationId ||
      activeRunIdRef.current !== requestedRunId
    ) {
      return;
    }

    let terminal: ConfirmedAgentRunTerminal | null = null;
    let confirmationError: unknown = null;
    try {
      terminal = await pollAgentRunTerminal(
        requestedWorkspaceId,
        requestedRunId,
        controller === null ? {} : { signal: controller.signal },
      );
    } catch (error: unknown) {
      confirmationError = error;
    }

    if (
      controller?.signal.aborted === true ||
      workspaceIdRef.current !== requestedWorkspaceId ||
      workspaceGenerationRef.current.value !== requestedWorkspaceGeneration ||
      selectedConversationIdRef.current !== requestedConversationId ||
      activeRunIdRef.current !== requestedRunId
    ) {
      return;
    }
    if (terminal !== null) {
      await applyConfirmedTerminal(
        requestedRunId,
        requestedConversationId,
        requestedWorkspaceId,
        requestedWorkspaceGeneration,
        controller,
        terminal,
      );
      return;
    }

    const confirmationDetail =
      confirmationError === null ? "" : `（状态检查失败：${publicError(confirmationError)}）`;
    setActiveRun((current) =>
      current?.runId === requestedRunId &&
      current.conversationId === requestedConversationId &&
      current.status === "running"
        ? {
            ...current,
            cancelRequested: false,
            error: `停止请求已提交，但服务尚未确认运行终态${confirmationDetail}。运行仍在后台处理中，你可以再次停止以重新检查。`,
          }
        : current,
    );
  }

  function retryLastQuestion(): void {
    if (activeRun?.status === "running") return;
    const previous = latestUserMessage(messages);
    if (previous === null || previous.attachments.length > 0) return;
    setQuestion(previous.content_markdown);
    setActiveRun(null);
    requestAnimationFrame(() => {
      document.querySelector<HTMLTextAreaElement>(".composer textarea")?.focus();
    });
  }

  async function saveRename(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (selectedConversationId === null || !renameTitle.trim() || submitting) return;
    try {
      const updated = await renameConversation(
        workspaceId,
        selectedConversationId,
        renameTitle.trim(),
      );
      setConversations((current) =>
        current.map((item) => (item.id === updated.id ? updated : item)),
      );
      setRenaming(false);
    } catch (error: unknown) {
      setComposerError(publicError(error));
    }
  }

  async function confirmDelete(): Promise<void> {
    if (selectedConversationId === null || submitting) return;
    try {
      await deleteConversation(workspaceId, selectedConversationId);
      streamAbortRef.current?.abort();
      setDeleteDialogOpen(false);
      selectedConversationIdRef.current = null;
      setSelectedConversationId(null);
      setCreatingNew(true);
      setMessages([]);
      setActiveRun(null);
      setTrace(null);
      await loadConversationList(null, false);
    } catch (error: unknown) {
      setDeleteDialogOpen(false);
      setComposerError(publicError(error));
    }
  }

  async function downloadAttachment(fileId: string): Promise<void> {
    try {
      const ticket = await getDownloadUrl(workspaceId, fileId);
      const anchor = document.createElement("a");
      anchor.href = ticket.url;
      anchor.rel = "noopener noreferrer";
      anchor.target = "_blank";
      anchor.click();
    } catch (error: unknown) {
      setComposerError(publicError(error));
    }
  }

  const runIsBusy = activeRun?.status === "running";
  const attachmentsReady = composerAttachments.every((item) => item.status === "ready");
  const submitDisabled =
    !canCompose || !question.trim() || submitting || runIsBusy || !attachmentsReady;
  const panelActiveRun =
    activeRun !== null && (traceRunId === null || traceRunId === activeRun.runId)
      ? activeRun
      : null;
  const traceEvents =
    trace !== null && trace.run.run_id === traceRunId
      ? trace.events
      : (panelActiveRun?.events ?? []);

  return (
    <main className="chat-shell">
      <header className="chat-topbar">
        <a className="chat-brand" href="/" aria-label="行业智能平台">
          <span className="chat-brand__mark">IIP</span>
          <span className="chat-brand__copy">
            <strong>行业智能平台</strong>
            <small>Intelligence Workspace</small>
          </span>
        </a>
        <div className="workspace-switcher">
          <label htmlFor="workspace-select">Workspace</label>
          <select
            disabled={submitting}
            id="workspace-select"
            onChange={(event) => {
              changeWorkspace(event.currentTarget.value);
            }}
            value={workspaceId}
          >
            {currentUser.workspaces.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </div>
        <div className="chat-account">
          <span className="chat-account__identity">
            <strong>{currentUser.user.email}</strong>
            <small>{workspace === undefined ? "" : roleNames[workspace.role]}</small>
          </span>
          <button
            aria-label="账户设置"
            className="icon-button"
            onClick={onOpenSettings}
            title="账户设置"
            type="button"
          >
            <Icon name="settings" />
          </button>
          <button
            aria-label="退出登录"
            className="icon-button"
            onClick={() => void onLogout()}
            title="退出登录"
            type="button"
          >
            <Icon name="user" />
          </button>
        </div>
      </header>

      <div
        className={`chat-grid${traceOpen ? " chat-grid--trace-open" : ""}${sidebarOpen ? " chat-grid--sidebar-open" : ""}`}
      >
        <aside className="conversation-sidebar" aria-label="会话列表">
          <div className="conversation-sidebar__header">
            <button
              className="new-conversation-button"
              disabled={!canCompose || submitting}
              onClick={startNewConversation}
              type="button"
            >
              <Icon name="new" />
              新建会话
            </button>
            <div className="conversation-search">
              <Icon name="search" />
              <input
                aria-label="搜索会话"
                onChange={(event) => {
                  setSearch(event.currentTarget.value);
                }}
                placeholder="搜索会话"
                type="search"
                value={search}
              />
            </div>
          </div>
          <p className="conversation-sidebar__label">最近会话</p>
          {conversationState === "error" ? (
            <div className="sidebar-error" role="alert">
              {conversationError}
              <button
                className="compact-button"
                onClick={() => void loadConversationList(null, false)}
                type="button"
              >
                重新加载
              </button>
            </div>
          ) : null}
          {conversationState === "ready" && filteredConversations.length === 0 ? (
            <p className="conversation-empty">
              还没有匹配的会话。开始一个问题，它会自动出现在这里。
            </p>
          ) : null}
          <ul className="conversation-list">
            {filteredConversations.map((conversation) => (
              <li
                className={`conversation-item${conversation.id === selectedConversationId ? " conversation-item--active" : ""}`}
                key={conversation.id}
              >
                <button
                  className="conversation-item__select"
                  disabled={submitting}
                  onClick={() => {
                    selectConversation(conversation.id);
                  }}
                  type="button"
                >
                  <strong>{conversation.title}</strong>
                  <span>{relativeTime(conversation.updated_at)}</span>
                </button>
              </li>
            ))}
          </ul>
          {conversationCursor === null ? null : (
            <button
              className="load-more-button"
              onClick={() => void loadConversationList(conversationCursor, true)}
              type="button"
            >
              加载更多会话
            </button>
          )}
        </aside>

        <section className="chat-main" aria-labelledby="conversation-title">
          <header className="chat-header">
            <button
              aria-label="打开会话列表"
              className="icon-button mobile-only"
              onClick={() => {
                setSidebarOpen(true);
              }}
              title="打开会话列表"
              type="button"
            >
              <Icon name="menu" />
            </button>
            <div className="chat-header__title">
              {renaming ? (
                <form onSubmit={(event) => void saveRename(event)}>
                  <input
                    aria-label="会话标题"
                    maxLength={160}
                    onBlur={() => {
                      setRenaming(false);
                    }}
                    onChange={(event) => {
                      setRenameTitle(event.currentTarget.value);
                    }}
                    ref={renameInputRef}
                    value={renameTitle}
                  />
                </form>
              ) : (
                <h1 id="conversation-title">
                  {selectedConversation?.title ??
                    (creatingNew ? "新的行业研究会话" : "Agent 工作台")}
                </h1>
              )}
              <div className="chat-header__meta">
                <span className={`live-dot${runIsBusy ? " live-dot--busy" : ""}`} />
                <span>
                  {runIsBusy
                    ? activeRun.connection === "reconnecting"
                      ? "正在恢复已提交事件"
                      : "Runtime 正在执行"
                    : "直接回答 · 无工具调用"}
                </span>
              </div>
            </div>
            <div className="chat-header__actions">
              {selectedConversationId === null || !canCompose ? null : (
                <>
                  <button
                    aria-label="重命名会话"
                    className="icon-button desktop-action"
                    disabled={submitting || runIsBusy}
                    onClick={() => {
                      setRenameTitle(selectedConversation?.title ?? "");
                      setRenaming(true);
                    }}
                    title="重命名会话"
                    type="button"
                  >
                    <Icon name="edit" />
                  </button>
                  <button
                    aria-label="删除会话"
                    className="icon-button desktop-action"
                    disabled={submitting || runIsBusy}
                    onClick={() => {
                      setDeleteDialogOpen(true);
                    }}
                    title="删除会话"
                    type="button"
                  >
                    <Icon name="trash" />
                  </button>
                </>
              )}
              <button
                aria-label="打开运行轨迹"
                className="icon-button"
                disabled={traceRunId === null && activeRun === null}
                onClick={() => {
                  const runId = activeRun?.runId ?? traceRunId;
                  if (runId !== null) void loadTrace(runId, true);
                  else setTraceOpen(true);
                }}
                title="打开运行轨迹"
                type="button"
              >
                <Icon name="bolt" />
              </button>
            </div>
          </header>

          <div className="message-scroll">
            {messagesError === null ? null : (
              <div className="message-load-error" role="alert">
                <span>{messagesError}</span>
                <button className="compact-button" onClick={retryMessageLoad} type="button">
                  重新加载消息
                </button>
              </div>
            )}
            {messagesState === "loading" && messages.length === 0 && activeRun === null ? (
              <div className="chat-skeleton" aria-label="正在加载消息">
                <span />
                <span />
                <span />
              </div>
            ) : messagesState === "error" &&
              messages.length === 0 &&
              activeRun === null ? null : messages.length === 0 && activeRun === null ? (
              <EmptyConversation
                canCompose={canCompose && !submitting}
                onSelectPrompt={(prompt) => {
                  setQuestion(prompt);
                }}
              />
            ) : (
              <div className="message-thread" aria-live="polite">
                {messages.map((message) => (
                  <MessageBubble
                    key={message.id}
                    message={message}
                    onDownload={(fileId) => void downloadAttachment(fileId)}
                    onOpenTrace={(runId) => void loadTrace(runId, true)}
                  />
                ))}
                {activeRun !== null && activeRun.status !== "completed" ? (
                  <StreamingMessage
                    activeRun={activeRun}
                    canRetry={(latestUserMessage(messages)?.attachments.length ?? 0) === 0}
                    onOpenTrace={() => void loadTrace(activeRun.runId, true)}
                    onRetry={retryLastQuestion}
                  />
                ) : null}
                <div ref={threadEndRef} />
              </div>
            )}
          </div>

          <div className="composer-wrap">
            {composerError === null ? null : (
              <div className="composer-error" role="alert">
                {composerError}
              </div>
            )}
            <form className="composer" onSubmit={(event) => void submitQuestion(event)}>
              <textarea
                aria-label="输入问题"
                disabled={!canCompose || submitting}
                maxLength={20_000}
                onChange={(event) => {
                  setQuestion(event.currentTarget.value);
                }}
                onKeyDown={composerKeyDown}
                placeholder={
                  canCompose
                    ? "提出一个行业问题，Shift + Enter 换行…"
                    : "观察者角色只能查看已有会话"
                }
                rows={2}
                value={question}
              />
              {composerAttachments.length === 0 ? null : (
                <div className="composer-attachments">
                  {composerAttachments.map((item) => (
                    <div
                      className={`composer-attachment${item.status === "error" ? " composer-attachment--error" : ""}`}
                      key={item.key}
                      title={item.error}
                    >
                      {item.status === "uploading" ? (
                        <span className="attachment-progress" aria-label="正在上传" />
                      ) : (
                        <Icon name={item.kind} />
                      )}
                      <span className="composer-attachment__name">{item.name}</span>
                      <button
                        aria-label={`移除 ${item.name}`}
                        className="icon-button"
                        disabled={item.status === "uploading"}
                        onClick={() => void removeComposerAttachment(item)}
                        type="button"
                      >
                        <Icon name="close" />
                      </button>
                      {item.error === undefined ? null : (
                        <span className="composer-attachment__error" role="status">
                          {item.error}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              )}
              <div className="composer-toolbar">
                <div className="composer-tools">
                  <input
                    accept=".txt,.md,.png,.jpg,.jpeg,.webp,text/plain,text/markdown,image/png,image/jpeg,image/webp"
                    aria-label="选择附件"
                    hidden
                    multiple
                    onChange={(event) => void addFiles(event)}
                    ref={fileInputRef}
                    type="file"
                  />
                  <button
                    className="attachment-button"
                    disabled={
                      !canCompose ||
                      submitting ||
                      composerAttachments.length >= MAX_ATTACHMENTS ||
                      runIsBusy
                    }
                    onClick={() => fileInputRef.current?.click()}
                    type="button"
                  >
                    <Icon name="attachment" />
                    <span>附件</span>
                  </button>
                  <select
                    aria-label="回答模式"
                    className="mode-select"
                    defaultValue="none"
                    disabled={submitting || runIsBusy}
                  >
                    <option value="none">直接回答</option>
                    <option disabled value="web">
                      Web 搜索 · Day 3
                    </option>
                    <option disabled value="local">
                      知识库 · Day 5
                    </option>
                    <option disabled value="both">
                      混合检索 · Day 5
                    </option>
                  </select>
                </div>
                <div className="composer-actions">
                  {runIsBusy ? (
                    <button
                      className="stop-button"
                      disabled={activeRun.cancelRequested}
                      onClick={() => void requestCancellation()}
                      type="button"
                    >
                      <Icon name="stop" />
                      {activeRun.cancelRequested ? "正在停止" : "停止"}
                    </button>
                  ) : null}
                  <button
                    aria-label="发送问题"
                    className="send-button"
                    disabled={submitDisabled}
                    type="submit"
                  >
                    <Icon name="send" />
                  </button>
                </div>
              </div>
            </form>
            <p className="composer-note">
              Day 2 仅启用直接回答。TXT、Markdown 与静态图片会经过服务端验证后进入模型上下文。
            </p>
          </div>
        </section>

        <TracePanel
          activeRun={panelActiveRun}
          events={traceEvents}
          onClose={() => {
            setTraceOpen(false);
          }}
          trace={trace}
          traceError={traceError}
          onRetry={traceRunId === null ? undefined : () => void loadTrace(traceRunId, true)}
          traceState={traceState}
        />
        <button
          aria-label="关闭侧栏"
          className="panel-backdrop"
          onClick={() => {
            setSidebarOpen(false);
            setTraceOpen(false);
          }}
          type="button"
        />
      </div>

      {deleteDialogOpen ? (
        <div className="dialog-backdrop" role="presentation">
          <section
            aria-labelledby="delete-dialog-title"
            aria-modal="true"
            className="dialog-card"
            role="dialog"
          >
            <h2 id="delete-dialog-title">删除这段会话？</h2>
            <p>会话会从列表中移除。该操作真实调用后端删除接口，不只是在浏览器里隐藏。</p>
            <div className="dialog-actions">
              <button
                onClick={() => {
                  setDeleteDialogOpen(false);
                }}
                type="button"
              >
                取消
              </button>
              <button className="danger-button" onClick={() => void confirmDelete()} type="button">
                确认删除
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </main>
  );
}

function EmptyConversation({
  canCompose,
  onSelectPrompt,
}: {
  readonly canCompose: boolean;
  readonly onSelectPrompt: (prompt: string) => void;
}) {
  return (
    <div className="chat-empty">
      <div className="chat-empty__sigil">
        <Icon name="sparkles" />
      </div>
      <h2>从一个清晰的问题开始。</h2>
      <p>
        这里是 Direct Answer 基线：一次模型调用、没有隐藏工具。每一步、Token
        和停止原因都能在运行轨迹中解释。
      </p>
      <div className="prompt-grid">
        {promptSuggestions.map((prompt) => (
          <button
            className="prompt-card"
            disabled={!canCompose}
            key={prompt}
            onClick={() => {
              onSelectPrompt(prompt);
            }}
            type="button"
          >
            {prompt}
          </button>
        ))}
      </div>
    </div>
  );
}

function MessageBubble({
  message,
  onDownload,
  onOpenTrace,
}: {
  readonly message: ConversationMessage;
  readonly onDownload: (fileId: string) => void;
  readonly onOpenTrace: (runId: string) => void;
}) {
  const isUser = message.role === "user";
  return (
    <article className={`message-row${isUser ? " message-row--user" : ""}`}>
      <div className="message-avatar">
        <Icon name={isUser ? "user" : "sparkles"} />
      </div>
      <div className={`message-card message-card--${isUser ? "user" : "assistant"}`}>
        <div className="message-label">
          {isUser ? "你" : "Agent"}
          {isUser ? <span className="mode-badge">{modeNames[message.search_mode]}</span> : null}
          {!isUser && message.status === "partial" ? (
            <span className="mode-badge">部分回答</span>
          ) : null}
        </div>
        {message.attachments.length === 0 ? null : (
          <div className="message-attachments">
            {message.attachments.map((attachment) => (
              <button
                className="message-attachment"
                key={attachment.file_id}
                onClick={() => {
                  onDownload(attachment.file_id);
                }}
                type="button"
              >
                <Icon name={attachment.kind === "image" ? "image" : "document"} />
                <span>{attachment.original_name}</span>
                <small>{formatBytes(attachment.actual_size)}</small>
                <Icon name="download" />
              </button>
            ))}
          </div>
        )}
        <SafeMarkdown content={message.content_markdown} />
        {isUser ? null : (
          <div className="run-actions">
            <button
              className="compact-button"
              onClick={() => {
                onOpenTrace(message.agent_run_id);
              }}
              type="button"
            >
              <Icon name="bolt" />
              查看运行轨迹
            </button>
          </div>
        )}
      </div>
    </article>
  );
}

function StreamingMessage({
  activeRun,
  canRetry,
  onOpenTrace,
  onRetry,
}: {
  readonly activeRun: ActiveRun;
  readonly canRetry: boolean;
  readonly onOpenTrace: () => void;
  readonly onRetry: () => void;
}) {
  const isRunning = activeRun.status === "running";
  return (
    <article className="message-row">
      <div className="message-avatar">
        <Icon name="sparkles" />
      </div>
      <div className="message-card message-card--assistant">
        <div className="message-label">
          Agent
          <span className="mode-badge">
            {isRunning
              ? activeRun.connection === "reconnecting"
                ? "恢复中"
                : "正在回答"
              : activeRun.status === "cancelled"
                ? "已停止"
                : "未完成"}
          </span>
        </div>
        {activeRun.partialMarkdown ? (
          <>
            <SafeMarkdown content={activeRun.partialMarkdown} />
            {isRunning ? <span className="streaming-caret" aria-label="正在生成" /> : null}
          </>
        ) : isRunning ? (
          <div className="chat-skeleton" aria-label="Agent 正在思考">
            <span />
            <span />
            <span />
          </div>
        ) : null}
        {activeRun.error === null ? null : (
          <div
            className={`run-state-card${activeRun.status === "failed" ? " run-state-card--error" : ""}`}
          >
            <Icon name={activeRun.status === "cancelled" ? "stop" : "refresh"} />
            <div>
              {activeRun.error}
              <div className="run-actions">
                <button className="compact-button" onClick={onOpenTrace} type="button">
                  <Icon name="bolt" />
                  查看原因
                </button>
                {isRunning ? null : (
                  <button
                    className="compact-button"
                    disabled={!canRetry}
                    onClick={onRetry}
                    type="button"
                  >
                    <Icon name="refresh" />
                    重新提问
                  </button>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    </article>
  );
}

function TracePanel({
  activeRun,
  events,
  onClose,
  onRetry,
  trace,
  traceError,
  traceState,
}: {
  readonly activeRun: ActiveRun | null;
  readonly events: readonly (AgentStreamEvent | AgentTrace["events"][number])[];
  readonly onClose: () => void;
  readonly onRetry: (() => void) | undefined;
  readonly trace: AgentTrace | null;
  readonly traceError: string | null;
  readonly traceState: LoadState;
}) {
  const manifest = trace?.context_manifests[0];
  const status = activeRun?.status ?? trace?.run.status;
  return (
    <aside className="trace-panel" aria-label="Agent 运行轨迹">
      <header className="trace-panel__header">
        <div>
          <h2>运行轨迹</h2>
          <p>只展示已提交的安全元数据</p>
        </div>
        <button
          aria-label="关闭运行轨迹"
          className="icon-button trace-panel__close"
          onClick={onClose}
          title="关闭运行轨迹"
          type="button"
        >
          <Icon name="close" />
        </button>
      </header>
      <div className="trace-panel__body">
        {traceState === "loading" ? (
          <div className="chat-skeleton" aria-label="正在加载运行轨迹">
            <span />
            <span />
            <span />
          </div>
        ) : traceError !== null && trace === null && activeRun === null ? (
          <div className="run-state-card run-state-card--error" role="alert">
            <Icon name="refresh" />
            <div>
              {traceError}
              {onRetry === undefined ? null : (
                <div className="run-actions">
                  <button className="compact-button" onClick={onRetry} type="button">
                    重新加载轨迹
                  </button>
                </div>
              )}
            </div>
          </div>
        ) : trace === null && activeRun === null ? (
          <div className="trace-empty">
            <Icon name="bolt" />
            <span>选择一次回答后，这里会显示 Runtime 步骤、上下文组成和用量。</span>
          </div>
        ) : (
          <>
            {traceError === null ? null : (
              <div className="run-state-card run-state-card--error" role="alert">
                {traceError}
              </div>
            )}
            <div className="trace-status">
              <div>
                <strong>
                  {trace?.run.run_type === "direct_answer" ? "Direct Answer L0" : "Agent Run"}
                </strong>
                <span>{trace?.run.runtime_version ?? "Runtime 正在记录事件"}</span>
              </div>
              <span className={`status-pill status-pill--${status ?? "running"}`}>
                {runStatusNames[status ?? "running"] ?? status ?? "运行中"}
              </span>
            </div>
            <div className="trace-metrics">
              <div className="trace-metric">
                <strong>{trace?.run.usage.input_tokens ?? "—"}</strong>
                <span>输入 Token</span>
              </div>
              <div className="trace-metric">
                <strong>{trace?.run.usage.output_tokens ?? "—"}</strong>
                <span>输出 Token</span>
              </div>
              <div className="trace-metric">
                <strong>{trace === null ? "—" : formatCost(trace.run.usage.cost_micro_usd)}</strong>
                <span>模型费用</span>
              </div>
            </div>
            {trace === null ? null : (
              <section className="trace-section">
                <div className="trace-section__title">
                  <span>步骤</span>
                  <span>{trace.steps.length}</span>
                </div>
                <ol className="step-list">
                  {trace.steps.map((step) => (
                    <li className="step-item" key={step.step_id}>
                      <strong>
                        {step.sequence}.{" "}
                        {step.kind === "model"
                          ? "模型调用"
                          : step.kind === "final"
                            ? "最终输出"
                            : step.kind}
                      </strong>
                      <span>
                        {step.status} · {step.usage.input_tokens + step.usage.output_tokens} Token
                      </span>
                    </li>
                  ))}
                </ol>
              </section>
            )}
            {manifest === undefined ? null : (
              <section className="trace-section">
                <div className="trace-section__title">
                  <span>上下文组成</span>
                  <span>{manifest.compiler_version}</span>
                </div>
                <ol className="source-list">
                  {manifest.sources.map((source) => (
                    <li
                      className={`source-item${source.included ? "" : " source-item--excluded"}`}
                      key={`${String(source.ordinal)}-${source.source_id}`}
                    >
                      <strong>{sourceNames[source.source_kind] ?? source.source_kind}</strong>
                      <span>
                        {source.included
                          ? `${String(source.estimated_token_count)} Token · 已送入模型`
                          : source.decision_reason}
                      </span>
                    </li>
                  ))}
                </ol>
              </section>
            )}
            <section className="trace-section">
              <div className="trace-section__title">
                <span>已提交事件</span>
                <span>{events.length}</span>
              </div>
              <ol className="event-list">
                {events.map((event) => {
                  const type = "event_type" in event ? event.event_type : event.type;
                  return (
                    <li className="event-item" key={`${String(event.sequence)}-${type}`}>
                      <strong>{eventNames[type] ?? type}</strong>
                      <span>sequence {event.sequence}</span>
                    </li>
                  );
                })}
              </ol>
            </section>
            {trace === null ? null : (
              <section className="trace-section">
                <div className="trace-section__title">停止原因</div>
                <div className="trace-status">
                  <div>
                    <strong>{trace.run.stop_reason ?? "尚未结束"}</strong>
                    <span>Trace ID · {trace.run.trace_id}</span>
                  </div>
                </div>
              </section>
            )}
          </>
        )}
      </div>
    </aside>
  );
}
