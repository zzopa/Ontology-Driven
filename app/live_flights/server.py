"""FastAPI & WebSocket application for real-time flight telemetry and replay."""
import asyncio
import json
import logging
import time
from typing import Dict, List, Optional, Set
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.live_flights.adapters.base import FeedAdapter
from app.live_flights.adapters.hkg_live import HkgLiveAdapter
from app.live_flights.adapters.wuh_native import WuhNativeAdapter
from app.live_flights.config import BASE_DIR
from app.live_flights.models import FlightSnapshot
from app.live_flights.recorder import FlightRecorder

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("live_flights.server")


class ModeSwitchRequest(BaseModel):
    mode: str  # "hkg_live_mapped" or "wuh_native"
    snap_to_scene: Optional[bool] = True


class ReplayControlRequest(BaseModel):
    action: str  # "play", "pause", "live", "seek"
    seek_to: Optional[float] = None
    speed: Optional[float] = 1.0


class FlightDataHub:
    def __init__(self):
        self.adapters: Dict[str, FeedAdapter] = {
            "hkg_live_mapped": HkgLiveAdapter(),
            "wuh_native": WuhNativeAdapter(),
        }
        self.current_mode = "hkg_live_mapped"
        self.snap_to_scene = True
        self.recorder = FlightRecorder()
        self.active_websockets: Set[WebSocket] = set()
        self._broadcast_task: Optional[asyncio.Task] = None
        self._record_counter = 0

    @property
    def current_adapter(self) -> FeedAdapter:
        return self.adapters[self.current_mode]

    async def start(self) -> None:
        logger.info(f"Initializing FlightDataHub in mode '{self.current_mode}'...")
        await self.current_adapter.start()
        self._broadcast_task = asyncio.create_task(self._broadcast_loop())

    async def stop(self) -> None:
        if self._broadcast_task:
            self._broadcast_task.cancel()
        for adapter in self.adapters.values():
            await adapter.stop()
        logger.info("FlightDataHub stopped.")

    async def switch_mode(self, mode: str, snap_to_scene: bool = True) -> None:
        if mode not in self.adapters:
            raise ValueError(f"Unknown mode '{mode}'. Available: {list(self.adapters.keys())}")
        if mode == self.current_mode:
            self.snap_to_scene = snap_to_scene
            return

        logger.info(f"Switching feed mode from {self.current_mode} to {mode}...")
        await self.adapters[self.current_mode].stop()
        self.current_mode = mode
        self.snap_to_scene = snap_to_scene
        await self.adapters[self.current_mode].start()

    async def _broadcast_loop(self) -> None:
        while True:
            try:
                # 1. Obtain current snapshot (either from live adapter or replay player)
                if self.recorder.mode == "replay":
                    snapshot = self.recorder.tick_replay()
                    if not snapshot:
                        snapshot = await self.current_adapter.get_latest_snapshot(self.snap_to_scene)
                else:
                    snapshot = await self.current_adapter.get_latest_snapshot(self.snap_to_scene)
                    # Automatically record to SQLite every 2 seconds
                    self._record_counter += 1
                    if self._record_counter % 2 == 0 and snapshot.total_flights > 0:
                        self.recorder.record_snapshot(snapshot)

                # 2. Broadcast to connected WebSocket clients
                if self.active_websockets and snapshot:
                    payload = snapshot.model_dump_json()
                    disconnected = []
                    for ws in list(self.active_websockets):
                        try:
                            await ws.send_text(payload)
                        except Exception:
                            disconnected.append(ws)
                    for dead in disconnected:
                        self.active_websockets.discard(dead)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in broadcast loop: {e}")
            await asyncio.sleep(1.0)


hub = FlightDataHub()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await hub.start()
    yield
    await hub.stop()


app = FastAPI(
    title="Airport 3D Live Flights Stream Service",
    description="Real-time ADS-B, official schedules, coordinate projection, and SQLite replay.",
    version="2.0.0",
    lifespan=lifespan,
)

# Enable CORS for local file:// previews or any web frontend host
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def serve_3d_airport():
    html_file = BASE_DIR / "武汉天河机场_写实渲染_v10_3.html"
    return FileResponse(html_file, media_type="text/html; charset=utf-8")


@app.get("/live-flights.js")
async def serve_live_flights_js():
    js_file = BASE_DIR / "live-flights.js"
    return FileResponse(js_file, media_type="application/javascript; charset=utf-8")


@app.get("/api/health")
async def health():
    return {
        "status": "healthy",
        "mode": hub.current_mode,
        "clients": len(hub.active_websockets),
        "recorder_mode": hub.recorder.mode,
        "time": time.time(),
    }


@app.get("/api/flights/live")
async def get_live_flights():
    if hub.recorder.mode == "replay":
        snap = hub.recorder.tick_replay()
        if snap:
            return snap
    return await hub.current_adapter.get_latest_snapshot(hub.snap_to_scene)


@app.get("/api/flights/mode")
async def get_mode():
    return {
        "current_mode": hub.current_mode,
        "available_modes": list(hub.adapters.keys()),
        "snap_to_scene": hub.snap_to_scene,
        "recorder_mode": hub.recorder.mode,
    }


@app.post("/api/flights/mode")
async def set_mode(req: ModeSwitchRequest):
    await hub.switch_mode(req.mode, req.snap_to_scene if req.snap_to_scene is not None else True)
    return {
        "success": True,
        "current_mode": hub.current_mode,
        "snap_to_scene": hub.snap_to_scene,
    }


@app.get("/api/flights/stands")
async def get_stands():
    snap = await hub.current_adapter.get_latest_snapshot(hub.snap_to_scene)
    return {
        "timestamp": snap.timestamp,
        "occupied_stands": snap.stands_occupied,
        "total_occupied": len(snap.stands_occupied),
    }


@app.get("/api/replay/status")
async def replay_status():
    min_ts, max_ts, cnt = hub.recorder.get_time_range()
    return {
        "mode": hub.recorder.mode,
        "is_playing": hub.recorder.is_playing,
        "replay_time": hub.recorder.replay_time,
        "replay_speed": hub.recorder.replay_speed,
        "min_timestamp": min_ts,
        "max_timestamp": max_ts,
        "total_frames": cnt,
    }


@app.post("/api/replay/control")
async def replay_control(req: ReplayControlRequest):
    res = hub.recorder.control_player(
        action=req.action,
        seek_to=req.seek_to,
        speed=req.speed,
    )
    return {"success": True, "player": res}


@app.websocket("/ws/live")
async def websocket_live_endpoint(websocket: WebSocket):
    await websocket.accept()
    hub.active_websockets.add(websocket)
    logger.info(f"WebSocket client connected. Total clients: {len(hub.active_websockets)}")

    # Send immediate first frame
    try:
        if hub.recorder.mode == "replay":
            initial_snap = hub.recorder.tick_replay()
        else:
            initial_snap = await hub.current_adapter.get_latest_snapshot(hub.snap_to_scene)
        if initial_snap:
            await websocket.send_text(initial_snap.model_dump_json())
    except Exception as e:
        logger.error(f"Error sending initial frame to websocket: {e}")

    try:
        while True:
            # Receive commands from client (e.g. switch mode, pause, seek)
            data = await websocket.receive_text()
            try:
                cmd = json.loads(data)
                action = cmd.get("action")
                if action == "switch_mode" and "mode" in cmd:
                    await hub.switch_mode(cmd["mode"], cmd.get("snap_to_scene", True))
                elif action in ("play", "pause", "seek", "live"):
                    hub.recorder.control_player(action, cmd.get("seek_to"), cmd.get("speed"))
            except Exception as e:
                logger.warning(f"Invalid client message: {e}")
    except WebSocketDisconnect:
        hub.active_websockets.discard(websocket)
        logger.info(f"WebSocket client disconnected. Total clients: {len(hub.active_websockets)}")
    except Exception as e:
        hub.active_websockets.discard(websocket)
        logger.warning(f"WebSocket error: {e}")
