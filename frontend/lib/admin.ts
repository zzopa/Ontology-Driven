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

export type SyncStepDetail = {
  step: string;
  label: string;
  mode?: string;
  fetched: number;
  inserted: number;
  updated: number;
  skipped: number;
  failed: number;
  elapsed_ms: number;
  watermark_before?: string;
  watermark_after?: string;
  message?: string;
  dimensions?: Record<string, Record<string, number>>;
};

export type SyncRun = {
  id: string;
  job_key: string;
  title: string;
  source_origin: "ui" | "external" | string;
  mode: string;
  status: "running" | "completed" | "partial" | "failed" | "interrupted" | string;
  started_at: string;
  finished_at?: string | null;
  elapsed_ms: number;
  fetched: number;
  inserted: number;
  updated: number;
  skipped: number;
  failed: number;
  watermark_before?: string;
  watermark_after?: string;
  actor: string;
  actor_name: string;
  params?: Record<string, unknown> | null;
  detail?: { steps?: SyncStepDetail[]; error?: string } | Record<string, unknown> | null;
  message?: string;
  backup_file?: string;
};

export type SyncSummary = {
  total_runs: number;
  completed_runs: number;
  failed_runs: number;
  running_runs: number;
  total_fetched: number;
  total_inserted: number;
  total_updated: number;
  total_failed: number;
  today_runs: number;
  today_fetched: number;
  latest_success_at?: string | null;
};

export type SyncBackup = { name: string; size_bytes: number; modified_at: string };

export type SyncJobState = {
  job_name: string;
  job_key: string;
  title: string;
  watermark: string;
  last_success_at: string;
  started_at?: string;
  parsed_result?: Record<string, unknown>;
};

export type SyncOracleTableStat = { count: number; min_date?: string | null; max_date?: string | null };

export type SyncOverview = {
  ok: boolean;
  data_source: string;
  oracle: {
    reachable: boolean;
    configured?: boolean;
    host: string;
    port: number;
    service_name?: string;
    airport_code?: string;
    latency_ms?: number;
    error?: string;
    source_stats?: {
      ok: boolean;
      latency_ms?: number;
      checked_at?: string;
      tables?: Record<string, SyncOracleTableStat>;
    } | null;
  };
  engine: {
    mode: string;
    ready: boolean;
    airport_code: string;
  };
  pg: {
    available: boolean;
    database: string;
    job_states: SyncJobState[];
    tables: Record<string, { count: number; min_date?: string | null; max_date?: string | null }>;
    dimensions: Record<string, number>;
  };
  backups: SyncBackup[];
  schema_status: Status;
  active_run: SyncRun | null;
  summary: SyncSummary;
  reconciled_added?: number;
};

export const SYNC_JOB_LABELS: Record<string, string> = {
  full_sync: "近 1 个月全量导入",
  full_pipeline: "一键组合同步",
  flights_scheduled: "航班增量（FIMS+ACDM）",
  flights_fims: "FIMS 航班动态",
  flights_acdm: "ACDM 航班动态",
  production: "货邮行生产量（FORMFILL）",
  calc_total: "动态汇总旅客（FIMS_CALC_TOTAL）",
  safeguards: "登机桥与保障节点（FIMS）",
  codeshares: "FIMS 共享航班",
  master_data: "基础资料全量（dim_*）",
};

export const SYNC_STATUS_LABELS: Record<string, string> = {
  running: "执行中",
  completed: "成功",
  partial: "部分成功",
  failed: "失败",
  interrupted: "中断",
};

export function formatDurationMs(ms?: number) {
  if (!ms || ms <= 0) return "< 1s";
  if (ms < 1000) return `${ms} ms`;
  const sec = ms / 1000;
  if (sec < 60) return `${sec.toFixed(1)} s`;
  const min = Math.floor(sec / 60);
  const rem = Math.round(sec % 60);
  return `${min}m ${rem}s`;
}

export function formatBytes(bytes?: number) {
  if (!bytes || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes, idx = 0;
  while (value >= 1024 && idx < units.length - 1) { value /= 1024; idx += 1; }
  return `${value >= 10 || idx === 0 ? Math.round(value) : value.toFixed(1)} ${units[idx]}`;
}

export function roleLabel(user: User) { return (user.role_names.length ? user.role_names : user.roles).join("、") || "业务账号"; }
export const TERM_NAMES = { company_alias: "企业简称", entity_alias: "对象别名", field: "字段解释", metric: "统计指标" };
export function formatDate(value?: string | null) { return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—"; }

export type OpsConcept = {
  id?: number;
  code: string;
  name: string;
  domain: string;
  conceptType: string;
  description: string;
  status: string;
  version?: number;
  synonymCount?: number;
};

export type OpsSynonym = {
  id: number;
  conceptId?: number;
  conceptCode: string;
  conceptName: string;
  conceptType?: string;
  term: string;
  termType: string;
  weight: number;
};

export type OpsAttribute = {
  id?: number;
  code: string;
  name: string;
  columnName: string;
  tableName: string;
  valueType: string;
  description: string;
};

export type OpsScenario = {
  code: string;
  name: string;
  queryType: string;
  metricCode: string;
  dimensions: string[];
  defaultFilters: Record<string, unknown>;
  selectFields: string[];
  triggerKeywords: string[];
  priority: number;
  status: string;
};

export type OpsMetric = {
  id?: number;
  code: string;
  name: string;
  domain: string;
  description: string;
  formulaDesc: string;
  aggregation: string;
  unit: string;
  ruleCode: string;
  availability: string;
  phase: number;
  status: string;
  baseTable: string;
  aggregateExpression: string;
  valueAlias: string;
  dateField: string;
  defaultFilters: Record<string, unknown>;
  joinsJson: Array<Record<string, unknown>>;
  requiredFields: string[];
};

export type OpsDimension = {
  id?: number;
  code: string;
  name: string;
  expression: string;
  alias: string;
  requiredField: string;
  status: string;
};

export type OpsFilter = {
  id?: number;
  code: string;
  name: string;
  columnExpr: string;
  operator: string;
  joinIndicator: boolean;
  valueType: string;
  status: string;
};

export type OpsRule = {
  id?: number;
  code: string;
  name: string;
  ruleType: string;
  domain: string;
  description: string;
  status: string;
  versionNo?: number;
  scopeType: string;
  expression: string;
  outputMapping?: Record<string, unknown>;
  priority: number;
  published?: boolean;
};

export type OpsCapability = {
  id?: number;
  code: string;
  name: string;
  domain: string;
  phase: number;
  status: string;
  coverageJson: Record<string, unknown>;
  fallbackMessage: string;
  alternatives: string[];
};

export type OpsPlanTemplate = {
  id?: number;
  code: string;
  name: string;
  planType: string;
  triggerConcepts: string[];
  requiredCaps: string[];
  responseTemplate: string;
  status: string;
  steps?: Array<{ id: number; stepOrder: number; stepCode: string; stepType: string; onFail: string }>;
};

export type BizModelOverview = {
  ok: boolean;
  objectCount: number;
  attributeCount: number;
  relationCount: number;
  objects: Array<{ code: string; name: string; summary: string; description: string; attributeCount: number; relationCount: number }>;
};

export type BizModelObjectDetail = {
  ok: boolean;
  object: { code: string; name: string; summary: string; description: string };
  attributes: Array<{ code: string; name: string; attrGroup: string; valueType: string; description: string; exampleValue: string; stdEntity: string; stdField: string; askAttrCode: string }>;
  attributesByGroup: Record<string, Array<{ code: string; name: string; attrGroup: string; valueType: string; description: string; exampleValue: string; stdEntity: string; stdField: string; askAttrCode: string }>>;
  relations: Array<{ code: string; name: string; cardinality: string; description: string; fromObject: string; toObject: string; fromObjectName: string; toObjectName: string; peerObject: string; peerObjectName: string; direction: string }>;
};

export type PhysicalModel = {
  ok: boolean;
  entities: Array<{
    code: string; name: string; entityKind: string; grainDesc: string; description: string;
    fields: Array<{ code: string; name: string; dataType: string; fieldGroup: string; isBizKeyPart: boolean; isRequired: boolean; naturalRef: string; askAttrCode: string; description: string }>;
  }>;
  relations: Array<{ code: string; name: string; fromEntity: string; toEntity: string; cardinality: string; joinType: string; fromFields: string; toFields: string; description: string }>;
};

export type OntologyCapabilityProfile = {
  ok: boolean;
  dataCoverage: { start?: string; end?: string; totalRecords?: number; sourceTable?: string; empty?: boolean };
  summary: { metricCount: number; filterCount: number; dimensionCount: number; scenarioCount: number; attributeCount: number; conceptCount?: number; synonymCount?: number };
  queryTypes: Array<{ queryType: string; name: string; supportLevel: string; description: string; exampleQuestions: string[]; unsupportedPatterns: string[] }>;
  timeSemantics: Array<{ slot: string; name: string; values: string[]; defaultPolicy: string }>;
  limitations: string[];
  examples: Array<{ question: string; queryType: string; scenarioCode: string; note: string }>;
};

export type HomeAirportConfig = { iataCode: string; cityName: string; displayName: string; aliases: string[] };
export type OperationalDayConfig = { enabled: boolean; startHour: number; timezone: string; boundaryLabel?: string };

export type AskFeedbackItem = {
  id: string;
  conversation_id: string;
  message_id: string;
  user_id: string;
  username: string;
  display_name: string;
  question_text: string;
  answer_text: string;
  response_summary?: Record<string, unknown> | null;
  reason_codes: string[];
  comment: string;
  severity: "HIGH" | "NORMAL" | string;
  status: "OPEN" | "INVESTIGATING" | "FIXED" | "WONT_FIX" | string;
  admin_note: string;
  created_at: string;
  updated_at: string;
};

export const FEEDBACK_REASON_LABELS: Record<string, string> = {
  WRONG_INTENT: "答非所问",
  WRONG_DATA: "数据不对",
  WRONG_FILTER: "条件搞错",
  NO_ANSWER: "未答出",
  OTHER: "其它",
};

export const FEEDBACK_STATUS_LABELS: Record<string, string> = {
  OPEN: "待处理",
  INVESTIGATING: "跟进中",
  FIXED: "已修复",
  WONT_FIX: "不处理",
};

export function govStatusBadge(status?: string): { label: string; tone: "green" | "amber" | "neutral" } {
  const s = String(status || "").toUpperCase();
  if (s === "PUBLISHED" || s === "AVAILABLE" || s === "ACTIVE" || s === "ENABLED" || s === "FULL" || s === "FIXED") {
    const labels: Record<string, string> = {
      PUBLISHED: "已发布", AVAILABLE: "可用", ACTIVE: "启用", ENABLED: "启用", FULL: "完全支持", FIXED: "已修复",
    };
    return { label: labels[s] || s, tone: "green" };
  }
  if (s === "DRAFT" || s === "PARTIAL" || s === "PLANNED" || s === "OPEN" || s === "INVESTIGATING") {
    const labels: Record<string, string> = {
      DRAFT: "草稿", PARTIAL: "部分可用", PLANNED: "规划中", OPEN: "待处理", INVESTIGATING: "跟进中",
    };
    return { label: labels[s] || s, tone: "amber" };
  }
  const labels: Record<string, string> = {
    ARCHIVED: "已归档", UNAVAILABLE: "不可用", DISABLED: "已停用", NONE: "未实现", WONT_FIX: "不处理",
  };
  return { label: labels[s] || s || "未知", tone: "neutral" };
}
