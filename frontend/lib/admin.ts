import type { ExecutionStep, QueryResult, Status } from "./types";

export type User = { id: string; username: string; display_name: string; enabled: boolean; source: "hbairport01"; data_scope: string; configured_data_scope: string; roles: string[]; role_names: string[]; can_manage: boolean };
export type Auth = { initialized: boolean; can_setup: boolean; user: User | null; allow_guest: boolean; source?: "hbairport01" };
export type Model = { id: string; name: string; url: string; model: string; enabled: boolean | number; has_key: boolean; active: boolean; updated_at: string };
export type Conversation = { id: string; title: string; owner: string; owner_name: string; archived: number; message_count: number; created_at: string; updated_at: string };
export type Message = { id: string; question: string; result: QueryResult | null; status: string; error?: string; model: string; created_at: string; completed_at?: string; execution_trace?: ExecutionStep[] };
export type ConversationDetail = { conversation: Conversation; messages: Message[] };
export type Paged<T> = { items: T[]; total: number; page: number; page_size: number };
export type Overview = { counts: Record<string, number>; status: Status; active_model: Model | null };
export type Term = { id?: string; kind: "company_alias" | "entity_alias" | "field" | "metric"; table: string; key: string; value: string; description: string; unit?: string; field?: string; group_by?: string[] };
export type Glossary = { revision: string; items: Term[]; entities: Record<string, { name: string; attributes: Record<string, string> }>; enterprises: { name: string; code: string }[] };
export function roleLabel(user: User) { return (user.role_names.length ? user.role_names : user.roles).join("、") || "业务账号"; }
export const TERM_NAMES = { company_alias: "企业简称", entity_alias: "对象别名", field: "字段解释", metric: "统计指标" };
export function formatDate(value?: string) { return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—"; }
