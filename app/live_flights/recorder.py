"""SQLite time-series recorder and replay engine for flight trajectories."""
import json
import logging
import sqlite3
import time
from typing import Dict, List, Optional, Tuple

from app.live_flights.config import DB_PATH
from app.live_flights.models import FlightSnapshot, FlightState

logger = logging.getLogger("live_flights.recorder")


class FlightRecorder:
    def __init__(self, db_path=DB_PATH):
        self.db_path = str(db_path)
        self._init_db()

        # Replay player status
        self.mode = "live"  # "live" or "replay"
        self.is_playing = True
        self.replay_time = 0.0
        self.replay_speed = 1.0
        self._last_tick = time.time()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS flight_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    mode TEXT NOT NULL,
                    total_flights INTEGER NOT NULL,
                    airborne_count INTEGER NOT NULL,
                    ground_count INTEGER NOT NULL,
                    stands_occupied TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON flight_snapshots(timestamp)")
            conn.commit()

    def record_snapshot(self, snapshot: FlightSnapshot) -> None:
        """Record live snapshot into SQLite table."""
        try:
            flights_data = [f.model_dump() for f in snapshot.flights]
            payload_json = json.dumps(flights_data)
            stands_json = json.dumps(snapshot.stands_occupied)

            with self._get_conn() as conn:
                conn.execute(
                    """
                    INSERT INTO flight_snapshots 
                    (timestamp, mode, total_flights, airborne_count, ground_count, stands_occupied, payload)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot.timestamp,
                        snapshot.mode,
                        snapshot.total_flights,
                        snapshot.airborne_count,
                        snapshot.ground_count,
                        stands_json,
                        payload_json,
                    ),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to record snapshot into SQLite: {e}")

    def get_time_range(self) -> Tuple[float, float, int]:
        """Return (min_timestamp, max_timestamp, count) in the database."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT MIN(timestamp) as min_ts, MAX(timestamp) as max_ts, COUNT(*) as cnt FROM flight_snapshots"
            ).fetchone()
            if row and row["cnt"] > 0:
                return float(row["min_ts"] or 0), float(row["max_ts"] or 0), int(row["cnt"])
            return 0.0, 0.0, 0

    def get_snapshot_near(self, target_ts: float) -> Optional[FlightSnapshot]:
        """Retrieve recorded snapshot closest to target_ts."""
        with self._get_conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM flight_snapshots 
                ORDER BY ABS(timestamp - ?) ASC 
                LIMIT 1
                """,
                (target_ts,),
            ).fetchone()

            if not row:
                return None

            flights_raw = json.loads(row["payload"])
            flights = [FlightState(**item) for item in flights_raw]
            stands = json.loads(row["stands_occupied"])

            return FlightSnapshot(
                timestamp=row["timestamp"],
                mode=f"{row['mode']}_replay",
                source="sqlite_replay",
                snap_to_scene=True,
                total_flights=row["total_flights"],
                airborne_count=row["airborne_count"],
                ground_count=row["ground_count"],
                matched_schedule_count=len(flights),
                flights=flights,
                stands_occupied=stands,
            )

    def tick_replay(self) -> Optional[FlightSnapshot]:
        """Advance replay clock by delta * speed and return current snapshot."""
        now = time.time()
        delta = now - self._last_tick
        self._last_tick = now

        if not self.is_playing:
            return self.get_snapshot_near(self.replay_time)

        min_ts, max_ts, cnt = self.get_time_range()
        if cnt == 0:
            return None

        if self.replay_time < min_ts or self.replay_time > max_ts:
            self.replay_time = min_ts

        self.replay_time += delta * self.replay_speed
        if self.replay_time > max_ts:
            self.replay_time = min_ts  # Loop back to start

        return self.get_snapshot_near(self.replay_time)

    def control_player(
        self,
        action: str,
        seek_to: Optional[float] = None,
        speed: Optional[float] = None,
    ) -> Dict[str, object]:
        """Control replay player state."""
        min_ts, max_ts, cnt = self.get_time_range()

        if action == "play":
            self.mode = "replay"
            self.is_playing = True
            self._last_tick = time.time()
        elif action == "pause":
            self.is_playing = False
        elif action == "live":
            self.mode = "live"
            self.is_playing = True
        elif action == "seek" and seek_to is not None:
            self.replay_time = max(min_ts, min(max_ts, seek_to))
            self._last_tick = time.time()

        if speed is not None and speed > 0:
            self.replay_speed = speed

        return {
            "mode": self.mode,
            "is_playing": self.is_playing,
            "replay_time": self.replay_time,
            "replay_speed": self.replay_speed,
            "min_timestamp": min_ts,
            "max_timestamp": max_ts,
            "recorded_frames": cnt,
        }
