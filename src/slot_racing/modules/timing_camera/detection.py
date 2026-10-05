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
        self._settings = settings
        self._states = {(zone.position_id, zone.lane): ZoneState.CLEAR for zone in settings.zones}
        self._width: int | None = None
        self._height: int | None = None
        self._crops: tuple[GrayFrame, ...] | None = None
        self.pixels_compared = 0
        if background is not None:
            self._check_frame(background)
            self._remember(background)

    @property
    def reference_pixels(self) -> int:
        """Pixels retained from the empty track. Only the zones, never the whole picture."""
        crops = self._crops
        if crops is None:
            return 0
        return sum(crop.width * crop.height for crop in crops)

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
        self._check_frame(frame)
        if self._crops is None:
            self._remember(frame)
            return ()
        return self._crossings(self._counts(frame), timestamp_ns)

    def observe_crops(
        self, crops: tuple[GrayFrame, ...], timestamp_ns: int
    ) -> tuple[LaneCrossing, ...]:
        """Update every zone from images that are already cut to the zone size.

        ``crops`` follows the configured zone order. The first call stores them
        as the background and reports no crossing.
        """
        require_range("timestamp_ns", timestamp_ns, 0)
        self._check_crops(crops)
        if self._crops is None:
            self._crops = crops
            return ()
        return self._crossings(self._crop_counts(crops), timestamp_ns)

    def synchronize(self, frame: GrayFrame) -> None:
        """Match every zone to ``frame`` without emitting a crossing.

        The reference background stays unchanged. A car that is already inside a
        zone is occupied afterwards, so the following frames do not treat it as
        a new entry. Used once after a pause, on the first frame grabbed after
        the resume.
        """
        self._check_frame(frame)
        if self._crops is None:
            self._remember(frame)
            return
        self._apply(self._counts(frame))

    def synchronize_crops(self, crops: tuple[GrayFrame, ...]) -> None:
        """Match every zone to pre-cut images without emitting a crossing."""
        self._check_crops(crops)
        if self._crops is None:
            self._crops = crops
            return
        self._apply(self._crop_counts(crops))

    def _counts(self, frame: GrayFrame) -> tuple[int, ...]:
        crops = self._crops
        if crops is None:
            raise RuntimeError("detection has no background")
        threshold = self._settings.threshold
        counts: list[int] = []
        for zone, reference in zip(self._settings.zones, crops, strict=True):
            counts.append(_foreground_pixels(frame, reference, zone.roi, threshold))
            self.pixels_compared += zone.roi.area
        return tuple(counts)

    def _crop_counts(self, crops: tuple[GrayFrame, ...]) -> tuple[int, ...]:
        reference = self._crops
        if reference is None:
            raise RuntimeError("detection has no background")
        threshold = self._settings.threshold
        counts = tuple(
            _foreground_crops(crop, stored, threshold)
            for crop, stored in zip(crops, reference, strict=True)
        )
        self.pixels_compared += sum(crop.width * crop.height for crop in crops)
        return counts

    def _crossings(self, counts: tuple[int, ...], timestamp_ns: int) -> tuple[LaneCrossing, ...]:
        crossings: list[LaneCrossing] = []
        minimum = self._settings.min_foreground_pixels
        for zone, count in zip(self._settings.zones, counts, strict=True):
            occupied = count >= minimum
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

    def _apply(self, counts: tuple[int, ...]) -> None:
        minimum = self._settings.min_foreground_pixels
        for zone, count in zip(self._settings.zones, counts, strict=True):
            state = ZoneState.OCCUPIED if count >= minimum else ZoneState.CLEAR
            self._states[(zone.position_id, zone.lane)] = state

    def _remember(self, frame: GrayFrame) -> None:
        self._width = frame.width
        self._height = frame.height
        self._crops = tuple(frame.crop(zone.roi) for zone in self._settings.zones)

    def _check_frame(self, frame: GrayFrame) -> None:
        if not isinstance(frame, GrayFrame):
            raise TypeError("frame must be a GrayFrame")
        if self._width is not None and (frame.width != self._width or frame.height != self._height):
            raise ValueError("frame size must match the background")
        self._check_zones(frame.width, frame.height)

    def _check_zones(self, width: int, height: int) -> None:
        for zone in self._settings.zones:
            roi = zone.roi
            if roi.x + roi.width > width or roi.y + roi.height > height:
                raise ValueError(
                    f"zone {zone.position_id!r} lane {zone.lane} extends outside the frame"
                )

    def _check_crops(self, crops: tuple[GrayFrame, ...]) -> None:
        if not isinstance(crops, tuple) or any(not isinstance(crop, GrayFrame) for crop in crops):
            raise TypeError("crops must be a tuple of GrayFrame")
        zones = self._settings.zones
        if len(crops) != len(zones):
            raise ValueError("crop count must match the detection zones")
        for crop, zone in zip(crops, zones, strict=True):
            if (crop.width, crop.height) != (zone.roi.width, zone.roi.height):
                raise ValueError("frame size must match the background")


def _foreground_pixels(
    frame: GrayFrame, background: GrayFrame, roi: DetectionRoi, threshold: int
) -> int:
    """Count foreground in ``roi`` only. ``background`` is already that rectangle."""
    count = 0
    width = frame.width
    reference = background.pixels
    index = 0
    for y in range(roi.y, roi.y + roi.height):
        row = y * width + roi.x
        for offset in range(roi.width):
            if abs(frame.pixels[row + offset] - reference[index]) >= threshold:
                count += 1
            index += 1
    return count


def _foreground_crops(frame: GrayFrame, background: GrayFrame, threshold: int) -> int:
    count = 0
    current = frame.pixels
    reference = background.pixels
    for index, pixel in enumerate(current):
        if abs(pixel - reference[index]) >= threshold:
            count += 1
    return count


def _crossing_order(crossing: LaneCrossing) -> tuple[int, str]:
    return (crossing.lane, crossing.position_id)
