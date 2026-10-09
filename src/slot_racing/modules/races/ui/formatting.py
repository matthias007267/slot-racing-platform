"""Presentation of race standings. Ranking stays in the race engine."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from slot_racing.modules.races.runner import LiveRow
from slot_racing.modules.races.time_trial_board import format_lap_seconds

_MISSING_LAP = "-"


def participant_status_key(*, finished: bool, paused: bool, ended: bool) -> str:
    """Translation key for one participant.

    A participant who has completed the race stays finished even when the race was aborted.
    Everyone else on an ended race is retired. Pause only applies while the race is still open.
    """
    if finished:
        return "race.participant.finished"
    if ended:
        return "race.participant.retired"
    if paused:
        return "race.participant.waiting"
    return "race.participant.racing"


def start_number_text(number: int | str | None) -> str:
    return "-" if number is None else str(number)


EMPTY_DISPLAY = "—"


def format_progress_cell(current: int, target: int) -> str:
    """Lap races keep ``current/target``. A time trial has no target and shows the lap counter."""
    if target < 1:
        return format_lap_progress(current, target)
    return f"{current}/{target}"


def format_signed_seconds(delta_ns: int) -> str:
    """``+0,137 s`` with a fixed three-digit millisecond part."""
    sign = "+" if delta_ns >= 0 else "-"
    millis_total = abs(delta_ns) // 1_000_000
    seconds, millis = divmod(millis_total, 1000)
    return f"{sign}{seconds},{millis:03d} s"


@dataclass(frozen=True, slots=True)
class LaneGap:
    """Gap of one occupied lane. ``next_ns`` is set only when more than two lanes are occupied."""

    leader_ns: int
    next_ns: int | None = None


def lane_gaps(rows: Sequence[LiveRow], *, by_best_lap: bool) -> dict[int, LaneGap]:
    """Gap to the leader, and to the driver directly ahead when more than two lanes are occupied."""
    timed = [row for row in rows if _gap_time(row, by_best_lap=by_best_lap) is not None]
    ordered = sorted(timed, key=lambda row: row.position)
    if not ordered:
        return {}
    leader_time = _gap_time(ordered[0], by_best_lap=by_best_lap)
    assert leader_time is not None
    show_next = len(ordered) > 2
    gaps: dict[int, LaneGap] = {}
    previous = leader_time
    for index, row in enumerate(ordered):
        current = _gap_time(row, by_best_lap=by_best_lap)
        assert current is not None
        next_gap = None if index == 0 or not show_next else current - previous
        gaps[row.lane] = LaneGap(leader_ns=current - leader_time, next_ns=next_gap)
        previous = current
    return gaps


def _gap_time(row: LiveRow, *, by_best_lap: bool) -> int | None:
    return row.best_lap_ns if by_best_lap else row.total_time_ns


def fastest_completed_lap(times: Sequence[int]) -> tuple[int, int] | None:
    """1-based lap number and time of the fastest completed lap.

    Equal times keep the earlier lap. An empty sequence means no valid lap.
    The race engine still owns ``best_lap_ns``; this only reads the lap list.
    """
    if not times:
        return None
    fastest = min(times)
    return times.index(fastest) + 1, fastest


def best_lap_display(times: Sequence[int]) -> tuple[str, str]:
    """Lap number and German lap time, for example ``7`` and ``3,026 s``.

    Both texts are ``-`` until a valid lap exists.
    """
    found = fastest_completed_lap(times)
    if found is None:
        return _MISSING_LAP, _MISSING_LAP
    number, time_ns = found
    return str(number), format_lap_seconds(time_ns)


def format_lap_progress(current: int, target: int) -> str:
    """Current lap against the race target.

    A race without a positive lap target is shown as the current lap only.
    ``format_duration`` is unrelated: this text is a counter, not a time.
    """
    if target < 1:
        return str(current) if current > 0 else EMPTY_DISPLAY
    return f"{current} / {target}"
