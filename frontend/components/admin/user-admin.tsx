"use client";

import { useCallback, useState } from "react";
import {
  Check,
  Edit3,
  KeyRound,
  Layers,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Shield,
  Sparkles,
  Trash2,
  Users,
  X,
} from "lucide-react";
import {
  formatDate,
  type Paged,
  type ScopeOptionsData,
  type SysDataScope,
  type SysProfileItem,
  type SysRole,
  type SysUserItem,
  type User,
} from "@/lib/admin";
import { request } from "@/lib/api";
import { Badge, Button, Card, Modal, Notice } from "../ui";
import { Empty, Loading, Pagination, useAction, useResource } from "./shared";

const SCOPE_TYPE_OPTIONS = [
  { value: "ALL", label: "全部数据 (ALL - 无限制)" },
  { value: "AIRLINE", label: "航司代码 (AIRLINE - 如 MU, CZ)" },
  { value: "TERMINAL", label: "航站楼 (TERMINAL - 如 T1, T2)" },
  { value: "AIRPORT", label: "机场代码 (AIRPORT - 如 WUH, PEK)" },
  { value: "DIRECTION", label: "进出港方向 (DIRECTION - ARR/DEP)" },
];

export function UserAdminPanel({ current }: { current: User }) {
  const [subTab, setSubTab] = useState<"users" | "profiles">("users");

  // User list filters
  const [search, setSearch] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [page, setPage] = useState(1);

  // Resources
  const rolesRes = useResource<{ ok: boolean; items: SysRole[] }>("/api/admin/roles");
  const profilesRes = useResource<{ ok: boolean; items: SysProfileItem[] }>("/api/admin/profiles");
  const scopeOptionsRes = useResource<ScopeOptionsData>("/api/admin/data-scope/options");
  const usersPath = `/api/admin/users?search=${encodeURIComponent(searchQuery)}&role=${encodeURIComponent(roleFilter)}&page=${page}&page_size=20`;
  const usersRes = useResource<Paged<SysUserItem>>(usersPath);
  const action = useAction();

  // User Modals
  const [createModal, setCreateModal] = useState(false);
  const [editUser, setEditUser] = useState<SysUserItem | null>(null);
  const [pwdUser, setPwdUser] = useState<SysUserItem | null>(null);
  const [deleteUser, setDeleteUser] = useState<SysUserItem | null>(null);

  // Profile Modal
  const [profileModal, setProfileModal] = useState<SysProfileItem | null>(null);
  const [deleteProfile, setDeleteProfile] = useState<SysProfileItem | null>(null);

  // User form states
  const [formUsername, setFormUsername] = useState("");
  const [formPassword, setFormPassword] = useState("");
  const [formDisplayName, setFormDisplayName] = useState("");
  const [formProfile, setFormProfile] = useState("OPS");
  const [formEnabled, setFormEnabled] = useState(true);
  const [formRoleIds, setFormRoleIds] = useState<number[]>([]);
  const [formDataScopes, setFormDataScopes] = useState<{ scope_type: string; scope_value: string; is_custom?: boolean }[]>([]);

  // Password reset state
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const passwordMismatch = Boolean(confirmPassword) && newPassword !== confirmPassword;
  const canResetPassword = Boolean(newPassword) && newPassword === confirmPassword && !action.busy;
  const closePasswordReset = useCallback(() => {
    setPwdUser(null);
    setNewPassword("");
    setConfirmPassword("");
  }, []);

  const roles = rolesRes.data?.items || [];
  const profiles = profilesRes.data?.items || [];
  const scopeOptions = scopeOptionsRes.data || { airlines: [], terminals: [], airports: [], directions: [] };

  const openCreateModal = () => {
    setFormUsername("");
    setFormPassword("");
    setFormDisplayName("");
    setFormProfile(profiles[0]?.code || "OPS");
    setFormEnabled(true);
    setFormRoleIds(roles.filter(r => r.code === "VIEWER").map(r => r.id));
    setFormDataScopes([{ scope_type: "ALL", scope_value: "", is_custom: false }]);
    setCreateModal(true);
  };

  const openEditModal = (u: SysUserItem) => {
    setFormUsername(u.username);
    setFormDisplayName(u.display_name);
    setFormProfile(u.overview_profile || "OPS");
    setFormEnabled(u.enabled);
    setFormRoleIds(u.roles.map(r => r.id));
    setFormDataScopes(
      u.data_scopes.length > 0
        ? u.data_scopes.map(s => ({
            scope_type: s.scope_type,
            scope_value: s.scope_value || "",
            is_custom: isValueCustom(s.scope_type, s.scope_value || ""),
          }))
        : [{ scope_type: "ALL", scope_value: "", is_custom: false }]
    );
    setEditUser(u);
  };

  const isValueCustom = (type: string, val: string): boolean => {
    if (!val || type === "ALL") return false;
    if (type === "AIRLINE") return !scopeOptions.airlines.some(a => a.code === val);
    if (type === "TERMINAL") return !scopeOptions.terminals.some(t => t.code === val);
    if (type === "AIRPORT") return !scopeOptions.airports.some(ap => ap.code === val);
    if (type === "DIRECTION") return !scopeOptions.directions.some(d => d.code === val);
    return true;
  };

  const addScopeRow = () => {
    const defaultAirline = scopeOptions.airlines[0]?.code || "MU";
    setFormDataScopes(prev => [...prev, { scope_type: "AIRLINE", scope_value: defaultAirline, is_custom: false }]);
  };

  const removeScopeRow = (idx: number) => {
    setFormDataScopes(prev => prev.filter((_, i) => i !== idx));
  };

  const updateScopeType = (idx: number, newType: string) => {
    let defaultVal = "";
    if (newType === "AIRLINE") defaultVal = scopeOptions.airlines[0]?.code || "MU";
    else if (newType === "TERMINAL") defaultVal = scopeOptions.terminals[0]?.code || "T1";
    else if (newType === "AIRPORT") defaultVal = scopeOptions.airports[0]?.code || "WUH";
    else if (newType === "DIRECTION") defaultVal = "ARR";
    setFormDataScopes(prev =>
      prev.map((item, i) => (i === idx ? { ...item, scope_type: newType, scope_value: defaultVal, is_custom: false } : item))
    );
  };

  const updateScopeValue = (idx: number, val: string) => {
    setFormDataScopes(prev =>
      prev.map((item, i) => (i === idx ? { ...item, scope_value: val } : item))
    );
  };

  const toggleCustomInput = (idx: number, custom: boolean) => {
    setFormDataScopes(prev =>
      prev.map((item, i) => (i === idx ? { ...item, is_custom: custom } : item))
    );
  };

  const toggleRoleId = (rid: number) => {
    setFormRoleIds(prev =>
      prev.includes(rid) ? prev.filter(id => id !== rid) : [...prev, rid]
    );
  };

  // Render individual scope value input (select with options or custom input)
  const renderScopeValuePicker = (scope: { scope_type: string; scope_value: string; is_custom?: boolean }, idx: number) => {
    if (scope.scope_type === "ALL") {
      return (
        <div style={{ flex: 1, color: "var(--color-muted)", fontSize: "13px" }}>
          全部数据范围（无行级限制）
        </div>
      );
    }

    if (scope.is_custom) {
      return (
        <div style={{ flex: 1, display: "flex", gap: "6px", alignItems: "center" }}>
          <input
            style={{ flex: 1 }}
            placeholder={`输入自定义 ${scope.scope_type} 代码`}
            value={scope.scope_value}
            onChange={e => updateScopeValue(idx, e.target.value)}
          />
          <Button
            variant="ghost"
            style={{ fontSize: "12px", padding: "2px 6px" }}
            onClick={() => toggleCustomInput(idx, false)}
          >
            从字典选择
          </Button>
        </div>
      );
    }

    let optionsList: { code: string; name: string }[] = [];
    if (scope.scope_type === "AIRLINE") optionsList = scopeOptions.airlines;
    else if (scope.scope_type === "TERMINAL") optionsList = scopeOptions.terminals;
    else if (scope.scope_type === "AIRPORT") optionsList = scopeOptions.airports;
    else if (scope.scope_type === "DIRECTION") optionsList = scopeOptions.directions;

    return (
      <div style={{ flex: 1, display: "flex", gap: "6px", alignItems: "center" }}>
        <select
          style={{ flex: 1 }}
          value={scope.scope_value}
          onChange={e => {
            if (e.target.value === "__CUSTOM__") {
              toggleCustomInput(idx, true);
              updateScopeValue(idx, "");
            } else {
              updateScopeValue(idx, e.target.value);
            }
          }}
        >
          {optionsList.map(opt => (
            <option key={opt.code} value={opt.code}>
              {opt.name}
            </option>
          ))}
          <option value="__CUSTOM__">➕ 自定义输入代码...</option>
        </select>
      </div>
    );
  };

  return (
    <>
      <div className="admin-section-head">
        <div>
          <h2>用户与权限管理</h2>
          <p>
            统一管理知航业务用户（sys_user）、功能角色分配（sys_role）、行级数据权限规则（sys_data_scope）与业务概览画像定义（sys_profile）
          </p>
        </div>
        <div className="manager-tools">
          <Button onClick={() => { usersRes.reload(); profilesRes.reload(); scopeOptionsRes.reload(); }}>
            <RefreshCw size={13} />
            刷新
          </Button>
          {subTab === "users" ? (
            <Button variant="primary" onClick={openCreateModal}>
              <Plus size={14} />
              新建用户
            </Button>
          ) : (
            <Button
              variant="primary"
              onClick={() =>
                setProfileModal({
                  code: "",
                  name: "",
                  description: "",
                  target_audience: "",
                  focus_metrics: "",
                  default_scenarios: "",
                  system_prompt_hint: "",
                  is_builtin: false,
                  enabled: true,
                })
              }
            >
              <Plus size={14} />
              新建业务画像
            </Button>
          )}
        </div>
      </div>

      {/* Sub Tabs: Users vs Profiles */}
      <div className="admin-tabs" style={{ marginBottom: "16px" }}>
        <button
          className={subTab === "users" ? "selected" : ""}
          onClick={() => setSubTab("users")}
        >
          <Users size={14} /> 用户账号列表
        </button>
        <button
          className={subTab === "profiles" ? "selected" : ""}
          onClick={() => setSubTab("profiles")}
        >
          <Sparkles size={14} /> 业务画像配置（3种内置与自定义）
        </button>
      </div>

      {action.feedback}

      {/* ===================== TAB 1: USERS LIST ===================== */}
      {subTab === "users" && (
        <Card className="card-pad">
          <form
            className="admin-toolbar"
            onSubmit={e => {
              e.preventDefault();
              setSearchQuery(search);
              setPage(1);
            }}
          >
            <label className="admin-search">
              <Search size={14} />
              <input
                aria-label="搜索用户账号或姓名"
                placeholder="搜索账号 / 姓名..."
                value={search}
                onChange={e => setSearch(e.target.value)}
              />
            </label>
            <select
              aria-label="角色筛选"
              value={roleFilter}
              onChange={e => {
                setRoleFilter(e.target.value);
                setPage(1);
              }}
            >
              <option value="">全部角色</option>
              {roles.map(r => (
                <option key={r.code} value={r.code}>
                  {r.name} ({r.code})
                </option>
              ))}
            </select>
            <Button type="submit">搜索</Button>
          </form>

          <Loading {...usersRes} />

          <div className="table-wrap">
            <table className="data-table attribute-table admin-table">
              <thead>
                <tr>
                  <th>账号</th>
                  <th>姓名</th>
                  <th>角色分配</th>
                  <th>概览画像</th>
                  <th>数据权限范围</th>
                  <th>状态</th>
                  <th>创建时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {(usersRes.data?.items || []).map(item => {
                  const isCurrent = String(item.id) === String(current.id);
                  const isAdminAccount = item.username === "admin";
                  const profileObj = profiles.find(p => p.code === item.overview_profile);
                  return (
                    <tr key={item.id}>
                      <td>
                        <strong>{item.username}</strong>
                        <small>ID: #{item.id}</small>
                      </td>
                      <td>{item.display_name}</td>
                      <td>
                        <div style={{ display: "flex", gap: "4px", flexWrap: "wrap" }}>
                          {item.roles.map(r => (
                            <Badge
                              key={r.id}
                              tone={
                                r.code === "ADMIN"
                                  ? "green"
                                  : r.code === "ANALYST"
                                  ? "amber"
                                  : "neutral"
                              }
                            >
                              <Shield size={11} style={{ marginRight: 2 }} />
                              {r.name}
                            </Badge>
                          ))}
                          {!item.roles.length && <small style={{ color: "var(--color-muted)" }}>未分配角色</small>}
                        </div>
                      </td>
                      <td>
                        <span title={profileObj?.description || item.overview_profile}>
                          <Badge tone="neutral">
                            <Sparkles size={11} style={{ marginRight: 2 }} />
                            {profileObj?.name || item.overview_profile}
                          </Badge>
                        </span>
                      </td>
                      <td>
                        <div style={{ display: "flex", gap: "4px", flexWrap: "wrap", maxWidth: "280px" }}>
                          {item.data_scopes.map((s, idx) => {
                            if (s.scope_type === "ALL") {
                              return <Badge key={idx} tone="green">全部数据 (ALL)</Badge>;
                            }
                            const typeLabel =
                              s.scope_type === "AIRLINE"
                                ? "航司"
                                : s.scope_type === "TERMINAL"
                                ? "航站楼"
                                : s.scope_type === "AIRPORT"
                                ? "机场"
                                : s.scope_type === "DIRECTION"
                                ? "方向"
                                : s.scope_type;
                            return (
                              <Badge key={idx}>
                                {typeLabel}: {s.scope_value || "*"}
                              </Badge>
                            );
                          })}
                          {!item.data_scopes.length && <Badge tone="neutral">无规则（默认限制）</Badge>}
                        </div>
                      </td>
                      <td>
                        <Badge tone={item.enabled ? "green" : "neutral"}>
                          {item.enabled ? "启用" : "停用"}
                        </Badge>
                      </td>
                      <td>
                        <small>{formatDate(item.created_at)}</small>
                      </td>
                      <td>
                        <div className="manager-tools">
                          <Button
                            variant="ghost"
                            aria-label={`编辑用户 ${item.username}`}
                            onClick={() => openEditModal(item)}
                          >
                            <Pencil size={13} />
                          </Button>
                          <Button
                            variant="ghost"
                            aria-label={`重置密码 ${item.username}`}
                            disabled={action.busy}
                            onClick={() => setPwdUser(item)}
                          >
                            <KeyRound size={13} />
                          </Button>
                          <Button
                            variant="ghost"
                            disabled={isCurrent || isAdminAccount}
                            title={
                              isCurrent
                                ? "不能删除当前登录账号"
                                : isAdminAccount
                                ? "不能删除系统内置 admin"
                                : "删除用户"
                            }
                            aria-label={`删除用户 ${item.username}`}
                            onClick={() => setDeleteUser(item)}
                          >
                            <Trash2 size={13} />
                          </Button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {!usersRes.data?.items.length && !usersRes.loading && (
            <Empty>暂无匹配的用户账号记录</Empty>
          )}
          <Pagination data={usersRes.data} page={page} onPage={setPage} />
        </Card>
      )}

      {/* ===================== TAB 2: PROFILES CONFIG ===================== */}
      {subTab === "profiles" && (
        <Card className="card-pad">
          <Loading {...profilesRes} />
          <div style={{ marginBottom: "16px" }}>
            <p style={{ margin: "0 0 12px", color: "var(--color-muted)", fontSize: "13px" }}>
              业务画像决定用户提问时大模型的<strong>关注重点与指标侧重点</strong>。系统预设 3 种内置画像（运行保障、市场航线、领导看板），支持配置自定义提示词引导与重点关注指标。
            </p>
          </div>

          <div className="table-wrap">
            <table className="data-table attribute-table admin-table">
              <thead>
                <tr>
                  <th>画像编码</th>
                  <th>画像名称</th>
                  <th>适用业务受众</th>
                  <th>重点关注指标</th>
                  <th>推荐提问场景</th>
                  <th>大模型 Prompt 指引</th>
                  <th>状态</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {profiles.map(p => (
                  <tr key={p.code}>
                    <td>
                      <strong>{p.code}</strong>
                      <small>
                        <Badge tone={p.is_builtin ? "green" : "neutral"}>
                          {p.is_builtin ? "系统内置" : "自定义"}
                        </Badge>
                      </small>
                    </td>
                    <td>
                      <strong>{p.name}</strong>
                      <div style={{ color: "var(--color-muted)", fontSize: "12px" }}>{p.description}</div>
                    </td>
                    <td>
                      <small>{p.target_audience || "—"}</small>
                    </td>
                    <td>
                      <div style={{ display: "flex", gap: "4px", flexWrap: "wrap", maxWidth: "260px" }}>
                        {(p.focus_metrics || "").split(",").filter(Boolean).map((m, idx) => (
                          <Badge key={idx}>{m.trim()}</Badge>
                        ))}
                      </div>
                    </td>
                    <td>
                      <small style={{ color: "var(--color-muted)" }}>{p.default_scenarios || "—"}</small>
                    </td>
                    <td>
                      <div
                        style={{
                          maxWidth: "240px",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                          fontSize: "12px",
                          color: "var(--color-muted)",
                        }}
                        title={p.system_prompt_hint}
                      >
                        {p.system_prompt_hint || "—"}
                      </div>
                    </td>
                    <td>
                      <Badge tone={p.enabled ? "green" : "neutral"}>{p.enabled ? "启用" : "停用"}</Badge>
                    </td>
                    <td>
                      <div className="manager-tools">
                        <Button
                          variant="ghost"
                          aria-label={`编辑画像 ${p.name}`}
                          onClick={() => setProfileModal(p)}
                        >
                          <Pencil size={13} />
                        </Button>
                        <Button
                          variant="ghost"
                          disabled={p.is_builtin}
                          title={p.is_builtin ? "系统内置画像不可删除" : "删除画像"}
                          aria-label={`删除画像 ${p.name}`}
                          onClick={() => setDeleteProfile(p)}
                        >
                          <Trash2 size={13} />
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {/* ===================== MODAL: CREATE USER ===================== */}
      {createModal && (
        <Modal
          title="新建业务用户"
          subtitle="添加系统登录用户，分配业务角色、概览画像与行级数据权限枚举规则"
          wide
          onClose={() => setCreateModal(false)}
        >
          <form
            onSubmit={e => {
              e.preventDefault();
              action.run(async () => {
                await request("/api/admin/users", {
                  method: "POST",
                  body: JSON.stringify({
                    username: formUsername.trim(),
                    password: formPassword,
                    display_name: formDisplayName.trim() || formUsername.trim(),
                    overview_profile: formProfile,
                    enabled: formEnabled,
                    role_ids: formRoleIds,
                    data_scopes: formDataScopes.filter(s => s.scope_type === "ALL" || s.scope_value.trim()),
                  }),
                });
                setCreateModal(false);
                usersRes.reload();
              }, `用户「${formUsername}」创建成功`);
            }}
          >
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", marginBottom: "12px" }}>
              <label className="form-field">
                账号 *
                <input
                  required
                  maxLength={64}
                  placeholder="登录账号（字母、数字、下划线）"
                  value={formUsername}
                  onChange={e => setFormUsername(e.target.value)}
                />
              </label>
              <label className="form-field">
                初始密码 *
                <input
                  type="password"
                  required
                  minLength={6}
                  maxLength={72}
                  placeholder="至少 6 位密码"
                  value={formPassword}
                  onChange={e => setFormPassword(e.target.value)}
                />
              </label>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "12px", marginBottom: "12px" }}>
              <label className="form-field">
                姓名 / 昵称
                <input
                  maxLength={64}
                  placeholder="如：张三（运行指挥）"
                  value={formDisplayName}
                  onChange={e => setFormDisplayName(e.target.value)}
                />
              </label>
              <label className="form-field">
                概览画像
                <select value={formProfile} onChange={e => setFormProfile(e.target.value)}>
                  {profiles.map(p => (
                    <option key={p.code} value={p.code}>
                      {p.name} ({p.code})
                    </option>
                  ))}
                </select>
              </label>
              <label className="form-field">
                状态
                <select
                  value={formEnabled ? "true" : "false"}
                  onChange={e => setFormEnabled(e.target.value === "true")}
                >
                  <option value="true">正常启用</option>
                  <option value="false">停用账号</option>
                </select>
              </label>
            </div>

            {/* 角色分配 */}
            <div style={{ marginBottom: "16px" }}>
              <strong style={{ fontSize: "13px", display: "block", marginBottom: "6px" }}>
                分配角色
              </strong>
              <div style={{ display: "flex", gap: "12px", flexWrap: "wrap" }}>
                {roles.map(r => {
                  const checked = formRoleIds.includes(r.id);
                  return (
                    <label
                      key={r.id}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "6px",
                        padding: "6px 10px",
                        background: checked ? "var(--color-bg-subtle, rgba(0,0,0,0.04))" : "transparent",
                        border: "1px solid var(--color-border, #e5e7eb)",
                        borderRadius: "6px",
                        cursor: "pointer",
                        fontSize: "13px",
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleRoleId(r.id)}
                      />
                      <span>
                        <strong>{r.name}</strong> ({r.code})
                      </span>
                    </label>
                  );
                })}
              </div>
            </div>

            {/* 数据权限规则（枚举选择 + 自定义） */}
            <div style={{ marginBottom: "16px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px" }}>
                <div>
                  <strong style={{ fontSize: "13px" }}>数据行级权限规则</strong>
                  <small style={{ color: "var(--color-muted)", marginLeft: "8px" }}>
                    按航司、航站楼、机场等枚举值精准隔离数据范围
                  </small>
                </div>
                <Button variant="ghost" onClick={addScopeRow} style={{ padding: "3px 8px", fontSize: "12px" }}>
                  <Plus size={12} /> 添加规则
                </Button>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                {formDataScopes.map((scope, idx) => (
                  <div key={idx} style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                    <select
                      style={{ width: "230px" }}
                      value={scope.scope_type}
                      onChange={e => updateScopeType(idx, e.target.value)}
                    >
                      {SCOPE_TYPE_OPTIONS.map(opt => (
                        <option key={opt.value} value={opt.value}>
                          {opt.label}
                        </option>
                      ))}
                    </select>

                    {renderScopeValuePicker(scope, idx)}

                    {formDataScopes.length > 1 && (
                      <Button variant="ghost" onClick={() => removeScopeRow(idx)} aria-label="删除规则">
                        <X size={14} />
                      </Button>
                    )}
                  </div>
                ))}
              </div>
            </div>

            {action.feedback}
            <div className="modal-actions">
              <Button onClick={() => setCreateModal(false)}>取消</Button>
              <Button type="submit" variant="primary" disabled={action.busy}>
                <Check size={14} /> 创建账号
              </Button>
            </div>
          </form>
        </Modal>
      )}

      {/* ===================== MODAL: EDIT USER ===================== */}
      {editUser && (
        <Modal
          title={`编辑用户「${editUser.username}」`}
          subtitle="修改用户基本资料、角色分配、概览画像及数据权限规则"
          wide
          onClose={() => setEditUser(null)}
        >
          <form
            onSubmit={e => {
              e.preventDefault();
              action.run(async () => {
                await request(`/api/admin/users/${editUser.id}`, {
                  method: "PUT",
                  body: JSON.stringify({
                    display_name: formDisplayName.trim() || editUser.username,
                    overview_profile: formProfile,
                    enabled: formEnabled,
                    role_ids: formRoleIds,
                    data_scopes: formDataScopes.filter(s => s.scope_type === "ALL" || s.scope_value.trim()),
                  }),
                });
                setEditUser(null);
                usersRes.reload();
              }, `用户「${editUser.username}」更新成功`);
            }}
          >
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "12px", marginBottom: "12px" }}>
              <label className="form-field">
                姓名 / 昵称
                <input
                  required
                  maxLength={64}
                  value={formDisplayName}
                  onChange={e => setFormDisplayName(e.target.value)}
                />
              </label>
              <label className="form-field">
                概览画像
                <select value={formProfile} onChange={e => setFormProfile(e.target.value)}>
                  {profiles.map(p => (
                    <option key={p.code} value={p.code}>
                      {p.name} ({p.code})
                    </option>
                  ))}
                </select>
              </label>
              <label className="form-field">
                账号状态
                <select
                  value={formEnabled ? "true" : "false"}
                  onChange={e => setFormEnabled(e.target.value === "true")}
                >
                  <option value="true">正常启用</option>
                  <option value="false">停用账号</option>
                </select>
              </label>
            </div>

            {/* 角色分配 */}
            <div style={{ marginBottom: "16px" }}>
              <strong style={{ fontSize: "13px", display: "block", marginBottom: "6px" }}>
                分配角色
              </strong>
              <div style={{ display: "flex", gap: "12px", flexWrap: "wrap" }}>
                {roles.map(r => {
                  const checked = formRoleIds.includes(r.id);
                  return (
                    <label
                      key={r.id}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "6px",
                        padding: "6px 10px",
                        background: checked ? "var(--color-bg-subtle, rgba(0,0,0,0.04))" : "transparent",
                        border: "1px solid var(--color-border, #e5e7eb)",
                        borderRadius: "6px",
                        cursor: "pointer",
                        fontSize: "13px",
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleRoleId(r.id)}
                      />
                      <span>
                        <strong>{r.name}</strong> ({r.code})
                      </span>
                    </label>
                  );
                })}
              </div>
            </div>

            {/* 数据权限规则（枚举选择 + 自定义） */}
            <div style={{ marginBottom: "16px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px" }}>
                <div>
                  <strong style={{ fontSize: "13px" }}>数据行级权限规则</strong>
                  <small style={{ color: "var(--color-muted)", marginLeft: "8px" }}>
                    按航司、航站楼、机场等枚举值精准隔离数据范围
                  </small>
                </div>
                <Button variant="ghost" onClick={addScopeRow} style={{ padding: "3px 8px", fontSize: "12px" }}>
                  <Plus size={12} /> 添加规则
                </Button>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                {formDataScopes.map((scope, idx) => (
                  <div key={idx} style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                    <select
                      style={{ width: "230px" }}
                      value={scope.scope_type}
                      onChange={e => updateScopeType(idx, e.target.value)}
                    >
                      {SCOPE_TYPE_OPTIONS.map(opt => (
                        <option key={opt.value} value={opt.value}>
                          {opt.label}
                        </option>
                      ))}
                    </select>

                    {renderScopeValuePicker(scope, idx)}

                    {formDataScopes.length > 1 && (
                      <Button variant="ghost" onClick={() => removeScopeRow(idx)} aria-label="删除规则">
                        <X size={14} />
                      </Button>
                    )}
                  </div>
                ))}
              </div>
            </div>

            {action.feedback}
            <div className="modal-actions">
              <Button onClick={() => setEditUser(null)}>取消</Button>
              <Button type="submit" variant="primary" disabled={action.busy}>
                <Check size={14} /> 保存更改
              </Button>
            </div>
          </form>
        </Modal>
      )}

      {/* ===================== MODAL: RESET PASSWORD ===================== */}
      {pwdUser && (
        <Modal
          title={`重置密码 · ${pwdUser.username}`}
          subtitle={`为用户「${pwdUser.display_name}」设置新的登录密码`}
          onClose={closePasswordReset}
        >
          <form
            onSubmit={e => {
              e.preventDefault();
              if (!canResetPassword) return;
              action.run(async () => {
                await request(`/api/admin/users/${pwdUser.id}/password`, {
                  method: "PUT",
                  body: JSON.stringify({ password: newPassword }),
                });
                closePasswordReset();
              }, `用户「${pwdUser.username}」密码已重置`);
            }}
          >
            <label className="form-field" style={{ marginBottom: "16px" }}>
              新密码 *
              <input
                type="password"
                autoComplete="new-password"
                required
                minLength={6}
                maxLength={72}
                placeholder="输入至少 6 位的新密码"
                value={newPassword}
                onChange={e => setNewPassword(e.target.value)}
              />
            </label>
            <label className="form-field" style={{ marginBottom: "16px" }}>
              确认新密码 *
              <input
                type="password"
                autoComplete="new-password"
                required
                maxLength={72}
                placeholder="再次输入新密码"
                value={confirmPassword}
                onChange={e => setConfirmPassword(e.target.value)}
                aria-invalid={passwordMismatch || undefined}
                aria-describedby={passwordMismatch ? "reset-password-mismatch" : undefined}
              />
            </label>
            {passwordMismatch && <div id="reset-password-mismatch"><Notice error>两次输入的密码不一致。</Notice></div>}
            {action.feedback}
            <div className="modal-actions">
              <Button onClick={closePasswordReset}>取消</Button>
              <Button type="submit" variant="primary" disabled={!canResetPassword}>
                <KeyRound size={13} /> 确认重置密码
              </Button>
            </div>
          </form>
        </Modal>
      )}

      {/* ===================== MODAL: DELETE USER ===================== */}
      {deleteUser && (
        <Modal
          title="确认删除该用户？"
          subtitle="删除操作将清空该用户的角色映射和数据权限配置，且不可逆"
          onClose={() => setDeleteUser(null)}
        >
          <p className="long-description" style={{ marginBottom: "16px" }}>
            确定要永久删除账号「<strong>{deleteUser.username}</strong>」（{deleteUser.display_name}）吗？
          </p>
          {action.feedback}
          <div className="modal-actions">
            <Button onClick={() => setDeleteUser(null)}>取消</Button>
            <Button
              enterConfirm
              disabled={action.busy}
              onClick={() => {
                action.run(async () => {
                  await request(`/api/admin/users/${deleteUser.id}`, { method: "DELETE" });
                  setDeleteUser(null);
                  usersRes.reload();
                }, `用户「${deleteUser.username}」已删除`);
              }}
            >
              <Trash2 size={13} /> 确认删除
            </Button>
          </div>
        </Modal>
      )}

      {/* ===================== MODAL: PROFILE CREATE/EDIT ===================== */}
      {profileModal && (
        <Modal
          title={profileModal.code && profiles.some(p => p.code === profileModal.code) ? `编辑画像「${profileModal.name}」` : "新建业务画像"}
          subtitle="定义不同业务角色的关注重点、关键指标侧重及大模型问数指引词"
          wide
          onClose={() => setProfileModal(null)}
        >
          <form
            onSubmit={e => {
              e.preventDefault();
              action.run(async () => {
                if (profileModal.code && profiles.some(p => p.code === profileModal.code)) {
                  // Update
                  await request(`/api/admin/profiles/${profileModal.code}`, {
                    method: "PUT",
                    body: JSON.stringify(profileModal),
                  });
                } else {
                  // Create
                  await request("/api/admin/profiles", {
                    method: "POST",
                    body: JSON.stringify(profileModal),
                  });
                }
                setProfileModal(null);
                profilesRes.reload();
              }, `画像「${profileModal.name}」保存成功`);
            }}
          >
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", marginBottom: "12px" }}>
              <label className="form-field">
                画像编码 *
                <input
                  required
                  maxLength={32}
                  disabled={Boolean(profileModal.code && profiles.some(p => p.code === profileModal.code))}
                  placeholder="如：CARGO、GROUND_SERVICE"
                  value={profileModal.code}
                  onChange={e => setProfileModal({ ...profileModal, code: e.target.value.toUpperCase() })}
                />
              </label>
              <label className="form-field">
                画像名称 *
                <input
                  required
                  maxLength={64}
                  placeholder="如：货运物流画像"
                  value={profileModal.name}
                  onChange={e => setProfileModal({ ...profileModal, name: e.target.value })}
                />
              </label>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", marginBottom: "12px" }}>
              <label className="form-field">
                面向岗位受众
                <input
                  maxLength={128}
                  placeholder="如：货运部、机坪配载、货站调度"
                  value={profileModal.target_audience}
                  onChange={e => setProfileModal({ ...profileModal, target_audience: e.target.value })}
                />
              </label>
              <label className="form-field">
                状态
                <select
                  value={profileModal.enabled ? "true" : "false"}
                  onChange={e => setProfileModal({ ...profileModal, enabled: e.target.value === "true" })}
                >
                  <option value="true">启用画像</option>
                  <option value="false">停用画像</option>
                </select>
              </label>
            </div>

            <label className="form-field" style={{ marginBottom: "12px" }}>
              业务描述
              <input
                placeholder="简要说明该画像的业务职责"
                value={profileModal.description}
                onChange={e => setProfileModal({ ...profileModal, description: e.target.value })}
              />
            </label>

            <label className="form-field" style={{ marginBottom: "12px" }}>
              重点关注指标（逗号分隔）
              <input
                placeholder="如：货邮吞吐量, 货运架次, 货邮装卸及时率"
                value={profileModal.focus_metrics}
                onChange={e => setProfileModal({ ...profileModal, focus_metrics: e.target.value })}
              />
            </label>

            <label className="form-field" style={{ marginBottom: "12px" }}>
              推荐提问或常见场景（逗号分隔）
              <input
                placeholder="如：本月货邮运输规模, 货运航线TOP5, 今日货运航班放行情况"
                value={profileModal.default_scenarios}
                onChange={e => setProfileModal({ ...profileModal, default_scenarios: e.target.value })}
              />
            </label>

            <label className="form-field" style={{ marginBottom: "16px" }}>
              大模型 Prompt 指引设定
              <textarea
                rows={3}
                placeholder="注入大模型的画像提示词，引导回答时的侧重点与总结口吻"
                value={profileModal.system_prompt_hint}
                onChange={e => setProfileModal({ ...profileModal, system_prompt_hint: e.target.value })}
              />
            </label>

            {action.feedback}
            <div className="modal-actions">
              <Button onClick={() => setProfileModal(null)}>取消</Button>
              <Button type="submit" variant="primary" disabled={action.busy}>
                <Check size={14} /> 保存画像配置
              </Button>
            </div>
          </form>
        </Modal>
      )}

      {/* ===================== MODAL: DELETE PROFILE ===================== */}
      {deleteProfile && (
        <Modal
          title="确认删除该画像？"
          subtitle="删除后将无法为新用户指定该画像"
          onClose={() => setDeleteProfile(null)}
        >
          <p className="long-description" style={{ marginBottom: "16px" }}>
            确定要删除自定义画像「<strong>{deleteProfile.name}</strong>」（代码：{deleteProfile.code}）吗？
          </p>
          {action.feedback}
          <div className="modal-actions">
            <Button onClick={() => setDeleteProfile(null)}>取消</Button>
            <Button
              enterConfirm
              disabled={action.busy}
              onClick={() => {
                action.run(async () => {
                  await request(`/api/admin/profiles/${deleteProfile.code}`, { method: "DELETE" });
                  setDeleteProfile(null);
                  profilesRes.reload();
                }, `画像「${deleteProfile.name}」已删除`);
              }}
            >
              <Trash2 size={13} /> 确认删除
            </Button>
          </div>
        </Modal>
      )}
    </>
  );
}
