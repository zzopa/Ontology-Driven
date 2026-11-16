import type { AircraftState, OpsPerceptionScenario, OpsSnapshot } from "./ops";

export const aircraftStates: Record<AircraftState, { label: string; color: string }> = {
  parked: { label: '停机', color: '#8da8c4' }, cleaning: { label: '保洁', color: '#60cef4' },
  fueling: { label: '加油', color: '#edba59' }, catering: { label: '配餐', color: '#b8a0ed' },
  loading: { label: '装卸', color: '#62d4ac' }, boarding: { label: '登机', color: '#68a5f4' },
  waiting_hangar: { label: '等待入库', color: '#df9d76' }, taxiing: { label: '滑行', color: '#f2d171' },
  taking_off: { label: '起飞', color: '#d49cf1' },
};

export function apronMonitor(snapshot: OpsSnapshot) {
  const states = Object.entries(aircraftStates).map(([code, info]) => ({ code: code as AircraftState, ...info,
    count: snapshot.flights.filter(f => (f.aircraft_state?.code || 'parked') === code).length }));
  const stands = snapshot.stands.filter(s => !s.kind);
  return { states, stands: stands.length, occupied: stands.filter(s => snapshot.flights.some(f => f.stand === s.code)).length,
    operations: ['cleaning', 'fueling', 'catering', 'loading', 'boarding'].map(kind => {
      const list = snapshot.flights.flatMap(f => f.operations.filter(op => op.kind === kind));
      return { kind, completed: list.filter(op => op.status === 'completed').length, active: list.filter(op => op.status === 'in_progress').length,
        risk: list.filter(op => op.status === 'risk').length, planned: list.filter(op => op.status !== 'completed' && op.status !== 'in_progress' && op.status !== 'risk').length, total: list.length };
    }) };
}

export function apronMapPosition(code: string, snapshot: OpsSnapshot) {
  const points = snapshot.stands.flatMap(s => s.scene ? [s.scene] : []), point = snapshot.stands.find(s => s.code === code)?.scene;
  if (!point || !points.length) return { left: '50%', top: '50%' };
  const xs = points.map(p => p.x), zs = points.map(p => p.z), minX = Math.min(...xs), minZ = Math.min(...zs);
  return { left: `${10 + (point.x - minX) / Math.max(1, Math.max(...xs) - minX) * 80}%`, top: `${20 + (point.z - minZ) / Math.max(1, Math.max(...zs) - minZ) * 65}%` };
}

export function perceptionStep(scenario: OpsPerceptionScenario, elapsed: number): number {
  return Math.max(0, scenario.steps.findLastIndex(step => step.at_ms <= elapsed));
}

export function previewProgress(scenario: OpsPerceptionScenario, elapsed: number, preview: boolean): number {
  if (!preview) return 0;
  return Math.max(0, Math.min(1, (elapsed - scenario.steps.at(-1)!.at_ms) / 8000));
}

export function scenarioFlights(snapshot: OpsSnapshot, scenario: OpsPerceptionScenario) {
  const alert = snapshot.alerts.find(a => a.id === scenario.alert_id);
  return snapshot.flights.filter(f => alert?.flight_ids.includes(f.id));
}

export function perceptionStandTone(scenario: OpsPerceptionScenario, code: string, detected: boolean, recommended: boolean, preview: boolean) {
  if (recommended && scenario.route_stands.includes(code) && (code !== scenario.route_stands[1] || preview)) return 'recommended';
  if (detected && scenario.stand_codes.includes(code) && code !== scenario.route_stands.at(-1)) return 'risk';
  return 'normal';
}

export function labelTop(x: number, y: number, placed: { x: number; y: number }[], width: number, height: number): number {
  while (placed.some(other => Math.abs(other.x - x) < width && Math.abs(other.y - y) < height)) y -= height;
  return y;
}

export function perceptionCommand(snapshot: OpsSnapshot, text: string) {
  const value = text.trim();
  const flight = snapshot.flights.find(f => value.toUpperCase().includes(f.flight_no));
  const stand = snapshot.stands.find(item => new RegExp(`(^|\\D)${item.code}(?!\\d)`).test(value));
  if (flight || stand) return { type: 'stand', code: flight?.stand || stand!.code } as const;
  if (/俯视|平面/.test(value)) return { type: 'camera', view: 'map' } as const;
  if (/全景|总览/.test(value)) return { type: 'camera', view: 'overview' } as const;
  const keyword = /配餐/.test(value) ? '配餐' : /装卸/.test(value) ? '装卸' : /保洁|冲突|调配/.test(value) ? '保洁' : '';
  const terminal = /T3/i.test(value) ? 'T3' : /T2/i.test(value) ? 'T2' : '';
  const scenario = keyword && snapshot.perception?.scenarios.find(item => item.title.includes(keyword) && (!terminal || item.terminal === terminal));
  if (scenario) return { type: 'scenario', id: scenario.id } as const;
  if (/T3/i.test(value)) return { type: 'camera', view: 't3' } as const;
  if (/T2|航站楼/i.test(value)) return { type: 'camera', view: 'terminal' } as const;
  return { type: 'unknown' } as const;
}
