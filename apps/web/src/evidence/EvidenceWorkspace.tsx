import { useCallback, useEffect, useState } from "react";

import { publicError, relativeTime } from "../chat/chat-workbench-model";
import {
  getClaimGraph,
  getEvidence,
  invalidateEvidence,
  listEvidence,
  listResearchClaims,
  listResearchRuns,
  type ClaimGraph,
  type Evidence,
  type EvidenceKind,
  type EvidenceStatus,
  type ResearchClaim,
  type ResearchRun,
} from "./evidence-api";
import "./evidence.css";

interface EvidenceWorkspaceProps {
  readonly canManage: boolean;
  readonly focusedEvidenceId: string | null;
  readonly refreshToken: number;
  readonly workspaceId: string;
}

type StatusFilter = "" | EvidenceStatus;
type KindFilter = "" | EvidenceKind;

const kindNames: Readonly<Record<string, string>> = {
  bidding: "招投标",
  news: "新闻",
  policy: "政策",
  sql_result: "SQL 结果",
  stock: "行情",
  web_snapshot: "Web 快照",
};

const statusNames: Readonly<Record<string, string>> = {
  active: "可引用",
  superseded: "已替代",
  tombstoned: "已撤销",
  unavailable: "不可用",
};

const relationNames: Readonly<Record<string, string>> = {
  context: "背景",
  refutes: "反驳",
  supports: "支持",
};

export function EvidenceWorkspace({
  canManage,
  focusedEvidenceId,
  refreshToken,
  workspaceId,
}: EvidenceWorkspaceProps) {
  const [items, setItems] = useState<Evidence[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(focusedEvidenceId);
  const [detail, setDetail] = useState<Evidence | null>(null);
  const [status, setStatus] = useState<StatusFilter>("");
  const [kind, setKind] = useState<KindFilter>("");
  const [researchRuns, setResearchRuns] = useState<ResearchRun[]>([]);
  const [selectedResearchRunId, setSelectedResearchRunId] = useState<string | null>(null);
  const [claims, setClaims] = useState<ResearchClaim[]>([]);
  const [graph, setGraph] = useState<ClaimGraph | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [invalidationReason, setInvalidationReason] = useState("");

  const fetchEvidence = useCallback(
    () =>
      listEvidence(workspaceId, {
        ...(kind === "" ? {} : { kind }),
        limit: 100,
        ...(status === "" ? {} : { status }),
      }),
    [kind, status, workspaceId],
  );

  const applyLoadedLedger = useCallback(
    (loadedEvidence: Evidence[], loadedRuns: ResearchRun[]) => {
      setError(null);
      setItems(loadedEvidence);
      setResearchRuns(loadedRuns);
      setSelectedId((current) => {
        if (
          focusedEvidenceId !== null &&
          loadedEvidence.some((item) => item.id === focusedEvidenceId)
        ) {
          return focusedEvidenceId;
        }
        if (current !== null && loadedEvidence.some((item) => item.id === current)) return current;
        return loadedEvidence[0]?.id ?? null;
      });
      setSelectedResearchRunId((current) => {
        if (current !== null && loadedRuns.some((item) => item.id === current)) return current;
        return loadedRuns[0]?.id ?? null;
      });
      if (loadedEvidence.length === 0) setDetail(null);
    },
    [focusedEvidenceId],
  );

  const loadLedger = useCallback(async () => {
    setLoading(true);
    try {
      const [loadedEvidence, loadedRuns] = await Promise.all([
        fetchEvidence(),
        listResearchRuns(workspaceId, 100),
      ]);
      applyLoadedLedger(loadedEvidence, loadedRuns);
    } catch (caught: unknown) {
      setError(publicError(caught));
      setItems([]);
      setResearchRuns([]);
    } finally {
      setLoading(false);
    }
  }, [applyLoadedLedger, fetchEvidence, workspaceId]);

  useEffect(() => {
    let active = true;
    void Promise.all([fetchEvidence(), listResearchRuns(workspaceId, 100)])
      .then(([loadedEvidence, loadedRuns]) => {
        if (active) applyLoadedLedger(loadedEvidence, loadedRuns);
      })
      .catch((caught: unknown) => {
        if (!active) return;
        setError(publicError(caught));
        setItems([]);
        setResearchRuns([]);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [applyLoadedLedger, fetchEvidence, refreshToken, workspaceId]);

  useEffect(() => {
    if (selectedId === null) return;
    let active = true;
    void getEvidence(workspaceId, selectedId)
      .then((loaded) => {
        if (!active) return;
        setDetail(loaded);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (!active) return;
        setDetail(null);
        setError(publicError(caught));
      });
    return () => {
      active = false;
    };
  }, [selectedId, workspaceId]);

  useEffect(() => {
    if (selectedResearchRunId === null) {
      return;
    }
    let active = true;
    void Promise.all([
      listResearchClaims(workspaceId, selectedResearchRunId, 100),
      getClaimGraph(workspaceId, selectedResearchRunId),
    ])
      .then(([loadedClaims, loadedGraph]) => {
        if (!active) return;
        setClaims(loadedClaims);
        setGraph(loadedGraph);
      })
      .catch((caught: unknown) => {
        if (!active) return;
        setClaims([]);
        setGraph(null);
        setError(publicError(caught));
      });
    return () => {
      active = false;
    };
  }, [selectedResearchRunId, workspaceId]);

  async function invalidateSelected(): Promise<void> {
    if (detail === null || invalidationReason.trim() === "") return;
    setBusy(true);
    setError(null);
    try {
      const updated = await invalidateEvidence(workspaceId, detail.id, detail.revision, {
        reason: invalidationReason.trim(),
        status: "tombstoned",
      });
      setDetail(updated);
      setInvalidationReason("");
      await loadLedger();
    } catch (caught: unknown) {
      setError(publicError(caught));
      await loadLedger();
    } finally {
      setBusy(false);
    }
  }

  const visibleClaims = selectedResearchRunId === null ? [] : claims;
  const visibleGraph = selectedResearchRunId === null ? null : graph;

  return (
    <section className="evidence-workspace" aria-label="Evidence Inspector">
      <header className="workspace-page-header">
        <div>
          <span className="eyebrow">Day 4 · Evidence / Claim Ledger</span>
          <h1>Evidence Inspector</h1>
          <p>检查 Observation 提升结果、来源版本、授权快照和 Claim 反向链路。</p>
        </div>
        <button
          className="secondary-button"
          onClick={() => {
            void loadLedger();
          }}
          type="button"
        >
          刷新服务端状态
        </button>
      </header>

      <div className="evidence-toolbar">
        <select
          aria-label="Evidence 状态"
          onChange={(event) => {
            setStatus(event.currentTarget.value as StatusFilter);
          }}
          value={status}
        >
          <option value="">全部状态</option>
          {Object.entries(statusNames).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <select
          aria-label="Evidence 类型"
          onChange={(event) => {
            setKind(event.currentTarget.value as KindFilter);
          }}
          value={kind}
        >
          <option value="">全部类型</option>
          {Object.entries(kindNames).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <button
          className="primary-button"
          onClick={() => {
            void loadLedger();
          }}
          type="button"
        >
          应用筛选
        </button>
      </div>

      {error === null ? null : (
        <div className="run-state-card run-state-card--error" role="alert">
          {error}
        </div>
      )}

      <div className="evidence-grid">
        <aside className="evidence-list" aria-busy={loading} aria-label="Evidence 列表">
          {loading ? (
            <div className="chat-skeleton">
              <span />
              <span />
              <span />
            </div>
          ) : items.length === 0 ? (
            <div className="evidence-empty">还没有正式 Evidence。</div>
          ) : (
            items.map((item) => (
              <button
                className={`evidence-list-item${selectedId === item.id ? " evidence-list-item--active" : ""}`}
                key={item.id}
                onClick={() => {
                  setSelectedId(item.id);
                }}
                type="button"
              >
                <strong>{item.title}</strong>
                <span>
                  {kindNames[item.kind] ?? item.kind} · {statusNames[item.status] ?? item.status}
                </span>
                <small>
                  r{item.revision} · {relativeTime(item.updated_at)}
                </small>
              </button>
            ))
          )}
        </aside>

        <article className="evidence-detail" aria-label="Evidence 详情" role="region">
          {detail === null ? (
            <div className="evidence-empty">选择 Evidence 查看不可变来源与 Trace。</div>
          ) : (
            <>
              <div className="evidence-detail__heading">
                <div>
                  <span className={`status-pill status-pill--${detail.status}`}>
                    {statusNames[detail.status] ?? detail.status}
                  </span>
                  <strong>{detail.title}</strong>
                </div>
                <small>{detail.id}</small>
              </div>
              <p className="evidence-excerpt">{detail.excerpt ?? "正文已按生命周期策略清空。"}</p>
              <dl className="evidence-facts">
                <div>
                  <dt>来源定位器</dt>
                  <dd>{detail.locator.locator_type}</dd>
                </div>
                <div>
                  <dt>来源版本</dt>
                  <dd>{detail.source_resource_version}</dd>
                </div>
                <div>
                  <dt>Normalizer</dt>
                  <dd>{detail.normalizer_version}</dd>
                </div>
                <div>
                  <dt>内容摘要</dt>
                  <dd>{detail.content_sha256}</dd>
                </div>
                <div>
                  <dt>许可 / 条款</dt>
                  <dd>{detail.license_or_terms}</dd>
                </div>
                <div>
                  <dt>授权快照</dt>
                  <dd>
                    {detail.authorization_snapshot.role} · {detail.authorization_snapshot.action}
                  </dd>
                </div>
              </dl>
              <section className="evidence-lineage" aria-label="Evidence 反向 Trace">
                <h2>反向 Trace</h2>
                <ol>
                  <li>
                    <strong>Evidence</strong>
                    <span>{detail.id}</span>
                  </li>
                  <li>
                    <strong>Observation</strong>
                    <span>{detail.origin_observation_id}</span>
                  </li>
                  <li>
                    <strong>ToolCall</strong>
                    <span>{detail.origin_tool_call_id}</span>
                  </li>
                  <li>
                    <strong>Step / Run</strong>
                    <span>
                      {detail.origin_step_id} / {detail.origin_run_id}
                    </span>
                  </li>
                </ol>
              </section>
              {detail.status !== "active" || !canManage ? null : (
                <div className="evidence-invalidation">
                  <input
                    aria-label="Evidence 失效原因"
                    disabled={busy}
                    maxLength={200}
                    onChange={(event) => {
                      setInvalidationReason(event.currentTarget.value);
                    }}
                    placeholder="记录撤销原因"
                    value={invalidationReason}
                  />
                  <button
                    className="danger-button"
                    disabled={busy || invalidationReason.trim() === ""}
                    onClick={() => {
                      void invalidateSelected();
                    }}
                    type="button"
                  >
                    撤销 Evidence
                  </button>
                </div>
              )}
            </>
          )}
        </article>

        <aside className="claim-inspector" aria-label="Claim Inspector">
          <div className="claim-inspector__heading">
            <h2>Claim Ledger</h2>
            <span>{visibleGraph === null ? 0 : visibleGraph.edges.length} 条关系</span>
          </div>
          <select
            aria-label="Research Run"
            onChange={(event) => {
              setSelectedResearchRunId(event.currentTarget.value || null);
            }}
            value={selectedResearchRunId ?? ""}
          >
            <option value="">暂无 Research Run</option>
            {researchRuns.map((run) => (
              <option key={run.id} value={run.id}>
                {run.id.slice(0, 8)} · {run.status}
              </option>
            ))}
          </select>
          {visibleClaims.length === 0 ? (
            <div className="evidence-empty">
              尚无 Claim；Step 4 的 Research Runtime 将写入这里。
            </div>
          ) : (
            visibleClaims.map((claim) => (
              <article className="claim-card" key={claim.id}>
                <div>
                  <strong>{claim.statement}</strong>
                  <span className={`status-pill status-pill--${claim.verification_status}`}>
                    {claim.verification_status}
                  </span>
                </div>
                <small>
                  coverage {(claim.coverage * 100).toFixed(0)}% · confidence{" "}
                  {(claim.confidence * 100).toFixed(0)}%
                </small>
                <ul>
                  {claim.relations.map((relation) => (
                    <li key={relation.evidence.id}>
                      <button
                        onClick={() => {
                          setSelectedId(relation.evidence.id);
                        }}
                        type="button"
                      >
                        {relationNames[relation.relation] ?? relation.relation} ·{" "}
                        {relation.evidence.title}
                      </button>
                      <small>
                        {relation.status} · ToolCall {relation.evidence.origin_tool_call_id}
                      </small>
                    </li>
                  ))}
                </ul>
              </article>
            ))
          )}
        </aside>
      </div>
    </section>
  );
}
