import { ApiProblem } from "../api/api";
import type { CurrentUser } from "../auth/auth-context";
import type {
  AgentStreamConnectionState,
  AgentStreamEvent,
  ConversationMessage,
  FileSnapshot,
} from "./chat-api";

export const MAX_ATTACHMENTS = 4;
export const MESSAGE_PAGE_SIZE = 100;

export type LoadState = "error" | "loading" | "ready";
export type RunStatus = "cancelled" | "completed" | "failed" | "running";

export interface ComposerAttachment {
  readonly key: string;
  readonly workspaceId: string;
  readonly name: string;
  readonly kind: "document" | "image";
  readonly status: "error" | "ready" | "uploading";
  readonly snapshot?: FileSnapshot;
  readonly error?: string;
}

export interface ActiveRun {
  readonly conversationId: string;
  readonly runId: string;
  readonly status: RunStatus;
  readonly connection: AgentStreamConnectionState;
  readonly partialMarkdown: string;
  readonly events: readonly AgentStreamEvent[];
  readonly error: string | null;
  readonly cancelRequested: boolean;
}

export interface ChatWorkbenchProps {
  readonly currentUser: CurrentUser;
  readonly onLogout: () => Promise<void>;
  readonly onOpenSettings: () => void;
}

export const promptSuggestions = [
  "用三点概括新能源汽车供应链目前最值得关注的变化。",
  "给我一个评估新行业机会时可以复用的分析框架。",
  "解释一家公司毛利率下降时应该优先检查哪些因素。",
  "把我上传的材料整理成清晰的管理层摘要。",
] as const;

export const modeNames = {
  both: "Web + 知识库",
  local: "知识库",
  none: "直接回答",
  web: "Web 搜索",
} as const;

export const roleNames = {
  admin: "管理员",
  member: "成员",
  owner: "所有者",
  viewer: "观察者",
} as const;

export const runStatusNames: Record<string, string> = {
  cancelled: "已停止",
  completed: "已完成",
  failed: "失败",
  queued: "排队中",
  running: "运行中",
  paused: "已暂停",
};

export const eventNames: Record<string, string> = {
  "agent.model.completed": "模型响应完成",
  "agent.model.delta": "收到流式片段",
  "agent.model.started": "模型调用开始",
  "agent.research.node_completed": "Research 节点完成",
  "agent.research.node_failed": "Research 节点失败",
  "agent.research.node_started": "Research 节点开始",
  "agent.run.cancelled": "运行已取消",
  "agent.run.completed": "运行已完成",
  "agent.run.failed": "运行失败",
  "agent.run.queued": "运行已排队",
  "agent.run.resumed": "运行已恢复",
  "agent.run.started": "运行已开始",
  "agent.step.completed": "步骤完成",
  "agent.step.failed": "步骤失败",
  "agent.step.started": "步骤开始",
  "agent.tool.approval_required": "工具调用等待批准",
  "agent.tool.cancelled": "工具调用已取消",
  "agent.tool.completed": "工具调用完成",
  "agent.tool.denied": "工具调用被拒绝",
  "agent.tool.failed": "工具调用失败",
  "agent.tool.requested": "工具调用已请求",
  "agent.tool.started": "工具调用开始",
  "stream.snapshot": "已恢复运行快照",
};

export const sourceNames: Record<string, string> = {
  attachment: "附件",
  conversation_summary: "会话摘要",
  long_term_memory: "长期 Memory",
  runtime_context_projection: "Workspace 安全投影",
  system_instructions: "系统指令",
  short_term_memory: "当前 Thread Memory",
  tool_observation: "工具观察结果",
  user_question: "当前问题",
};

export function publicError(error: unknown): string {
  if (error instanceof ApiProblem) {
    return `${error.message}${error.traceId === null ? "" : `（追踪号 ${error.traceId}）`}`;
  }
  return error instanceof Error && error.message ? error.message : "服务暂时不可用，请稍后重试。";
}

export function relativeTime(value: string): string {
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

export function formatBytes(value: number): string {
  if (value < 1_000) return `${String(value)} B`;
  if (value < 1_000_000) return `${(value / 1_000).toFixed(1)} KB`;
  return `${(value / 1_000_000).toFixed(1)} MB`;
}

export function formatCost(microUsd: number): string {
  if (microUsd === 0) return "$0";
  return `$${(microUsd / 1_000_000).toFixed(6)}`;
}

export function payloadString(event: AgentStreamEvent, field: string): string | null {
  const value = event.payload[field];
  return typeof value === "string" ? value : null;
}

export function isTerminalEvent(event: AgentStreamEvent): boolean {
  return terminalStatus(event) !== null;
}

export function terminalStatus(event: AgentStreamEvent): RunStatus | null {
  if (event.type === "agent.run.completed") return "completed";
  if (event.type === "agent.run.cancelled") return "cancelled";
  if (event.type === "agent.run.failed") return "failed";
  if (event.type === "stream.snapshot" && event.payload.terminal === true) {
    const status = event.payload.status;
    if (status === "completed" || status === "cancelled" || status === "failed") return status;
  }
  return null;
}

export function runFailureMessage(reason: string | null): string {
  if (reason === "provider_timeout") return "模型响应超时。你的问题已经保存，可以重新提问。";
  if (reason === "provider_rate_limited")
    return "模型服务当前繁忙。你的问题已经保存，可以稍后重新提问。";
  if (reason === "cancelled") return "本次回答已停止，已经生成的片段仍然保留。";
  if (reason === "incomplete_provider_response") return "模型连接在完成前中断，已保留收到的内容。";
  return `本次回答未完成${reason === null ? "" : `（${reason}）`}。你的问题没有丢失。`;
}

export function newestUnfinishedRun(messages: readonly ConversationMessage[]): string | null {
  const latest = messages.at(-1);
  if (latest === undefined) return null;
  const hasPersistedTerminalAnswer = messages.some(
    (message) =>
      message.agent_run_id === latest.agent_run_id &&
      message.role === "assistant" &&
      (message.status === "final" || message.status === "partial"),
  );
  return hasPersistedTerminalAnswer ? null : latest.agent_run_id;
}

/**
 * A persisted assistant row replaces the temporary streaming bubble for that
 * Run. `partial` is written only after a failed or cancelled Run has reached a
 * terminal state, so keeping both would render the same fragment twice.
 */
export function hasPersistedAssistantMessage(
  messages: readonly ConversationMessage[],
  runId: string,
): boolean {
  return messages.some((message) => message.agent_run_id === runId && message.role === "assistant");
}

export function userMessageForRun(
  messages: readonly ConversationMessage[],
  runId: string,
): ConversationMessage | null {
  return (
    messages.findLast((message) => message.agent_run_id === runId && message.role === "user") ??
    null
  );
}

export function latestUserMessage(
  messages: readonly ConversationMessage[],
): ConversationMessage | null {
  return messages.findLast((message) => message.role === "user") ?? null;
}

export function idempotencyKey(): string {
  return `web-${crypto.randomUUID()}`;
}

export function attachmentKind(file: File): "document" | "image" {
  return file.type.startsWith("image/") || /\.(?:jpe?g|png|webp)$/iu.test(file.name)
    ? "image"
    : "document";
}
