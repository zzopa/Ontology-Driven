"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowDown, ArrowRight, ArrowUpRight, Banknote, BookOpen, CalendarDays, Check, ChevronDown, Circle, ClipboardList, Code2, Database, FileText, GitBranch, History, Layers, MessageSquare, Plus, RefreshCw, Search, Send, Settings2, ShieldCheck, Sparkles, Users, X } from "lucide-react";
import { request, streamQuestion } from "@/lib/api";
import { composeQuestion, type QueryForm } from "@/lib/query";
import type { ExecutionStep, QueryResult, Status } from "@/lib/types";
import { stopSteps, updateStep } from "@/lib/execution";
import { liveAnswerState } from "@/lib/live-answer";
import type { Auth, Conversation, ConversationDetail, Paged, User } from "@/lib/admin";
import { ConversationsPanel } from "./admin/conversations";
import { AccountMenu, AuthForm } from "./admin/account";
import { Badge, Button, Card, Modal, Notice, Shell, Spinner } from "./ui";
import { ResultView } from "./result-view";
import { ExecutionProcess } from "./execution-process";

const DOMAINS = ["全部业务", "员工", "合同", "资金", "会议", "资产", "采购"];
const EXAMPLES = [
  { domain: "员工", text: "查找信科公司李康平的情况", icon: Users },
  { domain: "合同", text: "今年签了多少份合同？", icon: FileText },
  { domain: "资金", text: "今年的资金流水金额合计是多少？", icon: Banknote },
  { domain: "会议", text: "按月份统计今年的会议数量", icon: CalendarDays },
  { domain: "资产", text: "资产卡片共有多少条记录？", icon: Layers },
  { domain: "采购", text: "采购项目共有多少条记录？", icon: ClipboardList },
];
const STAGES = [{ id: "connect", label: "连接数据" }, { id: "parse", label: "理解问题" }, { id: "graph", label: "关联寻路" }, { id: "sql", label: "实时查询" }, { id: "answer", label: "证据校验" }, { id: "done", label: "完成" }];

function DataStatus({ status, error, refresh }: { status: Status | null; error: string; refresh: () => void }) {
  return <Card className="card-pad"><div className="card-head"><h2 className="section-title"><Database size={14} />数据连接</h2><Button variant="ghost" aria-label="更新连接状态" onClick={refresh}><RefreshCw size={12} /></Button></div>
    <div className="connection-info"><span className="db-mark"><Database size={17} /></span><div><strong>{status?.database || "连接检测中"}</strong><small>PostgreSQL · 实时查询</small></div><Badge tone={status?.ready ? "green" : "neutral"}>{status?.ready ? "已连接" : error ? "未连接" : "检测中"}</Badge></div>
    <div className="stats-grid">{[["业务对象", status?.queryable_entities], ["关联关系", status?.relations], ["数据表", status?.tables], ["结构字段", status?.fields]].map(([label, value]) => <div className="stat" key={label}><strong>{typeof value === "number" ? value.toLocaleString() : "—"}</strong><span>{label}</span></div>)}</div>
    <div className="card-divider" /><div className="status-caption"><span title={status?.refreshed_at}>结构更新 {status ? new Date(status.refreshed_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }) : "—"}</span><Badge>启动同步</Badge></div>
    {error && <Notice error>{error}</Notice>}
  </Card>;
}

function Pipeline() {
  const items = [{ icon: BookOpen, title: "业务本体", desc: "表结构、字段注释与管理规则" }, { icon: GitBranch, title: "关系检索", desc: "定位主体与可信关联路径" }, { icon: Code2, title: "实时 SQL", desc: "参数化查询 · 只读执行" }, { icon: ShieldCheck, title: "证据回答", desc: "核对来源、数值与统计口径" }];
  return <Card className="card-pad pipeline-card"><div className="card-head"><h2 className="section-title"><GitBranch size={14} />从问题到答案</h2><Badge>可追溯</Badge></div><ol className="pipeline">{items.map(item => <li key={item.title}><span className="pipeline-icon"><item.icon size={12} /></span><div><strong>{item.title}</strong><small>{item.desc}</small></div></li>)}</ol><div className="quiet-note"><ShieldCheck size={12} /><span>仅依据本次返回的事实回答，证据检查不能替代业务审核。</span></div></Card>;
}

export function Workspace({ embedded = false }: { embedded?: boolean }) {
  const [historyModal, setHistoryModal] = useState(false);
  const closeHistory = useCallback(() => setHistoryModal(false), []);
  const [question, setQuestion] = useState("");
  const [status, setStatus] = useState<Status | null>(null), [statusError, setStatusError] = useState("");
  const [domain, setDomain] = useState("全部业务"), [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<QueryForm>({ domain: "员工", company: "", keyword: "", month: "", intent: "detail" });
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [progress, setProgress] = useState({ stage: "connect", percent: 0, message: "正在准备查询…" });
  const [delta, setDelta] = useState(""), [elapsed, setElapsed] = useState(0);
  const [steps, setSteps] = useState<ExecutionStep[]>([]);
  const [result, setResult] = useState<QueryResult | null>(null), [history, setHistory] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<string | undefined>(), [snapshot, setSnapshot] = useState(false);
  const [auth, setAuth] = useState<Auth | null>(null), [loginOpen, setLoginOpen] = useState(false), [historyError, setHistoryError] = useState("");
  const [systemModal, setSystemModal] = useState(false);
  const [helpModal, setHelpModal] = useState(false);
  const abort = useRef<AbortController | null>(null), input = useRef<HTMLTextAreaElement>(null), resultRef = useRef<HTMLDivElement>(null);
  const mounted = useRef(true), activeQuestion = useRef("");
  const refresh = useCallback(async () => {
    try { const data = await request<Status>("/api/status"); if (mounted.current) { setStatus(data); setStatusError(""); } }
    catch (e) { if (mounted.current) setStatusError(e instanceof Error ? e.message : "状态获取失败"); }
  }, []);
  const refreshHistory = useCallback(async () => {
    try { const data = await request<Paged<Conversation>>("/api/conversations"); if (mounted.current) { setHistory(data.items); setHistoryError(""); } }
    catch (e) { if (mounted.current) setHistoryError(e instanceof Error ? e.message : "会话读取失败"); }
  }, []);
  const refreshAuth = useCallback(async () => {
    try { const value = await request<Auth>("/api/auth/status"); if (mounted.current) setAuth(value); }
    catch { /* Status panel reports connectivity problems separately. */ }
  }, []);
  useEffect(() => { mounted.current = true; refresh(); refreshHistory(); refreshAuth(); return () => { mounted.current = false; abort.current?.abort(); }; }, [refresh, refreshHistory, refreshAuth]);
  useEffect(() => {
    if (!busy) return;
    const started = Date.now(), timer = setInterval(() => setElapsed((Date.now() - started) / 1000), 250);
    return () => clearInterval(timer);
  }, [busy]);

  async function submit() {
    const value = question.trim(); if (!value || busy) { input.current?.focus(); return; }
    abort.current = new AbortController(); activeQuestion.current = value;
    setBusy(true); setError(""); setResult(null); setSnapshot(false); setDelta(""); setElapsed(0); setProgress({ stage: "connect", percent: 3, message: "正在准备查询…" });
    setSteps([{ id: "connect", label: "连接业务数据库", state: "running", message: "请求已提交，等待服务器建立只读连接", details: [], started_at: 0, elapsed: 0 }]);
    setTimeout(() => resultRef.current?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block: "nearest" }), 80);
    try {
      await streamQuestion(value, abort.current.signal, event => {
        if (!mounted.current) return;
        if (event.conversation_id) setConversationId(event.conversation_id);
        if (event.type === "progress") setProgress({ stage: event.stage || "connect", percent: event.percent || 3, message: event.message || "正在处理…" });
        else if (event.type === "step" && event.step) { const step = event.step; setSteps(items => updateStep(items, step)); }
        else if (event.type === "answer_delta") setDelta(text => text + (event.delta || ""));
        else if (event.type === "answer_error") setProgress(p => ({ ...p, message: event.message || "正在生成事实摘要…" }));
        else if (event.type === "result" && event.data) {
          setResult(event.data); setConversationId(event.data.conversation_id);
          setSteps(event.data.execution_trace || []);
        }
      }, conversationId);
    } catch (e) {
      if (mounted.current) {
        const interrupted = e instanceof Error && e.name === "AbortError";
        const message = interrupted ? "已停止等待。本次后台只读查询可能仍在完成，不会修改业务数据。" : e instanceof Error ? e.message : "查询失败，请重试";
        setError(message); setSteps(items => stopSteps(items, message, interrupted));
      }
    } finally { if (mounted.current) { setBusy(false); refresh(); refreshHistory(); } }
  }
  function newQuery() { if (busy) return; setConversationId(undefined); setSnapshot(false); setQuestion(""); setResult(null); setError(""); setDelta(""); setSteps([]); input.current?.focus(); }
  async function restoreConversation(item: Conversation) {
    if (busy) return;
    try {
      const detail = await request<ConversationDetail>(`/api/conversations/${item.id}`);
      const message = detail.messages.at(-1);
      setConversationId(item.id); setQuestion(message?.question || ""); setResult(message?.result || null); setSnapshot(true);
      setSteps(message?.execution_trace || message?.result?.execution_trace || []); setElapsed(0);
      setError(message?.error || (message?.status === "running" ? "此查询仍在后台执行，请稍后重新打开会话。" : "")); setDelta("");
    } catch (e) { setError(e instanceof Error ? e.message : "读取会话失败"); }
  }
  function loggedIn(user: User) { setAuth(value => value ? { ...value, initialized: true, user } : null); setLoginOpen(false); setConversationId(undefined); setResult(null); setSteps([]); setError(""); setDelta(""); setHistory([]); refreshHistory(); }
  const stageIndex = STAGES.findIndex(s => s.id === progress.stage);
  const liveAnswer = liveAnswerState(delta, busy, Boolean(result));
  const visibleExamples = domain === "全部业务" ? EXAMPLES.slice(0, 4) : EXAMPLES.filter(e => e.domain === domain);
  const closeSystem = useCallback(() => setSystemModal(false), []), closeHelp = useCallback(() => setHelpModal(false), []);
  const closeLogin = useCallback(() => setLoginOpen(false), []);

  return <Shell embedded={embedded} statusLabel={status?.ready ? "业务数据已连接" : statusError ? "数据连接异常" : "正在检测连接"} sidebar={<><div className="sidebar-section"><div className="nav-caption">历史会话 <span style={{ float: "right" }}>{history.length ? history.length.toString().padStart(2, "0") : ""}</span></div><div className="history-items">{history.length ? history.map(item => <button className={`history-item ${item.id === conversationId ? "selected" : ""}`} key={item.id} disabled={busy} onClick={() => restoreConversation(item)}><MessageSquare size={12} /><span>{item.title}</span></button>) : <div className="history-empty">{historyError || "还没有查询记录"}<br />{historyError ? "登录后可读取自己的会话" : "会话会自动保存到服务端"}</div>}</div><div className="history-tools"><button onClick={() => setHistoryModal(true)} className="inline-button"><History size={12} />管理、归档与导出会话</button></div></div>{auth?.user ? <AccountMenu user={auth.user} /> : auth?.initialized && <Button onClick={() => setLoginOpen(true)}><Users size={13} />登录业务账号</Button>}</>}>
    <div className="heading-row"><div><div className="eyebrow"><span className="eyebrow-line" />YOUR DATA, CONNECTED.</div><h1>让数据，回答你的问题。</h1><p className="heading-description">连接业务本体与实时数据，从一句问题开始，找到有依据的答案。</p></div><div className="heading-actions"><Button onClick={() => setSystemModal(true)}><Database size={13} />运行状态</Button><Button onClick={newQuery} disabled={busy}><Plus size={14} />新查询</Button></div></div>
    {auth?.user?.source === "hbairport01" && <div className="admin-mobile-account"><AccountMenu user={auth.user} /><Button onClick={() => setHistoryModal(true)}><History size={12} />我的会话</Button></div>}
    {auth && !auth.user && <Notice error>当前系统要求登录后问数。<Button onClick={() => setLoginOpen(true)}>登录业务账号</Button></Notice>}
    <div className="bento-grid"><Card className="query-card"><div className="card-head"><div><h2 className="query-title">每一个问题，都有<span>数据依据</span>。</h2><p className="card-description">员工、合同、资金、会议及更多业务，一处查询。</p></div><span className="mini-mark"><Sparkles size={16} /></span></div>
      <label className="query-label" htmlFor="question">你想了解什么？</label>
      <form onSubmit={e => { e.preventDefault(); submit(); }}><div className="composer"><textarea ref={input} id="question" name="question" placeholder="例如：查找信科公司李康平的情况" value={question} maxLength={4000} disabled={busy} onChange={e => setQuestion(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && !e.nativeEvent.isComposing && e.nativeEvent.keyCode !== 229 && !e.repeat) { e.preventDefault(); submit(); } }} /><div className="composer-bottom"><span className="composer-tip"><ShieldCheck size={12} />只读查询 <span className="keycap">Ctrl ↵</span><span className="keycap">⌘ ↵</span></span><Button type="submit" variant="primary" disabled={!question.trim() || busy || Boolean(auth && !auth.user)}>{busy ? <Spinner /> : <ArrowRight size={14} />}{busy ? "查询中" : "开始查询"}</Button></div></div></form>
      <div className="query-controls"><button className="inline-button" onClick={() => setShowForm(v => !v)} aria-expanded={showForm} aria-controls="query-form"><Settings2 size={12} />辅助查询表单<ChevronDown size={12} style={{ transform: showForm ? "rotate(180deg)" : "none" }} /></button><button className="inline-button" onClick={() => setHelpModal(true)}><BookOpen size={12} />提问指南</button></div>
      <AnimatePresence>{showForm && <motion.div id="query-form" initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} style={{ overflow: "hidden" }}><form className="form-grid" onSubmit={e => { e.preventDefault(); setQuestion(composeQuestion(form)); input.current?.focus(); }}>
        <label className="form-field">业务对象<select value={form.domain} onChange={e => setForm(v => ({ ...v, domain: e.target.value }))}>{DOMAINS.map(d => <option key={d}>{d}</option>)}</select></label>
        <label className="form-field">查询方式<select value={form.intent} onChange={e => setForm(v => ({ ...v, intent: e.target.value }))}><option value="detail">信息明细</option><option value="count">记录数量</option><option value="sum">金额合计</option></select></label>
        <label className="form-field">所属企业 · 可选<input placeholder="输入企业全称或已配置简称" value={form.company} onChange={e => setForm(v => ({ ...v, company: e.target.value }))} /></label>
        <label className="form-field">业务月份 · 可选<input type="month" value={form.month} onChange={e => setForm(v => ({ ...v, month: e.target.value }))} /></label>
        <label className="form-field form-span">姓名、编号或其他条件<input placeholder="例如：李康平，或合同编号 GL-XZ-2026001" value={form.keyword} onChange={e => setForm(v => ({ ...v, keyword: e.target.value }))} /></label>
        <div className="form-footer"><span className="helper">生成问题后可编辑；实际筛选以返回的解析条件为准。</span><Button type="submit" disabled={busy}><ArrowUpRight size={12} />生成问题</Button></div>
      </form></motion.div>}</AnimatePresence>
      <div className="examples"><div className="examples-head"><span>试着这样问</span><span>选择示例，编辑后提交 <ArrowDown size={10} style={{ display: "inline", marginLeft: 3 }} /></span></div><div className="domain-tabs" role="tablist" aria-label="示例业务领域">{DOMAINS.map(d => <button className={`domain-tab ${domain === d ? "selected" : ""}`} role="tab" aria-selected={domain === d} key={d} onClick={() => setDomain(d)}>{d === "全部业务" ? "推荐" : d}</button>)}</div><div className="example-grid">{visibleExamples.map(item => <button className="example-card" key={item.text} disabled={busy} onClick={() => { setQuestion(item.text); input.current?.focus(); }}><span className="example-icon"><item.icon size={13} /></span><span>{item.text}</span><ArrowUpRight size={12} className="example-arrow" /></button>)}</div></div>
    </Card><div className="side-stack"><DataStatus status={status} error={statusError} refresh={refresh} /><Pipeline /></div></div>
    {!result && !busy && <div className="lower-strip">{[{ icon: GitBranch, title: "懂业务的关系", desc: "基于表注释与管理规则定位关联路径" }, { icon: Database, title: "真实的查询结果", desc: "实时查询数据库，明细按需展开" }, { icon: ShieldCheck, title: "可核验的回答", desc: "查看来源事实、统计口径与执行 SQL" }].map(item => <Card className="capability" key={item.title}><item.icon size={18} strokeWidth={1.5} className="capability-icon" /><div><h3>{item.title}</h3><p>{item.desc}</p></div></Card>)}</div>}
    <div ref={resultRef} className="result-section" aria-live="polite" aria-busy={busy && !delta}>
      {busy && <><div className="result-top"><div><h2>{delta ? "已查到结果，正在补充解读" : "正在查找答案"}</h2><p>{activeQuestion.current}</p></div><Button onClick={() => abort.current?.abort()} variant="ghost"><X size={12} />停止等待</Button></div><Card className="progress-card"><div className="progress-top"><Spinner /><span>{progress.message}</span><time>{elapsed.toFixed(1)} s</time></div><div className="progress-track" role="progressbar" aria-label="查询处理进度" aria-valuenow={progress.percent} aria-valuemin={0} aria-valuemax={100}><div className="progress-fill" style={{ width: `${progress.percent}%` }} /></div><div className="progress-steps">{STAGES.map((s, i) => <span key={s.id} className={`progress-step ${i === stageIndex ? "active" : i < stageIndex ? "done" : ""}`}>{i < stageIndex ? <Check size={11} /> : <Circle size={10} />}{s.label}</span>)}</div></Card></>}
      {liveAnswer.visible && <div aria-busy={false} aria-live="polite" aria-atomic={false}><Card className="card-pad live-body"><div className="answer-head"><span className="answer-mark"><Sparkles size={16} /></span><strong>{liveAnswer.title}</strong><Badge tone={busy ? "green" : "amber"}>{busy ? "实时追加" : "未完整完成"}</Badge></div><div className={`answer-text${busy ? " live" : ""}`}>{delta}</div><p className="helper" style={{ marginTop: 12 }}>{liveAnswer.note}</p></Card></div>}
      {(busy || steps.length > 0 || snapshot && (result || error)) && <ExecutionProcess steps={steps} busy={busy} elapsed={elapsed} />}
      {error && <Notice error>{error} <button className="inline-button" onClick={submit} disabled={busy} style={{ marginLeft: 8 }}>重新查询 <RefreshCw size={11} /></button></Notice>}
      {result && !busy && <>{snapshot && <div className="snapshot-notice"><Notice>这是历史查询快照，不代表当前数据。重新提交问题可获取实时结果和分页明细。</Notice></div>}<ResultView key={result.message_id || result.question} result={result} showProcess={false} /></>}
    </div>
    {historyModal && auth?.user && <Modal wide title="我的问答会话" onClose={closeHistory}><ConversationsPanel current={auth.user} apiPrefix="/api/conversations" /></Modal>}
    {loginOpen && auth && <Modal title="业务账号登录" onClose={closeLogin}><AuthForm auth={auth} onLogin={loggedIn} /></Modal>}
    {systemModal && <Modal title="运行状态" subtitle="来自 /api/status 的实时服务信息" onClose={closeSystem}><div className="binding-grid">{[["业务数据库", status?.database], ["查询请求数", status?.requests], ["失败请求数", status?.failures], ["平均查询耗时", status ? `${status.average_seconds} s` : "—"], ["启动耗时", status ? `${status.startup_seconds} s` : "—"], ["结构更新时间", status?.refreshed_at]].map(([k, v]) => <div className="binding" key={k}><span>{k}</span><code>{v ?? "—"}</code></div>)}</div><Notice>结构在服务启动时刷新。管理后台按账号角色维护配置，本体与业务库查询保持分离。</Notice><div className="modal-actions"><Button onClick={refresh}><RefreshCw size={12} />刷新状态</Button></div></Modal>}
    {helpModal && <Modal title="如何得到更准确的答案" subtitle="描述清楚对象、时间与统计口径" onClose={closeHelp}><div className="rules-description"><div><b>指定对象</b><br />提供员工姓名、合同编号或具体业务名称。同名人员应补充所属企业。</div><div><b>明确时间</b><br />例如“2026年3月签订的合同”；业务时间和数据创建时间不同。</div><div><b>说清金额口径</b><br />合同总额、付款计划和实际资金流水不是同一个指标。不同币种不直接混算。</div><div><b>核对依据</b><br />在回答结果中查看中文字段、全部允许字段、关联记录、事实来源与参数化 SQL。</div></div><Notice>辅助表单生成的是可编辑的问题，不会在后台附加不可见条件。没有明确业务解释时，系统可能要求澄清。</Notice></Modal>}
  </Shell>;
}
