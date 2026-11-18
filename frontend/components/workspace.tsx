"use client";

import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  Activity,
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  BarChart3,
  CalendarDays,
  Check,
  ChevronDown,
  Database,
  GitBranch,
  History,
  MapPin,
  MessageSquare,
  Percent,
  Plane,
  Plus,
  RefreshCw,
  Send,
  Settings2,
  Sparkles,
  Trash2,
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
import { ReportCenter } from "./report-center";
import { ThemeSettings } from "./theme-settings";
import { formatDate } from "@/lib/admin";
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
  { label: "航班动态", query: "昨天实际执飞航班明细", icon: Plane },
  { label: "过站保障", query: "查看昨天的过站保障情况", icon: RefreshCw },
  { label: "起降高峰", query: "昨天高峰小时起降架次分布", icon: BarChart3 },
  { label: "A-CDM 通用报告", query: "生成昨天 A-CDM 通用报告", icon: BookOpen },
  { label: "地服运行简报", query: "生成今天地服公司运行简报", icon: CalendarDays },
  { label: "放行正常率", query: "昨天各航司放行正常率前5名", icon: Percent },
  { label: "保障节点", query: "近7天保障节点录入排行与登机桥周转", icon: GitBranch },
];

import { BotMessageContent, RichText } from "./bot-message-content";
import { AirportBenchmark, AnalysisComparisons, AnalysisHighlights, ForecastResults } from "./analysis-summary";

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
          <small>{status?.data_source === "acdm" ? "ACDM 本地数据" : "实时数据"}</small>
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
  const [reportCenter, setReportCenter] = useState(false), [unreadReports, setUnreadReports] = useState(0);
  const closeReports = useCallback(() => setReportCenter(false), []);

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
    if (!auth?.user?.can_manage || status?.data_source !== "ops_ask") return;
    let active = true;
    const load = () => request<{ unread: number }>("/api/notifications").then(r => { if (active) setUnreadReports(r.unread); }).catch(() => {});
    load(); const timer = setInterval(load, 30000);
    return () => { active = false; clearInterval(timer); };
  }, [auth?.user?.can_manage, status?.data_source, reportCenter]);

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
        const message = interrupted ? "已停止等待，后台查询可能仍在完成。" : e instanceof Error ? e.message : "查询失败，请重试";
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
  const turnaround = inspectedTurn?.result?.visual_meta?.kind === "turnaround_gantt" ? inspectedTurn.result.visual_meta : null;
  const inspectedTitle = inspectedTurn?.result?.visual_meta?.kind === "turnaround_gantt"
    ? `${inspectedTurn.result.visual_meta.flight_pair} · 过站保障 · ${inspectedTurn.result.visual_meta.flight_date}`
    : "查询结果";

  const closeSystem = useCallback(() => setSystemModal(false), []);
  const closeHelp = useCallback(() => setHelpModal(false), []);
  const closeLogin = useCallback(() => setLoginOpen(false), []);
  const closeDelete = useCallback(() => { if (!deleteBusy) { setDeleting(null); setDeleteError(""); } }, [deleteBusy]);

  const auxFormBlock = (
    <AnimatePresence>
      {showForm && (
        <motion.div id="query-form" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
          <form className="form-grid" onSubmit={e => { e.preventDefault(); setQuestion(composeQuestion(form)); setShowForm(false); input.current?.focus(); }}>
            <label className="form-field">业务对象<select value={form.domain} onChange={e => setForm(v => ({ ...v, domain: e.target.value }))}>{FLIGHT_DOMAINS.map(d => <option key={d} value={d}>{d}</option>)}</select></label>
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
    <div className={s.dayModeToggle} role="group" aria-label="日界口径">
      <button
        type="button"
        aria-pressed={dayMode === "CALENDAR"}
        onClick={() => setDayMode("CALENDAR")}
      >自然日</button>
      <button
        type="button"
        aria-pressed={dayMode === "OPERATIONAL"}
        onClick={() => setDayMode("OPERATIONAL")}
      >运营日</button>
    </div>
  ) : null;

  return (
    <Shell
      className={`${s.shell} ${hasActivity ? s.activeShell : ""}`}
      embedded={embedded}
      header={embedded ? undefined : <><div className={s.breadcrumb}><span className={s.mobileWordmark}>知航</span><span>工作空间</span><span>/</span><strong>运行问询</strong></div><div className={s.headerContext}>{status?.home_airport && <span><MapPin size={18} aria-hidden="true" />{status.home_airport.displayName || status.home_airport.cityName}</span>}<time><CalendarDays size={18} aria-hidden="true" />{status ? new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Shanghai" }) : "—"}</time><ThemeSettings /></div></>}
      statusLabel={status?.ready ? "业务数据已连接" : statusError ? "数据连接异常" : "正在检测连接"}
      sidebar={
        <>
          <div className="sidebar-section">
            <div className="nav-caption">最近会话</div>
            <div className="history-items">
              {history.length ? history.map(item => (
                <div className={s.historyRow} key={item.id}>
                  <button className={`history-item ${item.id === conversationId ? "selected" : ""}`} disabled={busy} onClick={() => restoreConversation(item)}>
                    <MessageSquare size={12} aria-hidden="true" /><span>{item.title}</span>
                  </button>
                  <Button variant="ghost" className={s.historyDelete} aria-label={`删除会话 ${item.title}`} title="删除会话" disabled={busy} onClick={() => { setDeleteError(""); setDeleting(item); }}><Trash2 size={14} aria-hidden="true" /></Button>
                </div>
              )) : (
                <div className="history-empty">{historyError ? "登录后可查看历史会话" : "还没有查询记录"}<br />{historyError ? "使用现有业务账号登录" : "查询记录会自动保存"}</div>
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
            <div><h1>{turnaround ? "航班保障" : "运行问询"}</h1>{!hasActivity && <p className={s.pageDescription}>查询航班动态、保障过程与运行指标</p>}</div>
            {acdm && <Badge>ACDM 数据副本</Badge>}
            {hasActivity && <Badge tone="green">{turns.length} 轮对话</Badge>}
          </div>
          <div className={s.actions}>
            {auth?.user?.can_manage && status?.data_source === "ops_ask" && <Button onClick={() => setReportCenter(true)}><BookOpen size={18} aria-hidden="true" />报告中心{unreadReports > 0 && <Badge tone="green">{unreadReports}未读</Badge>}</Button>}
            <Button onClick={() => setSystemModal(true)}><BarChart3 size={18} aria-hidden="true" />运行状态</Button>
            <Button variant="primary" onClick={newQuery} disabled={busy}><MessageSquare size={18} aria-hidden="true" />新对话</Button>
          </div>
        </header>

        {auth?.user?.source === "hbairport01" && <div className="admin-mobile-account"><AccountMenu user={auth.user} /><Button disabled={busy} onClick={() => setHistoryModal(true)}><History size={12} aria-hidden="true" />我的会话</Button></div>}
        {auth && !auth.user && <div className="login-notice"><Notice>登录业务账号后开始查询<Button variant="primary" onClick={() => setLoginOpen(true)}>登录业务账号<ArrowUpRight size={14} aria-hidden="true" /></Button></Notice></div>}

        {!hasActivity && (
          <div className={s.heroStage}>
            <Card className={s.queryCard}>
              <form id="question-form" onSubmit={e => { e.preventDefault(); submit(); }}>
                <label className={s.queryLabel} htmlFor="question">查询航班与保障运行</label>
                <div className={`composer ${s.composer}`}>
                  <textarea
                    ref={input}
                    id="question"
                    name="question"
                    rows={2}
                    aria-describedby="question-hint"
                    placeholder="输入航班号、机号或运行问题"
                    value={question}
                    maxLength={4000}
                    disabled={busy}
                    onChange={e => setQuestion(e.target.value)}
                    onKeyDown={queryKeyDown}
                  />
                </div>
              <div className={s.controls}>
                <div className={s.controlActions}>
                  {dayModeToggle}
                  <span id="question-hint" className={s.queryHint}>Enter 提交</span>
                  <button type="button" className="inline-button" onClick={() => setShowForm(v => !v)} aria-expanded={showForm} aria-controls="query-form"><Settings2 size={12} aria-hidden="true" />辅助查询表达<ChevronDown size={12} aria-hidden="true" style={{ transform: showForm ? "rotate(180deg)" : "none" }} /></button>
                  <button type="button" className="inline-button" onClick={() => setHelpModal(true)}><BookOpen size={12} aria-hidden="true" />提问指南</button>
                </div>
                <Button type="submit" variant="primary" className={s.submitButton} disabled={!question.trim() || busy || Boolean(auth && !auth.user)}>{busy && <Spinner />}{busy ? "查询中…" : "开始查询"}</Button>
              </div>
              </form>
              {auxFormBlock}
              <div className={s.quickStrip} role="group" aria-label="A-CDM 专题报告与高频指标">
                <span className={s.quickLabel}>快捷查询</span>
                {ACDM_QUICK_PROMPTS.map(item => (
                  <button key={item.label} type="button" className={s.quickChip} disabled={busy || Boolean(auth && !auth.user)} onClick={() => submitQuestion(item.query, undefined, true)}>
                    <item.icon size={20} aria-hidden="true" />{item.label}
                  </button>
                ))}
              </div>
            </Card>

            {!embedded && (
              <section className={s.recentSessions} aria-label="最近会话">
                <h2>最近会话</h2>
                {history.length ? history.slice(0, 3).map(item => <button className={s.recentRow} key={item.id} onClick={() => restoreConversation(item)} disabled={busy}><span className={s.recentIcon}><MessageSquare size={22} aria-hidden="true" /></span><strong>{item.title}</strong><span className={s.recentMeta}>{item.message_count} 轮对话</span><time dateTime={item.updated_at} title={formatDate(item.updated_at)}>{new Date(item.updated_at).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Shanghai" })}</time><ArrowUpRight size={16} aria-hidden="true" /></button>) : <p className={s.recentEmpty}>{auth?.user ? "还没有会话，开始你的第一次查询" : "登录后查看最近会话"}</p>}
              </section>
            )}
          </div>
        )}

        {statusError && <Notice error>{statusError}<button className="inline-button" onClick={refresh}>重试连接<RefreshCw size={12} aria-hidden="true" /></button></Notice>}

        {hasActivity && (
          <div className={`${s.splitWorkbench} ${turnaround ? s.flightWorkbench : ""}`} aria-live="polite">
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
                    ? "暂不支持"
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
                      </div>

                      <div className={s.botRow}>
                        <div
                          className={`${s.botCard} ${isInspected ? s.botCardActive : ""}`}
                          onClick={() => setInspectedTurnId(turn.id)}
                        >
                          <div className={s.botHead}>
                            <span className={s.botName}>
                              <Sparkles size={13} className={s.botSparkleIcon} aria-hidden="true" />
                              知航助手
                              <small>第 {idx + 1} 轮</small>
                            </span>
                            <Badge tone={outcomeTone}>
                              {turn.result?.partial && outcomeLabel === "已回答" ? "部分结果" : outcomeLabel}
                            </Badge>
                          </div>

                          {isTurnBusy && (
                            <div className={s.botProgress}>
                              <div className="progress-top"><Spinner /><span>{turn.progress.message}</span><time>{turn.elapsed.toFixed(1)}s</time></div>
                              <div className={s.botProgressTrack}><div className={s.botProgressFill} style={{ transform: `scaleX(${turn.progress.percent / 100})` }} /></div>
                              {turn.delta && <div className={s.botText}><BotMessageContent text={turn.delta} /></div>}
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
                            turn.result.forecast ? <ForecastResults forecast={turn.result.forecast} />
                              : turn.result.comparisons?.length ? <AnalysisComparisons result={turn.result} compact />
                              : turn.result.airport_sources?.length && turn.result.sections?.length
                                && new Set(turn.result.sections.map(item => item.period.join(":"))).size === 1
                              ? <AirportBenchmark result={turn.result} compact />
                              : turn.result.sections?.length ? <AnalysisHighlights sections={turn.result.sections} compact />
                              : <div className={s.botText}><BotMessageContent text={turn.result.answer} /></div>
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
                              {turn.result?.sections?.length ? `${turn.result.sections.length} 项结果`
                                : typeof turn.result?.main_total === "number" && turn.result.main_total > 0
                                ? `命中 ${turn.result.main_total.toLocaleString()} 条`
                                : turn.result?.source || "实时库"}
                            </span>
                            <button type="button" className={`${s.inspectTrigger} ${isInspected ? s.inspectTriggerActive : ""}`}
                              aria-current={isInspected ? "true" : undefined}
                              aria-label={`查看第 ${idx + 1} 轮查询结果`}
                              onClick={e => { e.stopPropagation(); setInspectedTurnId(turn.id); }}>
                              {isInspected ? (
                                <><Check size={12} aria-hidden="true" />当前结果</>
                              ) : (
                                <>查看结果<ArrowRight size={12} aria-hidden="true" /></>
                              )}
                            </button>
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
                    placeholder="继续提问"
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

            <section className={s.canvasPane} aria-label="查询结果">
              <div className={s.canvasHead}>
                <div className={s.canvasHeadTitle}>
                  <Activity size={16} aria-hidden="true" />
                  <Badge>第 {inspectedIndex + 1} 轮</Badge>
                  <strong title={inspectedTitle}>{inspectedTitle}</strong>
                </div>
                <div className={s.actions}>
                  {inspectedTurn && latestTurn && inspectedTurn.id !== latestTurn.id && (
                    <Button variant="ghost" onClick={() => setInspectedTurnId(latestTurn.id)} style={{ minHeight: 30, padding: "4px 10px", fontSize: 12 }}>
                      切回最新轮
                    </Button>
                  )}
                  {inspectedTurn?.isHistorical && <Badge>历史记录</Badge>}
                </div>
              </div>
              <div ref={canvasScrollRef} className={s.canvasBody}>
                {inspectedTurn ? (
                  <ResultView
                    compact
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
                    <Activity size={20} aria-hidden="true" />
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
            <div><b>查看查询结果</b><br />在结果中查看航班动态、保障时刻和相关明细。</div>
          </div>
          <p className="helper">辅助查询会将条件填入输入框，提交前可继续编辑。</p>
        </Modal>
      )}
      {reportCenter && <ReportCenter onClose={closeReports} />}
    </Shell>
  );
}
