"""Foreground detection on synthetic or captured grayscale frames.

Each configured zone watches one logical position on one lane. Occupancy is the
number of pixels inside that zone whose intensity differs from a reference
background by at least ``threshold``. The transition from clear to occupied is
one crossing. Further occupied frames do not create another crossing.

The detector knows frames, geometry, lanes, positions and timestamps. It does
not know races, drivers, vehicles or how a crossing becomes a race event.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from slot_racing.modules.timing_camera._checks import (
    require_position_id,
    require_range,
)
from slot_racing.modules.timing_camera.frames import GrayFrame
from slot_racing.modules.timing_camera.geometry import DetectionRoi


class ZoneState(StrEnum):
    """Whether a detection zone currently contains foreground."""

    CLEAR = "clear"
    OCCUPIED = "occupied"


@dataclass(frozen=True, slots=True)
class DetectionZone:
    """One narrow region that reports a single position on a single lane."""

    position_id: str
    lane: int
    roi: DetectionRoi

    def __post_init__(self) -> None:
        require_position_id(self.position_id)
        require_range("lane", self.lane, 1)
        if not isinstance(self.roi, DetectionRoi):
            raise TypeError("roi must be a DetectionRoi")


@dataclass(frozen=True, slots=True)
class DetectorSettings:
    """Everything the detector needs, and nothing about a camera or a race.

    ``threshold`` is the minimum absolute intensity difference from the
    background that counts as foreground. ``min_foreground_pixels`` is how many
    such pixels a zone needs before it counts as occupied.
    """

    zones: tuple[DetectionZone, ...]
    threshold: int = 32
    min_foreground_pixels: int = 1

    def __post_init__(self) -> None:
        require_range("threshold", self.threshold, 1, 255)
        minimum = require_range("min_foreground_pixels", self.min_foreground_pixels, 1)
        if not isinstance(self.zones, tuple):
            raise TypeError("zones must be a tuple")
        if not self.zones:
            raise ValueError("at least one detection zone is required")
        seen: set[tuple[str, int]] = set()
        for zone in self.zones:
            if not isinstance(zone, DetectionZone):
                raise TypeError("zones must contain DetectionZone values")
            key = (zone.position_id, zone.lane)
            if key in seen:
                raise ValueError(
                    f"duplicate detection zone for position {zone.position_id!r} lane {zone.lane}"
                )
            seen.add(key)
            if minimum > zone.roi.area:
                raise ValueError("min_foreground_pixels must fit inside every detection zone")


@dataclass(frozen=True, slots=True, kw_only=True)
class LaneCrossing:
    """One car entering one detection zone.

    ``foreground_pixels`` is technical detail for tests and later filtering.
    The value has no race, driver, vehicle or lap meaning.
    """

    position_id: str
    lane: int
    timestamp_ns: int
    foreground_pixels: int

    def __post_init__(self) -> None:
        require_position_id(self.position_id)
        require_range("lane", self.lane, 1)
        require_range("timestamp_ns", self.timestamp_ns, 0)
        require_range("foreground_pixels", self.foreground_pixels, 1)


class LaneCrossingDetector:
    """Per-zone clear/occupied state over a sequence of grayscale frames.

    Pass a reference ``background`` when the empty track is already known. Without
    one, the first :meth:`observe` call stores its frame as the background and
    reports no crossing. A static object that is part of that background never
    becomes a crossing; a car that appears afterwards does, once, until it leaves.
    """

    def __init__(self, settings: DetectorSettings, background: GrayFrame | None = None) -> None:
        if not isinstance(settings, DetectorSettings):
            raise TypeError("settings must be DetectorSettings")
        if background is not None:
            self._check_frame(background, settings, background)
        self._settings = settings
        self._background = background
        self._states = {(zone.position_id, zone.lane): ZoneState.CLEAR for zone in settings.zones}

    def zone_state(self, position_id: str, lane: int) -> ZoneState:
        """Current state of one configured zone."""
        try:
            return self._states[(position_id, lane)]
        except KeyError:
            raise ValueError(
                f"no detection zone for position {position_id!r} lane {lane}"
            ) from None

    def observe(self, frame: GrayFrame, timestamp_ns: int) -> tuple[LaneCrossing, ...]:
        """Update every zone from ``frame`` and return the new crossings.

        Crossings from the same frame share ``timestamp_ns`` and are ordered by
        lane, then by ``position_id``. The timestamp is the one passed in; this
        method does not read a clock.
        """
        require_range("timestamp_ns", timestamp_ns, 0)
        self._check_frame(frame, self._settings, self._background)
        if self._background is None:
            self._background = frame
            return ()
        crossings: list[LaneCrossing] = []
        for zone in self._settings.zones:
            count = _foreground_pixels(frame, self._background, zone.roi, self._settings.threshold)
            occupied = count >= self._settings.min_foreground_pixels
            key = (zone.position_id, zone.lane)
            if self._states[key] is ZoneState.CLEAR and occupied:
                self._states[key] = ZoneState.OCCUPIED
                crossings.append(
                    LaneCrossing(
                        position_id=zone.position_id,
                        lane=zone.lane,
                        timestamp_ns=timestamp_ns,
                        foreground_pixels=count,
                    )
                )
            elif self._states[key] is ZoneState.OCCUPIED and not occupied:
                self._states[key] = ZoneState.CLEAR
        crossings.sort(key=_crossing_order)
        return tuple(crossings)

    def synchronize(self, frame: GrayFrame) -> None:
        """Match every zone to ``frame`` without emitting a crossing.

        The reference background stays unchanged. A car that is already inside a
        zone is occupied afterwards, so the following frames do not treat it as
        a new entry. Used once after a pause, on the first frame grabbed after
        the resume.
        """
        self._check_frame(frame, self._settings, self._background)
        if self._background is None:
            self._background = frame
            return
        minimum = self._settings.min_foreground_pixels
        for zone in self._settings.zones:
            count = _foreground_pixels(frame, self._background, zone.roi, self._settings.threshold)
            state = ZoneState.OCCUPIED if count >= minimum else ZoneState.CLEAR
            self._states[(zone.position_id, zone.lane)] = state

    @staticmethod
    def _check_frame(
        frame: GrayFrame, settings: DetectorSettings, background: GrayFrame | None
    ) -> None:
        if not isinstance(frame, GrayFrame):
            raise TypeError("frame must be a GrayFrame")
        if background is not None and (
            frame.width != background.width or frame.height != background.height
        ):
            raise ValueError("frame size must match the background")
        for zone in settings.zones:
            roi = zone.roi
            if roi.x + roi.width > frame.width or roi.y + roi.height > frame.height:
                raise ValueError(
                    f"zone {zone.position_id!r} lane {zone.lane} extends outside the frame"
                )


def _foreground_pixels(
    frame: GrayFrame, background: GrayFrame, roi: DetectionRoi, threshold: int
) -> int:
    count = 0
    width = frame.width
    for y in range(roi.y, roi.y + roi.height):
        row = y * width
        for x in range(roi.x, roi.x + roi.width):
            index = row + x
            if abs(frame.pixels[index] - background.pixels[index]) >= threshold:
                count += 1
    return count


def _crossing_order(crossing: LaneCrossing) -> tuple[int, str]:
    return (crossing.lane, crossing.position_id)
