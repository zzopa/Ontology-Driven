"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  AlertCircle,
  AlertTriangle,
  ArrowRight,
  Ban,
  BarChart3,
  Bell,
  BellOff,
  BellRing,
  Calendar,
  CalendarClock,
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock,
  Download,
  Eye,
  FileCheck2,
  FilePlus2,
  FileQuestion,
  FileText,
  GitCompare,
  Inbox,
  Info,
  Layers,
  Loader2,
  Pause,
  Plane,
  Play,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  Trash2,
  Users,
  X
} from "lucide-react";
import { request } from "@/lib/api";
import { formatDate } from "@/lib/admin";
import type { QueryResult } from "@/lib/types";
import { Badge, Button, Modal, Notice, Spinner } from "./ui";
import s from "./report-center.module.css";

type Report = {
  id: string;
  title: string;
  kind: string;
  status: string;
  error?: string;
  created_at: string;
  attempts: number;
};

type Subscription = {
  id: string;
  kind: string;
  period: string;
  status: string;
  send_time: string;
  include_benchmarks: number;
};

type Notification = {
  id: string;
  report_id: string;
  title: string;
  is_read: number;
  created_at: string;
};

type ExtendedQueryResult = QueryResult & {
  text?: string;
  kind?: string;
  summary?: Record<string, any>;
  document?: {
    title: string;
    sections: { id?: string; title: string; claims: { text: string }[] }[];
  };
  related?: {
    table?: string;
    name?: string;
    note?: string;
    count?: number;
    rows?: Record<string, any>[];
  }[];
  benchmark?: {
    answer?: string;
    main_rows?: Record<string, any>[];
  };
};

const kinds: Record<string, string> = {
  general: "A-CDM通用报告",
  ground_briefing: "地服运行简报",
  safeguard_board: "保障质量与资源看板",
};

const periods: Record<string, string> = {
  day: "日报",
  week: "周报",
  month: "月报",
  quarter: "季报",
  year: "年报",
};

const statuses: Record<string, string> = {
  queued: "排队中",
  running: "生成中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

function getKindIcon(kind: string) {
  switch (kind) {
    case "general":
      return <BarChart3 size={16} />;
    case "ground_briefing":
      return <Plane size={16} />;
    case "safeguard_board":
      return <ShieldCheck size={16} />;
    default:
      return <FileText size={16} />;
  }
}

function getKindClass(kind: string) {
  switch (kind) {
    case "general":
      return s.kindGeneral;
    case "ground_briefing":
      return s.kindGround;
    case "safeguard_board":
      return s.kindSafeguard;
    default:
      return s.kindQuery;
  }
}

function renderStatusPill(status: string) {
  switch (status) {
    case "completed":
      return (
        <span className={s.statusCompleted}>
          <CheckCircle2 size={13} /> 已完成
        </span>
      );
    case "running":
      return (
        <span className={s.statusRunning}>
          <Loader2 size={13} className="spin" /> 生成中
        </span>
      );
    case "queued":
      return (
        <span className={s.statusQueued}>
          <Clock size={13} /> 排队中
        </span>
      );
    case "failed":
      return (
        <span className={s.statusFailed}>
          <AlertCircle size={13} /> 失败
        </span>
      );
    case "cancelled":
      return (
        <span className={s.statusCancelled}>
          <Ban size={13} /> 已取消
        </span>
      );
    default:
      return <Badge>{statuses[status] || status}</Badge>;
  }
}

export function ReportCenter({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState("reports");
  const [page, setPage] = useState(1);
  const [reports, setReports] = useState<Report[]>([]);
  const [total, setTotal] = useState(0);
  const [subscriptions, setSubscriptions] = useState<Subscription[]>([]);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [unread, setUnread] = useState(0);
  const [worker, setWorker] = useState(true);
  const [notePage, setNotePage] = useState(1);
  const [noteTotal, setNoteTotal] = useState(0);

  const [kind, setKind] = useState("general");
  const [period, setPeriod] = useState("day");
  const [target, setTarget] = useState(
    new Date(Date.now() - 86400000).toLocaleDateString("en-CA", {
      timeZone: "Asia/Shanghai",
    })
  );
  const [benchmark, setBenchmark] = useState(false);
  const [clock, setClock] = useState("08:00");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [preview, setPreview] = useState<{
    report: Report;
    result: ExtendedQueryResult;
  } | null>(null);

  const closePreview = useCallback(() => setPreview(null), []);

  const refresh = useCallback(async () => {
    try {
      const [r, subs, notes] = await Promise.all([
        request<{ items: Report[]; total: number; worker_active?: boolean }>(
          `/api/reports?page=${page}`
        ),
        request<{ items: Subscription[] }>("/api/report-subscriptions"),
        request<{ items: Notification[]; unread: number; total: number }>(
          `/api/notifications?page=${notePage}`
        ),
      ]);
      setReports(r.items);
      setTotal(r.total);
      setWorker(r.worker_active !== false);
      setSubscriptions(subs.items);
      setNotifications(notes.items);
      setUnread(notes.unread);
      setNoteTotal(notes.total);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "报告中心读取失败");
    }
  }, [page, notePage]);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 5000);
    return () => clearInterval(timer);
  }, [refresh]);

  async function action(fn: () => Promise<unknown>, success: string) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await fn();
      if (success) setMessage(success);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }

  async function generate(e: FormEvent) {
    e.preventDefault();
    await action(
      () =>
        request("/api/reports", {
          method: "POST",
          body: JSON.stringify({
            kind,
            period,
            target_date: target,
            include_benchmarks: benchmark,
          }),
        }),
      "已创建报告任务，生成后可下载高保真 PDF 报告。"
    );
  }

  async function subscribe(e: FormEvent) {
    e.preventDefault();
    await action(
      () =>
        request("/api/report-subscriptions", {
          method: "POST",
          body: JSON.stringify({
            kind,
            period,
            send_time: clock,
            status: "active",
            include_benchmarks: benchmark,
          }),
        }),
      "已启用站内定时订阅。将在所选周期发送日生成上一完整周期报告并推送通知。"
    );
  }

  return (
    <Modal
      title="运行报告中心"
      subtitle="民航 A-CDM 运营报告归档、定时订阅与决策简报 · 北京时间"
      wide
      onClose={onClose}
    >
      <div className={s.center}>
        {/* Top Status & Metrics Bar */}
        <div className={s.topBanner}>
          <div className={s.bannerLeft}>
            {worker ? (
              <span className={s.serviceBadge}>
                <span className={s.pulseDot} /> 报告服务就绪
              </span>
            ) : (
              <span className={s.serviceBadgeWarn}>
                <AlertTriangle size={13} /> 后台服务未运行
              </span>
            )}
            <span className={s.statPill}>
              <FileText size={13} /> 报告归档 {total} 份
            </span>
            <span className={s.statPill}>
              <CalendarClock size={13} />{" "}
              {subscriptions.filter((s) => s.status === "active").length} 项活跃订阅
            </span>
          </div>
          <Button
            variant="ghost"
            className={s.refreshBtn}
            onClick={refresh}
            aria-label="刷新报告中心"
          >
            <RefreshCw size={13} className={busy ? "spin" : ""} /> 刷新
          </Button>
        </div>

        {/* Tab Navigation */}
        <div className={s.tabs} role="tablist" aria-label="报告中心栏目">
          <button
            role="tab"
            aria-selected={tab === "reports"}
            className={tab === "reports" ? s.tabActive : s.tab}
            onClick={() => setTab("reports")}
          >
            <FileText size={15} /> 报告历史{" "}
            <span className={s.tabBadge}>{total}</span>
          </button>
          <button
            role="tab"
            aria-selected={tab === "subscriptions"}
            className={tab === "subscriptions" ? s.tabActive : s.tab}
            onClick={() => setTab("subscriptions")}
          >
            <CalendarClock size={15} /> 定时订阅{" "}
            <span className={s.tabBadge}>{subscriptions.length}</span>
          </button>
          <button
            role="tab"
            aria-selected={tab === "notifications"}
            className={tab === "notifications" ? s.tabActive : s.tab}
            onClick={() => setTab("notifications")}
          >
            <Bell size={15} /> 通知中心{" "}
            {unread > 0 && <span className={s.unreadBadge}>{unread}</span>}
          </button>
        </div>

        {error && <Notice error>{error}</Notice>}
        {message && <Notice>{message}</Notice>}
        {!worker && (
          <Notice>
            报告后台工作进程尚未启动，新提交的任务将保存在排队队列中。启动后台后将自动生成
            PDF。
          </Notice>
        )}

        {/* Tab 1: 报告历史 */}
        {tab === "reports" && (
          <>
            {/* Create Report Card */}
            <form className={s.createCard} onSubmit={generate}>
              <div className={s.createHeader}>
                <div className={s.createTitle}>
                  <Sparkles size={16} className={s.accentIcon} />
                  <strong>生成民航运行分析报告</strong>
                </div>
                <span className={s.createDesc}>
                  严格按 A-CDM 手册规范统计航班正常率、过站保障与资源周转指标
                </span>
              </div>

              <div className={s.formGrid}>
                <label className={s.field}>
                  <span className={s.fieldLabel}>
                    <BarChart3 size={14} /> 报告类型
                  </span>
                  <select
                    value={kind}
                    onChange={(e) => {
                      setKind(e.target.value);
                      if (e.target.value === "ground_briefing") setPeriod("day");
                    }}
                  >
                    {Object.entries(kinds).map(([key, name]) => (
                      <option key={key} value={key}>
                        {name}
                      </option>
                    ))}
                  </select>
                </label>

                <label className={s.field}>
                  <span className={s.fieldLabel}>
                    <Calendar size={14} /> 统计周期
                  </span>
                  <select
                    value={period}
                    onChange={(e) => setPeriod(e.target.value)}
                  >
                    {Object.entries(periods)
                      .filter(
                        ([key]) => kind !== "ground_briefing" || key === "day"
                      )
                      .map(([key, name]) => (
                        <option key={key} value={key}>
                          {name}
                        </option>
                      ))}
                  </select>
                </label>

                <label className={s.field}>
                  <span className={s.fieldLabel}>
                    <Clock size={14} /> 目标基准日
                  </span>
                  <input
                    type="date"
                    value={target}
                    required
                    onChange={(e) => setTarget(e.target.value)}
                  />
                </label>
              </div>

              <div className={s.formBottomRow}>
                <label className={s.toggleField}>
                  <input
                    type="checkbox"
                    checked={benchmark}
                    onChange={(e) => setBenchmark(e.target.checked)}
                  />
                  <span className={s.toggleBox}>
                    <GitCompare size={14} /> 附跨场对标（含郑州、长沙、南京模拟测试数据）
                  </span>
                </label>

                <Button
                  type="submit"
                  variant="primary"
                  disabled={busy}
                  className={s.submitBtn}
                >
                  {busy ? <Spinner /> : <FilePlus2 size={16} />} 立即生成 PDF 报告
                </Button>
              </div>
            </form>

            {/* Reports List Table */}
            <div className={s.tableCard}>
              <div className={s.tableHeaderBar}>
                <span>报告历史归档 · 共 {total} 份</span>
              </div>
              <div className={s.tableWrap}>
                <table className={s.reportTable}>
                  <thead>
                    <tr>
                      <th>报告信息</th>
                      <th>生成时间</th>
                      <th>状态</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {reports.map((row) => (
                      <tr key={row.id}>
                        <td>
                          <div className={s.reportInfoCell}>
                            <span
                              className={`${s.kindBadge} ${getKindClass(
                                row.kind
                              )}`}
                            >
                              {getKindIcon(row.kind)}
                            </span>
                            <div className={s.reportDetails}>
                              <div className={s.reportTitle}>{row.title}</div>
                              <div className={s.reportPills}>
                                <Badge tone="neutral">
                                  {kinds[row.kind] || row.kind}
                                </Badge>
                              </div>
                              {row.error && (
                                <div className={s.rowError}>
                                  <AlertCircle size={13} /> {row.error}
                                </div>
                              )}
                            </div>
                          </div>
                        </td>
                        <td className={s.timeCell}>
                          {formatDate(row.created_at)}
                        </td>
                        <td>{renderStatusPill(row.status)}</td>
                        <td>
                          <div className={s.actionGroup}>
                            {row.status === "failed" && (
                              <Button
                                variant="secondary"
                                className={s.actionBtn}
                                disabled={busy}
                                onClick={() =>
                                  action(
                                    () =>
                                      request(`/api/reports/${row.id}/retry`, {
                                        method: "POST",
                                      }),
                                    "已重新加入报告队列"
                                  )
                                }
                              >
                                <RotateCcw size={13} /> 重试
                              </Button>
                            )}
                            <Button
                              variant="secondary"
                              className={s.actionBtn}
                              disabled={row.status !== "completed" || busy}
                              onClick={() =>
                                action(async () => {
                                  const p = await request<{
                                    report: Report;
                                    result: ExtendedQueryResult;
                                  }>(`/api/reports/${row.id}`);
                                  setPreview(p);
                                }, "")
                              }
                            >
                              <Eye size={13} /> 预览
                            </Button>
                            {row.status === "completed" && (
                              <a
                                className={`btn btn-secondary ${s.actionBtn}`}
                                href={`/api/reports/${row.id}/download`}
                              >
                                <Download size={13} /> PDF
                              </a>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {!reports.length && (
                <div className={s.emptyBox}>
                  <FileQuestion size={44} className={s.emptyIcon} />
                  <h4>暂无报告记录</h4>
                  <p>
                    您可以在上方选择日期生成报告，或在问询对话结果中直接导出 PDF
                    报告。
                  </p>
                </div>
              )}

              <div className={s.paginationBar}>
                <Button
                  disabled={page <= 1}
                  onClick={() => setPage((p) => p - 1)}
                >
                  <ChevronLeft size={14} /> 上一页
                </Button>
                <span className={s.pageIndicator}>
                  第 {page} 页 / 共 {Math.ceil(total / 20) || 1} 页
                </span>
                <Button
                  disabled={page * 20 >= total}
                  onClick={() => setPage((p) => p + 1)}
                >
                  下一页 <ChevronRight size={14} />
                </Button>
              </div>
            </div>
          </>
        )}

        {/* Tab 2: 站内订阅 */}
        {tab === "subscriptions" && (
          <>
            <form className={s.createCard} onSubmit={subscribe}>
              <div className={s.createHeader}>
                <div className={s.createTitle}>
                  <CalendarClock size={16} className={s.accentIcon} />
                  <strong>新建定时订阅任务</strong>
                </div>
                <span className={s.createDesc}>
                  周期发送日自动生成上一周期完整报告并发送站内通知
                </span>
              </div>

              <div className={s.formGrid}>
                <label className={s.field}>
                  <span className={s.fieldLabel}>
                    <BarChart3 size={14} /> 报告类型
                  </span>
                  <select
                    value={kind}
                    onChange={(e) => {
                      setKind(e.target.value);
                      if (e.target.value === "ground_briefing") setPeriod("day");
                    }}
                  >
                    {Object.entries(kinds).map(([key, name]) => (
                      <option key={key} value={key}>
                        {name}
                      </option>
                    ))}
                  </select>
                </label>

                <label className={s.field}>
                  <span className={s.fieldLabel}>
                    <Calendar size={14} /> 统计周期
                  </span>
                  <select
                    value={period}
                    onChange={(e) => setPeriod(e.target.value)}
                  >
                    {Object.entries(periods)
                      .filter(
                        ([key]) => kind !== "ground_briefing" || key === "day"
                      )
                      .map(([key, name]) => (
                        <option key={key} value={key}>
                          {name}
                        </option>
                      ))}
                  </select>
                </label>

                <label className={s.field}>
                  <span className={s.fieldLabel}>
                    <Clock size={14} /> 发送时刻 (北京时间)
                  </span>
                  <input
                    type="time"
                    value={clock}
                    required
                    onChange={(e) => setClock(e.target.value)}
                  />
                </label>
              </div>

              <div className={s.formBottomRow}>
                <label className={s.toggleField}>
                  <input
                    type="checkbox"
                    checked={benchmark}
                    onChange={(e) => setBenchmark(e.target.checked)}
                  />
                  <span className={s.toggleBox}>
                    <GitCompare size={14} /> 附跨场对标（含模拟测试数据）
                  </span>
                </label>

                <Button
                  type="submit"
                  variant="primary"
                  disabled={busy}
                  className={s.submitBtn}
                >
                  <Bell size={16} /> 启用定时订阅
                </Button>
              </div>

              <p className={s.helperText}>
                <Info size={14} />{" "}
                日报每天、周报周一、月报每月1日、季报季度首日、年报1月1日自动执行；若本地同步历史不足将记录失败原因并显示。
              </p>
            </form>

            <div className={s.subsList}>
              {subscriptions.map((row) => (
                <div className={s.subCard} key={row.id}>
                  <div className={s.subLeft}>
                    <span
                      className={`${s.kindBadge} ${getKindClass(row.kind)}`}
                    >
                      {getKindIcon(row.kind)}
                    </span>
                    <div className={s.subDetails}>
                      <strong>
                        {kinds[row.kind]} · {periods[row.period]}
                      </strong>
                      <div className={s.subMeta}>
                        <span>
                          <Clock size={12} /> 北京时间 {row.send_time}
                        </span>
                        <span>·</span>
                        <span>
                          {row.include_benchmarks ? "含跨场对标" : "本场运行数据"}
                        </span>
                      </div>
                    </div>
                  </div>

                  <div className={s.subRight}>
                    <Badge
                      tone={row.status === "active" ? "green" : "neutral"}
                    >
                      {row.status === "active" ? "已启用" : "已暂停"}
                    </Badge>
                    <Button
                      variant="secondary"
                      className={s.actionBtn}
                      disabled={busy}
                      onClick={() =>
                        action(
                          () =>
                            request(`/api/report-subscriptions/${row.id}`, {
                              method: "PATCH",
                              body: JSON.stringify({
                                ...row,
                                include_benchmarks: Boolean(
                                  row.include_benchmarks
                                ),
                                status:
                                  row.status === "active" ? "paused" : "active",
                              }),
                            }),
                          "订阅状态已更新"
                        )
                      }
                    >
                      {row.status === "active" ? (
                        <>
                          <Pause size={13} /> 暂停
                        </>
                      ) : (
                        <>
                          <Play size={13} /> 恢复
                        </>
                      )}
                    </Button>
                    <Button
                      variant="secondary"
                      className={s.actionBtn}
                      disabled={busy}
                      onClick={() =>
                        action(
                          () =>
                            request(`/api/report-subscriptions/${row.id}`, {
                              method: "DELETE",
                            }),
                          "已取消订阅"
                        )
                      }
                    >
                      <Trash2 size={13} /> 取消
                    </Button>
                  </div>
                </div>
              ))}

              {!subscriptions.length && (
                <div className={s.emptyBox}>
                  <BellOff size={44} className={s.emptyIcon} />
                  <h4>暂无活跃订阅</h4>
                  <p>您可以在上方设定每日或定期报告推送，及时获取最新运行动态。</p>
                </div>
              )}
            </div>
          </>
        )}

        {/* Tab 3: 通知中心 */}
        {tab === "notifications" && (
          <div className={s.notesList}>
            {notifications.map((note) => (
              <div
                className={`${s.noteCard} ${!note.is_read ? s.isUnread : ""}`}
                key={note.id}
              >
                <div className={s.noteLeft}>
                  <BellRing size={18} className={s.noteIcon} />
                  <div>
                    <div className={s.noteTitle}>{note.title}</div>
                    <div className={s.noteTime}>
                      <Clock size={12} /> {formatDate(note.created_at)}
                    </div>
                  </div>
                </div>

                <div className={s.subRight}>
                  {!note.is_read && <Badge tone="green">未读</Badge>}
                  <Button
                    variant="secondary"
                    className={s.actionBtn}
                    onClick={() =>
                      action(async () => {
                        const p = await request<{
                          report: Report;
                          result: ExtendedQueryResult;
                        }>(`/api/reports/${note.report_id}`);
                        await request(`/api/notifications/${note.id}`, {
                          method: "PATCH",
                        });
                        setPreview(p);
                      }, "")
                    }
                  >
                    <Eye size={13} /> 查看报告
                  </Button>
                </div>
              </div>
            ))}

            {!notifications.length && (
              <div className={s.emptyBox}>
                <Inbox size={44} className={s.emptyIcon} />
                <h4>暂无站内通知</h4>
                <p>订阅的报告生成完成后，将在此处收到提醒通知。</p>
              </div>
            )}

            <div className={s.paginationBar}>
              <Button
                disabled={notePage <= 1}
                onClick={() => setNotePage((p) => p - 1)}
              >
                <ChevronLeft size={14} /> 上一页
              </Button>
              <span className={s.pageIndicator}>
                第 {notePage} 页 · 共 {noteTotal} 条通知
              </span>
              <Button
                disabled={notePage * 20 >= noteTotal}
                onClick={() => setNotePage((p) => p + 1)}
              >
                下一页 <ChevronRight size={14} />
              </Button>
            </div>
          </div>
        )}

        {/* Rich Report Preview Modal */}
        {preview && (
          <Modal
            title={preview.report.title}
            subtitle="民航 A-CDM 运行报告快照预览"
            onClose={closePreview}
            wide
          >
            <div className={s.previewRoot}>
              <div className={s.previewTop}>
                <div className={s.previewMeta}>
                  <Badge tone="green">
                    {kinds[preview.report.kind] || preview.report.kind}
                  </Badge>
                  <span className={s.timeCell}>
                    生成于 {formatDate(preview.report.created_at)}
                  </span>
                </div>
                <a
                  className="btn btn-primary"
                  href={`/api/reports/${preview.report.id}/download`}
                >
                  <Download size={14} /> 下载完整 PDF
                </a>
              </div>

              {preview.result.is_simulated && (
                <div className={s.previewWarn}>
                  <AlertTriangle size={16} />
                  <span>
                    包含模拟测试数据，仅用于功能验证，不代表长沙、南京、郑州机场实际运行。
                  </span>
                </div>
              )}

              {/* KPI Cards in Preview if available */}
              {preview.result.summary && (
                <div className={s.previewKpiGrid}>
                  {preview.result.summary.operated_all !== undefined && (
                    <div className={`${s.previewKpiCard} ${s.kpiToneBlue}`}>
                      <div className={s.kpiIconWrap}>
                        <Plane size={15} />
                      </div>
                      <div className={s.kpiContent}>
                        <div className={s.kpiLabelText}>实际执行航班</div>
                        <div className={s.kpiValueRow}>
                          <span className={s.kpiBigVal}>
                            {preview.result.summary.operated_all}
                          </span>
                          <span className={s.kpiUnitText}>架次</span>
                        </div>
                        <div className={s.kpiSubText}>
                          计划 {preview.result.summary.planned_all || "—"} · 执行率{" "}
                          {preview.result.summary.execution_rate || "—"}%
                        </div>
                      </div>
                    </div>
                  )}

                  {preview.result.summary.release_on_time_rate !== undefined && (
                    <div className={`${s.previewKpiCard} ${s.kpiToneEmerald}`}>
                      <div className={s.kpiIconWrap}>
                        <CheckCircle2 size={15} />
                      </div>
                      <div className={s.kpiContent}>
                        <div className={s.kpiLabelText}>放行正常率</div>
                        <div className={s.kpiValueRow}>
                          <span className={s.kpiBigVal}>
                            {preview.result.summary.release_on_time_rate}%
                          </span>
                        </div>
                        <div className={s.kpiSubText}>
                          正常出港 {preview.result.summary.release_normal_cnt || "—"} 班
                        </div>
                      </div>
                    </div>
                  )}

                  {preview.result.summary.takeoff_on_time_rate !== undefined && (
                    <div className={`${s.previewKpiCard} ${s.kpiToneTeal}`}>
                      <div className={s.kpiIconWrap}>
                        <Clock size={15} />
                      </div>
                      <div className={s.kpiContent}>
                        <div className={s.kpiLabelText}>起飞正常率</div>
                        <div className={s.kpiValueRow}>
                          <span className={s.kpiBigVal}>
                            {preview.result.summary.takeoff_on_time_rate}%
                          </span>
                        </div>
                        <div className={s.kpiSubText}>
                          正常起飞 {preview.result.summary.takeoff_normal_cnt || "—"} 架次
                        </div>
                      </div>
                    </div>
                  )}

                  {preview.result.summary.origin_dep_on_time_rate !== undefined && (
                    <div className={`${s.previewKpiCard} ${s.kpiToneIndigo}`}>
                      <div className={s.kpiIconWrap}>
                        <ShieldCheck size={15} />
                      </div>
                      <div className={s.kpiContent}>
                        <div className={s.kpiLabelText}>始发正常率</div>
                        <div className={s.kpiValueRow}>
                          <span className={s.kpiBigVal}>
                            {preview.result.summary.origin_dep_on_time_rate}%
                          </span>
                        </div>
                        <div className={s.kpiSubText}>
                          始发出港 {preview.result.summary.origin_normal_cnt || "—"} 班
                        </div>
                      </div>
                    </div>
                  )}

                  {preview.result.summary.bridge_rate_all !== undefined && (
                    <div className={`${s.previewKpiCard} ${s.kpiToneAmber}`}>
                      <div className={s.kpiIconWrap}>
                        <Layers size={15} />
                      </div>
                      <div className={s.kpiContent}>
                        <div className={s.kpiLabelText}>综合靠桥率</div>
                        <div className={s.kpiValueRow}>
                          <span className={s.kpiBigVal}>
                            {preview.result.summary.bridge_rate_all}%
                          </span>
                        </div>
                        <div className={s.kpiSubText}>
                          进港靠桥 {preview.result.summary.bridge_rate_arr || "—"}%
                        </div>
                      </div>
                    </div>
                  )}

                  {preview.result.summary.total_pax_sql !== undefined && (
                    <div className={`${s.previewKpiCard} ${s.kpiTonePurple}`}>
                      <div className={s.kpiIconWrap}>
                        <Users size={15} />
                      </div>
                      <div className={s.kpiContent}>
                        <div className={s.kpiLabelText}>旅客吞吐量</div>
                        <div className={s.kpiValueRow}>
                          <span className={s.kpiBigVal}>
                            {Number(
                              preview.result.summary.total_pax_sql
                            ).toLocaleString()}
                          </span>
                          <span className={s.kpiUnitText}>人次</span>
                        </div>
                        <div className={s.kpiSubText}>
                          综合客座率 {preview.result.summary.total_load_factor || "—"}%
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Document Sections Preview */}
              {preview.result.document?.sections &&
              preview.result.document.sections.length > 0 ? (
                <div className={s.previewSections}>
                  {preview.result.document.sections.map((sec, idx) => (
                    <div className={s.previewSection} key={sec.id || idx}>
                      <div className={s.previewSecHeading}>
                        <BarChart3 size={15} className={s.accentIcon} />
                        <span>{sec.title}</span>
                      </div>
                      <div className={s.previewClaimList}>
                        {sec.claims.map((claim, cIdx) => (
                          <div className={s.previewClaim} key={cIdx}>
                            <Check size={14} className={s.previewClaimBullet} />
                            <span>{claim.text}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className={s.previewAnswerCard}>
                  {preview.result.answer ||
                    preview.result.text ||
                    "报告已生成完毕，请下载 PDF 查看完整数据与分析。"}
                </div>
              )}

              {/* Related Tables Preview if present */}
              {preview.result.related && preview.result.related.length > 0 && (
                <div className={s.previewSections}>
                  {preview.result.related.slice(0, 2).map((rel, relIdx) => {
                    const rows = rel.rows || [];
                    if (!rows.length) return null;
                    const cols = Object.keys(rows[0] || {}).slice(0, 6);
                    return (
                      <div className={s.previewSection} key={relIdx}>
                        <div className={s.previewSecHeading}>
                          <FileText size={15} className={s.accentIcon} />
                          <span>{rel.name || rel.table}</span>
                          {rel.count && (
                            <Badge tone="neutral">共 {rel.count} 条记录</Badge>
                          )}
                        </div>
                        <div className={s.previewTableWrap}>
                          <table className={s.previewTable}>
                            <thead>
                              <tr>
                                {cols.map((col) => (
                                  <th key={col}>{col}</th>
                                ))}
                              </tr>
                            </thead>
                            <tbody>
                              {rows.slice(0, 5).map((row, rIdx) => (
                                <tr key={rIdx}>
                                  {cols.map((col) => (
                                    <td key={col}>{String(row[col] ?? "—")}</td>
                                  ))}
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}

              <div className={s.previewActions}>
                <Button variant="secondary" onClick={closePreview}>
                  关闭
                </Button>
                <a
                  className="btn btn-primary"
                  href={`/api/reports/${preview.report.id}/download`}
                >
                  <Download size={14} /> 下载完整 PDF
                </a>
              </div>
            </div>
          </Modal>
        )}
      </div>
    </Modal>
  );
}
