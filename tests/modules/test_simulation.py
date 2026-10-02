import pytest

from carrera.core.clock import NANOS_PER_SECOND as S
from carrera.core.clock import ManualClock, MonotonicClock
from carrera.core.domain import TimingLayout, TimingSetup
from carrera.core.events import SensorTriggered
from carrera.core.timing import TimingSessionSpec, TimingSource
from carrera.modules.timing.simulation import (
    SimulatedLane,
    SimulationTimingFactory,
    SimulationTimingProvider,
)

LAYOUT = TimingLayout.from_position_ids(["sf", "s1", "s2"])
SETUP = TimingSetup.for_layout(LAYOUT)


def make(
    lanes: list[SimulatedLane], laps: int = 2, start_ns: int = 0
) -> tuple[SimulationTimingProvider, ManualClock, list[SensorTriggered]]:
    clock = ManualClock(start_ns)
    sim = SimulationTimingProvider(clock, SETUP, lanes, laps)
    received: list[SensorTriggered] = []
    sim.start(received.append)
    return sim, clock, received


def test_is_a_timing_source() -> None:
    sim, _, _ = make([SimulatedLane(1, (10 * S,))])
    assert isinstance(sim, TimingSource)
    assert sim.source_id == "simulation"
    assert sim.is_running


def test_sensors_are_passed_in_driving_order_at_even_spacing() -> None:
    sim, _, received = make([SimulatedLane(1, (12 * S,))], laps=2)
    sim.run_to_end()
    assert [(e.sensor_id, e.timestamp_ns) for e in received] == [
        ("s1", 4 * S),
        ("s2", 8 * S),
        ("sf", 12 * S),
        ("s1", 16 * S),
        ("s2", 20 * S),
        ("sf", 24 * S),
    ]
    assert {e.source_id for e in received} == {"simulation"}


def test_lanes_run_at_different_speeds_and_events_are_time_ordered() -> None:
    sim, _, received = make([SimulatedLane(1, (9 * S,)), SimulatedLane(2, (12 * S,))], laps=1)
    sim.run_to_end()
    timestamps = [e.timestamp_ns for e in received]
    assert timestamps == sorted(timestamps)
    finish = {e.lane: e.timestamp_ns for e in received if e.sensor_id == "sf"}
    assert finish == {1: 9 * S, 2: 12 * S}


def test_lap_times_can_vary_per_lap_and_last_entry_repeats() -> None:
    sim, _, received = make([SimulatedLane(1, (12 * S, 6 * S))], laps=3)
    sim.run_to_end()
    finishes = [e.timestamp_ns for e in received if e.sensor_id == "sf"]
    assert finishes == [12 * S, 18 * S, 24 * S]


def test_start_delay_and_clock_offset() -> None:
    sim, _, received = make([SimulatedLane(1, (12 * S,), start_delay_ns=2 * S)], 1, start_ns=100)
    sim.run_to_end()
    assert received[0].timestamp_ns == 100 + 2 * S + 4 * S


def test_advance_delivers_only_events_up_to_the_target_and_moves_the_clock() -> None:
    sim, clock, received = make([SimulatedLane(1, (12 * S,))], laps=1)
    sim.advance_to(8 * S)
    assert [e.sensor_id for e in received] == ["s1", "s2"]
    assert clock.now_ns() == 8 * S
    assert sim.pending_events == 1
    assert sim.next_event_ns == 12 * S
    sim.advance_by(S)
    assert clock.now_ns() == 9 * S
    assert len(received) == 2


def test_clock_equals_event_time_during_delivery() -> None:
    clock = ManualClock()
    sim = SimulationTimingProvider(clock, SETUP, [SimulatedLane(1, (12 * S,))], laps=1)
    seen: list[tuple[int, int]] = []
    sim.start(lambda e: seen.append((clock.now_ns(), e.timestamp_ns)))
    sim.run_to_end()
    assert all(now == ts for now, ts in seen)


def test_stop_halts_delivery_even_from_within_the_sink() -> None:
    clock = ManualClock()
    sim = SimulationTimingProvider(clock, SETUP, [SimulatedLane(1, (12 * S,))], laps=2)
    received: list[SensorTriggered] = []

    def sink(event: SensorTriggered) -> None:
        received.append(event)
        sim.stop()

    sim.start(sink)
    sim.run_to_end()
    assert len(received) == 1
    assert not sim.is_running


def test_state_errors() -> None:
    sim, _, _ = make([SimulatedLane(1, (S,))])
    with pytest.raises(RuntimeError, match="already running"):
        sim.start(lambda _e: None)
    sim.advance_to(S)
    with pytest.raises(ValueError, match="past"):
        sim.advance_to(0)
    sim.stop()
    with pytest.raises(RuntimeError, match="not running"):
        sim.advance_to(2 * S)
    sim.stop()


def test_configuration_validation() -> None:
    with pytest.raises(ValueError):
        SimulatedLane(0, (S,))
    with pytest.raises(ValueError):
        SimulatedLane(1, ())
    with pytest.raises(ValueError):
        SimulatedLane(1, (0,))
    with pytest.raises(ValueError, match="laps"):
        SimulationTimingProvider(ManualClock(), SETUP, [], laps=0)
    with pytest.raises(ValueError, match="unique"):
        SimulationTimingProvider(
            ManualClock(), SETUP, [SimulatedLane(1, (S,)), SimulatedLane(1, (S,))], laps=1
        )


def test_poll_delivers_only_due_events_and_never_moves_the_clock() -> None:
    sim, clock, received = make([SimulatedLane(1, (12 * S,))], laps=1)
    sim.poll()
    assert received == []
    clock.set(8 * S)
    sim.poll()
    assert [e.sensor_id for e in received] == ["s1", "s2"]
    assert clock.now_ns() == 8 * S
    sim.poll()
    assert len(received) == 2
    clock.set(12 * S)
    sim.poll()
    assert [e.sensor_id for e in received] == ["s1", "s2", "sf"]


def test_pause_and_resume_shift_the_remaining_events() -> None:
    sim, clock, received = make([SimulatedLane(1, (12 * S,))], laps=1)
    clock.set(5 * S)
    sim.poll()
    assert [e.timestamp_ns for e in received] == [4 * S]
    sim.pause()
    clock.set(20 * S)
    sim.poll()
    assert len(received) == 1
    sim.resume()
    clock.set(23 * S - 1)
    sim.poll()
    assert len(received) == 1  # the next pass was due at 8 s and is shifted by the 15 s pause
    clock.set(23 * S)
    sim.poll()
    assert [e.timestamp_ns for e in received] == [4 * S, 23 * S]


def test_resume_without_pause_changes_nothing() -> None:
    sim, clock, received = make([SimulatedLane(1, (12 * S,))], laps=1)
    sim.resume()
    clock.set(4 * S)
    sim.poll()
    assert [e.timestamp_ns for e in received] == [4 * S]


def test_advance_requires_a_manual_clock() -> None:
    sim = SimulationTimingProvider(MonotonicClock(), SETUP, [SimulatedLane(1, (S,))], laps=1)
    sim.start(lambda _e: None)
    with pytest.raises(TypeError, match="ManualClock"):
        sim.advance_to(1)
    sim.stop()


def test_factory_creates_an_independent_source_per_race() -> None:
    clock = ManualClock()
    factory = SimulationTimingFactory(clock)
    assert factory.provider_id == "simulation"
    spec = TimingSessionSpec(setup=SETUP, lanes=(1, 2), laps=2)
    first = factory.create_source(spec)
    second = factory.create_source(spec)
    assert first is not second
    assert isinstance(first, TimingSource)
    received: list[SensorTriggered] = []
    first.start(received.append)
    clock.set(60 * S)
    first.poll()
    assert {e.lane for e in received} == {1, 2}
    assert len([e for e in received if e.sensor_id == "sf"]) == 4
    lane_finish = {e.lane: e.timestamp_ns for e in received if e.sensor_id == "sf"}
    assert lane_finish[1] < lane_finish[2]
