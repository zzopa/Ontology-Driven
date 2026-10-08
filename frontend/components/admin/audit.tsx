"use client";

import { useState } from "react";
import { RefreshCw, Search } from "lucide-react";
import { formatDate, type Paged } from "@/lib/admin";
import { Button, Card } from "../ui";
import { Loading, Pagination, useResource } from "./shared";

const AUDIT_NAMES: Record<string, string> = { "auth.login": "登录", "auth.logout": "退出", "auth.failed": "登录失败", "user.bootstrap": "初始化管理员", "user.create": "创建人员", "user.update": "更新人员/权限", "model.create": "新增模型", "model.update": "更新模型", "model.activate": "切换模型", "model.delete": "删除模型", "model.test": "检测模型", "conversation.inspect": "查看全局会话", "conversation.update": "修改/归档会话", "conversation.delete": "删除会话", "settings.update": "更新系统策略", "retention.purge": "清理过期会话", "ontology.save": "保存业务本体", "ontology.rollback": "回滚业务本体", "metadata.refresh": "同步数据库结构", "ontology.validate_relations": "抽样验证关系", "glossary.post": "新增本体词条", "glossary.put": "更新本体词条", "glossary.delete": "删除本体词条" };

export function AuditPanel() {
  const [page, setPage] = useState(1), [search, setSearch] = useState(""), [query, setQuery] = useState("");
  const resource = useResource<Paged<{ id: number; actor_name: string; action: string; target: string; detail: string; created_at: string }>>(`/api/admin/audit?page=${page}&search=${encodeURIComponent(query)}`);
  return <><div className="admin-section-head"><div><h2>操作审计</h2><p>记录配置与权限变更；不记录密码、模型密钥或回答正文</p></div><Button onClick={resource.reload}><RefreshCw size={13} />刷新</Button></div><Card className="card-pad"><form className="admin-toolbar" onSubmit={e => { e.preventDefault(); setQuery(search); setPage(1); }}><label className="admin-search"><Search size={14} /><input aria-label="搜索审计" value={search} onChange={e => setSearch(e.target.value)} placeholder="操作代码或目标 ID，例如 model.activate" /></label><Button type="submit">搜索</Button></form><Loading {...resource} /><div className="table-wrap"><table className="data-table attribute-table admin-table"><thead><tr><th>操作与时间</th><th>操作人员</th><th>目标 / 说明</th></tr></thead><tbody>{resource.data?.items.map(item => <tr key={item.id}><td><strong>{AUDIT_NAMES[item.action] || item.action}</strong><small>{item.action} · {formatDate(item.created_at)}</small></td><td>{item.actor_name}</td><td><code>{item.target || "—"}</code><div className="helper">{item.detail}</div></td></tr>)}</tbody></table></div><Pagination data={resource.data} page={page} onPage={setPage} /></Card></>;
}

