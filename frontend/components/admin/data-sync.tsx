"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity, Archive, CheckCircle2, ChevronDown, Clock, Database, Eye, Play,
  Radar, RefreshCw, Search, Server, ShieldAlert, Square,
} from "lucide-react";
import {
  formatBytes, formatDate, formatDurationMs,
  SYNC_JOB_LABELS, SYNC_STATUS_LABELS,
  type Paged, type SyncOverview, type SyncRun, type SyncSummary,
} from "@/lib/admin";
import { request } from "@/lib/api";
import { Badge, Button, Card, Modal, Notice, Spinner } from "../ui";
import { Empty, Loading, Pagination, useAction, useResource } from "./shared";

type SyncRunsResponse = Paged<SyncRun> & { summary?: SyncSummary };

const JOB_OPTIONS: { value: string; label: string; hint: string; supportsDateRange: boolean; defaultMode: string }[] = [
  { value: "full_sync", label: "近 1 个月全量导入（默认）", hint: "依次全量同步基础资料及近 1 个月 FIMS/ACDM 航班、共享航班、货邮行生产量、动态汇总旅客与登机桥/保障节点，并自动清理超期历史数据", supportsDateRange: true, defaultMode: "FULL" },
  { value: "master_data", label: "仅基础资料全量（航司/机位/登机桥/保障节点等）", hint: "从 Oracle 全量更新航司、机场、代理、特情原因、飞机、机位、登机口、登机桥、保障节点、跑道", supportsDateRange: false, defaultMode: "FULL" },
  { value: "flights_fims", label: "仅 FIMS 航班动态", hint: "支持全量拉取、按执行日区间补数或按水位增量", supportsDateRange: true, defaultMode: "FULL" },
  { value: "flights_acdm", label: "仅 ACDM 航班动态", hint: "支持全量拉取、按执行日区间补数或按水位增量", supportsDateRange: true, defaultMode: "FULL" },
  { value: "codeshares", label: "仅 FIMS 共享航班", hint: "按全量或执行日范围重建 std_flight_link 共享关系", supportsDateRange: true, defaultMode: "FULL" },
  { value: "production", label: "仅货邮行生产量（FORMFILL）", hint: "支持全量拉取或按执行日区间补数", supportsDateRange: true, defaultMode: "FULL" },
  { value: "calc_total", label: "仅动态汇总旅客（CALC_TOTAL）", hint: "同步出港旅客总数、值机/安检/登机人数及行李件数（TBH_FIMS_DYN_CALC_TOTAL）", supportsDateRange: true, defaultMode: "FULL" },
  { value: "safeguards", label: "仅登机桥与保障节点（FIMS）", hint: "同步靠桥起止、桥载电源/空调及 49 类保障节点明细", supportsDateRange: true, defaultMode: "FULL" },
  { value: "flights_scheduled", label: "仅航班动态增量（FIMS + ACDM）", hint: "按最新水位依次拉取 FIMS 与 ACDM 近期变更", supportsDateRange: false, defaultMode: "INCREMENTAL" },
];

const DIM_LABELS: Record<string, string> = {
  dim_airline: "航司",
  dim_airport: "机场",
  dim_aircraft: "飞机",
  dim_stand: "机位",
  dim_gate: "登机口",
  dim_air_bridge: "登机桥",
  dim_safeguard_node: "保障节点",
  dim_runway: "跑道",
  dim_agency: "代理",
  dim_abnormal_reason: "不正常原因",
  airline: "航司",
  airport: "机场",
  aircraft: "飞机",
  stand: "机位",
  gate: "登机口",
  airBridge: "登机桥",
  safeguardNode: "保障节点",
  runway: "跑道",
  agency: "代理",
  abnormalReason: "不正常原因",
};

const SOURCE_LABELS: Record<string, string> = {
  fims_flights: "Oracle FIMS 航班",
  acdm_flights: "Oracle ACDM 航班",
  codeshares: "Oracle 共享航班",
  production: "Oracle 货邮行生产量",
  calc_total: "Oracle 动态汇总旅客",
  air_bridges: "Oracle 登机桥动态",
  safeguard_nodes: "Oracle 保障节点动态",
};

function shanghaiDateOffset(offsetDays = 0) {
  const formatter = new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  return formatter.format(new Date(Date.now() + offsetDays * 86400_000));
}

function statusTone(status: string): "green" | "amber" | "neutral" {
  if (status === "completed" || status === "running") return "green";
  if (status === "partial" || status === "failed") return "amber";
  return "neutral";
}

function modeLabel(mode: string) {
  if (mode === "FULL") return "全量导入";
  if (mode === "BY_EXEC_DATE") return "按执行日";
  if (mode === "INCREMENTAL") return "水位增量";
  return mode;
}

function renderBreakdownLines(detail: SyncRun["detail"]) {
  if (!detail || typeof detail !== "object") return null;
  const typed = detail as { steps?: Array<Record<string, unknown>> };
  if (Array.isArray(typed.steps) && typed.steps.length > 0) return typed.steps;
  return null;
}

export function DataSyncPanel() {
  const [page, setPage] = useState(1);
  const [jobFilter, setJobFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [query, setQuery] = useState("");

  const overview = useResource<SyncOverview>("/api/admin/sync/overview");
  const runsUrl = `/api/admin/sync/runs?page=${page}&job_key=${encodeURIComponent(jobFilter)}&status=${encodeURIComponent(statusFilter)}&search=${encodeURIComponent(query)}`;
  const runs = useResource<SyncRunsResponse>(runsUrl);
  const action = useAction();

  const [jobType, setJobType] = useState("full_sync");
  const [mode, setMode] = useState("FULL");
  const [execFrom, setExecFrom] = useState(() => shanghaiDateOffset(-30));
  const [execTo, setExecTo] = useState(() => shanghaiDateOffset(7));
  const [withBackup, setWithBackup] = useState(true);
  const [selectedRun, setSelectedRun] = useState<SyncRun | null>(null);

  const selectedSpec = useMemo(
    () => JOB_OPTIONS.find(item => item.value === jobType) || JOB_OPTIONS[0],
    [jobType],
  );

  const reloadAll = useCallback(() => {
    overview.reload();
    runs.reload();
  }, [overview.reload, runs.reload]);

  const ov = overview.data;
  const activeRun = ov?.active_run || runs.data?.items.find(item => item.status === "running") || null;

  const lastReconciledRef = useRef(-1);
  useEffect(() => {
    if (!ov || runs.loading) return;
    const totalFromOverview = ov.summary?.total_runs ?? 0;
    const totalFromRuns = runs.data?.summary?.total_runs ?? -1;
    if ((ov.reconciled_added || 0) > 0 || totalFromOverview !== totalFromRuns) {
      if (lastReconciledRef.current !== totalFromOverview) {
        lastReconciledRef.current = totalFromOverview;
        runs.reload();
      }
    }
  }, [ov, runs.data?.summary?.total_runs, runs.loading, runs.reload]);

  const prevActiveIdRef = useRef<string | null>(null);
  useEffect(() => {
    const currentId = activeRun?.id || null;
    if (prevActiveIdRef.current && !currentId) {
      reloadAll();
    }
    prevActiveIdRef.current = currentId;
    if (!currentId) return;
    const timer = window.setInterval(() => {
      reloadAll();
    }, 2000);
    return () => window.clearInterval(timer);
  }, [activeRun?.id, reloadAll]);

  function onJobChange(next: string) {
    setJobType(next);
    const spec = JOB_OPTIONS.find(item => item.value === next);
    if (spec) {
      setMode(spec.defaultMode);
      if (spec.defaultMode === "FULL") {
        setExecFrom(shanghaiDateOffset(-30));
        setExecTo(shanghaiDateOffset(7));
      } else if (spec.defaultMode === "BY_EXEC_DATE") {
        setExecFrom(shanghaiDateOffset(-7));
        setExecTo(shanghaiDateOffset(0));
      }
    }
  }

  function onModeChange(nextMode: string) {
    setMode(nextMode);
    if (nextMode === "FULL") {
      setExecFrom(shanghaiDateOffset(-30));
      setExecTo(shanghaiDateOffset(7));
    } else if (nextMode === "BY_EXEC_DATE") {
      setExecFrom(shanghaiDateOffset(-7));
      setExecTo(shanghaiDateOffset(0));
    }
  }

  function submitRun(e: React.FormEvent) {
    e.preventDefault();
    action.run(async () => {
      await request<{ ok: boolean; run: SyncRun }>("/api/admin/sync/run", {
        method: "POST",
        body: JSON.stringify({
          job_type: jobType,
          mode,
          exec_date_from: execFrom,
          exec_date_to: execTo,
          with_backup: withBackup,
        }),
      });
      setPage(1);
      reloadAll();
    }, "已启动 Python 原生同步任务，正在实时记录拉取条数与耗时");
  }

  const summary = ov?.summary || runs.data?.summary;
  const flightTable = ov?.pg?.tables?.std_flight_core;
  const prodTable = ov?.pg?.tables?.std_flight_production;
  const linkTable = ov?.pg?.tables?.std_flight_link;
  const calcTotalTable = ov?.pg?.tables?.std_flight_calc_total;
  const airBridgeTable = ov?.pg?.tables?.std_flight_air_bridge;
  const safeguardTable = ov?.pg?.tables?.std_flight_safeguard_node;
  const execView = ov?.pg?.tables?.flight_execution_view;
  const acdmDetailView = ov?.pg?.tables?.mv_acdm_dyn_flight_detail;
  const engineReady = !!ov?.engine?.ready;
  const sourceStats = ov?.oracle?.source_stats?.tables;

  return (
    <>
      <div className="admin-section-head">
        <div>
          <h2>Oracle 数据同步</h2>
          <p>内置 Python 同步引擎直接读取 Oracle 清洗写入本地 ops_ask，并完整维护每次拉取量、耗时与水位记录</p>
        </div>
        <div className="manager-tools">
          <Button onClick={reloadAll} disabled={action.busy}>
            <RefreshCw size={13} />刷新状态
          </Button>
          <Button
            disabled={action.busy}
            onClick={() => action.run(async () => {
              await request("/api/admin/sync/probe-source", { method: "POST", body: JSON.stringify({}) });
              overview.reload();
            }, "已完成 Oracle 源库体量探测")}
          >
            <Radar size={13} />探测源库体量
          </Button>
          <Button
            disabled={action.busy || !!activeRun}
            onClick={() => action.run(async () => {
              const res = await request<{ backup: { name: string } }>("/api/admin/sync/backup", { method: "POST", body: JSON.stringify({}) });
              reloadAll();
              if (!res.backup?.name) throw new Error("备份文件名无效");
            }, "已完成业务库备份，保存至 artifacts/backups/")}
          >
            <Archive size={13} />立即备份库
          </Button>
        </div>
      </div>

      {action.feedback}
      <Loading {...overview} />

      {activeRun && (
        <div className="notice sync-active-banner" role="status">
          <Spinner />
          <div className="sync-active-body">
            <strong>正在执行：{activeRun.title}</strong>
            <small>
              {activeRun.message || "任务准备中…"} · 已累计拉取 {activeRun.fetched.toLocaleString()} 条（新增 {activeRun.inserted.toLocaleString()} / 更新 {activeRun.updated.toLocaleString()}） · 耗时 {formatDurationMs(activeRun.elapsed_ms)}
            </small>
          </div>
          <div className="manager-tools">
            <Badge tone="green">同步中</Badge>
            <Button
              disabled={action.busy}
              onClick={() => action.run(async () => {
                await request("/api/admin/sync/cancel", { method: "POST", body: JSON.stringify({}) });
                reloadAll();
              }, "已发送中止信号，当前批次完成后安全停止")}
            >
              <Square size={12} />中止同步
            </Button>
          </div>
        </div>
      )}

      {ov && summary && (
        <div className="admin-stat-grid">
          <section className="card admin-stat">
            <div><span>累计同步次数</span><Activity size={16} /></div>
            <strong>{summary.total_runs.toLocaleString()}</strong>
            <small>今日 {summary.today_runs} 次 · 成功 {summary.completed_runs} / 失败 {summary.failed_runs}</small>
          </section>
          <section className="card admin-stat">
            <div><span>累计拉取数据</span><Database size={16} /></div>
            <strong>{summary.total_fetched.toLocaleString()}</strong>
            <small>今日 {summary.today_fetched.toLocaleString()} 条 · 新增 {summary.total_inserted.toLocaleString()} · 更新 {summary.total_updated.toLocaleString()}</small>
          </section>
          <section className="card admin-stat">
            <div><span>标准航班库覆盖</span><Server size={16} /></div>
            <strong>{flightTable ? flightTable.count.toLocaleString() : "—"}</strong>
            <small>{flightTable?.min_date && flightTable?.max_date ? `${flightTable.min_date} ~ ${flightTable.max_date}` : "未检测到 std_flight_core"}</small>
          </section>
          <section className="card admin-stat">
            <div><span>最近同步时间</span><Clock size={16} /></div>
            <strong className="sync-kpi-time">{formatDate(summary.latest_success_at)}</strong>
            <small>{prodTable?.max_date ? `生产量覆盖至 ${prodTable.max_date}` : `当前数据源 ${ov.data_source}`}</small>
          </section>
        </div>
      )}

      <div className="admin-overview-grid">
        <Card className="card-pad">
          <div className="card-head">
            <h3 className="section-title"><Play size={15} />全量数据导入</h3>
            {ov?.oracle && (
              <Badge tone={ov.oracle.reachable && ov.oracle.configured ? "green" : "amber"}>
                Oracle {ov.oracle.host}:{ov.oracle.port}/{ov.oracle.service_name || "datacache"} {ov.oracle.reachable ? `已连通 ${ov.oracle.latency_ms ?? ""}ms` : "连接异常"}
              </Badge>
            )}
          </div>

          <p className="helper">
            已将原 Java 同步逻辑完整移植至本系统 Python 引擎（流式分批事务写入，无需启动外部程序）。每次执行默认全量导入近 1 个月（近 30 天至未来 7 天预排班，并清理超期历史）以下全部 7 类数据，问数视图同步后即时生效：
          </p>

          <form onSubmit={submitRun}>
            <div className="form-grid">
              <div className="form-span sync-pill-row">
                <Badge tone="green">基础资料全量</Badge>
                <Badge tone="green">FIMS 航班全量</Badge>
                <Badge tone="green">ACDM 航班全量</Badge>
                <Badge tone="green">共享航班全量</Badge>
                <Badge tone="green">货邮行生产量全量</Badge>
                <Badge tone="green">动态汇总旅客</Badge>
                <Badge tone="green">登机桥与保障节点</Badge>
              </div>

              <label className="form-field form-span">
                本次导入范围
                <select value={mode} onChange={e => onModeChange(e.target.value)} disabled={jobType === "master_data" || jobType === "flights_scheduled"}>
                  <option value="FULL">近 1 个月全量导入（默认 · 近 30 天至未来 7 天并清理超期）</option>
                  <option value="BY_EXEC_DATE">按指定起止日期全量重导（例如最近 7 天 / 自定义）</option>
                  {jobType !== "full_sync" && jobType !== "codeshares" && (
                    <option value="INCREMENTAL">仅增量拉取（按上次水位之后变更）</option>
                  )}
                </select>
              </label>

              {(mode === "BY_EXEC_DATE" || mode === "FULL") && jobType !== "master_data" && jobType !== "flights_scheduled" && (
                <div className="form-span sync-date-row">
                  <label className="form-field">
                    开始执行日
                    <input type="date" required value={execFrom} onChange={e => setExecFrom(e.target.value)} />
                  </label>
                  <label className="form-field">
                    结束执行日
                    <input type="date" required value={execTo} onChange={e => setExecTo(e.target.value)} />
                  </label>
                </div>
              )}

              <label className="admin-toggle form-span">
                <input type="checkbox" checked={withBackup} onChange={e => setWithBackup(e.target.checked)} />
                导入前自动备份当前业务库（保存到 artifacts/backups/，防止误覆盖）
              </label>

              {ov && !engineReady && (
                <div className="form-span">
                  <Notice error>未检测到完整 Oracle 凭据，请在 config.local.json（oracle 节点）或环境变量 HBASK_ORACLE_* 中配置。</Notice>
                </div>
              )}

              <details className="process-details form-span sync-advanced">
                <summary>
                  <span>高级选项：仅重跑单个模块</span>
                  <ChevronDown size={14} />
                </summary>
                <div className="sync-advanced-body">
                  <label className="form-field">
                    同步任务模块
                    <select value={jobType} onChange={e => onJobChange(e.target.value)}>
                      {JOB_OPTIONS.map(opt => (
                        <option key={opt.value} value={opt.value}>{opt.label}</option>
                      ))}
                    </select>
                    <span className="helper">{selectedSpec.hint}</span>
                  </label>
                </div>
              </details>
            </div>

            <div className="modal-actions">
              <Button type="submit" variant="primary" disabled={action.busy || !!activeRun || !engineReady}>
                {action.busy || activeRun ? <Spinner /> : <Play size={13} />}
                {activeRun ? "正在同步数据中…" : jobType === "full_sync" ? "立即全量同步" : `执行：${SYNC_JOB_LABELS[jobType] || jobType}`}
              </Button>
            </div>
          </form>
        </Card>

        <Card className="card-pad">
          <div className="card-head">
            <h3 className="section-title"><Server size={15} />Python 引擎与两库数据概览</h3>
            <Badge tone={engineReady ? "green" : "amber"}>
              {engineReady ? "Python 原生直连 · 已就绪" : "Oracle 凭据未配置"}
            </Badge>
          </div>

          {ov && (
            <>
              <div className="binding-grid">
                <div className="binding">
                  <span>标准生产量 (std_flight_production)</span>
                  <code>{prodTable ? `${prodTable.count.toLocaleString()} 条 (${prodTable.min_date} ~ ${prodTable.max_date})` : "未检测到"}</code>
                </div>
                <div className="binding">
                  <span>问数实时视图 (flight_execution)</span>
                  <code>{execView ? `${execView.count.toLocaleString()} 条 · 同步即时生效` : "未检测到"}</code>
                </div>
                <div className="binding">
                  <span>A-CDM 全量宽表视图 (mv_acdm_dyn_flight_detail)</span>
                  <code>{acdmDetailView ? `${acdmDetailView.count.toLocaleString()} 条 · 口径已校准` : "未检测到"}</code>
                </div>
                <div className="binding">
                  <span>共享 / 过站关联链 (std_flight_link)</span>
                  <code>{linkTable ? `${linkTable.count.toLocaleString()} 条` : "未检测到"}</code>
                </div>
                <div className="binding">
                  <span>动态汇总旅客 (std_flight_calc_total)</span>
                  <code>{calcTotalTable ? `${calcTotalTable.count.toLocaleString()} 条 (${calcTotalTable.min_date || "—"} ~ ${calcTotalTable.max_date || "—"})` : "未检测到"}</code>
                </div>
                <div className="binding">
                  <span>登机桥 / 保障节点明细</span>
                  <code>{`${airBridgeTable ? airBridgeTable.count.toLocaleString() : 0} 条 / ${safeguardTable ? safeguardTable.count.toLocaleString() : 0} 条`}</code>
                </div>
                <div className="binding">
                  <span>问数结构目录 ({ov.schema_status.database})</span>
                  <code>{`${ov.schema_status.tables} 张表 / ${ov.schema_status.fields} 字段`}</code>
                </div>
              </div>

              {sourceStats && (
                <>
                  <h4 className="sync-subhead">Oracle 源库体量（{formatDate(ov.oracle.source_stats?.checked_at)}）</h4>
                  <div className="binding-grid">
                    {Object.entries(sourceStats).map(([key, stat]) => (
                      <div className="binding" key={key}>
                        <span>{SOURCE_LABELS[key] || key}</span>
                        <code>{`${stat.count.toLocaleString()} 条`}{stat.min_date && stat.max_date ? ` (${stat.min_date} ~ ${stat.max_date})` : ""}</code>
                      </div>
                    ))}
                  </div>
                </>
              )}

              {Object.keys(ov.pg.dimensions || {}).length > 0 && (
                <div className="sync-pill-row">
                  {Object.entries(ov.pg.dimensions).map(([dim, count]) => (
                    <Badge key={dim}>{DIM_LABELS[dim] || dim}：{count.toLocaleString()}</Badge>
                  ))}
                </div>
              )}

              <div className="card-divider" />
              <h4 className="sync-subhead">最新任务水位与最近完成时间</h4>
              {ov.pg.job_states.length ? (
                <div className="table-wrap sync-compact-table">
                  <table className="data-table attribute-table admin-table">
                    <thead>
                      <tr><th>同步模块</th><th>最近水位 / 完成时间</th></tr>
                    </thead>
                    <tbody>
                      {ov.pg.job_states.map(js => (
                        <tr key={js.job_name}>
                          <td>
                            <strong>{js.title}</strong>
                            <small>{js.job_name}</small>
                          </td>
                          <td>
                            <code>{js.watermark ? formatDate(js.watermark) : "按执行日区间全量/补数"}</code>
                            <small>最近成功：{formatDate(js.last_success_at)}</small>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <Notice>当前数据库未检测到 public.sync_job_state 水位记录。</Notice>
              )}

              {ov.backups.length > 0 && (
                <>
                  <div className="card-divider" />
                  <h4 className="sync-subhead">最近数据库备份（artifacts/backups）</h4>
                  <div className="sync-pill-row">
                    {ov.backups.slice(0, 4).map(file => (
                      <Badge key={file.name}>{file.name} · {formatBytes(file.size_bytes)}</Badge>
                    ))}
                  </div>
                </>
              )}
            </>
          )}
        </Card>
      </div>

      <Card className="card-pad sync-ledger-card">
        <div className="card-head">
          <div>
            <h3 className="section-title"><Clock size={15} />每一次同步台账明细</h3>
            <p className="helper">维护每次同步的开始/完成时间、耗时、拉取总条数、新增/更新明细、备份文件与分步明细</p>
          </div>
          <Button onClick={runs.reload} disabled={runs.loading}><RefreshCw size={13} />刷新列表</Button>
        </div>

        <form
          className="admin-toolbar"
          onSubmit={e => { e.preventDefault(); setQuery(searchInput); setPage(1); }}
        >
          <label className="admin-search">
            <Search size={14} />
            <input
              aria-label="搜索同步记录"
              value={searchInput}
              onChange={e => setSearchInput(e.target.value)}
              placeholder="搜索任务名称、结果说明或操作人"
            />
          </label>
          <select aria-label="按任务筛选" value={jobFilter} onChange={e => { setJobFilter(e.target.value); setPage(1); }}>
            <option value="">全部任务</option>
            {Object.entries(SYNC_JOB_LABELS).map(([k, label]) => (
              <option key={k} value={k}>{label}</option>
            ))}
          </select>
          <select aria-label="按状态筛选" value={statusFilter} onChange={e => { setStatusFilter(e.target.value); setPage(1); }}>
            <option value="">全部状态</option>
            {Object.entries(SYNC_STATUS_LABELS).map(([k, label]) => (
              <option key={k} value={k}>{label}</option>
            ))}
          </select>
          <Button type="submit">筛选</Button>
        </form>

        <Loading {...runs} />

        <div className="table-wrap">
          <table className="data-table attribute-table admin-table">
            <thead>
              <tr>
                <th>同步时间 / 耗时</th>
                <th>任务与模式</th>
                <th>状态</th>
                <th>拉取与写入统计</th>
                <th>水位与说明</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {(runs.data?.items || []).map(run => (
                <tr key={run.id}>
                  <td>
                    <strong>{formatDate(run.started_at)}</strong>
                    <small>完成：{formatDate(run.finished_at)}</small>
                    <small>耗时：{formatDurationMs(run.elapsed_ms)}</small>
                  </td>
                  <td>
                    <strong>{run.title}</strong>
                    <div className="sync-pill-row">
                      <Badge>{modeLabel(run.mode)}</Badge>
                      <Badge tone={run.source_origin === "ui" ? "green" : "neutral"}>
                        {run.source_origin === "ui" ? "控制台发起" : "外部/历史记录"}
                      </Badge>
                    </div>
                    <small>操作人：{run.actor_name}</small>
                  </td>
                  <td>
                    <Badge tone={statusTone(run.status)}>
                      {run.status === "running" ? <Spinner /> : run.status === "completed" ? <CheckCircle2 size={12} /> : <ShieldAlert size={12} />}
                      {SYNC_STATUS_LABELS[run.status] || run.status}
                    </Badge>
                  </td>
                  <td className="sync-metrics-cell">
                    <strong>拉取 {run.fetched.toLocaleString()} 条</strong>
                    <small>
                      新增 {run.inserted.toLocaleString()} · 更新 {run.updated.toLocaleString()} · 跳过 {run.skipped.toLocaleString()} · 失败 {run.failed.toLocaleString()}
                    </small>
                  </td>
                  <td>
                    <div className="helper">{run.message || "—"}</div>
                    {run.watermark_after && <small>水位至：{formatDate(run.watermark_after)}</small>}
                    {run.backup_file && <small>备份：{run.backup_file}</small>}
                  </td>
                  <td>
                    <Button variant="ghost" aria-label={`查看同步详情 ${run.title}`} onClick={() => setSelectedRun(run)}>
                      <Eye size={13} />明细
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {!runs.data?.items.length && !runs.loading && <Empty>暂无同步记录，点击「立即全量同步」后将在此完整记录</Empty>}
        <Pagination data={runs.data} page={page} onPage={setPage} />
      </Card>

      {selectedRun && (
        <Modal
          wide
          title={`同步详情 · ${selectedRun.title}`}
          subtitle={`记录 ID：${selectedRun.id} · 开始 ${formatDate(selectedRun.started_at)} · 耗时 ${formatDurationMs(selectedRun.elapsed_ms)}`}
          onClose={() => setSelectedRun(null)}
        >
          <div className="binding-grid">
            <div className="binding"><span>状态 / 模式</span><code>{SYNC_STATUS_LABELS[selectedRun.status] || selectedRun.status} · {modeLabel(selectedRun.mode)}</code></div>
            <div className="binding"><span>来源 / 操作人</span><code>{selectedRun.source_origin === "ui" ? "控制台发起" : "外部/历史记录"} · {selectedRun.actor_name}</code></div>
            <div className="binding"><span>拉取 / 新增 / 更新</span><code>{selectedRun.fetched} / {selectedRun.inserted} / {selectedRun.updated}</code></div>
            <div className="binding"><span>跳过 / 失败</span><code>{selectedRun.skipped} / {selectedRun.failed}</code></div>
            <div className="binding"><span>同步前水位</span><code>{selectedRun.watermark_before ? formatDate(selectedRun.watermark_before) : "—"}</code></div>
            <div className="binding"><span>同步后水位</span><code>{selectedRun.watermark_after ? formatDate(selectedRun.watermark_after) : "—"}</code></div>
          </div>

          {selectedRun.backup_file && (
            <Notice>同步前已自动备份至：<code>{selectedRun.backup_file}</code></Notice>
          )}

          {selectedRun.message && (
            <Notice error={selectedRun.status === "failed"}>{selectedRun.message}</Notice>
          )}

          {renderBreakdownLines(selectedRun.detail) && (
            <>
              <h4 className="sync-subhead" style={{ marginTop: 18 }}>分步执行明细</h4>
              <div className="table-wrap">
                <table className="data-table attribute-table admin-table">
                  <thead>
                    <tr><th>步骤</th><th>模式</th><th>拉取</th><th>新增</th><th>更新</th><th>跳过</th><th>失败</th><th>耗时</th><th>说明</th></tr>
                  </thead>
                  <tbody>
                    {(renderBreakdownLines(selectedRun.detail) || []).map((step, idx) => (
                      <tr key={idx}>
                        <td><strong>{String(step.label || step.step || `步骤 ${idx + 1}`)}</strong></td>
                        <td>{modeLabel(String(step.mode || "—"))}</td>
                        <td><strong>{Number(step.fetched || 0).toLocaleString()}</strong></td>
                        <td>{Number(step.inserted || 0).toLocaleString()}</td>
                        <td>{Number(step.updated || 0).toLocaleString()}</td>
                        <td>{Number(step.skipped || 0).toLocaleString()}</td>
                        <td>{Number(step.failed || 0).toLocaleString()}</td>
                        <td>{formatDurationMs(Number(step.elapsed_ms || 0))}</td>
                        <td>{String(step.message || "—")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}

          {selectedRun.detail && !renderBreakdownLines(selectedRun.detail) && (
            <>
              <h4 className="sync-subhead" style={{ marginTop: 18 }}>任务返回细节</h4>
              <pre className="code-block">{JSON.stringify(selectedRun.detail, null, 2)}</pre>
            </>
          )}

          <div className="modal-actions">
            <Button variant="primary" onClick={() => setSelectedRun(null)}>关闭</Button>
          </div>
        </Modal>
      )}
    </>
  );
}
