"""Start sequence for a race.

The cue only decides when to ask the race to start. The race engine still owns
the clock and the first lap. The timing provider only measures laps after that
start. The start-light widget only draws the step this cue publishes. It does
not start the race.

The moments stay separate:

* ``counting`` — one more red light. The race clock is not running.
* ``start_signal`` — every light goes out. This is the moment the race starts.
* the race is then ``running``, which is the engine's own status.

Each step carries ``sound_id`` (``light-1`` … ``light-5``, then ``go``). A later
sound player can listen to :attr:`StartCue.changed` and use that id. This module
does not play audio.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtCore import QObject, QTimer, Signal


class StartPhase(StrEnum):
    """Where the start sequence is. The running race is not one of these phases."""

    COUNTING = "counting"
    START_SIGNAL = "start_signal"


# Five red lights, one second apart. The race starts when they go out, so the
# wait is five seconds. That replaces the old three-second 3-2-1, it is not
# added on top of it.
START_LIGHT_COUNT = 5


@dataclass(frozen=True, slots=True)
class StartCueStep:
    """One beat of the sequence.

    ``lit_lights`` is how many red lamps are on, from the left. Zero with
    :attr:`StartPhase.START_SIGNAL` is the lights-out start. ``sound_id`` names
    the beat for a future sound, and is not played here.
    """

    phase: StartPhase
    lit_lights: int
    sound_id: str


def _light_steps(count: int = START_LIGHT_COUNT) -> tuple[StartCueStep, ...]:
    if count < 1:
        raise ValueError("a start light needs at least one lamp")
    counting = [
        StartCueStep(StartPhase.COUNTING, lit, f"light-{lit}") for lit in range(1, count + 1)
    ]
    counting.append(StartCueStep(StartPhase.START_SIGNAL, 0, "go"))
    return tuple(counting)


DEFAULT_START_STEPS: tuple[StartCueStep, ...] = _light_steps()


def uses_start_cue() -> bool:
    """Whether the race UI waits for the lights before the engine starts.

    This is the only decision. It does not look at the timing provider: simulation,
    camera and a later sensor provider all measure laps, and the lights are how a
    race from the UI is started. ``False`` would start at once.
    """
    return True


class StartCue(QObject):
    """Steps through the lights and starts the race only when they go out.

    ``on_start`` is the existing race start. It is not called while a red light
    is coming on.
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
        for step in steps:
            if step.lit_lights < 0 or step.lit_lights > START_LIGHT_COUNT:
                raise ValueError("a start step can light at most five lamps")
            if step.phase is StartPhase.START_SIGNAL and step.lit_lights != 0:
                raise ValueError("the start signal shows every light off")
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
