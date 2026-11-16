import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { apronMapPosition, apronMonitor, labelTop, perceptionCommand, perceptionStandTone, perceptionStep, previewProgress, scenarioFlights } from "../lib/ops-perception.ts";
import type { OpsSnapshot, OpsPerceptionScenario } from "../lib/ops.ts";

const fixture = JSON.parse(readFileSync(new URL('../../data/ops-test-data.json', import.meta.url), 'utf8'));
const scene = fixture.perception.scenarios[0] as OpsPerceptionScenario;

test("perception reveals evidence in order without advancing to future stages", () => {
  assert.equal(perceptionStep(scene, 0), 0);
  assert.equal(perceptionStep(scene, 1799), 0);
  assert.equal(perceptionStep(scene, 1800), 1);
  assert.equal(perceptionStep(scene, 8499), 3);
  assert.equal(perceptionStep(scene, 8500), 4);
  assert.equal(perceptionStep(scene, 60000), 4);
});

test("nearby map labels separate vertically while distant labels retain their anchor", () => {
  const placed = [{ x: 100, y: 200 }, { x: 120, y: 170 }];
  assert.equal(labelTop(110, 200, placed, 38, 30), 140);
  assert.equal(labelTop(200, 200, placed, 38, 30), 200);
  assert.equal(labelTop(100, 230, placed, 38, 30), 230);
  assert.equal(placed.length, 2);
});

test("downstream impact is distinct from a resource conflict, and only preview changes the target highlight", () => {
  assert.equal(perceptionStandTone(scene, '201', false, false, false), 'normal');
  assert.equal(perceptionStandTone(scene, '201', true, false, false), 'risk');
  assert.equal(perceptionStandTone(scene, '209', true, true, false), 'risk');
  assert.equal(perceptionStandTone(scene, '205', true, false, false), 'normal');
  assert.equal(perceptionStandTone(scene, '203', true, true, false), 'recommended');
  assert.equal(perceptionStandTone(scene, '205', true, true, false), 'recommended');
  assert.equal(perceptionStandTone(scene, '201', true, true, true), 'recommended');
  assert.equal(perceptionStandTone(fixture.perception.scenarios[1], '214', true, false, false), 'risk');
});

test("demo commands locate fixture objects and never invent a business action", () => {
  const snapshot = fixture as OpsSnapshot;
  assert.deepEqual(perceptionCommand(snapshot, '定位201机位'), { type: 'stand', code: '201' });
  assert.deepEqual(perceptionCommand(snapshot, '查看 cz3103'), { type: 'stand', code: '201' });
  assert.deepEqual(perceptionCommand(snapshot, '航班号1201'), { type: 'unknown' });
  assert.deepEqual(perceptionCommand(snapshot, '俯视'), { type: 'camera', view: 'map' });
  assert.deepEqual(perceptionCommand(snapshot, '全景'), { type: 'camera', view: 'overview' });
  assert.deepEqual(perceptionCommand(snapshot, 'T2'), { type: 'camera', view: 'terminal' });
  assert.deepEqual(perceptionCommand(snapshot, 'T3'), { type: 'camera', view: 't3' });
  assert.deepEqual(perceptionCommand(snapshot, '定位01'), { type: 'stand', code: '01' });
  assert.deepEqual(perceptionCommand(snapshot, 'T3 保洁冲突'), { type: 'scenario', id: 't3-cleaning' });
  assert.deepEqual(perceptionCommand(snapshot, 'CZ9002'), { type: 'stand', code: 'RW-04R' });
  assert.deepEqual(perceptionCommand(snapshot, '配餐超时'), { type: 'scenario', id: fixture.perception.scenarios[1].id });
  assert.deepEqual(perceptionCommand(snapshot, '装卸设备'), { type: 'scenario', id: fixture.perception.scenarios[2].id });
  assert.deepEqual(perceptionCommand(snapshot, '保洁冲突'), { type: 'scenario', id: scene.id });
  assert.deepEqual(perceptionCommand(snapshot, '批准所有工单'), { type: 'unknown' });
});

test("monitor charts reconcile against the same flights, operations and physical stands", () => {
  const snapshot = fixture as OpsSnapshot, data = apronMonitor(snapshot);
  assert.equal(data.states.reduce((sum, row) => sum + row.count, 0), 27);
  assert.equal(data.stands, 75); assert.equal(data.occupied, 24);
  for (const code of ['taxiing', 'taking_off', 'waiting_hangar']) assert.equal(data.states.find(row => row.code === code)!.count, 1);
  for (const row of data.operations) assert.equal(row.completed + row.active + row.risk + row.planned, 27);
  const t3 = { ...snapshot, flights: snapshot.flights.filter(f => f.terminal === 'T3'), stands: snapshot.stands.filter(s => s.terminal === 'T3') };
  assert.equal(apronMonitor(t3).states.reduce((sum, row) => sum + row.count, 0), 15);
  for (const stand of snapshot.stands) {
    const position = apronMapPosition(stand.code, snapshot);
    assert.ok(parseFloat(position.left) >= 10 && parseFloat(position.left) <= 90);
    assert.ok(parseFloat(position.top) >= 20 && parseFloat(position.top) <= 85);
  }
});

test("compressed preview has an explicit start and finite final state", () => {
  assert.equal(previewProgress(scene, 16000, false), 0);
  assert.equal(previewProgress(scene, 8500, true), 0);
  assert.equal(previewProgress(scene, 12500, true), .5);
  assert.equal(previewProgress(scene, 16500, true), 1);
  assert.equal(previewProgress(scene, 8000, true), 0);
  assert.equal(previewProgress(scene, 60000, true), 1);
});

test("scenario associations use the shared fixture rather than unrelated demonstration flights", () => {
  const snapshot = fixture as OpsSnapshot;
  assert.deepEqual(scenarioFlights(snapshot, scene).map(f => f.flight_no), ['CZ3103', 'HU7134', 'CA1258']);
  const catering = fixture.perception.scenarios[1] as OpsPerceptionScenario;
  assert.deepEqual(scenarioFlights(snapshot, catering).map(f => f.flight_no), ['SC2221']);
  assert.deepEqual(scenarioFlights(snapshot, { ...scene, alert_id: 'unknown' }), []);
});
