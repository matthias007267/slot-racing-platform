"""Read-only catalogs through which modules look up each other's master data.

``races`` needs drivers, vehicles and tracks but must not import the modules that own them.
Those modules implement these interfaces and register them as services; consumers only depend on
the interfaces and the plain data classes defined here. ``RaceCatalog`` is the other direction:
the shell reads race counts and standings without importing the races module.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from slot_racing.core.domain import DriverId, RaceStatus, TrackId, VehicleId


@dataclass(frozen=True, slots=True)
class DriverInfo:
    id: DriverId
    name: str
    display_name: str | None
    start_number: int | str | None
    is_active: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def label(self) -> str:
        return self.display_name or self.name


@dataclass(frozen=True, slots=True)
class VehicleInfo:
    id: VehicleId
    name: str
    model: str | None
    manufacturer: str | None
    scale: str | None
    notes: str | None
    start_number: int | str | None
    is_active: bool
    driver_id: DriverId | None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def label(self) -> str:
        return f"{self.name} ({self.model})" if self.model else self.name


@dataclass(frozen=True, slots=True)
class TrackInfo:
    id: TrackId
    name: str
    description: str | None
    lane_count: int
    is_active: bool
    image_path: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DriverCatalog(ABC):
    @abstractmethod
    def list_drivers(self, *, active_only: bool = False) -> list[DriverInfo]: ...

    @abstractmethod
    def get_driver(self, driver_id: DriverId) -> DriverInfo | None: ...


class VehicleCatalog(ABC):
    @abstractmethod
    def list_vehicles(
        self, *, active_only: bool = False, driver_id: DriverId | None = None
    ) -> list[VehicleInfo]: ...

    @abstractmethod
    def get_vehicle(self, vehicle_id: VehicleId) -> VehicleInfo | None: ...


class TrackCatalog(ABC):
    @abstractmethod
    def list_tracks(self, *, active_only: bool = False) -> list[TrackInfo]: ...

    @abstractmethod
    def get_track(self, track_id: TrackId) -> TrackInfo | None: ...


@dataclass(frozen=True, slots=True)
class StandingLine:
    """One line of a standing. Times stay in nanoseconds; the UI formats them."""

    position: int | None
    driver_label: str
    best_lap_ns: int | None
    total_time_ns: int | None


@dataclass(frozen=True, slots=True)
class RaceSummary:
    """A race as the dashboard may show it. ``standings`` is empty when no result exists yet."""

    name: str
    status: RaceStatus
    track_name: str
    timing_provider: str
    laps: int
    standings: tuple[StandingLine, ...] = ()


class RaceCatalog(ABC):
    """Read model for races. The races module registers the implementation."""

    @abstractmethod
    def count_races(self) -> int: ...

    @abstractmethod
    def active_summary(self) -> RaceSummary | None:
        """The race currently held by the race controller, including one that just ended."""

    @abstractmethod
    def latest_summary(self) -> RaceSummary | None:
        """The newest stored race, whatever its status. No standings."""

    @abstractmethod
    def latest_result(self) -> RaceSummary | None:
        """The newest finished or aborted race, with up to three stored standings."""
