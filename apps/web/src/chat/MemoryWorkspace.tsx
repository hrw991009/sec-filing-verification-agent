import { useCallback, useEffect, useState, type SyntheticEvent } from "react";

import {
  deleteMemory,
  disableMemory,
  enableMemory,
  getMemory,
  listMemories,
  recordMemoryFeedback,
  updateMemory,
  type MemoryDetail,
  type MemorySnapshot,
} from "./chat-api";
import { publicError, relativeTime } from "./chat-workbench-model";

interface MemoryWorkspaceProps {
  readonly canManage: boolean;
  readonly focusedMemoryId: string | null;
  readonly userId: string;
  readonly workspaceId: string;
}

type MemoryStatusFilter = "" | "confirmed" | "disabled" | "expired";
type MemoryScopeFilter = "" | "user" | "workspace";
type MemoryKindFilter = "" | "preference" | "fact" | "instruction" | "note";

const kindNames: Readonly<Record<string, string>> = {
  fact: "事实",
  instruction: "指令",
  note: "备注",
  preference: "偏好",
};

const statusNames: Readonly<Record<string, string>> = {
  confirmed: "已启用",
  disabled: "已停用",
  expired: "已过期",
};

export function MemoryWorkspace({
  canManage,
  focusedMemoryId,
  userId,
  workspaceId,
}: MemoryWorkspaceProps) {
  const [memories, setMemories] = useState<MemorySnapshot[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(focusedMemoryId);
  const [detail, setDetail] = useState<MemoryDetail | null>(null);
  const [queryInput, setQueryInput] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<MemoryStatusFilter>("");
  const [scope, setScope] = useState<MemoryScopeFilter>("");
  const [kind, setKind] = useState<MemoryKindFilter>("");
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [content, setContent] = useState("");
  const [editScope, setEditScope] = useState<"user" | "workspace">("user");
  const [editKind, setEditKind] = useState<MemoryKindFilter>("note");
  const [expiresAt, setExpiresAt] = useState("");

  const fetchList = useCallback(
    () =>
      listMemories(workspaceId, {
        ...(kind === "" ? {} : { kind }),
        limit: 100,
        ...(query === "" ? {} : { query }),
        ...(scope === "" ? {} : { scope }),
        ...(status === "" ? {} : { status }),
      }),
    [kind, query, scope, status, workspaceId],
  );

  const loadList = useCallback(async () => {
    try {
      const loaded = await fetchList();
      setError(null);
      setMemories(loaded);
      if (loaded.length === 0) setDetail(null);
      setSelectedId((current) => {
        if (focusedMemoryId !== null && loaded.some((item) => item.id === focusedMemoryId)) {
          return focusedMemoryId;
        }
        if (current !== null && loaded.some((item) => item.id === current)) return current;
        return loaded[0]?.id ?? null;
      });
    } catch (caught: unknown) {
      setMemories([]);
      setError(publicError(caught));
    } finally {
      setLoading(false);
    }
  }, [fetchList, focusedMemoryId]);

  const loadDetail = useCallback(
    async (memoryId: string) => {
      try {
        const loaded = await getMemory(workspaceId, memoryId);
        setDetailError(null);
        setDetail(loaded);
        setContent(loaded.current_revision.content);
        setEditScope(loaded.memory.scope);
        setEditKind(loaded.memory.kind);
        setExpiresAt(
          loaded.memory.expires_at === null
            ? ""
            : new Date(loaded.memory.expires_at).toISOString().slice(0, 16),
        );
      } catch (caught: unknown) {
        setDetail(null);
        setDetailError(publicError(caught));
      } finally {
        setDetailLoading(false);
      }
    },
    [workspaceId],
  );

  useEffect(() => {
    let active = true;
    void fetchList()
      .then((loaded) => {
        if (!active) return;
        setError(null);
        setMemories(loaded);
        if (loaded.length === 0) setDetail(null);
        setSelectedId((current) => {
          if (focusedMemoryId !== null && loaded.some((item) => item.id === focusedMemoryId)) {
            return focusedMemoryId;
          }
          if (current !== null && loaded.some((item) => item.id === current)) return current;
          return loaded[0]?.id ?? null;
        });
      })
      .catch((caught: unknown) => {
        if (!active) return;
        setMemories([]);
        setError(publicError(caught));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [fetchList, focusedMemoryId]);

  useEffect(() => {
    if (selectedId === null) return;
    let active = true;
    void getMemory(workspaceId, selectedId)
      .then((loaded) => {
        if (!active) return;
        setDetailError(null);
        setDetail(loaded);
        setContent(loaded.current_revision.content);
        setEditScope(loaded.memory.scope);
        setEditKind(loaded.memory.kind);
        setExpiresAt(
          loaded.memory.expires_at === null
            ? ""
            : new Date(loaded.memory.expires_at).toISOString().slice(0, 16),
        );
      })
      .catch((caught: unknown) => {
        if (!active) return;
        setDetail(null);
        setDetailError(publicError(caught));
      })
      .finally(() => {
        if (active) setDetailLoading(false);
      });
    return () => {
      active = false;
    };
  }, [selectedId, workspaceId]);

  async function runMutation(operation: () => Promise<MemoryDetail>): Promise<void> {
    setBusy(true);
    setDetailError(null);
    try {
      const loaded = await operation();
      setDetail(loaded);
      setContent(loaded.current_revision.content);
      setEditScope(loaded.memory.scope);
      setEditKind(loaded.memory.kind);
      setExpiresAt(
        loaded.memory.expires_at === null
          ? ""
          : new Date(loaded.memory.expires_at).toISOString().slice(0, 16),
      );
      await loadList();
    } catch (caught: unknown) {
      const message = publicError(caught);
      if (selectedId !== null) await loadDetail(selectedId);
      setDetailError(message);
    } finally {
      setBusy(false);
    }
  }

  async function submitFeedback(value: "helpful" | "not_helpful"): Promise<void> {
    if (detail === null) return;
    const memoryId = detail.memory.id;
    setBusy(true);
    setDetailError(null);
    try {
      await recordMemoryFeedback(workspaceId, memoryId, detail.memory.revision, {
        memory_revision_id: detail.current_revision.id,
        reason: null,
        value,
      });
      await loadDetail(memoryId);
    } catch (caught: unknown) {
      const message = publicError(caught);
      await loadDetail(memoryId);
      setDetailError(message);
    } finally {
      setBusy(false);
    }
  }

  async function removeMemory(): Promise<void> {
    if (detail === null) return;
    const memoryId = detail.memory.id;
    setBusy(true);
    setDetailError(null);
    try {
      await deleteMemory(workspaceId, memoryId, detail.memory.revision);
      setDetail(null);
      setSelectedId(null);
      await loadList();
    } catch (caught: unknown) {
      const message = publicError(caught);
      await loadDetail(memoryId);
      setDetailError(message);
    } finally {
      setBusy(false);
    }
  }

  function submitUpdate(event: SyntheticEvent<HTMLFormElement>): void {
    event.preventDefault();
    if (detail === null || editKind === "") return;
    void runMutation(() =>
      updateMemory(workspaceId, detail.memory.id, detail.memory.revision, {
        content,
        expires_at: expiresAt === "" ? null : new Date(expiresAt).toISOString(),
        kind: editKind,
        scope: editScope,
      }),
    );
  }

  const canMutate = canManage && detail !== null && detail.memory.owner_user_id === userId && !busy;

  return (
    <section className="memory-workspace" aria-label="Memory 管理">
      <header className="workspace-page-header">
        <div>
          <span className="eyebrow">Day 4 · User-controlled Memory</span>
          <h1>Memory 管理</h1>
          <p>搜索、检查 revision，并让修改、停用、过期或删除在下一次 Run 立即生效。</p>
        </div>
        <button
          className="secondary-button"
          onClick={() => {
            setLoading(true);
            void loadList();
          }}
          type="button"
        >
          刷新服务端状态
        </button>
      </header>

      <form
        className="memory-toolbar"
        onSubmit={(event) => {
          event.preventDefault();
          const nextQuery = queryInput.trim();
          setLoading(true);
          if (nextQuery === query) void loadList();
          else setQuery(nextQuery);
        }}
      >
        <input
          aria-label="搜索 Memory"
          maxLength={200}
          onChange={(event) => {
            setQueryInput(event.currentTarget.value);
          }}
          placeholder="搜索当前 revision 正文"
          value={queryInput}
        />
        <select
          aria-label="Memory 状态"
          onChange={(event) => {
            setLoading(true);
            setStatus(event.currentTarget.value as MemoryStatusFilter);
          }}
          value={status}
        >
          <option value="">全部有效状态</option>
          <option value="confirmed">已启用</option>
          <option value="disabled">已停用</option>
          <option value="expired">已过期</option>
        </select>
        <select
          aria-label="Memory Scope"
          onChange={(event) => {
            setLoading(true);
            setScope(event.currentTarget.value as MemoryScopeFilter);
          }}
          value={scope}
        >
          <option value="">全部 Scope</option>
          <option value="user">仅自己</option>
          <option value="workspace">Workspace</option>
        </select>
        <select
          aria-label="Memory 类型"
          onChange={(event) => {
            setLoading(true);
            setKind(event.currentTarget.value as MemoryKindFilter);
          }}
          value={kind}
        >
          <option value="">全部类型</option>
          <option value="preference">偏好</option>
          <option value="fact">事实</option>
          <option value="instruction">指令</option>
          <option value="note">备注</option>
        </select>
        <button className="primary-button" type="submit">
          搜索
        </button>
      </form>

      {error === null ? null : (
        <div className="run-state-card run-state-card--error" role="alert">
          {error}
        </div>
      )}
      <div className="memory-management-grid">
        <aside className="memory-list" aria-busy={loading} aria-label="Memory 列表">
          {loading ? (
            <div className="chat-skeleton">
              <span />
              <span />
              <span />
            </div>
          ) : memories.length === 0 ? (
            <div className="memory-empty">没有符合条件的 Memory。</div>
          ) : (
            memories.map((memory) => (
              <button
                className={`memory-list-item${selectedId === memory.id ? " memory-list-item--active" : ""}`}
                key={memory.id}
                onClick={() => {
                  setDetailLoading(true);
                  if (selectedId === memory.id) void loadDetail(memory.id);
                  else setSelectedId(memory.id);
                }}
                type="button"
              >
                <span>
                  <strong>{kindNames[memory.kind]}</strong>
                  <small>{memory.scope === "user" ? "仅自己" : "Workspace"}</small>
                </span>
                <span>
                  <small>{statusNames[memory.status] ?? memory.status}</small>
                  <small>
                    r{memory.revision} · {relativeTime(memory.updated_at)}
                  </small>
                </span>
              </button>
            ))
          )}
        </aside>

        <article className="memory-detail" aria-busy={detailLoading}>
          {detailLoading ? (
            <div className="chat-skeleton">
              <span />
              <span />
              <span />
            </div>
          ) : detailError !== null ? (
            <div className="run-state-card run-state-card--error" role="alert">
              {detailError}
            </div>
          ) : detail === null ? (
            <div className="memory-empty">选择一条 Memory 查看治理状态。</div>
          ) : (
            <>
              <div className="memory-detail__heading">
                <div>
                  <span className={`status-pill status-pill--${detail.memory.status}`}>
                    {statusNames[detail.memory.status] ?? detail.memory.status}
                  </span>
                  <strong>
                    revision {detail.memory.revision} · content v{detail.memory.current_version}
                  </strong>
                </div>
                <small>来源 Conversation · {detail.memory.source_conversation_id}</small>
              </div>
              {detail.memory.owner_user_id === userId ? null : (
                <div className="memory-notice">
                  这是 Workspace 共享 Memory；仅创建者可以修改或删除。
                </div>
              )}
              <form className="memory-editor" onSubmit={submitUpdate}>
                <label>
                  <span>当前正文</span>
                  <textarea
                    disabled={!canMutate}
                    maxLength={4000}
                    onChange={(event) => {
                      setContent(event.currentTarget.value);
                    }}
                    rows={7}
                    value={content}
                  />
                </label>
                <div className="memory-field-grid">
                  <label>
                    <span>Scope</span>
                    <select
                      disabled={!canMutate}
                      onChange={(event) => {
                        setEditScope(event.currentTarget.value as "user" | "workspace");
                      }}
                      value={editScope}
                    >
                      <option value="user">仅自己</option>
                      <option value="workspace">Workspace</option>
                    </select>
                  </label>
                  <label>
                    <span>类型</span>
                    <select
                      disabled={!canMutate}
                      onChange={(event) => {
                        setEditKind(event.currentTarget.value as MemoryKindFilter);
                      }}
                      value={editKind}
                    >
                      <option value="preference">偏好</option>
                      <option value="fact">事实</option>
                      <option value="instruction">指令</option>
                      <option value="note">备注</option>
                    </select>
                  </label>
                  <label>
                    <span>过期时间</span>
                    <input
                      disabled={!canMutate}
                      min={new Date().toISOString().slice(0, 16)}
                      onChange={(event) => {
                        setExpiresAt(event.currentTarget.value);
                      }}
                      type="datetime-local"
                      value={expiresAt}
                    />
                  </label>
                </div>
                <div className="memory-dialog__actions">
                  <button
                    className="primary-button"
                    disabled={!canMutate || content.trim() === ""}
                    type="submit"
                  >
                    保存新 revision
                  </button>
                  {detail.memory.status === "disabled" ? (
                    <button
                      className="secondary-button"
                      disabled={!canMutate}
                      onClick={() =>
                        void runMutation(() =>
                          enableMemory(workspaceId, detail.memory.id, detail.memory.revision),
                        )
                      }
                      type="button"
                    >
                      恢复启用
                    </button>
                  ) : (
                    <button
                      className="secondary-button"
                      disabled={!canMutate}
                      onClick={() =>
                        void runMutation(() =>
                          disableMemory(workspaceId, detail.memory.id, detail.memory.revision),
                        )
                      }
                      type="button"
                    >
                      停用
                    </button>
                  )}
                  <button
                    className="danger-button"
                    disabled={!canMutate}
                    onClick={() => {
                      if (window.confirm("删除后将立即从搜索和下一次 Run 中移除，确定继续吗？")) {
                        void removeMemory();
                      }
                    }}
                    type="button"
                  >
                    删除
                  </button>
                </div>
              </form>
              <div className="memory-feedback-actions" aria-label="Memory 反馈">
                <span>这条 Memory 对回答是否有帮助？</span>
                <button
                  className="compact-button"
                  disabled={busy}
                  onClick={() => void submitFeedback("helpful")}
                  type="button"
                >
                  有帮助
                </button>
                <button
                  className="compact-button"
                  disabled={busy}
                  onClick={() => void submitFeedback("not_helpful")}
                  type="button"
                >
                  不相关
                </button>
              </div>
              <section className="memory-revisions" aria-label="Memory revisions">
                <h2>Revision 历史</h2>
                {detail.revisions.map((revision) => (
                  <article className="memory-revision-card" key={revision.id}>
                    <strong>
                      v{revision.version} · {kindNames[revision.kind]}
                    </strong>
                    <p>{revision.content}</p>
                    <small>
                      {revision.validity} · {new Date(revision.created_at).toLocaleString("zh-CN")}
                      {revision.expires_at === null
                        ? ""
                        : ` · 过期 ${new Date(revision.expires_at).toLocaleString("zh-CN")}`}
                    </small>
                  </article>
                ))}
              </section>
            </>
          )}
        </article>
      </div>
    </section>
  );
}
