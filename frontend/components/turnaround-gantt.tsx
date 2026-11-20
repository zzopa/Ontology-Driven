"use client";

import { useState } from "react";
import { AlertCircle, ArrowRight, CalendarDays, ChartGantt, Check, Circle, Clock3, FileText, GitBranch, Plane } from "lucide-react";
import type { FishboneNode, TurnaroundOperation, VisualTurnaround } from "@/lib/types";
import { completeTurnaroundOperations, hasOperationEnd, sortTurnaroundOperations, timelinePoint, timelineSpan, timelineWindow, turnaroundChartRows, turnaroundFlightNodes, turnaroundHeading, turnaroundOperationTimeline, turnaroundRecordSummary, turnaroundSummaryNodes } from "@/lib/turnaround-layout";
import { TurnaroundFishbone } from "./turnaround-fishbone";
import { FlightNodeList } from "./flight-milestones";
import s from "./turnaround-gantt.module.css";

const STATES = { completed: "有结束记录", in_progress: "结束未记录", delayed: "超计划", planned: "计划 / 未记录" };

export function TurnaroundGantt({ data, onQuestion, compact = false }: { data: VisualTurnaround; onQuestion?: (question: string) => void; compact?: boolean }) {
  const [activeRecordIndex, setActiveRecordIndex] = useState(0);
  const activeData = (data.flight_records && data.flight_records[activeRecordIndex]) || data;
  const [incompleteOnly, setIncompleteOnly] = useState(false);
  const [showAllDetails, setShowAllDetails] = useState(false);
  const [view, setView] = useState<"gantt" | "fishbone">("fishbone");
  const chartRows = turnaroundChartRows(activeData);
  const timeline = { ...activeData, ...turnaroundOperationTimeline({ ...activeData, operations: chartRows }) };
  const sortedOperations = sortTurnaroundOperations(completeTurnaroundOperations(activeData.operations), timeline.timeline_range);
  const heading = turnaroundHeading(activeData), summary = turnaroundRecordSummary(sortedOperations);
  const origin = activeData.source_row?.origin_airport, destination = activeData.source_row?.dest_airport;
  const route = typeof origin === "string" && typeof destination === "string" && origin && destination ? `${origin} → ${destination}` : "";
  const window = timelineWindow(timeline.timeline_range);
  const observed = window ? timelinePoint(activeData.milestones.current_time, window) : null;
  const target = window ? timelinePoint(activeData.milestones.tobt, window) : null;
  const operations = incompleteOnly ? sortedOperations.filter(operation => !hasOperationEnd(operation)) : sortedOperations;
  const rows: (TurnaroundOperation & { node?: FishboneNode })[] = compact ? operations : sortTurnaroundOperations(chartRows, timeline.timeline_range).filter(operation => !incompleteOnly || !operation.node && !hasOperationEnd(operation));
  const flightNodes = turnaroundFlightNodes(activeData, activeData.in_flight && !activeData.out_flight ? "ARR" : "DEP");
  const extraNodes = compact ? (activeData.ground_nodes || []).filter(node => !flightNodes.some(existing => existing.key === node.key && (existing.actual_time || "") === (node.actual_time || "") && (existing.plan_time || "") === (node.plan_time || "") && (existing.estimated_time || "") === (node.estimated_time || ""))).map(node => ({ ...node, key: `ground:${node.key}` })) : [];
  const nodes = <FlightNodeList nodes={[...flightNodes, ...extraNodes]} direction={activeData.in_flight && !activeData.out_flight ? "ARR" : "DEP"} source={activeData.flight_nodes_source || (compact ? undefined : "历史快照已有时刻已保留；此快照未保存的完整节点时刻可重新查询获取")} />;
  return <section className={`${s.sheet} ${compact ? s.focused : ""}`} aria-label="过站保障作业记录">
    {data.flight_records && data.flight_records.length > 1 && (
      <div className={s.flightRecordTabs} role="tablist" aria-label="执飞飞机切换">
        {data.flight_records.map((rec, idx) => {
          const isSelected = idx === activeRecordIndex;
          const typeLabel = rec.in_flight && rec.out_flight ? "进出港连班过站" : rec.out_flight ? "出港航班" : "进港航班";
          return (
            <button
              key={idx}
              type="button"
              role="tab"
              aria-selected={isSelected}
              className={`${s.flightRecordTab} ${isSelected ? s.flightRecordTabActive : ""}`}
              onClick={() => setActiveRecordIndex(idx)}
            >
              <Plane size={14} aria-hidden="true" />
              <strong>{rec.craft_no ? `飞机 ${rec.craft_no}` : `执飞记录 ${idx + 1}`}</strong>
              <span className={s.flightRecordMeta}>
                {typeLabel}{rec.stand ? ` · ${rec.stand}机位` : ""}{rec.gate ? ` · ${rec.gate}登机口` : ""}
              </span>
            </button>
          );
        })}
      </div>
    )}
    <header className={s.caseHeader}>
      <div className={s.identity}><span className={s.flightGlyph}><Plane size={28} strokeWidth={1.6} aria-hidden="true" /></span><div><span className={s.kicker}>航班保障记录</span><h3>{compact ? heading.title.replace(" · 过站保障", "") : heading.title}</h3>{compact && route && <span className={s.route}>{route}</span>}<p>{[heading.detail, activeData.stand && `机位 ${activeData.stand}`, activeData.gate && `登机口 ${activeData.gate}`].filter(Boolean).join(" · ")}</p></div></div>
      <div className={s.businessDay}><CalendarDays size={15} aria-hidden="true" /><span>业务日<strong>{activeData.flight_date || "未提供"}</strong></span></div>
    </header>
    {!compact && <div className={s.recordOverview}>
      <div><h4>{summary.title}</h4><p>{summary.detail}</p></div>
      {summary.total > 0 && <div className={s.completeness}><strong>{summary.ended}<small> / {summary.total}</small></strong><span>有结束记录 / 返回作业</span><div className={s.recordSegments} aria-hidden="true">{sortedOperations.map(operation => <i key={operation.id} data-recorded={hasOperationEnd(operation)} />)}</div></div>}
    </div>}
    {activeData.selection_note && <div className={s.dateNote}><CalendarDays size={13} aria-hidden="true" /><span><b>日期口径</b>{activeData.selection_note}</span></div>}
    {activeData.available_record && <div className={s.recordChoice}><div><strong>不同业务日的保障记录分别查看</strong><p>当前：{activeData.flight_date} · {activeData.source_label || "当前记录"}；另有 {activeData.available_record.flight_date} · {activeData.available_record.source_label}（{activeData.available_record.recorded_operations} 类作业有时刻）。</p></div>{onQuestion ? <button type="button" onClick={() => onQuestion(activeData.available_record!.question)}>查询 {activeData.available_record.flight_date} 保障记录<ArrowRight size={14} aria-hidden="true" /></button> : <p>可查询：{activeData.available_record.question}</p>}</div>}
    <dl className={s.clockRail}>{[["实际靠位", "AIBT", activeData.milestones.aibt], ["计划撤轮挡", "SOBT", activeData.milestones.sobt], ["目标撤轮挡", "TOBT", activeData.milestones.tobt], ["目标开车批准", "TSAT", activeData.milestones.tsat]].map(([label, code, value]) => <div key={code} data-code={code} data-present={Boolean(value)}><dt title={code ?? undefined}>{label}</dt><dd>{value || "未记录"}</dd></div>)}</dl>
    {!compact && nodes}
    <div className={s.chartPanel}>
    <div className={s.chartHeader}><div className={s.chartHeading}><h4>{compact ? "过站保障时序" : view === "gantt" ? "保障节点与作业时序" : "保障作业 · 鱼骨时序"}</h4><div className={s.viewSwitch} role="group" aria-label="图表视图"><button type="button" aria-pressed={view === "gantt"} onClick={() => setView("gantt")}><ChartGantt size={14} aria-hidden="true" />时序图</button><button type="button" aria-pressed={view === "fishbone"} onClick={() => setView("fishbone")}><GitBranch size={14} aria-hidden="true" />鱼骨图</button></div></div><div className={s.filters} aria-label="作业显示范围"><button aria-pressed={!incompleteOnly} onClick={() => setIncompleteOnly(false)}>全部作业<span>{summary.total}</span></button><button aria-pressed={incompleteOnly} onClick={() => setIncompleteOnly(true)}>缺结束记录<span>{summary.incomplete}</span></button></div></div>
    <div className={s.chartLegend}>{view === "gantt" ? <><span><i className={s.rangeKey} />实际作业区间</span><span><i className={s.pointKey} />实际时点</span><span><i className={s.planKey} />计划区间或预计时点</span></> : <><span><Check size={12} aria-hidden="true" />有结束记录</span><span><Circle size={11} aria-hidden="true" />缺结束记录</span><span><i className={s.planKey} />计划时刻</span><span title="TOBT"><i className={s.targetKey} />目标撤轮挡</span></>}<span className={s.observation}><Clock3 size={12} aria-hidden="true" />{activeData.milestones.current_time ? `观测时刻 ${activeData.milestones.current_time}` : "观测时刻未提供"}</span></div>
    {activeData.operations.length === 0 && <p className={s.emptyNote}>本条记录未提供保障作业时刻，保留全部作业列表。</p>}
    <>
      {!window && <p className={s.scaleNote}>时间窗口不足或跨日，保留原始时刻与作业顺序，不绘制推算时长。</p>}
      {view === "fishbone" ? <TurnaroundFishbone data={timeline} operations={operations} /> : <>
      <div className={s.chartScroll} tabIndex={0} role="region" aria-label="保障节点与作业时序，可横向滚动"><div className={s.chartGrid}>
        <div className={s.axis}><span>{compact ? "保障作业" : "保障节点 / 作业"}</span><div className={s.timeAxis}>{window && timeline.time_ticks.map(tick => { const point = timelinePoint(tick, window); return point !== null ? <time key={tick} style={{ left: `${point}%` }}>{tick}</time> : null; })}{target !== null && <span className={s.targetTag} title="目标撤轮挡（TOBT）" style={{ left: `${target}%` }}>目标 {activeData.milestones.tobt}</span>}</div><span>实际 / 计划时刻</span></div>
        {rows.map(operation => {
          const { actual_start: start, actual_end: end } = operation;
          const actual = window && timelineSpan(start, end, window);
          const plan = window && timelineSpan(operation.plan_start, operation.plan_end, window);
          const point = window && !actual ? timelinePoint(end || start, window) : null;
          const reference = operation.node && window ? timelinePoint(operation.plan_end, window) : null;
          const actualLabel = compact && !operation.milestones?.length && (start && end ? (start === end ? end : `${start} – ${end}`) : start ? `开始 ${start}` : end ? `结束 ${end}` : "");
          // Offsetting by the same fraction of the label width keeps both edges inside the lane.
          const labelPosition = actual ? actual.left + actual.width / 2 : point ?? 0;
          return <div className={s.row} data-state={operation.status} key={operation.id}>
            <div className={s.operation}><span className={s.stateIcon}>{hasOperationEnd(operation) ? <Check size={18} aria-hidden="true" /> : operation.status === "delayed" ? <AlertCircle size={18} aria-hidden="true" /> : <Clock3 size={18} aria-hidden="true" />}</span><div><strong>{operation.name}</strong><small>{operation.node ? operation.node.actual_time ? "有实际记录" : operation.node.estimated_time ? "有预计时刻" : operation.node.plan_time ? "有计划时刻" : "未记录" : operation.actual_end && !operation.actual_start ? "仅有结束时刻" : STATES[operation.status]}</small></div></div>
            <div className={s.lane}>
              {window && timeline.time_ticks.map(tick => { const point = timelinePoint(tick, window); return point !== null ? <i className={s.gridLine} key={tick} style={{ left: `${point}%` }} /> : null; })}
              {plan && <span className={s.plannedBar} style={{ left: `${plan.left}%`, width: `${plan.width}%` }} title={`计划 ${operation.plan_start} 至 ${operation.plan_end}`} />}
              {actual && <span className={s.actualBar} style={{ left: `${actual.left}%`, width: `${actual.width}%` }} title={`实际 ${start} 至 ${end}`} />}
              {point !== null && <span className={operation.actual_end ? s.endPoint : s.startPoint} style={{ left: `${point}%` }} title={`${operation.node ? "实际" : operation.actual_end ? "结束" : "开始"} ${operation.actual_end || operation.actual_start}`} />}
              {reference !== null && <span className={s.startPoint} style={{ left: `${reference}%` }} title={`${operation.node?.estimated_time ? "预计" : "计划"} ${operation.plan_end}`} />}
              {operation.milestones?.length ? <div className={s.rowMilestones}>{operation.milestones.map((node, index) => <span key={index}><b>{node.name}</b><small>计划 {node.plan || "—"} · 实际 {node.actual || "—"}</small></span>)}</div> : !actualLabel && !actual && !plan && point === null && reference === null && <span className={s.missing}>{operation.actual_start || operation.actual_end || operation.plan_start || operation.plan_end ? "时刻见右侧记录" : "未提供时刻"}</span>}
              {actualLabel && <span className={s.actualLabel} style={{ left: `${labelPosition}%`, transform: `translateX(${-labelPosition}%)` }}>{actualLabel}</span>}
              {target !== null && <span className={s.targetLine} style={{ left: `${target}%` }} />}
              {observed !== null && <span className={s.observedLine} style={{ left: `${observed}%` }} />}
            </div>
            <div className={s.times}>{operation.node ? <><span>{operation.node.actual_time || "未记录"}</span>{operation.node.estimated_time && <small>预计 {operation.node.estimated_time}</small>}{operation.node.plan_time && <small>计划 {operation.node.plan_time}</small>}</> : <><span>{operation.actual_start || "—"}<ArrowRight size={12} aria-hidden="true" />{operation.actual_end || "—"}</span>{(operation.plan_start || operation.plan_end) && <small>计划 {operation.plan_start || "—"} / {operation.plan_end || "—"}</small>}{operation.status === "delayed" && operation.delay_min != null && <b>超计划 {operation.delay_min} 分钟</b>}</>}</div>
          </div>;
        })}
        {operations.length === 0 && <p className={s.filteredEmpty}>本次返回作业均有结束记录。</p>}
      </div></div>
      <p className={s.chartFootnote} title="按实绩时间升序排列：优先开始，其次结束；无实绩时用计划时间，无时间的置后。同一时间保持原顺序。">时间轴覆盖进港保障节点、作业及出港节点；时点用标记，作业区间用横条。缺时刻的记录保留，未记录不等于未发生。</p>
      </>}
    </>
    {compact && <><ol className={s.nodeRail} aria-label="关键保障节点">{turnaroundSummaryNodes(activeData).map(node => <li key={node.key} data-recorded={Boolean(node.actual_time)}><span>{node.actual_time ? <Check size={16} aria-hidden="true" /> : <Circle size={16} aria-hidden="true" />}</span><strong>{node.name}</strong><small>{!node.actual_time && (node.estimated_time ? "预计 " : node.plan_time ? "计划 " : "")}{node.actual_time || node.estimated_time || node.plan_time || "未记录"}</small></li>)}</ol><div className={s.operationDetails}><table><caption>保障作业明细</caption><thead><tr><th>保障作业</th><th>开始</th><th>结束</th><th>状态</th></tr></thead><tbody>{(showAllDetails ? sortedOperations : sortedOperations.slice(0, 2)).map(operation => <tr key={operation.id}><td>{operation.name}</td><td>{operation.actual_start || "—"}</td><td>{operation.actual_end || "—"}</td><td><span data-recorded={hasOperationEnd(operation)}>{STATES[operation.status]}</span></td></tr>)}</tbody></table><button type="button" onClick={() => setShowAllDetails(value => !value)} aria-expanded={showAllDetails}>{showAllDetails ? "收起明细" : "查看全部明细"}<ArrowRight size={14} aria-hidden="true" /></button></div></>}
    {!compact && activeData.pipeline.length > 0 && <section className={s.pipeline}><h4>关键节点链路</h4><ol>{activeData.pipeline.map(node => <li key={node.step} data-state={node.status}><span>{String(node.step).padStart(2, "0")}</span><h5>{node.name}</h5><dl><div><dt>计划</dt><dd>{node.plan || "—"}</dd></div>{node.target && <div><dt>目标</dt><dd>{node.target}</dd></div>}<div><dt>实际</dt><dd>{node.actual || "—"}</dd></div></dl></li>)}</ol></section>}
    {activeData.conclusion.summary && <details className={s.explanation}><summary><FileText size={13} aria-hidden="true" />记录摘要依据</summary><p>{activeData.conclusion.summary}</p>{activeData.conclusion.meta.evidence && <small>{activeData.conclusion.meta.evidence}</small>}</details>}
    </div>
    {compact && <details className={s.extraNodes}><summary>完整航班节点与记录</summary>{nodes}{activeData.pipeline.length > 0 && <div className={s.pipelineRecords}>{activeData.pipeline.map(node => <p key={node.step}><strong>{node.name}</strong>计划 {node.plan || "—"} · 目标 {node.target || "—"} · 实际 {node.actual || "—"}</p>)}</div>}</details>}
  </section>;
}
