import type { ConversationSummary } from "./chat-api";
import type { LoadState } from "./chat-workbench-model";
import { relativeTime } from "./chat-workbench-model";
import { Icon } from "./icons";

interface ConversationSidebarProps {
  readonly canCompose: boolean;
  readonly conversationCursor: string | null;
  readonly conversationError: string | null;
  readonly conversationState: LoadState;
  readonly conversations: readonly ConversationSummary[];
  readonly search: string;
  readonly selectedConversationId: string | null;
  readonly submitting: boolean;
  readonly onChangeSearch: (search: string) => void;
  readonly onLoadMore: (cursor: string) => void;
  readonly onReload: () => void;
  readonly onSelectConversation: (conversationId: string) => void;
  readonly onStartNewConversation: () => void;
}

export function ConversationSidebar({
  canCompose,
  conversationCursor,
  conversationError,
  conversationState,
  conversations,
  onChangeSearch,
  onLoadMore,
  onReload,
  onSelectConversation,
  onStartNewConversation,
  search,
  selectedConversationId,
  submitting,
}: ConversationSidebarProps) {
  return (
    <aside className="conversation-sidebar" aria-label="会话列表">
      <div className="conversation-sidebar__header">
        <button
          className="new-conversation-button"
          disabled={!canCompose || submitting}
          onClick={onStartNewConversation}
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
              onChangeSearch(event.currentTarget.value);
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
          <button className="compact-button" onClick={onReload} type="button">
            重新加载
          </button>
        </div>
      ) : null}
      {conversationState === "ready" && conversations.length === 0 ? (
        <p className="conversation-empty">还没有匹配的会话。开始一个问题，它会自动出现在这里。</p>
      ) : null}
      <ul className="conversation-list">
        {conversations.map((conversation) => {
          const selected = conversation.id === selectedConversationId;
          return (
            <li
              className={`conversation-item${selected ? " conversation-item--active" : ""}`}
              key={conversation.id}
            >
              <button
                aria-current={selected ? "page" : undefined}
                className="conversation-item__select"
                disabled={submitting}
                onClick={() => {
                  onSelectConversation(conversation.id);
                }}
                type="button"
              >
                <strong>{conversation.title}</strong>
                <span>{relativeTime(conversation.updated_at)}</span>
              </button>
            </li>
          );
        })}
      </ul>
      {conversationCursor === null ? null : (
        <button
          className="load-more-button"
          onClick={() => {
            onLoadMore(conversationCursor);
          }}
          type="button"
        >
          加载更多会话
        </button>
      )}
    </aside>
  );
}
