"""The provider contract, checked against the simulation and a fake provider, and the race engine
driven through the standardized interface only (no simulation)."""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from slot_racing.core.clock import NANOS_PER_SECOND as S
from slot_racing.core.clock import ManualClock
from slot_racing.core.domain import (
    DriverId,
    Participant,
    RaceId,
    RaceStatus,
    TimingLayout,
    TimingSetup,
    default_timing_setup,
)
from slot_racing.core.events import (
    Event,
    EventBus,
    LapCompleted,
    RaceFinished,
    SectorCompleted,
    SensorTriggered,
)
from slot_racing.core.timing import TimingSessionSpec, TimingSource
from slot_racing.core.timing_registry import TimingProviderRegistry
from slot_racing.modules.races.engine import RaceConfig, RaceEngine
from slot_racing.modules.timing.simulation import SimulationTimingFactory
from tests.support.timing import FakeTimingFactory, FakeTimingSource


@dataclass
class Rig:
    """A source plus a way to make it produce events."""

    source: TimingSource
    produce: Callable[[], None]
    received: list[SensorTriggered]


def simulation_rig() -> Rig:
    clock = ManualClock()
    registry = TimingProviderRegistry(lambda: [SimulationTimingFactory(clock)])
    spec = TimingSessionSpec(setup=default_timing_setup(), lanes=(1, 2), laps=3)
    source = registry.create_source("simulation", spec)

    def produce() -> None:
        clock.advance(4 * S)
        source.poll()

    return Rig(source, produce, [])


def fake_rig() -> Rig:
    factory = FakeTimingFactory("fake")
    registry = TimingProviderRegistry(lambda: [factory])
    spec = TimingSessionSpec(setup=default_timing_setup(), lanes=(1,), laps=3)
    source = registry.create_source("fake", spec)
    assert isinstance(source, FakeTimingSource)
    counter = {"n": 0}

    def produce() -> None:
        counter["n"] += 1
        source.emit(counter["n"] * S, "sector_1")

    return Rig(source, produce, [])


@pytest.fixture(params=[simulation_rig, fake_rig], ids=["simulation", "fake"])
def rig(request: pytest.FixtureRequest) -> Rig:
    created: Rig = request.param()
    return created


def test_a_started_source_delivers_standardized_events(rig: Rig) -> None:
    assert not rig.source.is_running
    rig.source.start(rig.received.append)
    assert rig.source.is_running
    rig.produce()
    assert rig.received
    for event in rig.received:
        assert isinstance(event, SensorTriggered)
        assert event.source_id == rig.source.source_id
        assert event.timestamp_ns >= 0 and isinstance(event.timestamp_ns, int)
        assert event.sensor_id and event.position_id and event.lane >= 1


def test_no_events_after_stop(rig: Rig) -> None:
    rig.source.start(rig.received.append)
    rig.produce()
    count = len(rig.received)
    rig.source.stop()
    assert not rig.source.is_running
    rig.produce()
    rig.produce()
    assert len(rig.received) == count


def test_stop_is_safe_when_not_running_and_repeatable(rig: Rig) -> None:
    rig.source.stop()
    rig.source.start(rig.received.append)
    rig.source.stop()
    rig.source.stop()
    assert not rig.source.is_running


def test_pause_and_resume_are_accepted_by_every_source(rig: Rig) -> None:
    rig.source.start(rig.received.append)
    rig.source.pause()
    rig.source.resume()
    rig.source.poll()
    rig.source.stop()


def test_the_simulation_makes_no_progress_while_paused_and_continues_afterwards() -> None:
    clock = ManualClock()
    spec = TimingSessionSpec(setup=default_timing_setup(), lanes=(1,), laps=1)
    source = SimulationTimingFactory(clock).create_source(spec)
    received: list[SensorTriggered] = []
    source.start(received.append)
    clock.advance(2 * S)
    source.poll()
    before = [e.position_id for e in received]
    assert before == ["sector_1"]

    source.pause()
    clock.advance(60 * S)
    source.poll()
    assert [e.position_id for e in received] == before

    source.resume()
    source.poll()
    assert [e.position_id for e in received] == before
    clock.advance(10 * S)
    source.poll()
    assert [e.position_id for e in received] == ["sector_1", "sector_2", "start_finish"]
    timestamps = [e.timestamp_ns for e in received]
    assert timestamps == sorted(timestamps)
    assert timestamps[1] - timestamps[0] > 60 * S


FIVE = ["start_finish", "sector_1", "sector_2", "sector_3", "sector_4"]


class EngineBench:
    """Race engine plus a fake source. Nothing of the simulation is involved."""

    def __init__(self, position_ids: list[str] | None = None, laps: int = 2) -> None:
        self.setup = TimingSetup.from_position_ids(position_ids or default_ids())
        self.clock = ManualClock()
        self.bus = EventBus()
        self.events: list[Event] = []
        self.bus.subscribe(Event, self.events.append)
        self.source = FakeTimingSource("fake")
        config = RaceConfig(RaceId(1), laps, (Participant(DriverId(1), 1),), self.setup.layout)
        self.engine = RaceEngine(config, self.bus, self.clock, [self.source])

    def status(self) -> RaceStatus:
        return self.engine.status

    def of(self, event_type: type[Event]) -> list[Event]:
        return [e for e in self.events if isinstance(e, event_type)]

    def pass_position(self, at_s: float, position_id: str, lane: int = 1) -> None:
        self.source.emit(int(at_s * S), position_id, lane)


def default_ids() -> list[str]:
    return [p.id for p in default_timing_setup().layout.positions]


def test_engine_turns_fake_sensor_events_into_sector_and_lap_events() -> None:
    bench = EngineBench(laps=1)
    bench.engine.start()
    bench.pass_position(2, "sector_1")
    bench.pass_position(5, "sector_2")
    bench.pass_position(9, "start_finish")
    sectors = [e for e in bench.of(SectorCompleted) if isinstance(e, SectorCompleted)]
    assert [(e.sector_number, e.sector_time_ns) for e in sectors] == [
        (1, 2 * S),
        (2, 3 * S),
        (3, 4 * S),
    ]
    laps = [e for e in bench.of(LapCompleted) if isinstance(e, LapCompleted)]
    assert [(e.lap_number, e.lap_time_ns) for e in laps] == [(1, 9 * S)]
    assert bench.engine.status is RaceStatus.FINISHED
    assert isinstance(bench.events[-1], RaceFinished)
    assert not bench.source.is_running


def test_engine_works_with_five_positions_and_a_next_lap() -> None:
    bench = EngineBench(FIVE, laps=2)
    bench.engine.start()
    for lap in range(2):
        for index, position_id in enumerate([*FIVE[1:], FIVE[0]], start=1):
            bench.pass_position(lap * 10 + index * 2, position_id)
    assert len(bench.of(SectorCompleted)) == 10
    assert [e.lap_time_ns for e in bench.of(LapCompleted) if isinstance(e, LapCompleted)] == [
        10 * S,
        10 * S,
    ]
    assert bench.engine.status is RaceStatus.FINISHED


def test_engine_ignores_events_of_unknown_lanes_and_unexpected_positions() -> None:
    bench = EngineBench()
    bench.engine.start()
    bench.pass_position(1, "sector_2")
    bench.pass_position(2, "sector_1", lane=5)
    assert bench.of(SectorCompleted) == []
    assert len(bench.of(SensorTriggered)) == 2


def test_pause_stops_race_progress_and_resume_continues_on_the_race_time_base() -> None:
    bench = EngineBench()
    bench.engine.start()
    bench.pass_position(2, "sector_1")
    bench.clock.set(3 * S)
    bench.engine.pause()
    assert bench.source.paused and bench.engine.status is RaceStatus.PAUSED

    bench.pass_position(5, "sector_2")
    assert len(bench.of(SectorCompleted)) == 1
    assert bench.engine.elapsed_ns() == 3 * S

    bench.clock.set(8 * S)
    bench.engine.resume()
    assert not bench.source.paused and bench.engine.elapsed_ns() == 3 * S
    bench.pass_position(9, "sector_2")
    sectors = [e for e in bench.of(SectorCompleted) if isinstance(e, SectorCompleted)]
    assert [e.sector_time_ns for e in sectors] == [2 * S, 2 * S]
    assert bench.source.calls == ["start", "pause", "resume"]


def test_stop_releases_the_source_and_nothing_is_delivered_afterwards() -> None:
    bench = EngineBench(laps=5)
    bench.engine.start()
    bench.pass_position(2, "sector_1")
    bench.clock.set(3 * S)
    bench.engine.stop()
    assert bench.source.calls[-1] == "stop" and not bench.source.is_running
    count = len(bench.events)
    bench.pass_position(4, "sector_2")
    assert len(bench.events) == count
    final = bench.events[-1]
    assert isinstance(final, RaceFinished) and final.aborted


def test_a_source_that_fails_to_start_is_reported_and_the_race_stays_controlled() -> None:
    bench = EngineBench()
    bench.source.fail_on = {"start"}
    bench.engine.start()
    assert bench.status() is RaceStatus.RUNNING
    assert "fake" in bench.engine.source_errors
    assert "failed to start" in bench.engine.source_errors["fake"]
    bench.engine.stop()
    assert bench.status() is RaceStatus.FINISHED


def test_a_source_that_fails_while_running_is_isolated_and_not_called_again() -> None:
    bench = EngineBench()
    bench.engine.start()
    bench.source.fail_on = {"poll"}
    bench.engine.poll_sources()
    bench.engine.poll_sources()
    assert bench.source.calls.count("poll") == 1
    assert "failed to poll" in bench.engine.source_errors["fake"]
    bench.engine.pause()
    bench.engine.resume()
    assert bench.engine.status is RaceStatus.RUNNING
    bench.engine.stop()
    assert bench.source.calls[-1] == "stop"


def test_a_source_that_cannot_pause_does_not_break_the_race() -> None:
    bench = EngineBench()
    bench.engine.start()
    bench.source.fail_on = {"pause", "resume"}
    bench.engine.pause()
    bench.engine.resume()
    assert bench.engine.status is RaceStatus.RUNNING
    assert list(bench.engine.source_errors) == ["fake"]


def test_timing_layout_orders_are_independent_of_the_provider() -> None:
    layout = TimingLayout.from_position_ids(FIVE)
    assert [p.id for p in layout.lap_sequence] == [*FIVE[1:], FIVE[0]]


ENGINE_FILE = Path(__file__).resolve().parents[2] / "src/slot_racing/modules/races/engine.py"


def imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_the_race_engine_imports_only_core_abstractions() -> None:
    modules = imported_modules(ENGINE_FILE)
    internal = {m for m in modules if m.startswith("slot_racing")}
    assert internal
    assert all(
        m.startswith("slot_racing.core") or m == "slot_racing.modules.races.types" for m in internal
    )
    assert not any(
        part in module
        for module in modules
        for part in ("simulation", "camera", "raspberry", "cv2")
    )
