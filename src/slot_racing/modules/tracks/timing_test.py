"""Timing test mode: shows the standardized events a timing source delivers for a setup.

The source comes from the provider registry: the first available provider that declares
``supports_test_mode``. Today that is the simulation, so only simulated events are used.
Events go to a private sink and never to the application event bus, so a test cannot disturb
a running race.
"""

from __future__ import annotations

from dataclasses import dataclass

from slot_racing.core.clock import Clock
from slot_racing.core.domain import TimingSetup, TrackId
from slot_racing.core.errors import ValidationError
from slot_racing.core.events import SensorTriggered
from slot_racing.core.timing import ManuallyTriggerable, TimingSessionSpec, TimingSource
from slot_racing.core.timing_registry import TimingProviderRegistry

TEST_LANE = 1


@dataclass(frozen=True, slots=True)
class TimingTestEvent:
    number: int
    time_ns: int
    """Time since the test started."""
    sensor_id: str
    position_id: str
    lane: int


class TimingTestSession:
    def __init__(
        self,
        setup: TimingSetup,
        providers: TimingProviderRegistry,
        clock: Clock,
        track_id: TrackId | None = None,
    ) -> None:
        self.setup = setup
        self._providers = providers
        self._clock = clock
        self._track_id = track_id
        self._source: TimingSource | None = None
        self._started_ns = 0
        self.events: list[TimingTestEvent] = []

    @property
    def is_running(self) -> bool:
        return self._source is not None

    @property
    def count(self) -> int:
        return len(self.events)

    @property
    def last(self) -> TimingTestEvent | None:
        return self.events[-1] if self.events else None

    @property
    def detected_order(self) -> tuple[str, ...]:
        """Position ids in the order they were detected."""
        return tuple(event.position_id for event in self.events)

    @property
    def expected_next(self) -> str:
        """Position id the next event should report if the cars pass in driving order."""
        sequence = self.setup.layout.lap_sequence
        return sequence[len(self.events) % len(sequence)].id

    @property
    def in_order(self) -> bool:
        """Whether all detected positions follow the layout's driving order."""
        sequence = [p.id for p in self.setup.layout.lap_sequence]
        return all(
            position_id == sequence[index % len(sequence)]
            for index, position_id in enumerate(self.detected_order)
        )

    def start(self) -> None:
        """Create a simulated source for the setup and start it. Raises ``ValidationError``."""
        if self._source is not None:
            return
        candidates = [
            info
            for info in self._providers.providers()
            if info.available and info.capabilities.supports_test_mode
        ]
        if not candidates:
            raise ValidationError("error.timing.test_no_source")
        spec = TimingSessionSpec(
            setup=self.setup, lanes=(TEST_LANE,), laps=1, track_id=self._track_id
        )
        source = self._providers.create_source(candidates[0].provider_id, spec)
        if not isinstance(source, ManuallyTriggerable):
            raise ValidationError("error.timing.test_not_simulatable")
        self._started_ns = self._clock.now_ns()
        source.start(self._on_event)
        self._source = source

    def trigger(self) -> None:
        """Simulate a car passing the next position."""
        self.start()
        source = self._source
        if not isinstance(source, ManuallyTriggerable):
            raise ValidationError("error.timing.test_not_simulatable")
        source.trigger_next(TEST_LANE)

    def trigger_lap(self) -> None:
        """Simulate a car completing one lap, passing every position in driving order."""
        for _ in self.setup.layout.lap_sequence:
            self.trigger()

    def reset(self) -> None:
        self.stop()
        self.events.clear()

    def stop(self) -> None:
        source, self._source = self._source, None
        if source is not None:
            source.stop()

    def _on_event(self, event: SensorTriggered) -> None:
        self.events.append(
            TimingTestEvent(
                number=len(self.events) + 1,
                time_ns=max(0, event.timestamp_ns - self._started_ns),
                sensor_id=event.sensor_id,
                position_id=event.position_id,
                lane=event.lane,
            )
        )
