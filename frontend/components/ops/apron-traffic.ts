import * as THREE from "three";

export function faceTravel(model: THREE.Object3D, tangent: THREE.Vector3) {
  // Vehicles and aircraft are modeled with their nose on the local -Z axis.
  model.rotation.set(Math.atan2(tangent.y, Math.hypot(tangent.x, tangent.z)), Math.atan2(-tangent.x, -tangent.z), 0, 'YXZ');
}

export function clearTrafficPoint(point: THREE.Vector3, obstacles: THREE.Box3[], radius: number, height: number) {
  return obstacles.every(({ min, max }) => point.y >= max.y || point.y + height <= min.y
    || point.x + radius <= min.x || point.x - radius >= max.x
    || point.z + radius <= min.z || point.z - radius >= max.z);
}

export function trafficSections(curve: THREE.Curve<THREE.Vector3>, isClear: (position: THREE.Vector3) => boolean) {
  const length = curve.getLength(), samples = Math.max(2, Math.ceil(length));
  const sections: { start: number; end: number; length: number }[] = [];
  let start = -1;
  for (let i = 0; i <= samples + 1; i++) {
    if (i <= samples && isClear(curve.getPointAt(i / samples))) {
      if (start < 0) start = i;
    } else if (start >= 0) {
      const end = i - 1, span = (end - start) / samples * length;
      if (span >= 18) sections.push({ start: start / samples, end: end / samples, length: span });
      start = -1;
    }
  }
  return sections;
}

export function serviceBayRoute(points: [number, number][]) {
  if (points.length !== 7) return null;
  const [origin, entry, approach, stand, , exit, next] = points;
  const side = Math.sign(approach[1] - stand[1]) || 1;
  const heading = Math.sign(stand[0] - origin[0]) || 1;
  const stopZ = stand[1] + side * 37;
  // Pull alongside the apron edge and leave forward after a stand-side turn.
  const layout = [
    [origin[0], origin[1] + side * 39], entry,
    [stand[0] - heading * 20, approach[1]], [stand[0] - heading * 13, stopZ + side * 6],
    [stand[0], stopZ], [stand[0] + heading * 13, stopZ + side * 6],
    [stand[0] + heading * 21, exit[1] + side * 15], [exit[0], exit[1] + side * 15],
    [next[0], next[1] + side * 39],
  ].filter((point, i, all) => i === 0 || point[0] !== all[i - 1][0] || point[1] !== all[i - 1][1]);
  const path = new THREE.CatmullRomCurve3(layout.map(([x, z]) => new THREE.Vector3(x, .08, z)), false, 'centripetal');
  path.arcLengthDivisions = (layout.length - 1) * 24;
  const lengths = path.getLengths(), stopIndex = layout.findIndex(([x, z]) => x === stand[0] && z === stopZ);
  return { path, stop: lengths[stopIndex * 24] / lengths[lengths.length - 1] };
}
