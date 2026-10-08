"use client";

import { Activity, ArrowRight, BookOpen, Check, CircleDot, LockKeyhole, MessageSquare, RefreshCw, ShieldCheck, Users } from "lucide-react";
import { formatDate, type Overview } from "@/lib/admin";
import { Button, Card, Notice } from "../ui";
import { Loading, useResource } from "./shared";

export function OverviewPanel({ onTab }: { onTab: (tab: string) => void }) {
  const resource = useResource<Overview>("/api/admin/overview");
  const { data } = resource;
  return <><Loading {...resource} />{data && <><div className="admin-stat-grid">{[["模型配置", data.counts.models, Users], ["已保存会话", data.counts.conversations, MessageSquare], ["查询记录", data.counts.messages, Activity], ["业务对象", data.status.queryable_entities, BookOpen]].map(([label, value, Icon]) => { const Mark = Icon as typeof Users; return <Card className="admin-stat" key={String(label)}><div><span>{String(label)}</span><Mark size={16} /></div><strong>{Number(value).toLocaleString()}</strong><small>{label === "业务对象" ? "来自当前数据库目录" : "本地管理库实时数据"}</small></Card>; })}</div><div className="admin-overview-grid"><Card className="card-pad"><div className="card-head"><h2 className="section-title"><CircleDot size={15} />运行与连接</h2><Button variant="ghost" onClick={resource.reload} aria-label="刷新后台概览"><RefreshCw size={13} /></Button></div><div className="binding-grid">{[["业务数据库", data.status.database], ["当前模型", data.active_model?.model || "未配置"], ["表结构", `${data.status.tables} 张表 / ${data.status.fields} 个字段`], ["当前运行查询", data.counts.running], ["失败 / 中断查询", data.counts.failed_queries], ["结构同步", formatDate(data.status.refreshed_at)]].map(([key, value]) => <div className="binding" key={key}><span>{key}</span><code>{value}</code></div>)}</div><Notice>业务库保持只读。账号和角色读取 hbairport01，配置、会话和审计存入本地管理库；会话结果是当时的查询快照。</Notice></Card><Card className="card-pad"><h2 className="section-title"><ShieldCheck size={15} />管理边界</h2><div className="admin-checklist"><div><Check size={14} /><span>原系统 admin 角色：模型配置、业务规则、审计</span></div><div><Check size={14} /><span>其他业务角色：问数、自有会话、查看词条</span></div><div><Check size={14} /><span>账号、密码、角色及数据权限统一在原系统维护</span></div><div><LockKeyhole size={14} /><span>密码不可读取，模型密钥不回传浏览器</span></div></div><Button onClick={() => onTab("conversations")}><MessageSquare size={13} />查看会话记录<ArrowRight size={12} /></Button></Card></div></>}</>;
}

