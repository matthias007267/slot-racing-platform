"""Directional block detection on grayscale frames.

Each zone is reduced to a small matrix of block brightness values. A crossing
is not a bright pixel: a connected group of blocks has to move through the zone
in the configured travel direction, inside a time window. One pass emits one
crossing. The zone has to return to clear before the next car counts.

The reference is the block matrix of the calibration frame. It is not blended
while a car may be crossing, so the car cannot become part of the background.
Adapting that reference to a slow lighting change is a later step; a fixed
reference stays stable for the race.

The detector knows frames, geometry, lanes, positions and timestamps. It does
not know races, drivers, vehicles or how a crossing becomes a race event.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np

from slot_racing.modules.timing_camera._checks import (
    require_position_id,
    require_range,
)
from slot_racing.modules.timing_camera.frames import GrayFrame
from slot_racing.modules.timing_camera.geometry import DetectionRoi


class ZoneState(StrEnum):
    """Whether a detection zone currently contains a car."""

    CLEAR = "clear"
    OCCUPIED = "occupied"


class TravelDirection(StrEnum):
    """The direction a car is expected to travel through the zone."""

    TOP_TO_BOTTOM = "top_to_bottom"
    BOTTOM_TO_TOP = "bottom_to_top"
    LEFT_TO_RIGHT = "left_to_right"
    RIGHT_TO_LEFT = "right_to_left"


@dataclass(frozen=True, slots=True)
class DetectionZone:
    """One region that reports a single position on a single lane."""

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

    ``block_size`` is the requested tile edge in pixels. A zone that is thinner
    than that still gets at least two tiles along the travel direction.
    ``sensitivity`` from 0 to 100 drives the brightness difference, the
    connected-block minimum, the required shift and the time window. ``debug``
    keeps a trace of the last decision; it stays off during a race.
    """

    zones: tuple[DetectionZone, ...]
    block_size: int = 20
    sensitivity: int = 50
    direction: TravelDirection = TravelDirection.LEFT_TO_RIGHT
    debug: bool = False

    def __post_init__(self) -> None:
        require_range("block_size", self.block_size, 1, 128)
        require_range("sensitivity", self.sensitivity, 0, 100)
        if not isinstance(self.direction, TravelDirection):
            raise TypeError("direction must be a TravelDirection")
        if not isinstance(self.debug, bool):
            raise TypeError("debug must be a bool")
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


@dataclass(frozen=True, slots=True)
class SensitivityProfile:
    """Internal thresholds derived from one sensitivity value."""

    difference: float
    min_blocks: int
    min_shift: float
    window_ns: int


@dataclass(frozen=True, slots=True, kw_only=True)
class LaneCrossing:
    """One car entering one detection zone.

    ``foreground_pixels`` is the number of blocks in the accepted group. The
    name stays so existing event code keeps one technical field. The value has
    no race, driver, vehicle or lap meaning.
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


@dataclass(frozen=True, slots=True)
class DetectionTrace:
    """Why the last frame was accepted or rejected. Only built when debug is on."""

    position_id: str
    lane: int
    changed_blocks: int
    strength: float
    shift: float | None
    state: ZoneState
    accepted: bool
    reason: str


class LaneCrossingDetector:
    """Per-zone clear/occupied state over a sequence of grayscale frames.

    Pass a reference ``background`` when the empty track is already known. Without
    one, the first :meth:`observe` call stores its block matrix and reports no
    crossing. A static object that is part of that reference never becomes a
    crossing. A car that afterwards moves through the zone in the configured
    direction does, once, until the zone is clear again.
    """

    def __init__(self, settings: DetectorSettings, background: GrayFrame | None = None) -> None:
        if not isinstance(settings, DetectorSettings):
            raise TypeError("settings must be DetectorSettings")
        self._settings = settings
        self._profile = sensitivity_profile(settings.sensitivity)
        self._states = {(zone.position_id, zone.lane): ZoneState.CLEAR for zone in settings.zones}
        self._samples: list[list[tuple[int, float, float]]] = [[] for _ in settings.zones]
        self._width: int | None = None
        self._height: int | None = None
        self._blocks: list[int] = []
        self._references: list[np.ndarray] | None = None
        self._traces: tuple[DetectionTrace, ...] | None = None
        self.pixels_compared = 0
        if background is not None:
            self._check_frame(background)
            self._remember_frame(background)

    @property
    def reference_pixels(self) -> int:
        """Block values retained from the empty track. Never the whole picture."""
        references = self._references
        if references is None:
            return 0
        return sum(int(reference.size) for reference in references)

    def zone_state(self, position_id: str, lane: int) -> ZoneState:
        """Current state of one configured zone."""
        try:
            return self._states[(position_id, lane)]
        except KeyError:
            raise ValueError(
                f"no detection zone for position {position_id!r} lane {lane}"
            ) from None

    def trace(self) -> tuple[DetectionTrace, ...] | None:
        """Last per-zone decision, or ``None`` when debug is off.

        The race path leaves debug off, so this stays empty and the extra
        figures are not computed.
        """
        if not self._settings.debug:
            return None
        return self._traces

    def observe(self, frame: GrayFrame, timestamp_ns: int) -> tuple[LaneCrossing, ...]:
        """Update every zone from ``frame`` and return the new crossings.

        Crossings from the same frame share ``timestamp_ns`` and are ordered by
        lane, then by ``position_id``. The timestamp is the one passed in; this
        method does not read a clock.
        """
        require_range("timestamp_ns", timestamp_ns, 0)
        self._check_frame(frame)
        if self._references is None:
            self._remember_frame(frame)
            self._note_calibration()
            return ()
        arrays = [_roi_array(frame, zone.roi) for zone in self._settings.zones]
        return self._decide(arrays, timestamp_ns, emit=True)

    def observe_crops(
        self, crops: tuple[GrayFrame, ...], timestamp_ns: int
    ) -> tuple[LaneCrossing, ...]:
        """Update every zone from images that are already cut to the zone size.

        ``crops`` follows the configured zone order. The first call stores them
        as the background and reports no crossing.
        """
        require_range("timestamp_ns", timestamp_ns, 0)
        self._check_crops(crops)
        if self._references is None:
            self._remember_crops(crops)
            self._note_calibration()
            return ()
        return self._decide([_roi_array(crop, None) for crop in crops], timestamp_ns, emit=True)

    def synchronize(self, frame: GrayFrame) -> None:
        """Match every zone to ``frame`` without emitting a crossing.

        The reference stays unchanged. A car that is already inside a zone is
        occupied afterwards, so the following frames do not treat it as a new
        entry. Used once after a pause, on the first frame grabbed after the
        resume.
        """
        self._check_frame(frame)
        if self._references is None:
            self._remember_frame(frame)
            self._note_calibration()
            return
        arrays = [_roi_array(frame, zone.roi) for zone in self._settings.zones]
        self._decide(arrays, None, emit=False)

    def synchronize_crops(self, crops: tuple[GrayFrame, ...]) -> None:
        """Match every zone to pre-cut images without emitting a crossing."""
        self._check_crops(crops)
        if self._references is None:
            self._remember_crops(crops)
            self._note_calibration()
            return
        self._decide([_roi_array(crop, None) for crop in crops], None, emit=False)

    def _decide(
        self,
        arrays: list[np.ndarray],
        timestamp_ns: int | None,
        *,
        emit: bool,
    ) -> tuple[LaneCrossing, ...]:
        references = self._references
        if references is None:
            raise RuntimeError("detection has no background")
        crossings: list[LaneCrossing] = []
        traces: list[DetectionTrace] = []
        debug = self._settings.debug
        for index, zone in enumerate(self._settings.zones):
            current = block_means(arrays[index], self._blocks[index])
            reference = references[index]
            if current.shape != reference.shape:
                raise ValueError("frame size must match the background")
            self.pixels_compared += int(current.size)
            crossing, decision = self._zone_step(
                index, zone, current, reference, timestamp_ns, emit
            )
            if crossing is not None:
                crossings.append(crossing)
            if debug and decision is not None:
                traces.append(decision)
        if debug:
            self._traces = tuple(traces)
        crossings.sort(key=_crossing_order)
        return tuple(crossings)

    def _zone_step(
        self,
        index: int,
        zone: DetectionZone,
        current: np.ndarray,
        reference: np.ndarray,
        timestamp_ns: int | None,
        emit: bool,
    ) -> tuple[LaneCrossing | None, DetectionTrace | None]:
        profile = self._profile
        difference = np.abs(current - reference)
        mask = difference >= profile.difference
        required = _required_blocks(profile.min_blocks, int(mask.shape[0]), int(mask.shape[1]))
        component = _largest_component(mask, required)
        key = (zone.position_id, zone.lane)
        samples = self._samples[index]
        if not emit:
            return self._synchronize_zone(zone, key, samples, component, difference, mask)

        assert timestamp_ns is not None
        if component is None:
            return None, self._without_component(zone, key, samples, timestamp_ns, mask, difference)
        centroid_x, centroid_y = _centroid(component)
        count = int(component.shape[0])
        if self._states[key] is ZoneState.OCCUPIED:
            return None, self._trace(
                zone, mask, difference, component, None, ZoneState.OCCUPIED, False, "occupied"
            )
        samples.append((timestamp_ns, centroid_x, centroid_y))
        _drop_old(samples, timestamp_ns, profile.window_ns)
        if len(samples) < 2:
            return None, self._trace(
                zone, mask, difference, component, None, ZoneState.CLEAR, False, "too_short"
            )
        oldest = samples[0]
        shift = _travel_shift(
            self._settings.direction, oldest[1], oldest[2], centroid_x, centroid_y
        )
        if shift >= profile.min_shift:
            self._states[key] = ZoneState.OCCUPIED
            samples.clear()
            crossing = LaneCrossing(
                position_id=zone.position_id,
                lane=zone.lane,
                timestamp_ns=timestamp_ns,
                foreground_pixels=count,
            )
            return crossing, self._trace(
                zone, mask, difference, component, shift, ZoneState.OCCUPIED, True, "accepted"
            )
        if shift <= -profile.min_shift:
            samples.clear()
            samples.append((timestamp_ns, centroid_x, centroid_y))
            return None, self._trace(
                zone,
                mask,
                difference,
                component,
                shift,
                ZoneState.CLEAR,
                False,
                "wrong_direction",
            )
        return None, self._trace(
            zone,
            mask,
            difference,
            component,
            shift,
            ZoneState.CLEAR,
            False,
            "insufficient_motion",
        )

    def _synchronize_zone(
        self,
        zone: DetectionZone,
        key: tuple[str, int],
        samples: list[tuple[int, float, float]],
        component: np.ndarray | None,
        difference: np.ndarray,
        mask: np.ndarray,
    ) -> tuple[None, DetectionTrace | None]:
        samples.clear()
        if component is None:
            self._states[key] = ZoneState.CLEAR
            return None, self._trace(
                zone, mask, difference, None, None, ZoneState.CLEAR, False, "synchronized_clear"
            )
        self._states[key] = ZoneState.OCCUPIED
        return None, self._trace(
            zone,
            mask,
            difference,
            component,
            None,
            ZoneState.OCCUPIED,
            False,
            "synchronized_occupied",
        )

    def _without_component(
        self,
        zone: DetectionZone,
        key: tuple[str, int],
        samples: list[tuple[int, float, float]],
        timestamp_ns: int,
        mask: np.ndarray,
        difference: np.ndarray,
    ) -> DetectionTrace | None:
        if self._states[key] is ZoneState.OCCUPIED:
            self._states[key] = ZoneState.CLEAR
            samples.clear()
            reason = "released"
        else:
            _drop_old(samples, timestamp_ns, self._profile.window_ns)
            # A few changed blocks that never form a group are noise, not a car.
            reason = "below_size" if self._settings.debug and bool(np.any(mask)) else "clear"
        return self._trace(zone, mask, difference, None, None, ZoneState.CLEAR, False, reason)

    def _trace(
        self,
        zone: DetectionZone,
        mask: np.ndarray,
        difference: np.ndarray,
        component: np.ndarray | None,
        shift: float | None,
        state: ZoneState,
        accepted: bool,
        reason: str,
    ) -> DetectionTrace | None:
        if not self._settings.debug:
            return None
        if component is None:
            strength = 0.0
        else:
            selected = difference[component[:, 0], component[:, 1]]
            strength = float(selected.mean()) if selected.size else 0.0
        return DetectionTrace(
            position_id=zone.position_id,
            lane=zone.lane,
            changed_blocks=int(np.count_nonzero(mask)),
            strength=strength,
            shift=shift,
            state=state,
            accepted=accepted,
            reason=reason,
        )

    def _note_calibration(self) -> None:
        if not self._settings.debug:
            return
        self._traces = tuple(
            DetectionTrace(
                position_id=zone.position_id,
                lane=zone.lane,
                changed_blocks=0,
                strength=0.0,
                shift=None,
                state=ZoneState.CLEAR,
                accepted=False,
                reason="calibrated",
            )
            for zone in self._settings.zones
        )

    def _remember_frame(self, frame: GrayFrame) -> None:
        self._width = frame.width
        self._height = frame.height
        references: list[np.ndarray] = []
        blocks: list[int] = []
        for zone in self._settings.zones:
            block = effective_block_size(
                self._settings.block_size,
                zone.roi.width,
                zone.roi.height,
                self._settings.direction,
            )
            references.append(block_means(_roi_array(frame, zone.roi), block))
            blocks.append(block)
        self._references = references
        self._blocks = blocks

    def _remember_crops(self, crops: tuple[GrayFrame, ...]) -> None:
        references: list[np.ndarray] = []
        blocks: list[int] = []
        for crop in crops:
            block = effective_block_size(
                self._settings.block_size, crop.width, crop.height, self._settings.direction
            )
            references.append(block_means(_roi_array(crop, None), block))
            blocks.append(block)
        self._references = references
        self._blocks = blocks

    def _check_frame(self, frame: GrayFrame) -> None:
        if not isinstance(frame, GrayFrame):
            raise TypeError("frame must be a GrayFrame")
        if self._width is not None and (frame.width != self._width or frame.height != self._height):
            raise ValueError("frame size must match the background")
        for zone in self._settings.zones:
            roi = zone.roi
            if roi.x + roi.width > frame.width or roi.y + roi.height > frame.height:
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


def sensitivity_profile(level: int) -> SensitivityProfile:
    """Map one sensitivity slider onto the internal detection thresholds.

    ``0`` is strict: a large brightness step, four connected blocks, a shift of
    more than one block and a short window. ``100`` is lenient. ``50`` accepts
    a one-block step of a small connected group inside about 1.4 seconds.
    """
    require_range("sensitivity", level, 0, 100)
    unit = level / 100
    if level < 40:
        minimum = 4
    elif level < 75:
        minimum = 3
    else:
        minimum = 2
    return SensitivityProfile(
        difference=60 - unit * 44,
        min_blocks=minimum,
        min_shift=1.25 - unit * 0.5,
        window_ns=round((0.3 + unit * 2.2) * 1_000_000_000),
    )


def effective_block_size(
    requested: int, width: int, height: int, direction: TravelDirection
) -> int:
    """Tile edge that still leaves a direction to travel, even in a thin zone.

    The requested size is the normal case (20 pixels). A zone narrower than
    that along the travel axis uses a smaller tile so the grid has two columns
    or two rows. The other axis keeps at least one tile.
    """
    horizontal = direction in (TravelDirection.LEFT_TO_RIGHT, TravelDirection.RIGHT_TO_LEFT)
    along = width if horizontal else height
    across = height if horizontal else width
    along_limit = max(1, along // 2)
    across_limit = max(1, across)
    return max(1, min(requested, along_limit, across_limit))


def block_means(image: np.ndarray, block: int) -> np.ndarray:
    """Mean gray of every full ``block`` tile.

    The array is one zone, already contiguous. It is reshaped to
    ``(rows, block, cols, block)`` and reduced on the two tile axes. That is one
    NumPy reduction, not a Python loop over pixels, and the result is the small
    matrix detection uses from here on. Rows and columns that do not fill a
    tile are dropped so the reshape is exact and no partial tile is allocated.
    """
    rows = int(image.shape[0]) // block
    cols = int(image.shape[1]) // block
    trimmed = image[: rows * block, : cols * block]
    folded = trimmed.reshape(rows, block, cols, block)
    return folded.mean(axis=(1, 3))


def _roi_array(frame: GrayFrame, roi: DetectionRoi | None) -> np.ndarray:
    """The pixels detection needs, as a contiguous 2-D view or a ROI copy.

    Packed camera bytes stay a buffer view. Only a zone that is not already
    contiguous is copied, and that copy is the rectangle, not the whole frame.
    """
    if isinstance(frame.pixels, bytes):
        flat = np.frombuffer(frame.pixels, dtype=np.uint8)
    else:
        flat = np.asarray(frame.pixels, dtype=np.uint8)
    image = flat.reshape(frame.height, frame.width)
    if roi is not None:
        image = image[roi.y : roi.y + roi.height, roi.x : roi.x + roi.width]
    if image.flags.c_contiguous:
        return image
    return np.ascontiguousarray(image)


def _required_blocks(minimum: int, rows: int, cols: int) -> int:
    """How many connected blocks count as a car on this grid.

    A grid of one or two cells can only show a car as a single cell moving to
    the other cell, so one cell is enough there. Larger grids never accept a
    lone cell: the sensitivity minimum applies, but it is capped so a small
    zone can still qualify.
    """
    cells = rows * cols
    if cells <= 2:
        return 1
    return min(minimum, max(2, cells // 3))


def _largest_component(mask: np.ndarray, minimum: int) -> np.ndarray | None:
    """4-connected component of changed blocks, or ``None`` when none qualifies.

    Only changed cells are visited. Unchanged blocks are never walked.
    """
    coords = np.argwhere(mask)
    count = int(coords.shape[0])
    if count < minimum:
        return None
    parent = list(range(count))
    size = [1] * count
    lookup = {(int(coords[index, 0]), int(coords[index, 1])): index for index in range(count)}

    def find(item: int) -> int:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def unite(left: int, right: int) -> None:
        left = find(left)
        right = find(right)
        if left == right:
            return
        if size[left] < size[right]:
            left, right = right, left
        parent[right] = left
        size[left] += size[right]

    for index in range(count):
        row = int(coords[index, 0])
        col = int(coords[index, 1])
        above = lookup.get((row - 1, col))
        if above is not None:
            unite(index, above)
        before = lookup.get((row, col - 1))
        if before is not None:
            unite(index, before)

    best = 0
    best_size = 0
    for index in range(count):
        root = find(index)
        if size[root] > best_size:
            best = root
            best_size = size[root]
    if best_size < minimum:
        return None
    selected = [index for index in range(count) if find(index) == best]
    return coords[np.asarray(selected, dtype=np.intp)]


def _centroid(cells: np.ndarray) -> tuple[float, float]:
    return float(cells[:, 1].mean()), float(cells[:, 0].mean())


def _travel_shift(direction: TravelDirection, x0: float, y0: float, x1: float, y1: float) -> float:
    """Positive when the centroid moved along ``direction``. Image y grows downward."""
    if direction is TravelDirection.LEFT_TO_RIGHT:
        return x1 - x0
    if direction is TravelDirection.RIGHT_TO_LEFT:
        return x0 - x1
    if direction is TravelDirection.TOP_TO_BOTTOM:
        return y1 - y0
    return y0 - y1


def _drop_old(samples: list[tuple[int, float, float]], now: int, window_ns: int) -> None:
    cutoff = now - window_ns
    start = 0
    while start < len(samples) and samples[start][0] < cutoff:
        start += 1
    if start:
        del samples[:start]


def _crossing_order(crossing: LaneCrossing) -> tuple[int, str]:
    return (crossing.lane, crossing.position_id)
