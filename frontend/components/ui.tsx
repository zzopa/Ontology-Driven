"use client";

import { useEffect, useRef, useState, type ReactNode, type ButtonHTMLAttributes } from "react";
import { motion, MotionConfig } from "framer-motion";
import { ArrowUpRight, Boxes, Check, Copy, Database, GitBranch, LayoutDashboard, LoaderCircle, Settings2, ShieldCheck, X } from "lucide-react";
import { ThemeSettings } from "./theme-settings";
import { trustedParentOrigin } from "@/lib/query";
import { confirmOnEnter } from "@/lib/keyboard";

export function Providers({ children }: { children: ReactNode }) {
  useEffect(() => {
    document.addEventListener("keydown", confirmOnEnter);
    return () => document.removeEventListener("keydown", confirmOnEnter);
  }, []);
  return <MotionConfig reducedMotion="user">{children}</MotionConfig>;
}

export function Button({ children, className = "", variant = "secondary", type = "button", enterConfirm = type === "submit", ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost"; enterConfirm?: boolean }) {
  return <button type={type} data-enter-confirm={enterConfirm ? "true" : undefined} aria-keyshortcuts={enterConfirm ? "Enter" : undefined} className={`btn btn-${variant} ${className}`} {...props}>{children}</button>;
}
export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "green" | "amber" }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}
export function Card({ children, className = "", id }: { children: ReactNode; className?: string; id?: string }) {
  return <motion.section id={id} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: .3 }} className={`card backdrop-blur-md ${className}`}>{children}</motion.section>;
}
export function Spinner({ className = "" }: { className?: string }) { return <LoaderCircle aria-hidden="true" className={`spin ${className}`} size={16} />; }

export function Shell({ children, active = "ask", embedded = false, sidebar, statusLabel = "实时业务数据" }: { children: ReactNode; active?: "ask" | "ontology" | "admin"; embedded?: boolean; sidebar?: ReactNode; statusLabel?: string }) {
  const [parentOrigin, setParentOrigin] = useState<string | null>(null);
  useEffect(() => {
    if (!embedded) return;
    const requested = new URLSearchParams(window.location.search).get('parentOrigin');
    if (!requested || requested === window.location.origin) { setParentOrigin(window.location.origin); return; }
    fetch('/api/ui-config').then(r => r.json()).then(config => {
      if (Array.isArray(config.embed_parent_origins)) setParentOrigin(trustedParentOrigin(requested, window.location.origin, config.embed_parent_origins));
    }).catch(() => {});
  }, [embedded]);
  return <div className={`app-shell ${embedded ? "is-embedded" : ""}`}>
    {!embedded && <aside className="sidebar">
      <a className="brand" href="/"><span className="brand-mark"><Boxes size={20} strokeWidth={1.7} /></span><span>智能问数<small>AIRPORT INTELLIGENCE</small></span></a>
      <span className="nav-caption">工作空间</span>
      <nav aria-label="主导航"><a href="/" className={`nav-item ${active === "ask" ? "active" : ""}`} aria-current={active === "ask" ? "page" : undefined}><LayoutDashboard size={17} />智能问数{active === "ask" && <span className="nav-dot" />}</a><a href="/ontology" className={`nav-item ${active === "ontology" ? "active" : ""}`} aria-current={active === "ontology" ? "page" : undefined}><GitBranch size={17} />业务本体<ArrowUpRight size={13} className="nav-arrow" /></a><a href="/admin" className={`nav-item ${active === "admin" ? "active" : ""}`} aria-current={active === "admin" ? "page" : undefined}><Settings2 size={17} />管理后台{active === "admin" && <span className="nav-dot" />}</a></nav>
      {sidebar}
      <div className="sidebar-bottom"><span className="mini-mark"><ShieldCheck size={17} /></span><div>只读查询<small>业务数据不会被修改</small></div></div>
    </aside>}
    <div className="main-shell"><header className="topbar"><div className="breadcrumb"><span className="mobile-brand"><Boxes size={18} /></span><span>工作空间</span><span className="slash">/</span><strong>{active === "ask" ? "智能问数" : active === "admin" ? "管理后台" : "业务本体"}</strong>{embedded && <Badge>嵌入模式</Badge>}</div><div className="topbar-right"><ThemeSettings /><span className="connection-label"><span className="status-dot" />{statusLabel}</span>{!embedded && <a className="mobile-management" href={active === "admin" ? "/" : "/admin"} aria-label={active === "admin" ? "返回问数" : "打开管理后台"}><Settings2 size={16} /></a>}{embedded && parentOrigin && <Button variant="ghost" aria-label="关闭嵌入窗口" onClick={() => { if (window.parent === window) window.location.assign('/'); else window.parent.postMessage({ type: "hbask:close" }, parentOrigin); }}><X size={17} /></Button>}</div></header>
      <main className="workspace-main">{children}</main>
      <footer className="footer"><span><Database size={12} /> 数据有来源，结论可追溯</span><span>Airport Intelligence <span className="footer-separator">/</span> 本体驱动问数</span></footer>
    </div>
  </div>;
}

export function Modal({ title, subtitle, onClose, children, wide = false }: { title: string; subtitle?: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const oldOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    ref.current?.focus();
    const handleKey = (e: KeyboardEvent) => {
      const dialogs = document.querySelectorAll('[role="dialog"]');
      if (dialogs[dialogs.length - 1] !== ref.current) return;
      if (e.key === "Escape") { e.stopPropagation(); onClose(); }
      if (e.key === "Tab") {
        const items = ref.current?.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],input,select,textarea,[tabindex="0"]');
        if (!items?.length) return;
        const first = items[0], last = items[items.length - 1];
        if (e.shiftKey && (document.activeElement === first || document.activeElement === ref.current)) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener("keydown", handleKey);
    return () => { document.body.style.overflow = oldOverflow; document.removeEventListener("keydown", handleKey); previous?.focus(); };
  }, [onClose]);
  return <motion.div className="modal-backdrop" initial={{ opacity: 0 }} animate={{ opacity: 1 }} onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
    <motion.div ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-label={title} className={`modal ${wide ? "modal-wide" : ""}`} initial={{ opacity: 0, scale: .97, y: 12 }} animate={{ opacity: 1, scale: 1, y: 0 }} transition={{ duration: .2 }}>
      <div className="modal-head"><div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div><Button variant="ghost" aria-label="关闭弹窗" onClick={onClose}><X size={18} /></Button></div><div className="modal-content">{children}</div>
    </motion.div>
  </motion.div>;
}

export function Notice({ children, error = false }: { children: ReactNode; error?: boolean }) {
  return <div className={`notice ${error ? "notice-error" : ""}`} role={error ? "alert" : "status"}>{error ? <X size={16} /> : <ShieldCheck size={16} />}<span>{children}</span></div>;
}

export function CopyButton({ text, label = "复制" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false), [error, setError] = useState(false);
  async function copy() {
    try {
      if (navigator.clipboard?.writeText && window.isSecureContext) await navigator.clipboard.writeText(text);
      else {
        const textarea = document.createElement("textarea"); textarea.value = text; textarea.style.position = "fixed"; textarea.style.opacity = "0";
        document.body.appendChild(textarea); textarea.select(); const ok = document.execCommand("copy"); textarea.remove(); if (!ok) throw new Error("复制失败");
      }
      setCopied(true); setTimeout(() => setCopied(false), 1600);
    } catch { setCopied(false); setError(true); setTimeout(() => setError(false), 1800); }
  }
  return <Button onClick={copy} aria-label={label}>{copied ? <Check size={14} /> : <Copy size={14} />}{copied ? "已复制" : error ? "复制失败，请手动选择" : label}</Button>;
}
