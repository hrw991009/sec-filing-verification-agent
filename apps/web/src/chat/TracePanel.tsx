import type { AgentStreamEvent, AgentTrace } from "./chat-api";
import type { ActiveRun, LoadState } from "./chat-workbench-model";
import { eventNames, formatCost, runStatusNames, sourceNames } from "./chat-workbench-model";
import { Icon } from "./icons";

interface TracePanelProps {
  readonly activeRun: ActiveRun | null;
  readonly events: readonly (AgentStreamEvent | AgentTrace["events"][number])[];
  readonly evidencePromotionError: string | null;
  readonly evidencePromotionKey: string | null;
  readonly onClose: () => void;
  readonly onNormalizeObservation: (toolCallId: string, observationId: string) => void;
  readonly onOpenMemory: (memoryId: string) => void;
  readonly onRetry: (() => void) | undefined;
  readonly trace: AgentTrace | null;
  readonly traceError: string | null;
  readonly traceState: LoadState;
}

const toolDetailNames: Readonly<Record<string, string>> = {
  approval_policy: "审批策略",
  call_id: "调用 ID",
  cost_class: "费用等级",
  cost_micro_usd: "实际费用（微美元）",
  duration_ms: "耗时（毫秒）",
  error_code: "稳定错误码",
  max_cost_micro_usd: "费用上限（微美元）",
  max_result_bytes: "结果上限（字节）",
  observation_envelope_sha256: "模型可见信封摘要",
  observation_id: "Observation ID",
  policy_decision: "策略判定",
  policy_reason_code: "判定原因",
  policy_version: "策略版本",
  requested_tool_name: "请求工具",
  requested_tool_version: "请求版本",
  required_capability: "所需能力",
  resolved_tool_name: "执行工具",
  sanitizer_version: "脱敏器版本",
  side_effect_class: "副作用等级",
  timeout_ms: "超时（毫秒）",
  toolset_version: "工具集版本",
  tool_version: "工具版本",
};

const memoryDecisionNames: Readonly<Record<string, string>> = {
  excluded_conflicted: "与更高优先级 Memory 冲突",
  excluded_deleted: "已删除",
  excluded_disabled: "已停用",
  excluded_duplicate: "重复内容",
  excluded_expired: "已过期",
  excluded_negative_feedback: "用户反馈为不相关",
  excluded_not_relevant: "与当前目标不相关",
  excluded_sensitive: "敏感内容策略排除",
  excluded_stale: "来源或事实已过时",
  excluded_token_budget: "Context Token 预算不足",
  not_available: "当前不可用",
};

function traceEventType(event: TracePanelProps["events"][number]): string {
  return "event_type" in event ? event.event_type : event.type;
}

function runTypeName(runType: AgentTrace["run"]["run_type"] | undefined): string {
  if (runType === "direct_answer") return "Direct Answer L0";
  if (runType === "tool_loop") return "Tool Loop L2";
  if (runType === "research") return "Evidence Research L3";
  return "Agent Runtime";
}

export function TracePanel({
  activeRun,
  evidencePromotionError,
  evidencePromotionKey,
  events,
  onClose,
  onNormalizeObservation,
  onOpenMemory,
  onRetry,
  trace,
  traceError,
  traceState,
}: TracePanelProps) {
  const status = activeRun?.status ?? trace?.run.status;
  const toolEvents = trace?.events.filter((event) => event.event_type.startsWith("agent.tool."));
  return (
    <aside className="trace-panel" aria-label="Agent 运行轨迹">
      <header className="trace-panel__header">
        <div>
          <h2>运行轨迹</h2>
          <p>只展示已提交的安全元数据</p>
        </div>
        <button
          aria-label="关闭运行轨迹"
          className="icon-button trace-panel__close"
          onClick={onClose}
          title="关闭运行轨迹"
          type="button"
        >
          <Icon name="close" />
        </button>
      </header>
      <div className="trace-panel__body" aria-busy={traceState === "loading"}>
        {traceState === "loading" ? (
          <div className="chat-skeleton" aria-label="正在加载运行轨迹">
            <span />
            <span />
            <span />
          </div>
        ) : traceError !== null && trace === null && activeRun === null ? (
          <div className="run-state-card run-state-card--error" role="alert">
            <Icon name="refresh" />
            <div>
              {traceError}
              {onRetry === undefined ? null : (
                <div className="run-actions">
                  <button className="compact-button" onClick={onRetry} type="button">
                    重新加载轨迹
                  </button>
                </div>
              )}
            </div>
          </div>
        ) : trace === null && activeRun === null ? (
          <div className="trace-empty">
            <Icon name="bolt" />
            <span>选择一次回答后，这里会显示 Runtime 步骤、上下文组成和用量。</span>
          </div>
        ) : (
          <>
            {traceError === null ? null : (
              <div className="run-state-card run-state-card--error" role="alert">
                {traceError}
              </div>
            )}
            <div className="trace-status">
              <div>
                <strong>{runTypeName(trace?.run.run_type)}</strong>
                <span>{trace?.run.runtime_version ?? "Runtime 正在记录事件"}</span>
              </div>
              <span className={`status-pill status-pill--${status ?? "running"}`}>
                {runStatusNames[status ?? "running"] ?? status ?? "运行中"}
              </span>
            </div>
            <div className="trace-metrics">
              <div className="trace-metric">
                <strong>{trace?.run.usage.input_tokens ?? "—"}</strong>
                <span>输入 Token</span>
              </div>
              <div className="trace-metric">
                <strong>{trace?.run.usage.output_tokens ?? "—"}</strong>
                <span>输出 Token</span>
              </div>
              <div className="trace-metric">
                <strong>{trace === null ? "—" : formatCost(trace.run.usage.cost_micro_usd)}</strong>
                <span>模型费用</span>
              </div>
            </div>
            {trace === null ? null : (
              <section className="trace-section">
                <div className="trace-section__title">
                  <span>步骤</span>
                  <span>{trace.steps.length}</span>
                </div>
                <ol className="step-list">
                  {trace.steps.map((step) => (
                    <li className="step-item" key={step.step_id}>
                      <strong>
                        {step.sequence}.{" "}
                        {step.kind === "model"
                          ? "模型调用"
                          : step.kind === "tool"
                            ? "工具执行"
                            : step.kind === "final"
                              ? "最终输出"
                              : step.kind}
                      </strong>
                      <span>
                        {step.status} · {step.usage.input_tokens + step.usage.output_tokens} Token
                      </span>
                    </li>
                  ))}
                </ol>
              </section>
            )}
            {toolEvents === undefined || toolEvents.length === 0 ? null : (
              <section className="trace-section" aria-label="Tool Inspector">
                <div className="trace-section__title">
                  <span>Tool Inspector</span>
                  <span>{toolEvents.length} 个事实</span>
                </div>
                <ol className="tool-inspector-list">
                  {toolEvents.map((event) => {
                    const callId = event.details.call_id;
                    const observationId = event.details.observation_id;
                    const canNormalize =
                      event.event_type === "agent.tool.completed" &&
                      typeof callId === "string" &&
                      typeof observationId === "string";
                    const promotionKey = canNormalize ? `${callId}:${observationId}` : null;
                    return (
                      <li
                        className="tool-inspector-event"
                        key={`${String(event.sequence)}-${event.event_type}`}
                      >
                        <strong>{eventNames[event.event_type] ?? event.event_type}</strong>
                        <span>sequence {event.sequence}</span>
                        <dl>
                          {Object.entries(event.details)
                            .filter(([name]) => toolDetailNames[name] !== undefined)
                            .map(([name, value]) => (
                              <div key={name}>
                                <dt>{toolDetailNames[name]}</dt>
                                <dd>{String(value)}</dd>
                              </div>
                            ))}
                        </dl>
                        {canNormalize ? (
                          <button
                            className="compact-button tool-inspector-event__promote"
                            disabled={evidencePromotionKey === promotionKey}
                            onClick={() => {
                              onNormalizeObservation(callId, observationId);
                            }}
                            type="button"
                          >
                            {evidencePromotionKey === promotionKey
                              ? "正在校验来源…"
                              : "提升为 Evidence"}
                          </button>
                        ) : null}
                      </li>
                    );
                  })}
                </ol>
                {evidencePromotionError === null ? null : (
                  <p className="trace-promotion-error" role="alert">
                    {evidencePromotionError}
                  </p>
                )}
                <p className="trace-safety-note">
                  仅显示 allowlist 内的策略、预算、稳定错误码与摘要；原始参数、凭据和 Provider
                  响应不会进入 Inspector。
                </p>
              </section>
            )}
            {trace === null || trace.context_manifests.length === 0 ? null : (
              <section className="trace-section">
                <div className="trace-section__title">
                  <span>上下文组成</span>
                  <span>{trace.context_manifests.length} 次模型请求</span>
                </div>
                {trace.context_manifests.map((manifest, manifestIndex) => (
                  <div className="manifest-card" key={manifest.manifest_id}>
                    <strong>
                      请求 {String(manifestIndex + 1)} · {manifest.compiler_version}
                    </strong>
                    <ol className="source-list">
                      {manifest.sources.map((source) => (
                        <li
                          className={`source-item${source.included ? "" : " source-item--excluded"}`}
                          key={`${String(source.ordinal)}-${source.source_id}`}
                        >
                          <strong>{sourceNames[source.source_kind] ?? source.source_kind}</strong>
                          <span>
                            {source.included
                              ? `${String(source.estimated_token_count)} Token · 已送入模型${
                                  source.source_sha256 === null
                                    ? ""
                                    : ` · 摘要 ${source.source_sha256.slice(0, 12)}…`
                                }`
                              : (memoryDecisionNames[source.decision_reason] ??
                                source.decision_reason)}
                          </span>
                          {source.source_kind === "long_term_memory" ? (
                            <div className="source-item__memory">
                              <small>
                                {source.source_scope === "user" ? "仅自己" : "Workspace"} ·{" "}
                                {source.source_version}
                                {source.relevance_score === null
                                  ? ""
                                  : ` · 相关度 ${source.relevance_score.toFixed(2)}`}
                                {source.feedback_score === null
                                  ? ""
                                  : ` · 反馈 ${String(source.feedback_score)}`}
                              </small>
                              <button
                                className="compact-button"
                                onClick={() => {
                                  onOpenMemory(source.source_id);
                                }}
                                type="button"
                              >
                                查看 Memory revision
                              </button>
                            </div>
                          ) : null}
                        </li>
                      ))}
                    </ol>
                  </div>
                ))}
              </section>
            )}
            <section className="trace-section">
              <div className="trace-section__title">
                <span>已提交事件</span>
                <span>{events.length}</span>
              </div>
              <ol className="event-list">
                {events.map((event) => {
                  const type = traceEventType(event);
                  return (
                    <li className="event-item" key={`${String(event.sequence)}-${type}`}>
                      <strong>{eventNames[type] ?? type}</strong>
                      <span>sequence {event.sequence}</span>
                    </li>
                  );
                })}
              </ol>
            </section>
            {trace === null ? null : (
              <section className="trace-section">
                <div className="trace-section__title">停止原因</div>
                <div className="trace-status">
                  <div>
                    <strong>{trace.run.stop_reason ?? "尚未结束"}</strong>
                    <span>Trace ID · {trace.run.trace_id}</span>
                  </div>
                </div>
              </section>
            )}
          </>
        )}
      </div>
    </aside>
  );
}
