"use client";

import { useCallback, useState } from "react";
import { Archive, Download, MessageSquare, Pencil, RefreshCw, Save, Search, Trash2 } from "lucide-react";
import { request } from "@/lib/api";
import { formatDate, type Conversation, type ConversationDetail, type Paged, type User } from "@/lib/admin";
import { Badge, Button, Card, Modal, Notice } from "../ui";
import { ResultView } from "../result-view";
import { ExecutionProcess } from "../execution-process";
import { Empty, Loading, Pagination, useAction, useResource } from "./shared";

export function ConversationViewer({ detail, onClose }: { detail: ConversationDetail; onClose: () => void }) {
  const [selected, setSelected] = useState(detail.messages.at(-1)?.id || "");
  const message = detail.messages.find(item => item.id === selected);
  const action = useAction();
  function exportFile() {
    const blob = new Blob([JSON.stringify(detail, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob), link = document.createElement("a"); link.href = url; link.download = `conversation-${detail.conversation.id}.json`; link.click(); URL.revokeObjectURL(url);
  }
  return <Modal wide title={detail.conversation.title} subtitle="历史查询快照 · 不等于当前数据库最新数据 · 明细令牌不持久保存" onClose={onClose}><div className="admin-section-head"><label className="form-field" style={{ flex: 1 }}>查询记录<select value={selected} onChange={e => setSelected(e.target.value)}>{detail.messages.map(item => <option value={item.id} key={item.id}>{item.question} · {formatDate(item.created_at)}</option>)}</select></label><Button onClick={exportFile}><Download size={13} />导出会话</Button></div>{message && <><div className="admin-message-meta"><Badge tone={message.status === "completed" ? "green" : "amber"}>{({ completed: "已完成", running: "查询中", failed: "失败", interrupted: "服务重启中断" } as Record<string, string>)[message.status] || message.status}</Badge><span>模型 {message.model}</span><span>{formatDate(message.created_at)}</span></div>{message.error && <Notice error>{message.error}</Notice>}{message.result ? <ResultView key={message.id} result={message.result} /> : <><ExecutionProcess key={message.id} steps={message.execution_trace || []} /><Empty>本次查询没有已完成结果</Empty></>}</>}{action.feedback}</Modal>;
}

export function ConversationsPanel({ current, apiPrefix = "/api/conversations" }: { current: User; apiPrefix?: string }) {
  const scope = "mine";
  const [search, setSearch] = useState(""), [query, setQuery] = useState(""), [page, setPage] = useState(1), [archived, setArchived] = useState(false);
  const path = `${apiPrefix}?scope=${scope}&page=${page}&archived=${archived}&search=${encodeURIComponent(query)}`;
  const resource = useResource<Paged<Conversation>>(path), action = useAction();
  const [detail, setDetail] = useState<ConversationDetail | null>(null), [rename, setRename] = useState<Conversation | null>(null), [title, setTitle] = useState(""), [deleting, setDeleting] = useState<Conversation | null>(null);
  const closeDetail = useCallback(() => setDetail(null), []), closeRename = useCallback(() => setRename(null), []), closeDelete = useCallback(() => setDeleting(null), []);
  return <><div className="admin-section-head"><div><h2>会话管理</h2><p>查看自己的问题与回答，保留来源证据、SQL 和查询时间</p></div><Button onClick={resource.reload}><RefreshCw size={13} />刷新</Button></div><Card className="card-pad"><form className="admin-toolbar" onSubmit={e => { e.preventDefault(); setQuery(search); setPage(1); }}><label className="admin-search"><Search size={14} /><input aria-label="搜索会话" placeholder="搜索会话名称" value={search} onChange={e => setSearch(e.target.value)} /></label><Button type="submit">搜索</Button><select aria-label="会话归档状态" value={String(archived)} onChange={e => { setArchived(e.target.value === "true"); setPage(1); }}><option value="false">使用中</option><option value="true">已归档</option></select></form>{action.feedback}<Loading {...resource} />{resource.data?.items.length ? <div className="admin-conversations">{resource.data.items.map(item => <div className="admin-conversation" key={item.id}><span className="admin-icon"><MessageSquare size={17} /></span><button className="admin-conversation-title" disabled={action.busy} onClick={() => action.run(async () => setDetail(await request<ConversationDetail>(`${apiPrefix}/${item.id}?scope=${scope}`)), "会话已加载")}><strong>{item.title}</strong><small>{current.display_name} · {item.message_count} 次查询 · {formatDate(item.updated_at)}</small></button><div className="manager-tools"><Button variant="ghost" aria-label={`重命名会话 ${item.title}`} onClick={() => { setRename(item); setTitle(item.title); }}><Pencil size={13} /></Button><Button variant="ghost" disabled={action.busy} onClick={() => action.run(async () => { await request(`${apiPrefix}/${item.id}?scope=${scope}`, { method: "PATCH", body: JSON.stringify({ archived: !archived }) }); resource.reload(); }, archived ? "已恢复会话" : "已归档会话")}><Archive size={13} />{archived ? "恢复" : "归档"}</Button><Button variant="ghost" aria-label={`删除会话 ${item.title}`} onClick={() => setDeleting(item)}><Trash2 size={13} /></Button></div></div>)}</div> : !resource.loading && <Empty>没有符合条件的会话，完成一次问数后会自动保存</Empty>}<Pagination data={resource.data} page={page} onPage={setPage} /></Card>
    {detail && <ConversationViewer detail={detail} onClose={closeDetail} />}{rename && <Modal title="重命名会话" onClose={closeRename}><form onSubmit={e => { e.preventDefault(); action.run(async () => { await request(`${apiPrefix}/${rename.id}?scope=${scope}`, { method: "PATCH", body: JSON.stringify({ title }) }); setRename(null); resource.reload(); }); }}><label className="form-field">会话名称<input required maxLength={120} value={title} onChange={e => setTitle(e.target.value)} /></label>{action.feedback}<div className="modal-actions"><Button onClick={closeRename}>取消</Button><Button type="submit" variant="primary" disabled={action.busy}><Save size={13} />保存名称</Button></div></form></Modal>}
    {deleting && <Modal title="永久删除会话？" onClose={closeDelete}><p className="long-description">删除「{deleting.title}」及其全部问题、答案和快照，无法恢复。仅想隐藏时请使用归档。</p>{action.feedback}<div className="modal-actions"><Button onClick={closeDelete}>取消</Button><Button enterConfirm disabled={action.busy} onClick={() => action.run(async () => { await request(`${apiPrefix}/${deleting.id}?scope=${scope}`, { method: "DELETE" }); setDeleting(null); resource.reload(); }, "会话及快照已永久删除，审计记录保留")}><Trash2 size={13} />确认永久删除</Button></div></Modal>}
  </>;
}

