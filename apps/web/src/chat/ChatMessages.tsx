import type { RefObject } from "react";

import type { ConversationMessage } from "./chat-api";
import type { ActiveRun, LoadState } from "./chat-workbench-model";
import {
  formatBytes,
  hasPersistedAssistantMessage,
  latestUserMessage,
  modeNames,
  promptSuggestions,
  userMessageForRun,
} from "./chat-workbench-model";
import { Icon } from "./icons";
import { SafeMarkdown } from "./SafeMarkdown";

interface ChatMessagesProps {
  readonly activeRun: ActiveRun | null;
  readonly canCompose: boolean;
  readonly messages: readonly ConversationMessage[];
  readonly messagesError: string | null;
  readonly messagesState: LoadState;
  readonly threadEndRef: RefObject<HTMLDivElement | null>;
  readonly onDownload: (fileId: string) => void;
  readonly onOpenTrace: (runId: string) => void;
  readonly onRetryLastQuestion: (question: string) => void;
  readonly onRetryMessageLoad: () => void;
  readonly onSelectPrompt: (prompt: string) => void;
}

export function ChatMessages({
  activeRun,
  canCompose,
  messages,
  messagesError,
  messagesState,
  onDownload,
  onOpenTrace,
  onRetryLastQuestion,
  onRetryMessageLoad,
  onSelectPrompt,
  threadEndRef,
}: ChatMessagesProps) {
  const retryCandidate = latestUserMessage(messages);
  const canRetry = retryCandidate !== null && retryCandidate.attachments.length === 0;
  const activeRunHasPersistedAnswer =
    activeRun !== null && hasPersistedAssistantMessage(messages, activeRun.runId);

  return (
    <div className="message-scroll" aria-busy={messagesState === "loading"}>
      {messagesError === null ? null : (
        <div className="message-load-error" role="alert">
          <span>{messagesError}</span>
          <button className="compact-button" onClick={onRetryMessageLoad} type="button">
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
        <EmptyConversation canCompose={canCompose} onSelectPrompt={onSelectPrompt} />
      ) : (
        <div className="message-thread" aria-live="polite">
          {messages.map((message) => (
            <MessageBubble
              canRetry={canCompose && activeRun?.status !== "running"}
              key={message.id}
              message={message}
              onDownload={onDownload}
              onOpenTrace={onOpenTrace}
              onRetryLastQuestion={onRetryLastQuestion}
              retryUserMessage={
                message.role === "assistant" && message.status === "partial"
                  ? userMessageForRun(messages, message.agent_run_id)
                  : null
              }
            />
          ))}
          {activeRun !== null &&
          activeRun.status !== "completed" &&
          !activeRunHasPersistedAnswer ? (
            <StreamingMessage
              activeRun={activeRun}
              canRetry={canRetry}
              onOpenTrace={() => {
                onOpenTrace(activeRun.runId);
              }}
              onRetry={() => {
                if (canRetry) {
                  onRetryLastQuestion(retryCandidate.content_markdown);
                }
              }}
            />
          ) : null}
          <div ref={threadEndRef} />
        </div>
      )}
    </div>
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
  canRetry,
  message,
  onDownload,
  onOpenTrace,
  onRetryLastQuestion,
  retryUserMessage,
}: {
  readonly canRetry: boolean;
  readonly message: ConversationMessage;
  readonly onDownload: (fileId: string) => void;
  readonly onOpenTrace: (runId: string) => void;
  readonly onRetryLastQuestion: (question: string) => void;
  readonly retryUserMessage: ConversationMessage | null;
}) {
  const isUser = message.role === "user";
  const retryQuestion =
    retryUserMessage?.attachments.length === 0 ? retryUserMessage.content_markdown : null;
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
        {!isUser && message.status === "partial" ? (
          <div className="run-state-card" role="status">
            <Icon name="stop" />
            <div>这次回答未完整结束，已保留已提交片段；查看运行轨迹了解原因。</div>
          </div>
        ) : null}
        <div className="run-actions">
          <button
            className="compact-button"
            onClick={() => {
              onOpenTrace(message.agent_run_id);
            }}
            type="button"
          >
            <Icon name="bolt" />
            {isUser ? "查看本轮运行轨迹" : "查看运行轨迹"}
          </button>
          {!isUser && message.status === "partial" ? (
            <button
              className="compact-button"
              disabled={!canRetry || retryQuestion === null}
              onClick={() => {
                if (retryQuestion !== null) onRetryLastQuestion(retryQuestion);
              }}
              type="button"
            >
              <Icon name="refresh" />
              重新提问
            </button>
          ) : null}
        </div>
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
