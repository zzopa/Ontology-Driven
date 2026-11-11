"use client";

import { AlertCircle, ArrowRight, Check, Circle, Clock3, MapPin, Plane } from "lucide-react";
import type { FishboneNode, VisualFishbone } from "@/lib/types";
import { completeFlightNodes } from "@/lib/turnaround-layout";
import s from "./answer-layout.module.css";

export function FlightMilestones({ data }: { data: VisualFishbone }) {
  return <section className={s.flight} aria-label="航班与保障节点">
    <header className={s.flightIdentity}>
      <div className={s.flightNumber}><span className={s.planeMark}><Plane size={25} strokeWidth={1.6} aria-hidden="true" /></span><div><span className={s.eyebrow}>{data.direction_label || "航班问询"}</span><h3>{data.flight_no || "航班信息"}</h3></div></div>
      <div className={s.flightRoute}>{data.origin && <strong>{data.origin}</strong>}{data.origin && data.destination && <ArrowRight size={20} aria-hidden="true" />}{data.destination && <strong>{data.destination}</strong>}{data.flight_date && <time>{data.flight_date}</time>}</div>
      {data.status && <span className={s.flightStatus}>{data.status}</span>}
    </header>
    <dl className={s.flightDetails}>{[["机号", data.aircraft_reg], ["机型", data.aircraft_type], ["机位", data.stand], ["登机口", data.gate]].map(([label, value]) => <div key={label}><dt>{label === "机位" && <MapPin size={12} aria-hidden="true" />}{label}</dt><dd>{value || "未提供"}</dd></div>)}</dl>
    <FlightNodeList nodes={data.nodes} direction={data.direction} />
  </section>;
}

export function FlightNodeList({ nodes, direction, source }: { nodes: FishboneNode[]; direction?: string; source?: string }) {
  const allNodes = completeFlightNodes(nodes, direction);
  const completed = allNodes.filter(node => node.actual_time && node.code !== "TOBT" && node.code !== "TSAT").length;
  return <section className={s.fullProcess} aria-label="完整航班节点">
    <div className={s.milestoneHeading}><div><h4>完整航班节点</h4><p>完整保留流程节点，可横向滚动查看全部；计划、预计与实际分别展示</p></div><span>{completed} / {allNodes.length} 个节点有实际记录</span></div>
    <div className={s.nodeLegend}><span><Check size={12} aria-hidden="true" />有实际记录</span><span><Clock3 size={12} aria-hidden="true" />计划 / 预计 / 目标时刻</span><span><Circle size={12} aria-hidden="true" />未记录</span></div>
    <ol className={s.milestones} tabIndex={0} aria-label="航班节点流程，可横向滚动">{allNodes.map((node, i) => {
      const target = node.code === "TOBT" || node.code === "TSAT";
      const state = target ? (node.plan_time ? "planned" : "pending") : node.actual_time ? node.status : node.plan_time || node.estimated_time ? "planned" : "pending";
      const Icon = state === "completed" ? Check : state === "delayed" ? AlertCircle : state === "ongoing" ? Clock3 : Circle;
      const status = target ? (node.plan_time ? "目标时刻" : "未记录") : node.actual_time ? node.status_label : node.estimated_time ? "有预计时刻" : node.plan_time ? "有计划时刻" : "未记录";
      return <li key={node.key} className={s.milestone} data-state={state}>
        <div className={s.nodeHeading}><span className={s.nodeIndex}>{String(i + 1).padStart(2, "0")}</span><span className={s.nodeState}><Icon size={13} aria-hidden="true" />{status}</span></div>
        <h5>{node.name}<small>{node.code}</small></h5>
        <dl>{[[target ? "参考记录" : "实际", node.actual_time, node.actual_at], ["预计", node.estimated_time, node.estimated_at], [target ? "目标" : "计划", node.plan_time, node.plan_at]].map(([label, value, timestamp]) => <div key={label}><dt>{label}</dt><dd data-missing={!value}><time dateTime={timestamp || undefined} title={timestamp || undefined}>{value || "未记录"}</time></dd></div>)}</dl>
        {node.note && <p>{node.note}</p>}
      </li>;
    })}</ol>
    <p className={s.recordNote}>{source && <>{source}。<br /></>}未记录不等于未发生；预计、计划和目标时间不表示实际完成。跨日时刻保留日期。</p>
  </section>;
}
