"use client";

import dynamic from "next/dynamic";
import { useEffect, useRef, useState } from "react";
import { Activity, AlertTriangle, ArrowRight, Box, Check, ChevronRight, Circle, Crosshair, Eye, Layers3, Map, Pause, Plane, Play, Radar, RefreshCw, Route, Send, ShieldCheck, Sparkles, Users, Workflow } from "lucide-react";
import { kinds, opsHref, opsTime, type OpsSnapshot } from "@/lib/ops";
import { perceptionCommand, perceptionStandTone, perceptionStep, previewProgress, scenarioFlights } from "@/lib/ops-perception";
import { ResourceTimeline } from "./common";
import type { CameraView } from "./apron-scene";
import { ApronMonitor } from "./apron-monitor";
import s from "./perception.module.css";

const ApronScene = dynamic(() => import("./apron-scene"), { ssr: false, loading: () => <div className={s.sceneLoading}><Radar size={30} />正在构建三维示意机坪…</div> });
const phases = { scan: Radar, detect: AlertTriangle, associate: Workflow, check: ShieldCheck, proposal: Route };

export function OpsPerception({ snapshot, selected, onSelect, terminals, onTerminalChange }: { snapshot: OpsSnapshot; selected: string; onSelect: (id: string) => void; terminals: string[]; onTerminalChange: (terminal: string) => void }) {
  const config = snapshot.perception!;
  const initialScenario = config.scenarios.find(item => item.terminal === 'T3') || config.scenarios[0];
  const [scenarioId, setScenarioId] = useState(initialScenario.id), [elapsed, setElapsed] = useState(0);
  const [playing, setPlaying] = useState(false), [preview, setPreview] = useState(false), [reducedMotion, setReducedMotion] = useState(false);
  const [standCode, setStandCode] = useState(snapshot.flights.find(f => f.id === selected)?.stand || initialScenario.stand_codes[0]);
  const [view, setView] = useState<CameraView>(snapshot.terminal === "T3" ? "t3" : snapshot.terminal === "T2" ? "terminal" : "overview"), [viewRevision, setViewRevision] = useState(0);
  const [allLabels, setAllLabels] = useState(false), [standFilter, setStandFilter] = useState("all"), [after, setAfter] = useState(false);
  const [trafficPlaying, setTrafficPlaying] = useState(true);
  const [command, setCommand] = useState(''), [commandNote, setCommandNote] = useState('');
  const feed = useRef<HTMLDivElement>(null), previousStep = useRef(-1);
  const scenario = config.scenarios.find(item => item.id === scenarioId) || config.scenarios[0];
  const stepIndex = perceptionStep(scenario, elapsed), currentStep = scenario.steps[stepIndex];
  const lastAt = scenario.steps.at(-1)!.at_ms, progress = previewProgress(scenario, elapsed, preview);
  const proposal = snapshot.proposals?.find(p => p.id === scenario.proposal_id);
  const focused = snapshot.flights.find(f => f.stand === standCode);
  const related = scenarioFlights(snapshot, scenario);
  const detected = stepIndex >= 1, recommended = stepIndex === scenario.steps.length - 1 && !!proposal;
  const relatedIds = new Set(related.map(f => f.id));
  const tasks = snapshot.tasks.filter(t => relatedIds.has(t.flight_id) && (scenario.resource_ids.includes(t.resource_id) || !proposal && t.kind === snapshot.alerts.find(a => a.id === scenario.alert_id)?.kind));
  const resources = snapshot.resources.filter(r => scenario.resource_ids.includes(r.id) || tasks.some(t => t.resource_id === r.id));
  const timelineStart = new Date(Date.parse(snapshot.captured_at) - 12 * 60000).toISOString();
  const timelineEnd = new Date(Date.parse(snapshot.captured_at) + 58 * 60000).toISOString();

  useEffect(() => {
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    const apply = () => { setReducedMotion(media.matches); setPlaying(!media.matches); if (media.matches) setElapsed(lastAt); };
    apply(); media.addEventListener('change', apply); return () => media.removeEventListener('change', apply);
  }, [lastAt]);
  useEffect(() => {
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    const apply = () => setTrafficPlaying(!media.matches);
    apply(); media.addEventListener('change', apply); return () => media.removeEventListener('change', apply);
  }, []);

  useEffect(() => {
    if (!playing) return;
    let previous = performance.now();
    const timer = window.setInterval(() => {
      const now = performance.now(), delta = now - previous; previous = now;
      if (document.hidden) return;
      setElapsed(old => Math.min(lastAt + (preview ? 8000 : 0), old + Math.min(delta, 500)));
    }, 200);
    return () => window.clearInterval(timer);
  }, [playing, lastAt, preview]);
  useEffect(() => { if (elapsed >= lastAt + (preview ? 8000 : 0)) setPlaying(false); }, [elapsed, lastAt, preview]);
  useEffect(() => {
    if (previousStep.current !== stepIndex) {
      previousStep.current = stepIndex;
      feed.current?.scrollTo({ top: feed.current.scrollHeight, behavior: 'auto' });
    }
  }, [stepIndex, currentStep.focus_stand, reducedMotion]);
  useEffect(() => { const flight = snapshot.flights.find(f => f.id === selected); if (flight) setStandCode(flight.stand); }, [selected, snapshot.snapshot_id]);

  function camera(next: CameraView) { setView(next); setViewRevision(x => x + 1); }
  function choose(code: string) { setPlaying(false); setStandCode(code); onSelect(snapshot.flights.find(f => f.stand === code)?.id || ''); camera('focus'); }
  function reset(id = scenario.id) {
    const next = config.scenarios.find(item => item.id === id)!;
    setScenarioId(id); setElapsed(reducedMotion ? next.steps.at(-1)!.at_ms : 0); setPreview(false); setAfter(false); setPlaying(!reducedMotion);
    setStandCode(next.stand_codes[0]); previousStep.current = -1; camera('overview');
  }
  function startPreview() {
    setPreview(true); setAfter(true); setElapsed(reducedMotion ? lastAt + 8000 : lastAt); setPlaying(!reducedMotion);
    setStandCode(scenario.route_stands[1] || scenario.stand_codes[0]); camera(scenario.terminal === 'T3' ? 't3' : 'terminal');
  }
  function submitCommand(event: React.FormEvent) {
    event.preventDefault(); const value = command.trim(); if (!value) return;
    const action = perceptionCommand(snapshot, value);
    if (action.type === 'stand') { choose(action.code); setCommandNote(`已定位 ${action.code} 机位，可查看左侧保障节点。`); }
    else if (action.type === 'camera') { setPlaying(false); camera(action.view); setCommandNote('已切换场景视角。'); }
    else if (action.type === 'scenario') { reset(action.id); setCommandNote('正在播放所选感知场景。'); }
    else setCommandNote('演示支持机位、航班号、全景、俯视及预设场景；自由问询请使用原智能问询。');
    setCommand('');
  }

  return <section className={s.root} aria-label="智能感知演示工作台">
    <div className={s.commandBar}>
      <div className={s.heading}><span className={s.headingIcon}><Radar size={23} /></span><div><h1>机场运行数字孪生 <span>A-CDM COPILOT</span></h1><p>T3 / T2 空间示意 · 多航班感知与协同调度</p></div></div>
      <div className={s.controls}><button aria-pressed={view === 'map'} onClick={() => { setPlaying(false); camera('map'); }}><Map size={14} />2D 俯视</button><button aria-pressed={view === 'overview'} onClick={() => { setPlaying(false); camera('overview'); }}><Box size={14} />3D 全景</button><button aria-pressed={view === 'macro'} onClick={() => { setPlaying(false); camera('macro'); }}><Route size={14} />全场路网</button><button aria-pressed={view === 'terminal'} onClick={() => { setPlaying(false); camera('terminal'); }} disabled={snapshot.terminal === 'T3'}>T2 保障区</button><button aria-pressed={view === 't3'} disabled={snapshot.terminal === 'T2'} onClick={() => { setPlaying(false); camera('t3'); }}>T3 保障区</button><button onClick={() => { setPlaying(false); camera('focus'); }} aria-label="聚焦当前机位"><Crosshair size={14} /></button><span className={s.controlDivider} /><button aria-pressed={allLabels} onClick={() => setAllLabels(!allLabels)}><Layers3 size={14} />{allLabels ? '全部标签' : '精简标签'}</button><button onClick={() => reset()} aria-label="重播场景"><RefreshCw size={14} /></button></div>
      <div className={s.testClock}><strong>{opsTime(snapshot.captured_at)}<small> +{Math.floor(elapsed / 1000)}s 演示</small></strong><span>{snapshot.captured_at.slice(0, 10)} / 固定测试快照</span></div>
    </div>
    <div className={s.stage}>
      <div className={s.scene}><ApronScene snapshot={snapshot} scenario={scenario} selected={standCode} detected={detected} recommended={recommended} progress={progress} preview={preview} playing={playing} trafficPlaying={trafficPlaying} reducedMotion={reducedMotion} allLabels={allLabels} view={view} viewRevision={viewRevision} onSelect={choose} onInteract={() => setPlaying(false)} /></div>
      <div className={s.sceneMetrics}>{[["窗口内航班", snapshot.metrics.flights, Plane], ["保障进行中", snapshot.metrics.in_progress, Activity], ["风险事件", snapshot.alerts.length, AlertTriangle], ["登记班组", snapshot.resources.length, Users]].map(([label, number, Icon]) => { const Glyph = Icon as typeof Plane; return <div key={String(label)}><Glyph size={17} /><span>{String(label)}</span><strong>{String(number)}</strong></div>; })}</div>
      <aside className={`${s.glass} ${s.leftPanel}`} aria-label="机位与保障详情">
        <div className={s.panelHeading}><span><Layers3 size={14} />机位导航与保障节点</span><small>{snapshot.terminal === "ALL" ? "T2 / T3" : snapshot.terminal} · {snapshot.stands.filter(item => !item.kind).length} 机位</small></div>
        <label className={s.terminalScope}>监控范围<select aria-label="监控航站楼" value={snapshot.terminal} onChange={e => onTerminalChange(e.target.value)}>{terminals.map(t => <option key={t} value={t}>{t === "ALL" ? "全场 · T2 / T3" : t + " 航站楼"}</option>)}</select></label><div className={s.filters}>{[['all', '全部机位'], ['risk', '场景关联'], ['free', '空闲']].map(([id, name]) => <button key={id} aria-pressed={standFilter === id} onClick={() => setStandFilter(id)}>{name}</button>)}</div>
        <div className={s.standGrid}>{snapshot.stands.filter(item => standFilter === 'risk' ? scenario.stand_codes.includes(item.code) : standFilter === 'free' ? !snapshot.flights.some(f => f.stand === item.code) : true).map(stand => {
          const occupied = snapshot.flights.some(f => f.stand === stand.code), tone = perceptionStandTone(scenario, stand.code, detected, recommended, preview);
          return <button key={stand.code} aria-pressed={standCode === stand.code} aria-label={`${stand.code}机位，${occupied ? '有航班' : '空闲'}`} data-state={tone !== 'normal' ? tone : occupied ? 'occupied' : 'free'} onClick={() => choose(stand.code)}>{stand.code}<i /></button>;
        })}</div>
        <div className={s.flightHeading}><div><strong>{standCode} <small>机位</small></strong><span>{focused ? `${focused.terminal} · ${focused.flight_no} · ${focused.aircraft_reg}` : '测试场景空闲机位'}</span></div><span className={s.tobt}>TOBT<br /><b>{opsTime(focused?.tobt)}</b></span></div>
        <div className={s.aircraftStatus} data-state={focused?.aircraft_state?.code}><i />{focused?.aircraft_state?.label || "机位空闲"}<small>测试状态</small></div><div className={s.sensing}><Eye size={16} /><div><strong>机坪 AI 感知 · 演示信号</strong><span>作业节点识别 / 车辆位置 / 班组占用</span></div><span className={s.signal}>模拟</span></div>
        <div className={s.operationList}>{focused?.operations.map((op, index) => <div className={s.operation} key={op.kind} data-state={op.status}><span className={s.nodeNumber}>{String(index + 1).padStart(2, '0')}</span><div><strong>{op.name}</strong><span>计划 {opsTime(op.plan_start)}—{opsTime(op.plan_end)}</span><small>{op.actual_end ? `测试结束 ${opsTime(op.actual_end)}` : op.actual_start ? `测试开始 ${opsTime(op.actual_start)}` : '计划节点，尚未开始'}</small></div><span>{op.status === 'completed' ? <Check size={15} /> : op.status === 'risk' ? <AlertTriangle size={15} /> : op.status === 'in_progress' ? <Activity size={15} /> : <Circle size={12} />}{op.status_label}</span></div>) || <p className={s.empty}>此机位未配置测试航班，可选择其他机位。</p>}</div>
        {focused && <a className={s.detailLink} href={opsHref('/ops/flights', snapshot.terminal, { flight_id: focused.id })}>查看完整保障时序 <ChevronRight size={15} /></a>}
      </aside>
      <aside className={`${s.glass} ${s.rightPanel}`} aria-label="智能感知协同">
        <div className={s.panelHeading}><span><span className={s.breath} />A-CDM 智能协同中枢</span><small className={s.aiBadge}>COPILOT</small></div>
        <div className={s.scenarioHeading}><Sparkles size={12} />选择场景，观察感知与调度过程</div>
        <div className={s.scenarios}>{config.scenarios.map((item, index) => <button key={item.id} data-kind={item.id} aria-pressed={scenario.id === item.id} onClick={() => reset(item.id)}><span>0{index + 1}</span>{item.title}</button>)}</div>
        <div className={s.playback}><span><i className={playing ? s.playingDot : s.pausedDot} />{playing ? preview ? '播放调配预演' : '巡检分析中' : preview && progress === 1 ? '预演完成 · 待人工核查' : stepIndex === scenario.steps.length - 1 ? '建议已生成' : '演示已暂停'}</span><button disabled={reducedMotion} onClick={() => { if (playing) setPlaying(false); else if (elapsed >= lastAt + (preview ? 8000 : 0)) reset(); else setPlaying(true); }} aria-label={playing ? '暂停演示' : '继续演示'}>{playing ? <Pause size={14} /> : <Play size={14} />}{playing ? '暂停' : '继续'}</button></div>
        <div className={s.feed} ref={feed} role="log" aria-label="感知与分析记录" aria-live="polite">{scenario.steps.slice(0, stepIndex + 1).map(step => { const Icon = phases[step.phase]; return <article key={step.phase} className={s.eventCard} data-phase={step.phase}><div><Icon size={15} /><strong>{step.title}</strong><time>+{(step.at_ms / 1000).toFixed(1)}s</time></div><p>{step.description}</p>{step.phase === 'associate' && <div className={s.flightChips}>{related.map(f => <button key={f.id} onClick={() => choose(f.stand)}>{f.flight_no}<small>{f.stand}</small></button>)}</div>}{step.phase === 'check' && proposal && <div className={s.checks}>{['资质与设备', '班组在岗', '任务无交叠', '前后转场'].map(text => <span key={text}><Check size={12} />{text}</span>)}</div>}</article>; })}
          {recommended && proposal && <div className={s.proposal}><span>跨航班调配预演</span><h3>{proposal.target.flight_no} · {kinds[proposal.target.kind]}改派</h3><div className={s.resourceChange}><span>{snapshot.resources.find(r => r.id === proposal.target.resource_id)?.name.replace('（测试）', '')}</span><ArrowRight size={15} /><strong>{proposal.resource.name.replace('（测试）', '')}</strong></div><div className={s.slack}><div><b>{proposal.check.transfer_before}<small>分钟</small></b><span>到场转场</span></div><div><b>{proposal.check.transfer_after}<small>分钟</small></b><span>后续转场</span></div><div><b>{proposal.check.slack_minutes}<small>分钟</small></b><span>衔接余量</span></div></div><button className={s.primary} disabled={playing && preview} onClick={startPreview}><Route size={15} />{preview && progress === 1 ? '重新播放调配预演' : '播放调配预演 · 不派单'}</button><a href={opsHref('/ops/dispatch', snapshot.terminal, { proposal_id: proposal.id })}>查看前后排程与审批卡片 <ChevronRight size={14} /></a></div>}
          {stepIndex === scenario.steps.length - 1 && !proposal && <a className={s.detailLink} href={opsHref('/ops/work-orders', snapshot.terminal)}>查看工单处置记录 <ChevronRight size={15} /></a>}
        </div>
        {commandNote && <p className={s.commandNote} role="status">{commandNote}</p>}
        <form className={s.commandInput} onSubmit={submitCommand}><input aria-label="演示指令" placeholder="定位 201、CZ3103，或输入俯视…" value={command} onChange={event => setCommand(event.target.value)} /><button type="submit" aria-label="执行演示指令"><Send size={15} /></button></form>
        <div className={s.guard}><ShieldCheck size={12} /><span>静态测试演示 · 无实时感知 · 不下发现场</span></div>
      </aside>
      <ApronMonitor snapshot={snapshot} playing={trafficPlaying} reducedMotion={reducedMotion} onToggle={() => setTrafficPlaying(old => !old)} onSelect={choose} /><div className={s.sceneHint}><span><i className={s.blueDot} />在港</span><span><i className={s.redDot} />关联风险</span><span><i className={s.greenDot} />调配预演</span><em>左键平移 · 右键旋转 · 滚轮指向缩放</em></div>
    </div>
    <div className={s.bottomDock}>
      <section className={s.timeline}><div className={s.panelHeading}><span><Workflow size={15} />跨航班资源排程 <small>A-CDM TIMELINE</small></span><div className={s.timelineTabs}><button aria-pressed={!after} onClick={() => setAfter(false)}>现有安排</button><button disabled={!proposal} aria-pressed={after} onClick={() => setAfter(true)}>调配预演</button></div></div><ResourceTimeline compact tasks={tasks} resources={resources} proposal={proposal} after={after} start={timelineStart} end={timelineEnd} /></section>
      <section className={s.evidence}><div className={s.panelHeading}><span><Radar size={15} />感知证据链</span><small>固定测试快照</small></div><div className={s.evidenceSteps}>{scenario.steps.map((step, i) => <span key={step.phase} data-active={i <= stepIndex}>{i < stepIndex ? <Check size={12} /> : <b>{i + 1}</b>}{['巡检', '发现', '关联', '检查', '建议'][i]}</span>)}</div><p>{preview ? progress < .35 ? `模拟班组由${scenario.route_stands[0]}前往${scenario.route_stands[1]}机位。` : progress < .55 ? '模拟作业中：20分钟保障窗口采用压缩动画展示。' : progress < 1 ? `模拟班组前往${scenario.route_stands[2]}机位，检查后续航班任务。` : '预演完成，现场状态及正式工单均未改变。' : currentStep.description}</p><div className={s.previewTrack}><i style={{ transform: `scaleX(${preview ? progress : (stepIndex + 1) / scenario.steps.length})` }} /></div></section>
    </div>
    <div className={s.disclaimer}><span><i /> {config.layout_note}</span><a href={opsHref('/ops/flights', snapshot.terminal)}>多航班保障矩阵 <ChevronRight size={12} /></a></div>
  </section>;
}
