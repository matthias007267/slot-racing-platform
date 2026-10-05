"""Visible start sequence for a camera race.

The cue only decides when to ask the race to start. The race engine still owns
the clock and the first lap. A later start-light animation can replace the
labels below without calling into the engine itself.

The three moments stay separate:

* ``counting`` — 3, 2, 1. The race clock is not running.
* ``start_signal`` — GO. This is the moment the race is started.
* the race is then ``running``, which is the engine's own status.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtCore import QObject, QTimer, Signal

# Provider id registered by the camera timing plugin. The race module does not
# import that plugin; the id is the value stored on the race.
CAMERA_PROVIDER_ID = "camera"


class StartPhase(StrEnum):
    """Where the start sequence is. The running race is not one of these phases."""

    COUNTING = "counting"
    START_SIGNAL = "start_signal"


@dataclass(frozen=True, slots=True)
class StartCueStep:
    """One beat of the sequence. ``label`` is what the current display shows."""

    phase: StartPhase
    label: str


DEFAULT_START_STEPS: tuple[StartCueStep, ...] = (
    StartCueStep(StartPhase.COUNTING, "3"),
    StartCueStep(StartPhase.COUNTING, "2"),
    StartCueStep(StartPhase.COUNTING, "1"),
    StartCueStep(StartPhase.START_SIGNAL, "GO"),
)


def uses_start_cue(timing_provider: str) -> bool:
    """Camera races wait for the cue. Every other provider starts immediately."""
    return timing_provider == CAMERA_PROVIDER_ID


class StartCue(QObject):
    """Steps through the sequence and starts the race only on the start signal.

    ``on_start`` is the existing race start. It is not called for 3, 2 or 1.
    """

    changed = Signal(object)
    finished = Signal()

    def __init__(
        self,
        on_start: Callable[[], None],
        *,
        steps: tuple[StartCueStep, ...] = DEFAULT_START_STEPS,
        interval_ms: int = 1000,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if not steps:
            raise ValueError("a start cue needs at least one step")
        if interval_ms < 0:
            raise ValueError("interval_ms must not be negative")
        self._on_start = on_start
        self._steps = steps
        self._index = -1
        self._started = False
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.advance)

    @property
    def current(self) -> StartCueStep | None:
        if self._index < 0 or self._index >= len(self._steps):
            return None
        return self._steps[self._index]

    @property
    def started(self) -> bool:
        """True after the start signal has asked the race to start."""
        return self._started

    def begin(self) -> None:
        """Show the first step. The race stays stopped until the start signal."""
        self._timer.stop()
        self._index = -1
        self._started = False
        self.advance()
        if self._index < len(self._steps) - 1:
            self._timer.start()

    def advance(self) -> None:
        """Move to the next step. The step after GO ends the sequence."""
        if self._index + 1 >= len(self._steps):
            self._timer.stop()
            if self._index < len(self._steps):
                self._index = len(self._steps)
                self.finished.emit()
            return
        self._index += 1
        step = self._steps[self._index]
        self.changed.emit(step)
        if step.phase is StartPhase.START_SIGNAL and not self._started:
            self._started = True
            self._on_start()

    def stop(self) -> None:
        """Drop the sequence without starting the race again."""
        self._timer.stop()
        self._index = len(self._steps)
