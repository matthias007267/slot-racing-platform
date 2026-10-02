import pytest

from carrera.core.clock import NANOS_PER_SECOND as S
from carrera.core.clock import ManualClock
from carrera.core.domain import DriverId, Participant, RaceId, RaceStatus, TimingLayout
from carrera.core.events import (
    Event,
    EventBus,
    LapCompleted,
    LapStarted,
    RaceFinished,
    RacePaused,
    RaceResumed,
    RaceStarted,
    RaceStarting,
    SectorCompleted,
    SensorTriggered,
    WinnerDetermined,
)
from carrera.core.timing import SensorSink, TimingSource
from carrera.modules.races.engine import RaceConfig, RaceEngine, RaceStateError
from carrera.modules.timing.simulation import SimulatedLane, SimulationTimingProvider

LAYOUT = TimingLayout.from_sensor_ids(["sf", "s1", "s2"])
ALICE, BOB = DriverId(1), DriverId(2)


class Harness:
    def __init__(
        self,
        lanes: list[SimulatedLane] | None = None,
        laps: int = 3,
        sources: list[TimingSource] | None = None,
    ) -> None:
        self.clock = ManualClock()
        self.bus = EventBus()
        self.events: list[Event] = []
        self.bus.subscribe(Event, self.events.append)
        participants = (Participant(ALICE, 1), Participant(BOB, 2))
        self.config = RaceConfig(RaceId(7), laps, participants, LAYOUT)
        self.sim: SimulationTimingProvider | None = None
        all_sources: list[TimingSource] = list(sources or [])
        if lanes is not None:
            self.sim = SimulationTimingProvider(self.clock, LAYOUT, lanes, laps)
            all_sources.insert(0, self.sim)
        self.engine = RaceEngine(self.config, self.bus, self.clock, all_sources)

    def of(self, event_type: type[Event]) -> list[Event]:
        return [e for e in self.events if isinstance(e, event_type)]

    def trigger(self, timestamp_ns: int, sensor_id: str, lane: int = 1) -> None:
        self.bus.publish(
            SensorTriggered(
                timestamp_ns=timestamp_ns, source_id="manual", sensor_id=sensor_id, lane=lane
            )
        )


def status_of(harness: Harness) -> RaceStatus:
    return harness.engine.status


def two_lane_race(laps: int = 3) -> Harness:
    return Harness([SimulatedLane(1, (12 * S,)), SimulatedLane(2, (15 * S,))], laps)


def test_simulated_race_runs_from_start_to_finish() -> None:
    harness = two_lane_race()
    harness.engine.start()
    assert harness.engine.status is RaceStatus.RUNNING
    assert harness.sim is not None
    harness.sim.run_to_end()

    assert status_of(harness) is RaceStatus.FINISHED
    finished = harness.of(RaceFinished)
    assert len(finished) == 1
    result = finished[0]
    assert isinstance(result, RaceFinished)
    assert not result.aborted
    assert [(r.lane, r.position, r.laps_completed, r.finished) for r in result.results] == [
        (1, 1, 3, True),
        (2, 2, 3, True),
    ]
    assert [r.total_time_ns for r in result.results] == [36 * S, 45 * S]
    assert [r.best_lap_ns for r in result.results] == [12 * S, 15 * S]


def test_event_sequence_at_race_start() -> None:
    harness = two_lane_race()
    harness.engine.start()
    assert [type(e) for e in harness.events] == [
        RaceStarting,
        RaceStarted,
        LapStarted,
        LapStarted,
    ]
    started = harness.of(RaceStarted)[0]
    assert isinstance(started, RaceStarted)
    assert started.participants == harness.config.participants


def test_lap_and_sector_times() -> None:
    harness = two_lane_race(laps=2)
    harness.engine.start()
    assert harness.sim is not None
    harness.sim.run_to_end()

    laps = [e for e in harness.of(LapCompleted) if isinstance(e, LapCompleted) and e.lane == 2]
    assert [(e.lap_number, e.lap_time_ns, e.race_time_ns) for e in laps] == [
        (1, 15 * S, 15 * S),
        (2, 15 * S, 30 * S),
    ]
    sectors = [
        e for e in harness.of(SectorCompleted) if isinstance(e, SectorCompleted) and e.lane == 1
    ]
    assert [(e.lap_number, e.sector_number, e.sector_time_ns) for e in sectors] == [
        (1, 1, 4 * S),
        (1, 2, 4 * S),
        (1, 3, 4 * S),
        (2, 1, 4 * S),
        (2, 2, 4 * S),
        (2, 3, 4 * S),
    ]


def test_different_lap_times_are_measured_per_lap() -> None:
    harness = Harness([SimulatedLane(1, (12 * S, 9 * S, 10 * S)), SimulatedLane(2, (30 * S,))])
    harness.engine.start()
    assert harness.sim is not None
    harness.sim.run_to_end()
    laps = [e for e in harness.of(LapCompleted) if isinstance(e, LapCompleted) and e.lane == 1]
    assert [e.lap_time_ns for e in laps] == [12 * S, 9 * S, 10 * S]
    result = harness.engine.results()[0]
    assert result.best_lap_ns == 9 * S


def test_winner_is_determined_when_first_participant_finishes() -> None:
    harness = two_lane_race()
    harness.engine.start()
    assert harness.sim is not None
    harness.sim.advance_to(36 * S)

    winners = harness.of(WinnerDetermined)
    assert len(winners) == 1
    winner = winners[0]
    assert isinstance(winner, WinnerDetermined)
    assert (winner.driver_id, winner.lane, winner.laps_completed) == (ALICE, 1, 3)
    assert winner.total_time_ns == 36 * S
    assert status_of(harness) is RaceStatus.RUNNING
    assert not harness.of(RaceFinished)

    harness.sim.run_to_end()
    assert len(harness.of(WinnerDetermined)) == 1
    assert harness.events[-1].__class__ is RaceFinished


def test_finished_participants_ignore_further_passes() -> None:
    harness = two_lane_race(laps=1)
    harness.engine.start()
    assert harness.sim is not None
    harness.sim.advance_to(12 * S)
    before = len(harness.of(LapCompleted))
    harness.trigger(13 * S, "s1", lane=1)
    harness.trigger(14 * S, "sf", lane=1)
    assert len(harness.of(LapCompleted)) == before


def test_engine_releases_sources_and_bus_when_finished() -> None:
    harness = two_lane_race(laps=1)
    harness.engine.start()
    assert harness.sim is not None
    harness.sim.run_to_end()
    assert not harness.sim.is_running
    count = len(harness.events)
    harness.trigger(99 * S, "s1")
    assert len(harness.events) == count + 1  # only the SensorTriggered itself


def test_manual_stop_ranks_by_laps_then_time_and_names_a_winner() -> None:
    harness = two_lane_race(laps=5)
    harness.engine.start()
    assert harness.sim is not None
    harness.sim.advance_to(40 * S)
    harness.engine.stop()

    assert harness.engine.status is RaceStatus.FINISHED
    final = harness.events[-1]
    assert isinstance(final, RaceFinished)
    assert final.aborted
    assert [(r.lane, r.laps_completed, r.finished) for r in final.results] == [
        (1, 3, False),
        (2, 2, False),
    ]
    winner = harness.of(WinnerDetermined)[0]
    assert isinstance(winner, WinnerDetermined)
    assert winner.lane == 1
    assert not harness.sim.is_running


def test_stop_without_any_lap_has_no_winner() -> None:
    harness = two_lane_race()
    harness.engine.start()
    harness.engine.stop()
    assert harness.of(WinnerDetermined) == []
    final = harness.events[-1]
    assert isinstance(final, RaceFinished)
    assert [r.lane for r in final.results] == [1, 2]
    assert all(r.total_time_ns is None and r.best_lap_ns is None for r in final.results)


def test_pause_ignores_passes_and_excludes_paused_time() -> None:
    harness = Harness(laps=1)
    engine = harness.engine
    engine.start()
    harness.trigger(4 * S, "s1")

    harness.clock.set(5 * S)
    engine.pause()
    assert engine.status is RaceStatus.PAUSED
    harness.trigger(6 * S, "s2")
    assert len(harness.of(SectorCompleted)) == 1

    harness.clock.set(15 * S)
    engine.resume()
    assert status_of(harness) is RaceStatus.RUNNING
    harness.trigger(18 * S, "s2")
    harness.trigger(20 * S, "sf")

    sectors = [e for e in harness.of(SectorCompleted) if isinstance(e, SectorCompleted)]
    assert [(e.sector_number, e.sector_time_ns, e.race_time_ns) for e in sectors] == [
        (1, 4 * S, 4 * S),
        (2, 4 * S, 8 * S),
        (3, 2 * S, 10 * S),
    ]
    lap = harness.of(LapCompleted)[0]
    assert isinstance(lap, LapCompleted)
    assert lap.lap_time_ns == 10 * S
    assert [type(e) for e in harness.events if type(e) in (RacePaused, RaceResumed)] == [
        RacePaused,
        RaceResumed,
    ]


def test_unexpected_sensors_unknown_lanes_and_early_events_are_ignored() -> None:
    harness = Harness(laps=1)
    harness.clock.set(10 * S)
    harness.engine.start()
    harness.trigger(5 * S, "s1")
    harness.trigger(11 * S, "s2")
    harness.trigger(11 * S, "sf")
    harness.trigger(11 * S, "s1", lane=9)
    harness.trigger(12 * S, "s1")
    harness.trigger(12 * S, "s1")
    sectors = [e for e in harness.of(SectorCompleted) if isinstance(e, SectorCompleted)]
    assert [(e.lane, e.sector_number) for e in sectors] == [(1, 1)]
    assert sectors[0].sector_time_ns == 2 * S


def test_events_before_start_and_after_finish_are_not_processed() -> None:
    harness = Harness(laps=1)
    harness.trigger(1, "s1")
    assert harness.of(SectorCompleted) == []
    harness.engine.start()
    harness.engine.stop()
    harness.trigger(S, "s1")
    assert harness.of(SectorCompleted) == []


def test_invalid_state_transitions() -> None:
    engine = Harness(laps=1).engine
    with pytest.raises(RaceStateError):
        engine.pause()
    with pytest.raises(RaceStateError):
        engine.resume()
    with pytest.raises(RaceStateError):
        engine.stop()
    engine.start()
    with pytest.raises(RaceStateError):
        engine.start()
    with pytest.raises(RaceStateError):
        engine.resume()
    engine.pause()
    with pytest.raises(RaceStateError):
        engine.pause()
    engine.stop()
    with pytest.raises(RaceStateError):
        engine.start()


class _UnavailableSource(TimingSource):
    @property
    def source_id(self) -> str:
        return "camera"

    @property
    def is_running(self) -> bool:
        return False

    def start(self, sink: SensorSink) -> None:
        raise OSError("no camera found")

    def stop(self) -> None:
        raise OSError("cannot stop")


def test_failing_timing_source_does_not_break_the_race() -> None:
    harness = Harness(
        [SimulatedLane(1, (12 * S,)), SimulatedLane(2, (15 * S,))], 1, [_UnavailableSource()]
    )
    harness.engine.start()
    assert harness.sim is not None
    harness.sim.run_to_end()
    assert harness.engine.source_errors == {"camera": "OSError: no camera found"}
    assert harness.engine.status is RaceStatus.FINISHED


def test_engine_only_needs_standard_events_not_a_source() -> None:
    harness = Harness(laps=1)
    harness.engine.start()
    for lane in (1, 2):
        for sensor, at in (("s1", 4), ("s2", 8), ("sf", 12 + lane)):
            harness.trigger(at * S, sensor, lane)
    assert harness.engine.status is RaceStatus.FINISHED
    assert [r.lane for r in harness.engine.results()] == [1, 2]


def test_close_detaches_without_publishing() -> None:
    harness = two_lane_race()
    harness.engine.start()
    count = len(harness.events)
    harness.engine.close()
    assert harness.sim is not None and not harness.sim.is_running
    assert len(harness.events) == count


def test_config_validation() -> None:
    participants = (Participant(ALICE, 1), Participant(BOB, 2))
    with pytest.raises(ValueError, match="lap"):
        RaceConfig(RaceId(1), 0, participants, LAYOUT)
    with pytest.raises(ValueError, match="participant"):
        RaceConfig(RaceId(1), 1, (), LAYOUT)
    with pytest.raises(ValueError, match="lane"):
        RaceConfig(RaceId(1), 1, (Participant(ALICE, 1), Participant(BOB, 1)), LAYOUT)
    with pytest.raises(ValueError, match="driver"):
        RaceConfig(RaceId(1), 1, (Participant(ALICE, 1), Participant(ALICE, 2)), LAYOUT)


def test_elapsed_time_excludes_pauses_and_freezes_at_the_end() -> None:
    harness = two_lane_race(laps=1)
    assert harness.engine.elapsed_ns() == 0
    harness.engine.start()
    harness.clock.advance(3 * S)
    assert harness.engine.elapsed_ns() == 3 * S
    harness.engine.pause()
    harness.clock.advance(10 * S)
    assert harness.engine.elapsed_ns() == 3 * S
    harness.engine.resume()
    harness.clock.advance(2 * S)
    assert harness.engine.elapsed_ns() == 5 * S
    harness.engine.stop()
    final = harness.engine.elapsed_ns()
    harness.clock.advance(5 * S)
    assert harness.engine.elapsed_ns() == final == 5 * S


def test_poll_sources_delivers_events_of_host_driven_sources() -> None:
    harness = two_lane_race(laps=1)
    harness.engine.start()
    harness.engine.poll_sources()
    assert not harness.of(LapCompleted)
    harness.clock.set(12 * S)
    harness.engine.poll_sources()
    assert len(harness.of(LapCompleted)) == 1


def test_pausing_the_engine_pauses_the_simulation() -> None:
    harness = two_lane_race(laps=1)
    harness.engine.start()
    harness.clock.set(6 * S)
    harness.engine.pause()
    harness.clock.set(106 * S)
    harness.engine.resume()
    harness.engine.poll_sources()
    assert not harness.of(LapCompleted)
    harness.clock.set(112 * S)
    harness.engine.poll_sources()
    assert len(harness.of(LapCompleted)) == 1


class _PollFails(TimingSource):
    @property
    def source_id(self) -> str:
        return "poll-fails"

    @property
    def is_running(self) -> bool:
        return True

    def start(self, sink: SensorSink) -> None:
        pass

    def stop(self) -> None:
        pass

    def poll(self) -> None:
        raise RuntimeError("read error")


def test_failing_poll_is_recorded_and_the_source_is_skipped_afterwards() -> None:
    harness = Harness(
        [SimulatedLane(1, (12 * S,)), SimulatedLane(2, (15 * S,))], sources=[_PollFails()]
    )
    harness.engine.start()
    harness.clock.set(12 * S)
    harness.engine.poll_sources()
    assert "read error" in harness.engine.source_errors["poll-fails"]
    assert len(harness.of(LapCompleted)) == 1  # the healthy source still delivered
    harness.engine.poll_sources()
