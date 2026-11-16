import assert from "node:assert/strict";
import test from "node:test";
import { PerspectiveCamera, Plane, Raycaster, Vector2, Vector3 } from "three";
import { createApronControls, trackMapClick } from "../components/ops/apron-controls.ts";

class Surface extends EventTarget {
  clientWidth = 1000;
  clientHeight = 600;
  ownerDocument = new EventTarget();
  style = { touchAction: '', cursor: '' };
  stand: string | undefined;
  setPointerCapture() {}
  releasePointerCapture() {}
  getBoundingClientRect() { return { left: 50, top: 20, width: 1000, height: 600 }; }
  closest() { return this.stand ? { dataset: { standCode: this.stand } } : null; }
}

function event(type: string, x: number, y: number, button = 0, pointerId = 1) {
  return Object.assign(new Event(type, { cancelable: true }), { clientX: x, clientY: y, pageX: x, pageY: y, button, pointerId, pointerType: 'mouse', deltaY: -200, deltaMode: 0 });
}

function setup() {
  const element = new Surface(), camera = new PerspectiveCamera(42, 1000 / 600, 1, 5500);
  camera.position.set(250, 500, 700);
  const controls = createApronControls(camera, element as unknown as HTMLElement);
  controls.target.set(0, 0, 0); controls.update(); camera.updateMatrixWorld();
  return { element, camera, controls };
}

test('off-centre cursor zoom preserves the pointed ground location and moves the orbit target', () => {
  const { element, camera, controls } = setup();
  const ndc = new Vector2(.6, .3), ray = new Raycaster(); ray.setFromCamera(ndc, camera);
  const anchor = ray.ray.intersectPlane(new Plane(new Vector3(0, 1, 0), 0), new Vector3())!;
  const target = controls.target.clone(), distance = camera.position.distanceTo(target);
  element.dispatchEvent(event('wheel', 850, 230));
  const projected = anchor.clone().project(camera);
  assert.ok(Math.abs(projected.x - ndc.x) < 1e-8 && Math.abs(projected.y - ndc.y) < 1e-8);
  assert.ok(controls.target.distanceTo(target) > 1);
  assert.ok(camera.position.distanceTo(controls.target) < distance);
  assert.equal(controls.target.y, 0);
  const moved = controls.target.clone();
  for (let i = 0; i < 60; i++) controls.update();
  assert.ok(controls.target.distanceTo(moved) < 1e-8);
  controls.dispose();
});

test('left drag translates camera and target together on the apron; right drag rotates around the new target', () => {
  const { element, camera, controls } = setup();
  const before = camera.position.clone(), target = controls.target.clone();
  element.dispatchEvent(event('pointerdown', 700, 320));
  element.ownerDocument.dispatchEvent(event('pointermove', 800, 380));
  element.ownerDocument.dispatchEvent(event('pointerup', 800, 380));
  const shift = controls.target.clone().sub(target);
  assert.ok(shift.length() > 1); assert.equal(controls.target.y, 0);
  assert.ok(camera.position.clone().sub(before).distanceTo(shift) < 1e-8);
  const pivot = controls.target.clone(), position = camera.position.clone();
  element.dispatchEvent(event('pointerdown', 700, 320, 2));
  element.ownerDocument.dispatchEvent(event('pointermove', 800, 360, 2));
  element.ownerDocument.dispatchEvent(event('pointerup', 800, 360, 2));
  assert.ok(controls.target.distanceTo(pivot) < 1e-8);
  assert.ok(camera.position.distanceTo(position) > 1);
  controls.dispose();
});

test('dragging over a stand label, returning to the starting point, right clicks and multiple pointers never select a stand', () => {
  const element = new Surface(); element.stand = '305';
  const selected: string[] = [];
  const cleanup = trackMapClick(element as unknown as HTMLElement, (_, stand) => selected.push(stand!));
  const down = (x: number, button = 0, id = 1) => element.dispatchEvent(event('pointerdown', x, 300, button, id));
  const up = (x: number, button = 0, id = 1) => element.ownerDocument.dispatchEvent(event('pointerup', x, 300, button, id));
  down(700); element.ownerDocument.dispatchEvent(event('pointermove', 800, 300)); up(700);
  down(700); up(800);
  down(700, 2); up(700, 2);
  down(700, 0, 1); down(710, 0, 2); up(700, 0, 1); up(710, 0, 2);
  assert.deepEqual(selected, []);
  down(700); up(700); assert.deepEqual(selected, ['305']);
  down(700); element.dispatchEvent(event('pointercancel', 700, 300)); up(700);
  assert.deepEqual(selected, ['305']);
  cleanup(); down(700); up(700); assert.deepEqual(selected, ['305']);
});
