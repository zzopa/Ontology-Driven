"""Pydantic data models for flight data structures."""
from typing import List, Optional, Union
from pydantic import BaseModel, Field


class FlightPoint(BaseModel):
    x: float
    y: float
    z: float


class FlightState(BaseModel):
    id: str
    hex: str
    callsign: str
    flight_no: str
    airline: str
    airline_name: str
    tail_color: int
    ac_type: str
    ac_scale: float
    lat: float
    lon: float
    alt_baro: Optional[Union[float, int, str]] = "ground"
    gs: float
    track: float
    baro_rate: float
    dist_km: float
    direction: str  # "进港" / "出港" / "场面" / "过境"
    origin: str
    destination: str
    route: str
    status: str  # APPROACH, LANDED, TAXI_IN, PARKED, TAXI_OUT, TAKEOFF, CLIMB, EN_ROUTE
    stand: Optional[str] = None
    gate_name: Optional[str] = None
    scene_x: float
    scene_y: float
    scene_z: float
    scene_yaw: float
    scene_pitch: float
    trail: List[List[float]] = Field(default_factory=list)
    updated_at: float


class FlightSnapshot(BaseModel):
    timestamp: float
    mode: str  # "hkg_live_mapped" or "wuh_native"
    source: str
    snap_to_scene: bool
    total_flights: int
    airborne_count: int
    ground_count: int
    matched_schedule_count: int
    flights: List[FlightState]
    stands_occupied: List[str] = Field(default_factory=list)
