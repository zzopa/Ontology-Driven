"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { ChevronLeft, ChevronRight, Database } from "lucide-react";
import { request } from "@/lib/api";
import { Button, Notice, Spinner } from "../ui";

export function useResource<T>(path: string) {
  const [data, setData] = useState<T | null>(null), [error, setError] = useState(""), [loading, setLoading] = useState(true), [version, setVersion] = useState(0);
  const reload = useCallback(() => setVersion(v => v + 1), []);
  useEffect(() => {
    const abort = new AbortController(); setLoading(true); setError("");
    request<T>(path, { signal: abort.signal }).then(value => { if (!abort.signal.aborted) setData(value); }).catch(e => { if (e.name !== "AbortError" && !abort.signal.aborted) setError(e.message); }).finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [path, version]);
  return { data, error, loading, reload };
}

export function useAction() {
  const [busy, setBusy] = useState(false), [message, setMessage] = useState(""), [error, setError] = useState(false);
  async function run(operation: () => Promise<void>, success = "已保存") {
    if (busy) return; setBusy(true); setMessage("");
    try { await operation(); setError(false); setMessage(success); }
    catch (e) { setError(true); setMessage(e instanceof Error ? e.message : "操作失败"); }
    finally { setBusy(false); }
  }
  return { busy, run, feedback: message ? <Notice error={error}>{message}</Notice> : null };
}

export function Loading({ error, loading }: { error: string; loading: boolean }) {
  return error ? <Notice error>{error}</Notice> : loading ? <div className="admin-loading"><Spinner />正在读取管理数据…</div> : null;
}

export function Empty({ children }: { children: ReactNode }) { return <div className="empty-panel"><Database size={24} /><span>{children}</span></div>; }

export function Pagination({ data, page, onPage }: { data: { total: number; page_size: number } | null; page: number; onPage: (page: number) => void }) {
  if (!data) return null;
  return <div className="pagination"><span>共 {data.total} 条</span><div className="pagination-controls"><Button aria-label="上一页" disabled={page <= 1} onClick={() => onPage(page - 1)}><ChevronLeft size={13} /></Button><span>{page} / {Math.max(1, Math.ceil(data.total / data.page_size))}</span><Button aria-label="下一页" disabled={page * data.page_size >= data.total} onClick={() => onPage(page + 1)}><ChevronRight size={13} /></Button></div></div>;
}

