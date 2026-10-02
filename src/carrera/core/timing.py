"""The timing abstraction.

A :class:`TimingSource` knows how to detect cars (simulation, camera, Raspberry Pi, Carrera
hardware, ...) and reports every detection as a :class:`SensorTriggered` event. Consumers such
as the race engine only ever see those events, never the concrete source.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

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
