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


def start_number_text(number: int | None) -> str:
    return "-" if number is None else str(number)
