import { useCallback, useEffect, useMemo, useRef, useState, type SubmitEvent } from "react";

import type { Industry } from "../industry/industry-api";
import { cancelRun, getAgentTrace, type AgentTrace } from "../chat/chat-api";
import {
  formatCost,
  idempotencyKey,
  publicError,
  relativeTime,
  runStatusNames,
} from "../chat/chat-workbench-model";
import { SafeMarkdown } from "../chat/SafeMarkdown";
import { listResearchClaims, type ResearchClaim } from "../evidence/evidence-api";
import {
  getResearchRun,
  listResearchRuns,
  startResearch,
  type ResearchRun,
  type StartResearchRequest,
} from "./research-api";
import "./research.css";

interface ResearchWorkspaceProps {
  readonly canManage: boolean;
  readonly focusedResearchRunId: string | null;
  readonly industries: readonly Industry[];
  readonly onOpenAgent: (question: string, mode: "none" | "web") => void;
  readonly onOpenEvidence: (evidenceId: string | null) => void;
  readonly onSelectIndustry: (industryId: string) => void;
  readonly selectedIndustryId: string | null;
  readonly workspaceId: string;
}

const nodeNames: Readonly<Record<string, string>> = {
  clarify_scope: "校验研究范围",
  draft: "保存 L3 草稿",
  normalize_evidence: "Observation → Evidence",
  outline: "生成可解释提纲",
  plan: "冻结研究计划",
  research_loop: "统一 Runtime / Tool loop",
  synthesize_claims: "Evidence → Claim",
  write_research_brief: "保存 ResearchBrief",
};

const researchStatusNames: Readonly<Record<string, string>> = {
  active: "执行中",
  cancelled: "已取消",
  completed: "已完成",
  draft: "已建立",
  failed: "失败",
};

function lines(value: string): string[] {
  return [
    ...new Set(
      value
        .split(/\r?\n/u)
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  ];
}

function nodeEvents(trace: AgentTrace | null) {
  return trace?.events.filter((event) => event.event_type.startsWith("agent.research.node_")) ?? [];
}

export function ResearchWorkspace({
  canManage,
  focusedResearchRunId,
  industries,
  onOpenAgent,
  onOpenEvidence,
  onSelectIndustry,
  selectedIndustryId,
  workspaceId,
}: ResearchWorkspaceProps) {
  const [runs, setRuns] = useState<ResearchRun[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(focusedResearchRunId);
  const [detail, setDetail] = useState<ResearchRun | null>(null);
  const [trace, setTrace] = useState<AgentTrace | null>(null);
  const [claims, setClaims] = useState<ResearchClaim[]>([]);
  const [loading, setLoading] = useState(true);
  const [detailRefreshRevision, setDetailRefreshRevision] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<{
    readonly researchRunId: string;
    readonly message: string;
  } | null>(null);
  const [originalQuestion, setOriginalQuestion] = useState("");
  const [confirmedScope, setConfirmedScope] = useState("");
  const [exclusions, setExclusions] = useState("");
  const [completionCriteria, setCompletionCriteria] = useState(
    "形成带 Evidence/Claim 关系和不确定项的 L3 草稿",
  );
  const [maxSteps, setMaxSteps] = useState(20);
  const [maxTotalTokens, setMaxTotalTokens] = useState(16_384);
  const [maxCostMicroUsd, setMaxCostMicroUsd] = useState(500_000);
  const [timeoutSeconds, setTimeoutSeconds] = useState(600);
  const detailRequestRef = useRef(0);

  const chooseLoadedRun = useCallback(
    (loaded: ResearchRun[], preferredId: string | null = null) => {
      setRuns(loaded);
      setSelectedId((current) => {
        for (const candidate of [preferredId, focusedResearchRunId, current]) {
          if (candidate !== null && loaded.some((item) => item.id === candidate)) return candidate;
        }
        return loaded[0]?.id ?? null;
      });
      if (loaded.length === 0) {
        setDetail(null);
        setTrace(null);
        setClaims([]);
      }
    },
    [focusedResearchRunId],
  );

  const loadRuns = useCallback(
    async (preferredId: string | null = null): Promise<void> => {
      setLoading(true);
      setError(null);
      try {
        chooseLoadedRun(await listResearchRuns(workspaceId, 100), preferredId);
        setDetailRefreshRevision((current) => current + 1);
      } catch (caught: unknown) {
        setError(publicError(caught));
        chooseLoadedRun([]);
      } finally {
        setLoading(false);
      }
    },
    [chooseLoadedRun, workspaceId],
  );

  useEffect(() => {
    let active = true;
    void listResearchRuns(workspaceId, 100)
      .then((loaded) => {
        if (!active) return;
        chooseLoadedRun(loaded);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (!active) return;
        setError(publicError(caught));
        chooseLoadedRun([]);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [chooseLoadedRun, workspaceId]);

  useEffect(() => {
    if (selectedId === null) return;
    const requestNumber = detailRequestRef.current + 1;
    detailRequestRef.current = requestNumber;
    void getResearchRun(workspaceId, selectedId)
      .then(async (loaded) => {
        if (detailRequestRef.current !== requestNumber) return;
        const [loadedTrace, loadedClaims] = await Promise.allSettled([
          getAgentTrace(workspaceId, loaded.agent_run_id),
          listResearchClaims(workspaceId, loaded.id, 100),
        ]);
        if (detailRequestRef.current !== requestNumber) return;
        setDetail(loaded);
        setTrace(loadedTrace.status === "fulfilled" ? loadedTrace.value : null);
        setClaims(loadedClaims.status === "fulfilled" ? loadedClaims.value : []);
        const failures = [loadedTrace, loadedClaims]
          .filter((item) => item.status === "rejected")
          .map((item) => publicError(item.reason));
        setDetailError(
          failures.length === 0
            ? null
            : { message: failures.join("；"), researchRunId: selectedId },
        );
      })
      .catch((caught: unknown) => {
        if (detailRequestRef.current !== requestNumber) return;
        setDetail(null);
        setTrace(null);
        setClaims([]);
        setDetailError({ message: publicError(caught), researchRunId: selectedId });
      });
  }, [detailRefreshRevision, selectedId, workspaceId]);

  const scopeItems = useMemo(() => lines(confirmedScope), [confirmedScope]);
  const criteriaItems = useMemo(() => lines(completionCriteria), [completionCriteria]);
  const canSubmit =
    canManage &&
    selectedIndustryId !== null &&
    originalQuestion.trim() !== "" &&
    scopeItems.length > 0 &&
    criteriaItems.length > 0 &&
    !submitting;

  async function submit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!canSubmit) return;
    const request: StartResearchRequest = {
      completion_criteria: criteriaItems,
      confirmed_scope: scopeItems,
      exclusions: lines(exclusions),
      industry_id: selectedIndustryId,
      max_cost_micro_usd: maxCostMicroUsd,
      max_steps: maxSteps,
      max_total_tokens: maxTotalTokens,
      original_question: originalQuestion.trim(),
      timeout_seconds: timeoutSeconds,
    };
    setSubmitting(true);
    setError(null);
    try {
      const receipt = await startResearch(workspaceId, request, idempotencyKey());
      await loadRuns(receipt.research_run_id);
    } catch (caught: unknown) {
      setError(publicError(caught));
    } finally {
      setSubmitting(false);
    }
  }

  async function cancelSelected(): Promise<void> {
    if (detail === null) return;
    setCancelling(true);
    setDetailError(null);
    try {
      await cancelRun(workspaceId, detail.agent_run_id);
      await loadRuns(detail.id);
    } catch (caught: unknown) {
      setDetailError({ message: publicError(caught), researchRunId: detail.id });
    } finally {
      setCancelling(false);
    }
  }

  const events = nodeEvents(trace);
  const visibleDetail = detail?.id === selectedId ? detail : null;
  const visibleDetailError = detailError?.researchRunId === selectedId ? detailError.message : null;
  const detailLoading =
    selectedId !== null && visibleDetail === null && visibleDetailError === null;
  const detailIsActive =
    visibleDetail?.agent_status === "queued" || visibleDetail?.agent_status === "running";

  return (
    <section className="research-workspace" aria-label="Research L3 工作台">
      <header className="workspace-page-header">
        <div>
          <span className="eyebrow">Day 4 · Evidence Research L3</span>
          <h1>Research Workbench</h1>
          <p>
            显式确认 Brief，经唯一 Runtime/Tool loop 生成可解释草稿；当前不含 durable resume 或
            Verifier。
          </p>
        </div>
        <button
          className="secondary-button"
          onClick={() => void loadRuns(selectedId)}
          type="button"
        >
          刷新服务端状态
        </button>
      </header>

      {error === null ? null : (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}

      <div className="research-layout">
        <aside className="research-sidebar">
          <form className="research-form" onSubmit={(event) => void submit(event)}>
            <h2>新建 L3 Research</h2>
            <label>
              行业范围
              <select
                aria-label="Research 行业"
                disabled={!canManage || submitting}
                onChange={(event) => {
                  onSelectIndustry(event.currentTarget.value);
                }}
                value={selectedIndustryId ?? ""}
              >
                <option value="">选择行业</option>
                {industries.map((industry) => (
                  <option key={industry.id} value={industry.id}>
                    {industry.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              原始问题
              <textarea
                aria-label="Research 原始问题"
                disabled={!canManage || submitting}
                maxLength={4_000}
                onChange={(event) => {
                  setOriginalQuestion(event.currentTarget.value);
                }}
                required
                rows={3}
                value={originalQuestion}
              />
            </label>
            <label>
              已确认范围（每行一项）
              <textarea
                aria-label="Research 已确认范围"
                disabled={!canManage || submitting}
                onChange={(event) => {
                  setConfirmedScope(event.currentTarget.value);
                }}
                required
                rows={3}
                value={confirmedScope}
              />
            </label>
            <label>
              排除项（每行一项，可空）
              <textarea
                aria-label="Research 排除项"
                disabled={!canManage || submitting}
                onChange={(event) => {
                  setExclusions(event.currentTarget.value);
                }}
                rows={2}
                value={exclusions}
              />
            </label>
            <label>
              完成标准（每行一项）
              <textarea
                aria-label="Research 完成标准"
                disabled={!canManage || submitting}
                onChange={(event) => {
                  setCompletionCriteria(event.currentTarget.value);
                }}
                required
                rows={3}
                value={completionCriteria}
              />
            </label>
            <fieldset>
              <legend>可信预算</legend>
              <label>
                最大步骤
                <input
                  aria-label="Research 最大步骤"
                  max={64}
                  min={12}
                  onChange={(event) => {
                    setMaxSteps(event.currentTarget.valueAsNumber);
                  }}
                  type="number"
                  value={maxSteps}
                />
              </label>
              <label>
                最大 Token
                <input
                  aria-label="Research 最大 Token"
                  max={100_000}
                  min={1_024}
                  onChange={(event) => {
                    setMaxTotalTokens(event.currentTarget.valueAsNumber);
                  }}
                  type="number"
                  value={maxTotalTokens}
                />
              </label>
              <label>
                费用上限（微美元）
                <input
                  aria-label="Research 费用上限"
                  max={10_000_000}
                  min={0}
                  onChange={(event) => {
                    setMaxCostMicroUsd(event.currentTarget.valueAsNumber);
                  }}
                  type="number"
                  value={maxCostMicroUsd}
                />
              </label>
              <label>
                超时（秒）
                <input
                  aria-label="Research 超时秒数"
                  max={1_500}
                  min={30}
                  onChange={(event) => {
                    setTimeoutSeconds(event.currentTarget.valueAsNumber);
                  }}
                  type="number"
                  value={timeoutSeconds}
                />
              </label>
            </fieldset>
            <button className="primary-button" disabled={!canSubmit} type="submit">
              {submitting ? "正在建立正式 Run…" : "确认 Brief 并开始"}
            </button>
            {canManage ? null : <small>Viewer 只能读取 Research，不能创建或取消。</small>}
          </form>

          <section className="research-run-list" aria-busy={loading}>
            <div className="research-section-heading">
              <h2>历史 Research</h2>
              <span>{runs.length}</span>
            </div>
            {loading ? (
              <div className="research-empty">正在从正式 API 重建列表…</div>
            ) : runs.length === 0 ? (
              <div className="research-empty">尚无 Research Run。</div>
            ) : (
              runs.map((run) => (
                <button
                  className={`research-run-item${selectedId === run.id ? " research-run-item--active" : ""}`}
                  key={run.id}
                  onClick={() => {
                    setSelectedId(run.id);
                  }}
                  type="button"
                >
                  <strong>{run.brief.original_question}</strong>
                  <span>
                    {researchStatusNames[run.status] ?? run.status} · {run.current_node ?? "queued"}
                  </span>
                  <small>{relativeTime(run.updated_at)}</small>
                </button>
              ))
            )}
          </section>
        </aside>

        <article className="research-detail" aria-busy={detailLoading} aria-label="Research 详情">
          {visibleDetailError === null ? null : (
            <p className="form-error" role="alert">
              {visibleDetailError}
            </p>
          )}
          {detailLoading ? (
            <div className="research-empty">正在读取 Brief、Trace、Claim 和草稿…</div>
          ) : visibleDetail === null ? (
            <div className="research-empty">选择一个 Research Run 查看正式时间线。</div>
          ) : (
            <>
              <header className="research-detail__header">
                <div>
                  <span className={`status-pill status-pill--${visibleDetail.agent_status}`}>
                    {runStatusNames[visibleDetail.agent_status] ?? visibleDetail.agent_status}
                  </span>
                  <h2>{visibleDetail.brief.original_question}</h2>
                  <small>
                    {visibleDetail.graph_version} · Brief r{visibleDetail.brief.revision} · State
                    schema {visibleDetail.state_schema_version}
                  </small>
                </div>
                {detailIsActive && canManage ? (
                  <button
                    className="danger-button"
                    disabled={cancelling}
                    onClick={() => void cancelSelected()}
                    type="button"
                  >
                    {cancelling ? "正在请求取消…" : "取消 Research"}
                  </button>
                ) : null}
              </header>

              <section className="research-comparison" aria-label="L0 L2 L3 对照入口">
                <div>
                  <strong>L0 → L2 → L3 同题对照</strong>
                  <span>
                    先在 Agent 运行正式 L0/L2，再回到本 Run 比较
                    Trace、Evidence、Token、费用和延迟。
                  </span>
                </div>
                <button
                  onClick={() => {
                    onOpenAgent(visibleDetail.brief.original_question, "none");
                  }}
                  type="button"
                >
                  准备 L0
                </button>
                <button
                  onClick={() => {
                    onOpenAgent(visibleDetail.brief.original_question, "web");
                  }}
                  type="button"
                >
                  准备 L2
                </button>
              </section>

              <dl className="research-metrics">
                <div>
                  <dt>当前节点</dt>
                  <dd>
                    {visibleDetail.current_node === null
                      ? "尚未开始"
                      : nodeNames[visibleDetail.current_node]}
                  </dd>
                </div>
                <div>
                  <dt>Step / Event</dt>
                  <dd>
                    {visibleDetail.step_count} / {visibleDetail.event_count}
                  </dd>
                </div>
                <div>
                  <dt>Token</dt>
                  <dd>
                    {visibleDetail.input_tokens_used} in / {visibleDetail.output_tokens_used} out
                  </dd>
                </div>
                <div>
                  <dt>费用</dt>
                  <dd>{formatCost(visibleDetail.cost_micro_usd)}</dd>
                </div>
                <div>
                  <dt>停止原因</dt>
                  <dd>{visibleDetail.stop_reason ?? "尚未终止"}</dd>
                </div>
                <div>
                  <dt>截止时间</dt>
                  <dd>{new Date(visibleDetail.brief.budget.deadline).toLocaleString("zh-CN")}</dd>
                </div>
              </dl>

              <section className="research-card">
                <div className="research-section-heading">
                  <h3>ResearchBrief</h3>
                  <span>用户已确认</span>
                </div>
                <dl className="research-brief-grid">
                  <div>
                    <dt>确认范围</dt>
                    <dd>{visibleDetail.brief.confirmed_scope.join("；")}</dd>
                  </div>
                  <div>
                    <dt>排除项</dt>
                    <dd>{visibleDetail.brief.exclusions.join("；") || "无"}</dd>
                  </div>
                  <div>
                    <dt>完成标准</dt>
                    <dd>{visibleDetail.brief.completion_criteria.join("；")}</dd>
                  </div>
                  <div>
                    <dt>预算</dt>
                    <dd>
                      {visibleDetail.brief.budget.max_steps} steps /{" "}
                      {visibleDetail.brief.budget.max_total_tokens} Token /{" "}
                      {formatCost(visibleDetail.brief.budget.max_cost_micro_usd)}
                    </dd>
                  </div>
                </dl>
              </section>

              <section className="research-card">
                <div className="research-section-heading">
                  <h3>Plan</h3>
                  <span>
                    {visibleDetail.plan === null
                      ? "等待执行"
                      : `r${String(visibleDetail.plan.revision)}`}
                  </span>
                </div>
                {visibleDetail.plan === null ? (
                  <div className="research-empty">Planner 尚未提交正式计划。</div>
                ) : (
                  <>
                    <p>{visibleDetail.plan.planner_summary}</p>
                    <ol className="research-plan-list">
                      {visibleDetail.plan.actions.map((action) => (
                        <li key={action.ordinal}>
                          <strong>{action.objective}</strong>
                          <span>{action.allowed_tool_names.join(", ")}</span>
                        </li>
                      ))}
                    </ol>
                  </>
                )}
              </section>

              <section className="research-card">
                <div className="research-section-heading">
                  <h3>Research 时间线</h3>
                  <span>{events.length} 个节点事件</span>
                </div>
                {events.length === 0 ? (
                  <div className="research-empty">Run 仍在排队，尚无已提交节点事件。</div>
                ) : (
                  <ol className="research-timeline">
                    {events.map((event) => {
                      const node = String(event.details.node ?? "unknown");
                      return (
                        <li key={`${String(event.sequence)}-${event.event_type}`}>
                          <span
                            className={`research-timeline__marker research-timeline__marker--${event.event_type.split("_").at(-1) ?? "started"}`}
                          />
                          <div>
                            <strong>{nodeNames[node] ?? node}</strong>
                            <span>
                              {event.event_type.replace("agent.research.node_", "")} · sequence{" "}
                              {event.sequence}
                            </span>
                          </div>
                        </li>
                      );
                    })}
                  </ol>
                )}
                {trace === null ? null : (
                  <details>
                    <summary>统一 Runtime Step（{trace.steps.length}）</summary>
                    <ol className="research-step-list">
                      {trace.steps.map((step) => (
                        <li key={step.step_id}>
                          {step.sequence}. {step.kind} · {step.status} ·{" "}
                          {step.usage.input_tokens + step.usage.output_tokens} Token
                        </li>
                      ))}
                    </ol>
                  </details>
                )}
              </section>

              <section className="research-card">
                <div className="research-section-heading">
                  <h3>Evidence / Claim</h3>
                  <span>{claims.length} Claims</span>
                </div>
                {claims.length === 0 ? (
                  <div className="research-empty">尚未提交 Claim；失败或取消不会伪造结果。</div>
                ) : (
                  <div className="research-claim-list">
                    {claims.map((claim) => (
                      <article key={claim.id}>
                        <div>
                          <strong>{claim.statement}</strong>
                          <span className={`status-pill status-pill--${claim.verification_status}`}>
                            {claim.verification_status}
                          </span>
                        </div>
                        <small>
                          coverage {(claim.coverage * 100).toFixed(0)}% · conflict{" "}
                          {claim.conflict ? "yes" : "no"}
                        </small>
                        {claim.relations.map((relation) => (
                          <button
                            key={relation.evidence.id}
                            onClick={() => {
                              onOpenEvidence(relation.evidence.id);
                            }}
                            type="button"
                          >
                            {relation.relation} · {relation.evidence.title} ·{" "}
                            {relation.evidence.status}
                          </button>
                        ))}
                      </article>
                    ))}
                  </div>
                )}
                <button
                  className="secondary-button"
                  onClick={() => {
                    onOpenEvidence(null);
                  }}
                  type="button"
                >
                  查看完整 Evidence/Claim 图
                </button>
              </section>

              <section className="research-card research-draft">
                <div className="research-section-heading">
                  <h3>L3 草稿</h3>
                  <span>{visibleDetail.draft?.status ?? "尚未生成"}</span>
                </div>
                {visibleDetail.draft === null ? (
                  <div className="research-empty">只有 graph 安全完成后才保存草稿。</div>
                ) : (
                  <>
                    {visibleDetail.draft.uncertainty_summary === null ? null : (
                      <p className="research-uncertainty">
                        不确定项：{visibleDetail.draft.uncertainty_summary}
                      </p>
                    )}
                    <SafeMarkdown content={visibleDetail.draft.content_markdown} />
                  </>
                )}
              </section>
            </>
          )}
        </article>
      </div>
    </section>
  );
}
