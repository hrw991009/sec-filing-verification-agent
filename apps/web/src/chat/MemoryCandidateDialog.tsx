import { useMemo, useState } from "react";

import type {
  MemoryCandidate,
  MemoryResolution,
  MemorySnapshot,
  ResolveMemoryCandidateRequest,
} from "./chat-api";

interface MemoryCandidateDialogProps {
  readonly busy: boolean;
  readonly candidate: MemoryCandidate;
  readonly error: string | null;
  readonly memories: readonly MemorySnapshot[];
  readonly onClose: () => void;
  readonly onConfirm: (request: ResolveMemoryCandidateRequest) => void;
  readonly onReject: () => void;
  readonly resolution: MemoryResolution | null;
}

const policyLabels: Readonly<Record<MemoryCandidate["policy_reason"], string>> = {
  assistant_only_requires_edit: "仅来自助手内容，确认前必须编辑",
  mixed_sources: "来自用户与助手的混合来源",
  sensitive_content: "包含不可写入的敏感内容",
  user_authored: "来自用户明确表达的内容",
};

const actionLabels: Readonly<Record<ResolveMemoryCandidateRequest["action"], string>> = {
  create: "创建新记忆",
  merge: "合并到现有记忆",
  update: "更新现有记忆",
};

export function MemoryCandidateDialog({
  busy,
  candidate,
  error,
  memories,
  onClose,
  onConfirm,
  onReject,
  resolution,
}: MemoryCandidateDialogProps) {
  const availableTargets = useMemo(
    () => memories.filter((memory) => memory.status === "confirmed"),
    [memories],
  );
  const [content, setContent] = useState(candidate.suggested_content ?? "");
  const [scope, setScope] = useState<ResolveMemoryCandidateRequest["scope"]>(
    candidate.suggested_scope,
  );
  const [kind, setKind] = useState<ResolveMemoryCandidateRequest["kind"]>("note");
  const [action, setAction] = useState<ResolveMemoryCandidateRequest["action"]>("create");
  const [targetMemoryId, setTargetMemoryId] = useState(availableTargets[0]?.id ?? "");
  const [expiry, setExpiry] = useState(
    candidate.suggested_expires_at === null ? "" : candidate.suggested_expires_at.slice(0, 10),
  );
  const minimumExpiry = useMemo(() => {
    const tomorrow = new Date();
    tomorrow.setUTCDate(tomorrow.getUTCDate() + 1);
    return tomorrow.toISOString().slice(0, 10);
  }, []);

  const selectedTarget =
    availableTargets.find((memory) => memory.id === targetMemoryId) ??
    (targetMemoryId === "" ? (availableTargets[0] ?? null) : null);
  const requiresTarget = action !== "create";
  const canConfirm =
    !busy &&
    candidate.status === "candidate" &&
    candidate.policy_decision !== "rejected" &&
    content.trim().length > 0 &&
    (!requiresTarget || selectedTarget !== null);

  function submit(): void {
    if (!canConfirm) return;
    onConfirm({
      action,
      content: content.trim(),
      expires_at: expiry ? `${expiry}T00:00:00Z` : null,
      kind,
      scope,
      target_memory_id: requiresTarget && selectedTarget !== null ? selectedTarget.id : null,
      target_revision:
        requiresTarget && selectedTarget !== null ? selectedTarget.current_version : null,
    });
  }

  return (
    <div className="memory-dialog-backdrop" role="presentation">
      <section
        aria-labelledby="memory-dialog-title"
        aria-modal="true"
        className="memory-dialog"
        role="dialog"
      >
        <header className="memory-dialog__header">
          <div>
            <p className="eyebrow">Memory 候选 · Revision {candidate.revision}</p>
            <h2 id="memory-dialog-title">确认要长期保存的内容</h2>
          </div>
          <button
            aria-label="关闭记忆候选"
            className="compact-button"
            onClick={onClose}
            type="button"
          >
            关闭
          </button>
        </header>

        {resolution === null ? (
          <form
            className="memory-dialog__body"
            onSubmit={(event) => {
              event.preventDefault();
              submit();
            }}
          >
            <div className={`memory-policy memory-policy--${candidate.policy_decision}`}>
              <strong>{policyLabels[candidate.policy_reason]}</strong>
              <span>
                置信度 {Math.round(candidate.confidence * 100)}% · 来源消息{" "}
                {candidate.source_message_ids.length} 条 · 默认作用域{" "}
                {candidate.suggested_scope === "user" ? "仅自己" : "当前 Workspace"}
              </span>
            </div>

            {candidate.status === "rejected" || candidate.policy_decision === "rejected" ? (
              <div className="memory-dialog__notice" role="alert">
                该候选已被敏感信息策略拒绝，正文没有写入候选或正式
                Memory。请返回原消息清理敏感内容后重新选择。
              </div>
            ) : (
              <>
                <label className="memory-field">
                  <span>最终确认内容</span>
                  <textarea
                    aria-label="最终确认内容"
                    disabled={busy}
                    maxLength={4000}
                    onChange={(event) => {
                      setContent(event.currentTarget.value);
                    }}
                    rows={8}
                    value={content}
                  />
                  <small>系统建议只是候选；只有这里的最终内容会进入正式 Memory。</small>
                </label>

                <div className="memory-field-grid">
                  <label className="memory-field">
                    <span>写入动作</span>
                    <select
                      aria-label="写入动作"
                      disabled={busy}
                      onChange={(event) => {
                        setAction(
                          event.currentTarget.value as ResolveMemoryCandidateRequest["action"],
                        );
                      }}
                      value={action}
                    >
                      {Object.entries(actionLabels).map(([value, label]) => (
                        <option key={value} value={value}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="memory-field">
                    <span>类型</span>
                    <select
                      aria-label="记忆类型"
                      disabled={busy}
                      onChange={(event) => {
                        setKind(event.currentTarget.value as ResolveMemoryCandidateRequest["kind"]);
                      }}
                      value={kind}
                    >
                      <option value="preference">偏好</option>
                      <option value="fact">事实</option>
                      <option value="instruction">长期指令</option>
                      <option value="note">备注</option>
                    </select>
                  </label>
                  <label className="memory-field">
                    <span>作用域</span>
                    <select
                      aria-label="记忆作用域"
                      disabled={busy}
                      onChange={(event) => {
                        setScope(
                          event.currentTarget.value as ResolveMemoryCandidateRequest["scope"],
                        );
                      }}
                      value={scope}
                    >
                      <option value="user">仅自己</option>
                      <option value="workspace">当前 Workspace</option>
                    </select>
                  </label>
                  <label className="memory-field">
                    <span>过期日期（可选）</span>
                    <input
                      aria-label="记忆过期日期"
                      disabled={busy}
                      min={minimumExpiry}
                      onChange={(event) => {
                        setExpiry(event.currentTarget.value);
                      }}
                      type="date"
                      value={expiry}
                    />
                  </label>
                </div>

                {requiresTarget ? (
                  <label className="memory-field">
                    <span>目标 Memory</span>
                    <select
                      aria-label="目标 Memory"
                      disabled={busy || availableTargets.length === 0}
                      onChange={(event) => {
                        setTargetMemoryId(event.currentTarget.value);
                      }}
                      value={selectedTarget?.id ?? ""}
                    >
                      {availableTargets.length === 0 ? (
                        <option value="">暂无可更新的 Memory</option>
                      ) : null}
                      {availableTargets.map((memory) => (
                        <option key={memory.id} value={memory.id}>
                          {memory.kind} · Revision {memory.current_version} ·{" "}
                          {memory.id.slice(0, 8)}
                        </option>
                      ))}
                    </select>
                    <small>更新和合并都使用目标当前 Revision 做并发校验，不会静默覆盖。</small>
                  </label>
                ) : null}
              </>
            )}

            {error === null ? null : (
              <div className="memory-dialog__notice memory-dialog__notice--error" role="alert">
                {error}
              </div>
            )}

            <footer className="memory-dialog__actions">
              {candidate.status === "candidate" ? (
                <button className="compact-button" disabled={busy} onClick={onReject} type="button">
                  拒绝候选
                </button>
              ) : null}
              <button className="primary-button" disabled={!canConfirm} type="submit">
                {busy ? "正在提交…" : actionLabels[action]}
              </button>
            </footer>
          </form>
        ) : (
          <div className="memory-dialog__body">
            <div className="memory-dialog__success" role="status">
              <strong>Memory 已确认</strong>
              <span>
                {actionLabels[resolution.action]} · Revision{" "}
                {resolution.memory.memory.current_version}
              </span>
            </div>
            <div className="memory-revision-card">
              <p>{resolution.memory.current_revision.content}</p>
              <dl>
                <div>
                  <dt>类型</dt>
                  <dd>{resolution.memory.memory.kind}</dd>
                </div>
                <div>
                  <dt>作用域</dt>
                  <dd>{resolution.memory.memory.scope}</dd>
                </div>
                <div>
                  <dt>来源</dt>
                  <dd>{resolution.memory.current_revision.source_message_ids.length} 条消息</dd>
                </div>
              </dl>
            </div>
            <footer className="memory-dialog__actions">
              <button className="primary-button" onClick={onClose} type="button">
                完成
              </button>
            </footer>
          </div>
        )}
      </section>
    </div>
  );
}
