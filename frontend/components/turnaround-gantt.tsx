"use client";

import { useState } from "react";
import { AlertCircle, ArrowRight, CalendarDays, ChartGantt, Check, Circle, Clock3, FileText, GitBranch, Plane } from "lucide-react";
import type { VisualTurnaround } from "@/lib/types";
import { completeTurnaroundOperations, hasOperationEnd, sortTurnaroundOperations, timelinePoint, timelineSpan, timelineWindow, turnaroundFlightNodes, turnaroundHeading, turnaroundOperationTimeline, turnaroundRecordSummary } from "@/lib/turnaround-layout";
import { TurnaroundFishbone } from "./turnaround-fishbone";
import { FlightNodeList } from "./flight-milestones";
import s from "./turnaround-gantt.module.css";

const STATES = { completed: "有结束记录", in_progress: "结束未记录", delayed: "超计划", planned: "计划 / 未记录" };

export function TurnaroundGantt({ data, onQuestion }: { data: VisualTurnaround; onQuestion?: (question: string) => void }) {
  const [incompleteOnly, setIncompleteOnly] = useState(false);
  const [view, setView] = useState<"gantt" | "fishbone">("gantt");
  const timeline = { ...data, ...turnaroundOperationTimeline(data) };
  const sortedOperations = sortTurnaroundOperations(completeTurnaroundOperations(data.operations), timeline.timeline_range);
  const heading = turnaroundHeading(data), summary = turnaroundRecordSummary(sortedOperations);
  const window = timelineWindow(timeline.timeline_range);
  const observed = window ? timelinePoint(data.milestones.current_time, window) : null;
  const target = window ? timelinePoint(data.milestones.tobt, window) : null;
  const operations = incompleteOnly ? sortedOperations.filter(operation => !hasOperationEnd(operation)) : sortedOperations;
  return <section className={s.sheet} aria-label="过站保障作业记录">
    <header className={s.caseHeader}>
      <div className={s.identity}><span className={s.flightGlyph}><Plane size={28} strokeWidth={1.6} aria-hidden="true" /></span><div><span className={s.kicker}>航班保障记录</span><h3>{heading.title}</h3><p>{[heading.detail, data.stand && `机位 ${data.stand}`, data.gate && `登机口 ${data.gate}`].filter(Boolean).join(" · ")}</p></div></div>
      <div className={s.businessDay}><CalendarDays size={15} aria-hidden="true" /><span>业务日<strong>{data.flight_date || "未提供"}</strong></span></div>
    </header>
    <div className={s.recordOverview}>
      <div><h4>{summary.title}</h4><p>{summary.detail}</p></div>
      {summary.total > 0 && <div className={s.completeness}><strong>{summary.ended}<small> / {summary.total}</small></strong><span>有结束记录 / 返回作业</span><div className={s.recordSegments} aria-hidden="true">{sortedOperations.map(operation => <i key={operation.id} data-recorded={hasOperationEnd(operation)} />)}</div></div>}
    </div>
    {data.selection_note && <div className={s.dateNote}><CalendarDays size={13} aria-hidden="true" /><span><b>日期口径</b>{data.selection_note}</span></div>}
    {data.available_record && <div className={s.recordChoice}><div><strong>不同业务日的保障记录分别查看</strong><p>当前：{data.flight_date} · {data.source_label || "当前记录"}；另有 {data.available_record.flight_date} · {data.available_record.source_label}（{data.available_record.recorded_operations} 类作业有时刻）。</p></div>{onQuestion ? <button type="button" onClick={() => onQuestion(data.available_record!.question)}>查询 {data.available_record.flight_date} 保障记录<ArrowRight size={14} aria-hidden="true" /></button> : <p>可查询：{data.available_record.question}</p>}</div>}
    <dl className={s.clockRail}>{[["实际靠位", "AIBT", data.milestones.aibt], ["计划撤轮挡", "SOBT", data.milestones.sobt], ["目标撤轮挡", "TOBT", data.milestones.tobt], ["目标开车批准", "TSAT", data.milestones.tsat]].map(([label, code, value]) => <div key={code} data-code={code} data-present={Boolean(value)}><dt><b>{code}</b>{label}</dt><dd>{value || "未记录"}</dd></div>)}</dl>
    <FlightNodeList nodes={turnaroundFlightNodes(data, data.in_flight && !data.out_flight ? "ARR" : "DEP")} direction={data.in_flight && !data.out_flight ? "ARR" : "DEP"} source={data.flight_nodes_source || "历史快照已有时刻已保留；此快照未保存的完整节点时刻可重新查询获取"} />
    <div className={s.chartPanel}>
    <div className={s.chartHeader}><div className={s.chartHeading}><h4>{view === "gantt" ? "保障作业时序" : "保障作业 · 鱼骨时序"}</h4><div className={s.viewSwitch} role="group" aria-label="图表视图"><button type="button" aria-pressed={view === "gantt"} onClick={() => setView("gantt")}><ChartGantt size={14} aria-hidden="true" />时序图</button><button type="button" aria-pressed={view === "fishbone"} onClick={() => setView("fishbone")}><GitBranch size={14} aria-hidden="true" />鱼骨图</button></div></div><div className={s.filters} aria-label="作业显示范围"><button aria-pressed={!incompleteOnly} onClick={() => setIncompleteOnly(false)}>全部作业<span>{summary.total}</span></button><button aria-pressed={incompleteOnly} onClick={() => setIncompleteOnly(true)}>缺结束记录<span>{summary.incomplete}</span></button></div></div>
    <div className={s.chartLegend}>{view === "gantt" ? <><span><i className={s.rangeKey} />实绩区间</span><span><i className={s.pointKey} />单一时点</span><span><i className={s.planKey} />计划区间</span></> : <><span><Check size={12} aria-hidden="true" />有结束记录</span><span><Circle size={11} aria-hidden="true" />缺结束记录</span><span><i className={s.planKey} />计划时刻</span><span><i className={s.targetKey} />目标时刻（TOBT）</span></>}<span className={s.observation}><Clock3 size={12} aria-hidden="true" />{data.milestones.current_time ? `观测时刻 ${data.milestones.current_time}` : "观测时刻未提供"}</span></div>
    {data.operations.length === 0 && <p className={s.emptyNote}>本条记录未提供保障作业时刻，保留全部作业列表。</p>}
    <>
      {!window && <p className={s.scaleNote}>时间窗口不足或跨日，保留原始时刻与作业顺序，不绘制推算时长。</p>}
      {view === "fishbone" ? <TurnaroundFishbone data={timeline} operations={operations} /> : <>
      <div className={s.chartScroll} tabIndex={0} role="region" aria-label="保障作业时序，可横向滚动"><div className={s.chartGrid}>
        <div className={s.axis}><span>保障作业</span><div className={s.timeAxis}>{window && timeline.time_ticks.map(tick => { const point = timelinePoint(tick, window); return point !== null ? <time key={tick} style={{ left: `${point}%` }}>{tick}</time> : null; })}{target !== null && <span className={s.targetTag} style={{ left: `${target}%` }}>TOBT {data.milestones.tobt}</span>}</div><span>实际起止 / 计划起止</span></div>
        {operations.map(operation => {
          const actual = window && timelineSpan(operation.actual_start, operation.actual_end, window);
          const plan = window && timelineSpan(operation.plan_start, operation.plan_end, window);
          const point = window && !actual ? timelinePoint(operation.actual_end || operation.actual_start, window) : null;
          return <div className={s.row} data-state={operation.status} key={operation.id}>
            <div className={s.operation}><span className={s.stateIcon}>{hasOperationEnd(operation) ? <Check size={18} aria-hidden="true" /> : operation.status === "delayed" ? <AlertCircle size={18} aria-hidden="true" /> : <Clock3 size={18} aria-hidden="true" />}</span><div><strong>{operation.name}</strong><small>{operation.actual_end && !operation.actual_start ? "仅有结束时刻" : STATES[operation.status]}</small></div></div>
            <div className={s.lane}>
              {window && timeline.time_ticks.map(tick => { const point = timelinePoint(tick, window); return point !== null ? <i className={s.gridLine} key={tick} style={{ left: `${point}%` }} /> : null; })}
              {plan && <span className={s.plannedBar} style={{ left: `${plan.left}%`, width: `${plan.width}%` }} title={`计划 ${operation.plan_start} 至 ${operation.plan_end}`} />}
              {actual && <span className={s.actualBar} style={{ left: `${actual.left}%`, width: `${actual.width}%` }} title={`实际 ${operation.actual_start} 至 ${operation.actual_end}`} />}
              {point !== null && <span className={operation.actual_end ? s.endPoint : s.startPoint} style={{ left: `${point}%` }} title={`${operation.actual_end ? "结束" : "开始"} ${operation.actual_end || operation.actual_start}`} />}
              {operation.milestones?.length ? <div className={s.rowMilestones}>{operation.milestones.map((node, index) => <span key={index}><b>{node.name}</b><small>计划 {node.plan || "—"} · 实际 {node.actual || "—"}</small></span>)}</div> : !actual && !plan && point === null && <span className={s.missing}>{operation.actual_start || operation.actual_end || operation.plan_start || operation.plan_end ? "时刻见右侧记录" : "未提供时刻"}</span>}
              {target !== null && <span className={s.targetLine} style={{ left: `${target}%` }} />}
              {observed !== null && <span className={s.observedLine} style={{ left: `${observed}%` }} />}
            </div>
            <div className={s.times}><span>{operation.actual_start || "—"}<ArrowRight size={12} aria-hidden="true" />{operation.actual_end || "—"}</span>{(operation.plan_start || operation.plan_end) && <small>计划 {operation.plan_start || "—"} / {operation.plan_end || "—"}</small>}{operation.status === "delayed" && operation.delay_min != null && <b>超计划 {operation.delay_min} 分钟</b>}</div>
          </div>;
        })}
        {operations.length === 0 && <p className={s.filteredEmpty}>本次返回作业均有结束记录。</p>}
      </div></div>
      <p className={s.chartFootnote} title="按实绩时间升序排列：优先开始，其次结束；无实绩时用计划时间，无时间的置后。同一时间保持原顺序。">时间轴按保障作业及相关计划时刻确定；缺时刻的作业保留，未记录不等于未发生。</p>
      </>}
    </>
    {data.pipeline.length > 0 && <section className={s.pipeline}><h4>关键节点链路</h4><ol>{data.pipeline.map(node => <li key={node.step} data-state={node.status}><span>{String(node.step).padStart(2, "0")}</span><h5>{node.name}</h5><dl><div><dt>计划</dt><dd>{node.plan || "—"}</dd></div>{node.target && <div><dt>目标</dt><dd>{node.target}</dd></div>}<div><dt>实际</dt><dd>{node.actual || "—"}</dd></div></dl></li>)}</ol></section>}
    {data.conclusion.summary && <details className={s.explanation}><summary><FileText size={13} aria-hidden="true" />记录摘要依据</summary><p>{data.conclusion.summary}</p>{data.conclusion.meta.evidence && <small>{data.conclusion.meta.evidence}</small>}</details>}
    </div>
  </section>;
}
