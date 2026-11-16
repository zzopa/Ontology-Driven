"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, ArrowUpRight, ClipboardCheck, Clock3, GitBranch, LayoutDashboard, LoaderCircle, LogOut, Plane, RefreshCw, Send, ShieldCheck, Users } from "lucide-react";
import { request } from "@/lib/api";
import type { Auth } from "@/lib/admin";
import type { VisualTurnaround } from "@/lib/types";
import { inputISO, localInput, opsHref, opsMode, opsNow, opsReplayAt, opsRequest, opsTerminals, replayDataDate, opsTime, type OpsEvent, type OpsFlight, type OpsLedger, type OpsMe, type OpsMode, type OpsOrder, type OpsPage, type OpsProposal, type OpsResource, type OpsRun, type OpsSnapshot, type OpsTask } from "@/lib/ops";
import { AuthForm } from "../admin/account";
import { Button } from "../ui";
import { TurnaroundGantt } from "../turnaround-gantt";
import { Empty, FlightMatrix, FlightSummary, MapPanel, Panel, ResourceTimeline, Tag, Workload } from "./common";
import { DispatchPage } from "./dispatch";
import { ResourcesPage } from "./resources";
import { OrdersPage } from "./orders";
import { OpsPerception } from "./perception";
import s from "./ops.module.css";

const pages = [
  { id: "overview", href: "/ops", label: "感知总览", icon: LayoutDashboard },
  { id: "flights", href: "/ops/flights", label: "航班保障", icon: Plane },
  { id: "dispatch", href: "/ops/dispatch", label: "智慧派单", icon: GitBranch },
  { id: "work-orders", href: "/ops/work-orders", label: "工单中心", icon: ClipboardCheck },
  { id: "resources", href: "/ops/resources", label: "班组管理", icon: Users },
] as const;

export function OpsWorkspace({ page }: { page: OpsPage }) {
  const [auth, setAuth] = useState<Auth | null>(null), [authLoaded, setAuthLoaded] = useState(false);
  const [me, setMe] = useState<OpsMe | null>(null), [terminal, setTerminal] = useState("T2"), [minutes, setMinutes] = useState(90);
  const [mode, setMode] = useState<OpsMode>("replay");
  const [replayAt, setReplayAt] = useState("");
  const [snapshot, setSnapshot] = useState<OpsSnapshot | null>(null), [ledger, setLedger] = useState<OpsLedger | null>(null);
  const [proposals, setProposals] = useState<OpsProposal[]>([]), [orders, setOrders] = useState<OpsOrder[]>([]), [events, setEvents] = useState<OpsEvent[]>([]), [orderResources, setOrderResources] = useState<OpsResource[]>([]);
  const [manualTasks, setManualTasks] = useState<OpsTask[]>([]), [sourceFlights, setSourceFlights] = useState<OpsFlight[]>([]), [sourceError, setSourceError] = useState("");
  const [selected, setSelected] = useState(""), [detail, setDetail] = useState<VisualTurnaround | null>(null), [search, setSearch] = useState("");
  const [detailLinks, setDetailLinks] = useState<OpsFlight[]>([]), [detailNotes, setDetailNotes] = useState<{ field: string; value: string; reason: string }[]>([]);
  const [attentionOnly, setAttentionOnly] = useState(false), [timeline, setTimeline] = useState(false);
  const [error, setError] = useState(""), [notice, setNotice] = useState(""), [busy, setBusy] = useState(false), [loading, setLoading] = useState(false);
  const requestVersion = useRef(0), flightVersion = useRef(0);
  const canView = Boolean(me?.admin || me?.grants.some(g => g.terminal === terminal && ["viewer", "dispatcher"].includes(g.role)));
  const staticData = Boolean(me?.read_only && me.data_mode === "static-test");
  const canDispatch = !staticData && Boolean(me?.admin || me?.grants.some(g => g.terminal === terminal && g.role === "dispatcher"));

  const clearData = useCallback(() => {
    requestVersion.current++; flightVersion.current++;
    setSnapshot(null); setLedger(null); setOrders([]); setProposals([]); setEvents([]);
    setManualTasks([]); setSourceFlights([]); setSourceError(""); setOrderResources([]); setDetail(null); setDetailLinks([]); setDetailNotes([]); setSelected(""); setLoading(false);
  }, []);

  const identify = useCallback(async () => {
    const current = await request<Auth>("/api/auth/status");
    setAuth(current); setAuthLoaded(true);
    if (!current.user) { setMe(null); clearData(); return; }
    const user = await opsRequest<OpsMe>("/me", "T2");
    if (user.read_only) {
      const url = new URL(window.location.href); url.searchParams.delete("at"); url.searchParams.delete("mode"); window.history.replaceState(null, "", url);
      setMode("replay");
    }
    setMe(old => JSON.stringify(old) === JSON.stringify(user) ? old : user);
    setTerminal(old => !new URLSearchParams(window.location.search).has('terminal') && user.read_only && user.admin ? 'ALL' : opsTerminals(user).includes(old) ? old : user.terminals[0] || "T2");
  }, [clearData]);

  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    setMode(opsMode());
    setReplayAt(localInput());
    setTerminal(query.get("terminal") || "T2");
    const windowMinutes = Number(query.get("minutes"));
    if ([30, 60, 90, 120, 180].includes(windowMinutes)) setMinutes(windowMinutes);
    setSelected(query.get("flight_id") || "");
    identify().catch(e => { setError(e.message); setAuthLoaded(true); });
  }, [identify]);

  const load = useCallback(async () => {
    if (!me || !auth?.user) return;
    const version = ++requestVersion.current;
    setLoading(true);
    try {
      const current = await request<Auth>("/api/auth/status");
      if (!current.user) { if (version === requestVersion.current) { setAuth(current); setMe(null); clearData(); } return; }
      const currentMe = await opsRequest<OpsMe>("/me", terminal);
      if (JSON.stringify(currentMe) !== JSON.stringify(me)) {
        if (version === requestVersion.current) { clearData(); setAuth(current); setMe(currentMe); setTerminal(old => opsTerminals(currentMe).includes(old) ? old : currentMe.terminals[0] || "T2"); }
        return;
      }
      if (page === "work-orders") {
        const data = await opsRequest<{ orders: OpsOrder[]; events: OpsEvent[]; resources: OpsResource[]; manual_tasks: OpsTask[]; source_flights: OpsFlight[]; source_error: string }>("/work-orders", terminal);
        if (version === requestVersion.current) { setOrders(data.orders); setEvents(data.events); setOrderResources(data.resources); setManualTasks(data.manual_tasks); setSourceFlights(data.source_flights); setSourceError(data.source_error); }
      } else if (canView) {
        const snap = opsRequest<OpsSnapshot>(`/snapshot?minutes=${page === "overview" && me.read_only ? 90 : minutes}`, terminal);
        if (page === "resources" || page === "dispatch") {
          const [resources, data, plans] = await Promise.all([
            opsRequest<OpsLedger>("/resources", terminal), snap.catch(() => null),
            page === "dispatch" ? opsRequest<{ proposals: OpsProposal[] }>("/proposals", terminal) : Promise.resolve({ proposals: [] }),
          ]);
          if (version === requestVersion.current) { setLedger(resources); setSnapshot(data); setProposals(plans.proposals); }
        } else {
          const data = await snap;
          if (version === requestVersion.current) setSnapshot(data);
        }
      }
      if (version === requestVersion.current) setError("");
    } catch (e) {
      if (version === requestVersion.current) setError(e instanceof Error ? e.message : "刷新失败；当前保留上次快照");
    } finally { if (version === requestVersion.current) setLoading(false); }
  }, [me, auth?.user, page, terminal, minutes, canView, clearData]);

  useEffect(() => {
    if (!me) return;
    if (!canView && page !== "work-orders" && me.grants.some(g => g.terminal === terminal && g.role === "crew")) { window.location.replace(opsHref('/ops/work-orders', terminal)); return; }
    void load();
    const timer = window.setInterval(() => { if (!document.hidden) void load(); }, 15000);
    const visible = () => { if (!document.hidden) void load(); };
    document.addEventListener("visibilitychange", visible);
    return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", visible); requestVersion.current++; };
  }, [me, canView, page, terminal, load]);

  useEffect(() => {
    if (page !== "flights" || !selected || !canView) { setDetail(null); setDetailLinks([]); setDetailNotes([]); return; }
    const version = ++flightVersion.current;
    setDetail(null);
    opsRequest<{ turnaround: VisualTurnaround; linked_flights: OpsFlight[]; quality_notes: { field: string; value: string; reason: string }[] }>(`/flights/${encodeURIComponent(selected)}`, terminal).then(x => { if (version === flightVersion.current) { setDetail(x.turnaround); setDetailLinks(x.linked_flights); setDetailNotes(x.quality_notes); } }).catch(e => { if (version === flightVersion.current) setError(e.message); });
    return () => { flightVersion.current++; };
  }, [page, selected, terminal, canView]);

  const run: OpsRun = async (action, message = "已保存") => {
    if (busy) return;
    setBusy(true); setNotice("");
    try { await action(); setNotice(message); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : "操作未完成"); }
    finally { setBusy(false); }
  };

  function choose(id: string) {
    setSelected(id);
    const url = new URL(window.location.href);
    if (id) url.searchParams.set("flight_id", id); else url.searchParams.delete("flight_id");
    url.searchParams.set("terminal", terminal); url.searchParams.set("minutes", String(minutes));
    window.history.replaceState(null, "", url);
  }

  function changeTerminal(value: string) {
    clearData(); setTerminal(value); setError(''); setNotice('');
    const url = new URL(window.location.href); url.searchParams.set('terminal', value);
    for (const key of ['flight_id', 'proposal_id', 'task_id', 'order_id']) url.searchParams.delete(key);
    window.history.replaceState(null, '', url);
  }

  const selectedFlight = snapshot?.flights.find(f => f.id === selected);
  const href = (path: string) => opsHref(path, terminal, { minutes: String(minutes) });
  const displayTime = snapshot?.captured_at || me?.fixture_time || opsNow().toISOString();
  const immersive = page === "overview" && staticData && Boolean(snapshot?.perception?.scenarios?.length);
  return <div className={s.root} data-immersive={immersive}>
    <header className={s.header}><div className={s.brand}><Plane size={29} aria-hidden="true" />知航 A-CDM</div><nav className={s.nav} aria-label="运行工作台导航">{pages.filter(p => canView || p.id === "work-orders").map(p => <a key={p.id} href={href(p.href)} aria-current={page === p.id ? "page" : undefined}><p.icon size={16} aria-hidden="true" />{p.label}</a>)}</nav><div className={s.headerRight}><Tag tone={mode === "replay" ? "warn" : "info"}><ShieldCheck size={13} aria-hidden="true" />{staticData ? "静态 JSON 测试数据" : mode === "replay" ? authLoaded && opsReplayAt() ? "历史预演 · 自选时刻" : "历史预演 · 默认同刻" : "实时时钟"}</Tag>{authLoaded && <time>{localInput(new Date(displayTime)).slice(0, 10)} {opsTime(displayTime)}</time>}{me && <span>{me.user.display_name}</span>}<a href="/">原智能问询 <ArrowUpRight size={12} aria-hidden="true" /></a>{auth?.user && <button aria-label="退出登录" onClick={() => void run(async () => { await request("/api/auth/logout", { method: "POST" }); setMe(null); setAuth(old => old ? { ...old, user: null } : old); clearData(); }, "已退出登录")}><LogOut size={16} /></button>}</div></header>
    {!authLoaded ? <div className={s.empty}><LoaderCircle className={s.spinner} size={22} /> 正在读取登录状态</div> : !auth?.user ? <div className={s.auth}><div className={s.eyebrow}>运行统筹 · 共用业务账号</div>{error && <div role="alert" className={s.notice}>{error}</div>}<AuthForm auth={auth || { initialized: true, can_setup: false, user: null, allow_guest: false }} onLogin={() => { setError(""); void identify().catch(e => setError(e.message)); }} /></div> : <main className={immersive ? s.immersiveMain : s.main}>{immersive && snapshot ? <>{error && <div role="alert" className={s.notice}>{error}</div>}<OpsPerception snapshot={snapshot} selected={selected} onSelect={choose} terminals={me ? opsTerminals(me) : []} onTerminalChange={changeTerminal} /></> : <>
      <div className={s.scope}>{staticData ? <><label><span className={s.muted}>数据模式</span><input value="静态 JSON · 只读测试展示" readOnly /></label><label><span className={s.muted}>固定测试场景时间</span><input value={localInput(new Date(displayTime)).replace("T", " ")} readOnly /></label></> : <><label><span className={s.muted}>运行模式</span><select value={mode} disabled={busy} onChange={e => { const url = new URL(window.location.href); url.searchParams.set("mode", e.target.value); if (e.target.value === "live") url.searchParams.delete("at"); window.location.assign(url.href); }}><option value="replay">历史预演（可选时间）</option><option value="live">实时时钟</option></select></label>{mode === "replay" && <form className={s.inlineForm} onSubmit={e => { e.preventDefault(); try { const at = inputISO(replayAt); const url = new URL(window.location.href); url.searchParams.set("at", at); for (const key of ["flight_id", "proposal_id", "task_id", "order_id"]) url.searchParams.delete(key); window.location.assign(url.href); } catch (error) { setError(error instanceof Error ? error.message : "请选择有效时间"); } }}><label><span className={s.muted}>预演日期和时间（北京时间）</span><input name="replay_at" type="datetime-local" required max={`${replayDataDate}T23:59`} value={replayAt} disabled={busy} onChange={e => setReplayAt(e.target.value)} /></label><Button type="submit" disabled={busy || !replayAt}>应用时间</Button>{opsReplayAt() && <Button disabled={busy} onClick={() => { const url = new URL(window.location.href); url.searchParams.delete("at"); window.location.assign(url.href); }}>恢复默认同刻</Button>}</form>}</>}<label><span className={s.muted}>航站楼</span><select aria-label="航站楼" disabled={busy} value={terminal} onChange={e => changeTerminal(e.target.value)}>{(me ? opsTerminals(me) : [terminal]).map(t => <option key={t} value={t}>{t === "ALL" ? "全场 · T2 / T3" : t}</option>)}</select></label>{canView && <label><span className={s.muted}>观察窗口</span><select disabled={busy} value={minutes} onChange={e => setMinutes(Number(e.target.value))}>{[30, 60, 90, 120, 180].map(x => <option key={x} value={x}>未来{x}分钟</option>)}</select></label>}{["overview", "flights"].includes(page) && <label><span className={s.muted}>筛选航班</span><input value={search} onChange={e => setSearch(e.target.value)} placeholder="航班、机号或机位" /></label>}<span className={s.spacer} />{snapshot && <span className={s.muted}>观测 {opsTime(snapshot.captured_at, true)} · {staticData ? "静态测试" : mode === "replay" ? "模拟值守" : snapshot.settings.shadow_mode ? "影子值守" : "正式提醒"}</span>}<Button disabled={loading || busy || !me} onClick={() => void load()}><RefreshCw size={14} className={loading ? s.spinner : undefined} />刷新</Button></div>
      {!staticData && mode === "replay" && <div className={s.notice} data-tone="info"><Clock3 size={16} /><div>当前数据截至 {replayDataDate}。{opsReplayAt() ? "已固定到所选时刻，刷新和页面跳转会保留；可重新选择时间推进预演。" : "默认使用已知数据日的同一时刻；选择时间并应用后固定观测。"}切换时间不会清空或回滚已有模拟台账。</div></div>}
      {staticData && <div className={s.notice} data-tone="info"><ShieldCheck size={17} /><div>全部运行工作台页面当前展示虚构静态测试数据。筛选、航班下钻、方案切换和历史查看可用；审批、派单、报工及配置写入已暂停。原智能问询继续使用原数据源。</div></div>}
      {error && <div role="alert" className={s.notice} data-tone="bad"><AlertTriangle size={17} />{error}；刷新失败时请核对画面快照时间。</div>}
      {notice && <div role="status" className={s.notice} data-tone="info">{notice}<button className={s.linkButton} onClick={() => setNotice("")}>收起</button></div>}
      {!me?.admin && !me?.grants.length && <Empty>当前账号尚未获得运行岗位授权，请联系原系统管理员。原智能问询权限不受影响。</Empty>}
      {snapshot && <><div className={s.notice} data-tone={mode === "replay" || snapshot.source.fresh ? "info" : "warn"}><Clock3 size={16} /><div>{snapshot.worker.message}。{snapshot.source.message}{!staticData && mode === "replay" && <>。预演时钟 {snapshot.captured_at.slice(0, 10)} {opsTime(snapshot.captured_at)}；真实时间 {snapshot.real_time.slice(0, 10)} {opsTime(snapshot.real_time)}。模拟审批与工单独立保存，不下发现场。</>}<small style={{ display: "block" }}>{staticData ? "固定测试场景，包含完整五类保障、跨航班资源冲突、调配方案及工单状态。" : "规则未业务确认时不推断确定延误；人工报工与源系统实绩分别标记。"}</small></div></div>{["overview", "flights"].includes(page) && <div className={s.metrics}>{[["窗口内航班", snapshot.metrics.flights, Plane], ["保障进行中", snapshot.metrics.in_progress, Clock3], ["关注航班", snapshot.metrics.attention, AlertTriangle], ["登记资源缺口", snapshot.metrics.resource_gaps, Users], ["待审批", snapshot.metrics.pending, ClipboardCheck]].map(([label, value, Icon]) => { const MetricIcon = Icon as typeof Plane; return <div className={s.metric} key={String(label)}><MetricIcon size={22} /><span>{String(label)}</span><strong>{value === null ? "—" : String(value)}</strong></div>; })}</div>}</>}
      {page === "overview" && snapshot && <div className={s.grid}><div className={s.stack}><MapPanel snapshot={snapshot} selected={selected} onSelect={choose} /></div><div className={s.stack}><Panel title="多航班保障总览" aside={<Tag>{snapshot.metrics.flights} 班</Tag>}><div className={s.between}><div className={s.tabs}><button aria-pressed={!timeline} onClick={() => setTimeline(false)}>航班保障矩阵</button><button aria-pressed={timeline} onClick={() => setTimeline(true)}>资源时序</button></div><button className={s.linkButton} style={{ marginRight: 12 }} onClick={() => setAttentionOnly(!attentionOnly)}>{attentionOnly ? "查看全部" : "只看关注"}</button></div>{timeline ? <ResourceTimeline tasks={snapshot.tasks} resources={snapshot.resources} start={snapshot.captured_at} end={snapshot.window_end} /> : <FlightMatrix snapshot={snapshot} selected={selected} onSelect={choose} search={search} attentionOnly={attentionOnly} />}{selectedFlight && <><FlightSummary flight={selectedFlight} /><div className={s.panelBody}><a href={`${href("/ops/flights")}&flight_id=${encodeURIComponent(selected)}`}>查看单航班保障详情 →</a></div></>}</Panel><Workload snapshot={snapshot} /></div><div className={s.stack}><RiskQueue snapshot={snapshot} terminal={terminal} canDispatch={canDispatch || staticData} onSelect={choose} run={run} busy={busy || staticData} /><Assist snapshot={snapshot} terminal={terminal} readOnly={staticData} /></div></div>}
      {page === "flights" && snapshot && <><Panel title="航班保障 · 点击航班下钻" aside={<a href={href("/ops")}>返回统筹总览 →</a>}><FlightMatrix snapshot={snapshot} selected={selected} onSelect={choose} search={search} /></Panel>{detail ? <section className={s.detail} aria-label="选中航班保障详情">{detailLinks.length > 0 && <div className={s.row} style={{ marginBottom: 16 }}><span className={s.muted}>已核验连班（同机场、同机号、明确关联）：</span>{detailLinks.map(f => <a key={f.id} href={`${href("/ops/flights")}&flight_id=${encodeURIComponent(f.id)}`}>{f.flight_no} · {f.flight_date} →</a>)}</div>}{detailNotes.length > 0 && <div className={s.notice}><div>{mode === "replay" ? "预演时刻之后的实绩暂不展示；这不代表源库没有记录。" : "以下源实绩未通过时序检查，图中不计为完成；未展示不代表源库没有记录。"}{detailNotes.map((n, i) => <p key={i}>{n.field}：{opsTime(n.value, true)} · {n.reason}</p>)}</div></div>}<TurnaroundGantt data={detail} /></section> : <Empty>{selected ? "正在读取指定航班记录…" : "选择一个航班查看源系统保障时序与里程碑"}</Empty>}</>}
      {page === "dispatch" && ledger && <DispatchPage readOnly={staticData} ledger={ledger} snapshot={snapshot} proposals={proposals} terminal={terminal} canDispatch={canDispatch} run={run} busy={busy} />}
      {page === "resources" && ledger && <ResourcesPage ledger={ledger} snapshot={snapshot} terminal={terminal} me={me!} canDispatch={canDispatch} run={run} busy={busy} />}
      {page === "work-orders" && me && <OrdersPage readOnly={staticData} orders={orders} events={events} resources={orderResources} manualTasks={manualTasks} sourceFlights={sourceFlights} sourceError={sourceError} terminal={terminal} canDispatch={canDispatch} canReport={!staticData && (canDispatch || me.grants.some(g => g.terminal === terminal && g.role === "crew"))} resourceIds={canDispatch ? null : me.grants.filter(g => g.terminal === terminal && g.role === "crew").map(g => g.resource_id)} run={run} busy={busy} />}
      {loading && !snapshot && !ledger && page !== "work-orders" && <Empty>正在读取授权范围内的运行数据…</Empty>}
    </>}</main>}
    {!immersive && <footer className={s.footer}><span>{staticData ? "静态 JSON 虚构测试数据 · 只读展示 · 不下发现场" : mode === "replay" ? "历史时刻预演 · 模拟台账独立保存 · 不下发现场" : "源系统业务库只读 · 本系统工单人工执行"}</span><span>未记录 ≠ 未发生</span><span>原问答页面独立保留</span></footer>}
  </div>;
}

function RiskQueue({ snapshot, terminal, onSelect, canDispatch, run, busy }: { snapshot: OpsSnapshot; terminal: string; onSelect: (id: string) => void; canDispatch: boolean; run: OpsRun; busy: boolean }) {
  return <Panel title="统筹处置队列" aside={<Tag tone="warn">{snapshot.alerts.length} 项</Tag>}><div className={s.scroll}>{snapshot.alerts.map(a => {
    const task = snapshot.tasks.find(t => a.flight_ids.includes(t.flight_id) && t.kind === a.kind && t.status === "planned");
    return <article className={s.riskCard} data-high={a.severity === "high"} key={a.id}><div className={s.between}><h3><AlertTriangle size={15} />{a.title}</h3><Tag tone={a.status === "acknowledged" ? "info" : "warn"}>{a.status === "acknowledged" ? "已确认" : a.severity === "high" ? "高优先级" : "待核查"}</Tag></div><div className={s.row} style={{ marginTop: 9 }}>{a.flight_ids.map(id => <button key={id} className={s.linkButton} onClick={() => onSelect(id)}>{snapshot.flights.find(f => f.id === id)?.flight_no || snapshot.tasks.find(t => t.flight_id === id)?.flight_no || "窗口外关联航班"}</button>)}</div><p>{a.detail}</p><div className={s.row}>{a.status !== "acknowledged" && <Button disabled={busy} onClick={() => void run(() => opsRequest(`/alerts/${a.id}/acknowledge`, terminal, {}), "已确认告警；异常仍保留直至依据解除")}>确认告警</Button>}{task && canDispatch && <a href={opsHref("/ops/dispatch", terminal, { task_id: task.id })}>查看联动方案 →</a>}</div></article>;
  })}{!snapshot.alerts.length && <Empty>当前未触发已启用规则。<br />请核对采集状态和业务规则配置。</Empty>}</div><div className={s.legend}>按优先级展示；同一冲突关联多个航班。</div></Panel>;
}

function Assist({ snapshot, terminal, readOnly = false }: { snapshot: OpsSnapshot; terminal: string; readOnly?: boolean }) {
  const [question, setQuestion] = useState(""), [answer, setAnswer] = useState(""), [busy, setBusy] = useState(false);
  if (readOnly) return <Panel title="统筹协同" aside={<Tag>静态测试摘要</Tag>}><div className={s.assist}><p>当前测试窗口 {snapshot.metrics.flights} 班航班，关注 {snapshot.metrics.attention} 班，待审批方案 {snapshot.metrics.pending} 项。</p>{snapshot.alerts.map(a => <p className={s.answer} key={a.id}>{a.title}：{a.detail}</p>)}<p className={s.muted}>以上为 JSON 测试场景说明，交互问询与写操作暂未开放。</p></div></Panel>;
  return <Panel title="统筹协同" aside={<Tag>规则事实</Tag>}><div className={s.assist}><p className={s.muted}>解释当前授权快照和已登记资源，不直接执行派工。</p>{answer && <div className={s.answer} role="status">{answer}</div>}<form className={s.form} onSubmit={async e => { e.preventDefault(); if (busy) return; setBusy(true); try { const data = await opsRequest<{ answer: string; boundary: string }>("/assist", terminal, { question, snapshot_id: snapshot.snapshot_id }); setAnswer(data.answer + "\n\n" + data.boundary); } catch (error) { setAnswer(error instanceof Error ? error.message : "协同暂不可用"); } finally { setBusy(false); } }}><label><span>运行问题</span><textarea value={question} maxLength={1000} required onChange={e => setQuestion(e.target.value)} placeholder="未来90分钟哪里需要协调？" /></label><Button type="submit" variant="primary" disabled={busy || !question.trim()}><Send size={14} />{busy ? "核对快照…" : "查看依据"}</Button></form></div></Panel>;
}
