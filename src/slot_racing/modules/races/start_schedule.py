"""Time-based start sequence. The race clock stays in the race engine.

A schedule is an immutable list of monotonic timestamps. ``phase_at`` only reads
the schedule and a timestamp, so a late wake skips phases that are already over
and an early wake cannot light a lamp that is still in the future. Nothing here
counts timer ticks.

The timestamps use the same monotonic nanosecond clock as the race engine
(``Clock.now_ns``, normally ``time.perf_counter_ns``). They are host timestamps.
They are not a browser clock and must not be sent to a display as if they were.

The planned lights-out instant is not the race start. ``RaceEngine.start`` stamps
the clock when it runs, then subscribes to sensors and starts the timing sources.
Backdating the race to the plan would count time in which those sources were not
running, so a crossing in that gap would be missing while the clock claimed it
belonged to the race. The schedule records the plan. The engine records the start.
The difference is :class:`StartDeviation`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

PROTOCOL_VERSION = 1
TIME_BASE = "host_monotonic_ns"
TIMESTAMP_MEANING = "host_monotonic_nanoseconds_not_a_browser_clock"
DEFAULT_LIGHT_COUNT = 5

REASON_SKIPPED = "skipped_phases"
REASON_WAKE_LATE = "wake_after_planned_go"
REASON_ENGINE_LATE = "engine_start_after_planned_go"
REASON_BEFORE_PLAN = "actual_before_planned_go"
REASON_START_FAILED = "start_failed"


class StartSchedulePhase(StrEnum):
    """Where a schedule is for one timestamp. The running race is not a phase."""

    IDLE = "idle"
    COUNTING = "counting"
    START_SIGNAL = "start_signal"
    COMPLETE = "complete"
    REVOKED = "revoked"


class StartScheduleStatus(StrEnum):
    """Whether this plan may still start a race. Time does not change it."""

    ACTIVE = "active"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class StartScheduleStep:
    """One lamp change at an absolute monotonic timestamp."""

    phase: StartSchedulePhase
    lit_lights: int
    sound_id: str
    at_ns: int
    index: int


@dataclass(frozen=True, slots=True)
class StartScheduleState:
    """The phase ``phase_at`` derived. Computing it does not change the schedule."""

    phase: StartSchedulePhase
    step: StartScheduleStep | None
    sequence_id: str
    planned_go_ns: int
    may_start: bool
    due_index: int
    """Index of the step that is due, or the step count when the sequence is over.

    ``-1`` means no lamp is due yet. A wake skipped phases when this jumped by
    more than one since the previous wake.
    """


@dataclass(frozen=True, slots=True)
class StartDeviation:
    """Software timestamps around one start attempt.

    ``deviation_ns`` is the actual race start minus the planned lights-out.
    A correct start is never negative. The value compares two host timestamps.
    It does not prove that two displays showed the same lamp at the same moment.
    """

    sequence_id: str
    planned_go_ns: int
    actual_start_ns: int | None
    canceled: bool
    delay_reason: str | None

    @property
    def deviation_ns(self) -> int | None:
        if self.actual_start_ns is None:
            return None
        return self.actual_start_ns - self.planned_go_ns

    @property
    def deviation_ms(self) -> int | None:
        deviation = self.deviation_ns
        if deviation is None:
            return None
        return deviation // 1_000_000

    def to_document(self) -> dict[str, object]:
        return {
            "sequence_id": self.sequence_id,
            "planned_go_ns": self.planned_go_ns,
            "actual_start_ns": self.actual_start_ns,
            "deviation_ns": self.deviation_ns,
            "deviation_ms": self.deviation_ms,
            "canceled": self.canceled,
            "delay_reason": self.delay_reason,
        }


@dataclass(frozen=True, slots=True)
class StartSchedule:
    """One start sequence from the first lamp through the moment the lamps go out."""

    sequence_id: str
    reference_ns: int
    step_interval_ns: int
    light_count: int
    steps: tuple[StartScheduleStep, ...]
    planned_go_ns: int
    finished_ns: int
    status: StartScheduleStatus

    def __post_init__(self) -> None:
        if not self.sequence_id:
            raise ValueError("sequence_id is required")
        if self.reference_ns < 0:
            raise ValueError("reference_ns must not be negative")
        if self.step_interval_ns < 0:
            raise ValueError("step_interval_ns must not be negative")
        if self.light_count < 1:
            raise ValueError("a start light needs at least one lamp")
        if not self.steps:
            raise ValueError("a start schedule needs at least one step")
        if self.steps[-1].phase is not StartSchedulePhase.START_SIGNAL:
            raise ValueError("the start signal is the last step")
        if self.steps[-1].lit_lights != 0:
            raise ValueError("the start signal shows every light off")
        if self.planned_go_ns != self.steps[-1].at_ns:
            raise ValueError("planned_go_ns is the start signal")
        if self.finished_ns < self.planned_go_ns:
            raise ValueError("the sequence cannot finish before GO")
        if self.steps[0].at_ns != self.reference_ns:
            raise ValueError("the first lamp is at the reference timestamp")

    @classmethod
    def build(
        cls,
        *,
        reference_ns: int,
        step_interval_ns: int,
        light_count: int = DEFAULT_LIGHT_COUNT,
        sequence_id: str | None = None,
        steps: Sequence[tuple[str, int, str]] | None = None,
    ) -> StartSchedule:
        """Build the lamp times from ``reference_ns``.

        ``steps`` is ``(phase, lit_lights, sound_id)`` when the caller already
        has a sequence. The default is five red lamps and then lights-out.
        """
        specs = _default_steps(light_count) if steps is None else tuple(steps)
        if not specs:
            raise ValueError("a start schedule needs at least one step")
        built: list[StartScheduleStep] = []
        counting = 0
        for index, (phase_name, lit_lights, sound_id) in enumerate(specs):
            phase = _step_phase(phase_name)
            if lit_lights < 0:
                raise ValueError("lit_lights must not be negative")
            if phase is StartSchedulePhase.COUNTING:
                counting += 1
            built.append(
                StartScheduleStep(
                    phase=phase,
                    lit_lights=lit_lights,
                    sound_id=sound_id,
                    at_ns=reference_ns + step_interval_ns * index,
                    index=index,
                )
            )
        if counting < 1:
            raise ValueError("a start light needs at least one lamp")
        go_at = built[-1].at_ns
        return cls(
            sequence_id=sequence_id or uuid.uuid4().hex,
            reference_ns=reference_ns,
            step_interval_ns=step_interval_ns,
            light_count=counting,
            steps=tuple(built),
            planned_go_ns=go_at,
            finished_ns=go_at + step_interval_ns,
            status=StartScheduleStatus.ACTIVE,
        )

    def revoke(self) -> StartSchedule:
        """Return a plan that can no longer start a race. This object stays unchanged."""
        if self.status is StartScheduleStatus.REVOKED:
            return self
        return replace(self, status=StartScheduleStatus.REVOKED)

    def phase_at(self, now_ns: int) -> StartScheduleState:
        """The phase due at ``now_ns``. The same inputs always return the same state."""
        if self.status is StartScheduleStatus.REVOKED:
            return self._state(StartSchedulePhase.REVOKED, None, may_start=False, due_index=-1)
        if now_ns < self.reference_ns:
            return self._state(StartSchedulePhase.IDLE, None, may_start=False, due_index=-1)
        if now_ns >= self.finished_ns:
            return self._state(
                StartSchedulePhase.COMPLETE,
                None,
                may_start=True,
                due_index=len(self.steps),
            )
        chosen = self.steps[0]
        for step in self.steps:
            if step.at_ns <= now_ns:
                chosen = step
            else:
                break
        return self._state(
            chosen.phase,
            chosen,
            may_start=chosen.phase is StartSchedulePhase.START_SIGNAL,
            due_index=chosen.index,
        )

    def next_wake_ns(self, now_ns: int) -> int | None:
        """The next timestamp that can change the phase. ``None`` when nothing else will."""
        if self.status is not StartScheduleStatus.ACTIVE:
            return None
        if now_ns < self.reference_ns:
            return self.reference_ns
        for step in self.steps:
            if step.at_ns > now_ns:
                return step.at_ns
        if now_ns < self.finished_ns:
            return self.finished_ns
        return None

    def to_document(self) -> dict[str, object]:
        """JSON-ready description. It contains no widgets, clocks or callbacks.

        ``planned_go_ns`` and every ``at_ns`` are host monotonic nanoseconds.
        The broadcast writes those fields as decimal strings. A browser must
        not treat them as permission to start.
        """
        phases: list[dict[str, object]] = [
            {
                "phase": step.phase.value,
                "lit_lights": step.lit_lights,
                "sound_id": step.sound_id,
                "at_ns": step.at_ns,
            }
            for step in self.steps
        ]
        return {
            "protocol_version": PROTOCOL_VERSION,
            "time_base": TIME_BASE,
            "timestamp_meaning": TIMESTAMP_MEANING,
            "sequence_id": self.sequence_id,
            "status": self.status.value,
            "reference_ns": self.reference_ns,
            "step_interval_ns": self.step_interval_ns,
            "light_count": self.light_count,
            "planned_go_ns": self.planned_go_ns,
            "finished_ns": self.finished_ns,
            "phases": phases,
        }

    def _state(
        self,
        phase: StartSchedulePhase,
        step: StartScheduleStep | None,
        *,
        may_start: bool,
        due_index: int,
    ) -> StartScheduleState:
        return StartScheduleState(
            phase=phase,
            step=step,
            sequence_id=self.sequence_id,
            planned_go_ns=self.planned_go_ns,
            may_start=may_start,
            due_index=due_index,
        )


def _default_steps(light_count: int) -> tuple[tuple[str, int, str], ...]:
    if light_count < 1:
        raise ValueError("a start light needs at least one lamp")
    counting = tuple(
        (StartSchedulePhase.COUNTING.value, lit, f"light-{lit}")
        for lit in range(1, light_count + 1)
    )
    return (*counting, (StartSchedulePhase.START_SIGNAL.value, 0, "go"))


def _step_phase(name: str) -> StartSchedulePhase:
    if name == StartSchedulePhase.COUNTING.value:
        return StartSchedulePhase.COUNTING
    if name == StartSchedulePhase.START_SIGNAL.value:
        return StartSchedulePhase.START_SIGNAL
    raise ValueError(f"unknown start phase {name}")
