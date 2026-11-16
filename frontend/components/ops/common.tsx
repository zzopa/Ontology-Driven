"use client";

import type { ReactNode } from "react";
import { AlertTriangle, Check, Circle, Clock3, Plane } from "lucide-react";
import { kinds, opsTime, resourceConfirmed, timelinePosition, type OpsFlight, type OpsProposal, type OpsResource, type OpsSnapshot, type OpsTask } from "@/lib/ops";
import s from "./ops.module.css";

export function Panel({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return <section className={s.panel}><div className={s.panelHead}><h2>{title}</h2>{aside}</div>{children}</section>;
}

export function Tag({ tone = "neutral", children }: { tone?: string; children: ReactNode }) {
  return <span className={s.badge} data-tone={tone}>{children}</span>;
}

export function Empty({ children }: { children: ReactNode }) { return <div className={s.empty}>{children}</div>; }

export function MapPanel({ snapshot, selected, onSelect }: { snapshot: OpsSnapshot; selected: string; onSelect: (id: string) => void }) {
  const flightStands = snapshot.flights.map(f => f.stand).filter(Boolean);
  const codes = [...new Set([...flightStands, ...snapshot.stands.map(x => x.code)])];
  return <Panel title="机坪与资源态势" aside={<span className={s.muted}>机位示意</span>}>
    <div className={s.mapTitle}>{snapshot.terminal} 航站楼</div>
    {codes.length ? <div className={s.mapGrid}>{codes.map(code => {
      const flights = snapshot.flights.filter(f => f.stand === code), risk = flights.some(f => f.risk);
      const current = flights.some(f => f.id === selected);
      return <button className={s.stand} key={code} data-risk={risk} aria-pressed={current} disabled={!flights.length} aria-label={`机位 ${code}，${flights.map(f => f.flight_no).join("、") || "当前窗口无关联航班"}`} onClick={() => onSelect(flights[0].id)}><strong>{code}</strong><Plane size={28} aria-hidden="true" /><small>{flights.map(f => f.flight_no).join(" / ") || "窗口内无航班"}</small></button>;
    })}</div> : <Empty>当前航站楼未返回机位资料</Empty>}
    <div className={s.legend}>抽象机位排列，非真实地理比例；点击联动航班</div>
    <div className={s.panelBody}><h3 style={{ fontSize: 14 }}>班组可用情况</h3>{Object.entries(kinds).map(([kind, name]) => {
      const list = snapshot.resources.filter(r => r.kind === kind && r.enabled);
      const free = list.filter(r => r.availability === "idle" && resourceConfirmed(r, snapshot.captured_at) && !snapshot.tasks.some(t => t.resource_id === r.id && !["complete", "cancelled", "reported_complete"].includes(t.status) && (t.status === "active" && !t.actual_end || Date.parse(t.start_at) <= Date.parse(snapshot.captured_at) && Date.parse(t.end_at) > Date.parse(snapshot.captured_at)))).length;
      return <div key={kind} className={s.capacityRow}><span>{name}</span><div className={s.capacityBar}><i style={{ width: `${list.length ? free / list.length * 100 : 0}%` }} /></div><span>{list.length ? `${free}空闲 / ${list.length}` : "待配置"}</span></div>;
    })}<p className={s.muted} style={{ marginTop: 10 }}>空闲仅基于已登记任务与10分钟内的位置确认；派单仍需资质和排班检查。</p></div>
  </Panel>;
}

export function FlightMatrix({ snapshot, selected, onSelect, search = "", attentionOnly = false }: { snapshot: OpsSnapshot; selected: string; onSelect: (id: string) => void; search?: string; attentionOnly?: boolean }) {
  const flights = snapshot.flights.filter(f => (!attentionOnly || f.risk) && `${f.flight_no} ${f.stand} ${f.aircraft_reg}`.toLowerCase().includes(search.toLowerCase()));
  return <><div className={s.legend}>已加载 {flights.length} 条航班记录（进港 {flights.filter(f => f.direction === "ARR").length} / 出港 {flights.filter(f => f.direction === "DEP").length}）；下表状态指保障作业，进出港分列。{snapshot.mode === "replay" && <>实绩仅展示截至 {opsTime(snapshot.captured_at, true)} 的记录。</>}</div><div className={s.scroll} tabIndex={0} role="region" aria-label="多航班五类保障矩阵，可横向滚动"><table className={s.matrix}><thead><tr><th>航班</th><th>机位</th><th>TOBT / 预计</th>{Object.values(kinds).map(name => <th key={name}>{name}</th>)}</tr></thead><tbody>{flights.map(f => <tr key={f.id} className={`${f.risk ? s.risky : ""} ${selected === f.id ? s.selected : ""}`} onClick={() => onSelect(f.id)}><td><button className={s.flightButton} onClick={e => { e.stopPropagation(); onSelect(f.id); }}>{f.flight_no}</button><small>{f.direction === "ARR" ? "进港" : "出港"} · {f.aircraft_reg || "机号未记录"}</small></td><td>{f.stand || "未分配"}</td><td>{opsTime(f.tobt || f.target_at)}<small>{f.tobt ? "发布TOBT" : "计划/预计时刻"}</small></td>{f.operations.map(op => {
    const conflict = snapshot.alerts.some(a => a.severity === "high" && a.resource_id && a.resource_id === op.resource_id && a.kind === op.kind && a.flight_ids.includes(f.id));
    const tone = conflict || op.status === "risk" ? "bad" : op.status === "completed" ? "good" : op.status === "in_progress" ? "info" : "neutral";
    const Icon = conflict || op.status === "risk" ? AlertTriangle : op.status === "completed" ? Check : op.status === "in_progress" ? Clock3 : Circle;
    return <td key={op.kind} title={`${op.replay_censored ? "较晚实绩按当前预演时刻隐藏；" : op.status === "missing" ? "本条保障记录未提供该作业实绩，不代表没有航班；" : ""}计划 ${opsTime(op.plan_start, true)}—${opsTime(op.plan_end, true)}；截至此时的源实绩 ${opsTime(op.actual_start, true)}—${opsTime(op.actual_end, true)}`}><Tag tone={tone}><Icon size={12} aria-hidden="true" />{conflict ? "资源冲突" : op.status_label}</Tag>{op.manual_state && <small>人工：{op.manual_state === "active" ? "执行中" : op.manual_state === "reported_complete" ? "待核验" : op.manual_state === "complete" ? "已核验" : "已报工"}</small>}</td>;
  })}</tr>)}</tbody></table>{!flights.length && <Empty>当前范围没有匹配航班；空结果不代表现实业务量为零。</Empty>}</div><div className={s.legend}><Tag tone="good">完成</Tag><Tag tone="info">进行中</Tag><Tag>作业未记录</Tag><Tag>此时无实绩 / 待开始</Tag><Tag tone="bad">待核查 / 冲突</Tag><span>作业未记录 ≠ 没有航班；未记录 ≠ 未发生</span><span>显示 {flights.length} / {snapshot.metrics.flights} 班</span></div></>;
}

export function Workload({ snapshot }: { snapshot: OpsSnapshot }) {
  const slots = snapshot.workload.filter(x => x.kind === "cleaning");
  return <Panel title="未来时段资源负荷"><div className={s.scroll}><table className={s.loadTable}><thead><tr><th>保障类型</th>{slots.map(x => <th key={x.start_at}>{opsTime(x.start_at)}—{opsTime(x.end_at)}</th>)}</tr></thead><tbody>{Object.entries(kinds).map(([kind, name]) => <tr key={kind}><td>{name}</td>{snapshot.workload.filter(x => x.kind === kind).map(x => <td key={x.start_at} data-gap={Boolean(x.gap)}>{x.available === null ? "班组/排班待配置" : `需求 ${x.demand} / 确认能力 ${x.available}${x.gap ? ` · 缺${x.gap}` : ""}`}</td>)}</tr>)}</tbody></table></div><div className={s.legend}>仅计算已登记任务；未登记的外部任务和需求不在统计范围。</div></Panel>;
}

export function ResourceTimeline({ tasks, resources, proposal, after = false, start, end, compact = false }: { tasks: OpsTask[]; resources: OpsResource[]; proposal?: OpsProposal; after?: boolean; start: string; end: string; compact?: boolean }) {
  const updated = tasks.map(t => after && proposal && t.id === proposal.target.id ? { ...t, resource_id: proposal.resource.id, start_at: proposal.start_at, end_at: proposal.end_at } : t);
  const ticks = Array.from({ length: 5 }, (_, i) => new Date(Date.parse(start) + (Date.parse(end) - Date.parse(start)) * i / 4).toISOString());
  return <div className={`${s.timeline} ${compact ? s.timelineCompact : ''}`}><div className={s.timelineInner}><div className={s.axis}>{ticks.map(x => <span key={x}>{opsTime(x, Date.parse(end) - Date.parse(start) > 86400000)}</span>)}</div>{resources.map(r => {
    const list = updated.filter(t => t.resource_id === r.id && t.status !== "cancelled" && Date.parse(t.start_at) < Date.parse(end) && Date.parse(t.end_at) > Date.parse(start));
    return <div className={s.timeRow} key={r.id}><span>{r.name}{!compact && <small className={s.muted} style={{ display: "block" }}>{kinds[r.kind]}</small>}</span><div className={s.lane} style={{ minHeight: Math.max(compact ? 27 : 48, list.length * (compact ? 22 : 35)) }}>{list.map((t, index) => {
      const left = timelinePosition(t.start_at, start, end), right = timelinePosition(t.end_at, start, end);
      const overlap = list.some(other => other.id !== t.id && Date.parse(t.start_at) < Date.parse(other.end_at) && Date.parse(other.start_at) < Date.parse(t.end_at));
      return <div className={s.timeBar} key={t.id} data-preview={after && proposal?.target.id === t.id} data-conflict={overlap} style={{ left: `${left}%`, width: `${Math.max(1, right - left)}%`, top: index * (compact ? 22 : 35) }} title={`${t.flight_no} ${opsTime(t.start_at, true)}—${opsTime(t.end_at, true)}`}><span>{t.flight_no} {opsTime(t.start_at)}—{opsTime(t.end_at)}</span></div>;
    })}{!list.length && <span className={s.muted}>此时间窗无登记任务</span>}</div></div>;
  })}{!resources.length && <Empty>请先登记班组和排班</Empty>}<p className={s.timelineNote}>实线为登记安排，虚线为方案预演；甘特图不代表作业已经完成。</p></div></div>;
}

export function FlightSummary({ flight }: { flight: OpsFlight }) {
  return <div className={s.panelBody}><strong>{flight.flight_no} · {flight.stand || "机位未分配"}</strong><p className={s.muted}>{flight.flight_date} · {flight.aircraft_reg} · {flight.gate || "登机口未记录"}</p><div className={s.row} style={{ marginTop: 10 }}>{[["AIBT", flight.aibt], ["SOBT", flight.sobt], ["TOBT", flight.tobt], ["TSAT", flight.tsat]].map(([label, value]) => <Tag key={label}>{label} {opsTime(value)}</Tag>)}</div></div>;
}
