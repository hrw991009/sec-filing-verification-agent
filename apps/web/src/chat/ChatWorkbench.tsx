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

import { DataExplorerWorkspace } from "../data-explorer/DataExplorerWorkspace";
import { IndustryWorkspace } from "../industry/IndustryWorkspace";
import { getIndustryPreference, listIndustries, type Industry } from "../industry/industry-api";

import {
  cancelRun,
  deleteConversation,
  deleteFile,
  followAgentRunEvents,
  getAgentTrace,
  getDownloadUrl,
  listConversations,
  renameConversation,
  startTurn,
  uploadFile,
  type AgentTrace,
  type ConversationMessage,
  type ConversationSummary,
  type FileSnapshot,
} from "./chat-api";
import "./chat.css";
import { ChatComposer } from "./ChatComposer";
import { ChatTopbar, ConversationHeader, type WorkbenchView } from "./ChatHeader";
import { ChatMessages } from "./ChatMessages";
import {
  attachmentKind,
  idempotencyKey,
  isTerminalEvent,
  MAX_ATTACHMENTS,
  newestUnfinishedRun,
  payloadString,
  publicError,
  runFailureMessage,
  terminalStatus,
  type ActiveRun,
  type ComposerAttachment,
  type ChatWorkbenchProps,
  type LoadState,
  type RunStatus,
} from "./chat-workbench-model";
import { ConversationSidebar } from "./ConversationSidebar";
import { DeleteConversationDialog } from "./DeleteConversationDialog";
import { pollAgentRunTerminal, type ConfirmedAgentRunTerminal } from "./agent-run-status";
import { TracePanel } from "./TracePanel";
import { useAllConversationMessages } from "./useAllConversationMessages";

export function ChatWorkbench({ currentUser, onLogout, onOpenSettings }: ChatWorkbenchProps) {
  const initialWorkspaceId = currentUser.workspaces[0]?.id ?? "";
  const [workspaceId, setWorkspaceId] = useState(initialWorkspaceId);
  const [view, setView] = useState<WorkbenchView>("chat");
  const [searchMode, setSearchMode] = useState<"none" | "web">("none");
  const [industries, setIndustries] = useState<Industry[]>([]);
  const [industryId, setIndustryId] = useState<string | null>(null);
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

  const loadAllMessages = useAllConversationMessages(workspaceId);

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
            const snapshotMarkdown =
              event.type === "stream.snapshot" ? payloadString(event, "content_markdown") : null;
            terminal = terminalStatus(event);
            const stopReason = payloadString(event, "stop_reason");
            return {
              ...current,
              cancelRequested: terminal === null ? current.cancelRequested : false,
              error:
                terminal === "failed" || terminal === "cancelled"
                  ? runFailureMessage(stopReason)
                  : terminal === "completed"
                    ? null
                    : current.error,
              events: [...current.events, event],
              partialMarkdown:
                snapshotMarkdown ?? finalMarkdown ?? `${current.partialMarkdown}${delta ?? ""}`,
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
    let active = true;
    setIndustries([]);
    setIndustryId(null);
    if (!workspaceId) {
      return () => {
        active = false;
      };
    }
    void Promise.all([listIndustries(), getIndustryPreference(workspaceId)])
      .then(([nextIndustries, preference]) => {
        if (!active || workspaceIdRef.current !== workspaceId) return;
        setIndustries(nextIndustries);
        setIndustryId(preference?.industry.id ?? nextIndustries[0]?.id ?? null);
      })
      .catch((caught: unknown) => {
        if (active && workspaceIdRef.current === workspaceId) {
          setComposerError(`行业上下文加载失败：${publicError(caught)}`);
        }
      });
    return () => {
      active = false;
    };
  }, [workspaceId]);

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
      (searchMode === "web" && industryId === null) ||
      readyAttachments.length !== composerAttachments.length
    ) {
      return;
    }
    const fingerprint = JSON.stringify({
      attachmentIds: readyAttachments.map((item) => item.snapshot.id),
      conversationId: selectedConversationId,
      industryId: searchMode === "web" ? industryId : null,
      question: normalizedQuestion,
      searchMode,
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
          industry_id: searchMode === "web" ? industryId : null,
          knowledge_base_ids: [],
          mode: searchMode,
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

  function retryLastQuestion(previousQuestion: string): void {
    if (activeRun?.status === "running") return;
    if (!previousQuestion.trim()) return;
    setQuestion(previousQuestion);
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
    !canCompose ||
    !question.trim() ||
    submitting ||
    runIsBusy ||
    !attachmentsReady ||
    (searchMode === "web" && industryId === null);
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
      <ChatTopbar
        currentUser={currentUser}
        onChangeView={(nextView) => {
          setView(nextView);
          setSidebarOpen(false);
          setTraceOpen(false);
        }}
        onChangeWorkspace={changeWorkspace}
        onLogout={onLogout}
        onOpenSettings={onOpenSettings}
        submitting={submitting}
        view={view}
        workspaceId={workspaceId}
      />

      {view === "chat" ? (
        <div
          className={`chat-grid${traceOpen ? " chat-grid--trace-open" : ""}${sidebarOpen ? " chat-grid--sidebar-open" : ""}`}
        >
          <ConversationSidebar
            canCompose={canCompose}
            conversationCursor={conversationCursor}
            conversationError={conversationError}
            conversationState={conversationState}
            conversations={filteredConversations}
            onChangeSearch={setSearch}
            onLoadMore={(cursor) => void loadConversationList(cursor, true)}
            onReload={() => void loadConversationList(null, false)}
            onSelectConversation={selectConversation}
            onStartNewConversation={startNewConversation}
            search={search}
            selectedConversationId={selectedConversationId}
            submitting={submitting}
          />

          <section className="chat-main" aria-labelledby="conversation-title">
            <ConversationHeader
              activeRun={activeRun}
              canCompose={canCompose}
              creatingNew={creatingNew}
              onBeginRename={() => {
                setRenameTitle(selectedConversation?.title ?? "");
                setRenaming(true);
              }}
              onChangeRenameTitle={setRenameTitle}
              onDelete={() => {
                setDeleteDialogOpen(true);
              }}
              onOpenSidebar={() => {
                setSidebarOpen(true);
              }}
              onOpenTrace={(runId) => {
                if (runId !== null) void loadTrace(runId, true);
                else setTraceOpen(true);
              }}
              onSaveRename={(event) => void saveRename(event)}
              onStopRenaming={() => {
                setRenaming(false);
              }}
              renameInputRef={renameInputRef}
              renameTitle={renameTitle}
              renaming={renaming}
              runIsBusy={runIsBusy}
              searchMode={searchMode}
              selectedConversation={selectedConversation}
              selectedConversationId={selectedConversationId}
              submitting={submitting}
              traceRunId={traceRunId}
            />

            <ChatMessages
              activeRun={activeRun}
              canCompose={canCompose && !submitting}
              messages={messages}
              messagesError={messagesError}
              messagesState={messagesState}
              onDownload={(fileId) => void downloadAttachment(fileId)}
              onOpenTrace={(runId) => void loadTrace(runId, true)}
              onRetryLastQuestion={retryLastQuestion}
              onRetryMessageLoad={retryMessageLoad}
              onSelectPrompt={setQuestion}
              threadEndRef={threadEndRef}
            />

            <ChatComposer
              activeRun={activeRun}
              attachments={composerAttachments}
              canCompose={canCompose}
              error={composerError}
              fileInputRef={fileInputRef}
              onAddFiles={(event) => void addFiles(event)}
              onChangeQuestion={setQuestion}
              onChangeSearchMode={setSearchMode}
              onKeyDown={composerKeyDown}
              onRemoveAttachment={(attachment) => void removeComposerAttachment(attachment)}
              onRequestCancellation={() => void requestCancellation()}
              onSubmit={(event) => void submitQuestion(event)}
              question={question}
              runIsBusy={runIsBusy}
              searchMode={searchMode}
              selectedIndustryName={
                industries.find((industry) => industry.id === industryId)?.name ?? null
              }
              submitDisabled={submitDisabled}
              submitting={submitting}
            />
          </section>

          <TracePanel
            activeRun={panelActiveRun}
            events={traceEvents}
            onClose={() => {
              setTraceOpen(false);
            }}
            onRetry={traceRunId === null ? undefined : () => void loadTrace(traceRunId, true)}
            trace={trace}
            traceError={traceError}
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
      ) : view === "industry" ? (
        <IndustryWorkspace
          canManage={canCompose}
          industries={industries}
          onSelectIndustry={(nextIndustryId) => {
            setIndustryId(nextIndustryId);
          }}
          selectedIndustryId={industryId}
          workspaceId={workspaceId}
        />
      ) : (
        <DataExplorerWorkspace key={workspaceId} canManage={canCompose} workspaceId={workspaceId} />
      )}

      <DeleteConversationDialog
        onCancel={() => {
          setDeleteDialogOpen(false);
        }}
        onConfirm={() => void confirmDelete()}
        open={deleteDialogOpen}
      />
    </main>
  );
}
