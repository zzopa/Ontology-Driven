"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, ArrowRight, ArrowUpRight, Boxes, CalendarRange, Check, ChevronLeft, ChevronRight, Code2, Database, Expand, FileCheck2, Flag, GitBranch, HelpCircle, Layers, ListChecks, RefreshCw, Search, SearchX, ShieldCheck, Sparkles, Table2, X } from "lucide-react";
import { request } from "@/lib/api";
import { formatValue } from "@/lib/query";
import { answerCopyText, answerSections } from "@/lib/answer-document";
import type { Edge, ExecutionStep, Fact, FieldMeta, PageData, QueryResult, Row, VisualMeta } from "@/lib/types";
import { liveAnswerState } from "@/lib/live-answer";
import { turnaroundRecordSummary } from "@/lib/turnaround-layout";
import { formatStepElapsed } from "@/lib/execution";
import { Badge, Button, Card, CopyButton, Modal, Notice, Spinner } from "./ui";
import { ExecutionProcess } from "./execution-process";
import { TurnaroundGantt } from "./turnaround-gantt";
import { FlightMilestones } from "./flight-milestones";
import { BarVisual, DetailTableVisual, DonutVisual, StatsVisual, TrendVisual } from "./visual-views";
import s from "./answer-layout.module.css";


function RichText({ text }: { text: string }) {
  // React escapes every segment; never render model/database text as HTML.
  return <>{text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, index) => part.startsWith("**") && part.endsWith("**") ? <strong key={index}>{part.slice(2, -2)}</strong> : part.startsWith("`") && part.endsWith("`") ? <code key={index}>{part.slice(1, -1)}</code> : <span key={index}>{part}</span>)}</>;
}

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

function AnswerBody({ result, onEvidence }: { result: QueryResult; onEvidence: () => void }) {
  const sections = answerSections(result);
  if (!sections) {
    const reportBlocks = result.intent === "acdm_report" && result.answer ? parseReportSections(result.answer) : null;
    if (reportBlocks) {
      return <>{reportBlocks.map((block, index) => (
        <section className={s.answerSection} data-kind={block.isBoundary ? "limitations" : "query_facts"} key={`${block.title}-${index}`}>
          <span className={s.sectionKicker}>{block.title}</span>
          <div className={s.answerText}>
            {block.lines.map((line, lineIdx) => <div className={s.claim} key={lineIdx}><RichText text={line} /></div>)}
          </div>
        </section>
      ))}</>;
    }
    const raw = result.answer || "当前未生成回答，请查看数据明细。";
    const paragraphs = raw.split(/\n+/).map(line => line.trim()).filter(Boolean);
    return <section className={s.answerSection}><span className={s.sectionKicker}>问询结论</span><div className={s.answerText}>{paragraphs.length > 1 ? paragraphs.map((line, i) => <div className={s.claim} key={i}><RichText text={line} /></div>) : <RichText text={raw} />}</div></section>;
  }
  return <>{sections.map(section => <section className={s.answerSection} data-kind={section.kind} key={section.kind}>
    <h3>{section.kind === "query_facts" ? "问询结论" : section.title}{section.kind === "interpretation" && <span>模型解释</span>}</h3>
    <div className={s.answerText}>{section.claims.map((claim, i) => <div className={s.claim} key={i}><RichText text={claim.text} />{claim.fact_ids.length > 0 && <button className={s.claimSource} onClick={onEvidence} aria-label={`查看${section.title}第 ${i + 1} 条的来源`}><FileCheck2 size={11} aria-hidden="true" />{claim.fact_ids.join(" · ")}</button>}</div>)}</div>
  </section>)}</>;
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

export function DataRows({ rows, table, meta, cards = false }: { rows: Row[]; table: string; meta: FieldMeta; cards?: boolean }) {
  const [cell, setCell] = useState<Cell | null>(null);
  const close = useCallback(() => setCell(null), []);
  const fields = meta[table] || {}, columns = Array.from(new Set(rows.flatMap(row => Object.keys(row))));
  const select = (key: string, value: unknown) => setCell({ field: key, label: fields[key]?.label || key, value, unit: fields[key]?.unit, comment: fields[key]?.comment });
  if (!rows.length) return <div className="empty-panel"><Search size={20} /><span>当前条件下未找到记录</span><p>请检查企业名称、时间范围或记录编号，不会补充数据库中不存在的数据。</p></div>;
  return <>{cards ? <div className="record-cards">{rows.map((row, i) => <article className="record-card" key={i}><div className="record-head"><span>记录 {String(i + 1).padStart(2, "0")}</span><small>{Object.keys(row).length} 个允许展示字段</small></div><div className="field-grid">{Object.entries(row).map(([key, value]) => <button className="field-item" key={key} title={`${fields[key]?.label || key} · 点击查看完整内容`} onClick={() => select(key, value)}><span className="field-label">{fields[key]?.label || key}<Expand size={10} /></span><span className={`field-value ${value === null ? "is-null" : ""}`}>{formatValue(value)}{fields[key]?.unit && value !== null ? ` ${fields[key].unit}` : ""}</span></button>)}</div></article>)}</div> : <div className="table-wrap"><table className="data-table"><thead><tr><th>#</th>{columns.map(key => <th key={key} title={fields[key]?.comment || key}>{fields[key]?.label || key}{fields[key]?.unit && <span> · {fields[key].unit}</span>}{fields[key]?.label && fields[key].label !== key && <small>{key}</small>}</th>)}</tr></thead><tbody>{rows.map((row, i) => <tr key={i}><td className="row-index">{i + 1}</td>{columns.map(key => <td key={key}><button className="cell-button" title="查看完整字段内容" onClick={() => select(key, row[key])}>{formatValue(row[key])}</button></td>)}</tr>)}</tbody></table></div>}
    {cell && <Modal title={cell.label} subtitle={cell.field} onClose={close}><div className="value-meta">{cell.unit && <Badge>单位 · {cell.unit}</Badge>}{cell.comment && <span>{cell.comment}</span>}</div><pre className="code-block">{formatValue(cell.value)}</pre><div className="modal-actions"><CopyButton text={formatValue(cell.value)} label="复制完整内容" /></div></Modal>}
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
  return <Modal title={related ? `${related.name || related.table} · 关联明细` : "命中记录明细"} subtitle={`${table} · 统计覆盖全部匹配记录，分页仅用于展示`} wide onClose={onClose}>
    <div className="data-toolbar"><span>共 {total.toLocaleString()} 条记录 · 每页 {size} 条</span><Badge tone="green">全部允许字段</Badge></div>
    {loading ? <div className="empty-panel"><Spinner /><span>正在加载第 {page} 页…</span></div> : error ? <Notice error>{error}</Notice> : data && <DataRows rows={data.rows} table={data.subject || data.table || table} meta={data.field_meta} />}
    <div className="pagination"><span>{total && data && !loading ? `显示 ${(shownPage - 1) * size + 1}–${Math.min(total, shownPage * size)} 条` : ""}</span><div className="pagination-controls"><Button aria-label="上一页" disabled={page <= 1 || loading} onClick={() => setPage(p => p - 1)}><ChevronLeft size={14} />上一页</Button><span>{page} / {pages}</span><Button aria-label="下一页" disabled={page >= pages || loading} onClick={() => setPage(p => p + 1)}>下一页<ChevronRight size={14} /></Button></div></div>
  </Modal>;
}

function Graph({ result }: { result: QueryResult }) {
  const [active, setActive] = useState<string | null>(null);
  const nodes = result.graph?.nodes || [], edges = result.graph?.edges || [];
  const levels = Math.max(1, ...nodes.map(n => n.level));
  const graphHeight = Math.max(350, ...Array.from({ length: levels + 1 }, (_, level) => nodes.filter(n => n.level === level).length * 65 + 45));
  const graphWidth = Math.max(480, (levels + 1) * 180);
  const positions = useMemo(() => Object.fromEntries(nodes.map(node => {
    const group = nodes.filter(n => n.level === node.level), index = group.findIndex(n => n.id === node.id);
    return [node.id, { x: 90 + node.level * 180, y: graphHeight * (index + 1) / (group.length + 1) }];
  })), [nodes, graphHeight]);
  const tableName = (table: string) => result.evidence?.facts?.find(fact => fact.table === table)?.name || result.related.find(item => item.table === table)?.name || table.split(".").pop() || "未提供表名";
  if (!edges.length) return <>
    <div className="data-toolbar"><span>{nodes.length} 个数据表 · 0 条展开关系</span><Badge>本次查询范围</Badge></div>
    <section className={s.graphSummary}><Database size={24} aria-hidden="true" /><div><h3>{nodes.length === 1 ? "本次返回一张数据表" : "本次未返回表级关联路径"}</h3><p>{nodes.length === 1 ? "当前图谱未展开跨表关系，以下展示已返回的数据表。" : "记录来源可在数据明细和回答依据中核验。"}</p></div></section>
    <div className={s.graphTables}>{nodes.map(node => <div className={s.graphTable} key={node.id}><Database size={17} aria-hidden="true" /><div><strong>{tableName(node.id)}</strong><code>{node.id || "未提供表名"}</code></div><Badge>{node.main ? "主表" : "关联表"}</Badge></div>)}</div>
    <Notice>关联图谱展示本次返回的表级路径，记录来源可在回答依据中核验。</Notice>
  </>;
  return <><div className="data-toolbar"><span>{nodes.length} 个数据表 · {edges.length} 条展开关系</span><Badge>结构关系，不等于事实已命中</Badge></div><div className="graph-layout"><div className="graph-canvas" style={{ maxHeight: 420 }}><svg viewBox={`0 0 ${graphWidth} ${graphHeight}`} style={{ height: graphHeight, minWidth: graphWidth }} role="img" aria-label="查询数据表关联图"><defs><marker id="graph-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0L10 5L0 10Z" fill="#5c6b64" /></marker></defs>{edges.map((edge, i) => { const s = positions[edge.source], t = positions[edge.target]; return s && t ? <line key={i} x1={s.x} y1={s.y} x2={t.x} y2={t.y} className="graph-edge" markerEnd="url(#graph-arrow)" opacity={active && active !== edge.source && active !== edge.target ? .15 : 1} /> : null; })}{nodes.map(node => { const p = positions[node.id], name = tableName(node.id); return <g key={node.id} className={`graph-node ${node.main ? "main" : ""}`} transform={`translate(${p.x},${p.y})`} role="button" tabIndex={0} aria-label={node.id} onClick={() => setActive(active === node.id ? null : node.id)} onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setActive(active === node.id ? null : node.id); } }}><title>{node.id}</title><rect x="-70" y="-20" width="140" height="40" rx="8" /><text textAnchor="middle" y="3">{name.length > 22 ? name.slice(0, 21) + "…" : name}</text></g>; })}</svg></div><div className="edge-list">{edges.length ? edges.map((edge, i) => <button className={`edge-card ${active === edge.source || active === edge.target ? "active" : ""}`} key={i} onClick={() => setActive(edge.source)}><span>{edge.source}.{edge.fcol}</span><ArrowRight size={10} style={{ display: "block", margin: "3px 0", color: "#6ee7b7" }} /><span>{edge.target}.{edge.tcol}</span><small>{edge.note || "配置关联"} · {edge.type === "fk" ? "数据库外键" : "逻辑关系"}</small></button>) : <div className="empty-panel"><GitBranch size={20} /><span>本次查询未展开关联关系</span></div>}</div></div><Notice>这里展示本次查询使用的表级路径。记录之间的真实匹配与来源信息请到“回答依据”核验。</Notice></>;
}

function Evidence({ result }: { result: QueryResult }) {
  const facts = result.evidence?.facts || [], index = Object.fromEntries(facts.map(fact => [fact.id, fact]));
  const claims = result.claims || [];
  const factRows = (fact: Fact) => fact.values || Object.fromEntries(Object.entries(fact.properties || {}).map(([key, value]) => [key, value.value]));
  return <><div className="data-toolbar"><span>{claims.length} 条结论 · {facts.length} 条已提供事实</span><Badge><FileCheck2 size={10} />来源与数值检查</Badge></div>{claims.length ? claims.map((claim, i) => <details className="source-claim" key={i}><summary><FileCheck2 size={14} /><span>结论 {String(i + 1).padStart(2, "0")}</span><Badge>{claim.fact_ids.length} 条引用</Badge><ChevronRight size={12} /></summary><div className="answer-text" style={{ marginTop: 12 }}><RichText text={claim.text} /></div>{claim.fact_ids.map(id => { const fact = index[id]; return fact && <div className="source-fact" key={id}><div className="source-origin"><Badge tone="green">{fact.id}</Badge><strong>{fact.name}</strong><span>{fact.source.database}.{fact.source.table}</span><span className="source-key">{fact.source.record_key ? JSON.stringify(fact.source.record_key) : fact.source.scope}</span><span>业务时间 {formatValue(fact.business_time)}</span><span>查询时间 {fact.source.queried_at}</span></div><DataRows rows={[factRows(fact)]} table={fact.table} meta={result.field_meta} /></div>; })}</details>) : <div className="empty-panel"><FileCheck2 size={20} /><span>本次没有可展开的结论引用</span></div>}
    {!!result.evidence?.coverage?.length && <><h3 className="section-title" style={{ marginTop: 20 }}>证据覆盖范围</h3><div className="coverage-table">{result.evidence.coverage.map(item => <div className="coverage-row" key={item.table}><span title={item.table}>{item.name}</span><span>提供 {item.provided} / 共 {item.total.toLocaleString()} 条 <Badge tone={item.complete ? "green" : "amber"}>{item.complete ? "完整" : "部分证据"}</Badge></span></div>)}</div></>}
  </>;
}

type QueryRun = {
  question: string; busy: boolean; steps: ExecutionStep[]; elapsed: number;
  progress: { message: string; percent: number }; delta: string; error: string;
  onStop: () => void; onRetry: () => void;
};

export function ResultView({ result, run, onQuestion, onClarify, compact = false }: { result: QueryResult | null; run?: QueryRun; onQuestion?: (question: string) => void; onClarify?: (choice: { id: string; label: string }) => void; compact?: boolean }) {
  const busy = run?.busy || false, delta = run?.delta || "";
  const [tab, setTab] = useState(result ? "answer" : "process"), [records, setRecords] = useState(false), [related, setRelated] = useState<QueryResult["related"][number] | null>(null), [cards, setCards] = useState(true);
  const [fbOpen, setFbOpen] = useState(false), [fbReasons, setFbReasons] = useState<string[]>(["WRONG_DATA"]), [fbComment, setFbComment] = useState(""), [fbBusy, setFbBusy] = useState(false), [fbStatus, setFbStatus] = useState<{ ok: boolean; msg: string } | null>(null);
  const closeRecords = useCallback(() => setRecords(false), []), closeRelated = useCallback(() => setRelated(null), []), closeFb = useCallback(() => setFbOpen(false), []);
  useEffect(() => { setTab(result ? "answer" : "process"); setRecords(false); setRelated(null); setFbOpen(false); setFbStatus(null); }, [result, busy]);
  const target = result?.parse.aggregate_target || result?.parse.subject || "";
  const relatedHits = result?.related.reduce((sum, item) => sum + item.count, 0) || 0;
  const visual = result?.visual_meta && result.visual_meta.kind !== "none" ? result.visual_meta : null;
  const flight = visual?.kind === "fishbone";
  const turnaround = visual?.kind === "turnaround_gantt" ? visual : null;
  const focused = compact && Boolean(turnaround);
  const turnaroundSummary = turnaround ? turnaroundRecordSummary(turnaround.operations) : null;
  const sources = Array.from(new Map((result?.evidence?.facts || []).map(fact => [fact.table, { name: fact.name, table: fact.table }])).values());
  const liveAnswer = liveAnswerState(delta, busy, Boolean(result));
  const tabs = [{ id: "process", label: "执行过程", icon: ListChecks }, { id: "answer", label: "智能回答", icon: Sparkles }, { id: "data", label: "数据明细", icon: Table2 }, { id: "evidence", label: "回答依据", icon: ShieldCheck }, { id: "graph", label: "关联图谱", icon: GitBranch }, { id: "sql", label: "执行 SQL", icon: Code2 }];
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
    const isNoMatch = result.outcome === "unsupported" && Boolean(
      result.answer?.includes("回复回复不了") || result.answer?.includes("未匹配到业务本体对照")
    );
    const noticeMeta = result.outcome === "clarification_needed"
      ? { title: "请确认查询条件", badge: "待补充条件", tone: "amber" as const, Icon: HelpCircle }
      : result.outcome === "out_of_coverage"
        ? { title: "本地数据范围", badge: "超出范围", tone: "amber" as const, Icon: CalendarRange }
        : isNoMatch
          ? { title: "暂时无法回答", badge: "暂不支持", tone: "amber" as const, Icon: AlertTriangle }
          : result.outcome === "unsupported"
            ? { title: "当前查询能力", badge: "能力边界", tone: undefined, Icon: AlertTriangle }
            : { title: "问询结果", badge: "未命中记录", tone: undefined, Icon: SearchX };
    const NoticeIcon = noticeMeta.Icon;
    return <Card className="card-pad">
      <div className="card-head">
        <h2 className="section-title"><NoticeIcon size={15} aria-hidden="true" />{noticeMeta.title}</h2>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Badge tone={noticeMeta.tone}>{noticeMeta.badge}</Badge>
          <Badge>{formatStepElapsed(result.elapsed)}</Badge>
        </div>
      </div>
      <p className="helper" style={{ marginBottom: 10 }}>原问题：{result.question}</p>
      <div role="status" aria-live="polite" style={{ padding: "14px 16px", borderRadius: 12, background: "var(--surface-2)", border: "1px solid var(--line)", marginBottom: 12 }}>
        <p className="long-description" style={{ fontSize: 15, lineHeight: 1.7, color: "var(--text)", margin: 0 }}>{result.answer}</p>
      </div>
      {!!result.clarification?.options.length && <div role="group" aria-label="待确认选项" style={{ display: "flex", flexWrap: "wrap", gap: 10, marginBlock: 14 }}>{result.clarification.options.map(choice => <Button key={choice.id} variant="primary" style={{ minHeight: 40 }} disabled={!onClarify || busy} onClick={() => onClarify?.(choice)}>{choice.label}</Button>)}</div>}
      {!!result.capability_gate?.alternatives?.length && <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBlock: 12 }}><span className="helper" style={{ alignSelf: "center" }}>建议改查：</span>{result.capability_gate.alternatives.map(alt => onQuestion ? <Button key={alt} variant="ghost" disabled={busy} onClick={() => onQuestion(alt)}>{alt}</Button> : <Badge key={alt}>{alt}</Badge>)}</div>}
      {result.outcome === "clarification_needed" && <p className="helper">{onClarify ? "请选择以上选项，或在输入框补充条件；也可以输入一个新的完整问题。" : "这是历史澄清记录，请回到问答页打开本会话继续确认。"}</p>}
      <ExecutionProcess steps={result.execution_trace || []} inline />
    </Card>;
  }
  const resultHeader = <div className={`result-top ${focused ? s.focusedHeader : ""}`}><div><h2>查询结果</h2><p>{result?.question || run?.question}</p></div><div className="result-top-actions"><Badge>{formatStepElapsed(result?.elapsed ?? run?.elapsed ?? 0)}</Badge>{busy && run && <Button onClick={run.onStop} variant="ghost"><X size={12} aria-hidden="true" />停止等待</Button>}{result && <Button variant="ghost" onClick={() => setFbOpen(true)}><Flag size={12} aria-hidden="true" />反馈不准</Button>}{result && <CopyButton text={answerCopyText(result)} label="复制回答" />}</div></div>;
  const resultTabs = <div className="result-tabs" role="tablist" aria-label="查询结果内容">{tabs.map(item => <button key={item.id} id={`tab-${item.id}`} className={`result-tab ${tab === item.id ? "selected" : ""}`} role="tab" aria-selected={tab === item.id} aria-controls="result-panel" disabled={!result && item.id !== "process" && !(item.id === "answer" && delta)} onClick={() => setTab(item.id)}><item.icon size={13} aria-hidden="true" />{item.label}{item.id === "process" && busy && <Spinner />}{item.id === "evidence" && <Badge>{result?.claims?.length || 0}</Badge>}</button>)}</div>;
  return <>{!focused && resultHeader}{fbStatus && <Notice error={!fbStatus.ok}>{fbStatus.msg}</Notice>}<Card className={`result-card ${focused ? s.focusedCard : ""}`}>{!focused && resultTabs}<div className="result-content" role="tabpanel" id="result-panel" aria-labelledby={`tab-${tab}`}>
    {run?.error && <Notice error>{run.error} <button className="inline-button" onClick={run.onRetry} disabled={busy}>重新查询<RefreshCw size={11} aria-hidden="true" /></button></Notice>}
    {tab === "process" && <div className={s.processPane}>
      {busy && run && <div className="progress-card"><div className="progress-top"><Spinner /><span>{run.progress.message}</span><time>{run.elapsed.toFixed(1)} s</time></div><div className="progress-track" role="progressbar" aria-label="查询处理进度" aria-valuenow={run.progress.percent} aria-valuemin={0} aria-valuemax={100}><div className="progress-fill" style={{width:`${run.progress.percent}%`}} /></div></div>}
      <ExecutionProcess steps={run?.steps || result?.execution_trace || []} busy={busy} elapsed={run?.elapsed || 0} inline />
    </div>}
    {tab === "answer" && !result && liveAnswer.visible && <div aria-live="polite" aria-atomic={false}><div className="answer-head"><span className="answer-mark"><Sparkles size={16} aria-hidden="true" /></span><strong>{liveAnswer.title}</strong><Badge tone={busy ? "green" : "amber"}>{busy ? "实时追加" : "未完整完成"}</Badge></div><div className={`answer-text${busy ? " live" : ""}`}>{delta}</div><p className="helper">{liveAnswer.note}</p></div>}
    {tab === "answer" && result && <div className={s.resultAnswer}>
      {!focused && <div className={s.answerStatus}><span><Check size={13} aria-hidden="true" />查询已完成</span><span>{formatStepElapsed(result.elapsed)}</span></div>}
      <div className={`${s.answerLayout} ${flight ? s.flightLayout : ""} ${turnaround ? s.turnaroundLayout : ""} ${focused ? s.focusedLayout : ""}`}>
        {visual && <div className={s.visualPrimary}><VisualView meta={visual} compact={focused} onQuestion={onQuestion} onOpenAllRecords={() => (result.drill_token ? setRecords(true) : setTab("data"))} /></div>}
        {focused ? <details className={s.focusedNotes}><summary>查询说明</summary><AnswerBody result={result} onEvidence={() => setTab("evidence")} />{turnaroundSummary && turnaroundSummary.incomplete > 0 && <p className={s.recordAlert}>{turnaroundSummary.detail}</p>}</details> : <aside className={`${s.conclusionPanel} ${turnaround ? s.inspectionRail : ""}`}>
          {turnaround && <section className={s.objectContext}><div className={s.railHeading}><Boxes size={15} aria-hidden="true" /><h3>对象上下文</h3><span>本次命中</span></div><dl>{[["航班", turnaround.flight_pair], ["航空器", turnaround.craft_no], ["机位", turnaround.stand], ["登机口", turnaround.gate]].map(([label, value])=><div key={label}><dt>{label}</dt><dd>{value || "未提供"}</dd></div>)}</dl><button className={s.relationLink} onClick={()=>setTab("graph")}><GitBranch size={13} aria-hidden="true" />查询关系路径<span>{result.graph?.edges.length || 0} 条展开</span><ArrowUpRight size={12} aria-hidden="true" /></button></section>}
          <section className={turnaround ? s.answerRailCard : undefined}>
          <div className={s.conclusionTitle}><Sparkles size={18} aria-hidden="true" /><strong>问询回答</strong><button onClick={()=>setTab("evidence")}><ShieldCheck size={13} aria-hidden="true" />依据</button></div><div className={s.conclusionGrid}><div><AnswerBody result={result} onEvidence={()=>setTab("evidence")} />{turnaroundSummary && turnaroundSummary.incomplete > 0 && <p className={s.recordAlert}>{turnaroundSummary.detail}</p>}</div>
          {sources.length > 0 && <section className={s.sourceList}><span className={s.sectionKicker}>本次使用的来源</span>{sources.map(source=><button key={source.table} onClick={()=>setTab("evidence")}><Database size={13} aria-hidden="true" /><span>{source.name}<small>{source.table}</small></span><ArrowUpRight size={12} aria-hidden="true" /></button>)}</section>}
        </div></section></aside>}
      </div>
      {result.metric?.description && <p className={s.scopeNote}><ShieldCheck size={13} aria-hidden="true" />{result.metric.description}{result.metric.unit ? ` · 单位：${result.metric.unit}` : ""}</p>}
      {(Boolean(result.applied_rules?.length) || result.operational_window || result.plan_template) && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", marginTop: 8 }}>
          {result.operational_window && <Badge tone="green">运营日窗口：{result.operational_window.start} ~ {result.operational_window.end}</Badge>}
          {result.plan_template && <Badge>规划模板：{result.plan_template.name || result.plan_template.code}</Badge>}
          {result.applied_rules?.map(r => (
            <span key={r.code} title={r.description || r.code}><Badge>规则校验：{r.name} (v{r.versionNo || 1})</Badge></span>
          ))}
        </div>
      )}
      {result.intent === "acdm_report" && result.related.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", marginTop: 10 }}>
          <span className="helper" style={{ fontSize: 12 }}>专题明细附表：</span>
          {result.related.map(item => (
            <Button key={item.table} variant="ghost" onClick={() => setRelated(item)}>
              <Table2 size={12} aria-hidden="true" />{item.name || item.table} ({item.count.toLocaleString()})
            </Button>
          ))}
        </div>
      )}
      <div className={s.answerActions}><button onClick={()=>result.drill_token?setRecords(true):setTab("data")}><Table2 size={15} aria-hidden="true" />查看数据明细<span>{result.main_total.toLocaleString()}</span><ArrowUpRight size={12} aria-hidden="true" /></button>{relatedHits>0&&<button onClick={()=>setTab("data")}><Layers size={15} aria-hidden="true" />关联记录<span>{relatedHits.toLocaleString()}</span></button>}<button onClick={()=>setTab("evidence")}><ShieldCheck size={15} aria-hidden="true" />来源与统计口径<ArrowUpRight size={12} aria-hidden="true" /></button></div>
    </div>}
    {tab === "data" && result && <><div className="data-toolbar"><span>{result.intent === "detail" ? `主表预览 ${result.main_rows.length} / ${result.main_total.toLocaleString()} 条 · 展示全部允许字段` : `统计分组 ${result.main_rows.length} 组 · ${result.metric?.unit || "记录数"}`}</span><div className="manager-tools">{result.intent === "detail" && <Button onClick={() => setCards(v => !v)}><Table2 size={12} />{cards ? "表格视图" : "卡片视图"}</Button>}{result.drill_token && <Button onClick={() => setRecords(true)}><ArrowUpRight size={12} />全部明细</Button>}</div></div><DataRows rows={result.main_rows} table={result.intent === "detail" ? result.parse.subject : target} meta={result.field_meta} cards={cards && result.intent === "detail"} />
      {!!result.related.length && <><div className="card-head" style={{ marginTop: 24, marginBottom: 13 }}><h3 className="section-title"><Layers size={13} />关联业务数据</h3><Badge>{result.related.length} 张关联表</Badge></div><div className="related-grid">{result.related.map(item => <button className="related-card" key={item.table} onClick={() => setRelated(item)}><span className="related-heading"><GitBranch size={13} /><strong>{item.name || item.table}</strong><Badge>{item.type === "fk" ? "外键" : "逻辑关联"}</Badge></span><small>{item.table}</small><p>{item.note || "依据已配置路径查询"}</p><span className="related-card-bottom"><span><b>{item.count.toLocaleString()}</b> 条关联记录</span><span>查看完整字段 <ArrowUpRight size={11} style={{ display: "inline" }} /></span></span></button>)}</div></>}
    </>}
    {tab === "evidence" && result && <Evidence result={result} />}
    {tab === "graph" && result && <Graph result={result} />}
    {tab === "sql" && result && <><div className="sql-top"><h3 className="section-title"><Code2 size={14} />执行 SQL</h3><CopyButton text={result.sql} label="复制 SQL" /></div><pre className="code-block">{result.sql}</pre><h3 className="section-title" style={{ margin: "20px 0 12px" }}>绑定参数</h3><pre className="code-block">{JSON.stringify(result.sql_params || [], null, 2)}</pre><h3 className="section-title" style={{ margin: "20px 0 12px" }}>查询条件</h3><DataRows rows={result.parse.conditions.map(c => ({ 字段: c.col, 所属表: c.table || result.parse.subject, 运算符: c.op, 值: c.value }))} table="conditions" meta={{}} /></>}
  </div>{focused && resultTabs}</Card>{focused && resultHeader}
  {records && result && <RecordPage result={result} onClose={closeRecords} />}
  {related && result && (related.count > 0 && result.related_token ? <RecordPage result={result} related={related} onClose={closeRelated} /> : <Modal title={related.name || related.table} subtitle={related.table} onClose={closeRelated}><DataRows rows={related.rows} table={related.table} meta={result.field_meta} cards /></Modal>)}
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
