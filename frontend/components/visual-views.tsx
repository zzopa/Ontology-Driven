"use client";

import { useMemo, useState } from "react";
import { ArrowRight, ArrowUpRight, BarChart3, ChartGantt, Clock3, PieChart, Plane, Search, Table2, TrendingUp } from "lucide-react";
import { formatValue } from "@/lib/query";
import type { Row, VisualDetailFlightRow, VisualDetailTable, VisualMeta } from "@/lib/types";
import { Badge, Button } from "./ui";
import v from "./visual-views.module.css";

const FALLBACK_PALETTE = [
  "#2563eb",
  "#10b981",
  "#f59e0b",
  "#8b5cf6",
  "#ec4899",
  "#06b6d4",
  "#f97316",
  "#64748b",
];

type StatsMeta = Extract<VisualMeta, { kind: "stats" }>;
type DonutMeta = Extract<VisualMeta, { kind: "donut" }>;
type BarMeta = Extract<VisualMeta, { kind: "bar" }>;
type TrendMeta = Extract<VisualMeta, { kind: "trend" }>;

function parseNumeric(raw: string): number | null {
  const cleaned = raw.replace(/,/g, "").trim();
  if (!cleaned || !/^-?\d+(?:\.\d+)?$/.test(cleaned)) return null;
  const n = Number(cleaned);
  return Number.isFinite(n) ? n : null;
}

export function StatsVisual({ meta }: { meta: StatsMeta }) {
  const primary = meta.stats[0];
  const secondary = meta.stats.slice(1);
  if (!primary) return null;
  const pctNum = primary.unit === "%" ? parseNumeric(primary.value) : null;
  const showGauge = pctNum !== null && pctNum >= 0 && pctNum <= 100;

  return (
    <section className={v.card} aria-label={meta.title}>
      <div className={v.header}>
        <div className={v.titleGroup}>
          <span className={v.iconMark}><BarChart3 size={15} aria-hidden="true" /></span>
          <h4 className={v.title}>{meta.title}</h4>
        </div>
        <Badge tone="green">核心指标</Badge>
      </div>
      <div className={`${v.statsWrap} ${secondary.length === 0 ? v.statsWrapSingle : ""}`}>
        <div className={v.heroStat}>
          <span className={v.heroLabel}>{primary.label}</span>
          <div className={v.heroValueRow}>
            <strong className={v.heroValue}>{primary.value}</strong>
            {primary.unit && <span className={v.heroUnit}>{primary.unit}</span>}
          </div>
          {showGauge && (
            <div className={v.gaugeTrack} role="progressbar" aria-valuenow={pctNum} aria-valuemin={0} aria-valuemax={100} aria-label={primary.label}>
              <div className={v.gaugeFill} style={{ width: `${pctNum}%` }} />
            </div>
          )}
        </div>
        {secondary.length > 0 && (
          <div className={v.subStatsGrid}>
            {secondary.map((item, i) => (
              <div className={v.subStatCard} key={i}>
                <span className={v.subStatLabel}>{item.label}</span>
                <div className={v.subStatVal}>
                  <strong>{item.value}</strong>
                  {item.unit && <small>{item.unit}</small>}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

export function DonutVisual({ meta }: { meta: DonutMeta }) {
  const [active, setActive] = useState<number | null>(null);
  const radius = 40;
  const circumference = 2 * Math.PI * radius;
  let accumulated = 0;
  const activeSlice = active !== null ? meta.slices[active] : null;

  return (
    <section className={v.card} aria-label={meta.title}>
      <div className={v.header}>
        <div className={v.titleGroup}>
          <span className={v.iconMark}><PieChart size={15} aria-hidden="true" /></span>
          <h4 className={v.title}>{meta.title}</h4>
        </div>
        <div className={v.headerMeta}>
          <Badge>{meta.slices.length} 类分布</Badge>
          <Badge tone="green">合计 {meta.total.toLocaleString()} {meta.unit}</Badge>
        </div>
      </div>
      <div className={v.donutWrap}>
        <div className={v.donutStage}>
          <svg className={v.donutSvg} viewBox="0 0 100 100" role="img" aria-label={meta.title}>
            <circle cx="50" cy="50" r={radius} fill="transparent" stroke="var(--line)" strokeWidth="14" />
            {meta.slices.map((slice, i) => {
              const dash = (slice.percent / 100) * circumference;
              const offset = (accumulated / 100) * circumference;
              accumulated += slice.percent;
              const isFocus = active === null || active === i;
              return (
                <circle
                  key={i}
                  cx="50"
                  cy="50"
                  r={radius}
                  fill="transparent"
                  stroke={slice.color || FALLBACK_PALETTE[i % FALLBACK_PALETTE.length]}
                  strokeWidth={active === i ? 16.5 : 14}
                  strokeDasharray={`${dash} ${Math.max(0, circumference - dash)}`}
                  strokeDashoffset={-offset}
                  opacity={isFocus ? 1 : 0.35}
                  style={{ cursor: "pointer", transition: "opacity .15s ease, stroke-width .15s ease" }}
                  onMouseEnter={() => setActive(i)}
                  onMouseLeave={() => setActive(null)}
                />
              );
            })}
          </svg>
          <div className={v.donutCenter}>
            <span className={v.donutCenterLabel}>{activeSlice ? activeSlice.label : "总计"}</span>
            <strong className={v.donutCenterVal}>
              {activeSlice ? activeSlice.value.toLocaleString() : meta.total.toLocaleString()}
            </strong>
            <span className={v.donutCenterSub}>
              {activeSlice ? `${activeSlice.percent}% · ${meta.unit}` : meta.unit || meta.group_label}
            </span>
          </div>
        </div>
        <div className={v.donutLegend}>
          {meta.slices.map((slice, i) => (
            <button
              type="button"
              key={i}
              className={`${v.legendRow} ${active === i ? v.legendRowActive : ""}`}
              onMouseEnter={() => setActive(i)}
              onMouseLeave={() => setActive(null)}
              onFocus={() => setActive(i)}
              onBlur={() => setActive(null)}
            >
              <span className={v.legendLeft}>
                <span className={v.legendDot} style={{ backgroundColor: slice.color || FALLBACK_PALETTE[i % FALLBACK_PALETTE.length] }} />
                <span className={v.legendName} title={slice.label}>{slice.label}</span>
              </span>
              <span className={v.legendRight}>
                <strong>{slice.value.toLocaleString()} {meta.unit}</strong>
                <span className={v.legendPct}>{slice.percent}%</span>
              </span>
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}

export function BarVisual({ meta }: { meta: BarMeta }) {
  const maxVal = Math.max(...meta.bars.map(b => b.value), 1);
  const scaleBase = (meta.is_rate || meta.unit === "%") && maxVal <= 100 ? 100 : maxVal;

  return (
    <section className={v.card} aria-label={meta.title}>
      <div className={v.header}>
        <div className={v.titleGroup}>
          <span className={v.iconMark}><BarChart3 size={15} aria-hidden="true" /></span>
          <h4 className={v.title}>{meta.title}</h4>
        </div>
        <Badge>{meta.bars.length} 个{meta.group_label || "分类"}</Badge>
      </div>
      <div className={v.barsWrap}>
        {meta.bars.map((bar, i) => {
          const widthPct = Math.min(100, Math.max(2, (bar.value / scaleBase) * 100));
          const fill = bar.color || FALLBACK_PALETTE[i % FALLBACK_PALETTE.length];
          const showPct = bar.percent > 0 && meta.unit !== "%";
          return (
            <div key={i} className={v.barRow}>
              <div className={v.barHead}>
                <div className={v.barLabelGroup}>
                  <span className={`${v.rankPill} ${i < 3 ? v.rankTop : ""}`}>{String(i + 1).padStart(2, "0")}</span>
                  <span className={v.barName} title={bar.label}>{bar.label}</span>
                </div>
                <span className={v.barVal}>
                  <strong>{bar.value.toLocaleString()}</strong> {meta.unit}
                  {showPct ? ` (${bar.percent}%)` : ""}
                </span>
              </div>
              <div className={v.barTrack}>
                <div className={v.barFill} style={{ width: `${widthPct}%`, backgroundColor: fill }} />
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

export function TrendVisual({ meta }: { meta: TrendMeta }) {
  const [hovered, setHovered] = useState<number | null>(null);
  const maxVal = Math.max(...meta.series.map(s => s.value), 1);
  const avgVal = meta.series.length
    ? Math.round((meta.series.reduce((sum, s) => sum + s.value, 0) / meta.series.length) * 10) / 10
    : 0;
  const peakPoint = meta.series.reduce(
    (best, pt) => (pt.value > best.value ? pt : best),
    meta.series[0] || { label: "—", value: 0 },
  );
  const activePoint = hovered !== null ? meta.series[hovered] : null;
  const dense = meta.series.length > 14;

  return (
    <section className={v.card} aria-label={meta.title}>
      <div className={v.header}>
        <div className={v.titleGroup}>
          <span className={v.iconMark}><TrendingUp size={15} aria-hidden="true" /></span>
          <h4 className={v.title}>{meta.title}</h4>
        </div>
        <div className={v.trendStats}>
          <Badge>{meta.metric_label || "趋势"}</Badge>
          {meta.series.length > 0 && (
            <>
              <Badge tone="green">峰值 {peakPoint.value.toLocaleString()} {meta.unit} ({peakPoint.label})</Badge>
              <Badge>均值 {avgVal.toLocaleString()} {meta.unit}</Badge>
            </>
          )}
        </div>
      </div>
      <div className={v.trendPlotShell}>
        <div className={v.trendPlotArea}>
          <span className={v.trendGridLine} style={{ top: 20 }} aria-hidden="true" />
          <span className={v.trendGridLine} style={{ top: "55%" }} aria-hidden="true" />
          {meta.series.map((pt, i) => {
            const heightPct = Math.min(100, Math.max(4, (pt.value / maxVal) * 100));
            const isPeak = pt.value === peakPoint.value && pt.value > 0;
            const showValueAbove = !dense || isPeak || hovered === i;
            return (
              <button
                type="button"
                key={i}
                className={`${v.trendCol} ${isPeak ? v.trendColPeak : ""} ${hovered === i ? v.trendColActive : ""}`}
                onMouseEnter={() => setHovered(i)}
                onMouseLeave={() => setHovered(null)}
                onFocus={() => setHovered(i)}
                onBlur={() => setHovered(null)}
                aria-label={`${pt.label}: ${pt.value.toLocaleString()} ${meta.unit}`}
              >
                {showValueAbove && <span className={v.trendPillarVal}>{pt.value.toLocaleString()}</span>}
                <div className={v.trendPillar} style={{ height: `${heightPct}%` }} />
              </button>
            );
          })}
        </div>
        <div className={v.trendLabelsRow}>
          {meta.series.map((pt, i) => {
            const showLabel = !dense || i === 0 || i === meta.series.length - 1 || i % 2 === 0 || hovered === i;
            return (
              <span key={i} className={v.trendLabelCell} title={pt.label}>
                {showLabel ? pt.label : ""}
              </span>
            );
          })}
        </div>
      </div>
      {activePoint && (
        <div className={v.trendHoverBar}>
          <span>当前查看时段：<strong>{activePoint.label}</strong></span>
          <span>数值：<strong>{activePoint.value.toLocaleString()} {meta.unit}</strong></span>
        </div>
      )}
    </section>
  );
}

const FLIGHT_STATUS_MAP: Record<string, string> = {
  DEP: "已起飞",
  ARR: "已到达",
  DLY: "延误",
  CNL: "取消",
  CAN: "取消",
  CANCEL: "取消",
  CANCELLED: "取消",
  CANCELED: "取消",
  SCH: "计划",
  PLN: "计划",
  SCHEDULED: "计划",
  PLANNED: "计划",
  BOR: "登机中",
  BDG: "登机中",
  BOARDING: "登机中",
  LBD: "催促登机",
  CKI: "值机中",
  CHECKIN: "值机中",
  POK: "下机中",
  TXI: "滑行中",
  TAXIING: "滑行中",
  ONR: "飞行中",
  ENROUTE: "飞行中",
  ALT: "备降",
  DIV: "备降",
  DIVERTED: "备降",
  RTN: "返航",
  RET: "返航",
  RETURNED: "返航",
  POF: "客齐",
  CLS: "关门",
  RDY: "就绪",
};

const AIRPORT_IATA_MAP: Record<string, string> = {
  WUH: "武汉", PKX: "北京大兴", PEK: "北京首都",
  PVG: "上海浦东", SHA: "上海虹桥", CAN: "广州白云",
  SZX: "深圳宝安", CTU: "成都双流", TFU: "成都天府",
  CKG: "重庆江北", KMG: "昆明长水", XIY: "西安咸阳",
  HGH: "杭州萧山", NKG: "南京禄口", CGO: "郑州新郑",
  CSX: "长沙黄花", XMN: "厦门高崎", TAO: "青岛胶东",
  DLC: "大连周水子", SHE: "沈阳桃仙", URC: "乌鲁木齐地窝堡",
  HAK: "海口美兰", SYX: "三亚凤凰", TSN: "天津滨海",
  FOC: "福州长乐", NNG: "南宁吴圩", KWE: "贵阳龙洞堡",
  HRB: "哈尔滨太平", CGQ: "长春龙嘉", TYN: "太原武宿",
  HET: "呼和浩特白塔", LHW: "兰州中川", INC: "银川河东",
  XNN: "西宁曹家堡", LXA: "拉萨贡嘎", HKG: "香港国际",
  MFM: "澳门国际", TPE: "台北桃园",
};

function formatFlightStatus(status?: string, actTime?: string, dir?: string): string {
  if (!status || status === "—") {
    if (actTime && actTime !== "—") {
      return dir === "ARR" ? "已到达" : "已起飞";
    }
    return "计划";
  }
  const upper = status.trim().toUpperCase();
  if (FLIGHT_STATUS_MAP[upper]) {
    if (upper === "DEP") return actTime && actTime !== "—" ? "已起飞" : "出港";
    if (upper === "ARR") return actTime && actTime !== "—" ? "已到达" : "进港";
    return FLIGHT_STATUS_MAP[upper];
  }
  return status;
}

function formatAirport(nameOrCode?: string): string {
  if (!nameOrCode || nameOrCode === "—") return "—";
  const c = nameOrCode.trim().toUpperCase();
  return AIRPORT_IATA_MAP[c] || nameOrCode;
}

function isFlightDetailRow(row: VisualDetailFlightRow | Row): row is VisualDetailFlightRow {
  return typeof (row as VisualDetailFlightRow).flight_no === "string";
}

export function DetailTableVisual({
  meta,
  onQuestion,
  onOpenAllRecords,
}: {
  meta: VisualDetailTable;
  onQuestion?: (question: string) => void;
  onOpenAllRecords?: () => void;
}) {
  const [search, setSearch] = useState("");
  const [dirFilter, setDirFilter] = useState<"ALL" | "DEP" | "ARR">("ALL");
  const [delayedOnly, setDelayedOnly] = useState(false);
  const [selectedHour, setSelectedHour] = useState<number | null>(null);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return meta.rows.filter(row => {
      if (meta.is_flight && isFlightDetailRow(row)) {
        if (dirFilter !== "ALL" && row.direction !== dirFilter) return false;
        if (delayedOnly) {
          const isDelayed = (row.delay_minutes != null && row.delay_minutes > 15) || row.status === "延误" || row.status === "DLY";
          if (!isDelayed) return false;
        }
        if (selectedHour !== null) {
          const t = row.actual_time && row.actual_time !== "—" ? row.actual_time : row.plan_time && row.plan_time !== "—" ? row.plan_time : "";
          const timeSegment = t.includes(" ") ? t.split(" ")[t.split(" ").length - 1] : t;
          const h = timeSegment.includes(":") ? parseInt(timeSegment.split(":")[0], 10) : -1;
          if (h !== selectedHour) return false;
        }
        if (q) {
          const hay = [
            row.flight_no,
            row.airline,
            row.airline_name,
            row.origin,
            formatAirport(row.origin),
            row.destination,
            formatAirport(row.destination),
            row.route,
            row.stand,
            row.gate,
            row.aircraft_type,
            row.aircraft_reg,
            row.status,
            formatFlightStatus(row.status, row.actual_time, row.direction),
            row.delay_reason,
          ].filter(Boolean).join(" ").toLowerCase();
          if (!hay.includes(q)) return false;
        }
        return true;
      }
      if (!q) return true;
      return Object.values(row).some(val => String(val ?? "").toLowerCase().includes(q));
    });
  }, [meta.is_flight, meta.rows, search, dirFilter, delayedOnly, selectedHour]);

  const partial = meta.shown < meta.total;
  const summary = meta.summary;
  const hourly = useMemo(() => {
    if (meta.is_flight && meta.rows.length > 0) {
      const counts: Record<number, number> = {};
      for (const row of meta.rows) {
        if (!isFlightDetailRow(row)) continue;
        const t = row.actual_time && row.actual_time !== "—" ? row.actual_time : row.plan_time && row.plan_time !== "—" ? row.plan_time : "";
        const timeSegment = t.includes(" ") ? t.split(" ")[t.split(" ").length - 1] : t;
        const h = timeSegment.includes(":") ? parseInt(timeSegment.split(":")[0], 10) : -1;
        if (h >= 0 && h <= 23) {
          counts[h] = (counts[h] || 0) + 1;
        }
      }
      if (Object.keys(counts).length > 0) {
        return Array.from({ length: 24 }, (_, h) => ({
          hour: h,
          label: `${String(h).padStart(2, "0")}时`,
          count: counts[h] || 0,
        }));
      }
    }
    return summary?.hourly_distribution || [];
  }, [meta.is_flight, meta.rows, summary?.hourly_distribution]);
  const maxHourCount = Math.max(...hourly.map(h => h.count), 1);
  const peakHour = hourly.reduce(
    (best, cur) => (cur.count > best.count ? cur : best),
    hourly[0] || { hour: 0, label: "—", count: 0 },
  );

  return (
    <section className={v.card} aria-label={meta.title}>
      <div className={v.header}>
        <div className={v.titleGroup}>
          <span className={v.iconMark}>
            {meta.is_flight ? <Plane size={15} aria-hidden="true" /> : <Table2 size={15} aria-hidden="true" />}
          </span>
          <h4 className={v.title}>{meta.title}</h4>
        </div>
        <div className={v.headerMeta}>
          <Badge tone="green">共 {meta.total.toLocaleString()} 条</Badge>
          <Badge>{partial ? `展示前 ${meta.shown} 条` : "已展示全部"}</Badge>
          {onOpenAllRecords && (
            <Button variant="secondary" onClick={onOpenAllRecords} style={{ minHeight: 30, padding: "4px 10px", fontSize: 12 }}>
              <Table2 size={12} aria-hidden="true" />
              {partial ? `分页查看全部 ${meta.total} 条` : "全部字段视图"}
              <ArrowUpRight size={12} aria-hidden="true" />
            </Button>
          )}
        </div>
      </div>

      {meta.is_flight && summary && (
        <div className={v.detailPills}>
          {partial && <span className={v.detailPill}>以下统计基于已加载前 <strong>{meta.shown}</strong> 条</span>}
          {summary.dep_count > 0 && <span className={v.detailPill}>出港 <strong>{summary.dep_count}</strong></span>}
          {summary.arr_count > 0 && <span className={v.detailPill}>进港 <strong>{summary.arr_count}</strong></span>}
          {summary.planned_count != null && summary.planned_count > 0 && (
            <span className={v.detailPill}>待执行/计划 <strong>{summary.planned_count}</strong></span>
          )}
          {summary.ontime_count > 0 && <span className={v.detailPill}>准点/提前 <strong>{summary.ontime_count}</strong></span>}
          {summary.delayed_count > 0 && <span className={v.detailPill}>延误(&gt;15分) <strong>{summary.delayed_count}</strong></span>}
        </div>
      )}

      {meta.is_flight && hourly.length > 0 && (
        <div className={v.timelineStrip} aria-label="航班24小时时序走势分布">
          <div className={v.timelineHeading}>
            <span className={v.timelineTitle}>
              <Clock3 size={13} aria-hidden="true" />
              <strong>24小时时序走势</strong>
              <small>按时刻分布 · 点击下方柱状可筛选具体小时</small>
            </span>
            {peakHour && peakHour.count > 0 && (
              <span className={v.timelinePeak}>
                高峰时段 <strong>{peakHour.label}</strong>（{peakHour.count} 班）
                {selectedHour !== null && (
                  <button type="button" className={v.timelineReset} onClick={() => setSelectedHour(null)}>
                    清除时段筛选 ×
                  </button>
                )}
              </span>
            )}
          </div>
          <div className={v.timelineGrid}>
            {hourly.map(item => {
              const heightPct = Math.min(100, Math.max(item.count > 0 ? 15 : 4, (item.count / maxHourCount) * 100));
              const isPeak = peakHour && item.hour === peakHour.hour && item.count > 0;
              const isSelected = selectedHour === item.hour;
              return (
                <button
                  type="button"
                  key={item.hour}
                  className={`${v.timelineCol} ${item.count > 0 ? v.timelineColActive : ""} ${isPeak ? v.timelineColPeak : ""} ${isSelected ? v.timelineColSelected : ""}`}
                  onClick={() => setSelectedHour(prev => prev === item.hour ? null : item.hour)}
                  title={`${item.label}: ${item.count} 班${item.count > 0 ? " (点击筛选)" : ""}`}
                >
                  <span className={v.timelineColVal}>{item.count > 0 ? item.count : ""}</span>
                  <div className={v.timelineColTrack}>
                    <div className={v.timelineColBar} style={{ height: `${heightPct}%` }} />
                  </div>
                  <span className={v.timelineColHour}>{item.hour % 3 === 0 || item.hour === 23 ? `${item.hour}时` : ""}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}

      <div className={v.detailToolbar}>
        <div className={v.detailSearchWrap}>
          <Search size={13} aria-hidden="true" />
          <input
            type="search"
            className={v.detailSearch}
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder={meta.is_flight ? "搜索航班号、航线、机位、机型、状态…" : "在当前结果中筛选…"}
            aria-label="筛选明细记录"
          />
        </div>
        <div className={v.filterPills}>
          {meta.is_flight && summary && summary.dep_count > 0 && summary.arr_count > 0 && (
            <>
              <button type="button" className={`${v.filterBtn} ${dirFilter === "ALL" ? v.filterBtnActive : ""}`} onClick={() => setDirFilter("ALL")}>
                全部 ({meta.shown})
              </button>
              <button type="button" className={`${v.filterBtn} ${dirFilter === "DEP" ? v.filterBtnActive : ""}`} onClick={() => setDirFilter("DEP")}>
                出港 ({summary.dep_count})
              </button>
              <button type="button" className={`${v.filterBtn} ${dirFilter === "ARR" ? v.filterBtnActive : ""}`} onClick={() => setDirFilter("ARR")}>
                进港 ({summary.arr_count})
              </button>
            </>
          )}
          {meta.is_flight && summary && summary.delayed_count > 0 && (
            <button type="button" className={`${v.filterBtn} ${delayedOnly ? v.filterBtnActive : ""}`} onClick={() => setDelayedOnly(x => !x)}>
              仅看延误 ({summary.delayed_count})
            </button>
          )}
        </div>
      </div>

      <div className={v.flightTableWrap}>
        {meta.is_flight ? (
          <table className={v.flightTable}>
            <thead>
              <tr>
                <th>#</th>
                <th>航班</th>
                <th>航线</th>
                <th>实际 / 计划</th>
                <th>机位 · 登机口</th>
                <th>机型 · 机号</th>
                <th>状态</th>
                {onQuestion && <th>保障时序图</th>}
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 ? (
                <tr>
                  <td colSpan={onQuestion ? 8 : 7} style={{ textAlign: "center", padding: "24px 12px", color: "var(--muted)" }}>
                    当前筛选条件下无匹配航班，请清空搜索或切换过滤条件。
                  </td>
                </tr>
              ) : filtered.map((item, idx) => {
                if (!isFlightDetailRow(item)) return null;
                const delay = item.delay_minutes;
                const isLate = delay != null && delay > 15;
                const isEarly = delay != null && delay < -5;
                const statusText = formatFlightStatus(item.status, item.actual_time, item.direction);
                const statusBad = statusText === "取消";
                const statusWarn = isLate || statusText === "延误";
                return (
                  <tr key={`${item.flight_no}-${idx}`}>
                    <td className={v.rowIdx}>{idx + 1}</td>
                    <td>
                      <div className={v.flightCell}>
                        <span className={v.flightNo}>{item.flight_no}</span>
                        {item.airline_name && item.airline_name !== item.airline && (
                          <span style={{ fontSize: 11, color: "var(--muted)", whiteSpace: "nowrap" }}>
                            {item.airline_name}
                          </span>
                        )}
                        {item.direction_label && (
                          <span className={`${v.dirTag} ${item.direction === "DEP" ? v.dirDep : v.dirArr}`}>
                            {item.direction_label}
                          </span>
                        )}
                      </div>
                    </td>
                    <td>
                      {item.origin && item.destination ? (
                        <span className={v.routeCell} title={item.origin_code && item.destination_code ? `${item.origin_code} → ${item.destination_code}` : undefined}>
                          <span>{formatAirport(item.origin)}</span>
                          <ArrowRight size={12} aria-hidden="true" />
                          <span>{formatAirport(item.destination)}</span>
                        </span>
                      ) : (
                        <span>{item.route ? item.route.split("→").map(s => formatAirport(s.trim())).join(" → ") : "—"}</span>
                      )}
                    </td>
                    <td>
                      <div className={v.timeCell}>
                        <span className={v.timePrimary}>
                          <span>{item.actual_time !== "—" ? item.actual_time : item.plan_time || "—"}</span>
                          {delay != null && (
                            <span className={`${v.deltaPill} ${isLate ? v.deltaLate : v.deltaGood}`}>
                              {isLate ? `+${delay}分` : isEarly ? `提前${Math.abs(delay)}分` : "准点"}
                            </span>
                          )}
                        </span>
                        {item.plan_time && item.plan_time !== "—" && item.actual_time !== "—" && (
                          <span className={v.timeSub}>计划 {item.plan_time}</span>
                        )}
                      </div>
                    </td>
                    <td>
                      {[
                        item.stand ? `机位 ${item.stand}` : "",
                        item.gate ? `登机口 ${item.gate}` : "",
                        item.terminal || "",
                      ].filter(Boolean).join(" · ") || "—"}
                    </td>
                    <td>
                      {[item.aircraft_type, item.aircraft_reg].filter(Boolean).join(" · ") || "—"}
                    </td>
                    <td>
                      <span className={`${v.statusBadge} ${statusBad ? v.statusBad : statusWarn ? v.statusWarn : v.statusGood}`}>
                        {statusText || "—"}
                      </span>
                      {item.delay_reason && (
                        <span className={v.reasonSub} title={item.delay_reason}>{item.delay_reason}</span>
                      )}
                    </td>
                    {onQuestion && (
                      <td>
                        {item.drill_question ? (
                          <button
                            type="button"
                            className={v.drillBtn}
                            onClick={() => onQuestion(item.drill_question!)}
                            title={`查看 ${item.flight_no} 保障节点时序图与甘特图`}
                          >
                            <ChartGantt size={12} aria-hidden="true" />
                            时序图 →
                          </button>
                        ) : "—"}
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          <table className={v.flightTable}>
            <thead>
              <tr>
                <th>#</th>
                {meta.columns.map(col => (
                  <th key={col.key}>{col.label}{col.unit ? ` (${col.unit})` : ""}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 ? (
                <tr>
                  <td colSpan={meta.columns.length + 1} style={{ textAlign: "center", padding: "24px 12px", color: "var(--muted)" }}>
                    当前筛选条件下无匹配记录。
                  </td>
                </tr>
              ) : filtered.map((row, idx) => (
                <tr key={idx}>
                  <td className={v.rowIdx}>{idx + 1}</td>
                  {meta.columns.map(col => (
                    <td key={col.key}>{formatValue((row as Row)[col.key])}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className={v.tableFooterNote}>
        <span>
          显示 {filtered.length} / {meta.shown} 条已载入记录
          {partial ? `（数据库共命中 ${meta.total.toLocaleString()} 条）` : ""}
        </span>
        {onOpenAllRecords && (
          <span>需要更多原始列或翻页查看，请点击右上角「{partial ? `分页查看全部 ${meta.total} 条` : "全部字段视图"}」。</span>
        )}
      </div>
    </section>
  );
}
