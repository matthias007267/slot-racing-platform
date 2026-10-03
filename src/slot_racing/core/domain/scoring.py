"""Scoring rules for a race mode.

The race engine still turns sensor events into laps. Which of those laps ends a participant, and
how the standings are ordered, is decided here. A new mode adds one :class:`RaceMode` value and
one :class:`RaceScoring` implementation; it does not replace the others.
"""

from __future__ import annotations

from typing import Protocol

from slot_racing.core.domain.race import RaceMode

# Missing times sort after every real time. The value stays inside a signed 64-bit range.
_MISSING_TIME_NS = 2**62


class RaceScoring(Protocol):
    """Answers the two questions that differ between race modes."""

    stop_is_abort: bool
    """An early stop aborts a lap race and finishes a time trial normally."""

    marks_result_when_session_ends: bool
    """A stored time trial lap counts once the session ends. There is no lap target."""

    def participant_finished(self, laps_completed: int, target_laps: int) -> bool:
        """Whether this completed lap reaches the mode's finish condition."""

    def rank_key(
        self,
        *,
        finish_order: int | None,
        laps_completed: int,
        last_lap_end_ns: int | None,
        best_lap_ns: int | None,
        lane: int,
    ) -> tuple[int, int, int, int]:
        """Sort key for one participant. Smaller keys rank first."""


class LapScoring:
    """The first participant to complete the lap target wins, in the existing order."""

    stop_is_abort = True
    marks_result_when_session_ends = False

    def participant_finished(self, laps_completed: int, target_laps: int) -> bool:
        return laps_completed >= target_laps

    def rank_key(
        self,
        *,
        finish_order: int | None,
        laps_completed: int,
        last_lap_end_ns: int | None,
        best_lap_ns: int | None,
        lane: int,
    ) -> tuple[int, int, int, int]:
        if finish_order is not None:
            return (0, finish_order, 0, 0)
        return (
            1,
            -laps_completed,
            last_lap_end_ns if last_lap_end_ns is not None else 2**63,
            lane,
        )


class TimeTrialScoring:
    """Every completed lap is a measured time. The session ends only when it is stopped.

    Standings follow the best measured time. The lane stays part of that comparison: a time on
    another lane is a different result.
    """

    stop_is_abort = False
    marks_result_when_session_ends = True

    def participant_finished(self, laps_completed: int, target_laps: int) -> bool:
        return False

    def rank_key(
        self,
        *,
        finish_order: int | None,
        laps_completed: int,
        last_lap_end_ns: int | None,
        best_lap_ns: int | None,
        lane: int,
    ) -> tuple[int, int, int, int]:
        if best_lap_ns is None:
            return (1, _MISSING_TIME_NS, lane, 0)
        return (0, best_lap_ns, lane, 0)


def scoring_for(mode: RaceMode) -> RaceScoring:
    """Scoring of ``mode``. An unknown mode is rejected instead of being treated as a lap race."""
    if mode is RaceMode.LAPS:
        return LapScoring()
    if mode is RaceMode.TIME_TRIAL:
        return TimeTrialScoring()
    raise ValueError(f"unsupported race mode: {mode}")


def time_trial_stored_key(best_lap_ns: int | None, lane: int) -> tuple[int, int, int, int]:
    """Order stored time-trial laps the same way :class:`TimeTrialScoring` ranks a live session."""
    return TimeTrialScoring().rank_key(
        finish_order=None,
        laps_completed=0,
        last_lap_end_ns=None,
        best_lap_ns=best_lap_ns,
        lane=lane,
    )
