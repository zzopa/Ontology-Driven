import assert from "node:assert/strict";
import test from "node:test";
import { clockMinutes, completeFlightNodes, completeTurnaroundOperations, hasOperationEnd, sortTurnaroundOperations, timelinePoint, timelineWindow, timelineSpan, turnaroundChartRows, turnaroundFishboneLayout, turnaroundFlightNodes, turnaroundHeading, turnaroundOperationTimeline, turnaroundRecordSummary } from "../lib/turnaround-layout.ts";
import type { FishboneNode, TurnaroundOperation, VisualTurnaround } from "../lib/types.ts";

test("flight inquiry titles keep the requested flight primary and label the aircraft separately", () => {
  assert.deepEqual(turnaroundHeading({ query_identifier: "MU2613", flight_pair: "MU2613", craft_no: "B20AE" }), {
    title: "MU2613 · 过站保障", detail: "执飞机号 B20AE",
  });
  assert.deepEqual(turnaroundHeading({ query_identifier: "B-20AE", flight_pair: "MU2613", craft_no: "B20AE" }), {
    title: "B-20AE · 过站保障", detail: "对应航班 MU2613",
  });
  assert.equal(turnaroundHeading({ flight_pair: "MU2613", craft_no: "B20AE" }).title, "MU2613 · 过站保障");
  assert.equal(turnaroundHeading({ query_identifier: "MU2613", flight_pair: "MU2613 → MU2614", craft_no: "B20AE" }).title, "MU2613 · 过站保障");
  assert.equal(turnaroundHeading({}).title, "过站保障");
  assert.deepEqual(turnaroundHeading({ query_identifier: "CZ8318", flight_pair: "CZ8318", craft_no: "B1921", in_flight: "CZ8318", out_flight: "CZ3357" }), {
    title: "CZ8318 · 过站保障", detail: "执飞机号 B1921 · 进港 CZ8318 → 出港 CZ3357",
  });
});

test("ground chart combines actual inbound points with work intervals without inventing durations", () => {
  const ground = (key: string, time: string): FishboneNode => ({ key, name: key, code: key,
    category: "ramp", position: "top", status: time ? "completed" : "pending", status_label: "", actual_time: time, time_display: time });
  const data: VisualTurnaround = { kind: "turnaround_gantt", flight_pair: "CZ8318", in_flight: "CZ8318", out_flight: "CZ3357",
    craft_no: "B1921", stand: "", gate: "", flight_date: "2026-11-10", pipeline: [],
    milestones: { aibt: "07:07", sobt: null, tobt: null, tsat: null, current_time: null },
    timeline_range: "07:00 - 08:42", time_ticks: [],
    ground_nodes: [ground("inbound_landing", "07:00"), ground("aibt", "07:07"), ground("cargo_open", ""), ground("atot", "08:32")],
    operations: [{ id: "fueling", name: "加油", icon: "", status: "completed", actual_start: "07:15", actual_end: "07:22" }],
    conclusion: { summary: "", meta: { has_delay: false, evidence: "", actual: "", plan_end: "", delay_desc: "" } },
  };
  const original = structuredClone(data);
  const rows = turnaroundChartRows(data);
  assert.equal(rows.length, 9);
  const timeline = turnaroundOperationTimeline({ ...data, operations: rows });
  assert.equal(timeline.timeline_range, "07:00 - 08:42");
  const window = timelineWindow(timeline.timeline_range)!;
  const landing = rows.find(row => row.id === "node:inbound_landing")!;
  assert.equal(landing.node?.actual_time, "07:00");
  assert.equal(timelinePoint(landing.actual_end, window), 0);
  assert.equal(timelineSpan(landing.actual_start, landing.actual_end, window), null);
  assert.equal(rows.find(row => row.id === "node:cargo_open")?.actual_end, "");
  assert.equal(turnaroundRecordSummary(data.operations).ended, 1);
  assert.deepEqual(data, original);
});

test("complete flight flow retains the original eleven nodes and every additional returned node", () => {
  const baseline = completeFlightNodes([]);
  assert.equal(baseline.length, 11);
  assert.deepEqual(baseline.map(node => node.name), ["前站起飞", "落地", "挡轮挡", "客舱开门", "开始登机", "登机完成", "客舱关门", "货舱关门", "撤轮挡", "推出", "起飞"]);
  assert.ok(baseline.every(node => node.status === "pending" && !node.actual_time));
  const actual: FishboneNode = { ...baseline[6], actual_time: "14:46", actual_at: "2026-10-10T14:46:00+08:00", status: "completed", status_label: "有实际记录" };
  const extra: FishboneNode = { ...baseline[0], key: "bridge_off", name: "撤桥", actual_time: "14:52" };
  const input = [actual, extra];
  const original = structuredClone(input);
  const full = completeFlightNodes(input);
  assert.equal(full.length, 12);
  assert.equal(full.find(node => node.key === actual.key), actual);
  assert.equal(full.find(node => node.key === extra.key), extra);
  assert.deepEqual(input, original);
  assert.equal(completeFlightNodes([], "ARR")[1].key, "aldt");

  const disordered: FishboneNode[] = [
    { ...baseline[10], key: "atot", name: "跑道起飞", code: "ATOT", actual_time: "08:32", actual_at: "2026-11-10T08:32:00+08:00" },
    { ...baseline[2], key: "bridge_on", name: "靠接廊桥", code: "BRG_ON", actual_time: "07:22", actual_at: "2026-11-10T07:22:14+08:00" },
    { ...baseline[0], key: "checkin_start", name: "旅客值机", code: "CHK_S", actual_time: "06:26", actual_at: "2026-11-10T06:26:10+08:00" },
  ];
  const sorted = completeFlightNodes(disordered, "DEP");
  const timedKeys = sorted.filter(n => n.actual_at).map(n => n.key);
  assert.deepEqual(timedKeys, ["checkin_start", "bridge_on", "atot"]);
});

test("old snapshots retain known boarding, target, and door records when showing the complete flow", () => {
  const data = { milestones: { aibt: "00:00", sobt: null, tobt: "15:05", tsat: null, current_time: null }, pipeline: [],
    operations: [
      { id: "boarding", name: "登机", icon: "", status: "completed" as const, actual_start: "14:24", actual_end: "14:42" },
      { id: "bridge_push", name: "廊桥与推出", icon: "", status: "planned" as const, milestones: [{ name: "舱门关闭", actual: "14:46", plan: "15:00" }, { name: "撤桥", actual: "已闭环" }] },
    ] };
  const original = structuredClone(data);
  const nodes = turnaroundFlightNodes(data);
  assert.equal(nodes.find(node => node.key === "boarding_start")?.actual_time, "14:24");
  assert.equal(nodes.find(node => node.key === "boarding_end")?.actual_time, "14:42");
  assert.equal(nodes.find(node => node.key === "door_close")?.actual_time, "14:46");
  assert.equal(nodes.find(node => node.key === "aibt")?.actual_time, "00:00");
  assert.equal(nodes.find(node => node.key === "tobt")?.actual_time, undefined);
  assert.equal(nodes.find(node => node.key === "tobt")?.plan_time, "15:05");
  assert.equal(nodes.find(node => node.key === "bridge_off"), undefined);
  assert.deepEqual(data, original);
  assert.equal(turnaroundFlightNodes({ ...data, flight_nodes: nodes }), nodes);
});

test("sparse and old snapshots keep all five operation rows without inventing times", () => {
  const actual: TurnaroundOperation = { id: "boarding", name: "旅客登机", icon: "", status: "completed", actual_start: "14:29", actual_end: "14:54" };
  const rows = completeTurnaroundOperations([actual]);
  assert.equal(rows.length, 5);
  assert.equal(rows[0], actual);
  assert.ok(rows.slice(1).every(row => !row.actual_start && !row.actual_end && row.status === "planned"));
  assert.match(turnaroundRecordSummary(rows).detail, /未提供作业时刻/);
  assert.equal(completeTurnaroundOperations(rows).length, 5);
  assert.equal(completeTurnaroundOperations([...rows, { ...actual, id: "bridge_push" }]).length, 6);
});

test("operation windows ignore late arrival clocks and include every recorded task", () => {
  const data = { operations: [{ id: "boarding", name: "登机", icon: "", status: "completed" as const, actual_start: "14:29", actual_end: "14:54" }],
    milestones: { aibt: "19:56", sobt: null, tobt: null, tsat: null, current_time: null }, timeline_range: "14:20 - 20:06", time_ticks: [] };
  assert.deepEqual(turnaroundOperationTimeline(data), { timeline_range: "14:20 - 15:04", time_ticks: ["14:20", "14:30", "14:40", "14:50", "15:00"] });
  const mixed = { ...data, milestones: { ...data.milestones, tobt: "15:05" }, operations: [...data.operations, { ...data.operations[0], id: "cleaning", actual_start: "13:55", actual_end: "14:00" }] };
  assert.equal(turnaroundOperationTimeline(mixed).timeline_range, "13:50 - 15:15");
  assert.equal(turnaroundOperationTimeline({ ...data, timeline_range: "23:50 - 00:30" }).timeline_range, "23:50 - 00:30");
});

test("missing or invalid time records never acquire a demonstration time", () => {
  for (const value of [null, undefined, "", "24:00", "12:60", "12:30:99", "unknown"]) assert.equal(clockMinutes(value), null);
  assert.equal(clockMinutes("00:00"), 0);
  assert.equal(clockMinutes("14:20:00"), 860);
  assert.equal(timelineWindow(""), null);
  assert.equal(timelineWindow("23:40 - 00:30"), null);
});

test("bars are clipped to the actual observation window without inventing a duration", () => {
  const window = timelineWindow("14:00 - 15:00")!;
  assert.deepEqual(timelineSpan("13:45", "14:30", window), { left: 0, width: 50 });
  assert.deepEqual(timelineSpan("14:30", "15:15", window), { left: 50, width: 50 });
  assert.equal(timelineSpan("13:00", "13:45", window), null);
  assert.equal(timelineSpan("14:30", null, window), null);
  assert.equal(timelineSpan("14:30", "14:00", window), null);
});

test("record summary distinguishes end-only evidence from a missing completion", () => {
  const operations: TurnaroundOperation[] = [
    { id: "loading", name: "行李装卸", icon: "", status: "completed", actual_end: "14:50" },
    { id: "catering", name: "配餐", icon: "", status: "in_progress", actual_start: "13:55" },
    { id: "cleaning", name: "保洁", icon: "", status: "completed", actual_start: "13:55", actual_end: "14:00" },
  ];
  assert.deepEqual(turnaroundRecordSummary(operations), {
    total: 3, ended: 2, incomplete: 1, title: "2 项作业有结束记录",
    detail: "配餐有开始记录，结束时刻未提供；行李装卸仅有结束时刻。",
  });
  assert.equal(hasOperationEnd({ ...operations[0], actual_end: null }), false);
  assert.equal(hasOperationEnd({ ...operations[0], actual_end: null, milestones: [{ name: "完成", actual: "14:50" }] }), true);
  assert.equal(turnaroundRecordSummary([]).total, 0);
  assert.equal(turnaroundRecordSummary([]).title, "暂缺保障作业时刻");
});

test("single time records have a position but never a fabricated interval", () => {
  const window = timelineWindow("14:00 - 15:00")!;
  assert.equal(timelinePoint("14:30", window), 50);
  assert.equal(timelinePoint("15:00", window), 100);
  for (const value of [null, "", "13:59", "15:01", "24:30"]) assert.equal(timelinePoint(value, window), null);
  assert.equal(timelineSpan(null, "14:30", window), null);
});

test("MU2613 operations follow their recorded times instead of task or completion order", () => {
  const operations: TurnaroundOperation[] = [
    { id: "boarding", name: "旅客登机", icon: "", status: "completed", actual_start: "14:24", actual_end: "14:42" },
    { id: "loading", name: "行李装卸", icon: "", status: "completed", actual_end: "14:50" },
    { id: "cleaning", name: "保洁", icon: "", status: "completed", actual_start: "13:55", actual_end: "14:00" },
    { id: "fueling", name: "加油", icon: "", status: "completed", actual_start: "14:05", actual_end: "14:20" },
    { id: "catering", name: "配餐", icon: "", status: "in_progress", actual_start: "13:55" },
  ];
  const original = structuredClone(operations);
  const sorted = sortTurnaroundOperations(operations, "13:50 - 15:10");
  assert.deepEqual(sorted.map(operation => operation.id), ["cleaning", "catering", "fueling", "boarding", "loading"]);
  assert.deepEqual(sorted.filter(operation => !hasOperationEnd(operation)).map(operation => operation.id), ["catering"]);
  assert.deepEqual(operations, original);
});

test("sorting uses actual times before plans, validates clocks, and leaves untimed tasks last", () => {
  const base: TurnaroundOperation = { id: "", name: "", icon: "", status: "planned" };
  const operations: TurnaroundOperation[] = [
    { ...base, id: "missing" },
    { ...base, id: "invalid", actual_start: "24:00", plan_start: "unknown" },
    { ...base, id: "actual-start", actual_start: "14:20", actual_end: "14:40", plan_start: "08:00" },
    { ...base, id: "plan-end", plan_end: "14:00" },
    { ...base, id: "plan-start", plan_start: "9:05", plan_end: "10:00" },
    { ...base, id: "actual-end", actual_start: "invalid", actual_end: "14:10", plan_start: "07:00" },
    { ...base, id: "milestone", milestones: [{ name: "撤桥", plan: "06:00", actual: "14:30" }] },
    { ...base, id: "planned-milestone", milestones: [{ name: "撤桥", plan: "14:15" }] },
    { ...base, id: "midnight", actual_start: "00:00" },
  ];
  assert.deepEqual(sortTurnaroundOperations(operations, "00:00 - 15:00").map(operation => operation.id),
    ["midnight", "plan-start", "plan-end", "actual-end", "planned-milestone", "actual-start", "milestone", "missing", "invalid"]);
  assert.deepEqual(sortTurnaroundOperations([], "00:00 - 15:00"), []);
});

test("ambiguous clock-only windows retain source order instead of guessing dates", () => {
  const operations: TurnaroundOperation[] = [
    { id: "late", name: "", icon: "", status: "completed", actual_start: "23:50" },
    { id: "early", name: "", icon: "", status: "completed", actual_start: "00:10" },
  ];
  for (const range of ["23:40 - 00:30", "", "unknown"]) {
    assert.deepEqual(sortTurnaroundOperations(operations, range), operations);
  }
});

test("fishbone keeps parallel operations on one time anchor and uses end-only evidence honestly", () => {
  const base: TurnaroundOperation = { id: "", name: "", icon: "", status: "completed" };
  const operations = [
    { ...base, id: "boarding", actual_start: "14:24", actual_end: "14:42" },
    { ...base, id: "loading", actual_end: "14:50" },
    { ...base, id: "cleaning", actual_start: "13:55", actual_end: "14:00" },
    { ...base, id: "fueling", actual_start: "14:05", actual_end: "14:20" },
    { ...base, id: "catering", actual_start: "13:55", status: "in_progress" as const },
  ];
  const original = structuredClone(operations);
  const { positioned, unplaced } = turnaroundFishboneLayout(operations, "13:50 - 15:10");
  assert.deepEqual(positioned.map(node => node.operation.id), ["cleaning", "catering", "fueling", "boarding", "loading"]);
  assert.equal(positioned[0].point, positioned[1].point);
  assert.notEqual(positioned[0].side, positioned[1].side);
  assert.equal(positioned[4].point, 3 + 75 * .94);
  assert.equal(positioned[4].operation.actual_start, undefined);
  assert.equal(positioned[1].operation.actual_end, undefined);
  assert.deepEqual(unplaced, []);
  assert.deepEqual(operations, original);
});

test("crowded fishbone cards never overlap or move their true clock anchors", () => {
  const operations: TurnaroundOperation[] = Array.from({ length: 14 }, (_, index) => ({
    id: String(index), name: "作业", icon: "", status: "planned", plan_start: "14:59",
  }));
  const layout = turnaroundFishboneLayout(operations, "14:00 - 15:00");
  assert.equal(layout.positioned.length, operations.length);
  for (const node of layout.positioned) {
    assert.equal(node.point, 3 + 59 / 60 * 94);
    assert.ok(node.left >= 0 && node.left + 20 <= 100);
    assert.ok(node.top >= 0 && node.top + layout.cardHeight <= layout.height);
    for (const other of layout.positioned) {
      if (node === other) continue;
      assert.ok(node.left + 20 <= other.left || other.left + 20 <= node.left ||
        node.top + layout.cardHeight <= other.top || other.top + layout.cardHeight <= node.top);
    }
  }
});

test("fishbone preserves unpositionable records without inventing time anchors", () => {
  const base: TurnaroundOperation = { id: "", name: "", icon: "", status: "planned" };
  const operations = [
    { ...base, id: "unknown" },
    { ...base, id: "invalid", actual_start: "24:00" },
    { ...base, id: "outside", actual_start: "13:00" },
    { ...base, id: "plan-only", plan_start: "14:10" },
  ];
  const layout = turnaroundFishboneLayout(operations, "14:00 - 15:00");
  assert.deepEqual(layout.positioned.map(node => node.operation.id), ["plan-only"]);
  assert.deepEqual(layout.unplaced.map(operation => operation.id), ["outside", "unknown", "invalid"]);
  assert.equal(turnaroundFishboneLayout(operations, "23:50 - 00:20").positioned.length, 0);
  assert.equal(turnaroundFishboneLayout(operations, "").unplaced.length, operations.length);
  assert.equal(turnaroundFishboneLayout([], "14:00 - 15:00").positioned.length, 0);
});
