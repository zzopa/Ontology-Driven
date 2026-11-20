"use client";

import { useCallback, useEffect, useId, useMemo, useState } from "react";
import { AlertTriangle, ArrowRight, ArrowUpRight, Boxes, CalendarRange, ChevronLeft, ChevronRight, Code2, Database, Expand, FileCheck2, Flag, GitBranch, HelpCircle, Info, Layers, ListChecks, RefreshCw, Search, SearchX, ShieldCheck, Sparkles, Table2, X } from "lucide-react";
import { request } from "@/lib/api";
import { formatValue } from "@/lib/query";
import { answerCopyText, answerSections, displayAnswer } from "@/lib/answer-document";
import type { ExecutionStep, Fact, Field, FieldMeta, PageData, QueryResult, Row, VisualMeta } from "@/lib/types";
import { liveAnswerState } from "@/lib/live-answer";
import { turnaroundRecordSummary } from "@/lib/turnaround-layout";
import { queryElapsed } from "@/lib/query-flow";
import { Badge, Button, Card, CopyButton, Modal, Notice, Spinner } from "./ui";
import { ExecutionProcess } from "./execution-process";
import { QueryTimings } from "./query-flow";
import { TurnaroundGantt } from "./turnaround-gantt";
import { FlightMilestones } from "./flight-milestones";
import { BarVisual, DetailTableVisual, DonutVisual, StatsVisual, TrendVisual } from "./visual-views";
import { AnalysisSummary } from "./analysis-summary";
import { BotMessageContent, RichText } from "./bot-message-content";
import analysisStyle from "./analysis.module.css";
import s from "./answer-layout.module.css";


function parseReportSections(text: string): { title: string; lines: string[]; isBoundary: boolean }[] | null {
  if (!text.includes("【")) return null;
  const parts = text.split(/(?:^|\n)(【[^】\n]+】)/);
  const blocks: { title: string; lines: string[]; isBoundary: boolean }[] = [];
  for (let i = 0; i < parts.length; i++) {
    const part = parts[i].trim();
    if (!part) continue;
    if (part.startsWith("【") && part.endsWith("】")) {
      const title = part.slice(1, -1).trim();
      const rawBody = i + 1 < parts.length && !(parts[i + 1].trim().startsWith("【") && parts[i + 1].trim().endsWith("】")) ? parts[++i].trim() : "";
      const lines = rawBody.split(/\n+/).map(line => line.trim()).filter(Boolean);
      blocks.push({ title, lines, isBoundary: /边界|声明|口径|注意|提示|限制/.test(title) });
    } else if (blocks.length === 0) {
      const lines = part.split(/\n+/).map(line => line.trim()).filter(Boolean);
      if (lines.length) blocks.push({ title: "专题概览", lines, isBoundary: false });
    }
  }
  return blocks.length > 0 ? blocks : null;
}

function MetadataHint({ label, value }: { label: string; value: string }) {
  const id = useId();
  if (!value) return null;
  return <>
    <button type="button" className={s.metadataHint} popoverTarget={id} title={`${label}\n${value}`} aria-label={label}>
      <Info size={15} aria-hidden="true" />
    </button>
    <span id={id} className={s.metadataPopover} popover="auto"><strong>{label}</strong><code>{value}</code></span>
  </>;
}

const identifier = /^(?:[a-z_][\w$]*\.)*[a-z_][\w$]*$/i;

function tableLabel(table: string, name: string | undefined, fallback: string) {
  return name && name !== table && name !== table.split(".").at(-1) ? name : fallback;
}

function SourceList({ sources }: { sources: { name: string; table: string }[] }) {
  if (!sources.length) return null;
  return <section className={s.sourceList} aria-label="数据来源">
    <h3 className={s.sectionKicker}>数据来源</h3>
    <ul>{sources.map(({ name, table }, i) => <li key={table}>
      <Database size={14} aria-hidden="true" />
      <span title={table || undefined}>{tableLabel(table, name, `数据来源 ${i + 1}`)}</span>
      <MetadataHint label="查看数据表名" value={table} />
    </li>)}</ul>
  </section>;
}

function AnswerBody({ result, onEvidence }: { result: QueryResult; onEvidence: (claimIndex: number) => void }) {
  const sections = answerSections(result);
  if (!sections) {
    const reportBlocks = result.intent === "acdm_report" && result.answer ? parseReportSections(result.answer) : null;
    if (reportBlocks) {
      return <>{reportBlocks.map((block, index) => (
        <section className={s.answerSection} data-kind={block.isBoundary ? "limitations" : "query_facts"} key={`${block.title}-${index}`}>
          <span className={s.sectionKicker}>{block.title}</span>
          <div className={s.answerText}>
            <BotMessageContent text={block.lines.join("\n")} />
          </div>
        </section>
      ))}</>;
    }
    const raw = result.answer || "当前未生成回答，请查看数据明细。";
    return (
      <section className={s.answerSection} data-kind="query_facts">
        <h3><Sparkles size={15} aria-hidden="true" />查询结论</h3>
        <div className={s.answerText}>
          <BotMessageContent text={raw} />
        </div>
      </section>
    );
  }
  return <>{sections.map(section => (
    <section className={s.answerSection} data-kind={section.kind} key={section.kind}>
      <h3>{section.kind === "query_facts" ? <><Sparkles size={15} aria-hidden="true" />查询结论</> : section.kind === "limitations" ? "数据范围与限制" : section.title}</h3>
      <div className={s.answerText}>
        {section.claims.map((claim, i) => (
          <div className={s.claim} key={i}>
            <BotMessageContent text={claim.text} />
            {claim.fact_ids.length > 0 && (
              <button type="button" className={s.claimSource} onClick={() => onEvidence(result.claims!.indexOf(claim))} aria-label={`查看${section.title}第 ${i + 1} 条的依据`}>
                <FileCheck2 size={13} aria-hidden="true" />查看依据
              </button>
            )}
          </div>
        ))}
      </div>
    </section>
  ))}</>;
}

function VisualView({ meta, onQuestion, onOpenAllRecords, compact = false }: { meta?: VisualMeta; onQuestion?: (question: string) => void; onOpenAllRecords?: () => void; compact?: boolean }) {
  if (!meta || meta.kind === "none") return null;
  if (meta.kind === "turnaround_gantt") return <TurnaroundGantt data={meta} onQuestion={onQuestion} compact={compact} />;
  if (meta.kind === "stats") return <StatsVisual meta={meta} />;
  if (meta.kind === "donut") return <DonutVisual meta={meta} />;
  if (meta.kind === "bar") return <BarVisual meta={meta} />;
  if (meta.kind === "trend") return <TrendVisual meta={meta} />;
  if (meta.kind === "detail_table") return <DetailTableVisual meta={meta} onQuestion={onQuestion} onOpenAllRecords={onOpenAllRecords} />;
  if (meta.kind === "fishbone") return <FlightMilestones data={meta} onQuestion={onQuestion} />;
  return null;
}

type Cell = { field: string; label: string; value: unknown; unit?: string; comment?: string };

const TECHNICAL_FIELDS = new Set([
  "id", "raw_json", "ext_json", "deleted_at", "created_at", "updated_at",
  "row_version", "biz_key", "link_id", "record_key", "_serial_bucket",
  "sync_run_id", "sync_checkpoint", "sync_error", "pg_flight_id",
  "associated_flight_id", "primary_source_id", "source_system", "source_id",
  "source_flight_id", "source_ffid", "last_seen_at"
]);

// 涵盖航班、客运、货运、航线网络、运行正常率、资源保障等业务优先排序
const FIELD_ORDER_PRIORITY = [
  // 1. 航班与航司核心标识
  "flight_no", "flight_key", "flight_id", "airline_code", "airline_name",
  "flight_date", "exec_date", "direction", "direction_label",
  // 2. 航线与通航网络（航线、航段、始发、目的、经停）
  "route_name_zh", "route", "leg", "leg_seq",
  "origin_city", "dest_city", "via_city",
  "origin_airport", "dest_airport", "takeoff_airport_iata", "arrival_airport_iata",
  "flight_country_type", "route_type",
  // 3. 业务属性（客运、货运、性质）
  "attribute", "flight_task_cd",
  // 4. 客运业务（旅客、座位、客座率、行李）
  "dep_dpsr_depe_num", "psg_arrival_num", "passenger_volume", "pax_total_sum",
  "fpa_available_seat", "available_seat", "max_seat", "seat_capacity", "load_factor",
  "adult_count", "child_count", "infant_count",
  "fpa_luggage", "baggage_weight",
  // 5. 货运业务（货物、邮件、货邮量、业载）
  "fpa_goods", "cargo_weight", "fpa_mail", "mail_weight", "cargo_mail_weight",
  "max_payload", "available_payload", "ops_payload", "mtow",
  // 6. 时刻与状态（计划时刻、实际时刻、起降日、状态）
  "plan_takeoff_time", "real_takeoff_time", "plan_land_time", "real_land_time",
  "plan_time", "actual_time", "flight_status", "status",
  // 7. 延误与正常率（放行、起飞、延误时间、原因）
  "release_on_time", "takeoff_on_time", "dep_on_time", "flight_on_time",
  "clearance_delay_time", "takeoff_delay_time", "flight_delay_time", "delay_minutes",
  "delay_reason", "abnormal_reason", "abnormal_reason_cd",
  // 8. 机位、登机口、航站楼与飞机资源
  "craft_site_cd", "stand_code", "craft_site_is_remote", "gate_cd", "gate_code", "terminal",
  "aircraft_type", "craft_type_icao_cd", "aircraft_reg_no", "aircraft_reg", "craft_no",
  // 9. 地面保障与设施节点
  "node_name", "safeguard_code", "safeguard_real_time", "safeguard_alterate_time",
  "air_bridge_cd", "bridge_duration", "bridge_start_time", "bridge_end_time",
];

const COMMON_FIELD_LABELS: Record<string, string> = {
  attribute: "航班属性",
  flight_country_type: "国际国内",
  flight_task_cd: "任务性质",
  route_name_zh: "航线名称",
  leg: "航段",
  leg_seq: "航段序号",
  origin_city: "起飞城市",
  dest_city: "到达城市",
  via_city: "经停城市",
  origin_airport: "起飞机场",
  dest_airport: "到达机场",
  takeoff_airport_iata: "起飞三字码",
  arrival_airport_iata: "到达三字码",
  fpa_goods: "货物重量(kg)",
  cargo_weight: "货物重量(kg)",
  fpa_mail: "邮件重量(kg)",
  mail_weight: "邮件重量(kg)",
  cargo_mail_weight: "货邮合计(kg)",
  fpa_luggage: "行李重量(kg)",
  baggage_weight: "行李重量(kg)",
  dep_dpsr_depe_num: "出港旅客数",
  psg_arrival_num: "进港旅客数",
  passenger_volume: "旅客总人数",
  pax_total_sum: "旅客量(人)",
  fpa_available_seat: "可用座位数",
  available_seat: "可用座位数",
  load_factor: "客座率(%)",
  adult_count: "成人数",
  child_count: "儿童数",
  infant_count: "婴儿数",
  craft_site_is_remote: "是否远机位",
  is_operated_flight: "是否执飞",
  release_on_time: "放行正常",
  takeoff_on_time: "起飞正常",
  flight_delay_time: "延误时长(分)",
  clearance_delay_time: "放行延误(分)",
  safeguard_code: "保障节点代码",
  safeguard_real_time: "节点实测时刻",
  air_bridge_cd: "登机桥号",
  bridge_duration: "靠桥时长(分)",
};

function fieldLabel(key: string, field: Field | undefined, index: number) {
  const custom = COMMON_FIELD_LABELS[key.toLowerCase()];
  return tableLabel(key, field?.label || custom, identifier.test(key) ? `字段 ${index + 1}` : key);
}

export function DataRows({ rows, table, meta, cards = false }: { rows: Row[]; table: string; meta: FieldMeta; cards?: boolean }) {
  const [cell, setCell] = useState<Cell | null>(null);
  const [showAllCols, setShowAllCols] = useState(false);
  const close = useCallback(() => setCell(null), []);
  const fields = meta[table] || {};

  const allKeys = Array.from(new Set(rows.flatMap(row => Object.keys(row))));
  const hasManyCols = allKeys.length > 8;

  const activeKeys = useMemo(() => {
    if (showAllCols || !hasManyCols) return allKeys;
    // 过滤内部技术字段
    const businessKeys = allKeys.filter(k => !TECHNICAL_FIELDS.has(k.toLowerCase()));
    // 按业务优先级重新排序
    businessKeys.sort((a, b) => {
      const idxA = FIELD_ORDER_PRIORITY.indexOf(a.toLowerCase());
      const idxB = FIELD_ORDER_PRIORITY.indexOf(b.toLowerCase());
      if (idxA !== -1 && idxB !== -1) return idxA - idxB;
      if (idxA !== -1) return -1;
      if (idxB !== -1) return 1;
      return 0;
    });
    // 默认展示最关键的 8~10 个业务列
    return businessKeys.slice(0, 10);
  }, [allKeys, showAllCols, hasManyCols]);

  const columns = activeKeys.map((key, i) => ({
    key, label: fieldLabel(key, fields[key], i), unit: fields[key]?.unit, comment: fields[key]?.comment,
  }));
  const select = (column: typeof columns[number], value: unknown) => setCell({ ...column, field: column.key, value });
  if (!rows.length) return <div className="empty-panel"><Search size={20} aria-hidden="true" /><span>当前条件下未找到记录</span><p>可调整查询对象或时间范围后重试。</p></div>;
  return <>
    {hasManyCols && (
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 8, fontSize: 12 }}>
        <button
          type="button"
          onClick={() => setShowAllCols(v => !v)}
          style={{
            background: "none", border: "1px solid var(--border)", borderRadius: 6,
            padding: "2px 8px", cursor: "pointer", color: "var(--muted)", fontSize: 12,
          }}
        >
          {showAllCols ? `收起冗余字段（当前已显全部 ${allKeys.length} 列）` : `只看关键字段（已精简为 ${columns.length} 列 / 共 ${allKeys.length} 列）`}
        </button>
      </div>
    )}
    {cards ? <div className="record-cards">{rows.map((row, i) => <article className="record-card" key={i}>
      <div className="record-head">记录 {i + 1}</div>
      <div className="field-grid">{columns.filter(column => Object.hasOwn(row, column.key)).map(column => {
        const value = row[column.key];
        return <button type="button" className="field-item" key={column.key} title={column.key} onClick={() => select(column, value)}>
          <span className="field-label">{column.label}<Expand size={12} aria-hidden="true" /></span>
          <span className={`field-value ${value === null ? "is-null" : ""}`}>{formatValue(value)}{column.unit && value != null ? ` ${column.unit}` : ""}</span>
        </button>;
      })}</div>
    </article>)}</div> : <div className="table-wrap"><table className="data-table">
      <thead><tr><th scope="col">序号</th>{columns.map(column => <th scope="col" key={column.key} title={[column.comment, column.key].filter(Boolean).join("\n")}>
        {column.label}{column.unit && <span> · {column.unit}</span>}
      </th>)}</tr></thead>
      <tbody>{rows.map((row, i) => <tr key={i}><td className="row-index">{i + 1}</td>{columns.map(column => <td key={column.key}>
        <button type="button" className="cell-button" title={`查看${column.label}的完整内容`} onClick={() => select(column, row[column.key])}>{formatValue(row[column.key])}</button>
      </td>)}</tr>)}</tbody>
    </table></div>}
    {cell && <Modal title={cell.label} onClose={close}>
      {(cell.unit || cell.comment || cell.field !== cell.label) && <div className="value-meta">
        {cell.unit && <Badge>单位 · {cell.unit}</Badge>}{cell.comment && <span>{cell.comment}</span>}
        {cell.field !== cell.label && <MetadataHint label="查看字段名" value={cell.field} />}
      </div>}
      <pre className="code-block">{formatValue(cell.value)}</pre>
      <div className="modal-actions"><CopyButton text={formatValue(cell.value)} label="复制完整内容" /></div>
    </Modal>}
  </>;
}

function RecordPage({ result, related, onClose }: { result: QueryResult; related?: QueryResult["related"][number]; onClose: () => void }) {
  const [page, setPage] = useState(1), [data, setData] = useState<PageData | null>(null), [error, setError] = useState(""), [loading, setLoading] = useState(true);
  const table = related?.table || result.parse.aggregate_target || result.parse.subject;
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError("");
    request<PageData>(related ? "/api/related" : "/api/records", { method: "POST", signal: controller.signal, body: JSON.stringify({ token: related ? result.related_token : result.drill_token, table: related?.table, page }) })
      .then(d => { setData(d); setLoading(false); }).catch(e => { if (e.name !== "AbortError") { setError(e.message); setLoading(false); } });
    return () => controller.abort();
  }, [page, related, result]);
  const total = data?.total ?? related?.count ?? result.main_total, size = data?.page_size || result.page_size;
  const pages = Math.max(1, Math.ceil(total / size)), shownPage = data?.page || page;
  return <Modal title={related ? tableLabel(table, related.name, "关联明细") : "查询记录明细"} wide onClose={onClose}>
    <div className="data-toolbar"><span>共 {total.toLocaleString()} 条记录 · 每页 {size} 条</span><MetadataHint label="查看数据表名" value={table} /></div>
    {loading ? <div className="empty-panel"><Spinner /><span>正在加载第 {page} 页…</span></div> : error ? <Notice error>{error}</Notice> : data && <DataRows rows={data.rows} table={data.subject || data.table || table} meta={data.field_meta} />}
    <div className="pagination"><span>{total && data && !loading ? `显示 ${(shownPage - 1) * size + 1}–${Math.min(total, shownPage * size)} 条` : ""}</span><div className="pagination-controls"><Button aria-label="上一页" disabled={page <= 1 || loading} onClick={() => setPage(p => p - 1)}><ChevronLeft size={14} />上一页</Button><span>{page} / {pages}</span><Button aria-label="下一页" disabled={page >= pages || loading} onClick={() => setPage(p => p + 1)}>下一页<ChevronRight size={14} /></Button></div></div>
  </Modal>;
}

function Graph({ result }: { result: QueryResult }) {
  const [active, setActive] = useState<string | null>(null);
  const marker = useId();
  const nodes = result.graph?.nodes || [], edges = result.graph?.edges || [];
  const levels = Math.max(1, ...nodes.map(n => n.level));
  const graphHeight = Math.max(350, ...Array.from({ length: levels + 1 }, (_, level) => nodes.filter(n => n.level === level).length * 65 + 45));
  const graphWidth = Math.max(480, (levels + 1) * 180);
  const positions = useMemo(() => Object.fromEntries(nodes.map(node => {
    const group = nodes.filter(n => n.level === node.level), index = group.findIndex(n => n.id === node.id);
    return [node.id, { x: 90 + node.level * 180, y: graphHeight * (index + 1) / (group.length + 1) }];
  })), [nodes, graphHeight]);
  const tableName = (table: string) => {
    const i = nodes.findIndex(node => node.id === table);
    const name = result.related.find(item => item.table === table)?.name
      || result.evidence?.coverage?.find(item => item.table === table)?.name
      || result.evidence?.facts?.find(fact => fact.table === table)?.name
      || (table === result.parse.subject ? result.parse.disp : undefined);
    return tableLabel(table, name, nodes[i]?.main ? "主要数据" : i >= 0 ? `数据表 ${i + 1}` : "关联数据");
  };
  const select = (table: string) => setActive(current => current === table ? null : table);
  if (!edges.length) return <>
    <p className="helper">本次查询暂无可展示的跨表关联。</p>
    <div className={s.graphTables}>{nodes.map(node => <div className={s.graphTable} key={node.id}>
      <Database size={16} aria-hidden="true" /><strong title={node.id}>{tableName(node.id)}</strong>
      <MetadataHint label="查看数据表名" value={node.id} />
    </div>)}</div>
  </>;
  return <>
    <div className="data-toolbar"><span>{nodes.length} 张数据表 · {edges.length} 条查询关系</span></div>
    <div className="graph-layout">
      <div className="graph-canvas" style={{ maxHeight: 420 }}>
        <svg viewBox={`0 0 ${graphWidth} ${graphHeight}`} style={{ height: graphHeight, minWidth: graphWidth }} role="group" aria-label="查询数据表关联图">
          <defs><marker id={marker} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0L10 5L0 10Z" fill="var(--muted)" /></marker></defs>
          {edges.map((edge, i) => {
            const from = positions[edge.source], to = positions[edge.target];
            return from && to ? <line key={i} x1={from.x} y1={from.y} x2={to.x} y2={to.y} className="graph-edge" markerEnd={`url(#${marker})`} opacity={active && active !== edge.source && active !== edge.target ? .15 : 1} /> : null;
          })}
          {nodes.map(node => {
            const p = positions[node.id], name = tableName(node.id);
            return <g key={node.id} className={`graph-node ${node.main ? "main" : ""}`} transform={`translate(${p.x},${p.y})`} role="button" tabIndex={0} aria-label={name} aria-pressed={active === node.id}
              onClick={() => select(node.id)} onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(node.id); } }}>
              <title>{`${name}\n${node.id}`}</title><rect x="-70" y="-20" width="140" height="40" rx="8" />
              <text textAnchor="middle" y="4">{name.length > 10 ? `${name.slice(0, 9)}…` : name}</text>
            </g>;
          })}
        </svg>
      </div>
      <div className="edge-list">{edges.map((edge, i) => <div className={s.edgeRow} key={i}>
        <button type="button" className={`edge-card ${active === edge.source || active === edge.target ? "active" : ""}`} onClick={() => select(edge.source)}>
          <span>{tableName(edge.source)}<ArrowRight size={13} aria-hidden="true" />{tableName(edge.target)}</span>
          {edge.note && <small>{edge.note}</small>}
        </button>
        <MetadataHint label="查看关联字段" value={`${edge.source}.${edge.fcol}\n→ ${edge.target}.${edge.tcol}`} />
      </div>)}</div>
    </div>
  </>;
}

function Evidence({ result, claimIndex }: { result: QueryResult; claimIndex: number | null }) {
  const facts = result.evidence?.facts || [], index = Object.fromEntries(facts.map(fact => [fact.id, fact]));
  const claims = result.claims || [];
  const factRows = (fact: Fact) => fact.values || Object.fromEntries(Object.entries(fact.properties || {}).map(([key, value]) => [key, value.value]));
  return <>
    {claims.length ? claims.map((claim, i) => <details className={`source-claim ${s.evidenceClaim}`} key={i} open={i === claimIndex || undefined}>
      <summary><FileCheck2 size={16} aria-hidden="true" /><span>结论 {i + 1}</span><Badge>{claim.fact_ids.length} 条依据</Badge><ChevronRight size={14} aria-hidden="true" /></summary>
      <div className="answer-text" style={{ marginTop: 12 }}><RichText text={claim.text} /></div>
      {claim.fact_ids.map(id => {
        const fact = index[id];
        if (!fact) return null;
        const fullTable = [fact.source.database, fact.source.table].filter(Boolean).join(".") || fact.table;
        const details = [fullTable, fact.source.record_key ? JSON.stringify(fact.source.record_key) : "", fact.source.queried_at].filter(Boolean).join("\n");
        return <div className="source-fact" key={id}>
          <div className={`source-origin ${s.factOrigin}`}>
            <Database size={14} aria-hidden="true" /><strong title={fullTable}>{tableLabel(fact.table, fact.name, "数据来源")}</strong>
            {fact.source.scope && <span>{fact.source.scope}</span>}
            {fact.business_time != null && <span>业务时间 {formatValue(fact.business_time)}</span>}
            <MetadataHint label="查看来源详情" value={details} />
          </div>
          <DataRows rows={[factRows(fact)]} table={fact.table} meta={result.field_meta} />
        </div>;
      })}
    </details>) : <div className="empty-panel"><FileCheck2 size={20} aria-hidden="true" /><span>本次没有可展开的回答依据</span></div>}
    {!!result.evidence?.coverage?.length && <>
      <h3 className="section-title" style={{ marginTop: 20 }}>来源覆盖范围</h3>
      <div className="coverage-table">{result.evidence.coverage.map((item, i) => <div className={`coverage-row ${s.coverageRow}`} key={item.table}>
        <span title={item.table}>{tableLabel(item.table, item.name, `数据来源 ${i + 1}`)}<MetadataHint label="查看数据表名" value={item.table} /></span>
        <span>{item.provided.toLocaleString()} / {item.total.toLocaleString()} 条 <Badge tone={item.complete ? "green" : "amber"}>{item.complete ? "已完整提供" : "已提供部分"}</Badge></span>
      </div>)}</div>
    </>}
  </>;
}

type QueryRun = {
  question: string; busy: boolean; steps: ExecutionStep[]; elapsed: number; firstOutputElapsed?: number;
  progress: { message: string; percent: number }; delta: string; error: string;
  onStop: () => void; onRetry: () => void;
};

export function ResultView({ result, run, onQuestion, onClarify, compact = false }: { result: QueryResult | null; run?: QueryRun; onQuestion?: (question: string) => void; onClarify?: (choice: { id: string; label: string }) => void; compact?: boolean }) {
  const panelId = useId();
  const [pdf, setPdf] = useState<{ id: string; status: string; error?: string } | null>(null), [pdfBusy, setPdfBusy] = useState(false), [pdfError, setPdfError] = useState("");
  useEffect(() => { setPdf(result?.report_id ? { id: result.report_id, status: "queued" } : null); setPdfError(""); }, [result?.message_id, result?.report_id]);
  useEffect(() => {
    if (!pdf || !["queued", "running"].includes(pdf.status)) return;
    let active = true;
    const update = () => request<{ report: { id: string; status: string; error?: string } }>(`/api/reports/${pdf.id}`).then(r => { if (active) setPdf(r.report); }).catch(e => { if (active) setPdfError(e instanceof Error ? e.message : "PDF状态读取失败"); });
    update(); const timer = setInterval(update, 3000);
    return () => { active = false; clearInterval(timer); };
  }, [pdf?.id, pdf?.status]);
  async function exportPdf() {
    if (!result?.message_id || !result.conversation_id) return;
    setPdfBusy(true); setPdfError("");
    try {
      if (pdf?.status === "failed") {
        await request(`/api/reports/${pdf.id}/retry`, { method: "POST" }); setPdf({ id: pdf.id, status: "queued" });
      } else {
        const r = await request<{ report: { id: string; status: string } }>("/api/reports", { method: "POST", body: JSON.stringify({ message_id: result.message_id, conversation_id: result.conversation_id }) }); setPdf(r.report);
      }
    } catch (e) { setPdfError(e instanceof Error ? e.message : "PDF导出失败"); }
    finally { setPdfBusy(false); }
  }
  const busy = run?.busy || false, delta = run?.delta || "";
  const steps = run?.steps || result?.execution_trace || [];
  const elapsed = run?.elapsed ?? 0;
  const firstOutputElapsed = run?.firstOutputElapsed;
  const [tab, setTab] = useState(result ? "answer" : "process"), [records, setRecords] = useState(false), [related, setRelated] = useState<QueryResult["related"][number] | null>(null), [cards, setCards] = useState(false);
  const [claimIndex, setClaimIndex] = useState<number | null>(null);
  const [fbOpen, setFbOpen] = useState(false), [fbReasons, setFbReasons] = useState<string[]>(["WRONG_DATA"]), [fbComment, setFbComment] = useState(""), [fbBusy, setFbBusy] = useState(false), [fbStatus, setFbStatus] = useState<{ ok: boolean; msg: string } | null>(null);
  const closeRecords = useCallback(() => setRecords(false), []), closeRelated = useCallback(() => setRelated(null), []), closeFb = useCallback(() => setFbOpen(false), []);
  useEffect(() => { setTab(result ? "answer" : "process"); setClaimIndex(null); setRecords(false); setRelated(null); setFbOpen(false); setFbStatus(null); }, [result, busy]);
  const target = result?.parse.aggregate_target || result?.parse.subject || "";
  const relatedHits = result?.related.reduce((sum, item) => sum + item.count, 0) || 0;
  const visual = result?.visual_meta && result.visual_meta.kind !== "none" ? result.visual_meta : null;
  const flight = visual?.kind === "fishbone";
  const turnaround = visual?.kind === "turnaround_gantt" ? visual : null;
  const focused = compact && Boolean(turnaround);
  const turnaroundSummary = turnaround ? turnaroundRecordSummary(turnaround.operations) : null;
  const sources = Array.from(new Map((result?.evidence?.facts || []).map(fact => [fact.table, { name: fact.name, table: fact.table }])).values());
  const liveAnswer = liveAnswerState(delta, busy, Boolean(result));
  const tabs = [
    { id: "answer", label: "查询回答", icon: Sparkles, tone: "info" },
    { id: "data", label: "数据明细", icon: Table2, tone: "info" },
    { id: "evidence", label: "回答依据", icon: ShieldCheck, tone: "success" },
    { id: "graph", label: "数据关联", icon: GitBranch, tone: "info" },
    { id: "process", label: "执行过程", icon: ListChecks, tone: "muted" },
  ];
  const showEvidence = (index: number) => {
    setClaimIndex(index);
    setTab("evidence");
    document.getElementById(`${panelId}-tab-evidence`)?.focus();
  };
  const fbOptions: [string, string][] = [["WRONG_INTENT", "答非所问"], ["WRONG_DATA", "数据不对"], ["WRONG_FILTER", "条件搞错"], ["NO_ANSWER", "未答出"], ["OTHER", "其它问题"]];
  async function submitFeedback(e: React.FormEvent) {
    e.preventDefault();
    if (!result || fbBusy || !fbReasons.length) return;
    setFbBusy(true); setFbStatus(null);
    try {
      await request("/api/ask/feedback", {
        method: "POST",
        body: JSON.stringify({
          conversation_id: result.conversation_id || "",
          message_id: result.message_id || "",
          question_text: result.question,
          answer_text: result.answer,
          reason_codes: fbReasons,
          comment: fbComment,
          response_summary: {
            source: result.source,
            intent: result.intent,
            parse: result.parse,
            sql: result.sql,
            main_total: result.main_total,
            ontology_version: result.ontology_version,
          },
        }),
      });
      setFbStatus({ ok: true, msg: "感谢反馈，已进入后台「不准反馈」处置列表" });
      setFbOpen(false); setFbComment("");
    } catch (err) {
      setFbStatus({ ok: false, msg: err instanceof Error ? err.message : "反馈提交失败" });
    } finally { setFbBusy(false); }
  }
  if (result && result.intent === "notice") {
    const noticeMeta = result.outcome === "clarification_needed"
      ? { title: "请确认查询条件", tone: "warning", Icon: HelpCircle }
      : result.outcome === "out_of_coverage"
        ? { title: "超出数据覆盖范围", tone: "warning", Icon: CalendarRange }
        : result.outcome === "unsupported"
          ? { title: "暂时无法回答", tone: "warning", Icon: AlertTriangle }
          : { title: "未找到匹配记录", tone: "muted", Icon: SearchX };
    const NoticeIcon = noticeMeta.Icon;
    return <Card className="card-pad">
      <div className={s.resultNotice} data-tone={noticeMeta.tone} role="status">
        <span className={s.tabIcon}><NoticeIcon size={18} aria-hidden="true" /></span>
        <div><h2>{noticeMeta.title}</h2><p>{displayAnswer(result)}</p></div>
      </div>
      {!!result.clarification?.options.length && <div role="group" aria-label="待确认选项" style={{ display: "flex", flexWrap: "wrap", gap: 10, marginBlock: 14 }}>{result.clarification.options.map(choice => <Button key={choice.id} variant="primary" style={{ minHeight: 40 }} disabled={!onClarify || busy} onClick={() => onClarify?.(choice)}>{choice.label}</Button>)}</div>}
      {!!result.capability_gate?.alternatives?.length && <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBlock: 12 }}><span className="helper" style={{ alignSelf: "center" }}>建议改查：</span>{result.capability_gate.alternatives.map(alt => onQuestion ? <Button key={alt} variant="ghost" disabled={busy} onClick={() => onQuestion(alt)}>{alt}</Button> : <Badge key={alt}>{alt}</Badge>)}</div>}
      {result.outcome === "clarification_needed" && <p className="helper">{onClarify ? "请选择以上选项，或在输入框补充条件；也可以输入一个新的完整问题。" : "这是历史澄清记录，请回到问答页打开本会话继续确认。"}</p>}
      {steps.length > 0 && <ExecutionProcess steps={steps} elapsed={elapsed} firstOutputElapsed={firstOutputElapsed} question={result.question} />}
    </Card>;
  }
  const resultHeader = <div className={`result-top ${s.resultHeader} ${focused ? s.focusedHeader : ""}`}>
    {!run && <h2 className={s.resultQuestion}>{result?.question}</h2>}
    <div className="result-top-actions">
      {result && tab !== "process" && (elapsed > 0 || steps.length > 0) && <QueryTimings total={queryElapsed(steps, false, elapsed)} firstOutputElapsed={firstOutputElapsed} compact />}
      {busy && run && <Button onClick={run.onStop} variant="ghost"><X size={12} aria-hidden="true" />停止等待</Button>}
      {result && <Button variant="ghost" onClick={() => setFbOpen(true)}><Flag size={12} aria-hidden="true" />反馈不准</Button>}
      {result && <CopyButton text={answerCopyText(result)} label="复制回答" />}
    </div>
  </div>;
  const resultTabs = <div className={`result-tabs ${s.resultTabs}`} role="tablist" aria-label="查询结果内容" onKeyDown={event => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const available = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>(":scope > button:not(:disabled)"));
    const current = available.indexOf(event.target as HTMLButtonElement);
    if (current < 0) return;
    const next = event.key === "Home" ? 0 : event.key === "End" ? available.length - 1
      : (current + (event.key === "ArrowLeft" ? -1 : 1) + available.length) % available.length;
    event.preventDefault();
    available[next].focus();
    available[next].click();
  }}>{tabs.filter(item => result || item.id === "process" || (item.id === "answer" && delta)).map(item => <button
    type="button" key={item.id} id={`${panelId}-tab-${item.id}`} className={`result-tab ${tab === item.id ? "selected" : ""}`} data-tone={item.tone}
    role="tab" aria-selected={tab === item.id} aria-controls={panelId} tabIndex={tab === item.id ? 0 : -1}
    onClick={() => { if (item.id === "evidence") setClaimIndex(null); setTab(item.id); }}>
    <span className={s.tabIcon}><item.icon size={15} aria-hidden="true" /></span>{item.label}{item.id === "process" && busy && <Spinner />}
  </button>)}</div>;
  return <>{!focused && resultHeader}{fbStatus && <Notice error={!fbStatus.ok}>{fbStatus.msg}</Notice>}<Card className={`result-card ${focused ? s.focusedCard : ""}`}>{!focused && resultTabs}<div className="result-content" role="tabpanel" id={panelId} aria-labelledby={`${panelId}-tab-${tab}`}>
    {run?.error && <Notice error>{run.error} <button className="inline-button" onClick={run.onRetry} disabled={busy}>重新查询<RefreshCw size={11} aria-hidden="true" /></button></Notice>}
    {tab === "process" && <div className={s.processPane}>
      <ExecutionProcess steps={steps} question={run?.question || result?.question} busy={busy} elapsed={elapsed} firstOutputElapsed={firstOutputElapsed} inline />
      {result && (result.sql || result.parse.conditions.length > 0 || result.plan_template || Boolean(result.applied_rules?.length)) && <details className={s.technicalDrawer}>
        <summary><Code2 size={16} aria-hidden="true" />查询技术详情<ChevronRight size={14} aria-hidden="true" /></summary>
        <div className={s.drawerBody}>
          {result.plan_template && <p className="helper">查询模板 · {result.plan_template.name || result.plan_template.code}</p>}
          {!!result.applied_rules?.length && <ul className={s.rules}>{result.applied_rules.map(rule => <li key={rule.code} title={rule.description || rule.code}>{rule.name}（v{rule.versionNo || 1}）</li>)}</ul>}
          {result.sql && <><div className="sql-top"><h3 className="section-title">查询语句（SQL）</h3><CopyButton text={result.sql} label="复制 SQL" /></div><pre className="code-block">{result.sql}</pre></>}
          {!!result.sql_params?.length && <><h3 className="section-title">绑定参数</h3><pre className="code-block">{JSON.stringify(result.sql_params, null, 2)}</pre></>}
          {!!result.parse.conditions.length && <><h3 className="section-title">查询条件</h3><DataRows rows={result.parse.conditions.map(c => ({ 字段: c.col, 所属表: c.table || result.parse.subject, 运算符: c.op, 值: c.value }))} table="conditions" meta={{}} /></>}
        </div>
      </details>}
    </div>}
    {tab === "answer" && !result && liveAnswer.visible && <div aria-live="polite" aria-atomic={false}><div className="answer-head"><span className="answer-mark"><Sparkles size={16} aria-hidden="true" /></span><strong>{liveAnswer.title}</strong><Badge tone={busy ? "green" : "amber"}>{busy ? "实时追加" : "未完整完成"}</Badge></div><div className={`answer-text${busy ? " live" : ""}`}>{delta}</div><p className="helper">{liveAnswer.note}</p></div>}
    {tab === "answer" && result && <div className={s.resultAnswer}>
      {(result.sections || result.forecast || result.is_simulated) && <AnalysisSummary result={result} />}
      {((result.sections?.length || result.forecast) && !visual) ? (
        <details className={s.analysisEvidenceDrawer}>
          <summary>
            <span>完整回答</span>
            <ChevronRight size={15} aria-hidden="true" />
          </summary>
          <div className={s.drawerBody}>
            <AnswerBody result={result} onEvidence={showEvidence} />
            <SourceList sources={sources} />
          </div>
        </details>
      ) : (
        <div className={`${s.answerLayout} ${flight ? s.flightLayout : ""} ${turnaround ? s.turnaroundLayout : ""} ${focused ? s.focusedLayout : ""}`}>
          {visual && <div className={s.visualPrimary}><VisualView meta={visual} compact={focused} onQuestion={onQuestion} onOpenAllRecords={() => (result.drill_token ? setRecords(true) : setTab("data"))} /></div>}
          {focused ? <details className={s.focusedNotes}><summary>查询说明</summary><AnswerBody result={result} onEvidence={showEvidence} />{turnaroundSummary && turnaroundSummary.incomplete > 0 && <p className={s.recordAlert}>{turnaroundSummary.detail}</p>}</details> : <aside className={`${s.conclusionPanel} ${turnaround ? s.inspectionRail : ""}`}>
            {turnaround && <section className={s.objectContext}><div className={s.railHeading}><Boxes size={15} aria-hidden="true" /><h3>对象上下文</h3><span>本次命中</span></div><dl>{[["航班", turnaround.flight_pair], ["航空器", turnaround.craft_no], ["机位", turnaround.stand], ["登机口", turnaround.gate]].map(([label, value])=><div key={label}><dt>{label}</dt><dd>{value || "未提供"}</dd></div>)}</dl><button className={s.relationLink} onClick={()=>setTab("graph")}><GitBranch size={13} aria-hidden="true" />查询关系路径<span>{result.graph?.edges.length || 0} 条展开</span><ArrowUpRight size={12} aria-hidden="true" /></button></section>}
            <section className={turnaround ? s.answerRailCard : undefined}>
            <AnswerBody result={result} onEvidence={showEvidence} />{turnaroundSummary && turnaroundSummary.incomplete > 0 && <p className={s.recordAlert}>{turnaroundSummary.detail}</p>}
            <SourceList sources={sources} />
            </section></aside>}
        </div>
      )}
      {result.metric?.description && <p className={s.scopeNote}><ShieldCheck size={13} aria-hidden="true" />{result.metric.description}{result.metric.unit ? ` · 单位：${result.metric.unit}` : ""}</p>}
      {result.operational_window && <p className={s.scopeNote}><CalendarRange size={14} aria-hidden="true" />运营日范围 {result.operational_window.start} 至 {result.operational_window.end}</p>}
      {result.intent === "acdm_report" && result.related.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", marginTop: 10 }}>
          <span className="helper" style={{ fontSize: 12 }}>专题明细附表：</span>
          {result.related.map((item, i) => (
            <Button key={item.table} variant="ghost" title={item.table} onClick={() => setRelated(item)}>
              <Table2 size={14} aria-hidden="true" />{tableLabel(item.table, item.name, `附表 ${i + 1}`)} ({item.count.toLocaleString()})
            </Button>
          ))}
        </div>
      )}
      {!result.sections?.length && <div className={s.answerActions}><button onClick={()=>result.drill_token?setRecords(true):setTab("data")}><Table2 size={16} aria-hidden="true" />查看数据明细<span>{result.main_total.toLocaleString()}</span><ArrowUpRight size={12} aria-hidden="true" /></button>{relatedHits>0&&<button onClick={()=>setTab("data")}><Layers size={16} aria-hidden="true" />关联记录<span>{relatedHits.toLocaleString()}</span></button>}</div>}
      {(result.data_source === "ops_ask" || result.source?.startsWith("运营总控") || result.report_id) && result.message_id && result.conversation_id && <div className="data-toolbar"><Button disabled={pdfBusy || Boolean(pdf && ["queued", "running", "completed"].includes(pdf.status))} onClick={exportPdf}>{pdfBusy ? <Spinner /> : <FileCheck2 size={14} aria-hidden="true" />}{pdf?.status === "failed" ? "重试PDF" : "导出当前结果PDF"}</Button>{pdf && ["queued", "running"].includes(pdf.status) && <span className="helper">PDF后台生成中，可在报告中心查看</span>}{pdf?.status === "completed" && <a className="btn btn-primary" href={`/api/reports/${pdf.id}/download`}>下载PDF</a>}{(pdfError || pdf?.error) && <Notice error>{pdfError || pdf?.error}</Notice>}</div>}
    </div>}
    {tab === "data" && result && <><div className={`data-toolbar ${s.dataToolbar}`}>
      <span>{result.sections?.length ? `${result.sections.length} 项查询结果` : result.intent === "detail" ? `已显示 ${result.main_rows.length} / ${result.main_total.toLocaleString()} 条记录` : `${result.main_rows.length} 组统计结果${result.metric?.unit ? ` · 单位 ${result.metric.unit}` : ""}`}{!result.sections?.length && <MetadataHint label="查看数据表名" value={result.intent === "detail" ? result.parse.subject : target} />}</span>
      <div className="manager-tools">{result.intent === "detail" && <Button onClick={() => setCards(v => !v)}><Table2 size={14} aria-hidden="true" />{cards ? "表格视图" : "卡片视图"}</Button>}{result.drill_token && <Button onClick={() => setRecords(true)}><ArrowUpRight size={14} aria-hidden="true" />全部明细</Button>}</div>
    </div><DataRows rows={result.main_rows} table={result.intent === "detail" ? result.parse.subject : target} meta={result.field_meta} cards={cards && result.intent === "detail"} />
      {result.sections?.map(section => <details className={analysisStyle.part} key={section.id}><summary>{section.label}{section.is_simulated && " · 模拟测试数据"}</summary><div><ResultView result={section.result} onQuestion={onQuestion} compact /></div></details>)}
      {!!result.related.length && <><div className="card-head" style={{ marginTop: 24, marginBottom: 13 }}><h3 className="section-title"><Layers size={15} aria-hidden="true" />关联数据</h3></div>
        <div className="related-grid">{result.related.map((item, i) => <button type="button" className={`related-card ${s.relatedCard}`} key={item.table} title={item.table} onClick={() => setRelated(item)}>
          <span className="related-heading"><Database size={15} aria-hidden="true" /><strong>{tableLabel(item.table, item.name, `关联数据 ${i + 1}`)}</strong></span>
          <span className="related-card-bottom"><span>{item.count.toLocaleString()} 条记录</span><ArrowUpRight size={15} aria-hidden="true" /></span>
        </button>)}</div></>}
    </>}
    {tab === "evidence" && result && <Evidence result={result} claimIndex={claimIndex} />}
    {tab === "graph" && result && <Graph result={result} />}
  </div>{focused && resultTabs}</Card>{focused && resultHeader}
  {records && result && <RecordPage result={result} onClose={closeRecords} />}
  {related && result && (related.count > 0 && result.related_token ? <RecordPage result={result} related={related} onClose={closeRelated} /> : <Modal title={tableLabel(related.table, related.name, "关联明细")} onClose={closeRelated}>
    <div className="data-toolbar"><span>{related.count.toLocaleString()} 条记录</span><MetadataHint label="查看数据表名" value={related.table} /></div>
    <DataRows rows={related.rows} table={related.table} meta={result.field_meta} />
  </Modal>)}
  {fbOpen && result && <Modal title="反馈回答不准确" subtitle="描述问题，便于核对和处理" onClose={closeFb}>
    <form onSubmit={submitFeedback}>
      <p className="helper" style={{ marginBottom: 8 }}>请选择问题原因（可多选）：</p>
      <div className="gov-form-checks">
        {fbOptions.map(([code, label]) => (
          <label className="gov-check-pill" key={code}>
            <input type="checkbox" checked={fbReasons.includes(code)} onChange={e => setFbReasons(prev => e.target.checked ? [...prev, code] : prev.filter(x => x !== code))} />
            <span>{label}</span>
          </label>
        ))}
      </div>
      <label className="form-field" style={{ marginTop: 12 }}>补充说明（选填）
        <textarea rows={3} maxLength={2000} value={fbComment} onChange={e => setFbComment(e.target.value)} placeholder="请描述期望口径或哪部分数值、筛选有误…" />
      </label>
      <div className="modal-actions">
        <Button onClick={closeFb}>取消</Button>
        <Button type="submit" variant="primary" disabled={fbBusy || !fbReasons.length}>{fbBusy ? "提交中…" : "提交反馈"}</Button>
      </div>
    </form>
  </Modal>}
  </>;
}
