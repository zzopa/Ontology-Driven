"use client";

import { useState } from "react";
import { ArrowRight, ArrowUpRight, LogOut, ShieldCheck } from "lucide-react";
import { request } from "@/lib/api";
import { roleLabel, type Auth, type User } from "@/lib/admin";
import { Button, Card, Spinner } from "../ui";
import { useAction } from "./shared";

export function AuthForm({ onLogin }: { auth: Auth; onLogin: (user: User) => void }) {
  const [username, setUsername] = useState(""), [password, setPassword] = useState("");
  const action = useAction();
  return <Card className="admin-auth"><span className="admin-auth-mark"><ShieldCheck size={25} /></span><div className="eyebrow">WORKSPACE.</div><h1>登录业务账号</h1><p>使用现有填报系统账号。问数和管理后台共用账号、角色与数据权限。</p>
    <form onSubmit={e => { e.preventDefault(); action.run(async () => {
      const value = await request<{ user: User }>("/api/auth/login", { method: "POST", body: JSON.stringify({ username, password }) });
      setPassword(""); onLogin(value.user);
    }); }}><label className="form-field">账号<input autoComplete="username" required maxLength={64} value={username} onChange={e => setUsername(e.target.value)} placeholder="输入填报系统账号" /></label><label className="form-field">密码<input type="password" autoComplete="current-password" required maxLength={128} value={password} onChange={e => setPassword(e.target.value)} placeholder="输入账号密码" /></label>{action.feedback}<Button type="submit" variant="primary" disabled={action.busy}>{action.busy ? <Spinner /> : <ArrowRight size={14} />}登录</Button></form><div className="helper">账号、密码和角色在原填报系统维护。</div><a className="inline-button" href="/">返回智能问数 <ArrowUpRight size={12} /></a></Card>;
}

export function AccountMenu({ user, onLogout }: { user: User; onLogout?: () => void }) {
  const action = useAction();
  const scope = ({ ALL: "全部企业", DEPT_ONLY: "本公司", DEPT_TREE: "本公司及下属法人", SELF_ONLY: "本人记录" } as Record<string, string>)[user.data_scope] || user.data_scope;
  return <><div className="admin-account"><span className="admin-avatar">{user.display_name.slice(0, 1)}</span><div><strong>{user.display_name}</strong><small title={roleLabel(user)}>{scope} · {roleLabel(user)}</small></div><Button variant="ghost" aria-label="退出登录" disabled={action.busy} onClick={() => action.run(async () => { await request("/api/auth/logout", { method: "POST" }); onLogout?.(); if (!onLogout) window.location.reload(); }, "已退出")}><LogOut size={13} /></Button></div>{action.feedback}</>;
}
