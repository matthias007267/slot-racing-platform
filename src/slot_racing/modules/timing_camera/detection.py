"""Directional block detection on grayscale frames.

Each zone is reduced to a small matrix of block brightness values. A crossing
is not a bright pixel: a connected group of blocks has to move through the zone
in the configured travel direction, inside a time window. One pass emits one
crossing. The zone has to return to clear before the next car counts.

The reference starts as the block matrix of the calibration frame. Blocks that
stay quiet follow slow lighting changes. Blocks that belong to a car, or to a
group that is still moving, stay frozen so the car is not learned as background.
A zone is released when that car has gone, even if a smaller static remainder
is still different from the original calibration frame.

The detector knows frames, geometry, lanes, positions and timestamps. It does
not know races, drivers, vehicles or how a crossing becomes a race event.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

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


class ZoneRating(StrEnum):
    """How reliable a zone's analysis grid is. Not a reason to refuse saving."""

    GOOD = "good"
    LIMITED = "limited"
    CRITICAL = "critical"


class TravelDirection(StrEnum):
    """The direction a car is expected to travel through the zone."""

    TOP_TO_BOTTOM = "top_to_bottom"
    BOTTOM_TO_TOP = "bottom_to_top"
    LEFT_TO_RIGHT = "left_to_right"
    RIGHT_TO_LEFT = "right_to_left"


@dataclass(frozen=True, slots=True)
class DetectionZone:
    """One region that reports a single position on a single lane.

    ``check_direction`` compares the measured shift with ``DetectorSettings.direction``.
    When it is off, a large enough component still counts once, until the zone is free
    again. Older callers omit the flag and keep the check.
    """

    position_id: str
    lane: int
    roi: DetectionRoi
    check_direction: bool = True

    def __post_init__(self) -> None:
        require_position_id(self.position_id)
        require_range("lane", self.lane, 1)
        if not isinstance(self.roi, DetectionRoi):
            raise TypeError("roi must be a DetectionRoi")
        if not isinstance(self.check_direction, bool):
            raise TypeError("check_direction must be a bool")


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
    """Internal thresholds derived from one sensitivity value.

    ``min_blocks`` and ``min_shift`` are expressed for a 20 px tile. A finer
    grid converts them to the same area and the same pixel travel.
    """

    difference: float
    min_blocks: int
    min_shift: float
    window_ns: int


@dataclass(frozen=True, slots=True)
class ZoneAssessment:
    """Analysis-grid quality of one zone after it has been scaled to the capture.

    ``warnings`` are stable codes. The UI translates them. ``camera_fps`` does
    not change the rating: speed is unknown, so the interval is only context.
    """

    usable_blocks_x: int
    usable_blocks_y: int
    blocks_in_travel_direction: int
    blocks_cross_direction: int
    block_size: int
    rating: ZoneRating
    warnings: tuple[str, ...]
    camera_fps: int | None
    frame_interval_ms: int | None


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


@dataclass(frozen=True, slots=True)
class ZoneInspection:
    """One zone's real block matrices and the decision just taken.

    Built only while inspection is on. The matrices are the ones ``observe``
    already compared. Nothing here is a second detector.
    """

    position_id: str
    lane: int
    block_size: int
    analysis: tuple[tuple[float, ...], ...]
    reference: tuple[tuple[float, ...], ...]
    difference: tuple[tuple[float, ...], ...]
    active: tuple[tuple[bool, ...], ...]
    changed_blocks: int
    active_ratio: float
    mean_difference: float
    max_difference: float
    strength: float
    shift: float | None
    centroid: tuple[float, float] | None
    sample_count: int
    samples_expired: int
    state: ZoneState
    accepted: bool
    reason: str
    component_blocks: int
    difference_threshold: float
    min_blocks: int
    required_blocks: int
    min_shift: float
    window_ns: int
    direction: TravelDirection
    reference_frozen: bool
    reference_updates: int
    background_stable: bool
    release_candidate: bool
    direction_check: bool


# Quiet blocks follow the picture with this time constant. One second of real
# time moves a stable block most of the way. The step uses the frame timestamp,
# not an assumed frame rate.
_BACKGROUND_TAU_NS = 1_000_000_000

# A connected group must sit still this long, and the zone must be clear,
# before those blocks may join the background. A car that already counted
# stays occupied, so this delay does not absorb a stopped car.
_BACKGROUND_SETTLE_NS = 2_000_000_000

# After a crossing, the group that remains is leftover background when it is
# at most half the vehicle peak of this occupation, in both size and mean
# difference, and it is no longer travelling. Half is a relative split between
# "the object that occupied the zone" and "what was left behind", not a car size.
_RELEASE_RATIO = 0.5

# The remainder has to stay still for this long before the zone is armed again.
# At about 25 fps that is a few frames. It is not a cooldown between cars.
_RELEASE_STABLE_NS = 100_000_000

# Motion below this many blocks of the 20 px reference tile is not travel.
# Every sensitivity profile asks for a larger shift than this (0.75 to 1.25
# reference blocks, 15 to 25 pixels). A finer grid uses the same pixel distance.
_STABLE_SHIFT = 0.5

# The sensitivity numbers were chosen for 20 px tiles. Finer tiles keep that
# area and that pixel shift instead of treating one small tile as one old tile.
REFERENCE_BLOCK_PX = 20

# Named steps of the detection-resolution control. Stored documents keep the
# pixel edge in ``block_size``; a missing value stays at the coarse step.
RESOLUTION_PRESETS: tuple[tuple[str, int], ...] = (
    ("coarse", 20),
    ("medium", 15),
    ("fine", 10),
    ("very_fine", 5),
)

# Usable analysis tiles, after the zone is scaled to the captured picture.
# Travel is the axis the car crosses. Cross is the other axis.
TRAVEL_CRITICAL_BELOW = 6
TRAVEL_VERY_SMALL_BELOW = 8
TRAVEL_LIMITED_BELOW = 10
CROSS_MINIMUM_BLOCKS = 6

# Ignore blends smaller than this gray level so an unchanged frame is not
# reported as a reference update.
_REFERENCE_EPSILON = 0.05


@dataclass
class _ZoneMemory:
    """Runtime background state for one zone. Not saved with the configuration."""

    peak_blocks: int = 0
    peak_strength: float = 0.0
    stable_since_ns: int | None = None
    stable_centroid: tuple[float, float] | None = None
    stable_blocks: int = 0
    last_ns: int | None = None
    reference_updates: int = 0
    reference_frozen: bool = False
    background_stable: bool = False
    release_candidate: bool = False
    pending: np.ndarray | None = None


@dataclass(frozen=True, slots=True)
class FrameInspection:
    """Inspections for one analyzed frame, in configured zone order."""

    reference_ready: bool
    zones: tuple[ZoneInspection, ...]


class LaneCrossingDetector:
    """Per-zone clear/occupied state over a sequence of grayscale frames.

    Pass a reference ``background`` when the empty track is already known. Without
    one, the first :meth:`observe` call stores its block matrix and reports no
    crossing. A static object that is part of that reference never becomes a
    crossing. A car that afterwards moves through the zone in the configured
    direction does, once, until the car has left. A smaller static remainder
    does not keep the zone occupied.
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
        self._inspect = False
        self._last_inspection: FrameInspection | None = None
        self._zone_inspections: list[ZoneInspection] = []
        self._memory = [_ZoneMemory() for _ in settings.zones]
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

    def set_inspection(self, enabled: bool) -> None:
        """Keep the block matrices of each decision. Off by default.

        Enabling this does not change which crossings ``observe`` returns. It
        copies the matrices that decision already used.
        """
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        self._inspect = enabled
        if not enabled:
            self._last_inspection = None
            self._zone_inspections = []

    def last_inspection(self) -> FrameInspection | None:
        """The matrices of the last ``observe``, or ``None`` when inspection is off."""
        if not self._inspect:
            return None
        return self._last_inspection

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
        if self._inspect:
            self._zone_inspections = []
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
        if self._inspect:
            self._last_inspection = FrameInspection(True, tuple(self._zone_inspections))
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
        block = self._blocks[index]
        scaled_blocks = required_blocks_for_area(profile.min_blocks, block)
        required = _required_blocks(scaled_blocks, int(mask.shape[0]), int(mask.shape[1]))
        component = _largest_component(mask, required)
        key = (zone.position_id, zone.lane)
        samples = self._samples[index]
        memory = self._memory[index]
        strength, count, centroid = _component_facts(component, difference)
        if not emit:
            memory.last_ns = None
            return self._synchronize_zone(
                zone,
                key,
                samples,
                component,
                current,
                reference,
                difference,
                mask,
                block,
                strength,
                count,
            )

        assert timestamp_ns is not None
        self._follow_component(memory, timestamp_ns, strength, count, centroid, block)
        crossing, shift, expired, accepted, reason, sample_count = self._classify(
            zone,
            key,
            samples,
            component,
            centroid,
            count,
            timestamp_ns,
            memory,
            mask,
            block,
        )
        if reason in {"clear", "insufficient_motion", "below_size"} and _absorbing(
            memory, mask, self._states[key], component is not None
        ):
            reason = "background_adapting"
        self._prepare_reference(index, current, mask, timestamp_ns, component is not None)
        trace = self._trace(
            zone,
            current,
            reference,
            mask,
            difference,
            component,
            shift,
            centroid,
            sample_count,
            expired,
            block,
            self._states[key],
            accepted,
            reason,
            memory,
        )
        self._apply_reference(index)
        return crossing, trace

    def _classify(
        self,
        zone: DetectionZone,
        key: tuple[str, int],
        samples: list[tuple[int, float, float]],
        component: np.ndarray | None,
        centroid: tuple[float, float] | None,
        count: int,
        timestamp_ns: int,
        memory: _ZoneMemory,
        mask: np.ndarray,
        block: int,
    ) -> tuple[LaneCrossing | None, float | None, int, bool, str, int]:
        """Direction decision. The reference is still the one this frame compared."""
        profile = self._profile
        if component is None or centroid is None:
            expired = 0
            if self._states[key] is ZoneState.OCCUPIED:
                self._states[key] = ZoneState.CLEAR
                samples.clear()
                reason = "released"
            else:
                before = len(samples)
                _drop_old(samples, timestamp_ns, profile.window_ns)
                expired = before - len(samples)
                # A few changed blocks that never form a group are noise, not a car.
                reason = "below_size" if self._settings.debug and bool(np.any(mask)) else "clear"
            return None, None, expired, False, reason, len(samples)

        if self._states[key] is ZoneState.OCCUPIED:
            stable = _held_for(memory, timestamp_ns, _RELEASE_STABLE_NS)
            if memory.release_candidate and stable:
                self._states[key] = ZoneState.CLEAR
                samples.clear()
                memory.peak_blocks = 0
                memory.peak_strength = 0.0
                memory.release_candidate = False
                memory.stable_since_ns = timestamp_ns
                memory.background_stable = False
                return None, None, 0, False, "released_to_background", 0
            return None, None, 0, False, "occupied", len(samples)

        if not zone.check_direction:
            # One connected presence is one crossing. The next one waits until
            # the zone is clear again. Shift, window and sample count do not vote.
            self._states[key] = ZoneState.OCCUPIED
            samples.clear()
            crossing = LaneCrossing(
                position_id=zone.position_id,
                lane=zone.lane,
                timestamp_ns=timestamp_ns,
                foreground_pixels=count,
            )
            return crossing, None, 0, True, "accepted_without_direction_check", 1

        centroid_x, centroid_y = centroid
        samples.append((timestamp_ns, centroid_x, centroid_y))
        before = len(samples)
        _drop_old(samples, timestamp_ns, profile.window_ns)
        expired = before - len(samples)
        used = len(samples)
        if used < 2:
            return None, None, expired, False, "too_short", used
        oldest = samples[0]
        shift = _travel_shift(
            self._settings.direction, oldest[1], oldest[2], centroid_x, centroid_y
        )
        limit = shift_threshold_blocks(profile.min_shift, block, self._settings.block_size)
        if shift >= limit:
            self._states[key] = ZoneState.OCCUPIED
            samples.clear()
            crossing = LaneCrossing(
                position_id=zone.position_id,
                lane=zone.lane,
                timestamp_ns=timestamp_ns,
                foreground_pixels=count,
            )
            return crossing, shift, expired, True, "accepted", used
        if shift <= -limit:
            samples.clear()
            samples.append((timestamp_ns, centroid_x, centroid_y))
            return None, shift, expired, False, "wrong_direction", 1
        return None, shift, expired, False, "insufficient_motion", used

    def _follow_component(
        self,
        memory: _ZoneMemory,
        timestamp_ns: int,
        strength: float,
        count: int,
        centroid: tuple[float, float] | None,
        block: int,
    ) -> None:
        """Track the vehicle peak and whether the current group has stopped moving."""
        memory.release_candidate = False
        if centroid is None or count == 0:
            memory.stable_since_ns = None
            memory.stable_centroid = None
            memory.stable_blocks = 0
            memory.peak_blocks = 0
            memory.peak_strength = 0.0
            memory.background_stable = True
            return
        if count > memory.peak_blocks:
            memory.peak_blocks = count
        if strength > memory.peak_strength:
            memory.peak_strength = strength
        anchor = memory.stable_centroid
        if anchor is None or memory.stable_since_ns is None:
            moved = True
        else:
            distance = math.hypot(centroid[0] - anchor[0], centroid[1] - anchor[1])
            size_delta = abs(count - memory.stable_blocks)
            stable_limit = stable_shift_blocks(block, self._settings.block_size)
            moved = distance >= stable_limit or size_delta > max(
                _size_floor(block, self._settings.block_size), memory.stable_blocks // 4
            )
        if moved:
            memory.stable_since_ns = timestamp_ns
            memory.stable_centroid = centroid
            memory.stable_blocks = count
        since = memory.stable_since_ns
        age = 0 if since is None else timestamp_ns - since
        memory.background_stable = age >= _BACKGROUND_SETTLE_NS
        memory.release_candidate = (
            memory.peak_blocks >= 2
            and count <= memory.peak_blocks * _RELEASE_RATIO
            and memory.peak_strength > 0.0
            and strength <= memory.peak_strength * _RELEASE_RATIO
        )

    def _prepare_reference(
        self,
        index: int,
        current: np.ndarray,
        mask: np.ndarray,
        timestamp_ns: int,
        has_component: bool,
    ) -> None:
        """Decide the reference step. The matrix itself changes only after inspection copies it."""
        memory = self._memory[index]
        memory.pending = None
        references = self._references
        if references is None:
            return
        reference = references[index]
        zone = self._settings.zones[index]
        clear = self._states[(zone.position_id, zone.lane)] is ZoneState.CLEAR
        absorb = clear and (not has_component or memory.background_stable)
        eligible = np.ones(mask.shape, dtype=bool) if absorb else ~mask
        memory.reference_frozen = bool(np.any(mask & ~eligible))
        previous = memory.last_ns
        memory.last_ns = timestamp_ns
        if previous is None or timestamp_ns <= previous or reference.size == 0:
            return
        alpha = 1.0 - math.exp(-(timestamp_ns - previous) / _BACKGROUND_TAU_NS)
        if alpha <= 0.0:
            return
        step = alpha * (current - reference) * eligible
        if float(np.max(np.abs(step))) <= _REFERENCE_EPSILON:
            return
        memory.pending = step
        memory.reference_updates += 1

    def _apply_reference(self, index: int) -> None:
        memory = self._memory[index]
        pending = memory.pending
        memory.pending = None
        references = self._references
        if pending is None or references is None:
            return
        references[index] += pending

    def _synchronize_zone(
        self,
        zone: DetectionZone,
        key: tuple[str, int],
        samples: list[tuple[int, float, float]],
        component: np.ndarray | None,
        current: np.ndarray,
        reference: np.ndarray,
        difference: np.ndarray,
        mask: np.ndarray,
        block: int,
        strength: float,
        count: int,
    ) -> tuple[None, DetectionTrace | None]:
        samples.clear()
        memory = self._memory[self._settings.zones.index(zone)]
        centroid = None if component is None else _centroid(component)
        memory.release_candidate = False
        memory.last_ns = None
        if component is None:
            self._states[key] = ZoneState.CLEAR
            memory.peak_blocks = 0
            memory.peak_strength = 0.0
            memory.background_stable = True
            memory.reference_frozen = False
            return None, self._trace(
                zone,
                current,
                reference,
                mask,
                difference,
                None,
                None,
                None,
                0,
                0,
                block,
                ZoneState.CLEAR,
                False,
                "synchronized_clear",
                memory,
            )
        self._states[key] = ZoneState.OCCUPIED
        memory.peak_blocks = max(memory.peak_blocks, count)
        memory.peak_strength = max(memory.peak_strength, strength)
        memory.background_stable = False
        memory.reference_frozen = True
        return None, self._trace(
            zone,
            current,
            reference,
            mask,
            difference,
            component,
            None,
            centroid,
            0,
            0,
            block,
            ZoneState.OCCUPIED,
            False,
            "synchronized_occupied",
            memory,
        )

    def _trace(
        self,
        zone: DetectionZone,
        current: np.ndarray,
        reference: np.ndarray,
        mask: np.ndarray,
        difference: np.ndarray,
        component: np.ndarray | None,
        shift: float | None,
        centroid: tuple[float, float] | None,
        sample_count: int,
        samples_expired: int,
        block_size: int,
        state: ZoneState,
        accepted: bool,
        reason: str,
        memory: _ZoneMemory,
    ) -> DetectionTrace | None:
        if self._inspect:
            reported = reason
            if reason == "clear" and bool(np.any(mask)):
                # Debug names this below_size. Inspection reports that same fact
                # without turning debug on for the race.
                reported = "below_size"
            self._zone_inspections.append(
                _zone_inspection(
                    zone,
                    current,
                    reference,
                    difference,
                    mask,
                    component,
                    shift,
                    centroid,
                    sample_count,
                    samples_expired,
                    block_size,
                    self._settings.block_size,
                    state,
                    accepted,
                    reported,
                    self._profile,
                    self._settings.direction,
                    memory,
                )
            )
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
        if self._inspect and self._references is not None:
            zones: list[ZoneInspection] = []
            for index, zone in enumerate(self._settings.zones):
                reference = self._references[index]
                zeros = np.zeros(reference.shape, dtype=np.float64)
                inactive = np.zeros(reference.shape, dtype=bool)
                zones.append(
                    _zone_inspection(
                        zone,
                        reference,
                        reference,
                        zeros,
                        inactive,
                        None,
                        None,
                        None,
                        0,
                        0,
                        self._blocks[index],
                        self._settings.block_size,
                        ZoneState.CLEAR,
                        False,
                        "calibrated",
                        self._profile,
                        self._settings.direction,
                        self._memory[index],
                    )
                )
            self._last_inspection = FrameInspection(True, tuple(zones))
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


def create_lane_detector(
    settings: DetectorSettings, background: GrayFrame | None = None
) -> LaneCrossingDetector:
    """The detector a race and the setup diagnosis both construct.

    There is no second detection implementation behind this name.
    """
    return LaneCrossingDetector(settings, background=background)


def resolution_preset_name(block_size: int) -> str:
    """Preset name for a stored tile edge, or ``custom`` when it is not a step."""
    for name, size in RESOLUTION_PRESETS:
        if size == block_size:
            return name
    return "custom"


def minimum_component_area_px(min_blocks: int) -> int:
    """Pixel area of ``min_blocks`` reference tiles. Sensitivity sets ``min_blocks``."""
    return min_blocks * REFERENCE_BLOCK_PX * REFERENCE_BLOCK_PX


def required_blocks_for_area(min_blocks: int, block_size: int) -> int:
    """How many current tiles cover the sensitivity's reference area.

    At 20 px this is ``min_blocks`` itself. A 5 px tile needs sixteen times as
    many tiles for the same area, so a finer grid is not sixteen times easier.
    """
    if block_size < 1:
        raise ValueError("block_size must be positive")
    area = minimum_component_area_px(min_blocks)
    return math.ceil(area / (block_size * block_size))


def shift_threshold_blocks(min_shift: float, block_size: int, requested: int) -> float:
    """Shift limit in tiles of the grid that is actually analyzed.

    ``min_shift`` is in 20 px tiles. The same travel in pixels is
    ``min_shift * 20``. Dividing by the current tile edge keeps that distance.
    A zone thinner than the requested tile is already split into two steps;
    that grid keeps the original tile-count limit, because the zone cannot
    contain a 15 px centroid move.
    """
    if block_size < 1:
        raise ValueError("block_size must be positive")
    if block_size != requested:
        return min_shift
    return min_shift * REFERENCE_BLOCK_PX / block_size


def stable_shift_blocks(block_size: int, requested: int) -> float:
    """Centroid jitter, in current tiles, that still counts as the car moving."""
    return shift_threshold_blocks(_STABLE_SHIFT, block_size, requested)


def _size_floor(block_size: int, requested: int) -> int:
    """Smallest block-count change that counts as a different object.

    One reference tile of area. A shrunk thin-zone grid keeps a change of one
    of its own tiles.
    """
    if block_size != requested:
        return 1
    return max(1, math.ceil((REFERENCE_BLOCK_PX * REFERENCE_BLOCK_PX) / (block_size * block_size)))


def evaluate_detection_zone(
    actual_zone_width: int,
    actual_zone_height: int,
    block_size: int,
    direction: TravelDirection,
    camera_fps: int | None = None,
) -> ZoneAssessment:
    """Rate the analysis grid of a zone that is already in capture pixels.

    Partial edge tiles are ignored, matching :func:`block_means`. The rating
    uses full tiles along the travel direction and across it. It does not
    estimate how many frames a car will spend in the zone.
    """
    if isinstance(actual_zone_width, bool) or isinstance(actual_zone_height, bool):
        raise TypeError("zone size must be an integer")
    if actual_zone_width < 1 or actual_zone_height < 1:
        raise ValueError("zone size must be positive")
    require_range("block_size", block_size, 1, 128)
    if not isinstance(direction, TravelDirection):
        raise TypeError("direction must be a TravelDirection")
    effective = effective_block_size(block_size, actual_zone_width, actual_zone_height, direction)
    usable_x = actual_zone_width // effective
    usable_y = actual_zone_height // effective
    horizontal = direction in (TravelDirection.LEFT_TO_RIGHT, TravelDirection.RIGHT_TO_LEFT)
    travel = usable_x if horizontal else usable_y
    cross = usable_y if horizontal else usable_x
    warnings: list[str] = []
    if travel < TRAVEL_CRITICAL_BELOW:
        rating = ZoneRating.CRITICAL
        warnings.append("travel_critical")
    elif travel < TRAVEL_LIMITED_BELOW:
        rating = ZoneRating.LIMITED
        if travel < TRAVEL_VERY_SMALL_BELOW:
            warnings.append("travel_very_small")
        else:
            warnings.append("travel_limited")
    else:
        rating = ZoneRating.GOOD
    if cross < CROSS_MINIMUM_BLOCKS:
        warnings.append("cross_narrow")
        if rating is ZoneRating.GOOD:
            rating = ZoneRating.LIMITED
    interval: int | None = None
    fps: int | None = None
    if isinstance(camera_fps, int) and not isinstance(camera_fps, bool) and camera_fps > 0:
        fps = camera_fps
        interval = round(1000 / camera_fps)
    return ZoneAssessment(
        usable_blocks_x=usable_x,
        usable_blocks_y=usable_y,
        blocks_in_travel_direction=travel,
        blocks_cross_direction=cross,
        block_size=effective,
        rating=rating,
        warnings=tuple(warnings),
        camera_fps=fps,
        frame_interval_ms=interval,
    )


def sensitivity_profile(level: int) -> SensitivityProfile:
    """Map one sensitivity slider onto the internal detection thresholds.

    ``0`` is strict: a large brightness step, four reference tiles, a shift of
    more than one reference tile and a short window. ``100`` is lenient.
    ``50`` accepts a one-tile step of a small connected group inside about
    1.4 seconds. ``difference`` is the gray level of a block mean. A uniform
    brightness step does not change with the tile size, so it is not scaled.
    ``min_blocks`` and ``min_shift`` are in 20 px reference tiles; the detector
    converts them to the current grid.
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


def _zone_inspection(
    zone: DetectionZone,
    current: np.ndarray,
    reference: np.ndarray,
    difference: np.ndarray,
    mask: np.ndarray,
    component: np.ndarray | None,
    shift: float | None,
    centroid: tuple[float, float] | None,
    sample_count: int,
    samples_expired: int,
    block_size: int,
    requested_block: int,
    state: ZoneState,
    accepted: bool,
    reason: str,
    profile: SensitivityProfile,
    direction: TravelDirection,
    memory: _ZoneMemory,
) -> ZoneInspection:
    """Copy the block matrices this decision compared. Not the camera frame."""
    if difference.size == 0:
        mean_difference = 0.0
        max_difference = 0.0
    else:
        mean_difference = float(difference.mean())
        max_difference = float(difference.max())
    changed = int(np.count_nonzero(mask))
    total = int(mask.size)
    if component is None:
        strength = 0.0
        component_blocks = 0
    else:
        selected = difference[component[:, 0], component[:, 1]]
        strength = float(selected.mean()) if selected.size else 0.0
        component_blocks = int(component.shape[0])
    rows = int(mask.shape[0]) if mask.ndim == 2 else 0
    cols = int(mask.shape[1]) if mask.ndim == 2 else 0
    scaled_blocks = required_blocks_for_area(profile.min_blocks, block_size)
    applied_shift = shift_threshold_blocks(profile.min_shift, block_size, requested_block)
    return ZoneInspection(
        position_id=zone.position_id,
        lane=zone.lane,
        block_size=block_size,
        analysis=_float_matrix(current),
        reference=_float_matrix(reference),
        difference=_float_matrix(difference),
        active=_bool_matrix(mask),
        changed_blocks=changed,
        active_ratio=(changed / total) if total else 0.0,
        mean_difference=mean_difference,
        max_difference=max_difference,
        strength=strength,
        shift=shift,
        centroid=centroid,
        sample_count=sample_count,
        samples_expired=samples_expired,
        state=state,
        accepted=accepted,
        reason=reason,
        component_blocks=component_blocks,
        difference_threshold=profile.difference,
        min_blocks=profile.min_blocks,
        required_blocks=_required_blocks(scaled_blocks, rows, cols),
        min_shift=applied_shift,
        window_ns=profile.window_ns,
        direction=direction,
        reference_frozen=memory.reference_frozen,
        reference_updates=memory.reference_updates,
        background_stable=memory.background_stable,
        release_candidate=memory.release_candidate,
        direction_check=zone.check_direction,
    )


def _float_matrix(values: np.ndarray) -> tuple[tuple[float, ...], ...]:
    if values.size == 0:
        return ()
    listed = cast(list[list[float]], np.asarray(values, dtype=np.float64).tolist())
    return tuple(tuple(float(item) for item in row) for row in listed)


def _bool_matrix(values: np.ndarray) -> tuple[tuple[bool, ...], ...]:
    if values.size == 0:
        return ()
    listed = cast(list[list[bool]], np.asarray(values, dtype=bool).tolist())
    return tuple(tuple(bool(item) for item in row) for row in listed)


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


def _component_facts(
    component: np.ndarray | None, difference: np.ndarray
) -> tuple[float, int, tuple[float, float] | None]:
    if component is None:
        return 0.0, 0, None
    selected = difference[component[:, 0], component[:, 1]]
    strength = float(selected.mean()) if selected.size else 0.0
    return strength, int(component.shape[0]), _centroid(component)


def _held_for(memory: _ZoneMemory, timestamp_ns: int, required_ns: int) -> bool:
    since = memory.stable_since_ns
    if since is None:
        return False
    return timestamp_ns - since >= required_ns


def _absorbing(
    memory: _ZoneMemory, mask: np.ndarray, state: ZoneState, has_component: bool
) -> bool:
    """True when a settled clear zone is taking above-threshold blocks into the background."""
    return (
        state is ZoneState.CLEAR
        and has_component
        and memory.background_stable
        and bool(np.any(mask))
    )


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
