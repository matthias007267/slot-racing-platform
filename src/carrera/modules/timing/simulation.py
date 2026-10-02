"""Deterministic simulated timing source for development and tests.

Time is virtual: the simulation emits events only when it is advanced, and moves the shared
:class:`ManualClock` to each event's timestamp before delivering it. No threads, no sleeping.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from carrera.core.clock import ManualClock
from carrera.core.domain import TimingLayout
from carrera.core.events import SensorTriggered
from carrera.core.timing import SensorSink, TimingSource


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


class SimulationTimingProvider(TimingSource):
    """Cars pass every timing point of the layout at evenly spaced fractions of their lap."""

    def __init__(
        self,
        clock: ManualClock,
        layout: TimingLayout,
        lanes: Sequence[SimulatedLane],
        laps: int,
        source_id: str = "simulation",
    ) -> None:
        if laps < 1:
            raise ValueError("laps must be at least 1")
        if len({lane.lane for lane in lanes}) != len(lanes):
            raise ValueError("lanes must be unique")
        self._clock = clock
        self._layout = layout
        self._lanes = tuple(lanes)
        self._laps = laps
        self._source_id = source_id
        self._sink: SensorSink | None = None
        self._schedule: list[SensorTriggered] = []
        self._next_index = 0

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

    def advance_to(self, timestamp_ns: int) -> None:
        """Deliver all events up to and including ``timestamp_ns`` and move the clock there."""
        if self._sink is None:
            raise RuntimeError("simulation is not running")
        if timestamp_ns < self._clock.now_ns():
            raise ValueError("cannot advance to a time in the past")
        while self._sink is not None and self._next_index < len(self._schedule):
            event = self._schedule[self._next_index]
            if event.timestamp_ns > timestamp_ns:
                break
            self._next_index += 1
            self._clock.set(max(event.timestamp_ns, self._clock.now_ns()))
            self._sink(event)
        self._clock.set(max(timestamp_ns, self._clock.now_ns()))

    def advance_by(self, delta_ns: int) -> None:
        self.advance_to(self._clock.now_ns() + delta_ns)

    def run_to_end(self) -> None:
        """Deliver every remaining event."""
        last = self._schedule[-1].timestamp_ns if self._schedule else self._clock.now_ns()
        self.advance_to(max(last, self._clock.now_ns()))

    def _build_schedule(self, origin_ns: int) -> list[SensorTriggered]:
        sequence = self._layout.lap_sequence
        events: list[tuple[int, int, int, SensorTriggered]] = []
        for lane in self._lanes:
            lap_start = origin_ns + lane.start_delay_ns
            for lap_number in range(1, self._laps + 1):
                lap_time = lane.lap_time_ns(lap_number)
                for position, point in enumerate(sequence):
                    timestamp = lap_start + lap_time * (position + 1) // len(sequence)
                    event = SensorTriggered(
                        timestamp_ns=timestamp,
                        source_id=self._source_id,
                        sensor_id=point.sensor_id,
                        lane=lane.lane,
                    )
                    events.append((timestamp, lane.lane, position, event))
                lap_start += lap_time
        events.sort(key=lambda item: item[:3])
        return [item[3] for item in events]
