"""Presentation of race standings. Ranking stays in the race engine."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from slot_racing.core.clock import format_duration
from slot_racing.core.domain import RaceStatus
from slot_racing.core.i18n import Translator
from slot_racing.modules.races.runner import LiveRow, RaceSnapshot
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


def positive_lap_times(times: Sequence[int]) -> tuple[int, ...]:
    """Completed lap times the timing rules already accept. Zero is not a lap time."""
    return tuple(time for time in times if time > 0)


def format_lap_time(duration_ns: int | None) -> str:
    """A completed lap as ``m:ss.mmm``. Missing and non-positive times stay ``-``."""
    if duration_ns is None or duration_ns <= 0:
        return _MISSING_LAP
    return format_duration(duration_ns)


def fastest_completed_lap(times: Sequence[int]) -> tuple[int, int] | None:
    """1-based lap number and time of the fastest positive lap.

    Equal times keep the earlier lap. A non-positive time is not a candidate.
    An empty result means no valid lap. The race engine still owns the stored
    result; this only reads the lap list.
    """
    valid = [(index, time) for index, time in enumerate(times) if time > 0]
    if not valid:
        return None
    index, time_ns = min(valid, key=lambda item: (item[1], item[0]))
    return index + 1, time_ns


def best_lap_display(times: Sequence[int]) -> tuple[str, str]:
    """Lap number and German lap time, for example ``7`` and ``3,026 s``.

    Both texts are ``-`` until a valid lap exists.
    """
    found = fastest_completed_lap(times)
    if found is None:
        return _MISSING_LAP, _MISSING_LAP
    number, time_ns = found
    return str(number), format_lap_seconds(time_ns)


def remaining_laps(completed: int, target: int) -> str | None:
    """Laps still to drive. A race without a positive target has no such number."""
    if target < 1:
        return None
    return str(max(0, target - completed))


def remaining_time(elapsed_ns: int, duration_minutes: int | None) -> str | None:
    """Formatted time left in a time trial. No duration means the field does not exist."""
    if duration_minutes is None or duration_minutes < 1:
        return None
    left = duration_minutes * 60 * 1_000_000_000 - max(0, elapsed_ns)
    return format_duration(max(0, left))


def status_tone(snapshot: RaceSnapshot) -> str:
    if snapshot.status is RaceStatus.FINISHED and snapshot.aborted:
        return "error"
    if snapshot.status is RaceStatus.RUNNING:
        return "ok"
    if snapshot.status is RaceStatus.PAUSED:
        return "warn"
    if snapshot.status is RaceStatus.FINISHED:
        return "info"
    return "muted"


def status_presentation(translator: Translator, snapshot: RaceSnapshot) -> tuple[str, str]:
    status_text = translator.translate(f"race.status.{snapshot.status.value}")
    if snapshot.status is RaceStatus.FINISHED and snapshot.aborted:
        status_text = translator.translate("race.status.aborted")
    return status_text, status_tone(snapshot)


def status_message(translate: Callable[[str], str], snapshot: RaceSnapshot) -> str:
    """The standing line when no lap or finish notice is waiting."""
    if snapshot.status is RaceStatus.RUNNING:
        return translate("hud.message.started")
    if snapshot.status is RaceStatus.PAUSED:
        return translate("hud.message.paused")
    if snapshot.status is RaceStatus.FINISHED and snapshot.aborted:
        return translate("hud.message.aborted")
    if snapshot.status is RaceStatus.FINISHED:
        return translate("hud.message.finished")
    return ""


def ranking_cells(
    translator: Translator, snapshot: RaceSnapshot, row: LiveRow, *, ended: bool, paused: bool
) -> tuple[str, ...]:
    """One ranking row, in the same column order as the live table."""
    tr = translator.translate
    return (
        str(row.position),
        row.driver_label,
        row.vehicle_label,
        start_number_text(row.start_number),
        str(row.lane),
        str(row.laps_completed),
        format_progress_cell(row.current_lap, snapshot.laps),
        format_lap_time(row.last_lap_ns),
        format_lap_time(row.best_lap_ns),
        format_duration(row.total_time_ns),
        format_progress_cell(row.laps_completed, snapshot.laps),
        tr(participant_status_key(finished=row.finished, paused=paused, ended=ended)),
    )


def format_lap_progress(current: int, target: int) -> str:
    """Current lap against the race target.

    A race without a positive lap target is shown as the current lap only.
    ``format_duration`` is unrelated: this text is a counter, not a time.
    """
    if target < 1:
        return str(current) if current > 0 else EMPTY_DISPLAY
    return f"{current} / {target}"
