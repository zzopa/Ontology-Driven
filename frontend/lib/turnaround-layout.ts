import type { FishboneNode, TurnaroundOperation, VisualTurnaround } from "./types.ts";

export function completeTurnaroundOperations(operations: TurnaroundOperation[]): TurnaroundOperation[] {
  const missing = [["boarding", "旅客登机"], ["loading", "行李装卸"], ["cleaning", "保洁"], ["fueling", "加油"], ["catering", "配餐"]]
    .filter(([id]) => !operations.some(operation => operation.id === id))
    .map(([id, name]): TurnaroundOperation => ({ id, name, icon: "", status: "planned" }));
  return [...operations, ...missing];
}

export function turnaroundChartRows(data: VisualTurnaround): (TurnaroundOperation & { node?: FishboneNode })[] {
  return [...completeTurnaroundOperations(data.operations), ...(data.ground_nodes || []).map(node => ({
    id: `node:${node.key}`, name: node.name, icon: "", node,
    status: node.actual_time ? "completed" as const : "planned" as const,
    actual_end: node.actual_time, plan_end: node.estimated_time || node.plan_time,
  }))];
}

export function turnaroundOperationTimeline(data: Pick<VisualTurnaround, "operations" | "milestones" | "timeline_range" | "time_ticks">) {
  if (data.timeline_range && !timelineWindow(data.timeline_range)) return { timeline_range: data.timeline_range, time_ticks: data.time_ticks };
  const times = [...data.operations.flatMap(operation => [operation.actual_start, operation.actual_end, operation.plan_start, operation.plan_end]),
    data.milestones.sobt, data.milestones.tobt, data.milestones.tsat].map(clockMinutes).filter((value): value is number => value !== null);
  if (!times.length) return { timeline_range: "", time_ticks: [] };
  const first = Math.floor(Math.min(...times) / 10) * 10, last = Math.min(1439, Math.max(...times) + 10);
  const clock = (minute: number) => `${String(Math.floor(minute / 60)).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`;
  const ticks = [];
  for (let tick = first; tick <= last; tick += 10) ticks.push(clock(tick));
  return { timeline_range: `${clock(first)} - ${clock(last)}`, time_ticks: ticks };
}

function nodeTimeComparable(node: FishboneNode): { isoMs: number | null; clockMin: number | null } {
  const iso = node.actual_at || node.estimated_at || node.plan_at;
  const parsed = iso ? Date.parse(iso) : NaN;
  const clock = node.actual_time || node.estimated_time || node.plan_time;
  return {
    isoMs: Number.isNaN(parsed) ? null : parsed,
    clockMin: clockMinutes(clock),
  };
}

export function completeFlightNodes(nodes: FishboneNode[], direction?: string): FishboneNode[] {
  const baseline: [string, string][] = [
    ["origin_dep", "前站起飞"], [direction === "ARR" ? "aldt" : "inbound_landing", "落地"],
    ["aibt", "挡轮挡"], ["door_open", "客舱开门"], ["boarding_start", "开始登机"],
    ["boarding_end", "登机完成"], ["door_close", "客舱关门"], ["cargo_close", "货舱关门"],
    ["aobt", "撤轮挡"], ["pushback", "推出"], ["atot", "起飞"],
  ];
  const makePlaceholder = (key: string, name: string, index: number): FishboneNode => ({
    key, name, code: "", category: "control", position: index % 2 ? "bottom" : "top",
    status: "pending", status_label: "未记录", time_display: "",
  });
  if (!nodes.length) {
    return baseline.map(([key, name], index) => makePlaceholder(key, name, index));
  }

  const byKey = new Map(nodes.map(node => [node.key, node]));
  const flowOrder = direction === "ARR"
    ? ["origin_dep", "eldt", "aldt", "runway_exit", "aibt", "bridge_on", "door_open", "cargo_open",
      "checkin_start", "checkin_end", "ground_service", "boarding_start", "urge_boarding", "boarding_end",
      "door_close", "cargo_close", "bridge_off", "tobt", "tsat", "push_request", "pushback", "aobt", "engine_start", "atot", "baggage"]
    : ["origin_dep", "eldt", "inbound_landing", "runway_exit", "aibt", "bridge_on", "door_open", "cargo_open",
      "checkin_start", "checkin_end", "ground_service", "boarding_start", "urge_boarding", "boarding_end",
      "door_close", "cargo_close", "bridge_off", "tobt", "tsat", "push_request", "pushback", "aobt", "engine_start", "atot", "aldt"];
  const flowRank = new Map(flowOrder.map((key, index) => [key, index]));

  const merged: FishboneNode[] = [];
  const added = new Set<string>();
  const candidates: Array<{ node: FishboneNode; rank: number; seq: number }> = [];

  nodes.forEach((node, seq) => {
    added.add(node.key);
    candidates.push({ node, rank: flowRank.get(node.key) ?? 1000 + seq, seq });
  });
  baseline.forEach(([key, name], index) => {
    if (!added.has(key)) {
      added.add(key);
      candidates.push({ node: byKey.get(key) || makePlaceholder(key, name, index), rank: flowRank.get(key) ?? 500 + index, seq: 10000 + index });
    }
  });
  candidates.sort((a, b) => a.rank - b.rank || a.seq - b.seq);
  for (const item of candidates) merged.push(item.node);

  // Order nodes with recorded/planned timestamps chronologically while keeping untimed nodes after their predecessor.
  const comps = merged.map(node => nodeTimeComparable(node));
  const useIso = comps.every(c => (c.isoMs === null && c.clockMin === null) || c.isoMs !== null);
  const timed: Array<{ time: number; idx: number; node: FishboneNode }> = [];
  const anchoredAfter = new Map<number | null, FishboneNode[]>();
  let lastTimedIdx: number | null = null;
  merged.forEach((node, idx) => {
    const c = comps[idx];
    const t = useIso ? c.isoMs : c.clockMin;
    if (t !== null) {
      timed.push({ time: t, idx, node });
      lastTimedIdx = idx;
    } else {
      const bucket = anchoredAfter.get(lastTimedIdx) || [];
      bucket.push(node);
      anchoredAfter.set(lastTimedIdx, bucket);
    }
  });
  if (!timed.length) return merged;
  timed.sort((a, b) => a.time - b.time || a.idx - b.idx);
  const ordered: FishboneNode[] = [...(anchoredAfter.get(null) || [])];
  for (const item of timed) {
    ordered.push(item.node);
    ordered.push(...(anchoredAfter.get(item.idx) || []));
  }
  return ordered;
}

export function turnaroundFlightNodes(data: Pick<VisualTurnaround, "flight_nodes" | "milestones" | "operations" | "pipeline">, direction?: string): FishboneNode[] {
  if (data.flight_nodes?.length) return data.flight_nodes;
  const nodes = completeFlightNodes([], direction);
  const record = (key: string, name: string, field: "actual_time" | "plan_time", value?: string | null, code = "") => {
    if (!value || clockMinutes(value) === null) return;
    let node = nodes.find(item => item.key === key);
    if (!node) {
      node = { key, name, code, category: "control", position: "top", status: "pending", status_label: "未记录", time_display: "" };
      nodes.push(node);
    }
    node[field] = value;
    node.time_display = node.actual_time || value;
    node.status = node.actual_time ? "completed" : "planned";
    node.status_label = node.actual_time ? "有实际记录" : code === "TOBT" || code === "TSAT" ? "目标时刻" : "有计划时刻";
  };
  record("aibt", "挡轮挡", "actual_time", data.milestones.aibt);
  record("aobt", "撤轮挡", "plan_time", data.milestones.sobt);
  record("tobt", "目标推出", "plan_time", data.milestones.tobt, "TOBT");
  record("tsat", "目标开车批准", "plan_time", data.milestones.tsat, "TSAT");
  const boarding = data.operations.find(operation => operation.id === "boarding");
  record("boarding_start", "开始登机", "actual_time", boarding?.actual_start);
  record("boarding_start", "开始登机", "plan_time", boarding?.plan_start);
  record("boarding_end", "登机完成", "actual_time", boarding?.actual_end);
  record("boarding_end", "登机完成", "plan_time", boarding?.plan_end);
  for (const [key, name] of [["door_close", "舱门关闭"], ["bridge_off", "撤桥"], ["aobt", "撤轮挡"]]) {
    const point = data.operations.flatMap(operation => operation.milestones || []).find(node => node.name === name);
    record(key, name, "actual_time", point?.actual);
    record(key, name, "plan_time", point?.plan);
  }
  return nodes;
}

export function turnaroundSummaryNodes(data: Pick<VisualTurnaround, "flight_nodes" | "ground_nodes" | "milestones" | "operations" | "pipeline">): FishboneNode[] {
  const nodes = completeFlightNodes(turnaroundFlightNodes(data));
  return ["aibt", "door_open", "boarding_end", "door_close", "aobt", "pushback"]
    .map(key => {
      const node = nodes.find(item => item.key === key);
      return node?.actual_time ? node : data.ground_nodes?.find(item => item.key === key && item.actual_time) || node;
    })
    .filter((node): node is FishboneNode => Boolean(node));
}

export function turnaroundHeading(data: { query_identifier?: string; craft_no?: string; flight_pair?: string; out_flight?: string; in_flight?: string }) {
  const query = data.query_identifier?.trim();
  const flight = data.flight_pair || data.out_flight || data.in_flight;
  const aircraftQuery = Boolean(query && data.craft_no && query.replaceAll("-", "").toUpperCase() === data.craft_no.replaceAll("-", "").toUpperCase());
  const subject = query || flight || data.craft_no;
  const pair = data.in_flight && data.out_flight ? `进港 ${data.in_flight} → 出港 ${data.out_flight}` : "";
  const detail = [aircraftQuery ? (flight ? `对应航班 ${flight}` : "") : data.craft_no && `执飞机号 ${data.craft_no}`, pair || (!aircraftQuery && query && flight && query !== flight && `航班连班 ${flight}`)].filter(Boolean).join(" · ");
  return { title: subject ? `${subject} · 过站保障` : "过站保障", detail };
}

export function clockMinutes(value: string | null | undefined): number | null {
  const match = /^(\d{1,2}):(\d{2})(?::(\d{2}))?$/.exec(value || "");
  if (!match) return null;
  const hour = Number(match[1]), minute = Number(match[2]), second = Number(match[3] || 0);
  return hour < 24 && minute < 60 && second < 60 ? hour * 60 + minute : null;
}

export function timelineWindow(value: string) {
  const [first, last] = (value || "").split(/\s+[-–—]\s+/);
  const start = clockMinutes(first), end = clockMinutes(last);
  // shortcut: clock-only cross-day windows are ambiguous; show records until full timestamps are supplied.
  return start !== null && end !== null && end > start ? { start, end } : null;
}

export function operationClock(operation: TurnaroundOperation) {
  const nodes = operation.milestones || [];
  return [operation.actual_start, operation.actual_end, ...nodes.map(node => node.actual),
    operation.plan_start, operation.plan_end, ...nodes.map(node => node.plan)]
    .find(value => clockMinutes(value) !== null);
}

export function sortTurnaroundOperations<T extends TurnaroundOperation>(operations: T[], range: string): T[] {
  if (!timelineWindow(range)) return [...operations];
  return [...operations].sort((a, b) => (clockMinutes(operationClock(a)) ?? 24 * 60) - (clockMinutes(operationClock(b)) ?? 24 * 60));
}

export function turnaroundFishboneLayout(operations: TurnaroundOperation[], range: string) {
  const window = timelineWindow(range), rows: number[][] = [[], []];
  const cardHeight = Math.max(152, ...operations.map(operation => 88 + (operation.milestones?.length || 0) * 24));
  const positioned: { operation: TurnaroundOperation; point: number; left: number; side: number; level: number; top: number }[] = [];
  const unplaced: TurnaroundOperation[] = [];
  for (const operation of sortTurnaroundOperations(operations, range)) {
    const point = window ? timelinePoint(operationClock(operation), window) : null;
    if (point === null) { unplaced.push(operation); continue; }
    const anchor = 3 + point * .94, side = positioned.length % 2;
    const preferred = Math.min(anchor + 3, 78);
    let level = 0;
    // Move cards, never their time anchors; crowded cards use another level.
    while (rows[side][level] !== undefined && Math.max(preferred, rows[side][level] + 22) > 78) level++;
    const left = Math.max(preferred, (rows[side][level] ?? -22) + 22);
    rows[side][level] = left;
    positioned.push({ operation, point: anchor, left, side, level, top: 0 });
  }
  const step = cardHeight + 24, upper = Math.max(1, rows[0].length), lower = rows[1].length;
  const axisY = 96 + upper * step;
  for (const node of positioned) node.top = node.side === 0 ? 48 + (upper - node.level - 1) * step : axisY + 80 + node.level * step;
  return { positioned, unplaced, cardHeight, axisY, height: axisY + (lower ? 96 + lower * step : 64) };
}

export function timelineSpan(start: string | null | undefined, end: string | null | undefined, window: { start: number; end: number }) {
  const first = clockMinutes(start), last = clockMinutes(end);
  if (first === null || last === null || last <= first || last <= window.start || first >= window.end) return null;
  const left = Math.max(first, window.start), right = Math.min(last, window.end);
  return { left: (left - window.start) / (window.end - window.start) * 100, width: (right - left) / (window.end - window.start) * 100 };
}
export function hasOperationEnd(operation: TurnaroundOperation): boolean {
  return Boolean(operation.actual_end || operation.milestones?.length && operation.milestones.every(node => node.actual));
}

export function turnaroundRecordSummary(operations: TurnaroundOperation[]) {
  const ended = operations.filter(hasOperationEnd).length;
  const startOnly = operations.filter(operation => operation.actual_start && !hasOperationEnd(operation));
  const endOnly = operations.filter(operation => operation.actual_end && !operation.actual_start);
  const missing = operations.filter(operation => ![operation.actual_start, operation.actual_end, operation.plan_start, operation.plan_end].some(Boolean) && !operation.milestones?.length);
  const details = [startOnly.length && `${startOnly.map(operation => operation.name).join("、")}有开始记录，结束时刻未提供`, endOnly.length && `${endOnly.map(operation => operation.name).join("、")}仅有结束时刻`, missing.length && `${missing.map(operation => operation.name).join("、")}未提供作业时刻`].filter(Boolean);
  return { total: operations.length, ended, incomplete: operations.length - ended,
    title: operations.length ? `${ended} 项作业有结束记录` : "暂缺保障作业时刻",
    detail: details.length ? `${details.join("；")}。` : "仅反映本次返回作业的记录完整性。" };
}

export function timelinePoint(value: string | null | undefined, window: { start: number; end: number }) {
  const minute = clockMinutes(value);
  return minute !== null && minute >= window.start && minute <= window.end ? (minute - window.start) / (window.end - window.start) * 100 : null;
}

