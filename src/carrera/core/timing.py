"""The timing abstraction.

A :class:`TimingSource` knows how to detect cars (simulation, camera, Raspberry Pi, Carrera
hardware, ...) and reports every detection as a :class:`SensorTriggered` event. Consumers such
as the race engine only ever see those events, never the concrete source.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

from carrera.core.domain import TimingLayout
from carrera.core.events import SensorTriggered

SensorSink = Callable[[SensorTriggered], None]


class TimingSource(ABC):
    """Produces standardized sensor events while running."""

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


@dataclass(frozen=True, slots=True)
class TimingSessionSpec:
    """What a timing source needs to know about the race it will time."""

    layout: TimingLayout
    lanes: tuple[int, ...]
    laps: int


class TimingSourceFactory(ABC):
    """Creates a fresh :class:`TimingSource` for one race. Registered as a service by the module
    that provides the source (simulation today, camera or Raspberry Pi later)."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable identifier used to select this factory, for example ``simulation``."""

    @abstractmethod
    def create_source(self, spec: TimingSessionSpec) -> TimingSource: ...
