import { MOUSE, TOUCH, type Camera } from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

export function createApronControls(camera: Camera, element: HTMLElement) {
  const controls = new OrbitControls(camera, element);
  controls.zoomToCursor = true;
  controls.screenSpacePanning = false;
  controls.mouseButtons = { LEFT: MOUSE.PAN, MIDDLE: MOUSE.DOLLY, RIGHT: MOUSE.ROTATE };
  controls.touches = { ONE: TOUCH.PAN, TWO: TOUCH.DOLLY_ROTATE };
  controls.cursorStyle = 'grab';
  return controls;
}

export function trackMapClick(element: HTMLElement, onClick: (event: PointerEvent, stand?: string) => void) {
  const pointers = new Set<number>();
  let down: { id: number; x: number; y: number; stand?: string } | null = null, dragged = false;
  const onDown = (event: PointerEvent) => {
    pointers.add(event.pointerId);
    if (pointers.size > 1) { dragged = true; return; }
    const label = (event.target as HTMLElement).closest?.<HTMLElement>('[data-stand-code]');
    down = { id: event.pointerId, x: event.clientX, y: event.clientY, stand: label?.dataset.standCode };
    dragged = false;
  };
  const onMove = (event: PointerEvent) => {
    if (down && event.pointerId === down.id && Math.hypot(event.clientX - down.x, event.clientY - down.y) > 6) dragged = true;
  };
  const onUp = (event: PointerEvent) => {
    pointers.delete(event.pointerId);
    if (!down || down.id !== event.pointerId) return;
    const start = down; down = null;
    if (event.button === 0 && !dragged && Math.hypot(event.clientX - start.x, event.clientY - start.y) <= 6) onClick(event, start.stand);
  };
  const cancel = (event: PointerEvent) => { pointers.delete(event.pointerId); down = null; dragged = true; };
  element.addEventListener('pointerdown', onDown);
  element.ownerDocument.addEventListener('pointermove', onMove);
  element.ownerDocument.addEventListener('pointerup', onUp);
  element.addEventListener('pointercancel', cancel);
  element.addEventListener('lostpointercapture', cancel);
  return () => {
    element.removeEventListener('pointerdown', onDown);
    element.ownerDocument.removeEventListener('pointermove', onMove);
    element.ownerDocument.removeEventListener('pointerup', onUp);
    element.removeEventListener('pointercancel', cancel);
    element.removeEventListener('lostpointercapture', cancel);
  };
}
