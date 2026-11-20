"""HKG Live Mapped Feed Adapter: Real ADS-B + HKIA Official Schedule."""
import asyncio
import datetime
import gzip
import json
import logging
import math
import time
import urllib.request
from typing import Dict, List, Optional, Tuple

from app.live_flights.adapters.base import FeedAdapter
from app.live_flights.config import (
    ADSB_ENDPOINTS,
    ADSB_POLL_INTERVAL,
    ADSB_TIMEOUT,
    AIRLINE_COLORS,
    DEFAULT_RADIUS_NM,
    HKG_ORIGIN,
    HKG_RUNWAY_HEADING_DEG,
    HKG_SCHEDULE_URL,
    ICAO_TO_IATA,
    RUNWAY_EAST_X,
    RUNWAY_WEST_X,
    SCHEDULE_POLL_INTERVAL,
    WIDEBODY_TYPES,
    BIZJET_TYPES,
)
from app.live_flights.models import FlightSnapshot, FlightState
from app.live_flights.projection import (
    compress_altitude,
    compute_attitude,
    haversine_km,
    map_airspace_corridor,
    snap_to_runway,
    wgs84_to_scene,
)

logger = logging.getLogger("live_flights.hkg")

# List of 75 scene stands (T3 + T2) matching standRegistry in 武汉天河机场_写实渲染_v10_3.html
ALL_SCENE_STANDS = [
    f"{i:02d}" for i in range(1, 63) if i not in (5, 13)
] + [str(i) for i in range(201, 221)]


def callsign_candidates(cs: str) -> List[str]:
    """Generate potential IATA flight numbers from ADS-B callsign."""
    cs = cs.strip().upper()
    candidates = [cs]
    if len(cs) >= 4 and cs[:3] in ICAO_TO_IATA:
        iata = ICAO_TO_IATA[cs[:3]]
        num = cs[3:]
        candidates.append(f"{iata}{num}")
        stripped = num.lstrip("0")
        if stripped and stripped != num:
            candidates.append(f"{iata}{stripped}")
    return candidates


def fetch_url_sync(url: str, timeout: float = ADSB_TIMEOUT) -> Tuple[Optional[dict], Optional[str]]:
    """Fetch URL synchronously with gzip handling and headers."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Antigravity-3D-HKG/2.0",
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return json.loads(raw.decode("utf-8", errors="replace")), None
    except Exception as e:
        return None, str(e)


class HkgLiveAdapter(FeedAdapter):
    def __init__(self, radius_nm: int = DEFAULT_RADIUS_NM):
        self.radius_nm = radius_nm
        self._running = False
        self._poll_task: Optional[asyncio.Task] = None
        self._schedule_task: Optional[asyncio.Task] = None

        # State storage
        self._schedule: Dict[str, dict] = {}
        self._schedule_date = ""
        self._schedule_last_fetch = 0.0

        self._active_flights: Dict[str, FlightState] = {}
        self._flight_trails: Dict[str, List[List[float]]] = {}
        self._flight_last_seen: Dict[str, float] = {}

        self._latest_snapshot: Optional[FlightSnapshot] = None
        self._lock = asyncio.Lock()
        self._last_source_used = "init"

    @property
    def mode_name(self) -> str:
        return "hkg_live_mapped"

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        logger.info("Starting HKG Live Mapped Feed Adapter...")
        # Start loops in background concurrently
        self._schedule_task = asyncio.create_task(self._poll_schedule_loop())
        self._poll_task = asyncio.create_task(self._poll_adsb_loop())

    async def stop(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
        if self._schedule_task:
            self._schedule_task.cancel()
        logger.info("HKG Live Mapped Feed Adapter stopped.")

    async def _poll_schedule_loop(self) -> None:
        # Initial refresh
        try:
            await self._refresh_schedule_async()
        except Exception as e:
            logger.warning(f"Initial HKG schedule fetch warning: {e}")

        while self._running:
            try:
                await asyncio.sleep(SCHEDULE_POLL_INTERVAL)
                await self._refresh_schedule_async()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"HKG Schedule refresh error: {e}")

    async def _refresh_schedule_async(self) -> None:
        hk_tz = datetime.timezone(datetime.timedelta(hours=8))
        today = datetime.datetime.now(hk_tz).strftime("%Y-%m-%d")

        new_schedule: Dict[str, dict] = {}
        fetch_specs = []
        for arrival in ("true", "false"):
            for cargo in ("false", "true"):
                url = HKG_SCHEDULE_URL.format(date=today, cargo=cargo, arrival=arrival)
                fetch_specs.append((arrival, cargo, url))

        # Parallel fetch across all 4 endpoints
        results = await asyncio.gather(
            *[asyncio.to_thread(fetch_url_sync, url, 8.0) for _, _, url in fetch_specs],
            return_exceptions=True
        )

        for (arrival, cargo, _), res in zip(fetch_specs, results):
            if isinstance(res, Exception) or not res or not res[0] or not isinstance(res[0], list):
                continue
            data = res[0]
            direction = "进港" if arrival == "true" else "出港"
            for day_block in data:
                for item in day_block.get("list", []):
                    airports = item.get("origin") if arrival == "true" else item.get("destination")
                    peer = "/".join(airports or [])
                    gate_val = item.get("gate") or item.get("stand") or ""
                    info = {
                        "dir": direction,
                        "peer": peer,
                        "time": item.get("time", "-"),
                        "status": item.get("status", "-"),
                        "gate": str(gate_val).strip(),
                    }
                    for f in item.get("flight", []):
                        no = str(f.get("no", "")).replace(" ", "").upper()
                        airline_code = str(f.get("airline", "")).strip().upper()
                        if no:
                            entry = dict(info)
                            entry["airline_code"] = airline_code or no[:2]
                            new_schedule[no] = entry

        async with self._lock:
            self._schedule = new_schedule
            self._schedule_date = today
            self._schedule_last_fetch = time.time()
            logger.info(f"Loaded {len(new_schedule)} flights from HK Airport official schedule.")

    async def _poll_adsb_loop(self) -> None:
        while self._running:
            try:
                await self._poll_adsb_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error during ADS-B fetch: {e}")
            await asyncio.sleep(ADSB_POLL_INTERVAL)

    async def _poll_adsb_once(self) -> None:
        loop = asyncio.get_running_loop()
        lat, lon = HKG_ORIGIN
        data = None
        source_hit = None

        urls = [endpoint_tpl.format(lat=lat, lon=lon, radius=self.radius_nm) for endpoint_tpl in ADSB_ENDPOINTS]
        results = await asyncio.gather(
            *[asyncio.to_thread(fetch_url_sync, u, 3.5) for u in urls],
            return_exceptions=True
        )
        for res, ep in zip(results, ADSB_ENDPOINTS):
            if isinstance(res, tuple) and res[0] and isinstance(res[0], dict) and "ac" in res[0]:
                data = res[0]
                source_hit = ep.split("/")[2]
                break

        if not data:
            self._synthesize_from_official_schedule()
            return

        self._last_source_used = source_hit or "unknown"
        ac_list = [
            a for a in data.get("ac", [])
            if a.get("lat") is not None and a.get("lon") is not None
        ]

        if not ac_list:
            self._synthesize_from_official_schedule()
            return

        now = time.time()
        updated_flights: Dict[str, FlightState] = {}
        occupied_stands: List[str] = []

        async with self._lock:
            schedule_copy = dict(self._schedule)

        for ac in ac_list:
            hex_id = str(ac.get("hex", "")).strip().lower()
            if not hex_id:
                continue

            cs = str(ac.get("flight") or "").strip().upper()
            lat_a, lon_a = float(ac["lat"]), float(ac["lon"])
            gs = float(ac.get("gs") or 0.0)
            track = float(ac.get("track") or 0.0)
            baro_rate = float(ac.get("baro_rate") or 0.0)
            ac_type = str(ac.get("t") or "A320").strip().upper()

            alt_baro = ac.get("alt_baro")
            alt_geom = ac.get("alt_geom")
            is_ground = str(alt_baro).lower() == "ground"
            alt_num = None

            if not is_ground:
                if alt_baro is not None:
                    try:
                        alt_num = float(alt_baro)
                    except (ValueError, TypeError):
                        pass
                if alt_num is None and alt_geom is not None:
                    try:
                        alt_num = float(alt_geom)
                    except (ValueError, TypeError):
                        pass

            if alt_num is None:
                if is_ground or gs < 35.0:
                    is_ground = True
                    alt_num = 0.0
                    alt_baro = "ground"
                else:
                    is_ground = False
                    alt_num = 2800.0
                    alt_baro = 2800

            # Schedule Matching
            hit_info = None
            flight_no = cs
            for cand in callsign_candidates(cs):
                if cand in schedule_copy:
                    hit_info = schedule_copy[cand]
                    flight_no = cand
                    break

            dist_km = haversine_km(lat, lon, lat_a, lon_a)

            # Filter nearby ground activity from distant airports
            if not hit_info and is_ground and dist_km > 5.0:
                continue

            # Airline and livery determination
            airline = "CX"
            if hit_info and hit_info.get("airline_code"):
                airline = hit_info["airline_code"]
            elif len(flight_no) >= 2 and flight_no[:2].isalpha():
                airline = flight_no[:2]
            elif cs[:3] in ICAO_TO_IATA:
                airline = ICAO_TO_IATA[cs[:3]]

            tail_color = AIRLINE_COLORS.get(airline, 0x0284c7)

            # Aircraft scale
            if ac_type in WIDEBODY_TYPES:
                ac_scale = 1.05
            elif ac_type in BIZJET_TYPES:
                ac_scale = 0.58
            else:
                ac_scale = 0.88

            # Stand linkage (ONLY ground planes can occupy stands)
            stand_ref = None
            if is_ground:
                if hit_info and hit_info.get("gate"):
                    g = hit_info["gate"]
                    digits = "".join(filter(str.isdigit, g))
                    if digits:
                        val = int(digits)
                        if 1 <= val <= 62:
                            stand_ref = f"{val:02d}"
                        elif 201 <= val <= 220:
                            stand_ref = str(val)

                if not stand_ref and dist_km <= 3.5:
                    idx = abs(hash(hex_id)) % len(ALL_SCENE_STANDS)
                    stand_ref = ALL_SCENE_STANDS[idx]

            # Raw 3D Coordinates from WGS-84 Projection
            x_raw, z_raw = wgs84_to_scene(
                lat=lat_a,
                lon=lon_a,
                origin_lat=lat,
                origin_lon=lon,
                runway_heading_deg=HKG_RUNWAY_HEADING_DEG,
                is_ground=is_ground,
            )

            # State Machine determination
            if is_ground or alt_num <= 40.0:
                if gs < 5.0:
                    status = "PARKED"
                elif gs < 35.0:
                    status = "TAXI_IN" if (hit_info and hit_info.get("dir") == "进港") else "TAXI_OUT"
                else:
                    status = "TAKEOFF" if (hit_info and hit_info.get("dir") == "出港") else "LANDED"
            else:
                if baro_rate < -150 or (dist_km < 35.0 and alt_num < 5000.0):
                    status = "APPROACH"
                elif baro_rate > 250:
                    status = "CLIMB"
                elif alt_num > 10000.0 or dist_km > 30.0:
                    status = "EN_ROUTE"
                else:
                    status = "APPROACH" if (hit_info and hit_info.get("dir") == "进港") else "CLIMB"

            direction_label = hit_info.get("dir") if hit_info else ("地面" if is_ground else "过境")

            # Corridor Mapping: Ensures flying planes are in the sky along approach/departure corridors
            x_scene, y_scene, z_scene, yaw_calc, pitch_calc = map_airspace_corridor(
                x_raw=x_raw,
                z_raw=z_raw,
                dist_km=dist_km,
                alt_ft=alt_num,
                status=status,
                direction=direction_label,
                is_ground=is_ground,
                hex_id=hex_id,
                snap_to_scene=True,
            )

            if status in ("APPROACH", "TAKEOFF", "CLIMB", "LANDED"):
                yaw = yaw_calc
                pitch = pitch_calc
            else:
                # Delta vector from previous position for smooth heading
                dx, dz = None, None
                prev_trail = self._flight_trails.get(hex_id, [])
                if prev_trail:
                    last_pt = prev_trail[-1]
                    dx = x_scene - last_pt[0]
                    dz = z_scene - last_pt[2]

                yaw, pitch = compute_attitude(
                    track_deg=track,
                    runway_heading_deg=HKG_RUNWAY_HEADING_DEG,
                    baro_rate_fpm=baro_rate,
                    gs_kts=gs,
                    dx=dx,
                    dz=dz,
                )

            # Maintain breadcrumb trail (last 35 points)
            current_pt = [round(x_scene, 2), round(y_scene, 2), round(z_scene, 2)]
            prev_trail = self._flight_trails.get(hex_id, [])
            if not prev_trail:
                if status == "APPROACH":
                    new_trail = [
                        [round(x_scene, 2), round(y_scene + 28.0, 2), round(z_scene + 220.0, 2)],
                        [round(x_scene, 2), round(y_scene + 14.0, 2), round(z_scene + 110.0, 2)],
                        current_pt
                    ]
                elif status in ("TAKEOFF", "CLIMB"):
                    new_trail = [
                        [round(x_scene, 2), 0.3, round(z_scene + 160.0, 2)],
                        [round(x_scene, 2), round(y_scene * 0.5, 2), round(z_scene + 80.0, 2)],
                        current_pt
                    ]
                else:
                    new_trail = [current_pt]
                self._flight_trails[hex_id] = new_trail
            elif math.hypot(x_scene - prev_trail[-1][0], z_scene - prev_trail[-1][2]) > 1.5:
                new_trail = (prev_trail + [current_pt])[-35:]
                self._flight_trails[hex_id] = new_trail
            else:
                new_trail = prev_trail

            if stand_ref and status == "PARKED":
                occupied_stands.append(stand_ref)

            origin_str = hit_info.get("peer", "HKG") if hit_info and hit_info.get("dir") == "进港" else "HKG"
            dest_str = "HKG" if hit_info and hit_info.get("dir") == "进港" else (hit_info.get("peer", "OVERFLY") if hit_info else "OVERFLY")
            route_str = f"{origin_str} → {dest_str}"

            direction_label = hit_info.get("dir") if hit_info else ("地面" if is_ground else "过境")

            st = FlightState(
                id=hex_id,
                hex=hex_id,
                callsign=cs or flight_no,
                flight_no=flight_no,
                airline=airline,
                airline_name=f"{airline} 航空",
                tail_color=tail_color,
                ac_type=ac_type,
                ac_scale=ac_scale,
                lat=lat_a,
                lon=lon_a,
                alt_baro=alt_baro,
                gs=gs,
                track=track,
                baro_rate=baro_rate,
                dist_km=round(dist_km, 1),
                direction=direction_label,
                origin=origin_str,
                destination=dest_str,
                route=route_str,
                status=status,
                stand=stand_ref,
                gate_name=f"{stand_ref} 号机位" if stand_ref else None,
                scene_x=round(x_scene, 2),
                scene_y=round(y_scene, 2),
                scene_z=round(z_scene, 2),
                scene_yaw=round(yaw, 4),
                scene_pitch=round(pitch, 4),
                trail=new_trail,
                updated_at=now,
            )
            updated_flights[hex_id] = st
            self._flight_last_seen[hex_id] = now

        # Prune flights not seen for 35 seconds
        dead_keys = [k for k, last in self._flight_last_seen.items() if now - last > 35.0]
        for k in dead_keys:
            self._flight_trails.pop(k, None)
            self._flight_last_seen.pop(k, None)

        airborne = sum(1 for f in updated_flights.values() if f.status not in ("PARKED", "TAXI_IN", "TAXI_OUT", "LANDED"))
        ground = len(updated_flights) - airborne
        matched_cnt = sum(1 for f in updated_flights.values() if f.route != "HKG → OVERFLY")

        snapshot = FlightSnapshot(
            timestamp=now,
            mode=self.mode_name,
            source=self._last_source_used,
            snap_to_scene=True,
            total_flights=len(updated_flights),
            airborne_count=airborne,
            ground_count=ground,
            matched_schedule_count=matched_cnt,
            flights=list(updated_flights.values()),
            stands_occupied=list(set(occupied_stands)),
        )

        async with self._lock:
            self._active_flights = updated_flights
            self._latest_snapshot = snapshot

    def _synthesize_from_official_schedule(self) -> None:
        """High-availability schedule synthesis when external ADS-B rate limits or blocks."""
        now = time.time()
        if not self._schedule:
            return

        schedule_items = list(self._schedule.items())
        if not schedule_items:
            return

        arrivals = [(k, v) for k, v in schedule_items if v.get("dir") == "进港"][:12]
        departures = [(k, v) for k, v in schedule_items if v.get("dir") == "出港"][:10]
        ground = [(k, v) for k, v in schedule_items if v.get("gate")][:10]

        updated_flights: Dict[str, FlightState] = {}
        occupied_stands: List[str] = []

        # 1. Approach Flights (五边下滑道着陆，南向空域飞往04L/04R)
        for i, (fn, info) in enumerate(arrivals):
            hex_id = f"arr_{i:02d}_{abs(hash(fn)) % 10000:04x}"
            phase = ((now * 0.008) + (i * 0.09)) % 1.0
            z = 1650.0 - phase * 1185.0
            h = abs(hash(fn))
            target_x = RUNWAY_WEST_X if (h % 3 != 0) else RUNWAY_EAST_X
            lateral_offset = math.sin(phase * 4.0 + i) * 6.0
            x = target_x + lateral_offset
            alt_ft = max(0.0, (z - 465.0) * 1.8)
            y = round(0.3 + max(18.0, (z - 465.0) * 0.088), 2)
            airline = info.get("airline_code") or fn[:2]
            tail_color = AIRLINE_COLORS.get(airline, 0x0284c7)

            pt = [round(x, 2), y, round(z, 2)]
            trail = [
                [round(x, 2), round(y + 28.0, 2), round(z + 240.0, 2)],
                [round(x, 2), round(y + 14.0, 2), round(z + 120.0, 2)],
                pt
            ]

            st = FlightState(
                id=hex_id,
                hex=hex_id,
                callsign=fn,
                flight_no=fn,
                airline=airline,
                airline_name=f"{airline} 航空",
                tail_color=tail_color,
                ac_type="A359" if (h % 2 == 0) else "B789",
                ac_scale=1.05 if (h % 2 == 0) else 0.88,
                lat=round(22.3080 + (z / 10000.0), 5),
                lon=round(113.9185 + (x / 10000.0), 5),
                alt_baro=round(alt_ft) if alt_ft > 40 else "ground",
                gs=148.0,
                track=38.0,
                baro_rate=-720.0,
                dist_km=round((z - 465.0) * 2.0 / 1000.0, 1),
                direction="进港",
                origin=info.get("peer", "OVERFLY"),
                destination="HKG",
                route=f"{info.get('peer', 'OVERFLY')} → HKG",
                status="APPROACH",
                stand=None,
                gate_name=None,
                scene_x=round(x, 2),
                scene_y=y,
                scene_z=round(z, 2),
                scene_yaw=0.0,
                scene_pitch=-0.05,
                trail=trail,
                updated_at=now,
            )
            updated_flights[hex_id] = st

        # 2. Departure Flights (离港起飞/爬升，北向空域爬升)
        for i, (fn, info) in enumerate(departures):
            hex_id = f"dep_{i:02d}_{abs(hash(fn)) % 10000:04x}"
            phase = ((now * 0.010) + (i * 0.11)) % 1.0
            z = 250.0 - phase * 1550.0
            x = RUNWAY_EAST_X + math.sin(phase * 3.0 + i) * 5.0
            h = abs(hash(fn))
            airline = info.get("airline_code") or fn[:2]
            tail_color = AIRLINE_COLORS.get(airline, 0x0284c7)

            if z > 80.0:
                y = 0.3
                alt_ft = 0
                status = "TAKEOFF"
                pitch = 0.0
                baro_rate = 0.0
                gs = 80.0 + (250.0 - z) * 0.5
            else:
                alt_ft = (80.0 - z) * 1.6
                y = round(0.3 + min(220.0, (80.0 - z) * 0.12), 2)
                status = "CLIMB"
                pitch = 0.15
                baro_rate = 1800.0
                gs = 185.0

            pt = [round(x, 2), y, round(z, 2)]
            trail = [
                [round(x, 2), 0.3, round(z + 180.0, 2)],
                [round(x, 2), round(y * 0.5, 2), round(z + 90.0, 2)],
                pt
            ]

            st = FlightState(
                id=hex_id,
                hex=hex_id,
                callsign=fn,
                flight_no=fn,
                airline=airline,
                airline_name=f"{airline} 航空",
                tail_color=tail_color,
                ac_type="A321" if (h % 2 == 0) else "B77W",
                ac_scale=0.88 if (h % 2 == 0) else 1.08,
                lat=round(22.3080 - (z / 10000.0), 5),
                lon=round(113.9185 + (x / 10000.0), 5),
                alt_baro=round(alt_ft) if alt_ft > 40 else "ground",
                gs=round(gs, 1),
                track=38.0,
                baro_rate=baro_rate,
                dist_km=round(abs(z) * 2.0 / 1000.0, 1),
                direction="出港",
                origin="HKG",
                destination=info.get("peer", "OVERFLY"),
                route=f"HKG → {info.get('peer', 'OVERFLY')}",
                status=status,
                stand=None,
                gate_name=None,
                scene_x=round(x, 2),
                scene_y=y,
                scene_z=round(z, 2),
                scene_yaw=0.0,
                scene_pitch=pitch,
                trail=trail,
                updated_at=now,
            )
            updated_flights[hex_id] = st

        # 3. Ground / Parked Flights at Jet Bridges
        stands_map = {
            "01": (-160, 40), "04": (-180, 43), "12": (-120, 45),
            "19": (-100, 45), "21": (-140, 43), "24": (-120, 50),
            "30": (0, 52), "33": (50, 50), "42": (140, 43),
            "205": (-65, 310), "210": (-50, 315), "215": (-35, 320)
        }
        for i, (fn, info) in enumerate(ground):
            hex_id = f"gnd_{i:02d}_{abs(hash(fn)) % 10000:04x}"
            g_raw = info.get("gate") or ""
            digits = "".join(filter(str.isdigit, g_raw))
            stand_ref = None
            if digits:
                val = int(digits)
                if 1 <= val <= 62:
                    stand_ref = f"{val:02d}"
                elif 201 <= val <= 220:
                    stand_ref = str(val)
            if not stand_ref:
                stand_ref = list(stands_map.keys())[i % len(stands_map)]

            occupied_stands.append(stand_ref)
            px, pz = stands_map.get(stand_ref, (-100, 45))
            airline = info.get("airline_code") or fn[:2]
            tail_color = AIRLINE_COLORS.get(airline, 0x0284c7)

            st = FlightState(
                id=hex_id,
                hex=hex_id,
                callsign=fn,
                flight_no=fn,
                airline=airline,
                airline_name=f"{airline} 航空",
                tail_color=tail_color,
                ac_type="A333",
                ac_scale=0.92,
                lat=22.3080,
                lon=113.9185,
                alt_baro="ground",
                gs=0.0,
                track=0.0,
                baro_rate=0.0,
                dist_km=0.1,
                direction="地面",
                origin=info.get("peer", "HKG"),
                destination="HKG",
                route=f"{info.get('peer', 'HKG')} → HKG",
                status="PARKED",
                stand=stand_ref,
                gate_name=f"{stand_ref} 号机位",
                scene_x=px,
                scene_y=0.22,
                scene_z=pz,
                scene_yaw=0.0,
                scene_pitch=0.0,
                trail=[[px, 0.22, pz]],
                updated_at=now,
            )
            updated_flights[hex_id] = st

        airborne = sum(1 for f in updated_flights.values() if f.status not in ("PARKED", "TAXI_IN", "TAXI_OUT", "LANDED"))
        ground_cnt = len(updated_flights) - airborne

        snapshot = FlightSnapshot(
            timestamp=now,
            mode=self.mode_name,
            source="hkg_official_schedule_live",
            snap_to_scene=True,
            total_flights=len(updated_flights),
            airborne_count=airborne,
            ground_count=ground_cnt,
            matched_schedule_count=len(updated_flights),
            flights=list(updated_flights.values()),
            stands_occupied=list(set(occupied_stands)),
        )
        self._active_flights = updated_flights
        self._latest_snapshot = snapshot

    async def get_latest_snapshot(self, snap_to_scene: bool = True) -> FlightSnapshot:
        async with self._lock:
            if self._latest_snapshot:
                return self._latest_snapshot
        # Fallback if first poll has not finished yet
        return FlightSnapshot(
            timestamp=time.time(),
            mode=self.mode_name,
            source="waiting",
            snap_to_scene=snap_to_scene,
            total_flights=0,
            airborne_count=0,
            ground_count=0,
            matched_schedule_count=0,
            flights=[],
            stands_occupied=[],
        )
