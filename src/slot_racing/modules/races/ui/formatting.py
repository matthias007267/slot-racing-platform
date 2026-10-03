"""Presentation of race standings. Ranking stays in the race engine."""

from __future__ import annotations


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


def format_lap_progress(current: int, target: int) -> str:
    """Current lap against the race target.

    A race without a positive lap target is shown as the current lap only.
    ``format_duration`` is unrelated: this text is a counter, not a time.
    """
    if target < 1:
        return str(current) if current > 0 else EMPTY_DISPLAY
    return f"{current} / {target}"
