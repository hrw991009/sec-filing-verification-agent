import type { AgentStreamEvent, AgentTrace } from "./chat-api";
import type { ActiveRun, LoadState } from "./chat-workbench-model";
import { eventNames, formatCost, runStatusNames, sourceNames } from "./chat-workbench-model";
import { Icon } from "./icons";

interface TracePanelProps {
  readonly activeRun: ActiveRun | null;
  readonly events: readonly (AgentStreamEvent | AgentTrace["events"][number])[];
  readonly onClose: () => void;
  readonly onRetry: (() => void) | undefined;
  readonly trace: AgentTrace | null;
  readonly traceError: string | null;
  readonly traceState: LoadState;
}

export function TracePanel({
  activeRun,
  events,
  onClose,
  onRetry,
  trace,
  traceError,
  traceState,
}: TracePanelProps) {
  const manifest = trace?.context_manifests[0];
  const status = activeRun?.status ?? trace?.run.status;
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
                <strong>
                  {trace?.run.run_type === "direct_answer" ? "Direct Answer L0" : "Agent Run"}
                </strong>
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
            {manifest === undefined ? null : (
              <section className="trace-section">
                <div className="trace-section__title">
                  <span>上下文组成</span>
                  <span>{manifest.compiler_version}</span>
                </div>
                <ol className="source-list">
                  {manifest.sources.map((source) => (
                    <li
                      className={`source-item${source.included ? "" : " source-item--excluded"}`}
                      key={`${String(source.ordinal)}-${source.source_id}`}
                    >
                      <strong>{sourceNames[source.source_kind] ?? source.source_kind}</strong>
                      <span>
                        {source.included
                          ? `${String(source.estimated_token_count)} Token · 已送入模型`
                          : source.decision_reason}
                      </span>
                    </li>
                  ))}
                </ol>
              </section>
            )}
            <section className="trace-section">
              <div className="trace-section__title">
                <span>已提交事件</span>
                <span>{events.length}</span>
              </div>
              <ol className="event-list">
                {events.map((event) => {
                  const type = "event_type" in event ? event.event_type : event.type;
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
