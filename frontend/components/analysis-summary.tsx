"use client";

import type { AnalysisSection, Forecast, QueryResult, VisualMeta } from "@/lib/types";
import { analysisLabel, comparisonGroups, forecastPlot } from "@/lib/analysis";
import { CalendarRange, ChevronRight, ShieldCheck, Table2, TrendingUp } from "lucide-react";
import { BarVisual, DetailTableVisual, DonutVisual, TrendVisual } from "./visual-views";
import { Badge, Notice } from "./ui";
import s from "./analysis.module.css";

function GenericVisual({ meta }: { meta?: VisualMeta }) {
  if (!meta || meta.kind === "none") return null;
  if (meta.kind === "bar") return <BarVisual meta={meta} />;
  if (meta.kind === "donut") return <DonutVisual meta={meta} />;
  if (meta.kind === "trend") return <TrendVisual meta={meta} />;
  if (meta.kind === "detail_table") return <DetailTableVisual meta={meta} />;
  return null;
}

export function AnalysisHighlights({ sections, compact = false }: { sections: AnalysisSection[]; compact?: boolean }) {
  if (!sections.length) return null;
  const shown = compact ? sections.slice(0, 4) : sections;
  const period = analysisLabel(sections[0].label).period;
  const sharedPeriod = period && sections.every(item => analysisLabel(item.label).period === period);
  return (
    <div className={compact ? s.chatHighlights : s.highlights}>
      {sharedPeriod && <p className={s.period}><CalendarRange size={14} aria-hidden="true" />{period}</p>}
      <dl className={s.metrics}>
        {shown.map(item => {
          const label = analysisLabel(item.label);
          const value = item.value;
          const rowCount = item.result.main_rows?.length || 0;
          const incomplete = item.result.coverage_assessment?.status === "PARTIAL";
          return <div key={item.id} className={s.metric}>
            <dt>{label.name}{!sharedPeriod && label.period && <small>{label.period}</small>}
              {(item.is_simulated || incomplete) && <small className={s.metricNote}>{[item.is_simulated && "模拟数据", incomplete && "覆盖不完整"].filter(Boolean).join(" · ")}</small>}
            </dt>
            <dd>
              {typeof value === "number" && Number.isFinite(value)
                ? <><strong>{value.toLocaleString("zh-CN", { maximumFractionDigits: 4 })}</strong>{item.unit && <span>{item.unit}</span>}</>
                : <span>{rowCount > 1 ? `${rowCount} 项明细` : "暂无统计值"}</span>}
            </dd>
          </div>;
        })}
      </dl>
      {shown.length < sections.length && <p className={s.period}>另有 {sections.length - shown.length} 项</p>}
    </div>
  );
}

export function AnalysisComparisons({ result, compact = false }: { result: QueryResult; compact?: boolean }) {
  return <>{result.comparisons?.map(item => {
    const current = result.sections?.find(section => section.id === item.current_id);
    const baseline = result.sections?.find(section => section.id === item.baseline_id);
    if (!current || !baseline) return null;
    const currentLabel = analysisLabel(current.label);
    const baseLabel = analysisLabel(baseline.label);
    const fields = [
      { label: "比较期", scope: currentLabel.period, value: current.value, unit: current.unit },
      { label: "基期", scope: baseLabel.period, value: baseline.value, unit: baseline.unit },
      { label: "差值", scope: compact ? "" : "比较期减基期", value: item.difference, unit: item.difference_unit, signed: true },
      { label: "增长率", scope: compact ? "" : "差值 ÷ 基期 × 100%", value: item.growth_pct, unit: "%", signed: true },
    ];
    return <section key={item.current_id} className={`${s.highlights} ${s.comparison} ${compact ? s.comparisonCompact : ""}`}>
      <h3 className={s.comparisonTitle}><TrendingUp size={15} aria-hidden="true" />{currentLabel.name}</h3>
      <dl className={s.metrics}>
        {fields.map(field => <div className={s.metric} key={field.label}>
          <dt>{field.label}{field.scope && <small>{field.scope}</small>}</dt>
          <dd>{field.value == null ? <span>{field.unit === "%" && item.difference != null && baseline.value === 0 ? "基期为零" : "数据不足"}</span>
            : <><strong>{field.value.toLocaleString("zh-CN", {
              maximumFractionDigits: field.signed && field.unit === "%" ? 2 : 4,
              minimumFractionDigits: field.signed && field.unit === "%" ? 2 : 0,
              signDisplay: field.signed ? "exceptZero" : "auto",
            })}</strong>{field.unit && <span>{field.unit}</span>}</>}</dd>
        </div>)}
      </dl>
      {(current.is_simulated || baseline.is_simulated) && <p className={s.coverageNote}>含模拟数据</p>}
    </section>;
  })}</>;
}

export function AirportBenchmark({ result, compact = false }: { result: QueryResult; compact?: boolean }) {
  const sections = result.sections || [];
  const periods = new Set(sections.map(item => item.period.join(":")));
  const showMetrics = periods.size === 1;
  if (!result.airport_sources?.length || (compact && !showMetrics)) return null;
  return <div className={compact ? s.benchmarkCompact : s.highlights}>
    {showMetrics && <p className={s.period}><CalendarRange size={14} aria-hidden="true" />{analysisLabel(sections[0].label).period}</p>}
    <div className={s.benchmarkGrid}>
      {result.airport_sources.map(airport => {
        const items = sections.filter(item => item.object === airport.airport_code);
        return <section className={s.airportCard} key={airport.airport_code}>
          <h3>{airport.airport_name} <small>{airport.airport_code}</small></h3>
          {showMetrics && <dl className={s.airportMetrics}>{items.map(item => (
            <div key={item.id}>
              <dt>{analysisLabel(item.label).name.split(" · ").at(-1)}</dt>
              <dd>{item.value != null ? <><strong>{item.value.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}</strong> {item.unit}</>
                : item.result.main_rows.length > 1 ? "见分项明细" : "暂无数据"}</dd>
              {item.result.coverage_assessment?.status === "PARTIAL" && <dd className={s.airportCoverage}>仅覆盖部分日期</dd>}
            </div>
          ))}</dl>}
          {airport.sources.length ? airport.sources.map(source => <p className={s.airportSource} key={source}>{source}</p>)
            : <p className={s.airportSource}>本次未获取可核验的来源记录</p>}
        </section>;
      })}
    </div>
  </div>;
}

export function ForecastResults({ forecast }: { forecast: Forecast }) {
  const methods = [...new Set(forecast.points.map(point => point.method))];
  return <section className={`${s.tableCard} ${s.forecastTable}`}>
    <div className={s.tableHeader}>
      <div>
        <h3 className={s.tableTitle}>{forecast.metric}逐日预测</h3>
        <p className={s.forecastMeta}>训练 {forecast.training_start} 至 {forecast.training_end} · 单位 {forecast.unit}</p>
      </div>
    </div>
    <div className={s.table}>
      <table aria-label="逐日预测与回测">
        <thead><tr><th scope="col">日期</th><th scope="col">预测值</th><th scope="col">经验区间</th><th scope="col">回测 MAE</th></tr></thead>
        <tbody>{forecast.points.map(point => <tr key={point.date}>
          <th scope="row">{point.date}</th>
          <td><strong>{point.value.toLocaleString("zh-CN")}</strong>
            {methods.length > 1 && <small>{point.method}</small>}
            {point.planned_value != null && <small>已有计划 {point.planned_value}</small>}
          </td>
          <td>{point.lower}–{point.upper}</td>
          <td>{point.mae.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}</td>
        </tr>)}</tbody>
      </table>
    </div>
    <div className={s.forecastNotes}>
      {methods.length === 1 && <p>采用{methods[0]}</p>}
      {forecast.methodology && <p>{forecast.methodology}</p>}
      <p>{forecast.warning}</p>
    </div>
  </section>;
}

export function AnalysisSummary({ result }: { result: QueryResult }) {
  const forecast = result.forecast;
  const plot = forecast ? forecastPlot(forecast) : null;
  const sections = result.sections || [];
  const benchmarkSummary = result.airport_sources?.length && new Set(sections.map(item => item.period.join(":"))).size === 1;
  const comparedIds = new Set(result.comparisons?.flatMap(item => [item.current_id, item.baseline_id]));
  const otherSections = benchmarkSummary ? [] : sections.filter(item => !comparedIds.has(item.id));
  const comparisons = comparisonGroups(otherSections);
  const grouped = otherSections.filter(item => item.value == null && ["bar", "donut", "trend", "detail_table"].includes(item.result.visual_meta?.kind || ""));
  const chartIds = new Set([...comparisons.flat(), ...grouped].map(item => item.id));

  return (
    <div className={s.summary}>
      {result.is_simulated && (
        <Notice>
          标为模拟的数据仅供测试，不能用于实际机场排名或经营分析。
        </Notice>
      )}
      {result.partial && (
        <Notice>部分项目没有完整结果，请查看各分项状态和数据覆盖说明。</Notice>
      )}

      {result.sections && (
        <>
          <AirportBenchmark result={result} />
          <AnalysisComparisons result={result} />
          <AnalysisHighlights sections={otherSections.filter(item => !chartIds.has(item.id))} />
          {chartIds.size > 0 && <div className={s.charts}>
            {comparisons.map(group => {
              const multiplePeriods =
                new Set(group.map(x => x.period.join(":"))).size > 1;
              const singleObject = new Set(group.map(x => x.object || "")).size <= 1;
              const orderedGroup = multiplePeriods && singleObject
                ? [...group].sort((a, b) => (a.period[0] || "").localeCompare(b.period[0] || ""))
                : group;
              return (
                <div key={`${orderedGroup[0].metric_key}:${orderedGroup[0].unit}`} className={s.chartBlock}>
                  <BarVisual
                    meta={{
                      kind: "bar",
                      title: orderedGroup[0].result.metric?.name || "指标对比",
                      unit: orderedGroup[0].unit,
                      group_label: multiplePeriods && singleObject ? "期间" : "项目",
                      is_rate: orderedGroup[0].unit === "%",
                      bars: orderedGroup.map(item => ({
                        label:
                          (item.object ? item.label.split(" · ")[0] : analysisLabel(item.label).period || item.period[0]) +
                          (item.object && multiplePeriods ? " · " + (analysisLabel(item.label).period || item.period[0]) : "") +
                          (item.is_simulated ? " · 模拟" : ""),
                        value: item.value!,
                        percent: 0,
                        ...(multiplePeriods && singleObject && item.period[0] ? { time_key: item.period[0] } : {}),
                      })),
                    }}
                  />
                </div>
              );
            })}

            {grouped.map(item => <div key={item.id} className={s.chartBlock}>
              <p className={s.period}>{item.label}{item.is_simulated && " · 模拟数据"}</p>
              <GenericVisual meta={item.result.visual_meta} />
            </div>)}
          </div>}

          {!result.is_simulated && !result.partial && result.claims?.filter(claim => claim.kind === "limitations").map((claim, index) => (
            <p className={s.coverageNote} key={index}><ShieldCheck size={14} aria-hidden="true" />{claim.text}</p>
          ))}

          <details className={s.tableCard}>
            <summary className={s.tableHeader}>
              <span className={s.tableTitleGroup}>
                <span className={s.tableIconMark}>
                  <Table2 size={15} aria-hidden="true" />
                </span>
                <span className={s.tableTitle}>分项口径与来源</span>
              </span>
              <ChevronRight className={s.disclosureIcon} size={15} aria-hidden="true" />
            </summary>
            <div className={s.table} tabIndex={0}>
              <table>
                <thead>
                  <tr>
                    <th>项目</th>
                    <th>数值</th>
                    <th>计算依据 / 统计过程</th>
                    <th>来源 / 状态</th>
                  </tr>
                </thead>
                <tbody>
                  {result.sections.map(item => (
                    <tr key={item.id}>
                      <th scope="row">
                        <span className={s.itemLabel}>{item.label}</span>
                      </th>
                      <td>
                        <span className={s.valueCell}>
                          <strong>
                            {item.value == null
                              ? item.result?.main_rows && item.result.main_rows.length > 1
                                ? `${item.result.main_rows.length} 项明细`
                                : "—"
                              : item.value.toLocaleString("zh-CN", {
                                  maximumFractionDigits: 4,
                                })}
                          </strong>
                          {item.value != null && item.unit && <small>{item.unit}</small>}
                        </span>
                      </td>
                      <td>
                        <div className={s.basisCell}>
                          {item.calculation_process && <span>{item.calculation_process}</span>}
                          {(item.formula_desc || item.result.metric?.formula_desc || item.result.metric?.description) && (
                            <div className={s.formulaDescText}>
                              <span>{item.formula_desc || item.result.metric?.formula_desc || item.result.metric?.description}</span>
                            </div>
                          )}
                          {!item.calculation_process && !item.formula_desc && !item.result.metric?.formula_desc && !item.result.metric?.description && (
                            <span>—</span>
                          )}
                        </div>
                      </td>
                      <td>
                        <div className={s.sourceCell}>
                          <Badge tone={item.is_simulated ? "amber" : "neutral"}>
                            {item.is_simulated ? "模拟测试数据" : "本地观测数据"}
                          </Badge>
                          {item.result.outcome !== "answered" && (
                            <p className={s.subItemAnswer}>{item.result.answer}</p>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>

        </>
      )}

      {forecast && plot && (
        <>
          <ForecastResults forecast={forecast} />
          <svg
            viewBox="0 0 760 220"
            role="img"
            aria-label={`${forecast.metric}历史观测、预测与经验误差范围`}
            className={s.plot}
          >
            <title>
              {forecast.metric}：实线为历史，虚线为预测，浅色区域为经验误差范围
            </title>
            <line x1="42" y1="185" x2="720" y2="185" stroke="var(--line)" />
            <polygon points={plot.band} fill="var(--accent)" fillOpacity=".15" />
            <polyline
              points={plot.history}
              fill="none"
              stroke="var(--text)"
              strokeWidth="2"
            />
            <polyline
              points={plot.forecast}
              fill="none"
              stroke="var(--accent)"
              strokeWidth="3"
              strokeDasharray="6 4"
            />
            <text x="2" y="40">
              {plot.high.toFixed(0)}
            </text>
            <text x="2" y="176">
              {plot.low.toFixed(0)}
            </text>
            <text x="42" y="210">
              {plot.firstDate}
            </text>
            <text x="640" y="210">
              {plot.lastDate}
            </text>
          </svg>
          <p className="helper">
            实线：历史观测 虚线：预测 浅色带：小样本经验误差范围
          </p>
        </>
      )}
    </div>
  );
}
