"""Coordinate transformations, visual airspace compression, and attitude calculation."""
import math
from typing import Optional, Tuple
from app.live_flights.config import (
    METERS_PER_UNIT,
    RUNWAY_WEST_X,
    RUNWAY_EAST_X,
    RUNWAY_Z_MIN,
    RUNWAY_Z_MAX,
)

AIRPORT_SURFACE_RADIUS = 600.0   # Ground and apron boundary (1:1 uncompressed scale)
AIRSPACE_MAX_RADIUS = 2950.0      # Safe visual airspace horizon (well within camera.far = 3800)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate Great Circle distance in km between two WGS-84 coordinates."""
    r = 6371.0
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2.0) ** 2
    )
    return 2.0 * r * math.asin(math.sqrt(max(0.0, min(1.0, a))))


def wgs84_to_scene(
    lat: float,
    lon: float,
    origin_lat: float,
    origin_lon: float,
    runway_heading_deg: float,
    meters_per_unit: float = METERS_PER_UNIT,
    is_ground: bool = False,
) -> Tuple[float, float]:
    """
    Convert WGS-84 (lat, lon) to 3D scene local coordinates (x_scene, z_scene).
    Aligns the runway heading to the scene -Z axis.
    Airborne flights in the 35nm radar zone are smoothly mapped into the visual sky dome
    so they remain 100% visible inside the camera frustum (far=3800) instead of being clipped.
    """
    origin_rad = math.radians(origin_lat)
    east_m = (lon - origin_lon) * 111320.0 * math.cos(origin_rad)
    north_m = (lat - origin_lat) * 111320.0

    theta = math.radians(runway_heading_deg)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)

    x_raw = (east_m * cos_t - north_m * sin_t) / meters_per_unit
    z_raw = -(east_m * sin_t + north_m * cos_t) / meters_per_unit

    if is_ground:
        return x_raw, z_raw

    r_raw = math.hypot(x_raw, z_raw)
    if r_raw <= AIRPORT_SURFACE_RADIUS:
        # Near ground / airport boundary: 1:1 true scale
        return x_raw, z_raw

    # Airspace radial compression:
    # Continuous C^1 algebraic mapping from [600, 35000] into [600, 2950]
    delta_r = r_raw - AIRPORT_SURFACE_RADIUS
    headroom = AIRSPACE_MAX_RADIUS - AIRPORT_SURFACE_RADIUS  # 2350
    compressed_delta = (delta_r * headroom) / (delta_r + headroom)
    r_scene = AIRPORT_SURFACE_RADIUS + compressed_delta

    ratio = r_scene / r_raw
    return x_raw * ratio, z_raw * ratio


def compress_altitude(alt_val, on_ground: bool = False, in_apron: bool = False) -> float:
    """
    Compress real altitude (feet) into visual scene Y level.
    Apron ground = 0.22, runway ground = 0.3.
    Airborne aircraft are placed visibly in the sky (Y = 25 to 420).
    """
    if on_ground or alt_val == "ground" or alt_val is None:
        return 0.22 if in_apron else 0.3

    try:
        alt_ft = float(alt_val)
    except (ValueError, TypeError):
        return 0.3

    if alt_ft <= 40.0:
        return 0.22 if in_apron else 0.3

    # Low altitude / approach / climb (40 ft to 4,000 ft):
    # Clearly elevates airborne planes above runways and buildings
    if alt_ft <= 4000.0:
        # 500 ft -> Y ~ 32
        # 1500 ft -> Y ~ 75
        # 3000 ft -> Y ~ 138
        # 4000 ft -> Y ~ 178
        t = alt_ft / 4000.0
        return 0.3 + 15.0 + t * 165.0
    elif alt_ft <= 15000.0:
        # 4,000 to 15,000 ft: intermediate climb/descent
        # Y from 180 to 300
        t = (alt_ft - 4000.0) / 11000.0
        return 180.0 + t * 120.0
    else:
        # High cruise (15,000 to 45,000 ft)
        # Logarithmic expansion up to Y = 420
        extra_ft = alt_ft - 15000.0
        extra_y = math.log10(extra_ft / 1000.0 + 1.0) * 80.0
        return min(300.0 + extra_y, 440.0)


def compute_attitude(
    track_deg: Optional[float],
    runway_heading_deg: float,
    baro_rate_fpm: Optional[float] = None,
    gs_kts: Optional[float] = None,
    dx: Optional[float] = None,
    dz: Optional[float] = None,
) -> Tuple[float, float]:
    """
    Calculate aircraft yaw and pitch angles.
    Model nose points along local -Z axis.
    yaw = atan2(-dx, -dz) or based on rotated ADS-B track.
    pitch written to pitchGroup.rotation.x (positive = nose up).
    """
    # 1. Yaw angle
    if dx is not None and dz is not None and (abs(dx) > 1e-4 or abs(dz) > 1e-4):
        yaw = math.atan2(-dx, -dz)
    elif track_deg is not None:
        rel_track = (track_deg - runway_heading_deg) % 360.0
        yaw = math.radians(-rel_track)
    else:
        yaw = 0.0

    # 2. Pitch angle
    pitch = 0.0
    if baro_rate_fpm is not None and gs_kts is not None and gs_kts > 25.0:
        fpa_ratio = baro_rate_fpm / (gs_kts * 101.27)
        fpa_ratio = max(-0.25, min(0.25, fpa_ratio))
        pitch = math.asin(fpa_ratio)
        pitch = max(-0.08, min(0.22, pitch))

    return yaw, pitch


def snap_to_runway(
    x_scene: float,
    z_scene: float,
    alt_ft: float,
    status: str,
) -> Tuple[float, float]:
    """
    Optionally snap near-runway approaches and landings to 04L/04R runway centerlines.
    """
    if RUNWAY_Z_MIN - 1200.0 <= z_scene <= RUNWAY_Z_MAX + 1500.0 and alt_ft <= 1500.0:
        target_x = RUNWAY_WEST_X if x_scene < 0 else RUNWAY_EAST_X
        dist_to_runway = abs(x_scene - target_x)
        if dist_to_runway < 120.0:
            blend = max(0.0, 1.0 - (dist_to_runway / 120.0) ** 2)
            snapped_x = x_scene + (target_x - x_scene) * blend * 0.75
            return snapped_x, z_scene
    return x_scene, z_scene


def map_airspace_corridor(
    x_raw: float,
    z_raw: float,
    dist_km: float,
    alt_ft: float,
    status: str,
    direction: str,
    is_ground: bool,
    hex_id: str,
    snap_to_scene: bool = True,
) -> Tuple[float, float, float, float, float]:
    """
    Precision corridor mapping for realistic airport traffic flow.
    Returns (x, y, z, yaw, pitch).
    - APPROACH flights: aligned along South glideslope approach corridor (04L/04R), descending from Y=35~160 down to runway threshold (Z=465, Y=0.3).
    - CLIMB/TAKEOFF flights: aligned along North departure corridor (04R), climbing from runway (Z=80, Y=0.3) to sky (Z=-1200, Y=220).
    - PARKED/TAXI flights: strictly on ground apron (Y=0.22) and runway pavement (Y=0.3).
    - EN_ROUTE: cruising across the overhead sky (Y=140~320, R<=1400).
    """
    if is_ground or status == "PARKED":
        y = 0.22
        return x_raw, y, z_raw, 0.0, 0.0

    if status in ("TAXI_IN", "TAXI_OUT"):
        y = 0.22
        # Align along taxiway if snapped
        if snap_to_scene:
            taxi_x = -302.0 if x_raw < 0 else 312.0
            x_raw = taxi_x
        return x_raw, y, z_raw, (math.pi if z_raw < 0 else 0.0), 0.0

    if status == "LANDED":
        # Rolling out on runway pavement
        target_x = RUNWAY_WEST_X if x_raw < 0 else RUNWAY_EAST_X
        x = target_x if snap_to_scene else x_raw
        z = max(RUNWAY_Z_MIN + 30.0, min(RUNWAY_Z_MAX - 30.0, z_raw))
        return x, 0.3, z, 0.0, 0.0

    # 1. Approach Corridor (进近客机：五边下滑道着陆，南向空域飞往04L/04R)
    if status == "APPROACH" or direction == "进港":
        # Target runway: West 04L (or East 04R)
        h = abs(hash(hex_id))
        target_x = RUNWAY_WEST_X if (h % 3 != 0) else RUNWAY_EAST_X
        lateral_offset = (math.sin(h % 100) * 12.0) if not snap_to_scene else (math.sin(h % 50) * 4.0)

        # Distance mapped into visible approach corridor [465, 1650]
        norm_dist = min(dist_km, 35.0) / 35.0
        z = 465.0 + norm_dist * 1150.0
        x = target_x + lateral_offset

        # Visual glideslope: descending from Y=140 down to Y=0.3
        y_glide = 0.3 + (z - 465.0) * 0.095
        y_alt = compress_altitude(alt_ft, on_ground=False)
        y = round(max(20.0, min(160.0, y_glide * 0.65 + y_alt * 0.35)), 2)

        yaw = 0.0  # Pointing North along -Z toward runway threshold
        pitch = -0.05  # -3 deg nose down descent attitude
        return round(x, 2), y, round(z, 2), yaw, pitch

    # 2. Departure Corridor (离港起飞/爬升，北向空域爬升)
    if status in ("TAKEOFF", "CLIMB") or direction == "出港":
        target_x = RUNWAY_EAST_X
        norm_dist = min(dist_km, 35.0) / 35.0
        # Z extends North from runway (Z=250) to Z=-1250
        z = 250.0 - norm_dist * 1500.0
        h = abs(hash(hex_id))
        x = target_x + (math.sin(h % 60) * 10.0 if not snap_to_scene else 0.0)

        if z > 80.0:
            # On runway acceleration
            y = 0.3
            pitch = 0.0
        else:
            # Lifting off and climbing into sky
            climb_h = (80.0 - z) * 0.12
            y_alt = compress_altitude(alt_ft, on_ground=False)
            y = round(min(220.0, max(25.0, climb_h * 0.65 + y_alt * 0.35)), 2)
            pitch = 0.15  # +8.5 deg nose up climb

        yaw = 0.0  # Pointing North along -Z
        return round(x, 2), y, round(z, 2), yaw, pitch

    # 3. High Altitude En-route / Overflight (高空巡航过境)
    r_raw = math.hypot(x_raw, z_raw) or 1.0
    r_scene = min(1400.0, max(450.0, r_raw))
    x = (x_raw / r_raw) * r_scene
    z = (z_raw / r_raw) * r_scene
    y = round(min(320.0, max(140.0, compress_altitude(alt_ft, on_ground=False))), 2)
    yaw = math.atan2(-x, -z) if r_scene > 0 else 0.0
    return round(x, 2), y, round(z, 2), yaw, 0.0
