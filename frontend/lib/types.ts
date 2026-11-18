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
export type VisualStatItem = { label: string; value: string; unit?: string };
export type VisualDonutSlice = { label: string; value: number; percent: number; color: string; count?: number };
export type VisualBarItem = { label: string; value: number; percent: number; color?: string; count?: number; time_key?: string };
export type VisualTrendPoint = { label: string; value: number; time_key?: string };
export type VisualDetailColumn = { key: string; label: string; unit?: string };
export type VisualDetailFlightRow = {
  flight_no: string;
  airline?: string;
  airline_name?: string;
  direction?: string;
  direction_label?: string;
  flight_date?: string;
  origin?: string;
  origin_code?: string;
  destination?: string;
  destination_code?: string;
  route?: string;
  plan_time?: string;
  actual_time?: string;
  stand?: string;
  gate?: string;
  terminal?: string;
  aircraft_type?: string;
  aircraft_reg?: string;
  status?: string;
  delay_minutes?: number | null;
  delay_reason?: string;
  drill_question?: string;
};
export type VisualDetailTable = {
  kind: "detail_table";
  is_flight: boolean;
  title: string;
  total: number;
  shown: number;
  summary?: {
    dep_count: number;
    arr_count: number;
    ontime_count: number;
    delayed_count: number;
    planned_count?: number;
    hourly_distribution?: { hour: number; label: string; count: number }[];
  };
  columns: VisualDetailColumn[];
  rows: (VisualDetailFlightRow | Row)[];
};
export type FishboneNode = {
  key: string;
  name: string;
  code: string;
  category: "passenger" | "ramp" | "control" | "runway";
  position: "top" | "bottom";
  plan_time?: string;
  estimated_time?: string;
  actual_time?: string;
  plan_at?: string;
  estimated_at?: string;
  actual_at?: string;
  time_display: string;
  status: "completed" | "ongoing" | "delayed" | "planned" | "pending";
  status_label: string;
  delay_minutes?: number;
  note?: string;
};
export type VisualFishbone = {
  kind: "fishbone";
  title: string;
  flight_no: string;
  direction?: string;
  direction_label?: string;
  flight_date?: string;
  aircraft_type?: string;
  aircraft_reg?: string;
  stand?: string;
  gate?: string;
  origin?: string;
  destination?: string;
  status?: string;
  summary?: string;
  total_flights?: number;
  nodes: FishboneNode[];
};
export type TurnaroundMilestones = {
  aibt: string | null;
  sobt: string | null;
  tobt: string | null;
  tsat: string | null;
  current_time: string | null;
};
export type TurnaroundOperation = {
  id: string;
  name: string;
  icon: string;
  status: "completed" | "in_progress" | "delayed" | "planned";
  actual_start?: string | null;
  actual_end?: string | null;
  plan_start?: string | null;
  plan_end?: string | null;
  delay_min?: number;
  label?: string;
  milestones?: { name: string; plan?: string | null; actual?: string | null }[];
};
export type TurnaroundPipelineNode = {
  step: number;
  name: string;
  plan: string;
  actual: string;
  target?: string;
  status: "done" | "in_progress" | "waiting";
};
export type VisualTurnaround = {
  kind: "turnaround_gantt";
  source_row?: Row;
  query_identifier?: string;
  selection_note?: string;
  source_label?: string;
  flight_pair: string;
  in_flight: string;
  out_flight: string;
  craft_no: string;
  stand: string;
  gate: string;
  flight_date: string;
  milestones: TurnaroundMilestones;
  time_ticks: string[];
  timeline_range: string;
  operations: TurnaroundOperation[];
  pipeline: TurnaroundPipelineNode[];
  flight_nodes?: FishboneNode[];
  flight_nodes_source?: string;
  ground_nodes?: FishboneNode[];
  association_note?: string;
  available_record?: { flight_date: string; flight_no: string; source_label: string; recorded_operations: number; question: string };
  conclusion: {
    summary: string;
    meta: {
      has_delay: boolean;
      evidence: string;
      actual: string;
      plan_end: string;
      delay_desc: string;
    };
  };
};
export type VisualMeta =
  | { kind: "none" }
  | { kind: "stats"; title: string; stats: VisualStatItem[] }
  | { kind: "donut"; title: string; slices: VisualDonutSlice[]; total: number; unit: string; group_label: string }
  | { kind: "bar"; title: string; bars: VisualBarItem[]; unit: string; group_label: string; is_rate?: boolean }
  | { kind: "trend"; title: string; series: VisualTrendPoint[]; unit: string; metric_label: string }
  | VisualDetailTable
  | VisualFishbone
  | VisualTurnaround;
export type QueryResult = {
  data_source?: string;
  sections?: AnalysisSection[];
  airport_sources?: { airport_code: string; airport_name: string; sources: string[] }[];
  comparisons?: { current_id: string; baseline_id: string; difference: number | null; growth_pct: number | null; difference_unit: string }[];
  forecast?: Forecast;
  partial?: boolean;
  is_simulated?: boolean;
  data_origin?: "simulated" | "observed";
  report_id?: string;
  outcome?: "answered" | "clarification_needed" | "unsupported" | "no_data" | "out_of_coverage";
  clarification?: { question: string; options: { id: string; label: string }[] };
  coverage_assessment?: { status: string; complete?: boolean; message?: string; table?: string; coverage_start?: string | null; coverage_end?: string | null };
  execution_trace?: ExecutionStep[];
  conversation_id?: string; message_id?: string;
  ok: boolean; question: string; answer: string; intent: string; source: string; elapsed: number;
  parse: { subject: string; disp?: string; intent: string; conditions: Condition[]; aggregate_target?: string; time_bucket?: string };
  sql: string; sql_params?: unknown[]; main_total: number; main_rows: Row[]; field_meta: FieldMeta;
  related: { table: string; name: string; note: string; type: string; level: number; count: number; rows: Row[] }[];
  graph: { nodes: { id: string; main: boolean; level: number }[]; edges: Edge[] };
  graph_tables: unknown[]; drill_token?: string; related_token?: string; page_size: number;
  metric?: { name: string; unit: string; description: string; target: string; formula_desc?: string; aggregate_expression?: string };
  applied_rules?: Array<{ code: string; name: string; ruleType?: string; versionNo?: number; domain?: string; description?: string }>;
  plan_template?: { code: string; name: string; planType?: string; requiredCaps?: string[] } | null;
  capability_gate?: { allowed: boolean; status: string; capabilityCode?: string; message?: string; alternatives?: string[] } | null;
  operational_window?: { start: string; end: string; startHour: number; field?: string } | null;
  claims?: { text: string; fact_ids: string[]; kind?: Exclude<AnswerKind, "query_facts"> }[];
  visual_meta?: VisualMeta;
  query_summary?: string; answer_document?: AnswerDocument;
  answer_validation: string; ontology_version: string;
  evidence?: { facts: Fact[]; queried_at: string; coverage: { table: string; name: string; total: number; provided: number; complete: boolean }[] };
  timings?: Record<string, number>;
};
export type AnalysisSection = { id: string; label: string; metric_key: string; period_index: number; object: string; period: string[]; value: number | null; unit: string; is_simulated: boolean; result: QueryResult; formula_desc?: string; calculation_process?: string; sample_count?: number; num_val?: number; den_val?: number };
export type Forecast = { metric: string; unit: string; as_of: string; training_start: string; training_end: string; quality: "experimental"; warning: string; methodology?: string; history: { date: string; value: number }[]; points: { date: string; value: number; lower: number; upper: number; method: string; mae: number; backtest_origins: number; distance_days: number; planned_value?: number | null }[] };
export type PageData = { rows: Row[]; field_meta: FieldMeta; subject?: string; table?: string; total: number; page: number; page_size: number };
export type Status = {
  data_source?: string; external_models_enabled?: boolean;
  coverage?: { start: string; end_exclusive: string; exported_at: string };
  operational_day?: { enabled: boolean; startHour: number; timezone?: string; boundaryLabel?: string } | null;
  home_airport?: { iataCode?: string; cityName?: string; displayName?: string } | null;
  ready: boolean; database: string; tables: number; fields: number; relations: number; queryable_entities: number;
  refreshed_at: string; startup_seconds: number; average_seconds: number; requests: number; failures: number;
  version: string; warnings: unknown[]; metadata_diff: Record<string, string[]>;
};
export type MetricItem = {
  code: string; name: string; domain?: string; description?: string; formula_desc?: string;
  aggregation?: string; unit?: string; base_table?: string; aggregate_expression?: string;
  value_alias?: string; date_field?: string; default_filters?: Record<string, unknown>;
  joins_json?: Array<{ alias?: string; table?: string; on?: string; type?: string }>;
  status?: string; field?: string; aliases?: string[];
};
export type DimensionItem = { code: string; name: string; expression?: string; alias?: string };
export type ScenarioItem = { code: string; name: string; query_type?: string; metric_code?: string; dimensions?: string[] };
export type Entity = {
  name: string; aliases: string[]; attributes: Record<string, Field>; bindings: Record<string, string>;
  metrics: Record<string, MetricItem>;
  identity_fields?: string[]; grain?: string;
};
export type Ontology = {
  can_edit: boolean; revision: string; document: Record<string, unknown>;
  catalog: {
    database: string; version: string; refreshed_at: string; entities: Record<string, Entity>; warnings: unknown[];
    metrics?: Record<string, MetricItem>;
    dimensions?: Record<string, DimensionItem>;
    scenarios?: Record<string, ScenarioItem>;
    graph: { links_all: { source: string; target: string; from_table: string; to_table: string; note: string; cardinality: string; validation: string; status: string; type?: string }[] }
  };
};
export type StreamEvent = { type: string; stage?: string; message?: string; percent?: number; delta?: string; data?: QueryResult; conversation_id?: string; step?: ExecutionStep; query_id?: string; section?: AnalysisSection };
