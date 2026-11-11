"use client";

import type { CSSProperties } from "react";
import { AlertCircle, ArrowRight, BaggageClaim, Brush, Check, Clock3, DoorOpen, Fuel, Plane, Users, Utensils } from "lucide-react";
import type { TurnaroundOperation, VisualTurnaround } from "@/lib/types";
import { hasOperationEnd, timelinePoint, timelineWindow, turnaroundFishboneLayout } from "@/lib/turnaround-layout";
import s from "./turnaround-fishbone.module.css";

const ICONS = { cleaning: Brush, catering: Utensils, fueling: Fuel, boarding: Users, loading: BaggageClaim, bridge_push: DoorOpen };
const STATES = { completed: "有结束记录", in_progress: "结束未记录", delayed: "超计划", planned: "计划 / 未记录" };

function OperationCard({ operation, style }: { operation: TurnaroundOperation; style?: CSSProperties }) {
  const ended = hasOperationEnd(operation);
  const Icon = ICONS[operation.id as keyof typeof ICONS] || Plane;
  const StateIcon = ended ? Check : operation.status === "delayed" ? AlertCircle : Clock3;
  const state = ended ? "completed" : operation.status === "completed" ? "in_progress" : operation.status;
  return <article className={s.card} data-state={state} style={style}>
    <div className={s.cardHeading}><Icon size={22} strokeWidth={1.6} aria-hidden="true" /><h5>{operation.name}</h5><StateIcon size={15} aria-hidden="true" /></div>
    {operation.milestones?.length ? <dl className={s.milestones}>{operation.milestones.map((node, index) => <div key={index}><dt>{node.name}</dt><dd>实际 {node.actual || "—"} · 计划 {node.plan || "—"}</dd></div>)}</dl> : <>
      <div className={s.actual}><span className={s.timeLabel}>实际</span><time>{operation.actual_start || "—"}</time><ArrowRight size={12} aria-hidden="true" /><time>{operation.actual_end || "—"}</time></div>
      {(operation.plan_start || operation.plan_end) && <p className={s.plan}>计划 {operation.plan_start || "—"} → {operation.plan_end || "—"}</p>}
    </>}
    <p className={s.status}>{ended && operation.actual_end && !operation.actual_start ? "仅有结束时刻" : STATES[state]}{operation.status === "delayed" && operation.delay_min != null && ` · ${operation.delay_min} 分钟`}</p>
  </article>;
}

export function TurnaroundFishbone({ data, operations }: { data: VisualTurnaround; operations: TurnaroundOperation[] }) {
  const window = timelineWindow(data.timeline_range);
  const layout = turnaroundFishboneLayout(operations, data.timeline_range);
  const target = window ? timelinePoint(data.milestones.tobt, window) : null;
  const observed = window ? timelinePoint(data.milestones.current_time, window) : null;
  if (!operations.length) return <p className={s.empty}>本次返回作业均有结束记录。</p>;
  return <>
    {layout.positioned.length > 0 && <div className={s.scroll} tabIndex={0} role="region" aria-label="保障作业鱼骨图，可横向滚动">
      <div className={s.canvas} style={{ height: layout.height }}>
        <svg className={s.connectors} viewBox={`0 0 1000 ${layout.height}`} preserveAspectRatio="none" aria-hidden="true">
          {layout.positioned.map(node => <line key={node.operation.id} className={s.rib} x1={node.point * 10} y1={layout.axisY} x2={(node.left + 2) * 10} y2={node.side === 0 ? node.top + layout.cardHeight : node.top} />)}
        </svg>
        <div className={s.spine} style={{ top: layout.axisY }} aria-hidden="true" />
        {window && data.time_ticks.map(tick => {
          const point = timelinePoint(tick, window);
          return point !== null ? <span key={tick} className={s.tick} style={{ left: `${3 + point * .94}%`, top: layout.axisY }}><time>{tick}</time></span> : null;
        })}
        {target !== null && <>
          <span className={s.targetLine} style={{ left: `${3 + target * .94}%` }} aria-hidden="true" />
          <span className={s.targetLabel} style={{ left: `${Math.max(8, Math.min(92, 3 + target * .94))}%` }}>TOBT {data.milestones.tobt}<small>目标撤轮挡</small></span>
        </>}
        {observed !== null && <span className={s.observed} style={{ left: `${3 + observed * .94}%`, top: layout.axisY - 24 }}><small>观测 {data.milestones.current_time}</small></span>}
        {layout.positioned.map(node => <div key={node.operation.id}>
          <span className={s.anchor} data-end-only={Boolean(node.operation.actual_end && !node.operation.actual_start)} data-planned={!node.operation.actual_start && !node.operation.actual_end && !node.operation.milestones?.some(item => item.actual)} style={{ left: `${node.point}%`, top: layout.axisY }} aria-hidden="true" />
          <OperationCard operation={node.operation} style={{ left: `${node.left}%`, top: node.top, height: layout.cardHeight }} />
        </div>)}
      </div>
    </div>}
    {layout.unplaced.length > 0 && <section className={s.unplaced} aria-label="未定位作业记录"><p>以下作业无法按当前时间轴定位，保留原始记录。</p><div>{layout.unplaced.map(operation => <OperationCard key={operation.id} operation={operation} />)}</div></section>}
    <p className={s.note}>按首个已知实绩时刻排列，无实绩时用计划时刻；同一时刻的作业并行展示。仅有结束时刻的作业以实心菱形标记。“未记录”不等于“未发生”。</p>
  </>;
}
