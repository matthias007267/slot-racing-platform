"""The timing abstraction: the hardware independent interface of every timing provider.

A *provider* (simulation, camera, Raspberry Pi, manufacturer-specific hardware, ...) is
represented by a :class:`TimingSourceFactory` with a stable ``provider_id``. For one race the
factory builds a :class:`TimingSource` from a :class:`TimingSessionSpec`. The source detects
cars and reports every detection as a :class:`SensorTriggered` event. Consumers such as the
race engine only ever see those events, never the concrete source.

Time base: every event carries ``timestamp_ns``, an integer number of nanoseconds on the host's
monotonic :class:`~slot_racing.core.clock.Clock` timeline (``perf_counter_ns``). The *source* stamps
its events. A provider whose device has its own clock converts device time onto the host timeline
(for example by taking ``clock.now_ns()`` on arrival and correcting by the known latency). Wall
clock time, time zones, daylight saving time and NTP corrections never enter timing.

Lifecycle of a source, driven by the host (the race engine):

``start(sink)`` → events → ``pause()`` / ``resume()`` → ``stop()``. ``poll()`` is called regularly
while running, so host driven sources need no thread. ``pause()`` freezes the source's own
timeline; the race engine additionally ignores events that arrive while paused, so a source that
cannot pause (real devices) stays correct. After ``stop()`` no further events are delivered.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from slot_racing.core.domain import RaceId, TimingLayout, TimingSetup, TrackId
from slot_racing.core.events import SensorTriggered

SensorSink = Callable[[SensorTriggered], None]


class TimingSource(ABC):
    """Produces standardized sensor events while running.

    A source knows its own device or data source, the sensor and hardware ids of the session's
    :class:`TimingSetup` and the technical communication. It knows nothing about races, drivers,
    vehicles, standings, rules or the UI.
    """

    @property
    @abstractmethod
    def source_id(self) -> str:
        """Stable identifier, copied into every ``SensorTriggered`` event."""

    @property
    @abstractmethod
    def is_running(self) -> bool: ...

    @abstractmethod
    def start(self, sink: SensorSink) -> None:
        """Begin delivering events to ``sink``. May raise if the source is unavailable."""

    @abstractmethod
    def stop(self) -> None:
        """Stop delivering events. Must be safe to call when not running."""

    def poll(self) -> None:  # noqa: B027
        """Let a source that is driven by the host's event loop deliver due events.

        Sources with their own threads or callbacks leave this as a no-op. The host calls it
        regularly while a race runs, so no source needs a thread of its own.
        """

    def pause(self) -> None:  # noqa: B027
        """The race was paused. Sources that keep their own timeline (simulation) freeze it."""

    def resume(self) -> None:  # noqa: B027
        """The race continues after a pause."""


class ManuallyTriggerable(ABC):
    """Optional capability of a timing source: it can simulate a car passing on request.

    Used by the timing test mode. Sources that detect real cars do not implement it.
    """

    @abstractmethod
    def trigger_next(self, lane: int) -> None:
        """Deliver the event of the next timing position ``lane`` would pass, timed now."""


@dataclass(frozen=True, slots=True)
class TimingSessionSpec:
    """Everything a timing source needs to know about the session it will time.

    ``setup`` is the track's timing configuration; sources must not invent their own layout. It
    must be usable, that is every position is covered by an active sensor.
    """

    setup: TimingSetup
    lanes: tuple[int, ...]
    laps: int
    race_id: RaceId | None = None
    track_id: TrackId | None = None

    def __post_init__(self) -> None:
        self.setup.ensure_usable()

    @property
    def layout(self) -> TimingLayout:
        return self.setup.layout


class TimingSetupService(ABC):
    """Stores the timing configuration of tracks. Provided by the timing module."""

    @abstractmethod
    def get_setup(self, track_id: TrackId) -> TimingSetup | None:
        """The stored configuration, or ``None`` if the track has none yet."""

    @abstractmethod
    def save_setup(self, track_id: TrackId, setup: TimingSetup) -> None:
        """Store ``setup`` for the track, replacing the previous one."""

    @abstractmethod
    def clear_setup(self, track_id: TrackId) -> None:
        """Remove the stored configuration; the track falls back to the default layout."""


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """What a provider can do. Callers ask the capabilities, never the provider's name."""

    supports_test_mode: bool = False
    """Its sources are :class:`ManuallyTriggerable`, so the timing test mode can use them."""
    supports_multiple_lanes: bool = True
    """It can time more than one lane in one session."""


@dataclass(frozen=True, slots=True)
class ProviderAvailability:
    """Whether a provider can time a race right now.

    ``reason_key`` and ``reason_params`` form a translatable explanation for the UI when
    ``available`` is false (for example ``error.timing_provider.not_connected``).
    """

    available: bool = True
    reason_key: str | None = None
    reason_params: Mapping[str, object] = field(default_factory=dict)

    @classmethod
    def ok(cls) -> ProviderAvailability:
        return cls()

    @classmethod
    def unavailable(cls, reason_key: str, **params: object) -> ProviderAvailability:
        return cls(False, reason_key, params)


class TimingSourceFactory(ABC):
    """A timing provider: creates a fresh :class:`TimingSource` for one race.

    The module that provides the source registers the factory with
    ``PluginContext.register_timing_provider``. Races store only the ``provider_id`` and resolve
    the factory when they start, so a stored race does not depend on any Python class.

    The factory may check that the provider is available (:meth:`availability`), that the setup is
    supported (:meth:`validate`) and that the configuration is valid; it reports problems with
    :class:`~slot_racing.core.errors.TimingProviderError` subclasses carrying a translation key.
    """

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Stable technical identifier, for example ``simulation``, ``camera`` or
        ``raspberry_pi``. Not a UI label: the UI translates ``timing.provider.<provider_id>``."""

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities()

    def availability(self) -> ProviderAvailability:
        """Cheap check, safe to call often, whether the provider can start now."""
        return ProviderAvailability.ok()

    def validate(self, spec: TimingSessionSpec) -> None:  # noqa: B027
        """Raise ``ProviderConfigurationError`` if this provider cannot time ``spec``."""

    @abstractmethod
    def create_source(self, spec: TimingSessionSpec) -> TimingSource:
        """Build a source for ``spec``. Does not start it."""
