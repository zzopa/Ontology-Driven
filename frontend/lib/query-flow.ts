import type { ExecutionStep } from "./types.ts";

export const FLOW_NODES = [
  { id: "prepare", label: "接入准备", note: "连接数据库与加载上下文" },
  { id: "rules", label: "直接匹配本体", note: "用原问题识别对象与条件" },
  { id: "normalize", label: "语义标准化", note: "将口语表达对齐业务术语" },
  { id: "rematch", label: "重新匹配规则", note: "用标准化问题再次匹配" },
  { id: "model", label: "模型规划兜底", note: "生成受本体约束的计划" },
  { id: "validate", label: "校验查询计划", note: "核对字段、口径与关联" },
  { id: "query", label: "执行数据查询", note: "编译 SQL 并只读查库" },
  { id: "answer", label: "整理证据与回答", note: "依据真实查询结果生成" },
  { id: "done", label: "完成本轮", note: "封装结果与执行记录" },
] as const;

export type FlowNodeId = typeof FLOW_NODES[number]["id"];
export type FlowState = ExecutionStep["state"] | "pending" | "unvisited" | "unrecorded";

function nodeFor(step: ExecutionStep): FlowNodeId | undefined {
  const id = step.id.split(":").at(-1)!;
  if (id === "connect" || id === "follow_up") return "prepare";
  if (id.startsWith("rule_match_") || id === "ontology" || id === "intent") return "rules";
  if (id.startsWith("semantic_normalize_")) return "normalize";
  if (id.startsWith("rule_rematch_")) return "rematch";
  if (id.startsWith("model_route_") || id.startsWith("model_plan_") || id === "model") return "model";
  if (["plan", "relations", "plan_roles", "plan_cache", "coverage_notice"].includes(id)) return "validate";
  if (id.startsWith("sql") || id === "turnaround_lookup" || id === "forecast_history") return "query";
  if (id === "evidence" || id.startsWith("answer") || id === "forecast_backtest") return "answer";
  if (id === "done" || id === "query_error") return "done";
}

export function stepElapsed(step: ExecutionStep, busy: boolean, elapsed: number): number {
  return step.state === "running" && busy ? Math.max(step.elapsed, elapsed - step.started_at, 0) : step.elapsed;
}

export function queryFlow(steps: ExecutionStep[], busy: boolean, elapsed: number) {
  const recordedRoute = steps.some(step => /(?:^|:)(?:rule_(?:re)?match_|semantic_normalize_|model_(?:plan|route)_)/.test(step.id));
  const pastPlanning = steps.some(step => ["validate", "query", "answer", "done"].includes(nodeFor(step) || ""));
  const nodes = FLOW_NODES.map(node => {
    const items = steps.filter(step => nodeFor(step) === node.id);
    const active = items.filter(step => step.state === "running").at(-1);
    const last = [...items].sort((a, b) => (a.started_at + a.elapsed) - (b.started_at + b.elapsed)).at(-1);
    const state: FlowState = active ? (busy ? "running" : "interrupted") : last?.state
      || (recordedRoute && (pastPlanning || !busy) ? "unvisited" : busy ? "pending" : "unrecorded");
    // Nested evidence steps overlap; use interval coverage instead of adding their durations twice.
    const intervals = items.map(step => [step.started_at, step.started_at + stepElapsed(step, busy, elapsed)])
      .sort((a, b) => a[0] - b[0]);
    let duration = 0, end = -Infinity;
    for (const [start, stop] of intervals) {
      duration += Math.max(0, stop - Math.max(start, end));
      end = Math.max(end, stop);
    }
    return { ...node, state, elapsed: duration, recorded: items.length > 0, count: items.length, message: (active || last)?.message };
  });
  const current = steps.filter(step => step.state === "running" && nodeFor(step))
    .sort((a, b) => a.started_at - b.started_at).at(-1);
  const total = Math.max(elapsed, ...steps.map(step => step.started_at + stepElapsed(step, busy, elapsed)), 0);
  return { nodes, current: busy ? current?.label : undefined, currentNode: busy && current ? nodeFor(current) : undefined, total };
}

export function queryFlowExpressions(steps: ExecutionStep[], busy: boolean, question = "") {
  const normalized = steps.filter(step => /(?:^|:)semantic_normalize_/.test(step.id)).at(-1);
  const firstMatch = steps.find(step => /(?:^|:)rule_match_/.test(step.id));
  const original = question || firstMatch?.details[0]
    || (normalized?.state === "running" ? normalized.details[0] : "") || "未记录原始问题";
  let standard = busy ? "按需生成标准表达" : "本轮未记录标准表达";
  if (normalized?.state === "completed" && normalized.details[0]) standard = normalized.details[0];
  else if (normalized?.state === "running" && busy) standard = "正在生成标准表达…";
  else if (normalized?.state === "skipped") standard = "保留原始问法";
  else if (normalized) standard = "标准化未完成，保留原始问法";
  else if (steps.some(step => /(?:^|:)rule_match_/.test(step.id)) && !busy) standard = "本轮未使用语义标准化";
  return { original, standard };
}
