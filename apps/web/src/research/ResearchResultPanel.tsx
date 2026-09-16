import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { publicError } from "../chat/chat-workbench-model";
import { getResearchResultView, type ResearchResultView } from "./research-api";

const SafeChart = lazy(async () => {
  const module = await import("../data-explorer/SafeChart");
  return { default: module.SafeChart };
});
type Row = ResearchResultView["rows"][number];
type Value = Row["values"][number];
const metrics = ["roe_percent", "net_profit_margin_percent", "asset_turnover", "equity_multiplier"];
const verificationNames: Record<string, string> = {
  verified: "已核验",
  partial: "部分核验",
  conflict: "证据冲突",
  insufficient_evidence: "证据不足",
};

function SourceValue({
  value,
  unit,
  label,
  onOpenEvidence,
}: {
  readonly value: Value | undefined;
  readonly unit: string;
  readonly label: string;
  readonly onOpenEvidence: (id: string) => void;
}) {
  if (value === undefined) return <span>缺失</span>;
  return (
    <span className="result-value">
      <span>
        {value.value} <small>{unit}</small>
      </span>
      {value.evidence_refs.map((id, index) => (
        <button
          type="button"
          key={id}
          aria-label={`${label} 来源 ${String(index + 1)}`}
          onClick={() => {
            onOpenEvidence(id);
          }}
        >
          来源
        </button>
      ))}
    </span>
  );
}

function MetricBar({
  row,
  periods,
  onOpenEvidence,
}: {
  readonly row: Row;
  readonly periods: readonly string[];
  readonly onOpenEvidence: (id: string) => void;
}) {
  // Decimal strings remain the source of truth; Number is used only for geometry.
  const option = useMemo(
    () => ({
      dataset: {
        source: [
          ["财年", row.label],
          ...periods.map((period, index) => [period, Number(row.values[index]?.value)]),
        ],
      },
      series: [
        { type: "bar", name: `${row.label} (${row.unit})`, encode: { x: "财年", y: row.label } },
      ],
      xAxis: { type: "category" },
      yAxis: { type: "value" },
    }),
    [periods, row],
  );
  return (
    <figure className="result-bar">
      <figcaption>
        {row.label}（{row.unit}）
      </figcaption>
      <Suspense fallback={<p>正在加载柱状图；精确值见下表。</p>}>
        <SafeChart
          option={option}
          title={`${row.label}跨年度柱状图`}
          onDataClick={(index) => {
            const id = row.values[index]?.evidence_refs[0];
            if (id !== undefined) onOpenEvidence(id);
          }}
        />
      </Suspense>
    </figure>
  );
}

export function ResearchResults({
  result,
  onOpenEvidence,
}: {
  readonly result: ResearchResultView;
  readonly onOpenEvidence: (id: string) => void;
}) {
  const [selectedPeriod, setSelectedPeriod] = useState<string | null>(null);
  if (result.status === "not_applicable") return null;
  if (result.status !== "ready")
    return (
      <section className="research-card" aria-label="研究结果展示">
        <h3>研究结果暂不可展示</h3>
        {result.limitations.map((item) => (
          <p key={item}>{item}</p>
        ))}
      </section>
    );
  const period =
    selectedPeriod !== null && result.periods.includes(selectedPeriod)
      ? selectedPeriod
      : (result.periods.at(-1) ?? "");
  const index = result.periods.indexOf(period);
  const rowByKey = new Map(result.rows.map((row) => [row.key, row]));
  const metricRows = metrics.flatMap((key) => {
    const row = rowByKey.get(key);
    return row === undefined ? [] : [row];
  });
  function node(key: string) {
    const row = rowByKey.get(key);
    if (row === undefined) return null;
    return (
      <div className="dupont-node" key={key}>
        <span>{row.label}</span>
        <SourceValue
          value={row.values[index]}
          unit={row.unit}
          label={`${period} ${row.label}`}
          onOpenEvidence={onOpenEvidence}
        />
      </div>
    );
  }
  return (
    <section className="research-card research-results" aria-label="研究结果展示">
      <div className="research-section-heading">
        <h3>杜邦分析结果</h3>
        <span>{verificationNames[result.verification_status ?? ""] ?? "尚未核验当前版本"}</span>
      </div>
      <p>
        报告版本 {result.draft_revision} · {result.periods.length} 个财年；所有数值均可追溯来源。
      </p>
      <label>
        展示财年{" "}
        <select
          value={period}
          onChange={(event) => {
            setSelectedPeriod(event.currentTarget.value);
          }}
        >
          {result.periods.map((item) => (
            <option key={item} value={item}>
              {item}
            </option>
          ))}
        </select>
      </label>
      <div className="result-metrics">
        {metricRows.map((row) => {
          const value = row.values[index];
          return (
            <article className="result-metric" key={row.key}>
              <h4>{row.label}</h4>
              <SourceValue
                value={value}
                unit={row.unit}
                label={`${period} ${row.label}`}
                onOpenEvidence={onOpenEvidence}
              />
              <p>
                {value?.change == null
                  ? "无上年可比值"
                  : `较上年变化 ${value.change} ${row.change_unit}`}
              </p>
            </article>
          );
        })}
      </div>
      <h4>跨年度对比</h4>
      <p>各指标使用独立坐标轴，柱高从零起算；点击柱形可查看计算证据，精确数值见数据表。</p>
      <div className="result-bars">
        {metricRows.map((row) => (
          <MetricBar
            key={row.key}
            row={row}
            periods={result.periods}
            onOpenEvidence={onOpenEvidence}
          />
        ))}
      </div>
      <h4>杜邦分解 · {period}</h4>
      <div className="dupont-tree" aria-label="杜邦分解图">
        {node("roe_percent")}
        <p className="dupont-equation">ROE = 净利率 × 总资产周转率 × 权益乘数</p>
        <div className="dupont-factors">
          <div>
            {node("net_profit_margin_percent")}
            <p>净利润 ÷ 营业收入</p>
            {node("net_income")}
            {node("revenue")}
          </div>
          <div>
            {node("asset_turnover")}
            <p>营业收入 ÷ 平均总资产</p>
            {node("revenue")}
            {node("average_assets")}
          </div>
          <div>
            {node("equity_multiplier")}
            <p>平均总资产 ÷ 平均归母权益</p>
            {node("average_assets")}
            {node("average_equity")}
          </div>
        </div>
        <p>
          平均余额 =（期初余额 + 期末余额）÷ 2；独立四舍五入后，展示因子乘积可能与 ROE 有末位差异。
        </p>
      </div>
      <h4>各年输入、平均余额与计算结果</h4>
      <div
        className="result-table-scroll"
        // Keyboard users need to focus this region to scroll the wide table.
        // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
        tabIndex={0}
        role="region"
        aria-label="杜邦数据表（可横向滚动）"
      >
        <table>
          <caption>金额、单位和来源；财年按期末日期排列</caption>
          <thead>
            <tr>
              <th scope="col">指标</th>
              <th scope="col">单位</th>
              {result.periods.map((item) => (
                <th scope="col" key={item}>
                  {item}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {result.rows.map((row) => (
              <tr key={row.key}>
                <th scope="row">{row.label}</th>
                <td>{row.unit}</td>
                {result.periods.map((item, position) => (
                  <td key={item}>
                    <SourceValue
                      value={row.values[position]}
                      unit=""
                      label={`${item} ${row.label}`}
                      onOpenEvidence={onOpenEvidence}
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {result.limitations.map((item) => (
        <p className="research-uncertainty" key={item}>
          {item}
        </p>
      ))}
    </section>
  );
}

export function ResearchResultPanel({
  workspaceId,
  researchRunId,
  draftId,
  draftRevision,
  refreshRevision = 0,
  onOpenEvidence,
}: {
  readonly workspaceId: string;
  readonly researchRunId: string;
  readonly draftId: string;
  readonly draftRevision: number;
  readonly refreshRevision?: number;
  readonly onOpenEvidence: (id: string) => void;
}) {
  const [state, setState] = useState<{
    key: string;
    result?: ResearchResultView;
    error?: string;
  } | null>(null);
  const [retry, setRetry] = useState(0);
  const key = `${workspaceId}/${researchRunId}/${draftId}/${String(draftRevision)}/${String(refreshRevision)}`;
  useEffect(() => {
    let active = true;
    void getResearchResultView(workspaceId, researchRunId)
      .then((result) => {
        if (!active) return;
        if (
          result.research_run_id !== researchRunId ||
          result.draft_id !== draftId ||
          result.draft_revision !== draftRevision
        ) {
          setState({ key, error: "报告版本已变化，请刷新研究详情后查看结果。" });
        } else setState({ key, result });
      })
      .catch((error: unknown) => {
        if (active) setState({ key, error: publicError(error) });
      });
    return () => {
      active = false;
    };
  }, [workspaceId, researchRunId, draftId, draftRevision, key, retry]);
  if (state?.key !== key) return <p role="status">正在读取报告结果…</p>;
  if (state.error !== undefined)
    return (
      <div role="alert">
        <p>{state.error}</p>
        <button
          type="button"
          onClick={() => {
            setRetry((value) => value + 1);
          }}
        >
          重试读取结果
        </button>
      </div>
    );
  return state.result === undefined ? null : (
    <ResearchResults key={key} result={state.result} onOpenEvidence={onOpenEvidence} />
  );
}
