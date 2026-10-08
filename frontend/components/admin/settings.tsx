"use client";

import { useCallback, useEffect, useState } from "react";
import { Database, Save, Settings2, Trash2 } from "lucide-react";
import { request } from "@/lib/api";
import { Button, Card, Modal, Notice } from "../ui";
import { Loading, useAction, useResource } from "./shared";

export function SettingsPanel() {
  const resource = useResource<{ settings: { retention_days: number }; runtime: Record<string, unknown> }>("/api/admin/settings"), action = useAction();
  const [days, setDays] = useState(90), [confirmation, setConfirmation] = useState(false);
  useEffect(() => { if (resource.data) { setDays(resource.data.settings.retention_days); } }, [resource.data]);
  const close = useCallback(() => setConfirmation(false), []);
  return <><div className="admin-section-head"><div><h2>系统设置</h2><p>会话保留策略即时生效；数据库连接与监听参数仍使用服务端配置文件</p></div></div><Loading {...resource} />{action.feedback}<div className="admin-overview-grid"><Card className="card-pad"><h3 className="section-title"><Settings2 size={15} />会话保留</h3><form onSubmit={e => { e.preventDefault(); action.run(async () => { await request("/api/admin/settings", { method: "PUT", body: JSON.stringify({ retention_days: days }) }); resource.reload(); }, "系统策略已保存并生效"); }}><div className="admin-settings-fields"><p className="helper">问数和后台均需现有填报系统账号登录，账号和角色在原系统维护。</p><label className="form-field">会话保留天数<input type="number" min={1} max={3650} required value={days} onChange={e => setDays(Number(e.target.value))} /></label><p className="helper">作为手动清理的截止规则；不会在保存设置时自动删除历史会话。</p></div><div className="modal-actions"><Button type="submit" variant="primary" disabled={action.busy}><Save size={13} />保存策略</Button></div></form><div className="card-divider" /><Button disabled={action.busy} onClick={() => setConfirmation(true)}><Trash2 size={13} />清理超过保留期的会话</Button></Card><Card className="card-pad"><h3 className="section-title"><Database size={15} />当前服务配置 · 只读</h3><div className="binding-grid" style={{ marginTop: 18 }}>{Object.entries(resource.data?.runtime || {}).map(([key, value]) => <div className="binding" key={key}><span>{({ host: "监听地址", port: "服务端口", lan_cidr: "允许网段", database: "业务数据库", database_host: "数据库地址", page_size: "明细分页大小", query_timeout_ms: "SQL 超时（毫秒）", session_hours: "登录有效期（小时）", embed_parent_origins: "嵌入来源白名单" } as Record<string, string>)[key] || key}</span><code>{Array.isArray(value) ? value.join(", ") || "仅同源" : String(value)}</code></div>)}</div><Notice>连接密码不展示。修改 config.json / config.local.json 后重启；模型在本后台管理；账号、角色和数据权限在原填报系统维护。管理库与 secret.key 必须同时备份，密钥丢失不能恢复模型密钥及会话结果。</Notice></Card></div>
    {confirmation && <Modal title="清理过期会话？" onClose={close}><p className="long-description">按已保存的 {resource.data?.settings.retention_days} 天规则，永久删除超过保留期且没有正在运行查询的会话及全部快照。无法恢复，不删除审计日志和业务库数据。</p>{action.feedback}<div className="modal-actions"><Button onClick={close}>取消</Button><Button enterConfirm disabled={action.busy} onClick={() => action.run(async () => { const result = await request<{ deleted: number }>("/api/admin/retention/purge", { method: "POST" }); setConfirmation(false); if (result.deleted === 0) throw new Error("没有符合清理条件的会话，未删除数据"); }, "已清理过期会话，业务数据不受影响")}>确认永久清理</Button></div></Modal>}
  </>;
}

