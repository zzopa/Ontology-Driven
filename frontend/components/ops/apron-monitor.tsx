import { Activity, Pause, Play } from "lucide-react";
import { kinds, type Kind, type OpsSnapshot } from "@/lib/ops";
import { apronMonitor } from "@/lib/ops-perception";
import s from "./perception.module.css";

export function ApronMonitor({ snapshot, playing, reducedMotion, onToggle, onSelect }: {
  snapshot: OpsSnapshot; playing: boolean; reducedMotion: boolean; onToggle: () => void; onSelect: (code: string) => void;
}) {
  const data = apronMonitor(snapshot), total = snapshot.flights.length;
  let offset = 0;
  return <section className={s.monitor} aria-label="机坪监控图表">
    <header><span><Activity size={13} />机坪监控 <small>静态统计 · 循环动效演示</small></span><button onClick={onToggle} disabled={reducedMotion} aria-label={playing ? '暂停飞机动效' : '播放飞机动效'}>{playing ? <Pause size={12} /> : <Play size={12} />}{playing ? '暂停动效' : '播放动效'}</button></header>
    <div className={s.monitorBody}>
      <div className={s.donut}><svg viewBox="0 0 100 100" role="img" aria-label={`${total}班测试航班的状态分布`}><circle cx="50" cy="50" r="38" fill="none" stroke="#1b2d42" strokeWidth="8" />{data.states.map(item => {
        const length = total ? item.count / total * 100 : 0, start = offset; offset += length;
        return <circle key={item.code} cx="50" cy="50" r="38" fill="none" stroke={item.color} strokeWidth="8" pathLength="100" strokeDasharray={`${length} ${100 - length}`} strokeDashoffset={-start} transform="rotate(-90 50 50)" />;
      })}<text x="50" y="49" textAnchor="middle">{total}</text><text x="50" y="64" textAnchor="middle" className={s.donutSub}>测试航班</text></svg><small>机位占用 {data.occupied}/{data.stands}</small></div>
      <div className={s.stateGrid}>{data.states.map(item => <button key={item.code} disabled={!item.count} onClick={() => { const flight = snapshot.flights.find(f => (f.aircraft_state?.code || 'parked') === item.code); if (flight) onSelect(flight.stand); }} title={`定位${item.label}航班`}><i style={{ background: item.color }} /><span>{item.label}</span><b>{item.count}</b></button>)}</div>
      <div className={s.operationChart}>{data.operations.map(item => <div key={item.kind}><span>{kinds[item.kind as Kind]}</span><div role="img" aria-label={`${kinds[item.kind as Kind]}：完成${item.completed}，进行${item.active}，异常${item.risk}，待开始${item.planned}`}>{[['done',item.completed],['active',item.active],['risk',item.risk],['planned',item.planned]].map(([state,count]) => <i key={state} data-state={state} style={{ width: `${item.total ? Number(count) / item.total * 100 : 0}%` }} />)}</div><small>{item.completed}/{item.total}</small></div>)}<p><i />完成 <i />进行中 <i />异常 <i />待开始</p></div>
    </div>
  </section>;
}
