import assert from "node:assert/strict";
import test from "node:test";
import * as THREE from "three";
import { readFileSync } from "node:fs";
import { AIRPORT_SCALE, airportCameraView, apronStandLayout, buildAirport, createAircraft, createJetBridge, createVehicle } from "../components/ops/apron-models.ts";
import { frontRoadDecks, frontRoadLayers, frontRoadPaths, frontRoadPiers } from "../components/ops/apron-network-v7.ts";

test("airport models contain finite geometry and aircraft fit the illustrative stand spacing", () => {
  for (const model of [buildAirport(), createAircraft(0x168fc0), ...(['van', 'fuel', 'catering', 'baggage'] as const).map(kind => createVehicle(0x43d4b2, kind))]) {
    const geometries = new Set<THREE.BufferGeometry>(), materials = new Set<THREE.Material>();
    model.traverse(object => {
      const mesh = object as THREE.Mesh;
      if (mesh.geometry) {
        geometries.add(mesh.geometry);
        for (const value of mesh.geometry.getAttribute('position').array) assert.ok(Number.isFinite(value));
      }
      if (mesh.material) for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) materials.add(material);
    });
    assert.ok(geometries.size > 5);
    const bounds = new THREE.Box3().setFromObject(model); assert.ok(!bounds.isEmpty());
    geometries.forEach(value => value.dispose()); materials.forEach(value => value.dispose());
  }
  const aircraft = new THREE.Box3().setFromObject(createAircraft(0x168fc0)).getSize(new THREE.Vector3());
  assert.ok(aircraft.x > 30 && aircraft.x < 44);
  assert.ok(aircraft.z > 30 && aircraft.z < 46);
  assert.ok(aircraft.y > 6 && aircraft.y < 13);
});

test("default daylight framing keeps both terminals' physical stands above the middle monitoring dock", () => {
  const data = JSON.parse(readFileSync(new URL('../../data/ops-test-data.json', import.meta.url), 'utf8'));
  for (const [width, height, left, right] of [[1600, 688, 330, 1230], [1280, 430, 306, 934]]) {
    const camera = new THREE.PerspectiveCamera(42, width / height, 1, 5500), preset = airportCameraView('overview', height);
    camera.position.fromArray(preset.position);
    camera.lookAt(new THREE.Vector3(...preset.target)); camera.updateMatrixWorld();
    for (const stand of data.stands.filter((s: { kind?: string }) => !s.kind)) {
      const point = new THREE.Vector3(stand.scene.x, 0, stand.scene.z).project(camera);
      const x = (point.x + 1) * width / 2, y = (1 - point.y) * height / 2;
      assert.ok(x > left && x < right, `${width}: ${stand.code} outside side panels, x=${x}`);
      assert.ok(y > 65 && y < height - 197, `${width}: ${stand.code} outside map area, y=${y}`);
    }
  }
});

test("pure 3D terminal retains courtyard openings and fixture bridges align with the supplied layout", () => {
  const airport = buildAirport(), t3 = airport.getObjectByName('T3')!;
  assert.ok(t3); assert.ok(airport.getObjectByName('T2')); assert.ok(airport.getObjectByName('GTC / ATC'));
  assert.ok(airport.getObjectByName('Airfield / runways')); assert.ok(airport.getObjectByName('Cargo / maintenance / S19'));
  const base = t3.getObjectByName('T3 courtyard roof') as THREE.Mesh<THREE.ExtrudeGeometry>;
  const outline = base.geometry.parameters.shapes as THREE.Shape;
  assert.equal(outline.holes.length, 2);
  const data = JSON.parse(readFileSync(new URL('../../data/ops-test-data.json', import.meta.url), 'utf8'));
  for (const stand of data.stands.filter((s: { kind?: string }) => !s.kind)) {
    const spec = apronStandLayout.find(s => s.ref === stand.scene.layout_ref)!;
    assert.ok(spec);
    assert.equal(stand.scene.x, spec.x * AIRPORT_SCALE); assert.equal(stand.scene.z, spec.z * AIRPORT_SCALE);
    assert.equal(stand.scene.bridge_x, spec.rootX * AIRPORT_SCALE); assert.equal(stand.scene.bridge_z, spec.rootZ * AIRPORT_SCALE);
    assert.ok(Math.abs(stand.scene.heading - spec.rotY * 180 / Math.PI) < .000001);
    const point = new THREE.Vector3(-2, 0, -12).applyAxisAngle(new THREE.Vector3(0, 1, 0), stand.scene.heading * Math.PI / 180).add(new THREE.Vector3(stand.scene.x, 0, stand.scene.z));
    const bridge = createJetBridge(stand.scene.bridge_x, stand.scene.bridge_z, point.x, point.z);
    const bounds = new THREE.Box3().setFromObject(bridge);
    assert.ok(bounds.containsPoint(new THREE.Vector3(stand.scene.bridge_x, 3, stand.scene.bridge_z)));
    assert.ok(bounds.containsPoint(new THREE.Vector3(point.x, 5.2, point.z)));
    bridge.traverse(object => { const mesh = object as THREE.Mesh; mesh.geometry?.dispose(); if (mesh.material) for (const m of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) m.dispose(); });
  }
  airport.traverse(object => { const mesh = object as THREE.Mesh; if (mesh instanceof THREE.InstancedMesh) mesh.dispose(); mesh.geometry?.dispose(); if (mesh.material) for (const m of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) m.dispose(); });
});

test("every supplied demo stand and its original padded number is registered exactly once", () => {
  const html = readFileSync(new URL('../../artifacts/design/reference-map-network-v7.html', import.meta.url), 'utf8');
  const numbers = [...html.matchAll(/\{ ref: '(\d+)'/g)].map(match => match[1]);
  const data = JSON.parse(readFileSync(new URL('../../data/ops-test-data.json', import.meta.url), 'utf8'));
  const physical = data.stands.filter((s: { kind?: string }) => !s.kind);
  assert.equal(numbers.length, 75); assert.equal(new Set(numbers).size, 75);
  assert.deepEqual(new Set(apronStandLayout.map(s => s.ref)), new Set(numbers));
  assert.deepEqual(new Set(physical.map((s: { code: string }) => s.code)), new Set(numbers));
  assert.equal(physical.length, 75);
  assert.equal(physical.filter((s: { terminal: string }) => s.terminal === 'T3').length, 55);
  assert.equal(physical.filter((s: { terminal: string }) => s.terminal === 'T2').length, 20);
  assert.equal(physical.reduce((n: number, s: { scene: { dual: boolean } }) => n + (s.scene.dual ? 2 : 1), 0), 81);
  for (const stand of physical) assert.equal(stand.code, stand.scene.layout_ref);
});

test("v7 road contours, elevated decks and tunnel portals retain the supplied network", () => {
  const html = readFileSync(new URL('../../artifacts/design/reference-map-network-v7.html', import.meta.url), 'utf8');
  assert.deepEqual(frontRoadLayers, JSON.parse(html.match(/const FRONT_LAYERS = (.*);/)![1]));
  assert.deepEqual(frontRoadDecks, JSON.parse(html.match(/const FRONT_DECK = (.*);/)![1]));
  assert.deepEqual(frontRoadPiers, JSON.parse(html.match(/const FRONT_PIERS = (.*);/)![1]));
  assert.deepEqual(frontRoadPaths, JSON.parse(html.match(/const FRONT_PATHS = (.*);/)![1]));
  assert.equal(frontRoadLayers.flatMap(layer => layer.p).length, 35);
  assert.equal(frontRoadLayers.flatMap(layer => layer.p).reduce((n, rings) => n + rings.length - 1, 0), 15);
  assert.equal(frontRoadDecks.length, 8); assert.equal(frontRoadPiers.length, 19);
  assert.equal(frontRoadPaths.length, 20);
  const airport = buildAirport(), front = airport.getObjectByName('Front road network v7')!;
  assert.equal(front.children.length, 4);
  assert.deepEqual(new Set(front.children.map(object => ((object as THREE.Mesh).material as THREE.MeshStandardMaterial).color.getHex())), new Set(frontRoadLayers.map(layer => layer.c)));
  const deck = airport.getObjectByName('Front elevated roads v7') as THREE.Mesh<THREE.BufferGeometry, THREE.MeshStandardMaterial>;
  const positions = deck.geometry.getAttribute('position');
  assert.equal(positions.count, deck.geometry.getAttribute('color').count);
  assert.equal(deck.material.vertexColors, true);
  assert.ok(deck.castShadow && deck.receiveShadow);
  const heights = Array.from({ length: positions.count }, (_, i) => positions.getY(i));
  assert.ok(heights.some(y => Math.abs(y - 8.69) < .0001), 'T3 departure deck must rise above ground');
  assert.ok(heights.some(y => Math.abs(y - 4.69) < .0001), 'T2 deck must retain its separate elevation');
  assert.ok(heights.every(y => y >= -.061 && y <= 9.141), 'deck surface, underside and rails must remain within source heights');
  const piers = airport.getObjectByName('Front viaduct piers')!;
  assert.equal(piers.children.length, 1, 'repeated bridge piers should be batched');
  const pierVertices = (piers.children[0] as THREE.Mesh).geometry.getAttribute('position');
  const cylinder = new THREE.CylinderGeometry(.9, 1.1, 1, 12);
  assert.equal(pierVertices.count, cylinder.getAttribute('position').count * 19); cylinder.dispose();
  for (const [name, start, end] of [
    ['West under-apron tunnel', [-95.2, .6, 20.9], [-49.8, .6, -266.9]],
    ['East under-apron tunnel', [103.4, .6, 10], [107.1, .6, -221.1]],
  ] as const) {
    const strip = airport.getObjectByName(name) as THREE.Mesh<THREE.BufferGeometry, THREE.MeshBasicMaterial>;
    const vertices = strip.geometry.getAttribute('position');
    for (const [index, point] of [[0, start], [vertices.count - 2, end]] as const) {
      const midpoint = new THREE.Vector3().fromBufferAttribute(vertices, index).add(new THREE.Vector3().fromBufferAttribute(vertices, index + 1)).multiplyScalar(.5);
      assert.ok(midpoint.distanceTo(new THREE.Vector3(...point)) < .0001);
    }
    assert.equal(strip.renderOrder, 5); assert.equal(strip.material.opacity, .62); assert.equal(strip.material.depthWrite, false);
    assert.equal(airport.getObjectByName(name + ' edges')!.renderOrder, 6);
  }
  const portals: number[][] = [];
  airport.traverse(object => { if (object.name === 'Tunnel portal') portals.push(object.position.toArray()); });
  assert.deepEqual(portals, [[-95.2, .3, 20.9], [-49.8, .3, -266.9], [103.4, .3, 10], [107.1, .3, -221.1]]);
  airport.traverse(object => { const mesh = object as THREE.Mesh; if (mesh instanceof THREE.InstancedMesh) mesh.dispose(); mesh.geometry?.dispose(); if (mesh.material) for (const m of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) m.dispose(); });
});

test("whole-airport road view includes northern cargo and southern access roads above the monitoring dock", () => {
  for (const [width, height] of [[1600, 688], [1280, 430]]) {
    const camera = new THREE.PerspectiveCamera(42, width / height, 2, 26000), preset = airportCameraView('macro', height);
    camera.position.fromArray(preset.position); camera.lookAt(new THREE.Vector3(...preset.target)); camera.updateMatrixWorld();
    assert.ok(camera.position.distanceTo(new THREE.Vector3(...preset.target)) < 22000);
    for (const [x, z] of [[0, -2160], [0, 1860], [-1095, -1560], [1125, 1470]]) {
      const point = new THREE.Vector3(x, 0, z).project(camera), y = (1 - point.y) * height / 2;
      assert.ok(point.z > -1 && point.z < 1);
      assert.ok(Math.abs(point.x) < .45);
      assert.ok(y > 65 && y < height - 197, `${width}: (${x},${z}) projects to ${y}`);
    }
  }
});

test("traffic tracks produce finite positions and headings, with takeoff leaving the ground", () => {
  const data = JSON.parse(readFileSync(new URL('../../data/ops-test-data.json', import.meta.url), 'utf8'));
  const moving = data.flights.filter((f: { motion?: unknown }) => f.motion);
  assert.equal(moving.length, 2);
  for (const flight of moving) {
    const path = new THREE.CurvePath<THREE.Vector3>(), points = flight.motion.path.map((p: number[]) => new THREE.Vector3(p[0], p[1], p[2]));
    for (let i = 1; i < points.length; i++) path.add(new THREE.LineCurve3(points[i - 1], points[i]));
    for (const fraction of [0, .2, .5, .8, 1]) {
      assert.ok(path.getPointAt(fraction).toArray().every(Number.isFinite));
      assert.ok(path.getTangentAt(fraction).toArray().every(Number.isFinite));
    }
    if (flight.aircraft_state.code === 'taking_off') assert.ok(path.getPointAt(1).y > 100);
    else assert.equal(path.getPointAt(.5).y, 0);
  }
});

test("pure 3D road traffic animates on its network without rebuilding the airport", () => {
  const airport = buildAirport(), before = new Map<THREE.Object3D, THREE.Vector3>();
  airport.animateTraffic(0);
  airport.traverse(object => before.set(object, object.position.clone()));
  airport.animateTraffic(7);
  let moved = 0, meshes = 0, onDepartureDeck = false;
  airport.traverse(object => {
    assert.ok(object.position.toArray().every(Number.isFinite));
    if (!object.position.equals(before.get(object)!)) {
      moved++;
      const { x, y, z } = object.position;
      if (Math.abs(x) < 90 && z > 0 && z < 70 && y > 8.7) onDepartureDeck = true;
    }
    const mesh = object as THREE.Mesh;
    if (mesh.geometry) {
      meshes++;
      for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
        assert.ok(!Object.values(material).some(value => value instanceof THREE.Texture), 'map geometry must not depend on image textures');
      }
    }
  });
  assert.equal(moved, 57); // 56 road cars and one intercity train; operational planes live in the snapshot scene.
  assert.ok(onDepartureDeck, 'traffic on the T3 departure deck must follow its elevated surface');
  assert.ok(meshes < 260, `static parts should be batched, got ${meshes} meshes`);
  airport.animateTraffic(7);
  const atSeven = new Map<THREE.Object3D, THREE.Vector3>(); airport.traverse(object => atSeven.set(object, object.position.clone()));
  airport.animateTraffic(7); airport.traverse(object => assert.ok(object.position.equals(atSeven.get(object)!)));
  airport.traverse(object => { const mesh = object as THREE.Mesh; if (mesh instanceof THREE.InstancedMesh) mesh.dispose(); mesh.geometry?.dispose(); if (mesh.material) for (const m of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) m.dispose(); });
});
