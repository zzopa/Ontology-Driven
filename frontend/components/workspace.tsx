"use client";

import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  Activity,
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Bot,
  Check,
  ChevronDown,
  Database,
  GitBranch,
  History,
  Layers,
  MessageSquare,
  Plus,
  RefreshCw,
  Send,
  Settings2,
  ShieldCheck,
  Sparkles,
  Trash2,
  User as UserIcon,
  Users,
} from "lucide-react";
import { request, streamQuestion } from "@/lib/api";
import {
  applyTurnEvent,
  createChatTurn,
  failChatTurn,
  resolvedChoiceId,
  turnsFromMessages,
  type ChatTurn,
  type ClarificationRef,
} from "@/lib/chat-thread";
import { composeQuestion, type QueryForm } from "@/lib/query";
import type { Status } from "@/lib/types";
import type { Auth, Conversation, ConversationDetail, Paged, User } from "@/lib/admin";
import { ConversationsPanel } from "./admin/conversations";
import { AccountMenu, AuthForm } from "./admin/account";
import { Badge, Button, Card, Modal, Notice, Shell, Spinner } from "./ui";
import { ResultView } from "./result-view";
import s from "./workspace.module.css";

const FLIGHT_DOMAINS = [
  "实际执飞航班",
  "航班计划动态",
  "航空公司",
  "机场",
  "停机位",
  "登机口",
  "飞机/航空器",
  "延误与异常原因",
  "航班生产量(旅客/货邮)",
  "保障过程与时刻",
];

const ACDM_QUICK_PROMPTS = [
  { label: "A-CDM 通用报告", query: "生成昨天 A-CDM 通用报告" },
  { label: "地服运行简报", query: "生成今天地服公司运行简报" },
  { label: "保障节点与单桥周转", query: "近7天保障节点录入排行与登机桥周转" },
  { label: "地服起飞正常率", query: "昨天地服起飞正常率是多少" },
  { label: "航司放行正常率 Top5", query: "昨天各航司放行正常率前5名" },
  { label: "过站平均保障时长", query: "昨天过站平均保障时长是多少" },
  { label: "起降高峰小时", query: "昨天高峰小时起降架次分布" },
];

function DataStatus({ status, error, refresh }: { status: Status | null; error: string; refresh: () => void }) {
  return (
    <Card className="card-pad">
      <div className="card-head">
        <h2 className="section-title"><Database size={14} />数据连接</h2>
        <Button variant="ghost" aria-label="更新连接状态" onClick={refresh}><RefreshCw size={12} /></Button>
      </div>
      <div className="connection-info">
        <span className="db-mark"><Database size={17} /></span>
        <div>
          <strong>{status?.database || "连接检测中"}</strong>
          <small>{status?.data_source === "acdm" ? "ACDM 本地副本 · 本体查询" : "PostgreSQL · 实时查询"}</small>
        </div>
        <Badge tone={status?.ready ? "green" : "neutral"}>{status?.ready ? "已连接" : error ? "未连接" : "检测中"}</Badge>
      </div>
      <div className="stats-grid">
        {[["业务对象", status?.queryable_entities], ["关联关系", status?.relations], ["数据表", status?.tables], ["结构字段", status?.fields]].map(([label, value]) => (
          <div className="stat" key={label}>
            <strong>{typeof value === "number" ? value.toLocaleString() : "—"}</strong>
            <span>{label}</span>
          </div>
        ))}
      </div>
      <div className="card-divider" />
      <div className="status-caption">
        <span title={status?.refreshed_at}>结构更新 {status ? new Date(status.refreshed_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }) : "—"}</span>
        <Badge>启动同步</Badge>
      </div>
      {error && <Notice error>{error}</Notice>}
    </Card>
  );
}

export function Workspace({ embedded = false }: { embedded?: boolean }) {
  const [historyModal, setHistoryModal] = useState(false);
  const closeHistory = useCallback(() => setHistoryModal(false), []);
  const [question, setQuestion] = useState("");
  const [status, setStatus] = useState<Status | null>(null), [statusError, setStatusError] = useState("");
  const [showForm, setShowForm] = useState(false), [showQuickDrawer, setShowQuickDrawer] = useState(false);
  const [dayMode, setDayMode] = useState<"CALENDAR" | "OPERATIONAL">("CALENDAR");
  const [form, setForm] = useState<QueryForm>({ domain: "实际执飞航班", intent: "status", date: "昨天", direction: "", airline: "", keyword: "" });
  const [turns, setTurns] = useState<ChatTurn[]>([]), [activeTurnId, setActiveTurnId] = useState<string | null>(null);
  const [inspectedTurnId, setInspectedTurnId] = useState<string | null>(null);
  const [history, setHistory] = useState<Conversation[]>([]);
  const [deleting, setDeleting] = useState<Conversation | null>(null), [deleteBusy, setDeleteBusy] = useState(false), [deleteError, setDeleteError] = useState("");
  const [conversationId, setConversationId] = useState<string | undefined>(), [snapshot, setSnapshot] = useState(false);
  const [auth, setAuth] = useState<Auth | null>(null), [loginOpen, setLoginOpen] = useState(false), [historyError, setHistoryError] = useState("");
  const [systemModal, setSystemModal] = useState(false);
  const [helpModal, setHelpModal] = useState(false);

  const abort = useRef<AbortController | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const chatScrollRef = useRef<HTMLDivElement>(null);
  const canvasScrollRef = useRef<HTMLDivElement>(null);
  const mounted = useRef(true);
  const busy = Boolean(activeTurnId);

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

  useEffect(() => {
    mounted.current = true;
    refresh();
    refreshHistory();
    refreshAuth();
    return () => { mounted.current = false; abort.current?.abort(); };
  }, [refresh, refreshHistory, refreshAuth]);

  useEffect(() => {
    if (!activeTurnId) return;
    const started = Date.now();
    const timer = setInterval(() => {
      const secs = (Date.now() - started) / 1000;
      setTurns(prev => prev.map(t => t.id === activeTurnId ? { ...t, elapsed: secs } : t));
    }, 250);
    return () => clearInterval(timer);
  }, [activeTurnId]);

  const latestTurn = turns.at(-1);
  useEffect(() => {
    const el = chatScrollRef.current;
    if (el && turns.length > 0) {
      el.scrollTo({
        top: el.scrollHeight,
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
      });
    }
  }, [turns.length, latestTurn?.result?.message_id, latestTurn?.error, latestTurn?.delta]);

  useEffect(() => {
    canvasScrollRef.current?.scrollTo({ top: 0 });
  }, [inspectedTurnId]);

  async function executeTurn(turnId: string, value: string, clarification: ClarificationRef | undefined, clearInput: boolean) {
    if (!value || busy) { input.current?.focus(); return; }
    if (clearInput) setQuestion("");
    setShowQuickDrawer(false);
    abort.current = new AbortController();
    setActiveTurnId(turnId);
    setInspectedTurnId(turnId);
    setSnapshot(false);
    try {
      await streamQuestion(value, abort.current.signal, event => {
        if (!mounted.current) return;
        if (event.conversation_id) setConversationId(event.conversation_id);
        if (event.type === "result" && event.data?.conversation_id) setConversationId(event.data.conversation_id);
        setTurns(prev => prev.map(t => t.id === turnId ? applyTurnEvent(t, event) : t));
      }, conversationId, clarification, dayMode === "OPERATIONAL" ? "OPERATIONAL" : undefined);
    } catch (e) {
      if (mounted.current) {
        const interrupted = e instanceof Error && e.name === "AbortError";
        const message = interrupted ? "已停止等待。本次后台只读查询可能仍在完成，不会修改业务数据。" : e instanceof Error ? e.message : "查询失败，请重试";
        setTurns(prev => prev.map(t => t.id === turnId ? failChatTurn(t, message, interrupted) : t));
      }
    } finally {
      if (mounted.current) {
        setActiveTurnId(null);
        refresh();
        refreshHistory();
      }
    }
  }

  async function submit() {
    return submitQuestion(question);
  }

  function queryKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey || event.altKey || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
    event.preventDefault();
    if (!event.repeat) event.currentTarget.form?.querySelector<HTMLButtonElement>('button[type="submit"]')?.click();
  }

  async function submitQuestion(nextQuestion: string, choiceId?: string, standalone = false) {
    const value = nextQuestion.trim(); if (!value || busy) { input.current?.focus(); return; }
    const lastResult = standalone ? undefined : turns.at(-1)?.result;
    const clarification = lastResult?.outcome === "clarification_needed" && lastResult.message_id
      ? { clarification_of: lastResult.message_id, choice_id: choiceId }
      : undefined;
    const turnId = `turn-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    const newTurn = createChatTurn(turnId, value, value, clarification);
    setTurns(prev => [...prev, newTurn]);
    return executeTurn(turnId, value, clarification, !choiceId && !standalone);
  }

  async function retryTurn(turnId: string) {
    if (busy) return;
    const target = turns.find(t => t.id === turnId);
    if (!target) return;
    const reset = createChatTurn(turnId, target.question, target.displayQuestion, target.clarification);
    setTurns(prev => prev.map(t => t.id === turnId ? reset : t));
    return executeTurn(turnId, target.question, target.clarification, false);
  }

  function newQuery() {
    if (busy) return;
    setConversationId(undefined);
    setSnapshot(false);
    setQuestion("");
    setTurns([]);
    setInspectedTurnId(null);
    setShowQuickDrawer(false);
    input.current?.focus();
  }

  function conversationDeleted(id: string) {
    setHistory(items => items.filter(item => item.id !== id));
    if (id === conversationId) newQuery();
    refreshHistory();
  }

  async function deleteConversation() {
    if (!deleting || deleteBusy || busy) return;
    setDeleteBusy(true); setDeleteError("");
    try {
      await request(`/api/conversations/${deleting.id}`, { method: "DELETE" });
      conversationDeleted(deleting.id);
      setDeleting(null);
    } catch (e) { setDeleteError(e instanceof Error ? e.message : "删除会话失败，请重试"); }
    finally { setDeleteBusy(false); }
  }

  async function restoreConversation(item: Conversation) {
    if (busy) return;
    try {
      const detail = await request<ConversationDetail>(`/api/conversations/${item.id}`);
      const loaded = turnsFromMessages(detail.messages);
      setConversationId(item.id);
      setTurns(loaded);
      setInspectedTurnId(loaded.at(-1)?.id || null);
      setQuestion("");
      setSnapshot(true);
    } catch (e) {
      const errId = `restore-err-${Date.now()}`;
      setTurns([{ ...createChatTurn(errId, item.title, item.title), error: e instanceof Error ? e.message : "读取会话失败", steps: [] }]);
      setInspectedTurnId(errId);
    }
  }

  function loggedIn(user: User) {
    setAuth(value => value ? { ...value, initialized: true, user } : null);
    setLoginOpen(false);
    setConversationId(undefined);
    setTurns([]);
    setInspectedTurnId(null);
    setHistory([]);
    refreshHistory();
  }

  const acdm = status?.data_source === "acdm";
  const hasActivity = turns.length > 0;
  const inspectedTurn = turns.find(t => t.id === inspectedTurnId) || latestTurn || null;
  const inspectedIndex = inspectedTurn ? turns.findIndex(t => t.id === inspectedTurn.id) : -1;
  const inspectedTitle = inspectedTurn?.result?.visual_meta?.kind === "turnaround_gantt"
    ? `${inspectedTurn.result.visual_meta.flight_pair} · 过站保障 · ${inspectedTurn.result.visual_meta.flight_date}`
    : inspectedTurn?.result?.question || inspectedTurn?.displayQuestion || "洞察画板";

  const closeSystem = useCallback(() => setSystemModal(false), []);
  const closeHelp = useCallback(() => setHelpModal(false), []);
  const closeLogin = useCallback(() => setLoginOpen(false), []);
  const closeDelete = useCallback(() => { if (!deleteBusy) { setDeleting(null); setDeleteError(""); } }, [deleteBusy]);

  const auxFormBlock = (
    <AnimatePresence>
      {showForm && (
        <motion.div id="query-form" initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} style={{ overflow: "hidden" }}>
          <form className="form-grid" onSubmit={e => { e.preventDefault(); setQuestion(composeQuestion(form)); setShowForm(false); input.current?.focus(); }}>
            <label className="form-field">业务本体对象<select value={form.domain} onChange={e => setForm(v => ({ ...v, domain: e.target.value }))}>{FLIGHT_DOMAINS.map(d => <option key={d} value={d}>{d}</option>)}</select></label>
            <label className="form-field">查询方式<select value={form.intent} onChange={e => setForm(v => ({ ...v, intent: e.target.value }))}><option value="status">执行概况 / 动态</option><option value="detail">信息明细</option><option value="count">架次 / 数量统计</option></select></label>
            <label className="form-field">运行日期 · 可选<select value={form.date} onChange={e => setForm(v => ({ ...v, date: e.target.value }))}><option value="昨天">昨天（常用）</option><option value="今天">今天</option><option value="前天">前天</option><option value="最近7天">最近7天</option><option value="">不限日期</option></select></label>
            <label className="form-field">进出港方向 · 可选<select value={form.direction} onChange={e => setForm(v => ({ ...v, direction: e.target.value }))}><option value="">全部方向</option><option value="出港">出港 (DEP)</option><option value="进港">进港 (ARR)</option></select></label>
            <label className="form-field">航空公司 · 可选<input placeholder="例如：中国国际航空、深圳航空，或二字码 CA、ZH" value={form.airline || ""} onChange={e => setForm(v => ({ ...v, airline: e.target.value }))} /></label>
            <label className="form-field">航班号、编号或其他条件 · 可选<input placeholder="例如：航班号 CA1234、机号 B-1856 或机位 101" value={form.keyword || ""} onChange={e => setForm(v => ({ ...v, keyword: e.target.value }))} /></label>
            <div className="form-footer"><span className="helper">生成问题后可编辑；实际筛选以返回的解析条件为准。</span><Button type="submit" disabled={busy}><ArrowUpRight size={12} aria-hidden="true" />填入输入框</Button></div>
          </form>
        </motion.div>
      )}
    </AnimatePresence>
  );

  const dayModeToggle = status?.operational_day?.enabled ? (
    <div role="group" aria-label="日界口径" style={{ display: "inline-flex", gap: 3, alignItems: "center", padding: "2px", borderRadius: 6, border: "1px solid var(--line)" }}>
      <button
        type="button"
        className="inline-button"
        style={dayMode === "CALENDAR" ? { background: "rgba(16,185,129,.16)", color: "var(--accent-bright)", fontWeight: 600, padding: "2px 7px", borderRadius: 4, minHeight: 24 } : { padding: "2px 7px", opacity: .75, minHeight: 24 }}
        onClick={() => setDayMode("CALENDAR")}
      >自然日</button>
      <button
        type="button"
        className="inline-button"
        style={dayMode === "OPERATIONAL" ? { background: "rgba(56,189,248,.16)", color: "#7dd3fc", fontWeight: 600, padding: "2px 7px", borderRadius: 4, minHeight: 24 } : { padding: "2px 7px", opacity: .75, minHeight: 24 }}
        onClick={() => setDayMode("OPERATIONAL")}
      >运营日</button>
    </div>
  ) : null;

  return (
    <Shell
      embedded={embedded}
      statusLabel={status?.ready ? "业务数据已连接" : statusError ? "数据连接异常" : "正在检测连接"}
      sidebar={
        <>
          <div className="sidebar-section">
            <div className="nav-caption">历史会话 <span style={{ float: "right", fontVariantNumeric: "tabular-nums" }}>{history.length ? history.length.toString().padStart(2, "0") : ""}</span></div>
            <div className="history-items">
              {history.length ? history.map(item => (
                <div className={s.historyRow} key={item.id}>
                  <button className={`history-item ${item.id === conversationId ? "selected" : ""}`} disabled={busy} onClick={() => restoreConversation(item)}>
                    <MessageSquare size={12} aria-hidden="true" /><span>{item.title}</span>
                  </button>
                  <Button variant="ghost" className={s.historyDelete} aria-label={`删除会话 ${item.title}`} title="删除会话" disabled={busy} onClick={() => { setDeleteError(""); setDeleting(item); }}><Trash2 size={14} aria-hidden="true" /></Button>
                </div>
              )) : (
                <div className="history-empty">{historyError ? "登录后可读取个人历史会话" : "还没有查询记录"}<br />{historyError ? "支持原有业务账号权限" : "会话会自动保存到服务端"}</div>
              )}
            </div>
            <div className="history-tools"><button disabled={busy} onClick={() => setHistoryModal(true)} className="inline-button"><History size={12} aria-hidden="true" />管理、归档与导出会话</button></div>
          </div>
          {auth?.user ? <AccountMenu user={auth.user} /> : auth?.initialized && <Button className="sidebar-login-btn" onClick={() => setLoginOpen(true)}><Users size={13} aria-hidden="true" />登录业务账号</Button>}
        </>
      }
    >
      <div className={s.workspace}>
        <header className={s.toolbar}>
          <div className={s.title}>
            <h1>知航 · ACDM 智能问询</h1>
            {acdm && <Badge>ACDM 数据副本</Badge>}
            {hasActivity && <Badge tone="green">{turns.length} 轮对话</Badge>}
          </div>
          <div className={s.actions}>
            <Button onClick={() => setSystemModal(true)}><Database size={14} aria-hidden="true" />运行状态</Button>
            <Button onClick={newQuery} disabled={busy}><Plus size={14} aria-hidden="true" />新对话</Button>
          </div>
        </header>

        {auth?.user?.source === "hbairport01" && <div className="admin-mobile-account"><AccountMenu user={auth.user} /><Button disabled={busy} onClick={() => setHistoryModal(true)}><History size={12} aria-hidden="true" />我的会话</Button></div>}
        {auth && !auth.user && <div className="login-notice"><Notice>登录业务账号后，即可按你的数据权限开始查询。<Button variant="primary" onClick={() => setLoginOpen(true)}>登录业务账号<ArrowUpRight size={14} aria-hidden="true" /></Button></Notice></div>}

        {!hasActivity && (
          <div className={s.heroStage}>
            <Card className={s.queryCard}>
              <form onSubmit={e => { e.preventDefault(); submit(); }}>
                <label className={s.queryLabel} htmlFor="question">查询问题<small>航班 · ACDM · 地服调度</small></label>
                <div className={`composer ${s.composer}`}>
                  <textarea
                    ref={input}
                    id="question"
                    name="question"
                    rows={2}
                    aria-describedby="question-hint"
                    placeholder="输入航班号、机号或保障问题，例如：2026-11-10 CZ3357 的过站保障甘特图"
                    value={question}
                    maxLength={4000}
                    disabled={busy}
                    onChange={e => setQuestion(e.target.value)}
                    onKeyDown={queryKeyDown}
                  />
                  <Button type="submit" variant="primary" className="composer-submit" disabled={!question.trim() || busy || Boolean(auth && !auth.user)}>
                    {busy ? <Spinner /> : <Send size={16} aria-hidden="true" />}<span>{busy ? "查询中…" : "开始查询"}</span>
                  </Button>
                </div>
              </form>
              <div className={s.controls}>
                <span id="question-hint" className="composer-tip"><ShieldCheck size={12} aria-hidden="true" />只读查询 <span className="keycap">Enter 提交 · Shift+Enter 换行</span>{status?.home_airport?.cityName && <span style={{ marginLeft: 8, opacity: .8 }}>· 本场：{status.home_airport.displayName || status.home_airport.cityName}</span>}</span>
                <div className={s.controlActions}>
                  {dayModeToggle}
                  <button className="inline-button" onClick={() => setShowForm(v => !v)} aria-expanded={showForm} aria-controls="query-form"><Settings2 size={12} aria-hidden="true" />辅助查询表达<ChevronDown size={12} aria-hidden="true" style={{ transform: showForm ? "rotate(180deg)" : "none" }} /></button>
                  <button className="inline-button" onClick={() => setHelpModal(true)}><BookOpen size={12} aria-hidden="true" />提问指南</button>
                </div>
              </div>
              {auxFormBlock}
              <div className={s.quickStrip} role="group" aria-label="A-CDM 专题报告与高频指标">
                <span className={s.quickLabel}>A-CDM 速查：</span>
                {ACDM_QUICK_PROMPTS.map(item => (
                  <button key={item.label} type="button" className={s.quickChip} disabled={busy || Boolean(auth && !auth.user)} onClick={() => submitQuestion(item.query, undefined, true)}>
                    {item.label}
                  </button>
                ))}
              </div>
            </Card>

            {!embedded && (
              <section className={s.telemetryGrid} aria-label="运行数据源概览">
                <button type="button" className={`${s.telemetryCard} ${s.telemetryBtn}`} onClick={() => setSystemModal(true)}>
                  <span className={s.telemetryHead}><span>当前数据源</span><Database size={14} aria-hidden="true" /></span>
                  <strong className={s.telemetryValue} data-text="true">{status?.database || "连接检测中"}</strong>
                  <span className={s.telemetryMeta}>{status?.data_source === "acdm" ? "ACDM 本地副本 · 只读" : status?.ready ? "PostgreSQL 实时在线" : "正在检测数据库"}</span>
                </button>
                <div className={s.telemetryCard}>
                  <span className={s.telemetryHead}><span>业务本体对象</span><GitBranch size={14} aria-hidden="true" /></span>
                  <strong className={s.telemetryValue}>{typeof status?.queryable_entities === "number" ? status.queryable_entities.toLocaleString() : "—"}</strong>
                  <span className={s.telemetryMeta}>{typeof status?.relations === "number" ? `${status.relations} 条确认关联路径` : "业务模型与口径定义"}</span>
                </div>
                <div className={s.telemetryCard}>
                  <span className={s.telemetryHead}><span>受控表与字段</span><Layers size={14} aria-hidden="true" /></span>
                  <strong className={s.telemetryValue}>{typeof status?.tables === "number" ? status.tables.toLocaleString() : "—"}</strong>
                  <span className={s.telemetryMeta}>{typeof status?.fields === "number" ? `${status.fields.toLocaleString()} 个可查白名单字段` : "只读白名单访问"}</span>
                </div>
                <button type="button" className={`${s.telemetryCard} ${s.telemetryBtn}`} onClick={() => setSystemModal(true)}>
                  <span className={s.telemetryHead}><span>服务同步时间</span><Activity size={14} aria-hidden="true" /></span>
                  <strong className={s.telemetryValue}>{status?.refreshed_at ? new Date(status.refreshed_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }) : "—"}</strong>
                  <span className={s.telemetryMeta}>{status ? `已服务 ${status.requests} 次 · 均值 ${status.average_seconds}s` : "点击查看运行状态"}</span>
                </button>
              </section>
            )}
          </div>
        )}

        {statusError && <Notice error>{statusError}<button className="inline-button" onClick={refresh}>重试连接<RefreshCw size={12} aria-hidden="true" /></button></Notice>}

        {hasActivity && (
          <div className={s.splitWorkbench} aria-live="polite">
            <section className={s.chatPane} aria-label="对话记录与追问输入">
              <div className={s.chatPaneHead}>
                <div className={s.chatHeadTitle}>
                  <span>对话记录</span>
                  <Badge tone="green">{turns.length} 轮</Badge>
                  {snapshot && turns.some(t => t.isHistorical) && <Badge>历史会话</Badge>}
                </div>
                <Button variant="ghost" onClick={newQuery} disabled={busy} style={{ minHeight: 30, padding: "4px 10px", fontSize: 12 }}>
                  <Plus size={13} aria-hidden="true" />新对话
                </Button>
              </div>

              <div ref={chatScrollRef} className={s.chatMessages}>
                {turns.map((turn, idx) => {
                  const isLatest = idx === turns.length - 1;
                  const isTurnBusy = isLatest && busy && activeTurnId === turn.id;
                  const isInspected = inspectedTurn?.id === turn.id;
                  const chosenOptionId = resolvedChoiceId(turns, idx);
                  const isNoMatch = turn.result?.outcome === "unsupported" && Boolean(
                    turn.result.answer?.includes("回复回复不了") || turn.result.answer?.includes("未匹配到业务本体对照")
                  );
                  const outcomeLabel = isTurnBusy
                    ? "查询中"
                    : turn.error
                    ? "异常"
                    : turn.result?.outcome === "clarification_needed"
                    ? "确认条件"
                    : turn.result?.outcome === "no_data"
                    ? "未命中"
                    : isNoMatch
                    ? "回复不了"
                    : turn.result?.outcome === "unsupported"
                    ? "边界提示"
                    : "已回答";
                  const outcomeTone = isTurnBusy || turn.error || turn.result?.outcome === "clarification_needed" || isNoMatch
                    ? "amber"
                    : turn.result?.outcome === "answered"
                    ? "green"
                    : "neutral";

                  return (
                    <div key={turn.id} className={s.chatTurnGroup}>
                      <div className={s.userRow}>
                        <div className={s.userBubble}>{turn.displayQuestion}</div>
                        <span className={s.userAvatar} aria-hidden="true"><UserIcon size={13} /></span>
                      </div>

                      <div className={s.botRow}>
                        <span className={s.botAvatar} aria-hidden="true"><Bot size={15} /></span>
                        <div
                          className={`${s.botCard} ${isInspected ? s.botCardActive : ""}`}
                          onClick={() => setInspectedTurnId(turn.id)}
                          role="button"
                          tabIndex={0}
                          onKeyDown={e => {
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              setInspectedTurnId(turn.id);
                            }
                          }}
                          aria-label={`查看第 ${idx + 1} 轮详情到右侧画板`}
                        >
                          <div className={s.botHead}>
                            <span className={s.botName}>
                              知航助手
                              <small>第 {idx + 1} 轮</small>
                            </span>
                            <Badge tone={outcomeTone}>
                              {outcomeLabel}
                              {typeof turn.result?.elapsed === "number" && !isTurnBusy ? ` · ${turn.result.elapsed.toFixed(2)}s` : ""}
                            </Badge>
                          </div>

                          {isTurnBusy && (
                            <div className={s.botProgress}>
                              <div className="progress-top"><Spinner /><span>{turn.progress.message}</span><time>{turn.elapsed.toFixed(1)}s</time></div>
                              <div className={s.botProgressTrack}><div className={s.botProgressFill} style={{ width: `${turn.progress.percent}%` }} /></div>
                              {turn.delta && <p className={s.botText}>{turn.delta}</p>}
                            </div>
                          )}

                          {!isTurnBusy && turn.error && (
                            <>
                              <p className={s.botError}>{turn.error}</p>
                              <div>
                                <Button
                                  variant="secondary"
                                  onClick={e => { e.stopPropagation(); retryTurn(turn.id); }}
                                  style={{ minHeight: 30, padding: "4px 10px", fontSize: 12 }}
                                >
                                  <RefreshCw size={12} aria-hidden="true" />重新查询
                                </Button>
                              </div>
                            </>
                          )}

                          {!isTurnBusy && !turn.error && turn.result && (
                            <p className={s.botText}>{turn.result.answer || "已完成本轮查询，右侧画板可查看详细信息。"}</p>
                          )}

                          {Boolean(turn.result?.clarification?.options.length) && (
                            <div className={s.choicePills} onClick={e => e.stopPropagation()}>
                              {turn.result!.clarification!.options.map(choice => {
                                const chosen = chosenOptionId === choice.id;
                                return (
                                  <button
                                    key={choice.id}
                                    type="button"
                                    className={`${s.choicePill} ${chosen ? s.choicePillChosen : ""}`}
                                    disabled={!isLatest || isTurnBusy}
                                    onClick={() => submitQuestion(choice.label, choice.id)}
                                  >
                                    {chosen && <Check size={12} aria-hidden="true" />}
                                    <span>{choice.label}{chosen ? "（已选）" : ""}</span>
                                  </button>
                                );
                              })}
                            </div>
                          )}

                          {Boolean(turn.result?.capability_gate?.alternatives?.length) && (
                            <div className={s.choicePills} onClick={e => e.stopPropagation()}>
                              {turn.result!.capability_gate!.alternatives!.map(alt => (
                                <button
                                  key={alt}
                                  type="button"
                                  className={s.choicePill}
                                  disabled={busy}
                                  onClick={() => submitQuestion(alt, undefined, true)}
                                >
                                  <span>{alt}</span>
                                </button>
                              ))}
                            </div>
                          )}

                          <div className={s.botFooter}>
                            <span>
                              {typeof turn.result?.main_total === "number" && turn.result.main_total > 0
                                ? `命中 ${turn.result.main_total.toLocaleString()} 条`
                                : turn.result?.source || "实时库"}
                            </span>
                            <span className={`${s.inspectTrigger} ${isInspected ? s.inspectTriggerActive : ""}`}>
                              {isInspected ? (
                                <><Check size={12} aria-hidden="true" />已在右侧画板打开</>
                              ) : (
                                <>在右侧画板查看<ArrowRight size={12} aria-hidden="true" /></>
                              )}
                            </span>
                          </div>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>

              <div className={s.chatComposerWrap}>
                {showQuickDrawer && (
                  <div className={s.quickDrawer} role="group" aria-label="A-CDM 专题报告与高频指标">
                    {ACDM_QUICK_PROMPTS.map(item => (
                      <button
                        key={item.label}
                        type="button"
                        className={s.quickChip}
                        disabled={busy || Boolean(auth && !auth.user)}
                        onClick={() => submitQuestion(item.query, undefined, true)}
                      >
                        {item.label}
                      </button>
                    ))}
                  </div>
                )}
                {auxFormBlock}
                <form className={s.composerDockCard} onSubmit={e => { e.preventDefault(); submit(); }}>
                  <textarea
                    ref={input}
                    id="question"
                    name="question"
                    rows={2}
                    aria-label="继续追问"
                    aria-describedby="followup-hint"
                    placeholder="继续追问或切换条件（如：只看出港 / 改查昨天 / 看明细）…"
                    value={question}
                    maxLength={4000}
                    disabled={busy}
                    onChange={e => setQuestion(e.target.value)}
                    onKeyDown={queryKeyDown}
                  />
                  <div className={s.composerDockBar}>
                    <div className={s.composerDockTools}>
                      <button
                        type="button"
                        className={`${s.miniPillBtn} ${showQuickDrawer ? s.miniPillBtnActive : ""}`}
                        onClick={() => setShowQuickDrawer(v => !v)}
                      >
                        <BookOpen size={12} aria-hidden="true" />A-CDM 速查
                      </button>
                      <button
                        type="button"
                        className={`${s.miniPillBtn} ${showForm ? s.miniPillBtnActive : ""}`}
                        onClick={() => setShowForm(v => !v)}
                      >
                        <Settings2 size={12} aria-hidden="true" />辅助表达
                      </button>
                      {dayModeToggle}
                    </div>
                    <Button
                      type="submit"
                      variant="primary"
                      className={s.dockSendBtn}
                      disabled={!question.trim() || busy || Boolean(auth && !auth.user)}
                    >
                      {busy ? <Spinner /> : <Send size={13} aria-hidden="true" />}
                      <span>{busy ? "查询中…" : "发送"}</span>
                    </Button>
                  </div>
                  <span id="followup-hint" className="helper">Enter 提交 · Shift+Enter 换行</span>
                </form>
              </div>
            </section>

            <section className={s.canvasPane} aria-label="实时洞察画板">
              <div className={s.canvasHead}>
                <div className={s.canvasHeadTitle}>
                  <Sparkles size={15} style={{ color: "var(--accent-bright)" }} aria-hidden="true" />
                  <Badge>第 {inspectedIndex + 1} 轮</Badge>
                  <strong title={inspectedTitle}>{inspectedTitle}</strong>
                </div>
                <div className={s.actions}>
                  {inspectedTurn && latestTurn && inspectedTurn.id !== latestTurn.id && (
                    <Button variant="ghost" onClick={() => setInspectedTurnId(latestTurn.id)} style={{ minHeight: 30, padding: "4px 10px", fontSize: 12 }}>
                      切回最新轮
                    </Button>
                  )}
                  <Badge tone={inspectedTurn?.isHistorical ? "neutral" : "green"}>
                    {inspectedTurn?.isHistorical ? "历史记录" : "洞察画板"}
                  </Badge>
                </div>
              </div>
              <div ref={canvasScrollRef} className={s.canvasBody}>
                {inspectedTurn ? (
                  <ResultView
                    key={inspectedTurn.id}
                    result={inspectedTurn.result}
                    onQuestion={q => submitQuestion(q)}
                    onClarify={
                      inspectedTurn.id === latestTurn?.id && !busy
                        ? choice => submitQuestion(choice.label, choice.id)
                        : undefined
                    }
                    run={{
                      question: inspectedTurn.displayQuestion,
                      busy: inspectedTurn.id === activeTurnId && busy,
                      steps: inspectedTurn.steps,
                      elapsed: inspectedTurn.elapsed,
                      progress: inspectedTurn.progress,
                      delta: inspectedTurn.delta,
                      error: inspectedTurn.error || "",
                      onStop: () => abort.current?.abort(),
                      onRetry: () => retryTurn(inspectedTurn.id),
                    }}
                  />
                ) : (
                  <div className={s.canvasEmpty}>
                    <Sparkles size={20} aria-hidden="true" />
                    <span>提交问题后，图表、节点时序与数据明细将在此处展示。</span>
                  </div>
                )}
              </div>
            </section>
          </div>
        )}
      </div>

      {historyModal && auth?.user && <Modal wide title="我的问答会话" onClose={closeHistory}><ConversationsPanel current={auth.user} apiPrefix="/api/conversations" onDeleted={conversationDeleted} /></Modal>}
      {deleting && <Modal title="永久删除会话？" onClose={closeDelete}><p className="long-description">删除「{deleting.title}」及其全部问题、答案和快照，无法恢复。</p>{deleteError && <Notice error>{deleteError}</Notice>}<div className="modal-actions"><Button disabled={deleteBusy} onClick={closeDelete}>取消</Button><Button enterConfirm disabled={deleteBusy || busy} onClick={deleteConversation}>{deleteBusy ? <Spinner /> : <Trash2 size={13} aria-hidden="true" />}确认永久删除</Button></div></Modal>}
      {loginOpen && auth && <Modal title="业务账号登录" onClose={closeLogin}><AuthForm auth={auth} onLogin={loggedIn} /></Modal>}
      {systemModal && (
        <Modal title="运行状态" subtitle="数据连接与服务信息" onClose={closeSystem}>
          <DataStatus status={status} error={statusError} refresh={refresh} />
          {acdm && <Notice>当前查询 ACDM 数据副本，范围：{status?.coverage?.start} 至 {status?.coverage?.end_exclusive}（结束日期不含），复制时间：{status?.coverage?.exported_at}。</Notice>}
          <div className="binding-grid">
            {[["查询请求数", status?.requests], ["失败请求数", status?.failures], ["平均查询耗时", status ? `${status.average_seconds} s` : "—"], ["启动耗时", status ? `${status.startup_seconds} s` : "—"]].map(([k, v]) => (
              <div className="binding" key={k}><span>{k}</span><code>{v ?? "—"}</code></div>
            ))}
          </div>
        </Modal>
      )}
      {helpModal && (
        <Modal title="如何得到更准确的答案" subtitle="描述清楚业务对象、运行时间与统计口径" onClose={closeHelp}>
          <div className="rules-description">
            <div><b>指定业务对象</b><br />明确航班号（如 CA1234）、航空公司、机场三字码或机位编号。</div>
            <div><b>明确运行时间</b><br />例如“昨天”、“今天”或指定业务日（如 2026-11-08）。航班运行以业务日为基准。</div>
            <div><b>说明方向与口径</b><br />区分“出港”与“进港”；执飞情况统计、计划航班量与实际执飞明细口径不同。</div>
            <div><b>核对数据依据</b><br />在回答结果中查看中文字段、执飞时间、关联记录、事实来源与参数化 SQL。</div>
          </div>
          <Notice>辅助表达生成的是可编辑的问题，不会在后台附加不可见条件。没有明确业务解释时，系统可能要求澄清。</Notice>
        </Modal>
      )}
    </Shell>
  );
}
