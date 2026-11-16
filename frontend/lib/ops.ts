import { request } from "./api";

export const kinds = { cleaning: "保洁", fueling: "加油", catering: "配餐", loading: "装卸", boarding: "登机" } as const;
export type Kind = keyof typeof kinds;
export type OpsPage = "overview" | "flights" | "dispatch" | "work-orders" | "resources";
export type OpsMode = "replay" | "live";
export const replayDataDate = "2026-11-10";
export type OpsRun = (action: () => Promise<unknown>, message?: string) => Promise<void>;
export type OpsMe = { user: { id: string; display_name: string }; admin: boolean; terminals: string[]; mode: OpsMode; data_mode?: string; read_only?: boolean; fixture_time?: string; fixture_label?: string; grants: { user_id: string; terminal: string; role: "viewer" | "dispatcher" | "crew"; resource_id: string }[] };
export type OpsOperation = { kind: Kind; name: string; status: string; status_label: string; source: string; actual_start: string | null; actual_end: string | null; plan_start: string | null; plan_end: string | null; replay_censored?: boolean; manual_start?: string; manual_end?: string; manual_state?: string; task_id?: string; resource_id?: string };
export type AircraftState = 'parked' | 'cleaning' | 'fueling' | 'catering' | 'loading' | 'boarding' | 'waiting_hangar' | 'taxiing' | 'taking_off';
export type OpsFlight = { id: string; flight_no: string; flight_date: string; direction: string; aircraft_reg: string; stand: string; gate: string; terminal: string; tobt: string | null; tsat: string | null; aibt: string | null; sobt: string | null; target_at: string | null; risk?: boolean; operations: OpsOperation[];
  aircraft_state?: { code: AircraftState; label: string }; motion?: { duration_ms: number; path: [number, number, number][] } };
export type OpsResource = { id: string; terminal: string; kind: Kind; name: string; enabled: boolean; qualifications_confirmed: boolean; equipment_confirmed: boolean; qualified_until: string | null; position: string; availability: "idle" | "busy" | "unknown"; confirmed_at: string | null; revision: number };
export type OpsTask = { id: string; terminal: string; kind: Kind; resource_id: string; flight_id: string; flight_no: string; stand: string; start_at: string; end_at: string; latest_end_at?: string | null; status: string; actual_start?: string | null; actual_end?: string | null; revision: number; work_order_id?: string };
export type OpsRoster = { id: string; resource_id: string; start_at: string; end_at: string };
export type OpsPolicy = { kind: Kind; duration_minutes: number | null; grace_minutes: number; confirmed: boolean };
export type OpsAlert = { id: string; title: string; detail: string; flight_ids: string[]; kind: Kind | ""; severity: string; status: string; resource_id?: string; first_seen?: string; resolved_at?: string };
export type OpsSettings = { shadow_mode: boolean; dispatch_enabled: boolean };
export type OpsTransfer = { from_stand: string; to_stand: string; minutes: number };
export type OpsImpact = { flight_id: string; flight_no: string; relation: string; detail: string };
export type OpsCheck = { eligible: boolean; errors: string[]; impacts: OpsImpact[]; transfer_before: number | null; transfer_after: number | null; slack_minutes: number | null };
export type OpsProposal = { id: string; terminal: string; state: string; version: string; created_at: string; expires_at: string; target: OpsTask; resource: OpsResource; start_at: string; end_at: string; tobt: string | null; impacts: OpsImpact[]; check: OpsCheck };
export type OpsCandidate = OpsCheck & { resource: OpsResource };
export type OpsPerceptionStep = { at_ms: number; phase: "scan" | "detect" | "associate" | "check" | "proposal"; title: string; description: string; focus_stand?: string };
export type OpsPerceptionScenario = { id: string; terminal: string; title: string; alert_id: string; proposal_id?: string; stand_codes: string[]; resource_ids: string[]; route_stands: string[]; route_points: [number, number][]; steps: OpsPerceptionStep[] };
export type OpsStand = { airport: string; terminal: string; code: string; name: string; zone?: string; kind?: 'taxiway' | 'runway' | 'hangar'; scene?: { x: number; z: number; heading: number; bridge_x?: number; bridge_z?: number; dual?: boolean; layout_ref?: string } };
export type OpsOrder = { id: string; resource_id: string; resource_name: string; flight_id: string; flight_no: string; proposal_id: string; kind: Kind; stand: string; start_at: string; end_at: string; actual_start: string | null; actual_end: string | null; state: string; revision: number; source: string; approved_at: string; last_note?: string; verification_source?: string };
export type OpsEvent = { id: number; actor: string; action: string; target: string; detail: string; at: string };
export type OpsLedger = { resources: OpsResource[]; tasks: OpsTask[]; rosters: OpsRoster[]; transfers: OpsTransfer[]; policies: Record<Kind, OpsPolicy>; settings: OpsSettings; grants: OpsMe["grants"] };
export type OpsSnapshot = Omit<OpsLedger, "grants"> & {
  mode: OpsMode; data_mode?: string; read_only?: boolean; real_time: string; clock_offset_days: number;
  snapshot_id: string; terminal: string; captured_at: string; window_end: string; minutes: number;
  flights: OpsFlight[]; stands: OpsStand[]; alerts: OpsAlert[]; proposals?: OpsProposal[];
  perception?: { title: string; layout_note: string; calibrated: boolean; scenarios: OpsPerceptionScenario[] };
  metrics: { flights: number; in_progress: number; attention: number; resource_gaps: number | null; pending: number };
  source: { fresh: boolean; jobs: Record<string, string | null>; checked_at: string; message: string };
  worker: { active: boolean; heartbeat: string | null; message: string };
  workload: { kind: Kind; name: string; start_at: string; end_at: string; demand: number; available: number | null; gap: number | null; basis: string }[];
};

export function opsRequest<T>(path: string, terminal: string, payload?: unknown, method = "POST"): Promise<T> {
  const separator = path.includes("?") ? "&" : "?";
  const at = opsReplayAt();
  return request<T>(`/api/ops${path}${separator}terminal=${encodeURIComponent(terminal)}&mode=${opsMode()}${at ? `&at=${encodeURIComponent(at)}` : ""}`, payload === undefined ? undefined : { method, body: JSON.stringify(payload) });
}

export function opsMode(): OpsMode {
  return typeof window !== "undefined" && new URLSearchParams(window.location.search).get("mode") === "live" ? "live" : "replay";
}

export function opsReplayAt(): string {
  return opsMode() === "replay" && typeof window !== "undefined" ? new URLSearchParams(window.location.search).get("at") || "" : "";
}

export function opsNow(): Date {
  if (opsMode() === "live") return new Date(Date.now());
  const at = opsReplayAt();
  if (at) { try { return new Date(inputISO(at)); } catch { /* API reports invalid URL timestamps. */ } }
  const value = new Date(Date.now() - 86400000);
  const day = localInput(value).slice(0, 10);
  return new Date(value.getTime() - Math.max(0, Date.parse(day) - Date.parse(replayDataDate)));
}

export function opsHref(path: string, terminal: string, params: Record<string, string> = {}): string {
  const at = opsReplayAt();
  return `${path}?${new URLSearchParams({ terminal, mode: opsMode(), ...(at ? { at } : {}), ...params })}`;
}

export function opsTime(value: string | null | undefined, full = false): string {
  if (!value) return "未记录";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "时刻无效";
  return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", ...(full ? { month: "2-digit", day: "2-digit" } as const : {}), hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(date);
}

export function localInput(date = opsNow()): string {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(date);
  const p = (key: string) => parts.find(x => x.type === key)?.value || "";
  return `${p("year")}-${p("month")}-${p("day")}T${p("hour")}:${p("minute")}`;
}

export function inputISO(value: FormDataEntryValue | null): string {
  const text = String(value);
  const date = new Date(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/.test(text) ? text + "+08:00" : text);
  if (!value || !Number.isFinite(date.getTime())) throw new Error("请输入完整有效的日期和时间");
  return date.toISOString();
}

export function timelinePosition(value: string, start: string, end: string): number {
  const range = Date.parse(end) - Date.parse(start);
  return range > 0 ? Math.max(0, Math.min(100, (Date.parse(value) - Date.parse(start)) / range * 100)) : 0;
}

export function resourceConfirmed(resource: OpsResource, observed: string): boolean {
  const age = Date.parse(observed) - Date.parse(resource.confirmed_at || "");
  return Number.isFinite(age) && age >= -60000 && age <= 600000;
}

export function opsTerminals(me: OpsMe): string[] {
  return me.read_only && me.admin && me.data_mode === 'static-test' ? ['ALL', ...me.terminals] : me.terminals;
}
