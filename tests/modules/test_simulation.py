import pytest

from carrera.core.clock import NANOS_PER_SECOND as S
from carrera.core.clock import ManualClock
from carrera.core.domain import TimingLayout
from carrera.core.events import SensorTriggered
from carrera.core.timing import TimingSource
from carrera.modules.timing.simulation import SimulatedLane, SimulationTimingProvider

LAYOUT = TimingLayout.from_sensor_ids(["sf", "s1", "s2"])


def make(
    lanes: list[SimulatedLane], laps: int = 2, start_ns: int = 0
) -> tuple[SimulationTimingProvider, ManualClock, list[SensorTriggered]]:
    clock = ManualClock(start_ns)
    sim = SimulationTimingProvider(clock, LAYOUT, lanes, laps)
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
    sim = SimulationTimingProvider(clock, LAYOUT, [SimulatedLane(1, (12 * S,))], laps=1)
    seen: list[tuple[int, int]] = []
    sim.start(lambda e: seen.append((clock.now_ns(), e.timestamp_ns)))
    sim.run_to_end()
    assert all(now == ts for now, ts in seen)


def test_stop_halts_delivery_even_from_within_the_sink() -> None:
    clock = ManualClock()
    sim = SimulationTimingProvider(clock, LAYOUT, [SimulatedLane(1, (12 * S,))], laps=2)
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
        SimulationTimingProvider(ManualClock(), LAYOUT, [], laps=0)
    with pytest.raises(ValueError, match="unique"):
        SimulationTimingProvider(
            ManualClock(), LAYOUT, [SimulatedLane(1, (S,)), SimulatedLane(1, (S,))], laps=1
        )
