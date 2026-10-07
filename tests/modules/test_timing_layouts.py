"""Simulation and race engine with configured timing layouts instead of a built-in default."""

from __future__ import annotations

import pytest

from slot_racing.core.clock import NANOS_PER_SECOND as S
from slot_racing.core.clock import ManualClock
from slot_racing.core.domain import (
    DriverId,
    Participant,
    RaceId,
    RaceStatus,
    TimingLayout,
    TimingSensor,
    TimingSetup,
    TrackId,
)
from slot_racing.core.errors import ValidationError
from slot_racing.core.events import (
    Event,
    EventBus,
    LapCompleted,
    RaceFinished,
    SectorCompleted,
    SensorTriggered,
)
from slot_racing.core.timing import ManuallyTriggerable, TimingSessionSpec, TimingSetupService
from slot_racing.modules.races.engine import RaceConfig, RaceEngine
from slot_racing.modules.timing.simulation import (
    SimulatedLane,
    SimulationTimingFactory,
    SimulationTimingProvider,
)
from tests.modules.conftest import Env
from tests.modules.test_race_flow import drive_until_done

LAYOUT_A = ["start_finish", "sector_1", "sector_2"]
LAYOUT_B = ["start_finish", "sector_1", "sector_2", "sector_3", "sector_4"]


def setup_with_sensors(position_ids: list[str]) -> TimingSetup:
    layout = TimingLayout.from_position_ids(position_ids)
    return TimingSetup(
        layout,
        tuple(TimingSensor(f"sensor-{i}", p.id) for i, p in enumerate(layout.positions)),
    )


def run(setup: TimingSetup, laps: int = 1, lap_time: int = 10 * S) -> list[SensorTriggered]:
    received: list[SensorTriggered] = []
    simulation = SimulationTimingProvider(
        ManualClock(), setup, [SimulatedLane(1, (lap_time,))], laps
    )
    simulation.start(received.append)
    simulation.run_to_end()
    return received


@pytest.mark.parametrize("position_ids", [LAYOUT_A, LAYOUT_B])
def test_simulation_passes_the_configured_positions_in_order(position_ids: list[str]) -> None:
    setup = setup_with_sensors(position_ids)
    events = run(setup, laps=2)
    expected = [*position_ids[1:], position_ids[0]] * 2
    assert [e.position_id for e in events] == expected
    assert [e.sensor_id for e in events] == [setup.sensor_at(p).id for p in expected]


def test_simulation_spreads_one_lap_over_all_positions() -> None:
    events = run(setup_with_sensors(LAYOUT_B), lap_time=10 * S)
    assert [e.timestamp_ns for e in events] == [2 * S, 4 * S, 6 * S, 8 * S, 10 * S]


def test_a_layout_without_sectors_only_reports_start_finish() -> None:
    events = run(setup_with_sensors(["start_finish"]), laps=3)
    # The car is on the line at the start. That crossing opens the lap clock,
    # then each configured lap reports start/finish again.
    assert [e.position_id for e in events] == ["start_finish"] * 4
    assert [e.timestamp_ns for e in events] == [0, 10 * S, 20 * S, 30 * S]


def test_simulation_has_no_built_in_layout() -> None:
    with pytest.raises(TypeError):
        SimulationTimingProvider(ManualClock(), lanes=[SimulatedLane(1, (S,))], laps=1)  # type: ignore[call-arg]


def test_simulation_refuses_inactive_sensors() -> None:
    layout = TimingLayout.from_position_ids(LAYOUT_A)
    sensors = tuple(TimingSensor(p.id, p.id, active=p.order != 2) for p in layout.positions)
    with pytest.raises(ValidationError) as error:
        SimulationTimingProvider(ManualClock(), TimingSetup(layout, sensors), [], 1)
    assert error.value.key == "error.timing.sensor_inactive"


def test_session_spec_carries_layout_and_ids_but_no_hardware() -> None:
    setup = setup_with_sensors(LAYOUT_B)
    spec = TimingSessionSpec(
        setup=setup, lanes=(1, 2), laps=3, race_id=RaceId(4), track_id=TrackId(2)
    )
    assert spec.layout is setup.layout
    assert (spec.race_id, spec.track_id, spec.lanes) == (4, 2, (1, 2))
    inactive = TimingSetup(
        setup.layout, (*setup.sensors[1:], TimingSensor("x", "start_finish", active=False))
    )
    with pytest.raises(ValidationError):
        TimingSessionSpec(setup=inactive, lanes=(1,), laps=1)


def test_factory_builds_the_source_from_the_spec_setup() -> None:
    factory = SimulationTimingFactory(ManualClock())
    setup = setup_with_sensors(LAYOUT_B)
    source = factory.create_source(TimingSessionSpec(setup=setup, lanes=(1,), laps=1))
    assert isinstance(source, ManuallyTriggerable)
    received: list[SensorTriggered] = []
    source.start(received.append)
    for _ in range(5):
        source.trigger_next(1)
    assert [e.position_id for e in received] == [*LAYOUT_B[1:], LAYOUT_B[0]]


def test_manual_triggering_needs_a_started_source() -> None:
    simulation = SimulationTimingProvider(
        ManualClock(), setup_with_sensors(LAYOUT_A), [SimulatedLane(1, (S,))], 1
    )
    with pytest.raises(RuntimeError):
        simulation.trigger_next(1)


class EngineRun:
    def __init__(self, position_ids: list[str], laps: int = 2) -> None:
        self.setup = setup_with_sensors(position_ids)
        self.clock = ManualClock()
        self.bus = EventBus()
        self.events: list[Event] = []
        self.bus.subscribe(Event, self.events.append)
        config = RaceConfig(RaceId(1), laps, (Participant(DriverId(1), 1),), self.setup.layout)
        self.simulation = SimulationTimingProvider(
            self.clock, self.setup, [SimulatedLane(1, (20 * S, 10 * S))], laps
        )
        self.engine = RaceEngine(config, self.bus, self.clock, [self.simulation])

    def of(self, event_type: type[Event]) -> list[Event]:
        return [e for e in self.events if isinstance(e, event_type)]


@pytest.mark.parametrize("position_ids", [LAYOUT_A, LAYOUT_B, ["start_finish"], [*LAYOUT_B, "x"]])
def test_engine_measures_sectors_and_laps_for_any_layout(position_ids: list[str]) -> None:
    run_ = EngineRun(position_ids, laps=2)
    run_.engine.start()
    run_.simulation.run_to_end()

    count = len(position_ids)
    sectors = [e for e in run_.of(SectorCompleted) if isinstance(e, SectorCompleted)]
    laps = [e for e in run_.of(LapCompleted) if isinstance(e, LapCompleted)]
    assert [e.sector_number for e in sectors] == [*range(1, count + 1)] * 2
    assert all(abs(e.sector_time_ns - 20 * S // count) <= 1 for e in sectors[:count])
    assert sum(e.sector_time_ns for e in sectors[:count]) == 20 * S
    assert [(e.lap_number, e.lap_time_ns) for e in laps] == [(1, 20 * S), (2, 10 * S)]
    assert sum(e.sector_time_ns for e in sectors[count:]) == 10 * S
    assert run_.engine.status is RaceStatus.FINISHED
    assert isinstance(run_.events[-1], RaceFinished)


def test_engine_ignores_positions_out_of_order() -> None:
    run_ = EngineRun(LAYOUT_B, laps=1)
    run_.engine.start()
    # sector_2 is passed before sector_1: it must not count as the expected position.
    run_.bus.publish(
        SensorTriggered(
            timestamp_ns=S,
            source_id="manual",
            sensor_id="sensor-2",
            position_id="sector_2",
            lane=1,
        )
    )
    assert run_.of(SectorCompleted) == []


def test_engine_next_lap_starts_after_start_finish() -> None:
    run_ = EngineRun(LAYOUT_B, laps=3)
    run_.engine.start()
    run_.simulation.advance_to(31 * S)
    laps = [e for e in run_.of(LapCompleted) if isinstance(e, LapCompleted)]
    assert [e.lap_number for e in laps] == [1, 2]


def test_a_race_uses_the_stored_layout_of_its_track(env: Env) -> None:
    track = env.track()
    setups = env.runtime.services.get(TimingSetupService)
    setups.save_setup(track.id, setup_with_sensors(LAYOUT_B))
    race = env.races.create_race("Fünf Positionen", track.id, 2)
    driver_id, vehicle_id = env.pair(1)
    env.races.add_participant(race.id, driver_id, vehicle_id, 1)
    seen: list[Event] = []
    env.runtime.bus.subscribe(Event, seen.append)

    runner = env.controller.start_race(race.id)
    drive_until_done(env, runner)

    sensors = [e for e in seen if isinstance(e, SensorTriggered)]
    assert {e.position_id for e in sensors} == set(LAYOUT_B)
    assert {e.sensor_id for e in sensors} == {f"sensor-{i}" for i in range(5)}
    laps = env.races.get_laps(race.id)
    assert len(laps) == 2
    assert all(len(lap.sector_times_ns) == 5 for lap in laps)
    assert all(sum(lap.sector_times_ns) == lap.lap_time_ns for lap in laps)


def test_a_track_without_stored_layout_uses_the_default(env: Env) -> None:
    race = env.races.create_race("Alt", env.track_id(), 1)
    driver_id, vehicle_id = env.pair(1)
    env.races.add_participant(race.id, driver_id, vehicle_id, 1)
    seen: list[Event] = []
    env.runtime.bus.subscribe(SensorTriggered, seen.append)
    drive_until_done(env, env.controller.start_race(race.id))
    assert [e.position_id for e in seen if isinstance(e, SensorTriggered)] == [
        "sector_1",
        "sector_2",
        "start_finish",
    ]


def test_a_stored_layout_with_inactive_sensor_blocks_the_race_with_a_clear_error(
    env: Env,
) -> None:
    track = env.track()
    layout = TimingLayout.from_position_ids(LAYOUT_A)
    sensors = tuple(TimingSensor(p.id, p.id, active=p.order != 3) for p in layout.positions)
    env.runtime.services.get(TimingSetupService).save_setup(track.id, TimingSetup(layout, sensors))
    race = env.races.create_race("Blockiert", track.id, 1)
    driver_id, vehicle_id = env.pair(1)
    env.races.add_participant(race.id, driver_id, vehicle_id, 1)
    with pytest.raises(ValidationError) as error:
        env.controller.start_race(race.id)
    assert error.value.key == "error.timing.sensor_inactive"
    assert env.races.require_race(race.id).status is not RaceStatus.RUNNING
