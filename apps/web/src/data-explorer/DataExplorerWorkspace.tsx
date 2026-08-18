import { lazy, Suspense, useCallback, useEffect, useMemo, useState, type SubmitEvent } from "react";

import { ApiProblem } from "../api/api";
import {
  browseTableRows,
  ensureSampleConnection,
  executeQuery,
  listDataConnections,
  listQueryRuns,
  listTables,
  testDataConnection,
  type ChartType,
  type DataConnection,
  type DatabaseRows,
  type QueryRun,
  type QueryRunSummary,
  type TableSchema,
} from "./data-explorer-api";
import "./data-explorer.css";

const SafeChart = lazy(async () => {
  const module = await import("./SafeChart");
  return { default: module.SafeChart };
});

const defaultSql =
  "SELECT industry, SUM(revenue) AS total_revenue FROM public.sample_company_metrics GROUP BY industry ORDER BY industry LIMIT 20";

interface DataExplorerWorkspaceProps {
  readonly canManage: boolean;
  readonly workspaceId: string;
}

function message(error: unknown): string {
  return error instanceof ApiProblem
    ? `${error.message}${error.traceId === null ? "" : `（追踪号 ${error.traceId}）`}`
    : "数据库操作失败，请稍后重试。";
}

function displayCell(value: unknown): string {
  if (value === null) return "NULL";
  if (typeof value === "string") return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  return "[不支持的值]";
}

export function DataExplorerWorkspace({ canManage, workspaceId }: DataExplorerWorkspaceProps) {
  const [connections, setConnections] = useState<DataConnection[]>([]);
  const [connectionId, setConnectionId] = useState<string | null>(null);
  const [tables, setTables] = useState<TableSchema[]>([]);
  const [selectedTable, setSelectedTable] = useState<TableSchema | null>(null);
  const [rows, setRows] = useState<DatabaseRows | null>(null);
  const [offset, setOffset] = useState(0);
  const [queryRuns, setQueryRuns] = useState<QueryRunSummary[]>([]);
  const [result, setResult] = useState<QueryRun | null>(null);
  const [question, setQuestion] = useState("按行业汇总样例公司收入");
  const [sql, setSql] = useState(defaultSql);
  const [chartType, setChartType] = useState<ChartType>("bar");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedConnection = useMemo(
    () => connections.find((item) => item.id === connectionId) ?? null,
    [connectionId, connections],
  );

  const refresh = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      const [nextConnections, nextRuns] = await Promise.all([
        listDataConnections(workspaceId),
        listQueryRuns(workspaceId),
      ]);
      setConnections(nextConnections);
      setQueryRuns(nextRuns);
      setConnectionId((current) =>
        nextConnections.some((item) => item.id === current)
          ? current
          : (nextConnections[0]?.id ?? null),
      );
    } catch (caught: unknown) {
      setError(message(caught));
    } finally {
      setLoading(false);
    }
  }, [workspaceId]);

  useEffect(() => {
    const handle = window.setTimeout(() => {
      void refresh();
    }, 0);
    return () => {
      window.clearTimeout(handle);
    };
  }, [refresh]);

  useEffect(() => {
    if (connectionId === null) {
      return;
    }
    let active = true;
    void listTables(workspaceId, connectionId)
      .then((nextTables) => {
        if (!active) return;
        setTables(nextTables);
        setSelectedTable(nextTables[0] ?? null);
      })
      .catch((caught: unknown) => {
        if (active) setError(message(caught));
      });
    return () => {
      active = false;
    };
  }, [connectionId, workspaceId]);

  useEffect(() => {
    if (connectionId === null || selectedTable === null) {
      return;
    }
    let active = true;
    void browseTableRows(workspaceId, connectionId, selectedTable, offset)
      .then((nextRows) => {
        if (active) setRows(nextRows);
      })
      .catch((caught: unknown) => {
        if (active) setError(message(caught));
      });
    return () => {
      active = false;
    };
  }, [connectionId, offset, selectedTable, workspaceId]);

  async function createSample(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      const connection = await ensureSampleConnection(workspaceId);
      await refresh();
      setConnectionId(connection.id);
    } catch (caught: unknown) {
      setError(message(caught));
    } finally {
      setBusy(false);
    }
  }

  async function probeConnection(): Promise<void> {
    if (connectionId === null) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await testDataConnection(workspaceId, connectionId);
      setConnections((current) => current.map((item) => (item.id === updated.id ? updated : item)));
    } catch (caught: unknown) {
      setError(message(caught));
    } finally {
      setBusy(false);
    }
  }

  async function runQuery(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (connectionId === null || !canManage) return;
    setBusy(true);
    setError(null);
    try {
      const next = await executeQuery(workspaceId, {
        chart: {
          chart_type: chartType,
          series_column: null,
          title: "样例公司收入",
          x_column: chartType === "table" ? null : "industry",
          y_column: chartType === "table" ? null : "total_revenue",
        },
        connection_id: connectionId,
        generated_sql: sql.trim(),
        question: question.trim(),
      });
      setResult(next);
      setQueryRuns(await listQueryRuns(workspaceId));
    } catch (caught: unknown) {
      setError(message(caught));
      setQueryRuns(await listQueryRuns(workspaceId).catch(() => queryRuns));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="business-page" aria-labelledby="data-page-title">
      <header className="business-page__header">
        <div>
          <p className="eyebrow">READ-ONLY DATA</p>
          <h1 id="data-page-title">数据库与安全 Text2SQL</h1>
          <p>生成 SQL 必须经过完整 AST allowlist、执行计划预算和独立只读账号。</p>
        </div>
        {connections.length === 0 && canManage ? (
          <button
            className="business-primary"
            disabled={busy}
            onClick={() => void createSample()}
            type="button"
          >
            创建只读样例连接
          </button>
        ) : null}
      </header>

      {error === null ? null : (
        <div className="business-alert" role="alert">
          {error}
        </div>
      )}
      <div className="connection-strip" aria-busy={loading}>
        <label className="business-select">
          数据连接
          <select
            aria-label="数据连接"
            disabled={connections.length === 0}
            onChange={(event) => {
              setConnectionId(event.currentTarget.value);
              setTables([]);
              setSelectedTable(null);
              setRows(null);
              setOffset(0);
            }}
            value={connectionId ?? ""}
          >
            {connections.map((connection) => (
              <option key={connection.id} value={connection.id}>
                {connection.name}
              </option>
            ))}
          </select>
        </label>
        <span className={`status-pill status-pill--${selectedConnection?.status ?? "loading"}`}>
          {selectedConnection?.status ?? "未配置"}
        </span>
        {canManage && connectionId !== null ? (
          <button disabled={busy} onClick={() => void probeConnection()} type="button">
            测试连接
          </button>
        ) : null}
      </div>

      <div className="data-grid">
        <section className="business-card schema-panel">
          <div className="business-card__heading">
            <div>
              <h2>Schema 浏览</h2>
              <p>表、列、主键、索引与确定性分页</p>
            </div>
          </div>
          <label className="business-select">
            表
            <select
              aria-label="数据库表"
              disabled={tables.length === 0}
              onChange={(event) => {
                setSelectedTable(
                  tables.find(
                    (table) =>
                      `${table.schema_name}.${table.table_name}` === event.currentTarget.value,
                  ) ?? null,
                );
                setOffset(0);
              }}
              value={
                selectedTable === null
                  ? ""
                  : `${selectedTable.schema_name}.${selectedTable.table_name}`
              }
            >
              {tables.map((table) => (
                <option
                  key={`${table.schema_name}.${table.table_name}`}
                  value={`${table.schema_name}.${table.table_name}`}
                >
                  {table.schema_name}.{table.table_name}
                </option>
              ))}
            </select>
          </label>
          {selectedTable === null ? (
            <p className="business-empty">尚无可浏览表。</p>
          ) : (
            <>
              <dl className="schema-facts">
                <div>
                  <dt>估算行数</dt>
                  <dd>{selectedTable.estimated_rows}</dd>
                </div>
                <div>
                  <dt>存储字节</dt>
                  <dd>{selectedTable.total_bytes}</dd>
                </div>
                <div>
                  <dt>索引</dt>
                  <dd>{selectedTable.indexes.map((index) => index.name).join("、") || "—"}</dd>
                </div>
              </dl>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      {selectedTable.columns.map((column) => (
                        <th key={column.name}>
                          {column.name}
                          <small>{column.data_type}</small>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rows?.rows.map((row, rowIndex) => (
                      <tr key={`${String(offset)}-${String(rowIndex)}`}>
                        {row.map((cell, cellIndex) => (
                          <td key={selectedTable.columns[cellIndex]?.name ?? String(cellIndex)}>
                            {displayCell(cell)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="pagination">
                <button
                  disabled={offset === 0}
                  onClick={() => {
                    setOffset(Math.max(0, offset - 20));
                  }}
                  type="button"
                >
                  上一页
                </button>
                <span>offset {offset}</span>
                <button
                  disabled={(rows?.rows.length ?? 0) < 20}
                  onClick={() => {
                    setOffset(offset + 20);
                  }}
                  type="button"
                >
                  下一页
                </button>
              </div>
            </>
          )}
        </section>

        <form className="business-card query-form" onSubmit={(event) => void runQuery(event)}>
          <div className="business-card__heading">
            <div>
              <h2>受审计查询</h2>
              <p>SQL 是不可信候选；validated SQL 才能执行。</p>
            </div>
          </div>
          <label>
            业务问题
            <textarea
              aria-label="查询问题"
              maxLength={2000}
              onChange={(event) => {
                setQuestion(event.currentTarget.value);
              }}
              required
              value={question}
            />
          </label>
          <label>
            候选 SQL
            <textarea
              aria-label="候选 SQL"
              className="code-input"
              maxLength={20000}
              onChange={(event) => {
                setSql(event.currentTarget.value);
              }}
              required
              value={sql}
            />
          </label>
          <label>
            Artifact 类型
            <select
              aria-label="图表类型"
              onChange={(event) => {
                setChartType(event.currentTarget.value as ChartType);
              }}
              value={chartType}
            >
              <option value="table">表格</option>
              <option value="bar">柱状图</option>
              <option value="line">折线图</option>
              <option value="pie">饼图</option>
              <option value="scatter">散点图</option>
            </select>
          </label>
          <button
            className="business-primary"
            disabled={busy || !canManage || connectionId === null}
            type="submit"
          >
            {busy ? "正在校验与执行…" : "执行安全查询"}
          </button>
        </form>
      </div>

      {result === null ? null : (
        <section className="business-card query-result-summary" aria-label="QueryRun 结果">
          <div className="business-card__heading">
            <div>
              <h2>QueryRun · {result.status}</h2>
              <p>{result.question}</p>
            </div>
            <span className={`status-pill status-pill--${result.status}`}>
              {result.error_code ?? `${String(result.row_count)} rows`}
            </span>
          </div>
          <dl className="schema-facts">
            <div>
              <dt>validated SQL</dt>
              <dd>{result.validated_sql ?? "未通过 AST 校验，未执行"}</dd>
            </div>
            <div>
              <dt>计划成本</dt>
              <dd>{result.plan_cost ?? "—"}</dd>
            </div>
            <div>
              <dt>扫描行预算事实</dt>
              <dd>{result.plan_rows ?? "—"}</dd>
            </div>
          </dl>
        </section>
      )}

      {result?.table_artifact === null || result?.table_artifact === undefined ? null : (
        <section className="business-card artifact-card" aria-label="查询 Artifact">
          <div className="business-card__heading">
            <div>
              <h2>查询 Artifact</h2>
              <p>
                QueryRun {result.id} · {result.row_count} 行 · plan {result.plan_cost}
              </p>
            </div>
            <span className="hash-chip">
              sha256 {result.table_artifact.content_sha256.slice(0, 12)}…
            </span>
          </div>
          {result.chart_artifact === null ? null : (
            <Suspense fallback={<p className="business-empty">正在加载安全图表渲染器…</p>}>
              <SafeChart
                option={result.chart_artifact.option}
                title={result.chart_artifact.chart_type}
              />
            </Suspense>
          )}
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  {result.table_artifact.columns.map((column) => (
                    <th key={column}>{column}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.table_artifact.rows.map((row, rowIndex) => (
                  <tr key={String(rowIndex)}>
                    {row.map((cell, cellIndex) => (
                      <td key={`${String(rowIndex)}-${String(cellIndex)}`}>{displayCell(cell)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section className="business-card query-history">
        <div className="business-card__heading">
          <div>
            <h2>QueryRun 审计</h2>
            <p>失败尝试也保留稳定错误码，刷新后可恢复。</p>
          </div>
        </div>
        <ol className="run-list">
          {queryRuns.map((run) => (
            <li key={run.id}>
              <span>{new Date(run.created_at).toLocaleString("zh-CN")}</span>
              <strong>{run.status}</strong>
              <small>{run.error_code ?? `${String(run.row_count)} rows`}</small>
            </li>
          ))}
        </ol>
      </section>
    </section>
  );
}
