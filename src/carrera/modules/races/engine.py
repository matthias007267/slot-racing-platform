"""Hardware independent race engine.

The engine consumes ``SensorTriggered`` events and publishes the standardized race events. It
only knows the abstract :class:`TimingSource` and never a camera, GPIO or any other hardware.

Race rules implemented here:

* Standing start: lap 1 begins at ``RaceStarted``; a lap ends when the car passes the
  start/finish line after having passed all sector sensors in order.
* Sensor events that do not match the sensor a car is expected to pass next are ignored.
* A participant finishes after completing ``laps`` laps. The first one to finish is the winner.
  The race finishes when all participants have finished or when it is stopped manually.
* Events arriving while the race is paused are ignored and paused time is excluded from race time.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from carrera.core.clock import Clock
from carrera.core.domain import (
    DriverId,
    Participant,
    ParticipantResult,
    RaceId,
    RaceStatus,
    TimingLayout,
)
from carrera.core.events import (
    EventDispatcher,
    LapCompleted,
    LapStarted,
    RaceFinished,
    RacePaused,
    RaceResumed,
    RaceStarted,
    RaceStarting,
    SectorCompleted,
    SensorTriggered,
    Subscription,
    WinnerDetermined,
)
from carrera.core.timing import TimingSource

logger = logging.getLogger(__name__)


class RaceStateError(Exception):
    """An operation is not allowed in the current race status."""


@dataclass(frozen=True, slots=True)
class RaceConfig:
    race_id: RaceId
    laps: int
    participants: tuple[Participant, ...]
    layout: TimingLayout

    def __post_init__(self) -> None:
        if self.laps < 1:
            raise ValueError("a race needs at least one lap")
        if not self.participants:
            raise ValueError("a race needs at least one participant")
        if len({p.lane for p in self.participants}) != len(self.participants):
            raise ValueError("each lane can only be used by one participant")
        if len({p.driver_id for p in self.participants}) != len(self.participants):
            raise ValueError("each driver can only participate once")


@dataclass(slots=True)
class _ParticipantState:
    participant: Participant
    lap_number: int = 1
    next_point: int = 0
    lap_start_ns: int = 0
    sector_start_ns: int = 0
    lap_times_ns: list[int] = field(default_factory=list)
    last_lap_end_ns: int | None = None
    finish_order: int | None = None

    @property
    def finished(self) -> bool:
        return self.finish_order is not None


class RaceEngine:
    def __init__(
        self,
        config: RaceConfig,
        bus: EventDispatcher,
        clock: Clock,
        timing_sources: Sequence[TimingSource] = (),
    ) -> None:
        self._config = config
        self._bus = bus
        self._clock = clock
        self._sources = tuple(timing_sources)
        self._sequence = config.layout.lap_sequence
        self._states = {p.lane: _ParticipantState(p) for p in config.participants}
        self._status = RaceStatus.CREATED
        self._subscription: Subscription | None = None
        self._started_at_ns = 0
        self._paused_at_ns = 0
        self._paused_total_ns = 0
        self._finish_counter = 0
        self._final_elapsed_ns = 0
        self._winner_determined = False
        self.source_errors: dict[str, str] = {}
        """Timing sources that failed to start, by source id. The race runs without them."""

    @property
    def status(self) -> RaceStatus:
        return self._status

    @property
    def config(self) -> RaceConfig:
        return self._config

    def start(self) -> None:
        if self._status is not RaceStatus.CREATED:
            raise RaceStateError(f"cannot start a race that is {self._status}")
        now = self._clock.now_ns()
        race_id = self._config.race_id
        self._bus.publish(RaceStarting(timestamp_ns=now, race_id=race_id))
        self._started_at_ns = now
        self._status = RaceStatus.RUNNING
        self._subscription = self._bus.subscribe(SensorTriggered, self._on_sensor)
        self._bus.publish(
            RaceStarted(timestamp_ns=now, race_id=race_id, participants=self._config.participants)
        )
        for state in self._states.values():
            self._publish_lap_started(state, now, 0)
        self._start_sources()

    def pause(self) -> None:
        if self._status is not RaceStatus.RUNNING:
            raise RaceStateError(f"cannot pause a race that is {self._status}")
        self._paused_at_ns = self._clock.now_ns()
        self._status = RaceStatus.PAUSED
        self._for_each_source("pause", lambda source: source.pause())
        self._bus.publish(RacePaused(timestamp_ns=self._paused_at_ns, race_id=self._config.race_id))

    def resume(self) -> None:
        if self._status is not RaceStatus.PAUSED:
            raise RaceStateError(f"cannot resume a race that is {self._status}")
        now = self._clock.now_ns()
        self._paused_total_ns += now - self._paused_at_ns
        self._status = RaceStatus.RUNNING
        self._for_each_source("resume", lambda source: source.resume())
        self._bus.publish(RaceResumed(timestamp_ns=now, race_id=self._config.race_id))

    def stop(self) -> None:
        """End the race before everyone finished. Standings are ranked by laps, then time."""
        if self._status not in (RaceStatus.RUNNING, RaceStatus.PAUSED):
            raise RaceStateError(f"cannot stop a race that is {self._status}")
        self._finish(self._clock.now_ns(), aborted=True)

    def close(self) -> None:
        """Detach from the bus and stop timing sources without publishing anything."""
        self._release()

    def results(self) -> tuple[ParticipantResult, ...]:
        """Current standings as determined by the engine."""
        return self._ranking()

    def elapsed_ns(self) -> int:
        """Race time so far, excluding paused time. Frozen while paused and after the finish."""
        if self._status is RaceStatus.CREATED:
            return 0
        if self._status is RaceStatus.FINISHED:
            return self._final_elapsed_ns
        reference = (
            self._paused_at_ns if self._status is RaceStatus.PAUSED else self._clock.now_ns()
        )
        return max(0, self._race_time(reference))

    def poll_sources(self) -> None:
        """Let host-driven timing sources deliver due events. A failing source is recorded in
        ``source_errors`` and does not interrupt the race."""
        if self._status is RaceStatus.RUNNING:
            self._for_each_source("poll", lambda source: source.poll())

    def _for_each_source(self, action: str, call: Callable[[TimingSource], None]) -> None:
        for source in self._sources:
            if source.source_id in self.source_errors:
                continue
            try:
                call(source)
            except Exception as error:
                logger.exception("Timing source %s failed to %s", source.source_id, action)
                self.source_errors[source.source_id] = f"{type(error).__name__}: {error}"

    def _race_time(self, timestamp_ns: int) -> int:
        return timestamp_ns - self._started_at_ns - self._paused_total_ns

    def _on_sensor(self, event: SensorTriggered) -> None:
        if self._status is not RaceStatus.RUNNING or event.timestamp_ns < self._started_at_ns:
            return
        state = self._states.get(event.lane)
        if state is None or state.finished:
            return
        point = self._sequence[state.next_point]
        if event.sensor_id != point.sensor_id:
            logger.debug("Ignoring unexpected sensor %s on lane %s", event.sensor_id, event.lane)
            return

        race_time = self._race_time(event.timestamp_ns)
        participant = state.participant
        race_id = self._config.race_id
        self._bus.publish(
            SectorCompleted(
                timestamp_ns=event.timestamp_ns,
                race_id=race_id,
                driver_id=participant.driver_id,
                lane=participant.lane,
                lap_number=state.lap_number,
                sector_number=state.next_point + 1,
                sector_time_ns=race_time - state.sector_start_ns,
                race_time_ns=race_time,
            )
        )
        state.sector_start_ns = race_time
        state.next_point = (state.next_point + 1) % len(self._sequence)
        if state.next_point != 0:
            return
        self._complete_lap(state, event.timestamp_ns, race_time)

    def _complete_lap(self, state: _ParticipantState, timestamp_ns: int, race_time: int) -> None:
        participant = state.participant
        lap_time = race_time - state.lap_start_ns
        state.lap_times_ns.append(lap_time)
        state.last_lap_end_ns = race_time
        self._bus.publish(
            LapCompleted(
                timestamp_ns=timestamp_ns,
                race_id=self._config.race_id,
                driver_id=participant.driver_id,
                lane=participant.lane,
                lap_number=state.lap_number,
                lap_time_ns=lap_time,
                race_time_ns=race_time,
            )
        )
        if state.lap_number < self._config.laps:
            state.lap_number += 1
            state.lap_start_ns = race_time
            self._publish_lap_started(state, timestamp_ns, race_time)
            return

        self._finish_counter += 1
        state.finish_order = self._finish_counter
        if not self._winner_determined:
            self._publish_winner(state, timestamp_ns)
        if all(s.finished for s in self._states.values()):
            self._finish(timestamp_ns, aborted=False)

    def _publish_lap_started(
        self, state: _ParticipantState, timestamp_ns: int, race_time: int
    ) -> None:
        self._bus.publish(
            LapStarted(
                timestamp_ns=timestamp_ns,
                race_id=self._config.race_id,
                driver_id=state.participant.driver_id,
                lane=state.participant.lane,
                lap_number=state.lap_number,
                race_time_ns=race_time,
            )
        )

    def _publish_winner(self, state: _ParticipantState, timestamp_ns: int) -> None:
        self._winner_determined = True
        self._bus.publish(
            WinnerDetermined(
                timestamp_ns=timestamp_ns,
                race_id=self._config.race_id,
                driver_id=state.participant.driver_id,
                lane=state.participant.lane,
                laps_completed=len(state.lap_times_ns),
                total_time_ns=state.last_lap_end_ns or 0,
            )
        )

    def _finish(self, timestamp_ns: int, *, aborted: bool) -> None:
        if self._status is RaceStatus.FINISHED:
            return
        self._final_elapsed_ns = max(0, self._race_time(timestamp_ns))
        self._status = RaceStatus.FINISHED
        self._release()
        results = self._ranking()
        if not self._winner_determined and results and results[0].laps_completed > 0:
            leader = self._states[results[0].lane]
            self._publish_winner(leader, timestamp_ns)
        self._bus.publish(
            RaceFinished(
                timestamp_ns=timestamp_ns,
                race_id=self._config.race_id,
                results=results,
                aborted=aborted,
            )
        )

    def _ranking(self) -> tuple[ParticipantResult, ...]:
        def sort_key(state: _ParticipantState) -> tuple[int, int, int, int]:
            if state.finish_order is not None:
                return (0, state.finish_order, 0, 0)
            return (
                1,
                -len(state.lap_times_ns),
                state.last_lap_end_ns if state.last_lap_end_ns is not None else 2**63,
                state.participant.lane,
            )

        ranked = sorted(self._states.values(), key=sort_key)
        return tuple(
            ParticipantResult(
                driver_id=DriverId(state.participant.driver_id),
                lane=state.participant.lane,
                position=position,
                laps_completed=len(state.lap_times_ns),
                finished=state.finished,
                total_time_ns=state.last_lap_end_ns,
                best_lap_ns=min(state.lap_times_ns) if state.lap_times_ns else None,
            )
            for position, state in enumerate(ranked, start=1)
        )

    def _start_sources(self) -> None:
        for source in self._sources:
            try:
                source.start(self._bus.publish)
            except Exception as error:
                logger.exception("Timing source %s failed to start", source.source_id)
                self.source_errors[source.source_id] = f"{type(error).__name__}: {error}"

    def _release(self) -> None:
        if self._subscription is not None:
            self._subscription.cancel()
            self._subscription = None
        for source in self._sources:
            try:
                source.stop()
            except Exception:
                logger.exception("Timing source %s failed to stop", source.source_id)
