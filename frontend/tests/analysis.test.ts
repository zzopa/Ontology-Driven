import assert from "node:assert/strict";
import test from "node:test";
import { analysisLabel, comparisonGroups, forecastPlot } from "../lib/analysis.ts";
import type { AnalysisSection, Forecast } from "../lib/types.ts";
import { applyTurnEvent, createChatTurn } from "../lib/chat-thread.ts";

test("comparison charts require multiple available values of the same metric and unit", () => {
  const sections = [
    { metric_key: "flights", unit: "架次", value: 0 },
    { metric_key: "flights", unit: "架次", value: 11 },
    { metric_key: "rate", unit: "%", value: 80 },
    { metric_key: "rate", unit: "%", value: null },
    { metric_key: "rate", unit: "%", value: 88, result: { coverage_assessment: { status: "PARTIAL" } } },
    { metric_key: "flights", unit: "分钟", value: 5 },
  ] as AnalysisSection[];
  assert.deepEqual(comparisonGroups(sections).map(g => g.map(s => s.value)), [[0, 11]]);
});

test("analysis labels preserve the full inclusive display period and object", () => {
  assert.deepEqual(analysisLabel("武汉 · 登机桥保障总次 · 2026-11-02至2026-11-15"),
    { name: "武汉 · 登机桥保障总次", period: "2026-11-02至2026-11-15" });
  assert.deepEqual(analysisLabel("统计项目 · 本地来源"), { name: "统计项目 · 本地来源", period: "" });
});

test("forecast chart contains bounded coordinates for a constant series", () => {
  const value = { history: [{ date: "2026-11-15", value: 5 }], points: [{ date: "2026-11-16", value: 5, lower: 5, upper: 5 }] } as Forecast;
  const before = JSON.stringify(value);
  const plot = forecastPlot(value);
  assert.equal(plot.history, "42,176");
  assert.equal(plot.forecast, "718,176");
  assert.ok(!plot.band.includes("NaN"));
  assert.equal(JSON.stringify(value), before);
});

test("partial stream preserves simulation label without claiming a final result", () => {
  const section = { id: "q1", label: "长沙航班量", value: 500, unit: "架次", is_simulated: true, result: { answer: "测试" } } as AnalysisSection;
  const turn = applyTurnEvent(createChatTurn("t1", "机场对比", "机场对比"), { type: "query_result", query_id: "q1", section });
  assert.match(turn.delta, /长沙航班量：500 架次（模拟测试数据）/);
  assert.equal(turn.result, null);
});
