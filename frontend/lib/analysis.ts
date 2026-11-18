import type { AnalysisSection, Forecast } from "./types";

export function comparisonGroups(sections: AnalysisSection[]) {
  const groups = new Map<string, AnalysisSection[]>();
  for (const section of sections) {
    if (section.value === null || !Number.isFinite(section.value) || section.result?.coverage_assessment?.status === "PARTIAL") continue;
    const key = section.metric_key + ":" + section.unit;
    groups.set(key, [...(groups.get(key) || []), section]);
  }
  return [...groups.values()].filter(group => group.length > 1);
}

export function analysisLabel(label: string) {
  const match = /^(.*) · (\d{4}-\d{2}-\d{2}(?:至\d{4}-\d{2}-\d{2})?)$/.exec(label);
  return { name: match?.[1] ?? label, period: match?.[2] ?? "" };
}

export function forecastPlot(forecast: Forecast) {
  const sortedHistory = [...forecast.history].sort((a, b) => a.date.localeCompare(b.date));
  const sortedPoints = [...forecast.points].sort((a, b) => a.date.localeCompare(b.date));
  const history = sortedHistory.slice(-14);
  const values = [...history.map(p => p.value), ...sortedPoints.flatMap(p => [p.lower, p.upper])];
  const low = Math.min(...values), high = Math.max(...values), span = high - low || 1;
  const count = history.length + sortedPoints.length;
  const x = (index: number) => 42 + index * 676 / Math.max(count - 1, 1);
  const y = (value: number) => 176 - (value - low) / span * 138;
  return {
    low, high,
    history: history.map((p, i) => `${x(i)},${y(p.value)}`).join(" "),
    forecast: sortedPoints.map((p, i) => `${x(i + history.length)},${y(p.value)}`).join(" "),
    band: [...sortedPoints.map((p, i) => `${x(i + history.length)},${y(p.upper)}`), ...sortedPoints.map((p, i) => `${x(i + history.length)},${y(p.lower)}`).reverse()].join(" "),
    firstDate: history[0]?.date || sortedPoints[0]?.date,
    lastDate: sortedPoints.at(-1)?.date,
  };
}
