export type Row = Record<string, unknown>;
export type Field = { label?: string; comment?: string; description?: string; unit?: string; type?: string; column?: string; path?: string[]; enums?: Record<string, string> };
export type FieldMeta = Record<string, Record<string, Field>>;
export type Condition = { table?: string; col: string; op: string; value: unknown };
export type Edge = { source: string; target: string; fcol: string; tcol: string; note?: string; type?: string };
export type Fact = {
  id: string; table: string; name: string; kind: string; business_time?: unknown;
  values?: Row; properties?: Record<string, { value: unknown; label?: string; unit?: string }>;
  source: { database: string; table: string; queried_at: string; record_key?: Row; scope?: string };
};
export type ExecutionStep = {
  id: string; label: string; state: "running" | "completed" | "failed" | "skipped" | "interrupted";
  message: string; details: string[]; started_at: number; elapsed: number;
};
export type AnswerKind = "query_facts" | "findings" | "interpretation" | "limitations" | "recommendations";
export type AnswerDocument = {
  version: string; mode: "deterministic" | "model_composed"; composition_reason: string;
  claim_count: number; query_claim_count: number; semantic_proof: boolean;
  sections: { kind: AnswerKind; title: string; claim_ids: string[] }[];
};
export type QueryResult = {
  execution_trace?: ExecutionStep[];
  conversation_id?: string; message_id?: string;
  ok: boolean; question: string; answer: string; intent: string; source: string; elapsed: number;
  parse: { subject: string; disp?: string; intent: string; conditions: Condition[]; aggregate_target?: string; time_bucket?: string };
  sql: string; sql_params?: unknown[]; main_total: number; main_rows: Row[]; field_meta: FieldMeta;
  related: { table: string; name: string; note: string; type: string; level: number; count: number; rows: Row[] }[];
  graph: { nodes: { id: string; main: boolean; level: number }[]; edges: Edge[] };
  graph_tables: unknown[]; drill_token?: string; related_token?: string; page_size: number;
  metric?: { name: string; unit: string; description: string; target: string };
  claims?: { text: string; fact_ids: string[]; kind?: Exclude<AnswerKind, "query_facts"> }[];
  query_summary?: string; answer_document?: AnswerDocument;
  answer_validation: string; ontology_version: string;
  evidence?: { facts: Fact[]; queried_at: string; coverage: { table: string; name: string; total: number; provided: number; complete: boolean }[] };
  timings?: Record<string, number>;
};
export type PageData = { rows: Row[]; field_meta: FieldMeta; subject?: string; table?: string; total: number; page: number; page_size: number };
export type Status = {
  ready: boolean; database: string; tables: number; fields: number; relations: number; queryable_entities: number;
  refreshed_at: string; startup_seconds: number; average_seconds: number; requests: number; failures: number;
  version: string; warnings: unknown[]; metadata_diff: Record<string, string[]>;
};
export type Entity = {
  name: string; aliases: string[]; attributes: Record<string, Field>; bindings: Record<string, string>;
  metrics: Record<string, { name?: string; field: string; unit?: string; description?: string }>;
  identity_fields?: string[]; grain?: string;
};
export type Ontology = {
  can_edit: boolean; revision: string; document: Record<string, unknown>;
  catalog: { database: string; version: string; refreshed_at: string; entities: Record<string, Entity>; warnings: unknown[];
    graph: { links_all: { source: string; target: string; from_table: string; to_table: string; note: string; cardinality: string; validation: string; status: string; type?: string }[] } };
};
export type StreamEvent = { type: string; stage?: string; message?: string; percent?: number; delta?: string; data?: QueryResult; conversation_id?: string; step?: ExecutionStep };
