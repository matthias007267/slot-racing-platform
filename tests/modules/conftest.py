from __future__ import annotations

import random
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from slot_racing.app.runtime import Runtime
from slot_racing.core.catalog import DriverInfo, TrackInfo, VehicleInfo
from slot_racing.core.clock import ManualClock
from slot_racing.core.config import AppConfig
from slot_racing.core.domain import DriverId, TrackId, VehicleId
from slot_racing.core.storage import Database
from slot_racing.core.timing import TimingSourceFactory
from slot_racing.modules.drivers_vehicles.service import (
    DriverInput,
    DriverService,
    VehicleInput,
    VehicleService,
)
from slot_racing.modules.races.runner import RaceController
from slot_racing.modules.races.service import RaceService
from slot_racing.modules.timing.simulation import SimulationTimingFactory
from slot_racing.modules.tracks.service import TrackInput, TrackService


@dataclass
class Env:
    runtime: Runtime
    clock: ManualClock
    drivers: DriverService
    vehicles: VehicleService
    tracks: TrackService
    races: RaceService
    controller: RaceController

    def driver(self, name: str = "Anna", number: int | None = None) -> DriverInfo:
        return self.drivers.create_driver(DriverInput(name=name, start_number=number))

    def vehicle(self, name: str = "Porsche", driver_id: DriverId | None = None) -> VehicleInfo:
        return self.vehicles.create_vehicle(
            VehicleInput(name=name, model="911", driver_id=driver_id)
        )

    def track(self, name: str = "Ring", lanes: int = 2) -> TrackInfo:
        return self.tracks.create_track(TrackInput(name=name, lane_count=lanes))

    def pair(self, index: int) -> tuple[DriverId, VehicleId]:
        return self.driver(f"Driver {index}").id, self.vehicle(f"Car {index}").id

    def track_id(self, lanes: int = 2) -> TrackId:
        return self.track(lanes=lanes).id


@pytest.fixture
def env() -> Iterator[Env]:
    clock = ManualClock()
    runtime = Runtime.create(AppConfig(), database=Database.in_memory(), clock=clock)
    factory = runtime.services.get(TimingSourceFactory, "simulation")
    assert isinstance(factory, SimulationTimingFactory)
    factory.bind_rng(random.Random(7))
    yield Env(
        runtime=runtime,
        clock=clock,
        drivers=runtime.services.get(DriverService),
        vehicles=runtime.services.get(VehicleService),
        tracks=runtime.services.get(TrackService),
        races=runtime.services.get(RaceService),
        controller=runtime.services.get(RaceController),
    )
    runtime.shutdown()
