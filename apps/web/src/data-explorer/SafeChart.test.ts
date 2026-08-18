import { describe, expect, it } from "vitest";

import { isSafeChartOption } from "./safe-chart-option";

describe("SafeChart option boundary", () => {
  it("accepts the exact server-built bar contract", () => {
    expect(
      isSafeChartOption({
        dataset: {
          source: [
            ["industry", "revenue"],
            ["transport", 12],
          ],
        },
        series: [{ encode: { x: "industry", y: "revenue" }, type: "bar" }],
        title: { text: "Revenue" },
        tooltip: { trigger: "axis" },
        xAxis: { type: "category" },
        yAxis: { type: "value" },
      }),
    ).toBe(true);
  });

  it.each([
    { series: [{ formatter: "javascript:alert(1)", type: "bar" }] },
    {
      series: [
        { data: [{ name: "x", symbol: "image://https://evil.test/a", value: 1 }], type: "pie" },
      ],
    },
    { graphic: [{ type: "image" }], series: [{ type: "bar" }] },
    { series: [{ type: "custom" }] },
  ])("rejects executable, external, or unsupported option shapes", (option) => {
    expect(isSafeChartOption(option)).toBe(false);
  });
});
