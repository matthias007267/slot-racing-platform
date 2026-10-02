"""Test doubles for the timing provider interface. They let race engine and provider tests work
without the simulation."""

from __future__ import annotations

from carrera.core.events import SensorTriggered
from carrera.core.timing import (
    ManuallyTriggerable,
    ProviderAvailability,
    ProviderCapabilities,
    SensorSink,
    TimingSessionSpec,
    TimingSource,
    TimingSourceFactory,
)


class FakeTimingSource(TimingSource):
    """A source the test drives by hand: ``emit`` delivers an event while it is running.

    ``calls`` records the lifecycle (``start``, ``pause``, ``resume``, ``stop``, ``poll``).
    Methods named in ``fail_on`` raise ``RuntimeError``.
    """

    def __init__(self, source_id: str = "fake", fail_on: set[str] | None = None) -> None:
        self._source_id = source_id
        self._sink: SensorSink | None = None
        self.fail_on = fail_on or set()
        self.calls: list[str] = []
        self.paused = False

    @property
    def source_id(self) -> str:
        return self._source_id

    @property
    def is_running(self) -> bool:
        return self._sink is not None

    def start(self, sink: SensorSink) -> None:
        self._record("start")
        self._sink = sink

    def stop(self) -> None:
        self.calls.append("stop")
        self._sink = None

    def poll(self) -> None:
        self._record("poll")

    def pause(self) -> None:
        self._record("pause")
        self.paused = True

    def resume(self) -> None:
        self._record("resume")
        self.paused = False

    def emit(self, timestamp_ns: int, position_id: str, lane: int = 1, sensor_id: str = "") -> None:
        """Deliver one event, as a device would. Ignored after ``stop``."""
        if self._sink is None:
            return
        self._sink(
            SensorTriggered(
                timestamp_ns=timestamp_ns,
                source_id=self._source_id,
                sensor_id=sensor_id or f"sensor-{position_id}",
                position_id=position_id,
                lane=lane,
            )
        )

    def _record(self, call: str) -> None:
        self.calls.append(call)
        if call in self.fail_on:
            raise RuntimeError(f"fake source failed to {call}")


class FakeManualSource(FakeTimingSource, ManuallyTriggerable):
    def __init__(self) -> None:
        super().__init__("fake-manual")
        self.triggered: list[int] = []

    def trigger_next(self, lane: int) -> None:
        self.triggered.append(lane)


class FakeTimingFactory(TimingSourceFactory):
    """A configurable provider for registry, factory and error tests."""

    def __init__(
        self,
        provider_id: str = "fake",
        *,
        availability: ProviderAvailability | None = None,
        capabilities: ProviderCapabilities | None = None,
        validate_error: Exception | None = None,
        create_error: Exception | None = None,
        availability_error: Exception | None = None,
        source: FakeTimingSource | None = None,
    ) -> None:
        self._provider_id = provider_id
        self._availability = availability or ProviderAvailability.ok()
        self._capabilities = capabilities or ProviderCapabilities()
        self.validate_error = validate_error
        self.create_error = create_error
        self.availability_error = availability_error
        self.source = source
        self.specs: list[TimingSessionSpec] = []

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def availability(self) -> ProviderAvailability:
        if self.availability_error is not None:
            raise self.availability_error
        return self._availability

    def validate(self, spec: TimingSessionSpec) -> None:
        if self.validate_error is not None:
            raise self.validate_error

    def create_source(self, spec: TimingSessionSpec) -> TimingSource:
        if self.create_error is not None:
            raise self.create_error
        self.specs.append(spec)
        self.source = self.source or FakeTimingSource(self._provider_id)
        return self.source
