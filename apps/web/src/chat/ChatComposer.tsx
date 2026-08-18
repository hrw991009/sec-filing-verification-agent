import type { ChangeEvent, KeyboardEvent, RefObject, SubmitEvent } from "react";

import type { ActiveRun, ComposerAttachment } from "./chat-workbench-model";
import { MAX_ATTACHMENTS } from "./chat-workbench-model";
import { Icon } from "./icons";

interface ChatComposerProps {
  readonly activeRun: ActiveRun | null;
  readonly attachments: readonly ComposerAttachment[];
  readonly canCompose: boolean;
  readonly error: string | null;
  readonly fileInputRef: RefObject<HTMLInputElement | null>;
  readonly question: string;
  readonly searchMode: "none" | "web";
  readonly selectedIndustryName: string | null;
  readonly runIsBusy: boolean;
  readonly submitDisabled: boolean;
  readonly submitting: boolean;
  readonly onAddFiles: (event: ChangeEvent<HTMLInputElement>) => void;
  readonly onChangeQuestion: (question: string) => void;
  readonly onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
  readonly onRemoveAttachment: (attachment: ComposerAttachment) => void;
  readonly onRequestCancellation: () => void;
  readonly onChangeSearchMode: (mode: "none" | "web") => void;
  readonly onSubmit: (event: SubmitEvent<HTMLFormElement>) => void;
}

export function ChatComposer({
  activeRun,
  attachments,
  canCompose,
  error,
  fileInputRef,
  onAddFiles,
  onChangeQuestion,
  onChangeSearchMode,
  onKeyDown,
  onRemoveAttachment,
  onRequestCancellation,
  onSubmit,
  question,
  searchMode,
  selectedIndustryName,
  runIsBusy,
  submitDisabled,
  submitting,
}: ChatComposerProps) {
  return (
    <div className="composer-wrap">
      {error === null ? null : (
        <div className="composer-error" role="alert">
          {error}
        </div>
      )}
      <form className="composer" onSubmit={onSubmit}>
        <textarea
          aria-label="输入问题"
          disabled={!canCompose || submitting}
          maxLength={20_000}
          onChange={(event) => {
            onChangeQuestion(event.currentTarget.value);
          }}
          onKeyDown={onKeyDown}
          placeholder={
            canCompose ? "提出一个行业问题，Shift + Enter 换行…" : "观察者角色只能查看已有会话"
          }
          rows={2}
          value={question}
        />
        {attachments.length === 0 ? null : (
          <div className="composer-attachments">
            {attachments.map((item) => (
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
                  onClick={() => {
                    onRemoveAttachment(item);
                  }}
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
              onChange={onAddFiles}
              ref={fileInputRef}
              type="file"
            />
            <button
              className="attachment-button"
              disabled={
                !canCompose || submitting || attachments.length >= MAX_ATTACHMENTS || runIsBusy
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
              disabled={submitting || runIsBusy}
              onChange={(event) => {
                const mode = event.currentTarget.value;
                if (mode === "none" || mode === "web") onChangeSearchMode(mode);
              }}
              value={searchMode}
            >
              <option value="none">直接回答</option>
              <option value="web">Web 搜索 · L2</option>
              <option disabled value="local">
                知识库 · Day 5
              </option>
              <option disabled value="both">
                混合检索 · Day 5
              </option>
            </select>
          </div>
          <div className="composer-actions">
            {runIsBusy && activeRun !== null ? (
              <button
                aria-live="polite"
                className="stop-button"
                disabled={activeRun.cancelRequested}
                onClick={onRequestCancellation}
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
        {searchMode === "web"
          ? `当前行业：${selectedIndustryName ?? "尚未选择"}。模型只能调用 allowlist 内的只读 Web Tool。`
          : "直接回答不调用 Tool。TXT、Markdown 与静态图片仍会经过服务端验证。"}
      </p>
    </div>
  );
}
