"use client";

import { useEffect, useState } from "react";
import { BookOpen, Cpu, FileClock, LayoutDashboard, MessageSquare, Settings2, ShieldCheck } from "lucide-react";
import { roleLabel, type Auth, type User } from "@/lib/admin";
import { Badge, Shell } from "./ui";
import { Loading, useResource } from "./admin/shared";
import { AuthForm, AccountMenu } from "./admin/account";
import { OverviewPanel } from "./admin/overview";
import { ModelsPanel } from "./admin/models";
import { ConversationsPanel } from "./admin/conversations";
import { GlossaryPanel } from "./admin/glossary";
import { SettingsPanel } from "./admin/settings";
import { AuditPanel } from "./admin/audit";

const TABS = [{ id: "overview", title: "后台概览", icon: LayoutDashboard }, { id: "models", title: "模型配置", icon: Cpu, admin: true }, { id: "conversations", title: "会话管理", icon: MessageSquare }, { id: "glossary", title: "本体论词条", icon: BookOpen }, { id: "settings", title: "系统设置", icon: Settings2, admin: true }, { id: "audit", title: "操作审计", icon: FileClock, admin: true }];

export function AdminConsole() {
  const authResource = useResource<Auth>("/api/auth/status");
  const [user, setUser] = useState<User | null>(null), [tab, setTab] = useState("overview");
  useEffect(() => { setUser(authResource.data?.user || null); }, [authResource.data]);
  const tabs = TABS.filter(item => !item.admin || user?.can_manage);
  return <Shell active="admin" statusLabel={user ? `${roleLabel(user)} · 已登录` : "安全管理工作空间"} sidebar={user && <><div className="sidebar-section"><div className="nav-caption">管理控制台</div>{tabs.map(item => <button key={item.id} className={`nav-item admin-nav ${tab === item.id ? "active" : ""}`} onClick={() => setTab(item.id)}><item.icon size={15} />{item.title}{tab === item.id && <span className="nav-dot" />}</button>)}</div><AccountMenu user={user} onLogout={() => { setUser(null); setTab("overview"); authResource.reload(); }} /></>}>
    {!user ? <><Loading {...authResource} />{authResource.data && <AuthForm auth={authResource.data} onLogin={account => { setUser(account); setTab("overview"); }} />}</> : <div className="admin-workspace"><div className="heading-row"><div><div className="eyebrow"><span className="eyebrow-line" />CONTROL, WITH CONTEXT.</div><h1>配置清晰，知识有序。</h1><p className="heading-description">管理模型、业务规则与查询记录；账号和权限沿用原填报系统，让每一次问数都可管理、可追溯。</p></div><Badge tone="green"><ShieldCheck size={12} />{roleLabel(user)}</Badge></div><div className="admin-mobile-account"><AccountMenu user={user} onLogout={() => { setUser(null); authResource.reload(); }} /></div><div className="admin-tabs" role="tablist" aria-label="管理模块">{tabs.map(item => <button role="tab" aria-selected={tab === item.id} className={tab === item.id ? "selected" : ""} key={item.id} onClick={() => setTab(item.id)}><item.icon size={14} />{item.title}</button>)}</div><div role="tabpanel" aria-label={tabs.find(item => item.id === tab)?.title} key={tab}>
      {tab === "overview" && <OverviewPanel onTab={setTab} />}{tab === "models" && user.can_manage && <ModelsPanel />}{tab === "conversations" && <ConversationsPanel current={user} />}{tab === "glossary" && <GlossaryPanel current={user} />}{tab === "settings" && user.can_manage && <SettingsPanel />}{tab === "audit" && user.can_manage && <AuditPanel />}
    </div></div>}
  </Shell>;
}
