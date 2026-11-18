"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertCircle, BarChart3, BookCheck, BookOpen, Boxes, Compass, Cpu, Database,
  FileClock, LayoutDashboard, MessageSquare, Settings2, Share2, ShieldCheck, SlidersHorizontal, Users,
} from "lucide-react";
import { roleLabel, type AskFeedbackItem, type Auth, type Paged, type User } from "@/lib/admin";
import { request } from "@/lib/api";
import { Badge, Shell } from "./ui";
import { Loading, useResource } from "./admin/shared";
import { AuthForm, AccountMenu } from "./admin/account";
import { OverviewPanel } from "./admin/overview";
import { ModelsPanel } from "./admin/models";
import { ConversationsPanel } from "./admin/conversations";
import { GlossaryPanel } from "./admin/glossary";
import { DataSyncPanel } from "./admin/data-sync";
import { AuditPanel } from "./admin/audit";
import { OntologyAdminPanel } from "./admin/ontology-admin";
import { DataModelPanel } from "./admin/data-model";
import { MetricsAdminPanel } from "./admin/metrics-admin";
import { RulesAdminPanel } from "./admin/rules-admin";
import { CapabilitiesAdminPanel } from "./admin/capabilities-admin";
import { PlannerAdminPanel } from "./admin/planner-admin";
import { AskSettingsPanel } from "./admin/ask-settings";
import { FeedbackAdminPanel } from "./admin/feedback-admin";
import { UserAdminPanel } from "./admin/user-admin";

type TabDef = {
  id: string;
  title: string;
  icon: typeof LayoutDashboard;
  group: "engine" | "ops";
  admin?: boolean;
};

const TABS: TabDef[] = [
  { id: "ontology", title: "本体管理", icon: Share2, group: "engine", admin: true },
  { id: "model", title: "数据模型", icon: Boxes, group: "engine", admin: true },
  { id: "metrics", title: "指标管理", icon: BarChart3, group: "engine", admin: true },
  { id: "rules", title: "规则管理", icon: BookCheck, group: "engine", admin: true },
  { id: "capabilities", title: "能力注册", icon: Cpu, group: "engine", admin: true },
  { id: "planner", title: "查询规划", icon: Compass, group: "engine", admin: true },
  { id: "ask-settings", title: "问数设置", icon: SlidersHorizontal, group: "engine", admin: true },
  { id: "feedback", title: "不准反馈", icon: AlertCircle, group: "engine", admin: true },
  { id: "glossary", title: "本体论词条", icon: BookOpen, group: "engine" },
  { id: "overview", title: "后台概览", icon: LayoutDashboard, group: "ops" },
  { id: "sync", title: "数据同步", icon: Database, group: "ops", admin: true },
  { id: "models", title: "模型配置", icon: Settings2, group: "ops", admin: true },
  { id: "conversations", title: "会话管理", icon: MessageSquare, group: "ops" },
  { id: "users", title: "用户与权限", icon: Users, group: "ops", admin: true },
  { id: "audit", title: "操作审计", icon: FileClock, group: "ops", admin: true },
];

export function AdminConsole() {
  const authResource = useResource<Auth>("/api/auth/status");
  const [user, setUser] = useState<User | null>(null);
  const [tab, setTab] = useState("overview");
  const [openFeedbackCount, setOpenFeedbackCount] = useState(0);
  const initRef = useRef(false);

  useEffect(() => { setUser(authResource.data?.user || null); }, [authResource.data]);

  const refreshFeedbackCount = useCallback(() => {
    if (!user?.can_manage) return;
    request<Paged<AskFeedbackItem> & { counts?: Record<string, number> }>("/api/admin/feedback?status=OPEN&page=1")
      .then(res => setOpenFeedbackCount(res.counts?.OPEN ?? res.total ?? 0))
      .catch(() => {});
  }, [user?.can_manage]);

  useEffect(() => { refreshFeedbackCount(); }, [refreshFeedbackCount]);

  useEffect(() => {
    if (!user) return;
    const rawRequested = new URLSearchParams(window.location.search).get("tab");
    const mapped = rawRequested === "settings"
      ? "ask-settings"
      : (rawRequested === "glossary" && user.can_manage ? "ontology" : rawRequested);
    const valid = TABS.filter(item => (!item.admin || user.can_manage) && !(user.can_manage && item.id === "glossary"));
    if (mapped && valid.some(item => item.id === mapped)) {
      setTab(mapped);
      initRef.current = true;
    } else if (!initRef.current) {
      initRef.current = true;
      setTab(user.can_manage ? "ontology" : "overview");
    }
  }, [user]);

  const visibleTabs = TABS.filter(item => {
    if (item.admin && !user?.can_manage) return false;
    if (user?.can_manage && item.id === "glossary") return false; // 管理员由「本体管理」内页签统一承载词条映射
    return true;
  });
  const engineTabs = visibleTabs.filter(t => t.group === "engine");
  const opsTabs = visibleTabs.filter(t => t.group === "ops");

  const renderNavBtn = (item: TabDef) => (
    <button
      key={item.id}
      className={`nav-item admin-nav ${tab === item.id ? "active" : ""}`}
      onClick={() => setTab(item.id)}
    >
      <item.icon size={15} />
      <span>{item.title}</span>
      {item.id === "feedback" && openFeedbackCount > 0 && (
        <span className="nav-count-pill">{openFeedbackCount > 99 ? "99+" : openFeedbackCount}</span>
      )}
      {tab === item.id && <span className="nav-dot" />}
    </button>
  );

  return (
    <Shell
      active="admin"
      statusLabel={user ? `${roleLabel(user)} · 已登录` : "知航管理后台"}
      sidebar={user && (
        <>
          <div className="sidebar-section">
            <a className="nav-item admin-nav" href="/">
              <MessageSquare size={15} />
              <span>知航问询</span>
            </a>
            {engineTabs.length > 0 && (
              <>
                <div className="nav-caption nav-caption-sub">本体与问数引擎</div>
                {engineTabs.map(renderNavBtn)}
              </>
            )}
            <div className="nav-caption nav-caption-sub">平台运维与同步</div>
            {opsTabs.map(renderNavBtn)}
          </div>
          <AccountMenu user={user} onLogout={() => { initRef.current = false; setUser(null); setTab("overview"); authResource.reload(); }} />
        </>
      )}
    >
      {!user ? (
        <>
          <Loading {...authResource} />
          {authResource.data && <AuthForm auth={authResource.data} onLogin={account => { initRef.current = false; setUser(account); }} />}
        </>
      ) : (
        <div className="admin-workspace">
          <div className="heading-row">
            <div>
              <div className="eyebrow"><span className="eyebrow-line" />知航 · 本体与问数管理</div>
              <h1>配置清晰，本体有力。</h1>
              <p className="heading-description">统筹业务本体、数据模型、指标口径、规则能力、查询规划与 Oracle 数据同步；配置发布后即时赋能智能问数。</p>
            </div>
            <Badge tone="green"><ShieldCheck size={12} />{roleLabel(user)}</Badge>
          </div>

          <div className="admin-mobile-account">
            <AccountMenu user={user} onLogout={() => { initRef.current = false; setUser(null); authResource.reload(); }} />
          </div>

          <div className="admin-tabs" role="tablist" aria-label="管理模块">
            {visibleTabs.map(item => (
              <button
                role="tab"
                aria-selected={tab === item.id}
                className={tab === item.id ? "selected" : ""}
                key={item.id}
                onClick={() => setTab(item.id)}
              >
                <item.icon size={14} />{item.title}
              </button>
            ))}
          </div>

          <div role="tabpanel" aria-label={visibleTabs.find(item => item.id === tab)?.title} key={tab}>
            {tab === "ontology" && user.can_manage && <OntologyAdminPanel current={user} />}
            {tab === "model" && user.can_manage && <DataModelPanel />}
            {tab === "metrics" && user.can_manage && <MetricsAdminPanel />}
            {tab === "rules" && user.can_manage && <RulesAdminPanel />}
            {tab === "capabilities" && user.can_manage && <CapabilitiesAdminPanel />}
            {tab === "planner" && user.can_manage && <PlannerAdminPanel />}
            {tab === "ask-settings" && user.can_manage && <AskSettingsPanel />}
            {tab === "feedback" && user.can_manage && <FeedbackAdminPanel onUpdated={refreshFeedbackCount} />}
            {tab === "glossary" && <GlossaryPanel current={user} />}
            {tab === "overview" && <OverviewPanel onTab={setTab} canManage={user.can_manage} />}
            {tab === "sync" && user.can_manage && <DataSyncPanel />}
            {tab === "models" && user.can_manage && <ModelsPanel />}
            {tab === "conversations" && <ConversationsPanel current={user} />}
            {tab === "users" && user.can_manage && <UserAdminPanel current={user} />}
            {tab === "audit" && user.can_manage && <AuditPanel />}
          </div>
        </div>
      )}
    </Shell>
  );
}
