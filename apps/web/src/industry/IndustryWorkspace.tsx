import { useCallback, useEffect, useState } from "react";

import { ApiProblem } from "../api/api";
import {
  createCollectionSchedule,
  listCollectionRuns,
  listCollectionSchedules,
  listProviderStatuses,
  listSourceItems,
  setIndustryPreference,
  triggerCollection,
  type CollectionRun,
  type CollectionSchedule,
  type Industry,
  type ProviderStatus,
  type SourceItem,
  type SourceKind,
} from "./industry-api";
import "./industry.css";
import { safePublicLocator } from "./safe-public-locator";

const sourceKinds: readonly SourceKind[] = ["news", "policy", "tender", "stock"];
const kindNames: Readonly<Record<SourceKind, string>> = {
  news: "新闻资讯",
  policy: "政策",
  stock: "行情",
  tender: "招投标",
};

interface IndustryWorkspaceProps {
  readonly canManage: boolean;
  readonly industries: readonly Industry[];
  readonly selectedIndustryId: string | null;
  readonly workspaceId: string;
  readonly onSelectIndustry: (industryId: string) => void;
}

function message(error: unknown): string {
  return error instanceof ApiProblem
    ? `${error.message}${error.traceId === null ? "" : `（追踪号 ${error.traceId}）`}`
    : "行业数据暂时无法加载，请稍后重试。";
}

export function IndustryWorkspace({
  canManage,
  industries,
  onSelectIndustry,
  selectedIndustryId,
  workspaceId,
}: IndustryWorkspaceProps) {
  const [kind, setKind] = useState<SourceKind>("news");
  const [items, setItems] = useState<SourceItem[]>([]);
  const [providers, setProviders] = useState<ProviderStatus[]>([]);
  const [runs, setRuns] = useState<CollectionRun[]>([]);
  const [schedules, setSchedules] = useState<CollectionSchedule[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async (): Promise<void> => {
    if (selectedIndustryId === null) {
      setItems([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const [nextItems, nextProviders, nextRuns, nextSchedules] = await Promise.all([
        listSourceItems(workspaceId, selectedIndustryId, kind),
        listProviderStatuses(workspaceId),
        listCollectionRuns(workspaceId),
        listCollectionSchedules(workspaceId),
      ]);
      setItems(nextItems);
      setProviders(nextProviders);
      setRuns(nextRuns);
      setSchedules(nextSchedules);
    } catch (caught: unknown) {
      setError(message(caught));
    } finally {
      setLoading(false);
    }
  }, [kind, selectedIndustryId, workspaceId]);

  useEffect(() => {
    const handle = window.setTimeout(() => {
      void refresh();
    }, 0);
    return () => {
      window.clearTimeout(handle);
    };
  }, [refresh]);

  async function changeIndustry(industryId: string): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      const preference = await setIndustryPreference(workspaceId, industryId);
      onSelectIndustry(preference.industry.id);
    } catch (caught: unknown) {
      setError(message(caught));
    } finally {
      setBusy(false);
    }
  }

  async function ensureSchedule(): Promise<void> {
    if (!canManage || selectedIndustryId === null) return;
    setBusy(true);
    setError(null);
    try {
      await createCollectionSchedule(workspaceId, selectedIndustryId, kind);
      await refresh();
    } catch (caught: unknown) {
      setError(message(caught));
    } finally {
      setBusy(false);
    }
  }

  async function runNow(scheduleId: string): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      await triggerCollection(workspaceId, scheduleId);
      await refresh();
    } catch (caught: unknown) {
      setError(message(caught));
    } finally {
      setBusy(false);
    }
  }

  const selectedIndustry = industries.find((item) => item.id === selectedIndustryId);
  const selectedSchedules = schedules.filter(
    (item) => item.industry_id === selectedIndustryId && item.kind === kind,
  );
  return (
    <section className="business-page" aria-labelledby="industry-page-title">
      <header className="business-page__header">
        <div>
          <p className="eyebrow">CURRENT INDUSTRY</p>
          <h1 id="industry-page-title">行业情报</h1>
          <p>当前行业会写入 Turn 快照，并约束推荐、采集和 Web Tool 的参数。</p>
        </div>
        <label className="business-select">
          当前行业
          <select
            aria-label="当前行业"
            disabled={busy || industries.length === 0}
            onChange={(event) => void changeIndustry(event.currentTarget.value)}
            value={selectedIndustryId ?? ""}
          >
            {industries.map((industry) => (
              <option key={industry.id} value={industry.id}>
                {industry.name}
              </option>
            ))}
          </select>
        </label>
      </header>

      {error === null ? null : (
        <div className="business-alert" role="alert">
          {error}
          <button onClick={() => void refresh()} type="button">
            重试
          </button>
        </div>
      )}

      <div className="provider-grid" aria-label="来源就绪状态">
        {providers.map((provider) => (
          <article className="metric-card" key={`${provider.provider}-${provider.kind}`}>
            <span>{kindNames[provider.kind]}</span>
            <strong>{provider.provider}</strong>
            <small className={`status-text status-text--${provider.readiness}`}>
              {provider.readiness}
              {provider.reason_code === null ? "" : ` · ${provider.reason_code}`}
            </small>
          </article>
        ))}
      </div>

      <nav className="business-tabs" aria-label="行业内容类型">
        {sourceKinds.map((sourceKind) => (
          <button
            aria-current={kind === sourceKind ? "page" : undefined}
            className={kind === sourceKind ? "business-tab business-tab--active" : "business-tab"}
            key={sourceKind}
            onClick={() => {
              setKind(sourceKind);
            }}
            type="button"
          >
            {kindNames[sourceKind]}
          </button>
        ))}
      </nav>

      <div className="business-columns">
        <section className="business-card" aria-busy={loading}>
          <div className="business-card__heading">
            <div>
              <h2>
                {selectedIndustry?.name ?? "行业"} · {kindNames[kind]}
              </h2>
              <p>只展示正式来源投影；Observation 尚不等于 Evidence。</p>
            </div>
            <button className="compact-button" onClick={() => void refresh()} type="button">
              刷新
            </button>
          </div>
          {loading ? (
            <p className="business-empty">正在读取已持久化来源…</p>
          ) : items.length === 0 ? (
            <p className="business-empty">尚无已采集内容。可配置计划或手动运行一次采集。</p>
          ) : (
            <ol className="source-cards">
              {items.map((item) => (
                <li key={item.id}>
                  <div className="source-card__meta">
                    <span>{item.provider}</span>
                    <time dateTime={item.published_at}>
                      {new Date(item.published_at).toLocaleString("zh-CN")}
                    </time>
                  </div>
                  <h3>{item.title}</h3>
                  <p>{item.summary}</p>
                  {safePublicLocator(item.locator) === null ? (
                    <span className="status-text status-text--unavailable">来源地址未通过校验</span>
                  ) : (
                    <a
                      href={safePublicLocator(item.locator) ?? undefined}
                      rel="noreferrer noopener"
                      target="_blank"
                    >
                      查看原始来源
                    </a>
                  )}
                </li>
              ))}
            </ol>
          )}
        </section>

        <aside className="business-card collection-card">
          <div className="business-card__heading">
            <div>
              <h2>采集计划与 Job</h2>
              <p>Schedule → Occurrence → Job/Outbox → Worker</p>
            </div>
            {canManage ? (
              <button disabled={busy} onClick={() => void ensureSchedule()} type="button">
                配置 6 小时计划
              </button>
            ) : null}
          </div>
          {selectedSchedules.length === 0 ? (
            <p className="business-empty">当前类型尚无计划。</p>
          ) : (
            <ul className="schedule-list">
              {selectedSchedules.map((schedule) => (
                <li key={schedule.id}>
                  <strong>{schedule.cron_expression}</strong>
                  <span>
                    {schedule.timezone_name} · {schedule.misfire_policy}
                  </span>
                  {canManage ? (
                    <button disabled={busy} onClick={() => void runNow(schedule.id)} type="button">
                      立即运行
                    </button>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
          <h3>最近运行</h3>
          <ol className="run-list">
            {runs.slice(0, 6).map((run) => (
              <li key={run.id}>
                <span>
                  {kindNames[run.kind]} · {run.provider}
                </span>
                <strong>{run.status}</strong>
                <small>
                  新增 {run.inserted_count} · 重复 {run.duplicate_count}
                </small>
              </li>
            ))}
          </ol>
        </aside>
      </div>
    </section>
  );
}
