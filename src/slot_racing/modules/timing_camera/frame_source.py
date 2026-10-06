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
    """One grayscale frame stamped by whoever captured or synthesized it.

    ``crops`` is set once a race has cut the picture down to the detection zones.
    Each crop follows the zone order and contains only that rectangle. ``frame``
    is then only a size token and is not scanned.
    """

    frame: GrayFrame
    timestamp_ns: int
    crops: tuple[GrayFrame, ...] | None = None
    sequence: int = 0
    """Monotonic capture sequence. Zero means the producer did not assign one."""

    def __post_init__(self) -> None:
        if not isinstance(self.frame, GrayFrame):
            raise TypeError("frame must be a GrayFrame")
        require_range("timestamp_ns", self.timestamp_ns, 0)
        require_range("sequence", self.sequence, 0)
        if self.crops is None:
            return
        if not isinstance(self.crops, tuple) or any(
            not isinstance(crop, GrayFrame) for crop in self.crops
        ):
            raise TypeError("crops must be a tuple of GrayFrame")


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
        """Hold race events back while a race is paused."""

    def resume(self) -> bool:
        """Accept frames again after a pause.

        Return ``True`` when frames from the pause were discarded and the next
        frame should only resynchronize the detector, not count as a crossing.
        """
        return False

    def check(self) -> None:  # noqa: B027
        """Raise when capture has failed since ``start``."""

    @abstractmethod
    def poll_frame(self) -> TimedFrame | None:
        """The next frame to process, or ``None`` when nothing is due."""

    def poll_latest(self) -> TimedFrame | None:
        """The newest queued frame. Older frames are discarded.

        Preview uses this so the picture on screen is the current grab. A live
        camera keeps only that latest frame and detects it on its own worker.
        A manual source still returns frames from :meth:`poll_frame` in the
        order they were submitted.
        """
        latest: TimedFrame | None = None
        while True:
            frame = self.poll_frame()
            if frame is None:
                return latest
            latest = frame


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

    def resume(self) -> bool:
        if self._running:
            self._paused = False
        return False

    def submit(
        self,
        frame: GrayFrame,
        timestamp_ns: int,
        crops: tuple[GrayFrame, ...] | None = None,
    ) -> None:
        """Queue one frame. Ignored while this source is stopped or paused."""
        delivered = TimedFrame(frame, timestamp_ns, crops=crops)
        if self._running and not self._paused:
            self._pending.append(delivered)

    def poll_frame(self) -> TimedFrame | None:
        if not self._running or self._paused or not self._pending:
            return None
        return self._pending.popleft()
