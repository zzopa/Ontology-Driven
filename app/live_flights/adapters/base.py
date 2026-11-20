"""Abstract base class for Flight Data Feed Adapters."""
from abc import ABC, abstractmethod
from typing import Optional
from app.live_flights.models import FlightSnapshot


class FeedAdapter(ABC):
    @property
    @abstractmethod
    def mode_name(self) -> str:
        """Name of the feed mode ('hkg_live_mapped' or 'wuh_native')."""
        pass

    @abstractmethod
    async def start(self) -> None:
        """Initialize background tasks, HTTP sessions, or connection pools."""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Clean up background tasks and connections."""
        pass

    @abstractmethod
    async def get_latest_snapshot(self, snap_to_scene: bool = True) -> FlightSnapshot:
        """Fetch or compute the most recent snapshot of flight positions."""
        pass
