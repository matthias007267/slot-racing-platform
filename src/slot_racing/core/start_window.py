"""Crossings during the start lights, before the race is released.

The race UI and the sensor reader share this protocol. Neither module imports the other.
A false start is a crossing whose capture time falls inside an open start window and
before the release. The capture time is the device time converted by the measured mapping.
The moment the host happened to read the line is not used.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class FalseStartNotice:
    """One lane that crossed before the race was released. It is not a penalty."""

    race_id: int
    lane: int
    position_id: str
    captured_ns: int


class StartWindowWatch(Protocol):
    """Watches the start lights and names false starts. It does not score laps."""

    def open_start_window(self, race_id: int, opened_ns: int) -> None:
        """Begin one start sequence and drop false starts from the previous attempt."""

    def poll_start_window(self) -> tuple[FalseStartNotice, ...]:
        """Read lines that are already waiting and return every false start so far."""

    def close_start_window(
        self, released_ns: int, race_id: int | None = None
    ) -> tuple[FalseStartNotice, ...]:
        """The race is released at ``released_ns``. A capture at or after it is a race crossing.

        A release for another race than the open window is ignored.
        """

    def cancel_start_window(self, race_id: int | None = None) -> None:
        """The start sequence ended without a release. No crossing is a false start any more."""

    def note_start_marker(self, race_id: int, name: str, at_ns: int) -> None:
        """Record one instant of the start sequence for the diagnostic export only.

        The names are ``ui_release_requested``, ``ui_lights_out`` (the view changed its state,
        not a measured screen paint), ``timer_start`` and ``engine_release``.
        """

    def clear_false_starts(self) -> None:
        """Drop the notices. The open window, if any, stays open."""

    def false_starts(self) -> tuple[FalseStartNotice, ...]:
        """Notices collected since the window was opened."""
