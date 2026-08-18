import { useEffect, useRef } from "react";

import { BarChart, LineChart, PieChart, ScatterChart } from "echarts/charts";
import {
  DatasetComponent,
  GridComponent,
  TitleComponent,
  TooltipComponent,
} from "echarts/components";
import { init, use as registerEChartsModules } from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { isSafeChartOption } from "./safe-chart-option";

registerEChartsModules([
  BarChart,
  DatasetComponent,
  GridComponent,
  LineChart,
  PieChart,
  ScatterChart,
  SVGRenderer,
  TitleComponent,
  TooltipComponent,
]);

interface SafeChartProps {
  readonly option: Record<string, unknown>;
  readonly title: string;
}

export function SafeChart({ option, title }: SafeChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (container === null || !isSafeChartOption(option)) return;
    const chart = init(container, undefined, { renderer: "svg" });
    chart.setOption(option, { notMerge: true });
    const observer = new ResizeObserver(() => {
      chart.resize();
    });
    observer.observe(container);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [option]);

  if (!isSafeChartOption(option)) {
    return <p className="business-alert">图表配置未通过客户端 allowlist，已拒绝渲染。</p>;
  }
  return <div aria-label={title} className="safe-chart" ref={containerRef} role="img" />;
}
