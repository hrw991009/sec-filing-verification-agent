import { useCallback, useEffect, useMemo, useRef, useState, type SubmitEvent } from "react";

import type { Industry } from "../industry/industry-api";
import { listKnowledgeBases, type KnowledgeBase } from "../knowledge/knowledge-api";
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
  decideResearchApproval,
  decideMonitorSubscription,
  getResearchDurability,
  getResearchRun,
  listResearchRuns,
  resumeResearch,
  startResearch,
  type ResearchApproval,
  type ResearchDurability,
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
  paused: "等待确认",
};

const approvalStatusNames: Readonly<Record<string, string>> = {
  allowed: "已允许",
  denied: "已拒绝",
  pending: "等待决定",
  timed_out: "已超时",
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

function textArgument(value: unknown): string {
  return typeof value === "string" ? value : "-";
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
  const [durability, setDurability] = useState<ResearchDurability | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailRefreshRevision, setDetailRefreshRevision] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [deciding, setDeciding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<{
    readonly researchRunId: string;
    readonly message: string;
  } | null>(null);
  const [originalQuestion, setOriginalQuestion] = useState("");
  const [mode, setMode] = useState<"web" | "local">("web");
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [selectedKnowledgeBaseId, setSelectedKnowledgeBaseId] = useState<string | null>(null);
  const [cik, setCik] = useState("0000320193");
  const [accession, setAccession] = useState("0000320193-23-000106");
  const [form, setForm] = useState<"10-K" | "10-Q">("10-K");
  const [reportPeriod, setReportPeriod] = useState("2023-09-30");
  const [asOf, setAsOf] = useState("2023-11-03T12:00");
  const [unit, setUnit] = useState("USD");
  const [scale, setScale] = useState(6);
  const [requireAmbiguityApproval, setRequireAmbiguityApproval] = useState(false);
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
        setDurability(null);
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
    let active = true;
    void listKnowledgeBases(workspaceId)
      .then((loaded) => {
        if (!active) return;
        setKnowledgeBases(loaded);
        setSelectedKnowledgeBaseId((current) =>
          current !== null && loaded.some((item) => item.id === current)
            ? current
            : (loaded[0]?.id ?? null),
        );
      })
      .catch((caught: unknown) => {
        if (active) setError(publicError(caught));
      });
    return () => {
      active = false;
    };
  }, [workspaceId]);

  useEffect(() => {
    if (selectedId === null) return;
    const requestNumber = detailRequestRef.current + 1;
    detailRequestRef.current = requestNumber;
    void getResearchRun(workspaceId, selectedId)
      .then(async (loaded) => {
        if (detailRequestRef.current !== requestNumber) return;
        const [loadedTrace, loadedClaims, loadedDurability] = await Promise.allSettled([
          getAgentTrace(workspaceId, loaded.agent_run_id),
          listResearchClaims(workspaceId, loaded.id, 100),
          getResearchDurability(workspaceId, loaded.id),
        ]);
        if (detailRequestRef.current !== requestNumber) return;
        setDetail(loaded);
        setTrace(loadedTrace.status === "fulfilled" ? loadedTrace.value : null);
        setClaims(loadedClaims.status === "fulfilled" ? loadedClaims.value : []);
        setDurability(loadedDurability.status === "fulfilled" ? loadedDurability.value : null);
        const failures = [loadedTrace, loadedClaims, loadedDurability]
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
        setDurability(null);
        setDetailError({ message: publicError(caught), researchRunId: selectedId });
      });
  }, [detailRefreshRevision, selectedId, workspaceId]);

  const scopeItems = useMemo(() => lines(confirmedScope), [confirmedScope]);
  const criteriaItems = useMemo(() => lines(completionCriteria), [completionCriteria]);
  const sourceReady =
    mode === "web"
      ? selectedIndustryId !== null
      : selectedKnowledgeBaseId !== null &&
        cik.trim() !== "" &&
        accession.trim() !== "" &&
        reportPeriod !== "" &&
        asOf !== "" &&
        unit.trim() !== "";
  const canSubmit =
    canManage &&
    sourceReady &&
    originalQuestion.trim() !== "" &&
    scopeItems.length > 0 &&
    criteriaItems.length > 0 &&
    !submitting;

  async function submit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!canSubmit) return;
    if (mode === "local" && selectedKnowledgeBaseId === null) return;
    const commonRequest = {
      completion_criteria: criteriaItems,
      confirmed_scope: scopeItems,
      exclusions: lines(exclusions),
      max_cost_micro_usd: maxCostMicroUsd,
      max_steps: maxSteps,
      max_total_tokens: maxTotalTokens,
      mode,
      original_question: originalQuestion.trim(),
      timeout_seconds: timeoutSeconds,
    };
    let request: StartResearchRequest;
    if (mode === "web") {
      request = { ...commonRequest, industry_id: selectedIndustryId, mode };
    } else {
      if (selectedKnowledgeBaseId === null) return;
      request = {
        ...commonRequest,
        financial_scope: {
          accession: accession.trim(),
          as_of: new Date(asOf).toISOString(),
          cik: cik.trim(),
          form,
          report_period: reportPeriod,
          scale,
          schema_version: 1,
          unit: unit.trim().toUpperCase(),
        },
        knowledge_base_ids: [selectedKnowledgeBaseId],
        mode,
        ...(requireAmbiguityApproval
          ? { approval_reason: "company_or_period_ambiguity" as const }
          : {}),
      };
    }
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

  async function decideApproval(
    approval: ResearchApproval,
    outcome: "allow" | "deny",
  ): Promise<void> {
    if (detail === null) return;
    setDeciding(true);
    setDetailError(null);
    try {
      const request = {
        approval_request_id: approval.approval_request_id,
        checkpoint_revision: approval.checkpoint_revision,
        outcome,
      } as const;
      if (approval.reason === "monitor_subscription") {
        await decideMonitorSubscription(workspaceId, detail.id, request);
      } else {
        const decided = await decideResearchApproval(workspaceId, detail.id, request);
        if (outcome !== "allow") {
          await loadRuns(detail.id);
          return;
        }
        const resumeToken = decided.resume_token;
        if (resumeToken === null || resumeToken === undefined) {
          throw new Error("审批已允许，但服务端没有返回可用的恢复凭据。");
        }
        await resumeResearch(workspaceId, detail.id, {
          approval_request_id: decided.approval_request_id,
          checkpoint_revision: decided.checkpoint_revision,
          resume_token: resumeToken,
        });
      }
      await loadRuns(detail.id);
    } catch (caught: unknown) {
      setDetailError({ message: publicError(caught), researchRunId: detail.id });
    } finally {
      setDeciding(false);
    }
  }

  async function resumeAllowedApproval(approval: ResearchApproval): Promise<void> {
    const resumeToken = approval.resume_token;
    if (detail === null || resumeToken === null || resumeToken === undefined) return;
    setDeciding(true);
    setDetailError(null);
    try {
      await resumeResearch(workspaceId, detail.id, {
        approval_request_id: approval.approval_request_id,
        checkpoint_revision: approval.checkpoint_revision,
        resume_token: resumeToken,
      });
      await loadRuns(detail.id);
    } catch (caught: unknown) {
      setDetailError({ message: publicError(caught), researchRunId: detail.id });
    } finally {
      setDeciding(false);
    }
  }

  const events = nodeEvents(trace);
  const visibleDetail = detail?.id === selectedId ? detail : null;
  const visibleDetailError = detailError?.researchRunId === selectedId ? detailError.message : null;
  const detailLoading =
    selectedId !== null && visibleDetail === null && visibleDetailError === null;
  const detailIsActive =
    visibleDetail?.agent_status === "queued" ||
    visibleDetail?.agent_status === "running" ||
    visibleDetail?.agent_status === "paused";
  const latestApproval = durability?.approvals.at(-1) ?? null;
  const relatedEvidence = useMemo(() => {
    const unique = new Map<string, ResearchClaim["relations"][number]["evidence"]>();
    for (const claim of claims) {
      for (const relation of claim.relations) {
        unique.set(relation.evidence.id, relation.evidence);
      }
    }
    return [...unique.values()];
  }, [claims]);
  const retrievalEvidence = relatedEvidence.filter((evidence) =>
    ["sec_filing_chunk_v1", "sec_filing_text_v1", "sec_xbrl_fact_v1"].includes(
      evidence.locator.locator_type,
    ),
  );
  const calculationEvidence = relatedEvidence.filter(
    (evidence) => evidence.locator.locator_type === "financial_calculation_v1",
  );
  const excludedContext =
    trace?.context_manifests.flatMap((manifest) =>
      manifest.sources.filter((source) => !source.included),
    ) ?? [];
  const diffRequests =
    trace?.events.filter(
      (event) =>
        event.event_type === "agent.tool.requested" &&
        event.details.requested_tool_name === "sec.diff_filings",
    ) ?? [];
  const completedToolCalls = new Set(
    trace?.events
      .filter((event) => event.event_type === "agent.tool.completed")
      .map((event) => String(event.details.call_id ?? "")) ?? [],
  );

  return (
    <section className="research-workspace" aria-label="Research L4 工作台">
      <header className="workspace-page-header">
        <div>
          <span className="eyebrow">Day 7 · sec-l4-v1</span>
          <h1>Research Workbench</h1>
          <p>
            显式确认 Brief，经唯一 Runtime/Tool loop 生成可解释草稿，并以持久 Checkpoint、HITL
            和恢复事实解释执行过程。
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
            <h2>新建 L4 Research</h2>
            <div className="research-mode" role="group" aria-label="Research 数据源">
              <button
                aria-pressed={mode === "web"}
                disabled={!canManage || submitting}
                onClick={() => {
                  setMode("web");
                }}
                type="button"
              >
                公开网页
              </button>
              <button
                aria-pressed={mode === "local"}
                disabled={!canManage || submitting}
                onClick={() => {
                  setMode("local");
                }}
                type="button"
              >
                SEC Filing
              </button>
            </div>
            {mode === "web" ? (
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
            ) : (
              <fieldset className="research-financial-scope">
                <legend>SEC filing 范围</legend>
                <label>
                  Knowledge Base
                  <select
                    aria-label="Research Knowledge Base"
                    disabled={!canManage || submitting}
                    onChange={(event) => {
                      setSelectedKnowledgeBaseId(event.currentTarget.value || null);
                    }}
                    value={selectedKnowledgeBaseId ?? ""}
                  >
                    <option value="">选择知识库</option>
                    {knowledgeBases.map((knowledgeBase) => (
                      <option key={knowledgeBase.id} value={knowledgeBase.id}>
                        {knowledgeBase.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  CIK
                  <input
                    aria-label="Research CIK"
                    disabled={!canManage || submitting}
                    inputMode="numeric"
                    maxLength={10}
                    onChange={(event) => {
                      setCik(event.currentTarget.value);
                    }}
                    pattern="[0-9]{10}"
                    required
                    value={cik}
                  />
                </label>
                <label>
                  Accession
                  <input
                    aria-label="Research accession"
                    disabled={!canManage || submitting}
                    onChange={(event) => {
                      setAccession(event.currentTarget.value);
                    }}
                    pattern="[0-9]{10}-[0-9]{2}-[0-9]{6}"
                    required
                    value={accession}
                  />
                </label>
                <label>
                  Form
                  <select
                    aria-label="Research form"
                    disabled={!canManage || submitting}
                    onChange={(event) => {
                      setForm(event.currentTarget.value as "10-K" | "10-Q");
                    }}
                    value={form}
                  >
                    <option value="10-K">10-K</option>
                    <option value="10-Q">10-Q</option>
                  </select>
                </label>
                <label>
                  Report period
                  <input
                    aria-label="Research report period"
                    disabled={!canManage || submitting}
                    onChange={(event) => {
                      setReportPeriod(event.currentTarget.value);
                    }}
                    required
                    type="date"
                    value={reportPeriod}
                  />
                </label>
                <label>
                  As of
                  <input
                    aria-label="Research as of"
                    disabled={!canManage || submitting}
                    onChange={(event) => {
                      setAsOf(event.currentTarget.value);
                    }}
                    required
                    type="datetime-local"
                    value={asOf}
                  />
                </label>
                <label>
                  Unit
                  <input
                    aria-label="Research unit"
                    disabled={!canManage || submitting}
                    maxLength={16}
                    onChange={(event) => {
                      setUnit(event.currentTarget.value);
                    }}
                    required
                    value={unit}
                  />
                </label>
                <label>
                  Scale
                  <input
                    aria-label="Research scale"
                    disabled={!canManage || submitting}
                    max={12}
                    min={-12}
                    onChange={(event) => {
                      setScale(event.currentTarget.valueAsNumber);
                    }}
                    required
                    type="number"
                    value={scale}
                  />
                </label>
                <label className="research-approval-toggle">
                  <input
                    checked={requireAmbiguityApproval}
                    disabled={!canManage || submitting}
                    onChange={(event) => {
                      setRequireAmbiguityApproval(event.currentTarget.checked);
                    }}
                    type="checkbox"
                  />
                  公司或期间存在歧义，计划后暂停确认
                </label>
              </fieldset>
            )}
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
                  {visibleDetail.brief.financial_scope === null ? null : (
                    <div>
                      <dt>FinancialScope</dt>
                      <dd>
                        {visibleDetail.brief.financial_scope.cik} ·{" "}
                        {visibleDetail.brief.financial_scope.accession} ·{" "}
                        {visibleDetail.brief.financial_scope.form} ·{" "}
                        {visibleDetail.brief.financial_scope.report_period} ·{" "}
                        {visibleDetail.brief.financial_scope.unit} ×10^
                        {visibleDetail.brief.financial_scope.scale}
                      </dd>
                    </div>
                  )}
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

              <section className="research-card research-financial-audit">
                <div className="research-section-heading">
                  <h3>SEC 审查链</h3>
                  <span>同一 Trace / FinancialScope</span>
                </div>
                <div className="research-audit-grid">
                  <div>
                    <span>Retrieval</span>
                    <strong>{retrievalEvidence.length}</strong>
                    <small>已进入 Claim 的 Filing / XBRL Evidence</small>
                  </div>
                  <div>
                    <span>Context exclusions</span>
                    <strong>{excludedContext.length}</strong>
                    <small>由 financial-context-v1 manifest 记录</small>
                  </div>
                  <div>
                    <span>Calculation</span>
                    <strong>{calculationEvidence.length}</strong>
                    <small>确定性 Decimal Evidence</small>
                  </div>
                  <div>
                    <span>Filing diff</span>
                    <strong>{diffRequests.length}</strong>
                    <small>sec.diff_filings@v1 调用</small>
                  </div>
                </div>

                {excludedContext.length === 0 ? null : (
                  <details className="research-audit-details">
                    <summary>Context 排除项（{excludedContext.length}）</summary>
                    <ol>
                      {excludedContext.map((source, index) => (
                        <li key={`${source.source_id}-${String(index)}`}>
                          <strong>{source.source_kind}</strong>
                          <span>{source.decision_reason}</span>
                          <small>{source.source_version}</small>
                        </li>
                      ))}
                    </ol>
                  </details>
                )}

                {calculationEvidence.length === 0 ? null : (
                  <div className="research-calculation-list">
                    {calculationEvidence.map((evidence) => {
                      if (evidence.locator.locator_type !== "financial_calculation_v1") {
                        return null;
                      }
                      return (
                        <article key={evidence.id}>
                          <div>
                            <strong>{evidence.locator.formula}</strong>
                            <span>
                              {evidence.locator.result} {evidence.locator.unit} ×10^
                              {evidence.locator.scale}
                            </span>
                          </div>
                          <dl>
                            <div>
                              <dt>Operator</dt>
                              <dd>{evidence.locator.operator}</dd>
                            </div>
                            <div>
                              <dt>Reconciliation</dt>
                              <dd>
                                {evidence.locator.reconciliation_status ?? "legacy_unrecorded"}
                              </dd>
                            </div>
                            <div>
                              <dt>Lineage</dt>
                              <dd>{evidence.locator.input_evidence_refs.length} inputs</dd>
                            </div>
                          </dl>
                          <button
                            onClick={() => {
                              onOpenEvidence(evidence.id);
                            }}
                            type="button"
                          >
                            反查 Calculation Citation
                          </button>
                        </article>
                      );
                    })}
                  </div>
                )}

                {diffRequests.length === 0 ? null : (
                  <ol className="research-diff-events">
                    {diffRequests.map((event) => {
                      const callId = String(event.details.call_id ?? "");
                      return (
                        <li key={`${String(event.sequence)}-${callId}`}>
                          <strong>sec.diff_filings@v1</strong>
                          <span>
                            {completedToolCalls.has(callId) ? "completed" : "not_completed"}
                          </span>
                          <small>Call {callId.slice(0, 8)}</small>
                        </li>
                      );
                    })}
                  </ol>
                )}

                {retrievalEvidence.length === 0 ? null : (
                  <div className="research-citation-list">
                    {retrievalEvidence.map((evidence) => (
                      <button
                        key={evidence.id}
                        onClick={() => {
                          onOpenEvidence(evidence.id);
                        }}
                        type="button"
                      >
                        {evidence.locator.locator_type} · {evidence.title}
                      </button>
                    ))}
                  </div>
                )}
              </section>

              <section className="research-card research-durability">
                <div className="research-section-heading">
                  <h3>Checkpoint / HITL</h3>
                  <span>
                    {durability === null
                      ? "读取失败"
                      : `${String(durability.checkpoints.length)} checkpoints`}
                  </span>
                </div>
                {durability === null ? (
                  <div className="research-empty">暂时无法读取持久恢复事实。</div>
                ) : (
                  <>
                    <div className="research-durability-summary">
                      <span>重复副作用</span>
                      <strong>{durability.duplicate_side_effect_count}</strong>
                    </div>
                    {durability.checkpoints.length === 0 ? (
                      <div className="research-empty">Run 尚未提交首个 Checkpoint。</div>
                    ) : (
                      <ol className="research-checkpoint-list">
                        {durability.checkpoints.map((checkpoint) => (
                          <li key={checkpoint.checkpoint_id}>
                            <strong>
                              r{checkpoint.revision} ·{" "}
                              {nodeNames[checkpoint.node] ?? checkpoint.node}
                            </strong>
                            <span>
                              下一节点：
                              {checkpoint.next_node === null
                                ? "最终提交"
                                : (nodeNames[checkpoint.next_node] ?? checkpoint.next_node)}
                            </span>
                            <small>
                              State r{checkpoint.run_state_revision} ·{" "}
                              {new Date(checkpoint.saved_at).toLocaleString("zh-CN")}
                            </small>
                          </li>
                        ))}
                      </ol>
                    )}
                    {latestApproval === null ? null : (
                      <div className="research-approval-panel">
                        <div>
                          <strong>
                            {latestApproval.reason === "monitor_subscription"
                              ? "SEC Monitor 订阅审批"
                              : "公司 / 期间歧义确认"}
                          </strong>
                          <span>
                            {approvalStatusNames[latestApproval.status] ?? latestApproval.status} ·
                            Checkpoint r{latestApproval.checkpoint_revision}
                          </span>
                        </div>
                        {latestApproval.tool_arguments == null ? null : (
                          <dl className="research-approval-request">
                            <div>
                              <dt>Tool</dt>
                              <dd>
                                {latestApproval.tool_name}@{latestApproval.tool_version}
                              </dd>
                            </div>
                            <div>
                              <dt>CIK / Schedule</dt>
                              <dd>
                                {textArgument(latestApproval.tool_arguments.cik)} ·{" "}
                                {textArgument(latestApproval.tool_arguments.cron_expression)} ·{" "}
                                {textArgument(latestApproval.tool_arguments.timezone_name)}
                              </dd>
                            </div>
                          </dl>
                        )}
                        {latestApproval.status === "pending" && canManage ? (
                          <div className="research-approval-actions">
                            <button
                              disabled={deciding}
                              onClick={() => void decideApproval(latestApproval, "allow")}
                              type="button"
                            >
                              {deciding ? "正在提交…" : "允许并继续"}
                            </button>
                            <button
                              className="danger-button"
                              disabled={deciding}
                              onClick={() => void decideApproval(latestApproval, "deny")}
                              type="button"
                            >
                              拒绝并终止
                            </button>
                          </div>
                        ) : latestApproval.status === "allowed" &&
                          !latestApproval.resume_claimed &&
                          latestApproval.resume_token !== null &&
                          canManage ? (
                          <button
                            disabled={deciding}
                            onClick={() => void resumeAllowedApproval(latestApproval)}
                            type="button"
                          >
                            {deciding ? "正在恢复…" : "恢复执行"}
                          </button>
                        ) : null}
                      </div>
                    )}
                  </>
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
