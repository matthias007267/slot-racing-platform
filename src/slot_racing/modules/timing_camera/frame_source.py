"""Supplies grayscale frames to the camera timing source.

A frame source knows images and timestamps. It does not know races, drivers,
vehicles or how a crossing is scored.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass

from slot_racing.modules.timing_camera._checks import require_range
from slot_racing.modules.timing_camera.frames import GrayFrame


@dataclass(frozen=True, slots=True)
class TimedFrame:
    """One grayscale frame stamped by whoever captured or synthesized it."""

    frame: GrayFrame
    timestamp_ns: int

    def __post_init__(self) -> None:
        if not isinstance(self.frame, GrayFrame):
            raise TypeError("frame must be a GrayFrame")
        require_range("timestamp_ns", self.timestamp_ns, 0)


class FrameSource(ABC):
    """Where the camera timing source reads frames from.

    ``start`` and ``stop`` follow the timing source. ``pause`` holds frames back
    for as long as the race is paused; ``resume`` lets the next frames through.
    ``poll_frame`` returns the next frame that may be processed, or ``None``.
    """

    def start(self) -> None:  # noqa: B027
        """Begin accepting frames."""

    def stop(self) -> None:  # noqa: B027
        """Stop accepting frames and drop anything not yet delivered."""

    def pause(self) -> None:  # noqa: B027
        """Hold new frames back while a race is paused."""

    def resume(self) -> None:  # noqa: B027
        """Accept frames again after a pause."""

    @abstractmethod
    def poll_frame(self) -> TimedFrame | None:
        """The next frame to process, or ``None`` when nothing is due."""


class ManualFrameSource(FrameSource):
    """Queue of frames pushed by a test or another caller on this thread.

    Frames submitted while stopped or paused are discarded, so a pause cannot
    flush them later. Frames already queued before a pause stay queued and are
    returned again after ``resume``.
    """

    def __init__(self) -> None:
        self._pending: deque[TimedFrame] = deque()
        self._running = False
        self._paused = False

    def start(self) -> None:
        self._running = True
        self._paused = False

    def stop(self) -> None:
        self._running = False
        self._paused = False
        self._pending.clear()

    def pause(self) -> None:
        if self._running:
            self._paused = True

    def resume(self) -> None:
        if self._running:
            self._paused = False

    def submit(self, frame: GrayFrame, timestamp_ns: int) -> None:
        """Queue one frame. Ignored while this source is stopped or paused."""
        delivered = TimedFrame(frame, timestamp_ns)
        if self._running and not self._paused:
            self._pending.append(delivered)

    def poll_frame(self) -> TimedFrame | None:
        if not self._running or self._paused or not self._pending:
            return None
        return self._pending.popleft()
