"""Start sequence for a race.

The cue asks the race to start. The race engine still owns the clock, the first
lap and the sensors. This object only decides which lamp the clock says is due
and, once, that the lights-out time has been reached.

``QTimer`` wakes the cue. It is not the clock. A late wake reads the monotonic
time and jumps to the phase that is due. An early wake leaves the current lamp
alone. Counting wakes does not move the sequence.

The wake runs on the Qt GUI thread, because that is where the lamps are drawn.
A blocked GUI thread delays both the next lamp and the call into
``start_prepared``. The engine still stamps the moment ``start`` actually runs,
and :class:`StartDeviation` records how far that was after the planned lights-out.
There is no sleep and no extra thread.

The moments stay separate:

* ``counting`` — one more red light. The race clock is not running.
* ``start_signal`` — every light goes out. This is the moment the race may start.
* the race is then ``running``, which is the engine's own status.

Each step carries ``sound_id`` (``light-1`` … ``light-5``, then ``go``). The live
view may play that id. This module does not play audio, and a missed sound does
not move the start.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum

from PySide6.QtCore import QObject, QTimer, Signal

from slot_racing.core.clock import Clock, ManualClock, MonotonicClock
from slot_racing.modules.races.start_schedule import (
    DEFAULT_LIGHT_COUNT,
    REASON_BEFORE_PLAN,
    REASON_ENGINE_LATE,
    REASON_SKIPPED,
    REASON_START_FAILED,
    REASON_WAKE_LATE,
    StartDeviation,
    StartSchedule,
    StartSchedulePhase,
    StartScheduleState,
    StartScheduleStatus,
    StartScheduleStep,
)

# Qt's timer takes milliseconds. A longer gap is split into wakes that only
# re-read the clock, so a large test interval cannot overflow the timer.
_TIMER_WAKE_CAP_MS = 60_000

START_LIGHT_COUNT = DEFAULT_LIGHT_COUNT


class StartPhase(StrEnum):
    """Where the start sequence is. The running race is not one of these phases."""

    COUNTING = "counting"
    START_SIGNAL = "start_signal"


@dataclass(frozen=True, slots=True)
class StartCueStep:
    """One beat of the sequence.

    ``lit_lights`` is how many red lamps are on, from the left. Zero with
    :attr:`StartPhase.START_SIGNAL` is the lights-out start. ``sound_id`` names
    the beat for the race audio. Playing it is not this step's job.
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


def _cue_step(step: StartScheduleStep) -> StartCueStep:
    phase = (
        StartPhase.START_SIGNAL
        if step.phase is StartSchedulePhase.START_SIGNAL
        else StartPhase.COUNTING
    )
    return StartCueStep(phase, step.lit_lights, step.sound_id)


class StartCue(QObject):
    """Shows the lamp the monotonic clock says is due, and starts the race once.

    ``on_start`` is the existing race start. It is not called while a red light
    is coming on, and it is not called for a schedule that was stopped.
    """

    changed = Signal(object)
    finished = Signal()

    def __init__(
        self,
        on_start: Callable[[], None],
        *,
        steps: tuple[StartCueStep, ...] = DEFAULT_START_STEPS,
        interval_ms: int = 1000,
        clock: Clock | None = None,
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
        self._step_ns = interval_ms * 1_000_000
        self._clock = clock if clock is not None else MonotonicClock()
        self._schedule: StartSchedule | None = None
        self._shown_index = -1
        self._started = False
        self._failed = False
        self._display_closed = False
        self._deviation: StartDeviation | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timer)

    @property
    def current(self) -> StartCueStep | None:
        schedule = self._schedule
        if self._display_closed or schedule is None or self._shown_index < 0:
            return None
        if self._shown_index >= len(schedule.steps):
            return None
        return _cue_step(schedule.steps[self._shown_index])

    @property
    def started(self) -> bool:
        """True after the start signal has asked the race to start."""
        return self._started

    @property
    def schedule(self) -> StartSchedule | None:
        """The plan currently held, including a plan that has been revoked."""
        return self._schedule

    @property
    def deviation(self) -> StartDeviation | None:
        """The measured start, or the cancellation, once one of those has happened."""
        return self._deviation

    @property
    def step_interval_ns(self) -> int:
        return self._step_ns

    def begin(self) -> None:
        """Show the lamp that is due now. The race stays stopped until lights-out."""
        self.hold()
        self.wake()

    def hold(self) -> None:
        """Build the schedule and leave every lamp off until :meth:`wake`.

        The live view publishes this plan before ``wake``, so a connected browser
        can receive the first red phase instead of joining after it has passed.
        """
        self._timer.stop()
        self._shown_index = -1
        self._started = False
        self._failed = False
        self._display_closed = False
        self._deviation = None
        described = tuple(
            (step.phase.value, step.lit_lights, step.sound_id) for step in self._steps
        )
        self._schedule = StartSchedule.build(
            reference_ns=self._clock.now_ns(),
            step_interval_ns=self._step_ns,
            steps=described,
        )

    def wake(self) -> None:
        """Apply the phase due at the current monotonic time.

        A revoked or replaced sequence does nothing. Calling this twice for the
        same timestamp does not start the race twice and does not repeat a lamp.
        """
        if self._display_closed:
            self._timer.stop()
            return
        schedule = self._schedule
        if schedule is None or schedule.status is not StartScheduleStatus.ACTIVE:
            self._timer.stop()
            return
        now = self._clock.now_ns()
        state = schedule.phase_at(now)
        if state.may_start and not self._started:
            self._commit(schedule, now, state)
            schedule = self._schedule
            if (
                self._display_closed
                or schedule is None
                or schedule.status is not StartScheduleStatus.ACTIVE
            ):
                self._timer.stop()
                return
            now = self._clock.now_ns()
            state = schedule.phase_at(now)
        self._publish(state)
        if state.phase is StartSchedulePhase.COMPLETE:
            self._finish_display()
            return
        self._arm(now)

    def advance(self) -> None:
        """Catch up to the clock.

        With a :class:`ManualClock`, one call first moves that clock by one lamp
        interval while the race has not started. Tests step the gantry without
        sleeping, and the phase is still whatever the clock says afterwards.
        A real clock is only read, so two calls in the same instant stay on the
        lamp that is already due. After the race has started, one call ends the
        visible sequence without moving the clock.
        """
        schedule = self._schedule
        if (
            self._display_closed
            or schedule is None
            or schedule.status is not StartScheduleStatus.ACTIVE
        ):
            return
        if self._started:
            self._finish_display()
            return
        if isinstance(self._clock, ManualClock):
            self._clock.advance(self._step_ns)
        self.wake()

    def stop(self) -> None:
        """Drop the sequence. A race that already started is left running."""
        self._timer.stop()
        self._display_closed = True
        self._shown_index = -1
        schedule = self._schedule
        if schedule is not None and schedule.status is StartScheduleStatus.ACTIVE:
            self._schedule = schedule.revoke()
        if self._started or schedule is None:
            return
        self._deviation = StartDeviation(
            sequence_id=schedule.sequence_id,
            planned_go_ns=schedule.planned_go_ns,
            actual_start_ns=None,
            canceled=True,
            delay_reason=REASON_START_FAILED if self._failed else None,
        )

    def note_start_failed(self) -> None:
        """The start callback ran and the race is still stopped."""
        self._failed = True
        self._started = False

    def record_actual_start(self, actual_start_ns: int) -> None:
        """Replace the provisional stamp with the engine's start timestamp."""
        current = self._deviation
        if current is None or current.canceled:
            return
        reason = current.delay_reason
        if actual_start_ns < current.planned_go_ns:
            reason = REASON_BEFORE_PLAN
        elif actual_start_ns == current.planned_go_ns and reason == REASON_WAKE_LATE:
            reason = None
        elif actual_start_ns > current.planned_go_ns and reason is None:
            reason = REASON_ENGINE_LATE
        self._deviation = replace(current, actual_start_ns=actual_start_ns, delay_reason=reason)

    def _on_timer(self) -> None:
        self.wake()

    def _commit(self, schedule: StartSchedule, now_ns: int, state: StartScheduleState) -> None:
        self._started = True
        reason = None
        if state.due_index > self._shown_index + 1:
            reason = REASON_SKIPPED
        elif now_ns > schedule.planned_go_ns:
            reason = REASON_WAKE_LATE
        self._deviation = StartDeviation(
            sequence_id=schedule.sequence_id,
            planned_go_ns=schedule.planned_go_ns,
            actual_start_ns=now_ns,
            canceled=False,
            delay_reason=reason,
        )
        self._on_start()

    def _publish(self, state: StartScheduleState) -> None:
        step = state.step
        if step is None or step.index == self._shown_index:
            return
        self._shown_index = step.index
        self.changed.emit(_cue_step(step))

    def _finish_display(self) -> None:
        if self._display_closed:
            return
        self._display_closed = True
        self._timer.stop()
        self._shown_index = -1
        self.finished.emit()

    def _arm(self, now_ns: int) -> None:
        schedule = self._schedule
        if schedule is None or self._display_closed:
            self._timer.stop()
            return
        deadline = schedule.next_wake_ns(now_ns)
        if deadline is None:
            self._timer.stop()
            return
        remaining_ns = deadline - now_ns
        if remaining_ns <= 0:
            self._timer.stop()
            return
        delay_ms = min(_TIMER_WAKE_CAP_MS, (remaining_ns + 999_999) // 1_000_000)
        self._timer.start(delay_ms)
