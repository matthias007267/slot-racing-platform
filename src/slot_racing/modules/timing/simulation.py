"""Simulated timing source for development, demos and tests.

The simulation never sleeps and has no threads. With a :class:`ManualClock` (tests) it is
advanced explicitly and moves the clock to each event's timestamp before delivering it. With any
clock (the real one in the application) the host calls :meth:`poll`, which delivers the events
that have become due.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from slot_racing.core.clock import NANOS_PER_SECOND, Clock, ManualClock
from slot_racing.core.domain import TimingSetup
from slot_racing.core.errors import ProviderConfigurationError
from slot_racing.core.events import SensorTriggered
from slot_racing.core.timing import (
    ManuallyTriggerable,
    ProviderCapabilities,
    SensorSink,
    TimingSessionSpec,
    TimingSource,
    TimingSourceFactory,
)


@dataclass(frozen=True, slots=True)
class SimulatedLane:
    """Driving behaviour of the car on one lane."""

    lane: int
    lap_times_ns: tuple[int, ...]
    """Duration of each lap. Lap ``k`` uses entry ``k - 1``; the last entry repeats."""
    start_delay_ns: int = 0
    """Time between simulation start and the car actually moving."""

    def __post_init__(self) -> None:
        if self.lane < 1:
            raise ValueError("lane numbers start at 1")
        if not self.lap_times_ns or any(t <= 0 for t in self.lap_times_ns):
            raise ValueError("lap times must be positive")
        if self.start_delay_ns < 0:
            raise ValueError("start_delay_ns must not be negative")

    def lap_time_ns(self, lap_number: int) -> int:
        return self.lap_times_ns[min(lap_number, len(self.lap_times_ns)) - 1]


class SimulationTimingProvider(TimingSource, ManuallyTriggerable):
    """Cars pass every position of the configured layout at evenly spaced fractions of a lap.

    The positions and the sensors that report them come from the :class:`TimingSetup`; the
    simulation has no layout of its own."""

    def __init__(
        self,
        clock: Clock,
        setup: TimingSetup,
        lanes: Sequence[SimulatedLane],
        laps: int,
        source_id: str = "simulation",
    ) -> None:
        if laps < 1:
            raise ValueError("laps must be at least 1")
        if len({lane.lane for lane in lanes}) != len(lanes):
            raise ValueError("lanes must be unique")
        self._clock = clock
        setup.ensure_usable()
        self._setup = setup
        self._manual_progress: dict[int, int] = {}
        self._lanes = tuple(lanes)
        self._laps = laps
        self._source_id = source_id
        self._sink: SensorSink | None = None
        self._schedule: list[SensorTriggered] = []
        self._next_index = 0
        self._paused_at_ns: int | None = None

    @property
    def source_id(self) -> str:
        return self._source_id

    @property
    def is_running(self) -> bool:
        return self._sink is not None

    @property
    def pending_events(self) -> int:
        return len(self._schedule) - self._next_index

    @property
    def next_event_ns(self) -> int | None:
        if self._next_index >= len(self._schedule):
            return None
        return self._schedule[self._next_index].timestamp_ns

    def start(self, sink: SensorSink) -> None:
        if self._sink is not None:
            raise RuntimeError("simulation is already running")
        self._schedule = self._build_schedule(self._clock.now_ns())
        self._next_index = 0
        self._sink = sink

    def stop(self) -> None:
        self._sink = None
        self._paused_at_ns = None
        self._manual_progress.clear()

    def trigger_next(self, lane: int) -> None:
        sink = self._sink
        if sink is None:
            raise RuntimeError("simulation is not running")
        sequence = self._setup.layout.lap_sequence
        step = self._manual_progress.get(lane, 0)
        position = sequence[step % len(sequence)]
        self._manual_progress[lane] = step + 1
        sink(self._event(self._clock.now_ns(), position.id, lane))

    def _event(self, timestamp_ns: int, position_id: str, lane: int) -> SensorTriggered:
        return SensorTriggered(
            timestamp_ns=timestamp_ns,
            source_id=self._source_id,
            sensor_id=self._setup.sensor_at(position_id).id,
            position_id=position_id,
            lane=lane,
        )

    def pause(self) -> None:
        if self._sink is not None and self._paused_at_ns is None:
            self._paused_at_ns = self._clock.now_ns()

    def resume(self) -> None:
        """Shift remaining events by the pause, as if the cars had stood still.

        An event that was already due before the pause is placed at the resume instant, so no
        shifted timestamp stays inside the pause. The engine ignores timestamps from a pause.
        """
        if self._paused_at_ns is None:
            return
        now = self._clock.now_ns()
        shift = now - self._paused_at_ns
        self._paused_at_ns = None
        if shift > 0:
            for index in range(self._next_index, len(self._schedule)):
                event = self._schedule[index]
                self._schedule[index] = replace(
                    event, timestamp_ns=max(event.timestamp_ns + shift, now)
                )

    def poll(self) -> None:
        """Deliver every event that is due according to the clock. Does not move the clock."""
        if self._sink is None or self._paused_at_ns is not None:
            return
        now = self._clock.now_ns()
        while self._sink is not None and self._next_index < len(self._schedule):
            event = self._schedule[self._next_index]
            if event.timestamp_ns > now:
                break
            self._next_index += 1
            self._sink(event)

    def advance_to(self, timestamp_ns: int) -> None:
        """Deliver all events up to and including ``timestamp_ns`` and move the clock there.

        Only available with a :class:`ManualClock`."""
        clock = self._clock
        if not isinstance(clock, ManualClock):
            raise TypeError("advance_to requires a ManualClock; use poll() with a real clock")
        if self._sink is None:
            raise RuntimeError("simulation is not running")
        if timestamp_ns < clock.now_ns():
            raise ValueError("cannot advance to a time in the past")
        while self._sink is not None and self._next_index < len(self._schedule):
            event = self._schedule[self._next_index]
            if event.timestamp_ns > timestamp_ns:
                break
            self._next_index += 1
            clock.set(max(event.timestamp_ns, clock.now_ns()))
            self._sink(event)
        clock.set(max(timestamp_ns, clock.now_ns()))

    def advance_by(self, delta_ns: int) -> None:
        self.advance_to(self._clock.now_ns() + delta_ns)

    def run_to_end(self) -> None:
        """Deliver every remaining event."""
        last = self._schedule[-1].timestamp_ns if self._schedule else self._clock.now_ns()
        self.advance_to(max(last, self._clock.now_ns()))

    def _build_schedule(self, origin_ns: int) -> list[SensorTriggered]:
        sequence = self._setup.layout.lap_sequence
        events: list[tuple[int, int, int, SensorTriggered]] = []
        for lane in self._lanes:
            lap_start = origin_ns + lane.start_delay_ns
            for lap_number in range(1, self._laps + 1):
                lap_time = lane.lap_time_ns(lap_number)
                for position, point in enumerate(sequence):
                    timestamp = lap_start + lap_time * (position + 1) // len(sequence)
                    event = self._event(timestamp, point.id, lane.lane)
                    events.append((timestamp, lane.lane, position, event))
                lap_start += lap_time
        events.sort(key=lambda item: item[:3])
        return [item[3] for item in events]


PROVIDER_ID = "simulation"


class SimulationTimingFactory(TimingSourceFactory):
    """Creates a simulation for a race: every lane drives at its own, slightly varying pace."""

    def __init__(
        self,
        clock: Clock,
        *,
        base_lap_time_ns: int = 5 * NANOS_PER_SECOND,
        lane_step_ns: int = 400_000_000,
    ) -> None:
        self._clock = clock
        self._base_lap_time_ns = base_lap_time_ns
        self._lane_step_ns = lane_step_ns

    @property
    def provider_id(self) -> str:
        return PROVIDER_ID

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(supports_test_mode=True, supports_multiple_lanes=True)

    def validate(self, spec: TimingSessionSpec) -> None:
        if len(set(spec.lanes)) != len(spec.lanes):
            raise ProviderConfigurationError("error.timing_provider.lanes_duplicate")

    def create_source(self, spec: TimingSessionSpec) -> TimingSource:
        lanes = [
            SimulatedLane(
                lane=lane,
                lap_times_ns=tuple(
                    self._lap_time(lane, index, lap) for lap in range(1, spec.laps + 1)
                ),
            )
            for index, lane in enumerate(spec.lanes)
        ]
        return SimulationTimingProvider(self._clock, spec.setup, lanes, spec.laps)

    def _lap_time(self, lane: int, index: int, lap: int) -> int:
        # Deterministic jitter of -100..+100 ms so laps differ but runs are reproducible.
        jitter_ms = ((lap * 7 + lane * 3) % 5 - 2) * 50
        return self._base_lap_time_ns + index * self._lane_step_ns + jitter_ms * 1_000_000
