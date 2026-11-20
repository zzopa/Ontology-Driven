import * as THREE from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import { TessellateModifier } from "three/addons/modifiers/TessellateModifier.js";
import { frontRoadDecks, frontRoadLayers, frontRoadPaths, frontRoadPiers } from "./apron-network-v7.ts";
import { clearTrafficPoint, faceTravel, trafficSections } from "./apron-traffic.ts";

const material = (color: number, metalness = .25, roughness = .45) => new THREE.MeshStandardMaterial({ color, metalness, roughness });

export function createAircraft(color: number) {
  const root = new THREE.Group();
  const white = material(0xf1f5f9, .35, .28), livery = material(color), glass = material(0x162e48, .65, .18), rubber = material(0x101722);
  function part(geometry: THREE.BufferGeometry, mat: THREE.Material, x: number, y: number, z: number) {
    const mesh = new THREE.Mesh(geometry, mat); mesh.position.set(x, y, z); mesh.castShadow = true; root.add(mesh); return mesh;
  }
  const profile = [[0, -18], [.9, -17], [1.65, -14], [1.9, -11], [1.9, 8], [1.5, 12], [.4, 16], [0, 17]];
  const body = part(new THREE.LatheGeometry(profile.map(([radius, z]) => new THREE.Vector2(radius, z)), 24), white, 0, 4.2, 0);
  body.rotation.x = Math.PI / 2;
  // Swept wing silhouettes remain legible from the full-airport camera.
  function wing(points: number[][], y: number, z: number, mat = white) {
    const shape = new THREE.Shape(points.map(([x, pz]) => new THREE.Vector2(x, pz)));
    const mesh = part(new THREE.ExtrudeGeometry(shape, { depth: .35, bevelEnabled: false }), mat, 0, y, z);
    mesh.rotation.x = Math.PI / 2; return mesh;
  }
  for (const side of [-1, 1]) {
    wing([[side * 1.4, -4], [side * 15.8, 5.4], [side * 16.4, 8], [side * 2, 3.5]], 4, 0);
    wing([[side * .8, -1], [side * 6.5, 3], [side * 6.5, 4.2], [side * 1, 2.5]], 5, 11);
    const engine = part(new THREE.CylinderGeometry(1.15, 1, 5, 16), white, side * 6.5, 2.7, -.5); engine.rotation.x = Math.PI / 2;
    const intake = part(new THREE.CylinderGeometry(.88, .88, .15, 16), glass, side * 6.5, 2.7, -3.05); intake.rotation.x = Math.PI / 2;
    part(new THREE.SphereGeometry(.3, 10, 8), livery, side * 6.5, 2.7, -3.15);
    part(new THREE.BoxGeometry(.3, 2.3, .3), material(0x6e7f8f), side * 2, 1.6, 4);
    const wheel = part(new THREE.CylinderGeometry(.65, .65, .65, 10), rubber, side * 2.2, .7, 4); wheel.rotation.z = Math.PI / 2;
    const tip = part(new THREE.BoxGeometry(.3, 1.8, 1.2), livery, side * 16, 4.7, 7); tip.rotation.z = side * -.25;
  }
  const fin = new THREE.Shape([new THREE.Vector2(10, 4.7), new THREE.Vector2(13.2, 10), new THREE.Vector2(16.1, 9.6), new THREE.Vector2(16, 4.7)]);
  part(new THREE.ExtrudeGeometry(fin, { depth: .5, bevelEnabled: false }), livery, -.25, 0, 0).rotation.y = -Math.PI / 2;
  const cockpit = part(new THREE.SphereGeometry(1, 12, 8), glass, 0, 5, -14.8); cockpit.scale.set(1.2, .55, 1.4);
  const windows = new THREE.InstancedMesh(new THREE.SphereGeometry(.16, 6, 5), glass, 36);
  const matrix = new THREE.Matrix4(); let index = 0;
  for (const side of [-1, 1]) for (let z = -10; z <= 10; z += 1.2) { matrix.makeTranslation(side * 1.86, 4.9, z); windows.setMatrixAt(index++, matrix); }
  windows.count = index; root.add(windows);
  part(new THREE.BoxGeometry(.25, 1.8, .3), rubber, 0, 1.5, -11);
  const noseWheel = part(new THREE.CylinderGeometry(.5, .5, .7, 10), rubber, 0, .6, -11); noseWheel.rotation.z = Math.PI / 2;
  root.scale.set(1.25, 1.1, 1.25);
  return root;
}

export function createVehicle(color: number, kind: "van" | "fuel" | "catering" | "baggage" = "van") {
  const root = new THREE.Group(), paint = material(color), silver = material(0x97adbc, .6), dark = material(0x102333), tire = material(0x11161f);
  function box(w: number, h: number, d: number, x: number, y: number, z: number, mat = paint) {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat); mesh.position.set(x, y, z); mesh.castShadow = true; root.add(mesh); return mesh;
  }
  box(2.8, .7, 7, 0, 1.1, 0, dark); box(2.7, 2.3, 2.4, 0, 2.2, -2.3);
  box(2.4, .9, .05, 0, 2.7, -3.52, dark); box(.05, .9, 1.3, 1.37, 2.7, -2.4, dark); box(.05, .9, 1.3, -1.37, 2.7, -2.4, dark);
  for (const x of [-1.45, 1.45]) for (const z of [-2.2, 2.3]) {
    const wheel = new THREE.Mesh(new THREE.CylinderGeometry(.65, .65, .4, 10), tire); wheel.rotation.z = Math.PI / 2; wheel.position.set(x, .7, z); root.add(wheel);
  }
  if (kind === 'fuel') {
    const tank = new THREE.Mesh(new THREE.CylinderGeometry(1.35, 1.35, 4.5, 16), silver); tank.rotation.x = Math.PI / 2; tank.position.set(0, 2.6, 1); root.add(tank);
    box(.15, .8, 3.5, 1.34, 2.6, 1);
  } else if (kind === 'catering') {
    box(2.9, 3.8, 4.8, 0, 4.2, 1); box(2.6, .15, 1.4, 0, 2.5, -2.2, silver);
    for (const x of [-1, 1]) box(.18, 3, .18, x, 2.2, 1, silver).rotation.x = .5;
  } else if (kind === 'baggage') {
    box(2.2, .6, 8, 0, 2.3, 1, silver).rotation.x = -.2;
    box(1.8, .15, 7.8, 0, 2.7, 1, dark).rotation.x = -.2;
  } else box(2.7, 2.8, 4.6, 0, 2.5, 1);
  box(.45, .3, .1, -.8, 1.7, -3.55, material(0xffefbc)); box(.45, .3, .1, .8, 1.7, -3.55, material(0xffefbc));
  box(.35, .23, .12, -.94, 1.55, 3.52, material(0xe34e42)); box(.35, .23, .12, .94, 1.55, 3.52, material(0xe34e42));
  return root;
}

function roadVehicleGeometry(paint: number, bus: boolean) {
  const parts: THREE.BufferGeometry[] = [];
  function part(geometry: THREE.BufferGeometry, color: number, x: number, y: number, z: number) {
    const unindexed = geometry.toNonIndexed().translate(x, y, z), rgb = new THREE.Color(color);
    const colors = new Float32Array(unindexed.getAttribute('position').count * 3);
    for (let i = 0; i < colors.length; i += 3) rgb.toArray(colors, i);
    unindexed.setAttribute('color', new THREE.BufferAttribute(colors, 3)); parts.push(unindexed); geometry.dispose();
  }
  const size = bus ? 3.8 : 2.54, cabin = bus ? 1.1 : .56;
  part(new THREE.BoxGeometry(1.25, .34, size), paint, 0, .39, 0);
  part(new THREE.BoxGeometry(1.12, cabin, bus ? 3.66 : 1.32), 0x334757, 0, .54 + cabin / 2, bus ? 0 : .18);
  part(new THREE.BoxGeometry(1.18, .12, bus ? 3.7 : 1.36), paint, 0, .55 + cabin, bus ? 0 : .18);
  for (const side of [-1, 1]) {
    for (const z of [-size * .32, size * .34]) part(new THREE.CylinderGeometry(.23, .23, .16, 8).rotateZ(Math.PI / 2), 0x20282e, side * .61, .23, z);
    part(new THREE.BoxGeometry(.24, .12, .06), 0xffeed0, side * .42, .47, -size / 2);
    part(new THREE.BoxGeometry(.2, .12, .06), 0xc2342b, side * .43, .46, size / 2);
  }
  const geometry = mergeGeometries(parts)!; parts.forEach(part => part.dispose());
  return geometry;
}

// Geometry adapted from the supplied code_artifact_network_v7.html; illustrative, not surveyed.
export const AIRPORT_SCALE = 3;
export const airportViews: Record<'overview' | 'map' | 'terminal' | 't3' | 'macro', { position: [number, number, number]; target: [number, number, number] }> = {
  overview: { position: [0, 1550, 1500], target: [0, 0, 350] },
  map: { position: [0, 2400, 350.1], target: [0, 0, 350] },
  terminal: { position: [-650, 520, 930], target: [-450, 0, 465] },
  t3: { position: [60, 720, 760], target: [0, 0, -105] },
  macro: { position: [0, 8000, 4050], target: [0, 0, 550] },
};

export function airportCameraView(view: keyof typeof airportViews, height: number) {
  const preset = airportViews[view];
  if (height >= 688 || !['overview', 'map', 'macro'].includes(view)) return preset;
  // Keep the two terminals above the fixed monitoring dock in short desktop windows.
  const h = Math.max(360, height), scale = (h / (h - 265)) / (688 / 423);
  const target: [number, number, number] = [...preset.target];
  const centerZ = view === 'macro' ? -150 : 150;
  target[2] = centerZ + (preset.target[2] - centerZ) * scale * 688 / h;
  const position = preset.position.map((value, i) => target[i] + (value - preset.target[i]) * scale) as [number, number, number];
  return { position, target };
}
const referenceStands = [
    { ref: '01', zone: 'T3 西南指廊南侧', gx: -120, gz: 22.2, px: -120, pz: 43,   rot: 0,               bridges: 1, size: 0.84 },
    { ref: '02', zone: 'T3 西南指廊南侧', gx: -140, gz: 22.2, px: -140, pz: 43,   rot: 0,               bridges: 1, size: 0.85 },
    { ref: '03', zone: 'T3 西南指廊南侧', gx: -160, gz: 22.2, px: -160, pz: 43,   rot: 0,               bridges: 1, size: 0.85 },
    { ref: '04', zone: 'T3 西南指廊南侧', gx: -180, gz: 22.2, px: -180, pz: 43,   rot: 0,               bridges: 1, size: 0.84 },
    { ref: '06', zone: 'T3 西南指廊西端头', gx: -198, gz: 21.0, px: -216, pz: 35,   rot: -Math.PI * 0.28, bridges: 1, size: 0.82 },
    { ref: '07', zone: 'T3 西南指廊西端头', gx: -201, gz: 15.0, px: -221, pz: 16,   rot: -Math.PI * 0.48, bridges: 1, size: 0.82 },
    { ref: '08', zone: 'T3 西南指廊西端头', gx: -198, gz: 9.0,  px: -216, pz: -4,   rot: -Math.PI * 0.72, bridges: 1, size: 0.82 },
    { ref: '09', zone: 'T3 西南指廊北侧端头', gx: -186, gz: 7.8, px: -192, pz: -13,  rot: Math.PI,         bridges: 1, size: 0.82 },
    { ref: '10', zone: 'T3 西南指廊北侧', gx: -172, gz: 7.8,  px: -172, pz: -13,  rot: Math.PI,         bridges: 1, size: 0.85 },
    { ref: '11', zone: 'T3 西南指廊北侧', gx: -154, gz: 7.8,  px: -154, pz: -13,  rot: Math.PI,         bridges: 1, size: 0.86 },
    { ref: '12', zone: 'T3 西南指廊北侧', gx: -136, gz: 7.8,  px: -136, pz: -13,  rot: Math.PI,         bridges: 1, size: 0.85 },
    { ref: '13', zone: 'T3 西南指廊北侧', gx: -118, gz: 7.8,  px: -118, pz: -13,  rot: Math.PI,         bridges: 1, size: 0.84 },
    { ref: '14', zone: 'T3 西港湾内壁',   gx: -97,  gz: -36,  px: -119, pz: -36,  rot: -Math.PI / 2,    bridges: 1, size: 0.85 },

    { ref: '15', zone: 'T3 西北指廊南侧', gx: -122, gz: -81.2, px: -122, pz: -61,  rot: 0,               bridges: 1, size: 0.85 },
    { ref: '16', zone: 'T3 西北指廊南侧', gx: -142, gz: -81.2, px: -142, pz: -61,  rot: 0,               bridges: 1, size: 0.86 },
    { ref: '17', zone: 'T3 西北指廊南侧', gx: -162, gz: -81.2, px: -162, pz: -61,  rot: 0,               bridges: 1, size: 0.86 },
    { ref: '18', zone: 'T3 西北指廊南侧', gx: -182, gz: -81.2, px: -182, pz: -61,  rot: 0,               bridges: 1, size: 0.85 },
    { ref: '19', zone: 'T3 西北指廊端头', gx: -197, gz: -83.5, px: -215, pz: -71,  rot: -Math.PI * 0.28, bridges: 1, size: 0.85 },
    { ref: '21', zone: 'T3 西北指廊端头', gx: -200, gz: -91.5, px: -220, pz: -91.5,rot: -Math.PI * 0.50, bridges: 1, size: 0.86 },
    { ref: '22', zone: 'T3 西北指廊端头', gx: -197, gz: -99.5, px: -215, pz: -112, rot: -Math.PI * 0.72, bridges: 1, size: 0.86 },
    { ref: '24', zone: 'T3 西北指廊北端', gx: -182, gz: -102.5,px: -182, pz: -124, rot: Math.PI,         bridges: 1, size: 0.90 },
    { ref: '25', zone: 'T3 西北指廊北侧', gx: -158, gz: -102.5,px: -158, pz: -124, rot: Math.PI,         bridges: 1, size: 0.92 },
    { ref: '26', zone: 'T3 西北指廊北侧', gx: -135, gz: -102.5,px: -135, pz: -125, rot: Math.PI,         bridges: 2, size: 1.04 },
    { ref: '27', zone: 'T3 西北指廊北侧', gx: -112, gz: -102.5,px: -112, pz: -124, rot: Math.PI,         bridges: 1, size: 0.90 },
    { ref: '28', zone: 'T3 主楼北侧西段', gx: -86,  gz: -103.5,px: -86,  pz: -126, rot: Math.PI,         bridges: 2, size: 1.08 },
    { ref: '29', zone: 'T3 塔台西侧近机位', gx: -62, gz: -104.5,px: -62, pz: -126, rot: Math.PI,         bridges: 1, size: 0.90 },
    { ref: '30', zone: 'T3 塔台西侧宽体位', gx: -40, gz: -105.0,px: -40, pz: -127, rot: Math.PI,         bridges: 2, size: 1.10 },

    { ref: '31', zone: 'T3 塔台东侧主楼', gx: 12,  gz: -105.0, px: 12,  pz: -126, rot: Math.PI,         bridges: 1, size: 0.92 },
    { ref: '32', zone: 'T3 主楼北侧东段', gx: 36,  gz: -104.8, px: 36,  pz: -126, rot: Math.PI,         bridges: 1, size: 0.92 },
    { ref: '33', zone: 'T3 主楼北侧宽体位', gx: 62, gz: -104.2, px: 62,  pz: -127, rot: Math.PI,         bridges: 2, size: 1.12 },
    { ref: '34', zone: 'T3 主楼北侧东段', gx: 88,  gz: -103.5, px: 88,  pz: -125, rot: Math.PI,         bridges: 1, size: 0.92 },
    { ref: '35', zone: 'T3 东北指廊北侧', gx: 112, gz: -102.8, px: 112, pz: -125, rot: Math.PI,         bridges: 2, size: 1.05 },
    { ref: '36', zone: 'T3 东北指廊北侧', gx: 134, gz: -102.5, px: 134, pz: -124, rot: Math.PI,         bridges: 1, size: 0.92 },
    { ref: '37', zone: 'T3 东北指廊北侧', gx: 155, gz: -102.5, px: 155, pz: -125, rot: Math.PI,         bridges: 2, size: 1.06 },
    { ref: '38', zone: 'T3 东北指廊北侧', gx: 175, gz: -102.5, px: 175, pz: -124, rot: Math.PI,         bridges: 1, size: 0.90 },
    { ref: '39', zone: 'T3 东北指廊北端', gx: 191, gz: -102.2, px: 197, pz: -121, rot: Math.PI * 0.9,   bridges: 1, size: 0.88 },
    { ref: '40', zone: 'T3 东北指廊东端头', gx: 198, gz: -98.5, px: 216, pz: -110, rot: Math.PI * 0.72,  bridges: 1, size: 0.86 },
    { ref: '41', zone: 'T3 东北指廊东端头', gx: 200, gz: -91.0, px: 220, pz: -91,  rot: Math.PI * 0.50,  bridges: 1, size: 0.85 },
    { ref: '43', zone: 'T3 东北指廊东端头', gx: 198, gz: -83.5, px: 216, pz: -71,  rot: Math.PI * 0.28,  bridges: 1, size: 0.85 },
    { ref: '45', zone: 'T3 东北指廊南侧', gx: 182, gz: -81.2, px: 182, pz: -61,  rot: 0,               bridges: 1, size: 0.85 },
    { ref: '46', zone: 'T3 东北指廊南侧', gx: 162, gz: -81.2, px: 162, pz: -61,  rot: 0,               bridges: 1, size: 0.85 },
    { ref: '47', zone: 'T3 东北指廊南侧', gx: 142, gz: -81.2, px: 142, pz: -61,  rot: 0,               bridges: 1, size: 0.85 },
    { ref: '48', zone: 'T3 东北指廊南侧', gx: 122, gz: -81.2, px: 122, pz: -61,  rot: 0,               bridges: 1, size: 0.85 },
    { ref: '49', zone: 'T3 东港湾内壁',   gx: 97,  gz: -36,   px: 119, pz: -36,  rot: Math.PI / 2,     bridges: 1, size: 0.85 },

    { ref: '50', zone: 'T3 东南指廊北侧', gx: 124, gz: 15.8,  px: 124, pz: -5,   rot: Math.PI,         bridges: 1, size: 0.85 },
    { ref: '51', zone: 'T3 东南指廊北侧', gx: 144, gz: 15.8,  px: 144, pz: -5,   rot: Math.PI,         bridges: 1, size: 0.85 },
    { ref: '52', zone: 'T3 东南指廊北侧', gx: 164, gz: 15.8,  px: 164, pz: -5,   rot: Math.PI,         bridges: 1, size: 0.85 },
    { ref: '53', zone: 'T3 东南指廊北侧', gx: 184, gz: 15.8,  px: 184, pz: -5,   rot: Math.PI,         bridges: 1, size: 0.85 },
    { ref: '55', zone: 'T3 东南指廊东端头', gx: 199, gz: 17.5, px: 217, pz: 6,    rot: Math.PI * 0.70,  bridges: 1, size: 0.84 },
    { ref: '56', zone: 'T3 东南指廊东端头', gx: 199, gz: 28.5, px: 217, pz: 40,   rot: Math.PI * 0.30,  bridges: 1, size: 0.84 },
    { ref: '58', zone: 'T3 东南指廊南侧', gx: 186, gz: 30.2,  px: 186, pz: 51,   rot: 0,               bridges: 1, size: 0.85 },
    { ref: '59', zone: 'T3 东南指廊南侧', gx: 168, gz: 30.2,  px: 168, pz: 51,   rot: 0,               bridges: 1, size: 0.85 },
    { ref: '60', zone: 'T3 东南指廊南侧', gx: 150, gz: 30.2,  px: 150, pz: 51,   rot: 0,               bridges: 1, size: 0.86 },
    { ref: '61', zone: 'T3 东南指廊南侧', gx: 132, gz: 30.2,  px: 132, pz: 51,   rot: 0,               bridges: 1, size: 0.85 },
    { ref: '62', zone: 'T3 东南指廊根部', gx: 114, gz: 30.2,  px: 114, pz: 51,   rot: 0,               bridges: 1, size: 0.84 },

    { ref: '201', zone: 'T2 北指廊北侧根部', gx: -136, gz: 101.2, px: -136, pz: 81,  rot: Math.PI, bridges: 1, size: 0.80 },
    { ref: '202', zone: 'T2 北指廊北侧',     gx: -153, gz: 101.2, px: -153, pz: 81,  rot: Math.PI, bridges: 1, size: 0.80 },
    { ref: '203', zone: 'T2 北指廊北侧',     gx: -170, gz: 101.2, px: -170, pz: 81,  rot: Math.PI, bridges: 1, size: 0.80 },
    { ref: '204', zone: 'T2 北指廊北侧',     gx: -187, gz: 101.2, px: -187, pz: 81,  rot: Math.PI, bridges: 1, size: 0.80 },
    { ref: '205', zone: 'T2 北指廊北侧西端', gx: -204, gz: 101.2, px: -204, pz: 81,  rot: Math.PI, bridges: 1, size: 0.72 },
    { ref: '206', zone: 'T2 北指廊南侧西端', gx: -204, gz: 114.8, px: -204, pz: 135, rot: 0,       bridges: 1, size: 0.80 },
    { ref: '207', zone: 'T2 北指廊南侧',     gx: -187, gz: 114.8, px: -187, pz: 135, rot: 0,       bridges: 1, size: 0.80 },
    { ref: '208', zone: 'T2 北指廊南侧',     gx: -170, gz: 114.8, px: -170, pz: 135, rot: 0,       bridges: 1, size: 0.80 },
    { ref: '209', zone: 'T2 北指廊南侧',     gx: -153, gz: 114.8, px: -153, pz: 135, rot: 0,       bridges: 1, size: 0.80 },
    { ref: '210', zone: 'T2 北指廊南侧根部', gx: -136, gz: 114.8, px: -136, pz: 135, rot: 0,       bridges: 1, size: 0.80 },

    { ref: '211', zone: 'T2 南指廊北侧根部', gx: -136, gz: 193.2, px: -136, pz: 173, rot: Math.PI, bridges: 1, size: 0.82 },
    { ref: '212', zone: 'T2 南指廊北侧',     gx: -153, gz: 193.2, px: -153, pz: 173, rot: Math.PI, bridges: 1, size: 0.82 },
    { ref: '213', zone: 'T2 南指廊北侧',     gx: -170, gz: 193.2, px: -170, pz: 173, rot: Math.PI, bridges: 1, size: 0.82 },
    { ref: '214', zone: 'T2 南指廊北侧',     gx: -187, gz: 193.2, px: -187, pz: 173, rot: Math.PI, bridges: 1, size: 0.80 },
    { ref: '215', zone: 'T2 南指廊北侧西端', gx: -204, gz: 193.2, px: -204, pz: 173, rot: Math.PI, bridges: 1, size: 0.80 },
    { ref: '216', zone: 'T2 南指廊南侧西端', gx: -204, gz: 206.8, px: -204, pz: 227, rot: 0,       bridges: 1, size: 0.80 },
    { ref: '217', zone: 'T2 南指廊南侧',     gx: -187, gz: 206.8, px: -187, pz: 227, rot: 0,       bridges: 1, size: 0.80 },
    { ref: '218', zone: 'T2 南指廊南侧',     gx: -170, gz: 206.8, px: -170, pz: 227, rot: 0,       bridges: 1, size: 0.80 },
    { ref: '219', zone: 'T2 南指廊南侧',     gx: -153, gz: 206.8, px: -153, pz: 227, rot: 0,       bridges: 1, size: 0.82 },
    { ref: '220', zone: 'T2 南指廊南侧根部', gx: -136, gz: 206.8, px: -136, pz: 227, rot: 0,       bridges: 1, size: 0.80 }
];
export const apronStandLayout = referenceStands.map(s => ({ ref: s.ref, x: s.px, z: s.pz, rotY: s.rot, rootX: s.gx, rootZ: s.gz, isDual: s.bridges === 2 }));

export function buildAirport() {
  const scene = new THREE.Group(); scene.name = 'Pure 3D airport';
  const roadCars: { mesh: THREE.Mesh; curve: THREE.Curve<THREE.Vector3>; start: number; end: number; length: number; heightAt?: (x: number, z: number) => number | undefined; phase: number; speed: number; offset: number }[] = [];
  const intercityTrains: { mesh: THREE.Group; start: THREE.Vector3; end: THREE.Vector3; t: number; speed: number }[] = [];
  const roadSplinePaths: { curve: THREE.CatmullRomCurve3; heightAt?: (x: number, z: number) => number | undefined; offset?: number }[] = [];
  const buildings: THREE.Object3D[] = [], obstacles: THREE.Box3[] = [];
  const allRoadClearanceCurves: { points: THREE.Vector3[]; halfWidth: number }[] = [];
  let seed = 71;
  const random = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 4294967296; };
  function buildPure3DAirfieldAndRunways() {
      const airfieldGroup = new THREE.Group(); airfieldGroup.name = 'Airfield / runways';

      const earthMat = new THREE.MeshStandardMaterial({ color: 0xd6cab8, roughness: 0.92, metalness: 0.02 });
      const apronConcreteMat = new THREE.MeshStandardMaterial({ color: 0xe5dec9, roughness: 0.82, metalness: 0.05 });
      const taxiwayConcreteMat = new THREE.MeshStandardMaterial({ color: 0xdad2be, roughness: 0.85, metalness: 0.05 });
      const runwayAsphaltMat = new THREE.MeshStandardMaterial({ color: 0x4b525c, roughness: 0.72, metalness: 0.08 });
      const darkAsphaltMat = new THREE.MeshStandardMaterial({ color: 0x384152, roughness: 0.75, metalness: 0.1 });
      const grassIslandMat = new THREE.MeshStandardMaterial({ color: 0x5c7c47, roughness: 0.88, metalness: 0.02 });
      const lushParkMat = new THREE.MeshStandardMaterial({ color: 0x3a6634, roughness: 0.85, metalness: 0.02 });
      const curbMat = new THREE.MeshStandardMaterial({ color: 0xbdb5a6, roughness: 0.8 });
      const yellowLineMat = new THREE.MeshBasicMaterial({ color: 0xf59e0b });
      const whiteMarkingMat = new THREE.MeshBasicMaterial({ color: 0xf8fafc });
      const waterMat = new THREE.MeshStandardMaterial({ color: 0x0284c7, roughness: 0.15, metalness: 0.65, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2 });

      const earthBase = new THREE.Mesh(new THREE.BoxGeometry(3600, 2, 3600), earthMat);
      earthBase.position.y = -1.1;
      earthBase.receiveShadow = true;
      airfieldGroup.add(earthBase);

      const mainApronSlab = new THREE.Mesh(new THREE.BoxGeometry(580, 0.3, 680), apronConcreteMat);
      mainApronSlab.position.set(0, 0.05, -20);
      mainApronSlab.receiveShadow = true;
      airfieldGroup.add(mainApronSlab);

      const seamMat = new THREE.MeshBasicMaterial({ color: 0xc8bfa8 });
      for (let x = -260; x <= 260; x += 26) {
          const seamX = new THREE.Mesh(new THREE.BoxGeometry(0.18, 0.02, 640), seamMat);
          seamX.position.set(x, 0.21, -20);
          airfieldGroup.add(seamX);
      }
      for (let z = -320; z <= 280; z += 26) {
          const seamZ = new THREE.Mesh(new THREE.BoxGeometry(540, 0.02, 0.18), seamMat);
          seamZ.position.set(0, 0.21, z);
          airfieldGroup.add(seamZ);
      }

      const towerPad = new THREE.Mesh(new THREE.BoxGeometry(54, 0.36, 44), darkAsphaltMat);
      towerPad.position.set(-16, 0.12, -135);
      towerPad.receiveShadow = true;
      airfieldGroup.add(towerPad);

      function create3DGrassIsland(x: number, z: number, w: number, d: number, radius = 8, mat = grassIslandMat, height = 0.45) {
          const shape = new THREE.Shape();
          const hw = w / 2, hd = d / 2;
          const r = Math.min(radius, hw * 0.45, hd * 0.45);
          shape.moveTo(-hw + r, -hd);
          shape.lineTo(hw - r, -hd);
          shape.quadraticCurveTo(hw, -hd, hw, -hd + r);
          shape.lineTo(hw, hd - r);
          shape.quadraticCurveTo(hw, hd, hw - r, hd);
          shape.lineTo(-hw + r, hd);
          shape.quadraticCurveTo(-hw, hd, -hw, hd - r);
          shape.lineTo(-hw, -hd + r);
          shape.quadraticCurveTo(-hw, -hd, -hw + r, -hd);

          const geo = new THREE.ExtrudeGeometry(shape, {
              depth: height,
              bevelEnabled: true,
              bevelSegments: 2,
              steps: 1,
              bevelSize: 0.4,
              bevelThickness: 0.2
          });
          geo.rotateX(-Math.PI / 2);
          const mesh = new THREE.Mesh(geo, mat);
          mesh.position.set(x, 0.15, z);
          mesh.receiveShadow = true;
          mesh.castShadow = true;
          airfieldGroup.add(mesh);
      }

      create3DGrassIsland(115, -268, 128, 22, 9);
      create3DGrassIsland(235, -268, 84,  22, 9);
      create3DGrassIsland(115, -198, 124, 50, 12);
      create3DGrassIsland(235, -198, 92,  50, 12);
      create3DGrassIsland(-8,  -202, 18,  46, 7);
      create3DGrassIsland(-165, -255, 160, 26, 8);

      create3DGrassIsland(0, 138, 186, 265, 22, lushParkMat, 0.38);

      create3DGrassIsland(175, 210, 130, 170, 10, grassIslandMat, 0.35);
      const sePond = new THREE.Mesh(new THREE.CylinderGeometry(16, 16, 0.25, 24), waterMat);
      sePond.scale.set(1.2, 1, 0.75);
      sePond.position.set(182, 0.48, 222);
      airfieldGroup.add(sePond);

      const yaozihaiLake = new THREE.Mesh(new THREE.CylinderGeometry(140, 140, 0.2, 32), waterMat);
      yaozihaiLake.scale.set(1.1, 1, 2.2);
      yaozihaiLake.position.set(-620, -0.05, -120);
      airfieldGroup.add(yaozihaiLake);

      const majiaLake = new THREE.Mesh(new THREE.CylinderGeometry(110, 110, 0.2, 32), waterMat);
      majiaLake.scale.set(1.4, 1, 1.3);
      majiaLake.position.set(-520, -0.05, 520);
      airfieldGroup.add(majiaLake);

      function add3DTaxiwayStrip(x1: number, z1: number, x2: number, z2: number, width = 12, withSurface = true) {
          const dx = x2 - x1, dz = z2 - z1;
          const len = Math.hypot(dx, dz);
          const angle = Math.atan2(dx, dz);
          const mx = (x1 + x2) / 2, mz = (z1 + z2) / 2;

          if (withSurface) {
              const surf = new THREE.Mesh(new THREE.BoxGeometry(width, 0.26, len), taxiwayConcreteMat);
              surf.position.set(mx, 0.08, mz);
              surf.rotation.y = angle;
              surf.receiveShadow = true;
              airfieldGroup.add(surf);
          }

          const line = new THREE.Mesh(new THREE.BoxGeometry(0.42, 0.04, len), yellowLineMat);
          line.position.set(mx, 0.23, mz);
          line.rotation.y = angle;
          airfieldGroup.add(line);
      }

      [-295, -235, -165, -142].forEach(zPos => {
          add3DTaxiwayStrip(-295, zPos, 305, zPos, 13, true);
      });

      [-46, -26, 55, 125, 164, 232].forEach(zPos => {
          add3DTaxiwayStrip(-252, zPos, -108, zPos, 10, false);
      });
      [-44, -20, 62].forEach(zPos => {
          add3DTaxiwayStrip(114, zPos, 262, zPos, 10, false);
      });

      function build3DRunwaySystem(rwyX: number, taxiInner1X: number, taxiInner2X: number, sideSign: number) {
          const rwyLen = 1040;
          const rwyWidth = sideSign > 0 ? 30 : 26;

          const rwyMesh = new THREE.Mesh(new THREE.BoxGeometry(rwyWidth, 0.35, rwyLen), runwayAsphaltMat);
          rwyMesh.position.set(rwyX, 0.12, -20);
          rwyMesh.receiveShadow = true;
          airfieldGroup.add(rwyMesh);

          [-rwyWidth / 2 + 1.0, rwyWidth / 2 - 1.0].forEach(offsetX => {
              const edgeLine = new THREE.Mesh(new THREE.BoxGeometry(0.45, 0.04, rwyLen - 20), whiteMarkingMat);
              edgeLine.position.set(rwyX + offsetX, 0.31, -20);
              airfieldGroup.add(edgeLine);
          });

          for (let z = -480; z <= 440; z += 26) {
              const dash = new THREE.Mesh(new THREE.BoxGeometry(0.55, 0.04, 14), whiteMarkingMat);
              dash.position.set(rwyX, 0.31, z);
              airfieldGroup.add(dash);
          }

          [-515, 475].forEach((threshZ, idx) => {
              for (let k = -4; k <= 4; k++) {
                  if (k === 0) continue;
                  const key = new THREE.Mesh(new THREE.BoxGeometry(1.3, 0.04, 20), whiteMarkingMat);
                  key.position.set(rwyX + k * 2.5, 0.31, threshZ);
                  airfieldGroup.add(key);
              }
              const aimZ = idx === 0 ? threshZ + 85 : threshZ - 85;
              [-5.5, 5.5].forEach(dx => {
                  const aimBlock = new THREE.Mesh(new THREE.BoxGeometry(3.4, 0.04, 28), whiteMarkingMat);
                  aimBlock.position.set(rwyX + dx, 0.31, aimZ);
                  airfieldGroup.add(aimBlock);
              });
          });

          add3DTaxiwayStrip(taxiInner1X, -530, taxiInner1X, 490, 14, true);
          add3DTaxiwayStrip(taxiInner2X, -450, taxiInner2X, 450, 13, true);

          const midIsland1X = (rwyX + taxiInner1X) / 2;
          const island1W = Math.abs(rwyX - taxiInner1X) - rwyWidth / 2 - 8;
          [-440, -290, -140, 10, 160, 310, 440].forEach(iz => {
              create3DGrassIsland(midIsland1X, iz, island1W, 105, 8, grassIslandMat, 0.36);
          });

          const midIsland2X = (taxiInner1X + taxiInner2X) / 2;
          const island2W = Math.abs(taxiInner1X - taxiInner2X) - 14;
          [-360, -190, -20, 150, 310].forEach(iz => {
              create3DGrassIsland(midIsland2X, iz, island2W, 115, 6, grassIslandMat, 0.34);
          });

          const exitZPairs = [
              [490, 490], [375, 315], [225, 165], [75, 15],
              [-80, -140], [-225, -165], [-375, -315], [-520, -520]
          ];
          exitZPairs.forEach(pair => {
              add3DTaxiwayStrip(rwyX, pair[0], taxiInner1X, pair[1], 11, true);
              add3DTaxiwayStrip(taxiInner1X, pair[1], taxiInner2X, pair[1], 11, true);
          });
      }

      build3DRunwaySystem(-365, -302, -256, -1);
      build3DRunwaySystem(375,  312,  266,  1);

      scene.add(airfieldGroup);
  }

  function buildT3Terminal() {
      const t3Group = new THREE.Group(); t3Group.name = 'T3';

      const roofWhiteMat = new THREE.MeshStandardMaterial({
          color: 0xf8fafc,
          roughness: 0.26,
          metalness: 0.18
      });
      const roofSilverRibMat = new THREE.MeshStandardMaterial({
          color: 0xe2e8f0,
          roughness: 0.32,
          metalness: 0.35
      });
      const skylightGlassMat = new THREE.MeshStandardMaterial({
          color: 0x1e293b,
          roughness: 0.12,
          metalness: 0.88,
          emissive: 0x38bdf8,
          emissiveIntensity: 0.10
      });

      const glassFacadeMat = new THREE.MeshStandardMaterial({
          color: 0x708999,
          roughness: 0.28,
          metalness: 0.35
      });

      const northSpanShape = new THREE.Shape();
      northSpanShape.moveTo(-198, 100);
      northSpanShape.quadraticCurveTo(0, 108, 198, 100);
      northSpanShape.lineTo(196, 82);
      northSpanShape.quadraticCurveTo(0, 86, -196, 82);
      northSpanShape.closePath();

      const northGeo = new THREE.ExtrudeGeometry(northSpanShape, {
          depth: 11.5,
          bevelEnabled: true,
          bevelSegments: 4,
          steps: 1,
          bevelSize: 2.0,
          bevelThickness: 2.4
      });
      northGeo.rotateX(-Math.PI / 2);
      const northMesh = new THREE.Mesh(northGeo, [roofWhiteMat, glassFacadeMat]);
      northMesh.position.y = 1.5;
      northMesh.castShadow = true;
      northMesh.receiveShadow = true;
      t3Group.add(northMesh);

      const glassNorth = new THREE.Mesh(new THREE.BoxGeometry(388, 9, 17), glassFacadeMat);
      glassNorth.position.set(0, 4.8, -91);
      t3Group.add(glassNorth);

      const bodyShape = new THREE.Shape();
      bodyShape.moveTo(-94, 86);
      bodyShape.lineTo(94, 86);
      bodyShape.lineTo(95, 18);
      bodyShape.quadraticCurveTo(95, 7, 80, 8);
      bodyShape.quadraticCurveTo(0, 18, -80, 8);
      bodyShape.quadraticCurveTo(-95, 7, -95, 18);
      bodyShape.closePath();

      const courtyardCoords = [
          [[-84, 72], [-56, 72], [-56, 44], [-84, 52]],
          [[84, 72],  [56, 72],  [56, 44],  [84, 52]]
      ];
      courtyardCoords.forEach(pts => {
          const hole = new THREE.Path();
          hole.moveTo(pts[0][0], pts[0][1]);
          for (let i = 1; i < pts.length; i++) hole.lineTo(pts[i][0], pts[i][1]);
          hole.closePath();
          bodyShape.holes.push(hole);
      });

      const bodyGeo = new THREE.ExtrudeGeometry(bodyShape, {
          depth: 11.9,
          bevelEnabled: true,
          bevelSegments: 5,
          steps: 1,
          bevelSize: 2.2,
          bevelThickness: 2.8
      });
      bodyGeo.rotateX(-Math.PI / 2);
      const bodyMesh = new THREE.Mesh(bodyGeo, [roofWhiteMat, glassFacadeMat]);
      bodyMesh.position.y = 1.5;
      bodyMesh.castShadow = true;
      bodyMesh.receiveShadow = true;
      t3Group.add(bodyMesh);

      const rise = (x: number, z: number) => 5.2 * Math.max(0, 1 - (x / 94) ** 2) * Math.max(0, 1 - ((z + 47) / 39) ** 2);
      const roofSkin = new THREE.ExtrudeGeometry(bodyShape, { depth: .95, bevelEnabled: false, curveSegments: 24 });
      roofSkin.rotateX(-Math.PI / 2);
      const curved = new TessellateModifier(9, 8).modify(roofSkin), roofPoints = curved.getAttribute('position');
      for (let i = 0; i < roofPoints.count; i++) roofPoints.setY(i, roofPoints.getY(i) + rise(roofPoints.getX(i), roofPoints.getZ(i)));
      roofSkin.setAttribute('position', roofPoints); roofSkin.setAttribute('uv', curved.getAttribute('uv'));
      roofSkin.clearGroups(); roofSkin.deleteAttribute('normal'); roofSkin.computeVertexNormals(); curved.dispose();
      const roof = new THREE.Mesh(roofSkin, roofWhiteMat); roof.name = 'T3 courtyard roof';
      roof.position.y = 16.15; roof.castShadow = true; roof.receiveShadow = true; t3Group.add(roof);

      for (let i = -13; i <= 13; i++) {
          const x = i * 4, fan = Math.abs(i) / 13;
          const zStart = -77 + fan * 7, zEnd = -20 - fan * 20;
          for (const [offset, width, mat] of [[0, .86, skylightGlassMat], [1.3, .32, roofSilverRibMat]] as const) {
              const strip = new THREE.PlaneGeometry(width, zEnd - zStart, 1, 12).rotateX(-Math.PI / 2);
              const coords = strip.getAttribute('position'), center = (zStart + zEnd) / 2;
              for (let j = 0; j < coords.count; j++) {
                  const px = coords.getX(j) + x + offset, pz = coords.getZ(j) + center;
                  coords.setXYZ(j, px, 17.27 + rise(px, pz), pz);
              }
              strip.computeVertexNormals(); t3Group.add(new THREE.Mesh(strip, mat));
          }
      }

      const glazedPier = [glassFacadeMat, glassFacadeMat, roofWhiteMat, roofWhiteMat, glassFacadeMat, glassFacadeMat];
      const swConcourse = new THREE.Mesh(new THREE.BoxGeometry(98, 10.5, 13), glazedPier);
      swConcourse.position.set(-151, 6.2, 15);
      swConcourse.castShadow = true;
      swConcourse.receiveShadow = true;
      t3Group.add(swConcourse);

      const swSkylight = new THREE.Mesh(new THREE.BoxGeometry(82, 0.5, 1.8), skylightGlassMat);
      swSkylight.position.set(-151, 11.55, 15);
      t3Group.add(swSkylight);

      const swGlass = new THREE.Mesh(new THREE.BoxGeometry(96, 8.2, 11.6), glassFacadeMat);
      swGlass.position.set(-151, 4.4, 15);
      t3Group.add(swGlass);

      const seConcourse = new THREE.Mesh(new THREE.BoxGeometry(100, 10.5, 13), glazedPier);
      seConcourse.position.set(148, 6.2, 23);
      seConcourse.castShadow = true;
      seConcourse.receiveShadow = true;
      t3Group.add(seConcourse);

      const seSkylight = new THREE.Mesh(new THREE.BoxGeometry(84, 0.5, 1.8), skylightGlassMat);
      seSkylight.position.set(148, 11.55, 23);
      t3Group.add(seSkylight);

      const seGlass = new THREE.Mesh(new THREE.BoxGeometry(98, 8.2, 11.6), glassFacadeMat);
      seGlass.position.set(148, 4.4, 23);
      t3Group.add(seGlass);

      [-148, 148].forEach(px => {
          const pierSkylight = new THREE.Mesh(new THREE.BoxGeometry(82, 0.5, 2.2), skylightGlassMat);
          pierSkylight.position.set(px, 15.3, -91);
          t3Group.add(pierSkylight);
      });

      const westLink = new THREE.Mesh(new THREE.BoxGeometry(10.5, 9.2, 86), roofWhiteMat);
      westLink.position.set(-100, 5.4, 52);
      westLink.castShadow = true;
      westLink.receiveShadow = true;
      t3Group.add(westLink);

      const westLinkGlass = new THREE.Mesh(new THREE.BoxGeometry(9.2, 7.2, 84), glassFacadeMat);
      westLinkGlass.position.set(-100, 4.0, 52);
      t3Group.add(westLinkGlass);

      buildings.push(t3Group); scene.add(t3Group);
  }

  function buildT2Terminal() {
      const t2Group = new THREE.Group(); t2Group.name = 'T2';

      const t2RoofMat = new THREE.MeshStandardMaterial({
          color: 0xf1f5f9,
          roughness: 0.35,
          metalness: 0.25
      });
      const t2RibMat = new THREE.MeshStandardMaterial({
          color: 0xcbd5e1,
          roughness: 0.4,
          metalness: 0.35
      });
      const t2GlassMat = new THREE.MeshStandardMaterial({
          color: 0x8fa5b1,
          roughness: 0.26,
          metalness: 0.32
      });

      const glazedHall = [t2GlassMat, t2GlassMat, t2RoofMat, t2RoofMat, t2GlassMat, t2GlassMat];
      const mainHall = new THREE.Mesh(new THREE.BoxGeometry(24, 12, 118), glazedHall);
      mainHall.position.set(-112, 6, 155);
      mainHall.castShadow = true;
      mainHall.receiveShadow = true;
      t2Group.add(mainHall);

      for (let z = 102; z <= 208; z += 7.5) {
          const rib = new THREE.Mesh(new THREE.BoxGeometry(22, 0.55, 1.4), t2RibMat);
          rib.position.set(-112, 12.25, z);
          t2Group.add(rib);
      }

      const glassCurve = new THREE.Mesh(
          new THREE.CylinderGeometry(16, 16, 10, 24, 1, false, 0, Math.PI),
          t2GlassMat
      );
      glassCurve.scale.set(0.75, 1, 3.4);
      glassCurve.position.set(-101, 5, 155);
      t2Group.add(glassCurve);

      const pierNorth = new THREE.Mesh(new THREE.BoxGeometry(86, 9.5, 12), glazedHall);
      pierNorth.position.set(-167, 5, 108);
      pierNorth.castShadow = true;
      pierNorth.receiveShadow = true;
      t2Group.add(pierNorth);

      const pierSouth = new THREE.Mesh(new THREE.BoxGeometry(86, 9.5, 12), glazedHall);
      pierSouth.position.set(-167, 5, 200);
      pierSouth.castShadow = true;
      pierSouth.receiveShadow = true;
      t2Group.add(pierSouth);
      for (const z of [108, 200]) {
          const fascia = new THREE.Mesh(new THREE.BoxGeometry(86.7, .55, 13.2), t2RoofMat);
          fascia.position.set(-167, 9.75, z); t2Group.add(fascia);
          for (let i = -2; i <= 2; i++) {
              const mullion = new THREE.Mesh(new THREE.BoxGeometry(.18, 7.8, .16), t2RibMat);
              mullion.position.set(-210.16, 5.25, z + i * 2.15); t2Group.add(mullion);
          }
      }

      const fboMat = new THREE.MeshStandardMaterial({ color: 0xe2e8f0, roughness: 0.4, metalness: 0.2 });
      const fboBldg = new THREE.Mesh(new THREE.BoxGeometry(22, 8.5, 36), fboMat);
      fboBldg.position.set(-118, 4.25, 262);
      fboBldg.castShadow = true;
      fboBldg.receiveShadow = true;
      t2Group.add(fboBldg);

      buildings.push(t2Group); scene.add(t2Group);
  }

  function buildGTCAndTower() {
      const centerGroup = new THREE.Group(); centerGroup.name = 'GTC / ATC';

      const whiteMat = new THREE.MeshStandardMaterial({
          color: 0xffffff,
          roughness: 0.25,
          metalness: 0.2
      });
      const skylightMat = new THREE.MeshStandardMaterial({
          color: 0x0f172a,
          roughness: 0.1,
          metalness: 0.9,
          emissive: 0x38bdf8,
          emissiveIntensity: 0.12
      });

      const gtcShape = new THREE.Shape();
      gtcShape.moveTo(-18, -14);
      gtcShape.quadraticCurveTo(-7, -52, -18, -90);
      gtcShape.lineTo(18, -90);
      gtcShape.quadraticCurveTo(7, -52, 18, -14);
      gtcShape.closePath();

      const gtcGeo = new THREE.ExtrudeGeometry(gtcShape, {
          depth: 7,
          bevelEnabled: true,
          bevelSize: 1.2,
          bevelThickness: 1.5
      });
      gtcGeo.rotateX(-Math.PI / 2);
      const gtcMesh = new THREE.Mesh(gtcGeo, [whiteMat, material(0x8ea2af, .28, .32)]);
      gtcMesh.position.y = 1.2;
      gtcMesh.castShadow = true;
      gtcMesh.receiveShadow = true;
      centerGroup.add(gtcMesh);

      const gtcSpine = new THREE.Mesh(new THREE.BoxGeometry(6.2, 9.0, 68), skylightMat);
      gtcSpine.position.set(0, 4.7, 52);
      centerGroup.add(gtcSpine);

      const towerGroup = new THREE.Group();
      towerGroup.position.set(-16, 0.2, -135);

      const baseBldg = new THREE.Mesh(new THREE.BoxGeometry(20, 7, 18), whiteMat);
      baseBldg.position.y = 3.5;
      baseBldg.castShadow = true;
      towerGroup.add(baseBldg);

      const shaftGeo = new THREE.CylinderGeometry(2.4, 3.8, 52, 20);
      const shaftMesh = new THREE.Mesh(shaftGeo, whiteMat);
      shaftMesh.position.y = 26;
      shaftMesh.castShadow = true;
      towerGroup.add(shaftMesh);

      const cabMesh = new THREE.Mesh(new THREE.CylinderGeometry(4.6, 3.0, 7, 20), skylightMat);
      cabMesh.position.y = 51;
      towerGroup.add(cabMesh);

      const spire = new THREE.Mesh(new THREE.CylinderGeometry(0.15, 0.4, 14, 8), whiteMat);
      spire.position.y = 63;
      towerGroup.add(spire);

      centerGroup.add(towerGroup);
      buildings.push(centerGroup); scene.add(centerGroup);
  }

  function buildDetailedViaductMesh(pointsArray: THREE.Vector3[], roadWidth: number, deckColor: number, closed = false, withPiers = true, layerPriority = 1) {
      const curve = new THREE.CatmullRomCurve3(pointsArray, closed, 'centripetal', 0.45);
      allRoadClearanceCurves.push({ points: curve.getPoints(36), halfWidth: roadWidth * 0.68 + 2.2 });

      const segments = Math.max(96, pointsArray.length * 22);
      const pts = curve.getSpacedPoints(segments);
      const group = new THREE.Group();

      const halfW = roadWidth / 2;
      const railW = 0.42;

      const topPositions: number[] = [];
      const topIndices: number[] = [];
      const railPositions: number[] = [];
      const railIndices: number[] = [];
      const dashPositions: number[] = [];
      const dashIndices: number[] = [];

      for (let i = 0; i < pts.length; i++) {
          const p = pts[i];
          let tangent;
          if (i < pts.length - 1) {
              tangent = new THREE.Vector3().subVectors(pts[i + 1], p).normalize();
          } else if (closed) {
              tangent = new THREE.Vector3().subVectors(pts[1], p).normalize();
          } else {
              tangent = new THREE.Vector3().subVectors(p, pts[i - 1]).normalize();
          }

          const right = new THREE.Vector3(-tangent.z, 0, tangent.x).normalize();

          const lx = p.x - right.x * halfW;
          const lz = p.z - right.z * halfW;
          const rx = p.x + right.x * halfW;
          const rz = p.z + right.z * halfW;
          const yTop = p.y + layerPriority * 0.03;

          topPositions.push(lx, yTop, lz, rx, yTop, rz);

          const lOutX = p.x - right.x * (halfW + railW);
          const lOutZ = p.z - right.z * (halfW + railW);
          const rOutX = p.x + right.x * (halfW + railW);
          const rOutZ = p.z + right.z * (halfW + railW);
          const yRail = yTop + 0.32;

          railPositions.push(
              lOutX, yRail, lOutZ, lx, yRail, lz,
              rx, yRail, rz, rOutX, yRail, rOutZ
          );

          const dashHalf = 0.18;
          dashPositions.push(
              p.x - right.x * dashHalf, yTop + 0.04, p.z - right.z * dashHalf,
              p.x + right.x * dashHalf, yTop + 0.04, p.z + right.z * dashHalf
          );

          if (i < pts.length - 1) {
              const base = i * 2;
              topIndices.push(base, base + 1, base + 2, base + 1, base + 3, base + 2);

              const rBase = i * 4;
              railIndices.push(rBase, rBase + 1, rBase + 4, rBase + 1, rBase + 5, rBase + 4);
              railIndices.push(rBase + 2, rBase + 3, rBase + 6, rBase + 3, rBase + 7, rBase + 6);

              if (i % 3 === 0) {
                  dashIndices.push(base, base + 1, base + 2, base + 1, base + 3, base + 2);
              }
          }
      }

      const deckGeo = new THREE.BufferGeometry();
      deckGeo.setAttribute('position', new THREE.Float32BufferAttribute(topPositions, 3));
      deckGeo.setIndex(topIndices);
      deckGeo.computeVertexNormals();

      const deckMat = new THREE.MeshStandardMaterial({
          color: deckColor,
          roughness: 0.55,
          metalness: 0.1,
          side: THREE.DoubleSide,
          polygonOffset: true,
          polygonOffsetFactor: -layerPriority * 2,
          polygonOffsetUnits: -layerPriority * 2
      });
      const deckMesh = new THREE.Mesh(deckGeo, deckMat);
      deckMesh.castShadow = true;
      deckMesh.receiveShadow = true;
      group.add(deckMesh);

      const railGeo = new THREE.BufferGeometry();
      railGeo.setAttribute('position', new THREE.Float32BufferAttribute(railPositions, 3));
      railGeo.setIndex(railIndices);
      railGeo.computeVertexNormals();

      const railMat = new THREE.MeshStandardMaterial({
          color: 0xe2e8f0,
          roughness: 0.45,
          metalness: 0.2,
          side: THREE.DoubleSide
      });
      const railMesh = new THREE.Mesh(railGeo, railMat);
      railMesh.castShadow = true;
      group.add(railMesh);

      const dashGeo = new THREE.BufferGeometry();
      dashGeo.setAttribute('position', new THREE.Float32BufferAttribute(dashPositions, 3));
      dashGeo.setIndex(dashIndices);
      dashGeo.computeVertexNormals();

      const dashMat = new THREE.MeshBasicMaterial({
          color: 0xffffff,
          side: THREE.DoubleSide,
          polygonOffset: true,
          polygonOffsetFactor: -layerPriority * 2 - 2,
          polygonOffsetUnits: -2
      });
      group.add(new THREE.Mesh(dashGeo, dashMat));

      if (withPiers) {
          const pierMat = new THREE.MeshStandardMaterial({ color: 0x94a3b8, roughness: 0.65 });
          const pierStep = Math.max(8, Math.floor(pts.length / 18));
          for (let i = 4; i < pts.length - 4; i += pierStep) {
              const p = pts[i];
              if (p.y > 1.35) {
                  const pierH = p.y - 0.15;
                  const pier = new THREE.Mesh(new THREE.CylinderGeometry(0.55, 0.7, pierH, 10), pierMat);
                  pier.position.set(p.x, pierH / 2, p.z);
                  pier.castShadow = true;
                  group.add(pier);

                  const cap = new THREE.Mesh(new THREE.BoxGeometry(roadWidth * 0.72, 0.55, 1.4), pierMat);
                  cap.position.set(p.x, p.y - 0.3, p.z);
                  if (i < pts.length - 1) cap.lookAt(pts[i + 1].x, p.y - 0.3, pts[i + 1].z);
                  group.add(cap);
              }
          }
      }

      return { group, curve };
  }

  function isPointNearAnyRoad(x: number, z: number, extraMargin = 0) {
      for (let i = 0; i < allRoadClearanceCurves.length; i++) {
          const item = allRoadClearanceCurves[i];
          const samples = 36;
          const minDist = item.halfWidth + extraMargin;
          const minDistSq = minDist * minDist;
          for (let s = 0; s <= samples; s++) {
              const pt = item.points[s];
              const dx = pt.x - x;
              const dz = pt.z - z;
              if (dx * dx + dz * dz < minDistSq) return true;
          }
      }
      return false;
  }

  function buildMacroDistrictBuildingsAndRoads() {
      const macroGroup = new THREE.Group(); macroGroup.name = 'Cargo / maintenance / S19';

      const warehouseMat = new THREE.MeshStandardMaterial({ color: 0xe2e8f0, roughness: 0.45, metalness: 0.25 });
      const blueRoofMat = new THREE.MeshStandardMaterial({ color: 0x0369a1, roughness: 0.35, metalness: 0.3 });
      const officeMat = new THREE.MeshStandardMaterial({ color: 0xf1f5f9, roughness: 0.4, metalness: 0.2 });
      const darkRoofMat = new THREE.MeshStandardMaterial({ color: 0x475569, roughness: 0.5 });

      const TUN_Y = 0.6;
      function mkRoad(pts: THREE.Vector3[], w: number, col: number, pier: boolean, layer: number) {
          const r = buildDetailedViaductMesh(pts.map(p => new THREE.Vector3(p.x, p.y, p.z)), w, col, false, pier, layer);
          macroGroup.add(r.group);
          return r;
      }
      function buildTunnelGhost(pts: THREE.Vector3[], width: number, name: string) {
          const curve = new THREE.CatmullRomCurve3(pts.map(p => new THREE.Vector3(p.x, TUN_Y, p.z)), false, 'centripetal', 0.4);
          const sp = curve.getSpacedPoints(Math.max(60, pts.length * 20));
          const pos: number[] = [], idx: number[] = [], dpos: number[] = [], didx: number[] = [];
          const hw = width / 2;
          sp.forEach((p, i) => {
              const q = sp[Math.min(i + 1, sp.length - 1)], o = sp[Math.max(i - 1, 0)];
              const t = new THREE.Vector3().subVectors(q, o).normalize();
              const rx = -t.z, rz = t.x;
              pos.push(p.x - rx * hw, p.y, p.z - rz * hw, p.x + rx * hw, p.y, p.z + rz * hw);
              if (i < sp.length - 1) {
                  const b = i * 2; idx.push(b, b + 1, b + 2, b + 1, b + 3, b + 2);
                  if (i % 4 < 2) {
                    
                      [-1, 1].forEach(s => {
                          const e = hw * s, n = sp[i + 1];
                          dpos.push(p.x + rx * (e - 0.25), p.y + 0.03, p.z + rz * (e - 0.25), p.x + rx * (e + 0.25), p.y + 0.03, p.z + rz * (e + 0.25),
                                    n.x + rx * (e - 0.25), n.y + 0.03, n.z + rz * (e - 0.25), n.x + rx * (e + 0.25), n.y + 0.03, n.z + rz * (e + 0.25));
                      });
                  }
              }
          });
          for (let k = 0; k < dpos.length / 12; k++) didx.push(k * 4, k * 4 + 1, k * 4 + 2, k * 4 + 1, k * 4 + 3, k * 4 + 2);
          const g = new THREE.BufferGeometry();
          g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3)); g.setIndex(idx);
          const m = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color: 0xfde0bd, transparent: true, opacity: 0.62, side: THREE.DoubleSide, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -6, polygonOffsetUnits: -6 }));
          m.renderOrder = 5; m.name = name; macroGroup.add(m);
          const g2 = new THREE.BufferGeometry();
          g2.setAttribute('position', new THREE.Float32BufferAttribute(dpos, 3)); g2.setIndex(didx);
          const m2 = new THREE.Mesh(g2, new THREE.MeshBasicMaterial({ color: 0xc2410c, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -8, polygonOffsetUnits: -8 }));
          m2.renderOrder = 6; m2.name = name + ' edges'; macroGroup.add(m2);
          return curve;
      }
      function mkPortal(x: number, z: number, tx: number, tz: number) {
          const g = new THREE.Group();
          g.name = 'Tunnel portal'; g.position.set(x, 0.3, z);
          g.rotation.y = Math.atan2(tx, tz);
          const wm = new THREE.MeshStandardMaterial({ color: 0x94a3b8, roughness: 0.6 });
          [-4.6, 4.6].forEach(sx => { const w = new THREE.Mesh(new THREE.BoxGeometry(1.0, 2.4, 12), wm); w.position.set(sx, 1.2, 0); w.castShadow = true; g.add(w); });
          const lin = new THREE.Mesh(new THREE.BoxGeometry(10.2, 0.9, 1.6), new THREE.MeshStandardMaterial({ color: 0x64748b, roughness: 0.5 }));
          lin.position.set(0, 2.1, 0); g.add(lin);
          macroGroup.add(g);
      }
      const dirOf = (a: THREE.Vector3, b: THREE.Vector3) => [b.x - a.x, b.z - a.z];
      function route(name: string, ramp: THREE.Vector3[], tun: THREE.Vector3[], sur: THREE.Vector3[], w: number) {
          const rr = mkRoad(ramp, w, 0xea580c, false, 3); roadSplinePaths.push({ curve: rr.curve, offset: .09 });
          const c = buildTunnelGhost(tun, w + 1.2, name);
          const d0 = dirOf(tun[0], tun[1]), d1 = dirOf(tun[tun.length - 2], tun[tun.length - 1]);
          mkPortal(tun[0].x, tun[0].z, d0[0], d0[1]);
          mkPortal(tun[tun.length - 1].x, tun[tun.length - 1].z, d1[0], d1[1]);
          const sr = mkRoad(sur, w, 0xea580c, false, 2); roadSplinePaths.push({ curve: sr.curve, offset: .06 });
          return c;
      }
                  route('West under-apron tunnel', [
          new THREE.Vector3(-74.7, 8.7, 60.9),
          new THREE.Vector3(-86.5, 4.4, 41.4),
          new THREE.Vector3(-95.2, 0.6, 20.9)
      ], [
          new THREE.Vector3(-95.2, 0.6, 20.9),
          new THREE.Vector3(-100.0, 0.6, -37.8),
          new THREE.Vector3(-97.2, 0.6, -83.5),
          new THREE.Vector3(-84.2, 0.6, -126.4),
          new THREE.Vector3(-62.1, 0.6, -181.4),
          new THREE.Vector3(-49.3, 0.6, -220.2),
          new THREE.Vector3(-49.8, 0.6, -266.9)
      ], [
          new THREE.Vector3(-49.8, 0.6, -266.9),
          new THREE.Vector3(-49.9, 0.6, -278.6),
          new THREE.Vector3(-51.0, 0.6, -315.3),
          new THREE.Vector3(-38.5, 0.6, -350.0),
          new THREE.Vector3(-19.4, 0.6, -381.8),
          new THREE.Vector3(1.9, 0.6, -410.9),
          new THREE.Vector3(15.9, 0.6, -435.1)
      ], 6.4);
      route('East under-apron tunnel', [
          new THREE.Vector3(78.5, 8.7, 14.8),
          new THREE.Vector3(97.5, 4.4, 12.2),
          new THREE.Vector3(103.4, 0.6, 10.0)
      ], [
          new THREE.Vector3(103.4, 0.6, 10.0),
          new THREE.Vector3(106.2, 0.6, -35.6),
          new THREE.Vector3(109.2, 0.6, -85.5),
          new THREE.Vector3(110.3, 0.6, -137.5),
          new THREE.Vector3(110.9, 0.6, -181.3),
          new THREE.Vector3(107.1, 0.6, -221.1)
      ], [
          new THREE.Vector3(107.1, 0.6, -221.1),
          new THREE.Vector3(106.5, 0.6, -232.4),
          new THREE.Vector3(103.5, 0.6, -265.1),
          new THREE.Vector3(92.2, 0.6, -298.3),
          new THREE.Vector3(73.8, 0.6, -325.2),
          new THREE.Vector3(49.1, 0.6, -350.5),
          new THREE.Vector3(29.2, 0.6, -380.1),
          new THREE.Vector3(17.5, 0.6, -412.9),
          new THREE.Vector3(15.9, 0.6, -435.1)
      ], 6.4);
      const northTrunk = mkRoad([
          new THREE.Vector3(15.9, 0.8, -435.1),
          new THREE.Vector3(10.4, 0.8, -481.2),
          new THREE.Vector3(10.7, 0.8, -520.8),
          new THREE.Vector3(14.9, 0.8, -556.0)
      ].concat([new THREE.Vector3(14, 0.8, -720)]), 7.4, 0xea580c, false, 3);
      roadSplinePaths.push({ curve: northTrunk.curve, offset: .09 });

      const cargoRoad2 = buildDetailedViaductMesh([
          new THREE.Vector3(-58, 0.6, -355),
          new THREE.Vector3(-58, 0.6, -680)
      ], 5.0, 0x64748b, false, false, 1);
      macroGroup.add(cargoRoad2.group);

      const maintRoad2 = buildDetailedViaductMesh([
          new THREE.Vector3(78, 0.6, -355),
          new THREE.Vector3(78, 0.6, -640)
      ], 5.0, 0x64748b, false, false, 1);
      macroGroup.add(maintRoad2.group);

      [-395, -485, -575].forEach(zPos => {
          const crossRoad = buildDetailedViaductMesh([
              new THREE.Vector3(-165, 0.6, zPos),
              new THREE.Vector3(175,  0.6, zPos)
          ], 4.6, 0x64748b, false, false, 1);
          macroGroup.add(crossRoad.group);
      });

      const cargoWarehouses = [
          { x: -105, z: -365, w: 46, h: 7,   d: 24, roof: blueRoofMat },
          { x: -105, z: -440, w: 52, h: 7.5, d: 28, roof: warehouseMat },
          { x: -32,  z: -440, w: 34, h: 6.5, d: 24, roof: warehouseMat },
          { x: -105, z: -530, w: 48, h: 7.5, d: 28, roof: blueRoofMat },
          { x: -32,  z: -530, w: 32, h: 6.5, d: 24, roof: warehouseMat },
          { x: -115, z: -635, w: 24, h: 8,   d: 62, roof: darkRoofMat },
          { x: -82,  z: -635, w: 24, h: 8,   d: 62, roof: darkRoofMat }
      ];
      cargoWarehouses.forEach(b => {
          const body = new THREE.Mesh(new THREE.BoxGeometry(b.w, b.h, b.d), warehouseMat);
          body.position.set(b.x, b.h / 2 + 0.2, b.z);
          body.castShadow = true;
          body.receiveShadow = true;
          buildings.push(body);
          macroGroup.add(body);

          const top = new THREE.Mesh(new THREE.BoxGeometry(b.w + 1.5, 0.8, b.d + 1.5), b.roof);
          top.position.set(b.x, b.h + 0.6, b.z);
          macroGroup.add(top);
      });

      const maintHangars = [
          { x: 42,  z: -440, w: 40, h: 11, d: 28 },
          { x: 125, z: -440, w: 48, h: 12, d: 32 },
          { x: 42,  z: -530, w: 38, h: 10, d: 26 },
          { x: 125, z: -530, w: 50, h: 12, d: 30 }
      ];
      maintHangars.forEach(h => {
          const mesh = new THREE.Mesh(new THREE.BoxGeometry(h.w, h.h, h.d), officeMat);
          mesh.position.set(h.x, h.h / 2 + 0.2, h.z);
          mesh.castShadow = true;
          mesh.receiveShadow = true;
          buildings.push(mesh);
          macroGroup.add(mesh);

          const archRoof = new THREE.Mesh(new THREE.BoxGeometry(h.w + 2, 1.4, h.d + 2), blueRoofMat);
          archRoof.position.set(h.x, h.h + 0.9, h.z);
          macroGroup.add(archRoof);
      });

      const s19InboundBridge = buildDetailedViaductMesh([
          new THREE.Vector3(-6.5, 1.8, 620),
          new THREE.Vector3(-6.5, 2.2, 460),
          new THREE.Vector3(-6.5, 1.6, 330),
          new THREE.Vector3(-6.5, 0.8, 276)
      ], 6.4, 0xbe123c, false, true, 2);
      macroGroup.add(s19InboundBridge.group);
      roadSplinePaths.push({ curve: s19InboundBridge.curve, offset: .09 });

      const s19OutboundBridge = buildDetailedViaductMesh([
          new THREE.Vector3(6.5, 0.8, 276),
          new THREE.Vector3(6.5, 1.6, 330),
          new THREE.Vector3(6.5, 2.2, 460),
          new THREE.Vector3(6.5, 1.8, 620)
      ], 6.4, 0xbe123c, false, true, 2);
      macroGroup.add(s19OutboundBridge.group);
      roadSplinePaths.push({ curve: s19OutboundBridge.curve, offset: .09 });

      const westFirstRoad = buildDetailedViaductMesh([
          new THREE.Vector3(-22, 0.5, 620),
          new THREE.Vector3(-22, 0.5, 340),
          new THREE.Vector3(-22, 0.5, 280)
      ], 4.6, 0x475569, false, false, 1);
      macroGroup.add(westFirstRoad.group);

      const eastFirstRoad = buildDetailedViaductMesh([
          new THREE.Vector3(22, 0.5, 280),
          new THREE.Vector3(22, 0.5, 340),
          new THREE.Vector3(22, 0.5, 620)
      ], 4.6, 0x475569, false, false, 1);
      macroGroup.add(eastFirstRoad.group);

      [335, 415, 495].forEach(zPos => {
          const southCross = buildDetailedViaductMesh([
              new THREE.Vector3(-210, 0.5, zPos),
              new THREE.Vector3(210,  0.5, zPos)
          ], 4.5, 0x64748b, false, false, 1);
          macroGroup.add(southCross.group);
      });

      const southBlocks = [
          { x: -75,  z: 375, w: 34, h: 11, d: 22 },
          { x: -135, z: 375, w: 36, h: 9,  d: 22 },
          { x: -75,  z: 455, w: 38, h: 12, d: 24 },
          { x: -135, z: 455, w: 34, h: 10, d: 22 },
          { x: 75,   z: 375, w: 38, h: 12, d: 24 },
          { x: 135,  z: 375, w: 36, h: 10, d: 24 },
          { x: 75,   z: 455, w: 40, h: 13, d: 26 }
      ];
      southBlocks.forEach(b => {
          const bldg = new THREE.Mesh(new THREE.BoxGeometry(b.w, b.h, b.d), officeMat);
          bldg.position.set(b.x, b.h / 2 + 0.2, b.z);
          bldg.castShadow = true;
          bldg.receiveShadow = true;
          buildings.push(bldg);
          macroGroup.add(bldg);
      });

      const taxiBridgeMat = new THREE.MeshStandardMaterial({ color: 0xcbd5e1, roughness: 0.55, metalness: 0.15 });
      const shoulderGreenMat = new THREE.MeshStandardMaterial({ color: 0x4d7c43, roughness: 0.8 });
      [
          { z: 278, label: 'H' },
          { z: 298, label: 'G' }
      ].forEach(tb => {
          const groundLink = new THREE.Mesh(new THREE.BoxGeometry(520, 0.28, 10.5), taxiBridgeMat);
          groundLink.position.set(0, 0.12, tb.z);
          groundLink.receiveShadow = true;
          macroGroup.add(groundLink);

          const bridgeDeck = new THREE.Mesh(new THREE.BoxGeometry(76, 1.3, 12.5), taxiBridgeMat);
          bridgeDeck.position.set(0, 5.4, tb.z);
          bridgeDeck.castShadow = true;
          bridgeDeck.receiveShadow = true;
          macroGroup.add(bridgeDeck);

          [-5.2, 5.2].forEach(sz => {
              const shoulder = new THREE.Mesh(new THREE.BoxGeometry(76, 1.36, 1.6), shoulderGreenMat);
              shoulder.position.set(0, 5.42, tb.z + sz);
              macroGroup.add(shoulder);
          });

          const yellowCenter = new THREE.Mesh(
              new THREE.BoxGeometry(520, 0.12, 0.45),
              new THREE.MeshBasicMaterial({ color: 0xfacc15 })
          );
          yellowCenter.position.set(0, 6.08, tb.z);
          macroGroup.add(yellowCenter);
      });

      const trainGroup = new THREE.Group();
      const carriageGeo = new THREE.BoxGeometry(2.6, 2.4, 14);
      const carriageMat = new THREE.MeshStandardMaterial({ color: 0xf8fafc, roughness: 0.2, metalness: 0.4 });
      const stripeMat = new THREE.MeshBasicMaterial({ color: 0x0284c7 });
      for (let c = 0; c < 4; c++) {
          const car = new THREE.Mesh(carriageGeo, carriageMat);
          car.position.z = (c - 1.5) * 15.2;
          car.position.y = 1.6;
          trainGroup.add(car);
          const stripe = new THREE.Mesh(new THREE.BoxGeometry(2.7, 0.45, 14), stripeMat);
          stripe.position.set(0, 1.8, car.position.z);
          trainGroup.add(stripe);
      }
      for (const side of [-1, 1]) {
          const cab = new THREE.Mesh(new THREE.BoxGeometry(2.2, .66, .08), darkRoofMat);
          cab.position.set(0, 2.03, side * 29.84); trainGroup.add(cab);
          const rail = new THREE.Mesh(new THREE.BoxGeometry(.12, .16, 435), darkRoofMat);
          rail.position.set(3.7 + side * .86, .16, -537.5); macroGroup.add(rail);
      }
      macroGroup.add(trainGroup);
      intercityTrains.push({
          mesh: trainGroup,
          start: new THREE.Vector3(3.7, -.12, -355),
          end: new THREE.Vector3(3.7, -.12, -720),
          t: 0.2,
          speed: 0.0022
      });

      scene.add(macroGroup);
  }

          function buildLandscapeAndTraffic() {
              const landGroup = new THREE.Group(); landGroup.name = 'Gardens / viaducts';

              const heartLawnMat = new THREE.MeshStandardMaterial({ color: 0x3b6e38, roughness: 0.82 });
              const walkwayMat = new THREE.MeshStandardMaterial({ color: 0xd6d3d1, roughness: 0.65 });

              const leftLawnShape = new THREE.Shape();
              leftLawnShape.moveTo(-10, -14);
              leftLawnShape.quadraticCurveTo(-72, -14, -68, -48);
              leftLawnShape.quadraticCurveTo(-64, -82, -12, -88);
              leftLawnShape.quadraticCurveTo(-6, -50, -10, -14);
              const leftLawnGeo = new THREE.ExtrudeGeometry(leftLawnShape, { depth: 1.1, bevelEnabled: true, bevelSize: 0.6, bevelThickness: 0.4 });
              leftLawnGeo.rotateX(-Math.PI / 2);
              const leftLawnMesh = new THREE.Mesh(leftLawnGeo, heartLawnMat);
              leftLawnMesh.position.y = 0.25;
              leftLawnMesh.receiveShadow = true;
              landGroup.add(leftLawnMesh);

              const rightLawnShape = new THREE.Shape();
              rightLawnShape.moveTo(10, -14);
              rightLawnShape.quadraticCurveTo(72, -14, 68, -48);
              rightLawnShape.quadraticCurveTo(64, -82, 12, -88);
              rightLawnShape.quadraticCurveTo(6, -50, 10, -14);
              const rightLawnGeo = new THREE.ExtrudeGeometry(rightLawnShape, { depth: 1.1, bevelEnabled: true, bevelSize: 0.6, bevelThickness: 0.4 });
              rightLawnGeo.rotateX(-Math.PI / 2);
              const rightLawnMesh = new THREE.Mesh(rightLawnGeo, heartLawnMat);
              rightLawnMesh.position.y = 0.25;
              rightLawnMesh.receiveShadow = true;
              landGroup.add(rightLawnMesh);

              [-48, -32, -18, 18, 32, 48].forEach(wx => {
                  const stripLen = 62 - Math.abs(wx) * 0.65;
                  const vWalk = new THREE.Mesh(new THREE.BoxGeometry(0.9, 0.15, stripLen), walkwayMat);
                  vWalk.position.set(wx, 1.72, 48);
                  landGroup.add(vWalk);
              });
              [28, 48, 68].forEach(wz => {
                  [-38, 38].forEach(sideX => {
                      const hWalk = new THREE.Mesh(new THREE.BoxGeometry(44, 0.15, 0.85), walkwayMat);
                      hWalk.position.set(sideX, 1.72, wz);
                      hWalk.rotation.y = sideX < 0 ? 0.18 : -0.18;
                      landGroup.add(hWalk);
                  });
              });

              const frontRoads = new THREE.Group(); frontRoads.name = 'Front road network v7'; landGroup.add(frontRoads);
      frontRoadLayers.forEach(L => {
                  const mat = new THREE.MeshStandardMaterial({ color: L.c, roughness: 0.62, metalness: 0.05, polygonOffset: true, polygonOffsetFactor: -L.y * 4, polygonOffsetUnits: -L.y * 4 });
                  L.p.forEach(rings => {
                      const sh = new THREE.Shape(rings[0].map(a => new THREE.Vector2(a[0], -a[1])));
                      for (let k = 1; k < rings.length; k++) sh.holes.push(new THREE.Path(rings[k].map(a => new THREE.Vector2(a[0], -a[1]))));
                      const g = new THREE.ExtrudeGeometry(sh, { depth: 0.12, bevelEnabled: false });
                      g.rotateX(-Math.PI / 2);
                      const m = new THREE.Mesh(g, mat);
                      m.position.y = L.y; m.receiveShadow = true;
                      frontRoads.add(m);
                  });
              });
      const deckH = (function () {
          const sm = (t: number) => t <= 0 ? 0 : (t >= 1 ? 1 : t * t * (3 - 2 * t));
          const hZ = (zone: number, z: number) => zone === 3 ? 8.0 * sm((122 - z) / 52) : 4.0 * sm((214 - z) / 26);
          const BASE = 0.69, TH = 0.75, RAIL = 0.45, MAXE = 2.6;
          const pos: number[] = [], col: number[] = [];
          const C = { y: new THREE.Color(0xfef9c3), o: new THREE.Color(0xfdba74), wall: new THREE.Color(0x57534e), rail: new THREE.Color(0xe2e8f0), bot: new THREE.Color(0x44403c) };
          const tri = (a: number[], b: number[], c: number[], cl: THREE.Color) => { pos.push(...a, ...b, ...c); for (let k = 0; k < 3; k++) col.push(cl.r, cl.g, cl.b); };
          const quad = (a: number[], b: number[], c: number[], d: number[], cl: THREE.Color) => { tri(a, b, c, cl); tri(a, c, d, cl); };
          frontRoadDecks.forEach(D => {
              const zone = D.z, cl = D.c ? C.o : C.y, rings = D.p;
              const Y = (z: number) => BASE + hZ(zone, z);
              const contour = rings[0].map(a => new THREE.Vector2(a[0], a[1]));
              const holes = rings.slice(1).map(r => r.map(a => new THREE.Vector2(a[0], a[1])));
              const verts = contour.concat(...holes);
              const tris = THREE.ShapeUtils.triangulateShape(contour, holes);
              const d2 = (p: number[], q: number[]) => Math.hypot(p[0] - q[0], p[1] - q[1]);
              tris.forEach(t => {
                  const stack: number[][][] = [[[verts[t[0]].x, verts[t[0]].y], [verts[t[1]].x, verts[t[1]].y], [verts[t[2]].x, verts[t[2]].y]]];
                  while (stack.length) {
                      const [p, q, r] = stack.pop()!;
                      const a = d2(p, q), b = d2(q, r), c = d2(r, p), m = Math.max(a, b, c);
                      if (m <= MAXE) {
                          const P = (v: number[]) => [v[0], Y(v[1]), v[1]];
                          tri(P(p), P(r), P(q), cl);
                          if (Y(p[1]) > 1.0 || Y(q[1]) > 1.0 || Y(r[1]) > 1.0) { const B = (v: number[]) => [v[0], Y(v[1]) - TH, v[1]]; tri(B(p), B(q), B(r), C.bot); }
                          continue;
                      }
                      const mid = (u: number[], v: number[]) => [(u[0] + v[0]) / 2, (u[1] + v[1]) / 2];
                      if (a === m) { const n = mid(p, q); stack.push([p, n, r], [n, q, r]); }
                      else if (b === m) { const n = mid(q, r); stack.push([p, q, n], [p, n, r]); }
                      else { const n = mid(r, p); stack.push([p, q, n], [n, q, r]); }
                  }
              });
              rings.forEach(ring => {
                  for (let i = 0; i < ring.length; i++) {
                      const p = ring[i], q = ring[(i + 1) % ring.length];
                      const L = Math.hypot(q[0] - p[0], q[1] - p[1]), n = Math.max(1, Math.ceil(L / 2));
                      for (let s = 0; s < n; s++) {
                          const x1 = p[0] + (q[0] - p[0]) * s / n, z1 = p[1] + (q[1] - p[1]) * s / n;
                          const x2 = p[0] + (q[0] - p[0]) * (s + 1) / n, z2 = p[1] + (q[1] - p[1]) * (s + 1) / n;
                          const y1 = Y(z1), y2 = Y(z2);
                          if (y1 < 1.0 && y2 < 1.0) continue;
                          quad([x1, y1, z1], [x2, y2, z2], [x2, y2 - TH, z2], [x1, y1 - TH, z1], C.wall);
                          quad([x1, y1, z1], [x2, y2, z2], [x2, y2 + RAIL, z2], [x1, y1 + RAIL, z1], C.rail);
                      }
                  }
              });
          });
          const g = new THREE.BufferGeometry();
          g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
          g.setAttribute('color', new THREE.Float32BufferAttribute(col, 3));
          g.computeVertexNormals();
          const dm = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.68, metalness: 0.05, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -3, polygonOffsetUnits: -3 }));
          dm.name = 'Front elevated roads v7'; dm.castShadow = true; dm.receiveShadow = true; landGroup.add(dm);
          const piers = new THREE.Group(); piers.name = 'Front viaduct piers'; landGroup.add(piers);
          const pm = new THREE.MeshStandardMaterial({ color: 0xcbd5e1, roughness: 0.8 });
          frontRoadPiers.forEach(p => { const h = BASE + hZ(p[2], p[1]) - TH; const m = new THREE.Mesh(new THREE.CylinderGeometry(0.9, 1.1, h, 12), pm); m.position.set(p[0], h / 2, p[1]); m.castShadow = true; piers.add(m); });
          buildings.push(piers);
          const inRing = (x: number, z: number, ring: number[][]) => { let ins = false; for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) { const a = ring[i], b = ring[j]; if (((a[1] > z) !== (b[1] > z)) && (x < (b[0] - a[0]) * (z - a[1]) / (b[1] - a[1]) + a[0])) ins = !ins; } return ins; };
          const onSurface = (x: number, z: number, rings: number[][][]) => inRing(x, z, rings[0]) && !rings.slice(1).some(h => inRing(x, z, h));
          return (x: number, z: number) => {
              let height: number | undefined;
              for (const D of frontRoadDecks) if (onSurface(x, z, D.p)) height = Math.max(height ?? 0, BASE + hZ(D.z, z));
              if (height !== undefined) return height;
              if (frontRoadLayers[0].p.some(rings => onSurface(x, z, rings))) return frontRoadLayers[0].y + .12;
          };
      })();
              frontRoadPaths.forEach(pp => {
                  const cv = new THREE.CatmullRomCurve3(pp.map(a => new THREE.Vector3(a[0], deckH(a[0], a[1]) ?? .52, a[1])), false, 'centripetal');
                  allRoadClearanceCurves.push({ points: cv.getPoints(36), halfWidth: 6.5 });
                  roadSplinePaths.push({ curve: cv, heightAt: deckH });
              });

              const roundaboutRing = new THREE.Mesh(
                  new THREE.TorusGeometry(5.5, 1.5, 12, 28),
                  new THREE.MeshStandardMaterial({ color: 0xfde047, roughness: 0.45 })
              );
              roundaboutRing.rotation.x = Math.PI / 2;
              roundaboutRing.position.set(-66.7, 2.65, 182.7);
              landGroup.add(roundaboutRing);

              const canopyMat = new THREE.MeshStandardMaterial({ color: 0xe2e8f0, roughness: 0.45 });
              for (let row = 0; row < 5; row++) {
                  const canopy = new THREE.Mesh(new THREE.BoxGeometry(24, 1.2, 3.2), canopyMat);
                  canopy.position.set(53, 1.2, 114 + row * 6.5);
                  canopy.castShadow = true;
                  landGroup.add(canopy);
              }
              for (let row = 0; row < 5; row++) {
                  const canopy = new THREE.Mesh(new THREE.BoxGeometry(24, 1.2, 3.2), canopyMat);
                  canopy.position.set(53, 1.2, 164 + row * 6.5);
                  canopy.castShadow = true;
                  landGroup.add(canopy);
              }

              const treeCount = 1350;
              const trunkGeo = new THREE.CylinderGeometry(0.25, 0.4, 2.2, 6);
              const trunkMat = new THREE.MeshStandardMaterial({ color: 0x5c4033, roughness: 0.9 });
              const crownGeo = new THREE.DodecahedronGeometry(1.85, 1);
              const crownMat = new THREE.MeshStandardMaterial({ color: 0x2e6f40, roughness: 0.75 });

              const trunkInst = new THREE.InstancedMesh(trunkGeo, trunkMat, treeCount);
              const crownInst = new THREE.InstancedMesh(crownGeo, crownMat, treeCount);
              trunkInst.castShadow = true;
              crownInst.castShadow = true;
              crownInst.receiveShadow = true;

              const dummy = new THREE.Object3D();
              let idx = 0;

              function placeTree(x: number, z: number, scale = 1, checkRoad = true) {
                  if (idx >= treeCount) return;
                  if (checkRoad && isPointNearAnyRoad(x, z, 1.3)) return;

                  dummy.position.set(x, 1.2 * scale, z);
                  dummy.scale.setScalar(scale);
                  dummy.updateMatrix();
                  trunkInst.setMatrixAt(idx, dummy.matrix);

                  dummy.position.set(x, 2.8 * scale, z);
                  dummy.scale.set(scale * (0.9 + random() * 0.35), scale * (0.8 + random() * 0.4), scale * (0.9 + random() * 0.35));
                  dummy.updateMatrix();
                  crownInst.setMatrixAt(idx, dummy.matrix);
                  idx++;
              }

              for (let i = 0; i < 340; i++) {
                  const side = i % 2 === 0 ? -1 : 1;
                  const rx = side * (15 + random() * 42);
                  const rz = 20 + random() * 56;
                  if (Math.hypot(rx, rz - 48) < 54) {
                      placeTree(rx, rz, 0.75 + random() * 0.55, true);
                  }
              }
              for (let i = 0; i < 420; i++) {
                  const angle = random() * Math.PI * 2;
                  const r = Math.sqrt(random());
                  const lx = -53 + Math.cos(angle) * (24 * r);
                  const lz = 152 + Math.sin(angle) * (46 * r);
                  if (Math.hypot(lx + 53, lz - 142) > 8.0) {
                      placeTree(lx, lz, 0.8 + random() * 0.6, true);
                  }
              }
              for (let i = 0; i < 440; i++) {
                  const rx = -20 + random() * 112;
                  const rz = 92 + random() * 155;
                  const inCanopy = (rx > 36 && rx < 70 && rz > 108 && rz < 198);
                  if (!inCanopy) {
                      placeTree(rx, rz, 0.8 + random() * 0.55, true);
                  }
              }
              let safetyGuard = 0;
              while (idx < treeCount && safetyGuard < 3000) {
                  safetyGuard++;
                  placeTree(118 + random() * 120, 145 + random() * 140, 0.85 + random() * 0.65, false);
              }

              trunkInst.count = idx;
              crownInst.count = idx;
              landGroup.add(trunkInst);
              landGroup.add(crownInst);

              scene.add(landGroup);
          }
  buildPure3DAirfieldAndRunways(); buildT3Terminal(); buildT2Terminal(); buildGTCAndTower();
  buildMacroDistrictBuildingsAndRoads(); buildLandscapeAndTraffic();
  scene.updateMatrixWorld(true);
  for (const building of buildings) building.traverse(object => {
    if (object instanceof THREE.Mesh) {
      object.geometry.computeBoundingBox();
      obstacles.push(object.geometry.boundingBox!.clone().applyMatrix4(object.matrixWorld));
    }
  });
  const roadTraffic = new THREE.Group(); roadTraffic.name = 'Road traffic'; scene.add(roadTraffic);
  const carMat = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: .48, metalness: .2 });
  const carColors = [0xe5e7eb, 0x5c6d77, 0xa2342f, 0xafb8bf, 0x24556d];
  const carModels = carColors.map((color, i) => roadVehicleGeometry(color, i === 0));
  for (const { curve, heightAt, offset = 0 } of roadSplinePaths) {
    const sections = trafficSections(curve, point => {
      if (heightAt) {
        const y = heightAt(point.x, point.z);
        if (y === undefined || [[1.45, 0], [-1.45, 0], [0, 1.45], [0, -1.45]].some(([x, z]) => {
          const edge = heightAt(point.x + x, point.z + z);
          return edge === undefined || Math.abs(edge - y) > .6;
        })) return false;
        point.y = y;
      }
      return clearTrafficPoint(point, obstacles, 2.5, 1.9);
    });
    for (const section of sections) {
      const count = Math.ceil(section.length / 55);
      for (let i = 0; i < count; i++) {
        const mesh = new THREE.Mesh(carModels[roadCars.length % carModels.length], carMat);
        mesh.receiveShadow = true; roadTraffic.add(mesh);
        roadCars.push({ mesh, curve, heightAt, offset, ...section, phase: (i + .5) / count, speed: heightAt ? 7 : 12 });
      }
    }
  }
  scene.scale.setScalar(AIRPORT_SCALE); scene.position.y = -.6;

  // Bake repeated static parts by material; moving vehicles keep their own transforms.
  const moving = new Set<THREE.Object3D>([...roadCars.map(c => c.mesh), ...intercityTrains.map(t => t.mesh)]);
  const originals = new Set<THREE.BufferGeometry>();
  function batch(group: THREE.Object3D) {
    const buckets = new Map<string, THREE.Mesh<THREE.BufferGeometry, THREE.Material>[]>();
    for (const child of [...group.children]) {
      if (moving.has(child)) continue;
      if (child instanceof THREE.Mesh && !(child instanceof THREE.InstancedMesh) && !Array.isArray(child.material) && !child.name) {
        const key = `${child.material.uuid}/${child.castShadow}/${child.receiveShadow}/${child.renderOrder}/${!!child.geometry.index}/${Object.keys(child.geometry.attributes).sort().join()}`;
        const items = buckets.get(key) || []; items.push(child); buckets.set(key, items);
      } else batch(child);
    }
    for (const meshes of buckets.values()) {
      if (meshes.length < 2) continue;
      const pieces = meshes.map(mesh => { mesh.updateMatrix(); return mesh.geometry.clone().applyMatrix4(mesh.matrix); });
      const geometry = mergeGeometries(pieces); pieces.forEach(g => g.dispose());
      if (!geometry) continue;
      const merged = new THREE.Mesh(geometry, meshes[0].material);
      merged.renderOrder = meshes[0].renderOrder; merged.castShadow = meshes[0].castShadow; merged.receiveShadow = meshes[0].receiveShadow; group.add(merged);
      for (const mesh of meshes) { originals.add(mesh.geometry); group.remove(mesh); }
    }
  }
  batch(scene);
  const retained = new Set<THREE.BufferGeometry>(); scene.traverse(o => { if (o instanceof THREE.Mesh) retained.add(o.geometry); });
  originals.forEach(g => { if (!retained.has(g)) g.dispose(); });
  const tangent = new THREE.Vector3();
  return Object.assign(scene, { trafficClear(position: THREE.Vector3, radius: number, height: number) {
    const local = position.clone().sub(scene.position).divideScalar(AIRPORT_SCALE);
    return clearTrafficPoint(local, obstacles, radius / AIRPORT_SCALE, height / AIRPORT_SCALE);
  }, animateTraffic(seconds: number) {
    for (const car of roadCars) {
      const distance = (car.phase + seconds * car.speed / (car.length + 15)) % 1 * (car.length + 15);
      const t = THREE.MathUtils.lerp(car.start, car.end, Math.min(distance / car.length, 1));
      const point = car.curve.getPointAt(t, car.mesh.position);
      const surface = car.heightAt ? car.heightAt(point.x, point.z) : point.y + car.offset;
      point.y = (surface ?? point.y) + .035;
      car.mesh.visible = surface !== undefined && distance <= car.length && clearTrafficPoint(point, obstacles, 2.35, 1.85);
      faceTravel(car.mesh, car.curve.getTangentAt(t, tangent));
    }
    for (const train of intercityTrains) {
      const t = (train.t + seconds * train.speed * 60) % 2;
      train.mesh.position.lerpVectors(train.start, train.end, t <= 1 ? t : 2 - t);
    }
  } });
}

export function createJetBridge(startX: number, startZ: number, endX: number, endZ: number) {
  const root = new THREE.Group();
  const steel = material(0xe2e8f0, .45, .35), joint = material(0x334155, .5, .6);
  const glass = new THREE.MeshStandardMaterial({ color: 0xbae6fd, roughness: .2, metalness: .75, emissive: 0x38bdf8, emissiveIntensity: .12 });
  function part(geometry: THREE.BufferGeometry, mat: THREE.Material, x: number, y: number, z: number, angle = 0) {
    const mesh = new THREE.Mesh(geometry, mat); mesh.position.set(x, y, z); mesh.rotation.y = angle; mesh.castShadow = true; root.add(mesh);
  }
  const dx = endX - startX, dz = endZ - startZ, length = Math.max(.1, Math.hypot(dx, dz)), angle = Math.atan2(dx, dz);
  part(new THREE.CylinderGeometry(3.75, 3.75, 7.68, 14), steel, startX, 4, startZ);
  part(new THREE.CylinderGeometry(4.05, 4.05, .64, 14), joint, startX, 8, startZ);
  part(new THREE.BoxGeometry(3.24, 1.92, length), steel, (startX + endX) / 2, 6, (startZ + endZ) / 2, angle);
  part(new THREE.BoxGeometry(3.42, .99, length * .85), glass, (startX + endX) / 2, 6.05, (startZ + endZ) / 2, angle);
  part(new THREE.CylinderGeometry(.6, .6, 5.44, 8), joint, endX, 2.88, endZ);
  part(new THREE.BoxGeometry(3.9, .51, 1.95), joint, endX, .56, endZ, angle);
  part(new THREE.BoxGeometry(3.66, 2.05, 1.86), joint, endX, 5.92, endZ, angle);
  return root;
}
export const airportLandmarks = [
  { id: 't3', title: 'T3 航站楼', sub: '双院落 / 收退式主楼前缘', x: 0, y: 82, z: -144, tone: 'gold' },
  { id: 'nw', title: 'T3 西北指廊', sub: '北侧机坪', x: -450, y: 55, z: -273 },
  { id: 'ne', title: 'T3 东北指廊', sub: '北侧机坪', x: 450, y: 55, z: -273 },
  { id: 'sw', title: 'T3 西南指廊', sub: '西港湾', x: -453, y: 55, z: 45 },
  { id: 'se', title: 'T3 东南指廊', sub: '东港湾', x: 444, y: 55, z: 69 },
  { id: 'gtc', title: 'GTC 交通中心', sub: '心形花园 / 立体高架路网', x: 0, y: 55, z: 156 },
  { id: 'tower', title: '空管塔台', sub: 'ATC / 建筑示意', x: -48, y: 215, z: -405 },
  { id: 't2', title: 'T2 航站楼', sub: '双指廊 / 保障测试区', x: -336, y: 65, z: 465, tone: 'teal' },
  { id: 'cargo', title: '北部航空货运区', sub: '货站 / 货运一道', x: -255, y: 60, z: -1455 },
  { id: 'maintenance', title: '机务维修区', sub: '机库 / 等待入库', x: 285, y: 65, z: -1320 },
  { id: 'fbo', title: '公务机楼 FBO', sub: '南侧工作区', x: -354, y: 50, z: 786 },
  { id: 'west-runway', title: '西跑道 04L / 22R', sub: '双平行滑行道', x: -1095, y: 25, z: -135 },
  { id: 'east-runway', title: '东跑道 04R / 22L', sub: '双平行滑行道', x: 1125, y: 25, z: -75 },
  { id: 's19', title: 'S19 机场二高速', sub: '南部集散路网', x: 0, y: 35, z: 1095 },
  { id: 'taxi-bridges', title: 'G / H 跨线滑行道桥', sub: '高速上跨', x: 0, y: 35, z: 864 },
];
