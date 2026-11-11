"use client";

import { useCallback, useState } from "react";
import { AlertTriangle, RefreshCw, Save, Search, Sparkles } from "lucide-react";
import {
  FEEDBACK_REASON_LABELS,
  FEEDBACK_STATUS_LABELS,
  formatDate,
  govStatusBadge,
  type AskFeedbackItem,
  type OpsConcept,
  type Paged,
} from "@/lib/admin";
import { request } from "@/lib/api";
import { Badge, Button, Card, Modal, Notice } from "../ui";
import { Empty, Loading, Pagination, useAction, useResource } from "./shared";

type FeedbackPage = Paged<AskFeedbackItem> & { counts?: Record<string, number> };

const STATUS_TABS = [
  { key: "OPEN", label: "待处理" },
  { key: "INVESTIGATING", label: "跟进中" },
  { key: "FIXED", label: "已修复" },
  { key: "WONT_FIX", label: "不处理" },
  { key: "ALL", label: "全部" },
];

export function FeedbackAdminPanel({ onUpdated }: { onUpdated?: () => void }) {
  const [statusTab, setStatusTab] = useState("OPEN");
  const [reasonFilter, setReasonFilter] = useState("");
  const [severityFilter, setSeverityFilter] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);

  const url = `/api/admin/feedback?status=${encodeURIComponent(statusTab)}&reason=${encodeURIComponent(reasonFilter)}&severity=${encodeURIComponent(severityFilter)}&start_date=${encodeURIComponent(startDate)}&end_date=${encodeURIComponent(endDate)}&search=${encodeURIComponent(query)}&page=${page}`;
  const res = useResource<FeedbackPage>(url);
  const concepts = useResource<{ ok: boolean; items: OpsConcept[] }>("/api/admin/ontology/concepts");
  const action = useAction();

  const [detail, setDetail] = useState<AskFeedbackItem | null>(null);
  const [editStatus, setEditStatus] = useState("OPEN");
  const [editNote, setEditNote] = useState("");
  const [promoteConcept, setPromoteConcept] = useState("FlightDelay");
  const [promoteTerm, setPromoteTerm] = useState("");

  const closeDetail = useCallback(() => setDetail(null), []);

  function openDetail(item: AskFeedbackItem) {
    setDetail(item);
    setEditStatus(item.status);
    setEditNote(item.admin_note || "");
    setPromoteConcept(concepts.data?.items[0]?.code || "FlightDelay");
    setPromoteTerm("");
  }

  const counts = res.data?.counts || {};

  return (
    <>
      <div className="admin-section-head">
        <div>
          <h2>不准反馈</h2>
          <p>收集问答用户提交的不准确/未答出反馈，核查执行 SQL 并一键沉淀为本体同义词</p>
        </div>
        <Button onClick={() => { res.reload(); onUpdated?.(); }}><RefreshCw size={13} />刷新列表</Button>
      </div>

      {action.feedback}

      <div className="feedback-pills" role="tablist" aria-label="反馈处理状态">
        {STATUS_TABS.map(t => (
          <button
            role="tab"
            aria-selected={statusTab === t.key}
            key={t.key}
            className={`gov-subtab ${statusTab === t.key ? "selected" : ""}`}
            onClick={() => { setStatusTab(t.key); setPage(1); }}
          >
            {t.label} <Badge>{counts[t.key] ?? 0}</Badge>
          </button>
        ))}
      </div>

      <Card className="card-pad">
        <form className="admin-toolbar" style={{ flexWrap: "wrap" }} onSubmit={e => { e.preventDefault(); setQuery(searchInput); setPage(1); }}>
          <label className="admin-search">
            <Search size={14} />
            <input value={searchInput} onChange={e => setSearchInput(e.target.value)} placeholder="搜索问题、用户名或处理备注" />
          </label>
          <select aria-label="问题类型筛选" value={reasonFilter} onChange={e => { setReasonFilter(e.target.value); setPage(1); }}>
            <option value="">全部原因</option>
            {Object.entries(FEEDBACK_REASON_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <select aria-label="严重级别筛选" value={severityFilter} onChange={e => { setSeverityFilter(e.target.value); setPage(1); }}>
            <option value="">全部级别</option>
            <option value="HIGH">严重 (HIGH)</option>
            <option value="NORMAL">一般 (NORMAL)</option>
          </select>
          <input type="date" aria-label="开始日期" value={startDate} onChange={e => { setStartDate(e.target.value); setPage(1); }} />
          <input type="date" aria-label="结束日期" value={endDate} onChange={e => { setEndDate(e.target.value); setPage(1); }} />
          <Button type="submit">搜索</Button>
          {(reasonFilter || severityFilter || startDate || endDate || query) && (
            <Button type="button" variant="ghost" onClick={() => {
              setReasonFilter(""); setSeverityFilter(""); setStartDate(""); setEndDate(""); setSearchInput(""); setQuery(""); setPage(1);
            }}>重置</Button>
          )}
        </form>

        <Loading {...res} />

        <div className="table-wrap">
          <table className="data-table attribute-table admin-table">
            <thead>
              <tr><th>提交时间</th><th>提交人</th><th>原问题</th><th>问题类型</th><th>严重度</th><th>状态</th><th>操作</th></tr>
            </thead>
            <tbody>
              {(res.data?.items || []).map(item => {
                const st = govStatusBadge(item.status);
                return (
                  <tr key={item.id}>
                    <td>{formatDate(item.created_at)}</td>
                    <td><strong>{item.display_name || item.username || item.user_id}</strong></td>
                    <td><span className="gov-clamp">{item.question_text}</span></td>
                    <td>{item.reason_codes.map(c => <Badge key={c}>{FEEDBACK_REASON_LABELS[c] || c}</Badge>)}</td>
                    <td><Badge tone={item.severity === "HIGH" ? "amber" : "neutral"}>{item.severity === "HIGH" ? "严重" : "一般"}</Badge></td>
                    <td><Badge tone={st.tone}>{FEEDBACK_STATUS_LABELS[item.status] || st.label}</Badge></td>
                    <td><Button variant="ghost" onClick={() => openDetail(item)}>查看详情</Button></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {!res.data?.items.length && !res.loading && <Empty>当前状态下暂无不准反馈记录</Empty>}
        <Pagination data={res.data || null} page={page} onPage={setPage} />
      </Card>

      {detail && (
        <Modal title="反馈详情与本体闭环处置" subtitle={`提交人：${detail.display_name || detail.username} · ${formatDate(detail.created_at)}`} wide onClose={closeDetail}>
          <div className="gov-stack">
            <div>
              <strong style={{ fontSize: 13 }}>用户提问</strong>
              <p className="long-description">{detail.question_text}</p>
            </div>
            <div>
              <strong style={{ fontSize: 13 }}>原系统回答</strong>
              <pre className="code-block">{detail.answer_text || "（空）"}</pre>
            </div>
            {detail.comment && <Notice>用户补充说明：{detail.comment}</Notice>}
            {detail.response_summary && (
              <details className="process-details">
                <summary>查看命中解析摘要（主体 / SQL / 版本）</summary>
                <pre className="code-block">{JSON.stringify(detail.response_summary, null, 2)}</pre>
              </details>
            )}

            <div className="card-divider" />

            <form onSubmit={e => {
              e.preventDefault();
              action.run(async () => {
                await request(`/api/admin/feedback/${detail.id}`, {
                  method: "PATCH",
                  body: JSON.stringify({ status: editStatus, adminNote: editNote }),
                });
                setDetail(null);
                res.reload();
                onUpdated?.();
              }, "反馈处置状态已更新");
            }}>
              <div className="form-grid">
                <label className="form-field">处理状态
                  <select value={editStatus} onChange={e => setEditStatus(e.target.value)}>
                    {Object.entries(FEEDBACK_STATUS_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </select>
                </label>
                <label className="form-field form-span">处理备注
                  <textarea rows={2} maxLength={1000} value={editNote} onChange={e => setEditNote(e.target.value)} placeholder="记录定位原因或修复说明…" />
                </label>
              </div>
              <div className="modal-actions">
                <Button onClick={closeDetail}>关闭</Button>
                <Button type="submit" variant="primary" disabled={action.busy}><Save size={13} />保存处理状态</Button>
              </div>
            </form>

            <div className="card-divider" />
            <div className="gov-stack">
              <strong style={{ fontSize: 13 }}><Sparkles size={13} style={{ display: "inline", marginRight: 6 }} />本体闭环：直接沉淀同义词并标记「已修复」</strong>
              <div className="form-grid">
                <label className="form-field">归属概念
                  <select value={promoteConcept} onChange={e => setPromoteConcept(e.target.value)}>
                    {(concepts.data?.items || []).map(c => <option key={c.code} value={c.code}>{c.name} ({c.code})</option>)}
                  </select>
                </label>
                <label className="form-field">沉淀触发同义词
                  <input value={promoteTerm} onChange={e => setPromoteTerm(e.target.value)} placeholder="输入从该问题中提取的同义触发词" />
                </label>
              </div>
              <div className="modal-actions">
                <Button
                  disabled={action.busy || !promoteTerm.trim()}
                  onClick={() => action.run(async () => {
                    await request(`/api/admin/feedback/${detail.id}/promote-synonym`, {
                      method: "POST",
                      body: JSON.stringify({ conceptCode: promoteConcept, term: promoteTerm, adminNote: editNote }),
                    });
                    setDetail(null);
                    res.reload();
                    onUpdated?.();
                  }, "已写入同义词、发布至实时本体并标记为已修复")}
                >
                  <AlertTriangle size={13} />沉淀同义词并发布修复
                </Button>
              </div>
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}
