import type { RefObject, SubmitEvent } from "react";

import type { CurrentUser } from "../auth/auth-context";
import type { ConversationSummary } from "./chat-api";
import type { ActiveRun } from "./chat-workbench-model";
import { roleNames } from "./chat-workbench-model";
import { Icon } from "./icons";

export type WorkbenchView = "chat" | "data" | "industry" | "memory";

interface ChatTopbarProps {
  readonly currentUser: CurrentUser;
  readonly submitting: boolean;
  readonly workspaceId: string;
  readonly view: WorkbenchView;
  readonly onChangeWorkspace: (workspaceId: string) => void;
  readonly onLogout: () => Promise<void>;
  readonly onOpenSettings: () => void;
  readonly onChangeView: (view: WorkbenchView) => void;
}

export function ChatTopbar({
  currentUser,
  onChangeWorkspace,
  onChangeView,
  onLogout,
  onOpenSettings,
  submitting,
  view,
  workspaceId,
}: ChatTopbarProps) {
  const workspace = currentUser.workspaces.find((candidate) => candidate.id === workspaceId);
  return (
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
            onChangeWorkspace(event.currentTarget.value);
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
      <nav className="product-nav" aria-label="主要功能">
        {(
          [
            ["chat", "Agent"],
            ["industry", "行业情报"],
            ["data", "数据库"],
            ["memory", "Memory"],
          ] as const
        ).map(([target, label]) => (
          <button
            aria-current={view === target ? "page" : undefined}
            className={
              view === target ? "product-nav__item product-nav__item--active" : "product-nav__item"
            }
            key={target}
            onClick={() => {
              onChangeView(target);
            }}
            type="button"
          >
            {label}
          </button>
        ))}
      </nav>
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
  );
}

interface ConversationHeaderProps {
  readonly activeRun: ActiveRun | null;
  readonly canCompose: boolean;
  readonly creatingNew: boolean;
  readonly renameInputRef: RefObject<HTMLInputElement | null>;
  readonly renameTitle: string;
  readonly renaming: boolean;
  readonly runIsBusy: boolean;
  readonly selectedConversation: ConversationSummary | undefined;
  readonly selectedConversationId: string | null;
  readonly submitting: boolean;
  readonly traceRunId: string | null;
  readonly searchMode: "none" | "web";
  readonly onBeginRename: () => void;
  readonly onChangeRenameTitle: (title: string) => void;
  readonly onDelete: () => void;
  readonly onOpenSidebar: () => void;
  readonly onOpenTrace: (runId: string | null) => void;
  readonly onSaveRename: (event: SubmitEvent<HTMLFormElement>) => void;
  readonly onStopRenaming: () => void;
}

export function ConversationHeader({
  activeRun,
  canCompose,
  creatingNew,
  onBeginRename,
  onChangeRenameTitle,
  onDelete,
  onOpenSidebar,
  onOpenTrace,
  onSaveRename,
  onStopRenaming,
  renameInputRef,
  renameTitle,
  renaming,
  runIsBusy,
  selectedConversation,
  selectedConversationId,
  submitting,
  traceRunId,
  searchMode,
}: ConversationHeaderProps) {
  return (
    <header className="chat-header">
      <button
        aria-label="打开会话列表"
        className="icon-button mobile-only"
        onClick={onOpenSidebar}
        title="打开会话列表"
        type="button"
      >
        <Icon name="menu" />
      </button>
      <div className="chat-header__title">
        {renaming ? (
          <form onSubmit={onSaveRename}>
            <input
              aria-label="会话标题"
              maxLength={160}
              onBlur={onStopRenaming}
              onChange={(event) => {
                onChangeRenameTitle(event.currentTarget.value);
              }}
              ref={renameInputRef}
              value={renameTitle}
            />
          </form>
        ) : (
          <h1 id="conversation-title">
            {selectedConversation?.title ?? (creatingNew ? "新的行业研究会话" : "Agent 工作台")}
          </h1>
        )}
        <div className="chat-header__meta">
          <span className={`live-dot${runIsBusy ? " live-dot--busy" : ""}`} />
          <span>
            {runIsBusy
              ? activeRun?.connection === "reconnecting"
                ? "正在恢复已提交事件"
                : "Runtime 正在执行"
              : searchMode === "web"
                ? "Web Tool · L2 有界循环"
                : "直接回答 · L0"}
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
              onClick={onBeginRename}
              title="重命名会话"
              type="button"
            >
              <Icon name="edit" />
            </button>
            <button
              aria-label="删除会话"
              className="icon-button desktop-action"
              disabled={submitting || runIsBusy}
              onClick={onDelete}
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
            onOpenTrace(activeRun?.runId ?? traceRunId);
          }}
          title="打开运行轨迹"
          type="button"
        >
          <Icon name="bolt" />
        </button>
      </div>
    </header>
  );
}
