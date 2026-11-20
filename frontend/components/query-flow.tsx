"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowRight, Check, ChevronDown, CircleAlert, Clock3, FileText, Flag, GitBranch, Pause, Plane, Settings2, SkipForward } from "lucide-react";
import { formatStepElapsed } from "@/lib/execution";
import { queryFlow, queryFlowExpressions, type FlowNodeId } from "@/lib/query-flow";
import type { ExecutionStep } from "@/lib/types";
import { Spinner } from "./ui";
import s from "./query-flow.module.css";

const STATES = { running: "进行中", completed: "已完成", failed: "失败", skipped: "已跳过", interrupted: "停止等待", pending: "待执行", unvisited: "未经过", unrecorded: "未记录" };
const EDGES: { from: FlowNodeId; to: FlowNodeId; kind?: "direct" | "optional" }[] = [
  { from: "prepare", to: "rules" }, { from: "rules", to: "validate", kind: "direct" },
  { from: "rules", to: "normalize" }, { from: "normalize", to: "rematch" },
  { from: "rematch", to: "validate" }, { from: "rematch", to: "model", kind: "optional" },
  { from: "model", to: "validate", kind: "optional" }, { from: "validate", to: "query" },
  { from: "query", to: "answer" }, { from: "answer", to: "done" },
  { from: "rules", to: "model", kind: "optional" }, { from: "normalize", to: "model", kind: "optional" },
];

export function QueryTimings({ total, firstOutputElapsed, compact = false }: {
  total: number; firstOutputElapsed?: number; compact?: boolean;
}) {
  return <div className={compact ? `${s.timings} ${s.compactTimings}` : s.timings}>
    <div className={s.total}><span><Clock3 size={15} aria-hidden="true" />查询耗时</span><time>{formatStepElapsed(total)}</time></div>
    {firstOutputElapsed !== undefined && <div className={s.firstOutput}><span>首次响应</span><time>{formatStepElapsed(firstOutputElapsed)}</time></div>}
  </div>;
}

export function QueryFlow({ steps, busy, elapsed, firstOutputElapsed, question, detailsId, detailsOpen, onToggleDetails }: {
  steps: ExecutionStep[]; busy: boolean; elapsed: number; question: string;
  firstOutputElapsed?: number;
  detailsId: string; detailsOpen: boolean; onToggleDetails: () => void;
}) {
  const { nodes, current, currentNode, total } = queryFlow(steps, busy, elapsed);
  const { original, standard } = queryFlowExpressions(steps, busy, question);
  const used = Object.fromEntries(nodes.map(node => [node.id, node])) as Record<FlowNodeId, typeof nodes[number]>;
  const map = useRef<HTMLDivElement>(null);
  const [paths, setPaths] = useState<string[]>([]);
  const [originPath, setOriginPath] = useState("");
  useEffect(() => {
    const root = map.current;
    if (!root) return;
    function measure() {
      const bounds = root!.getBoundingClientRect();
      const points = Object.fromEntries(nodes.map(node => {
        const port = root!.querySelector(`[data-node="${node.id}"] [data-port]`)!.getBoundingClientRect();
        return [node.id, { x: port.x - bounds.x + port.width / 2, y: port.y - bounds.y + port.height / 2 }];
      })) as Record<FlowNodeId, { x: number; y: number }>;
      const narrow = Math.abs(points.prepare.y - points.rules.y) > 2;
      setOriginPath(narrow ? "" : `M20,${points.prepare.y} H${points.prepare.x}`);
      setPaths(EDGES.map(edge => {
        const a = points[edge.from], b = points[edge.to];
        if (narrow) {
          if (nodes.findIndex(node => node.id === edge.to) - nodes.findIndex(node => node.id === edge.from) > 1) {
            const side = edge.kind === "direct" ? 8 : 16;
            return `M${a.x},${a.y} C${side},${a.y} ${side},${a.y + 16} ${side},${a.y + 24} V${b.y - 24} C${side},${b.y - 8} ${side},${b.y} ${b.x},${b.y}`;
          }
          return `M${a.x},${a.y} C${a.x},${(a.y + b.y) / 2} ${b.x},${(a.y + b.y) / 2} ${b.x},${b.y}`;
        }
        if (edge.kind === "direct") {
          const top = a.y - 56;
          return `M${a.x},${a.y} H${a.x + 26} C${a.x + 66},${a.y} ${a.x + 56},${top} ${a.x + 104},${top} H${b.x - 90} C${b.x - 44},${top} ${b.x - 52},${b.y} ${b.x},${b.y}`;
        }
        if (edge.to === "model") {
          if (edge.from === "rules") {
            const lane = Math.max(a.x + 24, points.normalize.x - 32);
            return `M${a.x},${a.y} H${lane - 24} Q${lane},${a.y} ${lane},${a.y + 24} V${b.y - 24} Q${lane},${b.y} ${lane + 24},${b.y} H${b.x}`;
          }
          return `M${a.x},${a.y} C${a.x},${a.y + 64} ${b.x},${b.y - 54} ${b.x},${b.y}`;
        }
        if (edge.from === "model") {
          const side = b.x + 44;
          return `M${a.x},${a.y} H${side - 24} Q${side},${a.y} ${side},${a.y - 24} V${b.y + 24} Q${side},${b.y} ${side - 24},${b.y} H${b.x}`;
        }
        if (Math.abs(a.y - b.y) < 2) return `M${a.x},${a.y} H${b.x}`;
        const bend = Math.min(76, Math.abs(b.x - a.x) / 2);
        return `M${a.x},${a.y} C${a.x + bend},${a.y} ${b.x - bend},${b.y} ${b.x},${b.y}`;
      }));
    }
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(root);
    root.querySelectorAll("[data-node]").forEach(node => observer.observe(node));
    return () => observer.disconnect();
  }, []);
  const directTaken = used.validate.recorded && !used.normalize.recorded && !used.model.recorded;
  const active = currentNode ? used[currentNode] : undefined;
  return <section className={s.flow} aria-label="问询执行流程图">
    <header className={s.header}>
      <div className={s.heading}>
        <div><h3><GitBranch size={18} aria-hidden="true" />问询流程</h3>{busy && <span className={s.current} role="status">{active?.label || current || "处理中"}</span>}</div>
      </div>
      <QueryTimings total={total} firstOutputElapsed={firstOutputElapsed} />
    </header>
    <div className={s.map} ref={map}>
      <span className={s.origin} aria-hidden="true"><Plane size={18} fill="currentColor" /></span>
      <svg className={s.edges} aria-hidden="true">
        <path d={originPath} className={used.prepare.recorded ? s.taken : undefined} />
        {paths.map((path, i) => {
          const edge = EDGES[i];
          if (edge.to === "model" && edge.from === "rules" && (!used.model.recorded || used.normalize.recorded)) return null;
          if (edge.to === "model" && edge.from === "normalize" && (!used.normalize.recorded || !used.model.recorded || used.rematch.recorded)) return null;
          const taken = used[edge.from].recorded && used[edge.to].recorded && used[edge.to].state !== "skipped"
            && !(edge.kind === "direct" && !directTaken)
            && !(edge.from === "rematch" && edge.to === "validate" && used.model.recorded);
          return <path key={i} d={path} className={taken ? s.taken : undefined} data-from={edge.from} data-to={edge.to} data-optional={edge.kind === "optional"} data-running={taken && used[edge.to].state === "running"} />;
        })}
      </svg>
      <span className={`${s.sign} ${s.directSign}`} data-selected={directTaken}><ArrowRight size={16} aria-hidden="true" />规则直达</span>
      {(!used.model.recorded || used.normalize.recorded) && <span className={`${s.sign} ${s.semanticSign}`} data-selected={used.normalize.recorded}><ArrowRight size={16} aria-hidden="true" />语义解析</span>}
      {nodes.map(node => {
        const StateIcon = node.state === "failed" ? CircleAlert : node.state === "interrupted" ? Pause : node.state === "skipped" ? SkipForward : node.id === "done" && !node.recorded ? Flag : Check;
        return <div key={node.id} data-node={node.id} data-state={node.state} className={s.node} aria-current={node.state === "running" ? "step" : undefined} aria-label={`${node.label}，${STATES[node.state]}${node.recorded ? `，${formatStepElapsed(node.elapsed)}` : ""}`}>
          <div className={s.caption}><strong>{node.id === "model" && <Settings2 size={15} aria-hidden="true" />}{node.label}{node.id === "model" && <small>可选</small>}</strong><p>{node.note}</p></div>
          <span className={s.station} data-port aria-hidden="true">{node.recorded && node.state !== "running" ? <StateIcon size={14} /> : node.id === "done" ? <Flag size={12} /> : null}</span>
          <div className={s.timing}>
            {node.state === "running" && <span><Spinner />进行中</span>}
            {node.state === "completed" && node.id === "rules" && <span>已检查</span>}
            {["failed", "skipped", "interrupted"].includes(node.state) && <span>{STATES[node.state]}</span>}
            {node.id === "model" && !node.recorded ? <span>{node.state === "unrecorded" ? "未记录" : "未触发"}</span> : <time>{node.recorded ? formatStepElapsed(node.elapsed) : "—"}</time>}
          </div>
        </div>;
      })}
    </div>
    <div className={s.legend}><span><i className={s.completedMark}><Check size={10} aria-hidden="true" /></i>已完成</span><span><i className={s.runningMark} />进行中</span><span><i />待执行</span><span><i className={s.optionalMark} />可选路径</span><small>重叠步骤不重复计时</small></div>
    <div className={s.expressions}>
      <div><strong>原始问题</strong><span title={original}>{original}</span></div><ArrowRight className={s.expressionArrow} size={22} aria-hidden="true" />
      <div><strong>标准表达</strong><span title={standard} data-processing={used.normalize.state === "running"}>{standard}</span></div>
      <button type="button" onClick={onToggleDetails} aria-expanded={detailsOpen} aria-controls={detailsId}><FileText size={18} aria-hidden="true" />{detailsOpen ? "收起详细记录" : "展开详细记录"}<ChevronDown size={14} className={detailsOpen ? s.expanded : undefined} aria-hidden="true" /></button>
    </div>
  </section>;
}
