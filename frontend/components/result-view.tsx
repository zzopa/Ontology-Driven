"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowRight, ArrowUpRight, Braces, Check, ChevronLeft, ChevronRight, Clock3, Code2, Database, Expand, FileCheck2, GitBranch, Layers, List, Search, ShieldCheck, Sparkles, Table2 } from "lucide-react";
import { request } from "@/lib/api";
import { formatValue } from "@/lib/query";
import { answerCopyText, answerSections } from "@/lib/answer-document";
import type { Edge, Fact, FieldMeta, PageData, QueryResult, Row } from "@/lib/types";
import { Badge, Button, Card, CopyButton, Modal, Notice, Spinner } from "./ui";
import { ExecutionProcess } from "./execution-process";

function RichText({ text }: { text: string }) {
  // React escapes every segment; never render model/database text as HTML.
  return <>{text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, index) => part.startsWith("**") && part.endsWith("**") ? <strong key={index}>{part.slice(2, -2)}</strong> : part.startsWith("`") && part.endsWith("`") ? <code key={index}>{part.slice(1, -1)}</code> : <span key={index}>{part}</span>)}</>;
}

function AnswerBody({ result }: { result: QueryResult }) {
  const sections = answerSections(result);
  if (!sections) return <div className="answer-text"><RichText text={result.answer || "当前未生成回答，请查看数据明细。"} /></div>;
  return <>{sections.map(section => <section className="source-claim" key={section.kind} style={{ padding: 16 }}>
    <h3 className="section-title">{section.title}{section.kind === "interpretation" && <Badge>模型解释</Badge>}</h3>
    <div className="answer-text" style={{ marginTop: 10 }}>{section.claims.map((claim, i) => <div key={i} style={{ marginTop: i ? 10 : 0 }}><RichText text={claim.text} /></div>)}</div>
  </section>)}<p className="helper">原始字段和统计口径保留在数据明细中；解释与建议不等于数据库已证实事实，来源检查也不是完整语义证明。</p></>;
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
  return <><div className="data-toolbar"><span>{nodes.length} 个数据表 · {edges.length} 条展开关系</span><Badge>结构关系，不等于事实已命中</Badge></div><div className="graph-layout"><div className="graph-canvas" style={{ maxHeight: 420 }}><svg viewBox={`0 0 ${graphWidth} ${graphHeight}`} style={{ height: graphHeight, minWidth: graphWidth }} role="img" aria-label="查询数据表关联图"><defs><marker id="graph-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0L10 5L0 10Z" fill="#5c6b64" /></marker></defs>{edges.map((edge, i) => { const s = positions[edge.source], t = positions[edge.target]; return s && t ? <line key={i} x1={s.x} y1={s.y} x2={t.x} y2={t.y} className="graph-edge" markerEnd="url(#graph-arrow)" opacity={active && active !== edge.source && active !== edge.target ? .15 : 1} /> : null; })}{nodes.map(node => { const p = positions[node.id], name = node.id.split(".").pop() || node.id; return <g key={node.id} className={`graph-node ${node.main ? "main" : ""}`} transform={`translate(${p.x},${p.y})`} role="button" tabIndex={0} aria-label={node.id} onClick={() => setActive(active === node.id ? null : node.id)} onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setActive(active === node.id ? null : node.id); } }}><title>{node.id}</title><rect x="-70" y="-20" width="140" height="40" rx="8" /><text textAnchor="middle" y="3">{name.length > 22 ? name.slice(0, 21) + "…" : name}</text></g>; })}</svg></div><div className="edge-list">{edges.length ? edges.map((edge, i) => <button className={`edge-card ${active === edge.source || active === edge.target ? "active" : ""}`} key={i} onClick={() => setActive(edge.source)}><span>{edge.source}.{edge.fcol}</span><ArrowRight size={10} style={{ display: "block", margin: "3px 0", color: "#6ee7b7" }} /><span>{edge.target}.{edge.tcol}</span><small>{edge.note || "配置关联"} · {edge.type === "fk" ? "数据库外键" : "逻辑关系"}</small></button>) : <div className="empty-panel"><GitBranch size={20} /><span>本次查询未展开关联关系</span></div>}</div></div><Notice>这里展示本次查询使用的表级路径。记录之间的真实匹配与来源信息请到“回答依据”核验。</Notice></>;
}

function Evidence({ result }: { result: QueryResult }) {
  const facts = result.evidence?.facts || [], index = Object.fromEntries(facts.map(fact => [fact.id, fact]));
  const claims = result.claims || [];
  const factRows = (fact: Fact) => fact.values || Object.fromEntries(Object.entries(fact.properties || {}).map(([key, value]) => [key, value.value]));
  return <><div className="data-toolbar"><span>{claims.length} 条结论 · {facts.length} 条已提供事实</span><Badge><FileCheck2 size={10} />来源与数值检查</Badge></div>{claims.length ? claims.map((claim, i) => <details className="source-claim" key={i}><summary><FileCheck2 size={14} /><span>结论 {String(i + 1).padStart(2, "0")}</span><Badge>{claim.fact_ids.length} 条引用</Badge><ChevronRight size={12} /></summary><div className="answer-text" style={{ marginTop: 12 }}><RichText text={claim.text} /></div>{claim.fact_ids.map(id => { const fact = index[id]; return fact && <div className="source-fact" key={id}><div className="source-origin"><Badge tone="green">{fact.id}</Badge><strong>{fact.name}</strong><span>{fact.source.database}.{fact.source.table}</span><span className="source-key">{fact.source.record_key ? JSON.stringify(fact.source.record_key) : fact.source.scope}</span><span>业务时间 {formatValue(fact.business_time)}</span><span>查询时间 {fact.source.queried_at}</span></div><DataRows rows={[factRows(fact)]} table={fact.table} meta={result.field_meta} /></div>; })}</details>) : <div className="empty-panel"><FileCheck2 size={20} /><span>本次没有可展开的结论引用</span></div>}
    {!!result.evidence?.coverage?.length && <><h3 className="section-title" style={{ marginTop: 20 }}>证据覆盖范围</h3><div className="coverage-table">{result.evidence.coverage.map(item => <div className="coverage-row" key={item.table}><span title={item.table}>{item.name}</span><span>提供 {item.provided} / 共 {item.total.toLocaleString()} 条 <Badge tone={item.complete ? "green" : "amber"}>{item.complete ? "完整" : "部分证据"}</Badge></span></div>)}</div></>}
    <Notice>来源引用、数字和单位检查不是完整语义证明；部分证据不能代表全部记录的业务状态。</Notice>
  </>;
}

export function ResultView({ result, showProcess = true }: { result: QueryResult; showProcess?: boolean }) {
  const [tab, setTab] = useState("answer"), [records, setRecords] = useState(false), [related, setRelated] = useState<QueryResult["related"][number] | null>(null), [cards, setCards] = useState(true);
  const closeRecords = useCallback(() => setRecords(false), []), closeRelated = useCallback(() => setRelated(null), []);
  useEffect(() => { setTab("answer"); setRecords(false); setRelated(null); }, [result]);
  const target = result.parse.aggregate_target || result.parse.subject;
  const relatedHits = result.related.reduce((sum, item) => sum + item.count, 0);
  const tabs = [{ id: "answer", label: "智能回答", icon: Sparkles }, { id: "data", label: "数据明细", icon: Table2 }, { id: "evidence", label: "回答依据", icon: ShieldCheck }, { id: "graph", label: "关联图谱", icon: GitBranch }, { id: "sql", label: "执行 SQL", icon: Code2 }];
  return <>{showProcess && <ExecutionProcess steps={result.execution_trace || []} />}<div className="result-top"><div><h2>查询结果</h2><p>{result.question}</p></div><div className="result-top-actions"><Badge>{result.elapsed}s</Badge><CopyButton text={answerCopyText(result)} label="复制回答" /></div></div><Card className="result-card"><div className="result-tabs" role="tablist" aria-label="查询结果内容">{tabs.map(item => <button key={item.id} id={`tab-${item.id}`} className={`result-tab ${tab === item.id ? "selected" : ""}`} role="tab" aria-selected={tab === item.id} aria-controls="result-panel" onClick={() => setTab(item.id)}><item.icon size={13} />{item.label}{item.id === "evidence" && <Badge>{result.claims?.length || 0}</Badge>}</button>)}</div><div className="result-content" role="tabpanel" id="result-panel" aria-labelledby={`tab-${tab}`}>
    {tab === "answer" && <><div className="answer-head"><span className="answer-mark"><Sparkles size={17} /></span><div><strong>智能数据助手</strong><small>基于实时查询与来源证据</small></div><Badge tone="green"><Check size={10} />已完成查询</Badge></div><AnswerBody result={result} /><div className="result-meta"><span><Database size={11} />{result.parse.disp || result.parse.subject}</span><span><Clock3 size={11} />{result.elapsed}s</span><span><Code2 size={11} />{result.intent === "detail" ? "信息明细" : result.intent === "sum" ? "金额合计" : "记录统计"}</span><span><ShieldCheck size={11} />{result.answer_validation === "deterministic" ? "确定性统计" : result.answer_validation === "evidence_fallback" ? "事实摘要回退" : "来源及数值校验"}</span></div><div className="metrics"><button className="metric-card" disabled={!result.drill_token} onClick={() => setRecords(true)}><strong>{result.main_total.toLocaleString()}</strong><span className="metric-label">{result.intent === "sum" ? "参与记录数" : "命中记录数"}<ArrowUpRight size={13} /></span></button><div className="metric-card static"><strong>{relatedHits.toLocaleString()}</strong><span className="metric-label">关联记录数</span></div><button className="metric-card" onClick={() => setTab("graph")}><strong>{(result.graph?.nodes?.length || result.graph_tables.length + 1).toLocaleString()}</strong><span className="metric-label">查询涉及数据表<ArrowUpRight size={13} /></span></button></div>{result.metric?.description && <Notice>{result.metric.description}{result.metric.unit ? ` · 单位：${result.metric.unit}` : ""}</Notice>}{result.intent === "sum" && <div style={{ marginTop: 20 }}><DataRows rows={result.main_rows} table={target} meta={result.field_meta} /></div>}{result.main_total > 5 && result.intent === "detail" && <div className="quiet-note"><List size={12} /><span>统计和关联查询覆盖全部 {result.main_total.toLocaleString()} 条命中记录。模型依据已提供证据回答，完整明细可分页查看。</span></div>}</>}
    {tab === "data" && <><div className="data-toolbar"><span>{result.intent === "detail" ? `主表预览 ${result.main_rows.length} / ${result.main_total.toLocaleString()} 条 · 展示全部允许字段` : `统计分组 ${result.main_rows.length} 组 · ${result.metric?.unit || "记录数"}`}</span><div className="manager-tools">{result.intent === "detail" && <Button onClick={() => setCards(v => !v)}><Table2 size={12} />{cards ? "表格视图" : "卡片视图"}</Button>}{result.drill_token && <Button onClick={() => setRecords(true)}><ArrowUpRight size={12} />全部明细</Button>}</div></div><DataRows rows={result.main_rows} table={result.intent === "detail" ? result.parse.subject : target} meta={result.field_meta} cards={cards && result.intent === "detail"} />
      {!!result.related.length && <><div className="card-head" style={{ marginTop: 24, marginBottom: 13 }}><h3 className="section-title"><Layers size={13} />关联业务数据</h3><Badge>{result.related.length} 张关联表</Badge></div><div className="related-grid">{result.related.map(item => <button className="related-card" key={item.table} onClick={() => setRelated(item)}><span className="related-heading"><GitBranch size={13} /><strong>{item.name || item.table}</strong><Badge>{item.type === "fk" ? "外键" : "逻辑关联"}</Badge></span><small>{item.table}</small><p>{item.note || "依据已配置路径查询"}</p><span className="related-card-bottom"><span><b>{item.count.toLocaleString()}</b> 条关联记录</span><span>查看完整字段 <ArrowUpRight size={11} style={{ display: "inline" }} /></span></span></button>)}</div></>}
    </>}
    {tab === "evidence" && <Evidence result={result} />}
    {tab === "graph" && <Graph result={result} />}
    {tab === "sql" && <><div className="sql-top"><h3 className="section-title"><Code2 size={14} />实际执行的参数化 SQL</h3><CopyButton text={result.sql} label="复制 SQL" /></div><pre className="code-block">{result.sql}</pre><h3 className="section-title" style={{ margin: "20px 0 12px" }}>绑定参数</h3><pre className="code-block">{JSON.stringify(result.sql_params || [], null, 2)}</pre><h3 className="section-title" style={{ margin: "20px 0 12px" }}>问题解析条件</h3><DataRows rows={result.parse.conditions.map(c => ({ 字段: c.col, 所属表: c.table || result.parse.subject, 运算符: c.op, 值: c.value }))} table="conditions" meta={{}} /><Notice>仅执行目录白名单内的只读查询。参数单独绑定，不拼接为可执行 SQL。</Notice></>}
  </div></Card>
  {records && <RecordPage result={result} onClose={closeRecords} />}
  {related && (related.count > 0 && result.related_token ? <RecordPage result={result} related={related} onClose={closeRelated} /> : <Modal title={related.name || related.table} subtitle={related.table} onClose={closeRelated}><DataRows rows={related.rows} table={related.table} meta={result.field_meta} cards /></Modal>)}
  </>;
}
