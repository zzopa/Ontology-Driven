"use client";

import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import type { OpsSnapshot, OpsPerceptionScenario } from "@/lib/ops";
import { apronMapPosition, labelTop, perceptionStandTone } from "@/lib/ops-perception";
import { airportCameraView, airportLandmarks, airportViews, buildAirport, createAircraft, createJetBridge, createVehicle } from "./apron-models";
import { createApronControls, trackMapClick } from "./apron-controls";
import s from "./perception.module.css";

export type CameraView = "overview" | "terminal" | "t3" | "map" | "macro" | "focus";
type Props = { snapshot: OpsSnapshot; scenario: OpsPerceptionScenario; selected: string; detected: boolean; recommended: boolean;
  progress: number; preview: boolean; playing: boolean; trafficPlaying: boolean; reducedMotion: boolean; allLabels: boolean;
  view: CameraView; viewRevision: number; onSelect: (stand: string) => void; onInteract: () => void };

export default function ApronScene(props: Props) {
  const host = useRef<HTMLDivElement>(null), labels = useRef<Record<string, HTMLButtonElement | null>>({});
  const landmarks = useRef<Record<string, HTMLDivElement | null>>({}), vehicleLabel = useRef<HTMLDivElement>(null);
  const latest = useRef(props); latest.current = props;
  const [unavailable, setUnavailable] = useState(false);
  const sceneKey = props.snapshot.snapshot_id + JSON.stringify(props.snapshot.stands);

  useEffect(() => {
    const container = host.current;
    if (!container || unavailable) return;
    let renderer: THREE.WebGLRenderer;
    try { renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true }); }
    catch { setUnavailable(true); return; }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.7));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;
    renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFShadowMap; renderer.shadowMap.autoUpdate = false; renderer.shadowMap.needsUpdate = true;
    container.prepend(renderer.domElement);
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0xdbeafe); scene.fog = new THREE.FogExp2(0xdbeafe, .00018);
    const camera = new THREE.PerspectiveCamera(42, 1, 10, 26000);
    camera.position.fromArray(airportViews.overview.position);
    const controls = createApronControls(camera, container);
    controls.target.fromArray(airportViews.overview.target); controls.enableDamping = true; controls.dampingFactor = .075;
    controls.maxPolarAngle = Math.PI / 2 - .07; controls.minDistance = 75; controls.maxDistance = 22000;
    scene.add(new THREE.AmbientLight(0xffffff, .75));
    scene.add(new THREE.HemisphereLight(0xe0f2fe, 0x718143, 1.1));
    const sun = new THREE.DirectionalLight(0xfffbeb, 3.1); sun.position.set(660, 930, 420); sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048); sun.shadow.camera.left = -1350; sun.shadow.camera.right = 900; sun.shadow.camera.top = 750; sun.shadow.camera.bottom = -750; sun.shadow.camera.far = 2400; sun.shadow.bias = -.0005; scene.add(sun);
    const airport = buildAirport(); scene.add(airport);

    function line(points: number[][], color: number, opacity = 1) {
      const item = new THREE.Line(new THREE.BufferGeometry().setFromPoints(points.map(p => new THREE.Vector3(p[0], p[1], p[2]))), new THREE.LineBasicMaterial({ color, transparent: true, opacity }));
      scene.add(item); return item;
    }
    const pads: THREE.Mesh[] = [];
    const standObjects = new Map<string, { pad: THREE.MeshBasicMaterial; border: THREE.LineBasicMaterial; beacon: THREE.MeshBasicMaterial; halo: THREE.Mesh; position: THREE.Vector3; physical: boolean }>();
    const textures = new Set<THREE.Texture>(), aircraftModels = new Map<number, THREE.Group>(), vehicleModels = new Map<string, THREE.Group>();
    const traffic: { plane: THREE.Group; path: THREE.CurvePath<THREE.Vector3>; duration: number; code: string }[] = [];
    const serviceEffects: { marker: THREE.Mesh; kind: string }[] = [];
    for (const stand of latest.current.snapshot.stands) {
      if (!stand.scene) continue;
      const { x, z, heading } = stand.scene;
      const group = new THREE.Group(); group.position.set(x, 0, z); group.rotation.y = THREE.MathUtils.degToRad(heading); scene.add(group);
      const padMaterial = new THREE.MeshBasicMaterial({ color: 0x3ba8e7, transparent: true, opacity: 0, side: THREE.DoubleSide, depthWrite: false });
      const pad = new THREE.Mesh(new THREE.PlaneGeometry(44, 46), padMaterial); pad.rotation.x = -Math.PI / 2; pad.position.set(0, .15, 0); pad.userData.stand = stand.code; pads.push(pad); group.add(pad);
      const borderMaterial = new THREE.LineBasicMaterial({ color: 0x9f8959, transparent: true, opacity: .12 });
      const border = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(44, .1, 46)), borderMaterial); border.position.set(0, .25, 0); group.add(border);
      const lead = new THREE.Vector3((stand.scene.bridge_x ?? x) - x, 0, (stand.scene.bridge_z ?? z) - z).applyAxisAngle(new THREE.Vector3(0, 1, 0), -group.rotation.y);
      group.add(line([[lead.x, .3, lead.z], [0, .3, 0]], 0xf59e0b, .85));
      group.add(line([[-6.3, .4, 0], [6.3, .4, 0]], 0xef4444, .85));
      const canvas = document.createElement('canvas'); canvas.width = 128; canvas.height = 64;
      const ctx = canvas.getContext('2d');
      if (ctx) {
        ctx.strokeStyle = '#d4ac55'; ctx.lineWidth = 3; ctx.strokeRect(10, 4, 108, 56); ctx.font = 'bold 40px monospace'; ctx.fillStyle = '#edca75'; ctx.textAlign = 'center'; ctx.fillText(stand.code, 64, 46);
        const texture = new THREE.CanvasTexture(canvas); texture.colorSpace = THREE.SRGBColorSpace; textures.add(texture);
        const decal = new THREE.Mesh(new THREE.PlaneGeometry(10, 5), new THREE.MeshBasicMaterial({ map: texture, transparent: true, depthWrite: false }));
        decal.rotation.set(-Math.PI / 2, 0, 0); decal.position.set(0, .35, -20); group.add(decal);
      }
      if (!stand.kind) {
        const occupied = latest.current.snapshot.flights.some(f => f.stand === stand.code);
        const origin = new THREE.Vector3(stand.scene.bridge_x ?? x, 0, stand.scene.bridge_z ?? z);
        const door = new THREE.Vector3(-2.4, 0, -15).applyAxisAngle(new THREE.Vector3(0, 1, 0), group.rotation.y).add(group.position);
        if (!occupied) door.lerp(origin, .72);
        scene.add(createJetBridge(origin.x, origin.z, door.x, door.z));
        if (stand.scene.dual) {
          const shift = new THREE.Vector3(-4, 0, 0).applyAxisAngle(new THREE.Vector3(0, 1, 0), group.rotation.y);
          const second = new THREE.Vector3(-2.4, 0, -4).applyAxisAngle(new THREE.Vector3(0, 1, 0), group.rotation.y).add(group.position);
          scene.add(createJetBridge(origin.x + shift.x, origin.z + shift.z, second.x, second.z));
        }
      }
      const beaconMaterial = new THREE.MeshBasicMaterial({ color: 0xfa6578, transparent: true, opacity: 0, side: THREE.DoubleSide, depthWrite: false });
      const beacon = new THREE.Mesh(new THREE.CylinderGeometry(.6, 2, 36, 16, 1, true), beaconMaterial); beacon.position.set(18, 18, 0); group.add(beacon);
      const halo = new THREE.Mesh(new THREE.RingGeometry(21, 21.7, 48), new THREE.MeshBasicMaterial({ color: 0xf76a7f, transparent: true, opacity: .65, side: THREE.DoubleSide, depthWrite: false })); halo.rotation.x = -Math.PI / 2; halo.position.set(0, .4, 0); group.add(halo);
      const flight = latest.current.snapshot.flights.find(f => f.stand === stand.code);
      if (flight) {
        const color = flight.flight_no.startsWith('CZ') ? 0x168fc0 : flight.flight_no.startsWith('HU') ? 0xe9a43e : 0xc84660;
        if (!aircraftModels.has(color)) aircraftModels.set(color, createAircraft(color));
        const plane = aircraftModels.get(color)!.clone(true);
        plane.position.set(0, .2, 0); group.add(plane);
        if (flight.motion) {
          // shortcut: moving demo aircraft omit shadows to keep the static shadow map reusable.
          plane.traverse(item => { item.castShadow = false; }); group.remove(plane); scene.add(plane);
          const path = new THREE.CurvePath<THREE.Vector3>();
          const points = flight.motion.path.map(p => new THREE.Vector3(...p));
          for (let i = 1; i < points.length; i++) path.add(new THREE.LineCurve3(points[i - 1], points[i]));
          traffic.push({ plane, path, duration: flight.motion.duration_ms / 1000, code: stand.code });
          line(points.map(p => [p.x, .5, p.z]), flight.aircraft_state?.code === 'taking_off' ? 0xc49cf8 : 0xe0b348, .45);
          group.visible = false;
        }
        for (const [i, kind] of (stand.kind ? [] : ['fuel', 'catering', 'baggage'] as const).entries()) {
          if (!vehicleModels.has(kind)) vehicleModels.set(kind, createVehicle([0x39b9a0, 0x469ee4, 0xd5a044][i], kind));
          const vehicle = vehicleModels.get(kind)!.clone(true); vehicle.position.set(i === 2 ? -10 : 10, 0, (i - 1) * 10); group.add(vehicle);
        }
      }
      const state = flight?.aircraft_state?.code;
      if (state === 'cleaning' || state === 'fueling') {
        const marker = new THREE.Mesh(state === 'fueling' ? new THREE.SphereGeometry(1, 8, 6) : new THREE.BoxGeometry(6, .1, 1), new THREE.MeshBasicMaterial({ color: state === 'fueling' ? 0xf0bd5a : 0x61d6f5, transparent: true, opacity: .8 })); group.add(marker);
        group.add(line([[10, 3, 0], [3, 3, 0]], state === 'fueling' ? 0xf0bd5a : 0x61d6f5)); serviceEffects.push({ marker, kind: state });
      }
      standObjects.set(stand.code, { pad: padMaterial, border: borderMaterial, beacon: beaconMaterial, halo, position: new THREE.Vector3(x, 15, z), physical: !stand.kind });
    }
    const radar = new THREE.Mesh(new THREE.CircleGeometry(420, 70, 0, .16), new THREE.MeshBasicMaterial({ color: 0x31b5e7, transparent: true, opacity: .035, side: THREE.DoubleSide, depthWrite: false }));
    radar.rotation.x = -Math.PI / 2; radar.position.set(latest.current.snapshot.terminal === 'T2' ? -450 : 0, .5, latest.current.snapshot.terminal === 'T2' ? 430 : -280); scene.add(radar);
    const routeGroup = new THREE.Group(); scene.add(routeGroup);
    const truck = createVehicle(0x42daba); truck.scale.setScalar(1.2); scene.add(truck);
    let routeId = '', firstLeg: THREE.CurvePath<THREE.Vector3> | undefined, nextLeg: THREE.CurvePath<THREE.Vector3> | undefined;
    let previousRevision = -1, tween = false, manualCamera = false;
    const destination = new THREE.Vector3(), target = new THREE.Vector3();
    let width = 1, height = 1;
    const resize = new ResizeObserver(() => { width = container.clientWidth; height = container.clientHeight; if (!width || !height) return; renderer.setSize(width, height); camera.aspect = width / height; camera.updateProjectionMatrix(); if (!manualCamera) previousRevision = -1; }); resize.observe(container);
    const pointer = new THREE.Vector2(), raycaster = new THREE.Raycaster();
    const disposeClicks = trackMapClick(container, (e, stand) => {
      if (stand) { latest.current.onSelect(stand); return; }
      const rect = renderer.domElement.getBoundingClientRect(); pointer.set((e.clientX - rect.left) / rect.width * 2 - 1, -(e.clientY - rect.top) / rect.height * 2 + 1);
      raycaster.setFromCamera(pointer, camera); const hit = raycaster.intersectObjects(pads).find(item => item.object.parent?.visible !== false); if (hit) latest.current.onSelect(hit.object.userData.stand);
    });
    const interact = () => { tween = false; manualCamera = true; latest.current.onInteract(); };
    controls.addEventListener('start', interact);
    const lost = (event: Event) => { event.preventDefault(); setUnavailable(true); }; renderer.domElement.addEventListener('webglcontextlost', lost);
    let previousTime = 0, trafficTime = 0;
    function animate(time: number) {
      const delta = Math.min(.08, (time - previousTime) / 1000 || .016); previousTime = time;
      const value = latest.current;
      if (value.viewRevision !== previousRevision) {
        previousRevision = value.viewRevision; manualCamera = false;
        const pos = standObjects.get(value.selected)?.position || new THREE.Vector3();
        controls.enableRotate = value.view !== 'map';
        if (value.view === 'focus') { destination.set(pos.x + 65, 150, pos.z + 190); target.set(pos.x, 0, pos.z); }
        else { const preset = airportCameraView(value.view, height); destination.fromArray(preset.position); target.fromArray(preset.target); }
        tween = true;
      }
      if (tween) {
        const amount = value.reducedMotion ? 1 : 1 - Math.exp(-delta * 5);
        camera.position.lerp(destination, amount); controls.target.lerp(target, amount);
        if (camera.position.distanceTo(destination) < .1) tween = false;
      }
      controls.update();
      (scene.fog as THREE.FogExp2).density = .00018 * Math.min(1, 2000 / camera.position.distanceTo(controls.target));
      if (value.trafficPlaying && !value.reducedMotion) trafficTime += delta;
      airport.animateTraffic(trafficTime);
      for (const item of traffic) {
        const fraction = (trafficTime % item.duration) / item.duration;
        item.plane.position.copy(item.path.getPointAt(fraction));
        const direction = item.path.getTangentAt(fraction); item.plane.rotation.y = Math.atan2(-direction.x, -direction.z); item.plane.rotation.x = Math.asin(direction.y);
        standObjects.get(item.code)?.position.copy(item.plane.position).add(new THREE.Vector3(0, 15, 0));
      }
      for (const item of serviceEffects) { const fraction = (trafficTime % 2.5) / 2.5; item.marker.position.set(item.kind === 'fueling' ? 10 - 7 * fraction : 0, item.kind === 'fueling' ? 3 : 7, item.kind === 'cleaning' ? -10 + fraction * 20 : 0); }
      if (!value.reducedMotion && value.trafficPlaying) radar.rotation.z += delta * .3;
      const projectedLabels: { x: number; y: number }[] = [];
      for (const [code, object] of standObjects) {
        const tone = perceptionStandTone(value.scenario, code, value.detected, value.recommended, value.preview);
        const risk = tone === 'risk', recommended = tone === 'recommended';
        const color = recommended ? 0x21876a : risk ? 0xc43f5a : code === value.selected ? 0x2489b8 : 0x9f8959;
        object.border.color.setHex(color); object.border.opacity = risk || recommended || code === value.selected ? .7 : .1;
        object.pad.color.setHex(color); object.pad.opacity = code === value.selected ? .1 : risk ? .07 : 0;
        object.beacon.color.setHex(color); object.beacon.opacity = (risk || recommended) ? (value.reducedMotion || !value.playing) ? .42 : .42 + Math.sin(time / 340) * .18 : 0;
        object.halo.visible = risk || recommended || code === value.selected;
        (object.halo.material as THREE.MeshBasicMaterial).color.setHex(color);
        object.halo.scale.setScalar(value.reducedMotion || !value.playing ? 1 : 1 + Math.sin(time / 450) * .025);
        const label = labels.current[code]; if (!label) continue;
        const point = object.position.clone().project(camera);
        const x = (point.x + 1) * width / 2, anchor = (1 - point.y) * height / 2;
        const moving = value.snapshot.flights.some(f => f.stand === code && (f.motion || ['waiting_hangar', 'cleaning', 'fueling'].includes(f.aircraft_state?.code || '')));
        const numberOnly = !value.allLabels && object.physical;
        const compact = !moving && ['overview', 'map', 'macro'].includes(value.view);
        const y = numberOnly ? anchor : labelTop(x, anchor, projectedLabels, moving ? 130 : compact ? 38 : 95, compact ? 30 : 42);
        const visible = point.z < 1 && point.z > -1 && Math.abs(point.x) < 1.05 && y > 45 && y < height;
        label.style.left = `${x}px`; label.style.top = `${y}px`; label.style.setProperty('--stem', `${anchor - y + 10}px`);
        label.style.visibility = visible ? 'visible' : 'hidden';
        if (visible && !numberOnly) projectedLabels.push({ x, y });
      }
      for (const landmark of airportLandmarks) {
        const label = landmarks.current[landmark.id]; if (!label) continue;
        const point = new THREE.Vector3(landmark.x, landmark.y, landmark.z).project(camera);
        label.style.left = `${(point.x + 1) * width / 2}px`; label.style.top = `${(1 - point.y) * height / 2}px`;
        const concise = !value.allLabels && !(value.view === 'macro' ? ['t3', 'cargo', 'maintenance', 's19', 'west-runway', 'east-runway'] : ['t3', 't2', 'gtc', 'tower']).includes(landmark.id);
        label.style.visibility = value.view !== 'focus' && !concise && point.z < 1 && point.z > -1 && Math.abs(point.x) < .95 && Math.abs(point.y) < .92 ? 'visible' : 'hidden';
      }
      if (routeId !== value.scenario.id) {
        routeId = value.scenario.id;
        for (const child of [...routeGroup.children]) { const item = child as THREE.Line; item.geometry.dispose(); (item.material as THREE.Material).dispose(); routeGroup.remove(child); }
        const points = value.scenario.route_points.map(([x, z]) => new THREE.Vector3(x, 2, z));
        const path = (list: THREE.Vector3[]) => { const result = new THREE.CurvePath<THREE.Vector3>(); for (let i = 1; i < list.length; i++) result.add(new THREE.LineCurve3(list[i - 1], list[i])); return result; };
        firstLeg = points.length > 3 ? path(points.slice(0, 4)) : undefined;
        nextLeg = points.length > 3 ? path(points.slice(3)) : undefined;
        if (points.length > 1) {
          const route = new THREE.Line(new THREE.BufferGeometry().setFromPoints(points), new THREE.LineDashedMaterial({ color: 0x47e4ca, dashSize: 5, gapSize: 3 })); route.computeLineDistances(); routeGroup.add(route);
        }
      }
      routeGroup.visible = value.recommended;
      truck.visible = value.recommended && !!firstLeg && !!nextLeg;
      if (truck.visible && firstLeg && nextLeg) {
        const p = value.progress;
        const curve = p < .55 ? firstLeg : nextLeg;
        const fraction = p < .35 ? p / .35 : p < .55 ? 1 : (p - .55) / .45;
        truck.position.copy(curve.getPointAt(Math.min(1, fraction)));
        const direction = curve.getTangentAt(Math.min(1, fraction)); truck.rotation.y = Math.atan2(-direction.x, -direction.z);
      }
      if (vehicleLabel.current) {
        const point = truck.position.clone().add(new THREE.Vector3(0, 10, 0)).project(camera);
        vehicleLabel.current.style.left = `${(point.x + 1) * width / 2}px`; vehicleLabel.current.style.top = `${(1 - point.y) * height / 2 + 35}px`;
        vehicleLabel.current.style.visibility = truck.visible && value.view !== 'overview' && value.view !== 'map' && point.z < 1 && Math.abs(point.x) < .95 && Math.abs(point.y) < .95 ? 'visible' : 'hidden';
      }
      renderer.render(scene, camera);
    }
    let inView = true;
    const updateVisibility = () => { renderer.setAnimationLoop(document.hidden || !inView ? null : animate); previousTime = 0; };
    const intersection = new IntersectionObserver(entries => { inView = entries[0].isIntersecting; updateVisibility(); }); intersection.observe(container);
    document.addEventListener('visibilitychange', updateVisibility); updateVisibility();
    return () => {
      renderer.setAnimationLoop(null); resize.disconnect(); intersection.disconnect(); document.removeEventListener('visibilitychange', updateVisibility);
      disposeClicks(); controls.dispose(); renderer.domElement.removeEventListener('webglcontextlost', lost);
      const geometries = new Set<THREE.BufferGeometry>(), materials = new Set<THREE.Material>();
      scene.traverse(item => { const mesh = item as THREE.Mesh; if (mesh instanceof THREE.InstancedMesh) mesh.dispose(); if (mesh.geometry) geometries.add(mesh.geometry); if (mesh.material) for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) { materials.add(material); for (const value of Object.values(material)) if (value instanceof THREE.Texture) textures.add(value); } });
      geometries.forEach(g => g.dispose()); materials.forEach(m => m.dispose()); textures.forEach(t => t.dispose()); renderer.dispose(); renderer.domElement.remove();
    };
  }, [sceneKey, unavailable]);

  const shown = props.snapshot.stands.filter(stand => stand.scene);
  if (unavailable) return <div className={s.flatMap}><div className={s.flatTerminal}>{props.snapshot.terminal === "ALL" ? "T2 / T3" : props.snapshot.terminal} · 平面示意</div><p className={s.fallbackNote}>3D 渲染不可用，已显示同一数据的平面态势。</p>{props.snapshot.stands.filter(stand => stand.scene).map(stand => <button key={stand.code} className={s.flatStand} style={apronMapPosition(stand.code, props.snapshot)} data-risk={perceptionStandTone(props.scenario, stand.code, props.detected, props.recommended, props.preview) === 'risk'} aria-pressed={props.selected === stand.code} onClick={() => props.onSelect(stand.code)}>{stand.code}<span>{props.snapshot.flights.find(f => f.stand === stand.code)?.flight_no || '空闲'}</span></button>)}</div>;
  return <div ref={host} className={s.canvasHost} data-view={props.view} aria-label="三维机坪：左键拖拽平移、右键旋转、滚轮以鼠标位置缩放"><div className={s.sceneLabels}>{airportLandmarks.map(item => <div key={item.id} ref={node => { landmarks.current[item.id] = node; }} className={s.buildingLabel} data-tone={item.tone}><strong>{item.title}</strong><small>{item.sub}</small></div>)}{shown.map(stand => { const flight = props.snapshot.flights.find(f => f.stand === stand.code), tone = perceptionStandTone(props.scenario, stand.code, props.detected, props.recommended, props.preview); const detailed = props.allLabels || !!stand.kind; return <button key={stand.code} ref={node => { labels.current[stand.code] = node; }} className={s.sceneLabel} data-compact={!detailed} data-stand-code={stand.code} data-motion={!!flight?.motion || ["waiting_hangar", "cleaning", "fueling"].includes(flight?.aircraft_state?.code || "")} data-state={flight?.aircraft_state?.code} data-risk={tone === 'risk'} data-recommended={tone === 'recommended'} aria-pressed={props.selected === stand.code} title={`${stand.terminal} ${stand.code}机位 · ${flight ? `${flight.flight_no} · ${flight.aircraft_state?.label || '停机'}` : '未分配测试航班'}`} aria-label={`查看${stand.code}机位 ${flight?.flight_no || '空闲'}`} onClick={event => { if (event.detail === 0) props.onSelect(stand.code); }}><strong>{stand.code}</strong><span>{flight ? `${flight.flight_no} · ${flight.aircraft_state?.label || '停机'}` : '空闲机位'}</span></button>; })}<div ref={vehicleLabel} className={s.vehicleLabel}>{props.snapshot.resources.find(r => r.id === props.scenario.resource_ids[1])?.name.replace('（测试）', '') || '候选班组'} <span>模拟轨迹</span></div></div></div>;
}
