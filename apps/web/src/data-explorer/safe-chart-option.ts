const allowedTopLevel = new Set(["dataset", "series", "title", "tooltip", "xAxis", "yAxis"]);
const allowedSeries = new Set(["bar", "line", "pie", "scatter"]);
const allowedSeriesKeys = new Set(["data", "encode", "name", "type"]);
const axisKeys = new Set(["type"]);
const dataItemKeys = new Set(["name", "value"]);
const datasetKeys = new Set(["source"]);
const encodeKeys = new Set(["x", "y"]);
const titleKeys = new Set(["text"]);
const tooltipKeys = new Set(["trigger"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasOnlyKeys(value: Record<string, unknown>, allowed: ReadonlySet<string>): boolean {
  return Object.keys(value).every((key) => allowed.has(key));
}

function isAxis(value: unknown): boolean {
  return (
    value === undefined ||
    (isRecord(value) &&
      hasOnlyKeys(value, axisKeys) &&
      (value.type === "category" || value.type === "value"))
  );
}

function isChartScalar(value: unknown): boolean {
  return (
    value === null ||
    typeof value === "string" ||
    (typeof value === "number" && Number.isFinite(value))
  );
}

function isSeriesData(value: unknown): boolean {
  return (
    Array.isArray(value) &&
    value.length <= 200 &&
    value.every(
      (item) =>
        (Array.isArray(item) && item.length <= 16 && item.every(isChartScalar)) ||
        (isRecord(item) &&
          hasOnlyKeys(item, dataItemKeys) &&
          typeof item.name === "string" &&
          isChartScalar(item.value)),
    )
  );
}

function isDataset(value: unknown): boolean {
  return (
    isRecord(value) &&
    hasOnlyKeys(value, datasetKeys) &&
    Array.isArray(value.source) &&
    value.source.length <= 201 &&
    value.source.every((row) => Array.isArray(row) && row.length <= 64 && row.every(isChartScalar))
  );
}

export function isSafeChartOption(option: Record<string, unknown>): boolean {
  let encoded: string;
  try {
    encoded = JSON.stringify(option);
  } catch {
    return false;
  }
  if (encoded.length > 524_288 || Object.keys(option).some((key) => !allowedTopLevel.has(key))) {
    return false;
  }
  const series = option.series;
  if (
    !Array.isArray(series) ||
    series.length === 0 ||
    series.length > 32 ||
    !series.every(
      (entry) =>
        isRecord(entry) &&
        hasOnlyKeys(entry, allowedSeriesKeys) &&
        allowedSeries.has(String(entry.type)) &&
        (entry.name === undefined ||
          (typeof entry.name === "string" && entry.name.length <= 120)) &&
        (entry.data === undefined || isSeriesData(entry.data)) &&
        (entry.encode === undefined ||
          (isRecord(entry.encode) &&
            hasOnlyKeys(entry.encode, encodeKeys) &&
            typeof entry.encode.x === "string" &&
            typeof entry.encode.y === "string")),
    )
  ) {
    return false;
  }
  const dataset = option.dataset;
  const title = option.title;
  const tooltip = option.tooltip;
  return (
    (dataset === undefined || isDataset(dataset)) &&
    (title === undefined ||
      (isRecord(title) &&
        hasOnlyKeys(title, titleKeys) &&
        typeof title.text === "string" &&
        title.text.length <= 120)) &&
    (tooltip === undefined ||
      (isRecord(tooltip) &&
        hasOnlyKeys(tooltip, tooltipKeys) &&
        (tooltip.trigger === "axis" || tooltip.trigger === "item"))) &&
    isAxis(option.xAxis) &&
    isAxis(option.yAxis)
  );
}
