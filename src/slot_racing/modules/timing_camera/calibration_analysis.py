"""Choose a detection zone by replaying one recording through the existing detector.

The capture resolution is the resolution that was recorded. ``block_size`` is
the internal analysis grid the detector already uses. The setup preview is a
separate, scaled picture. Replaying a recording at one capture size does not
validate a different camera resolution.

A matching event count is not enough: a missed lap and a false trigger can
cancel out. Short and long gaps are counted as well. When the recording does
not support a choice, the report says so and is not applicable.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass

import numpy as np

from slot_racing.modules.timing_camera.calibration_polygon import (
    CalibrationPolygon,
    contains_point,
    rectangle_inside_polygon,
    rois_overlap,
)
from slot_racing.modules.timing_camera.calibration_recording import (
    CalibrationTape,
    RecordedFrame,
    RecordingStats,
)
from slot_racing.modules.timing_camera.configuration import (
    NormalizedRoi,
    pixels_to_roi,
    roi_to_pixels,
)
from slot_racing.modules.timing_camera.detection import (
    RESOLUTION_PRESETS,
    DetectionZone,
    DetectorSettings,
    LaneCrossingDetector,
    TravelDirection,
)
from slot_racing.modules.timing_camera.frames import GrayFrame
from slot_racing.modules.timing_camera.geometry import DetectionRoi

_SHORT_RATIO = 0.45
_LONG_RATIO = 1.6
_SENSITIVITIES = (20, 40, 60, 80)
_RATING_RANK = {"good": 0, "limited": 1, "unclear": 2, "insufficient": 3}

Progress = Callable[[int, int], None]
Cancelled = Callable[[], bool]


@dataclass(frozen=True, slots=True)
class IntervalAssessment:
    """What the gaps between crossings say. The lap count is only a reference."""

    detected: int
    missed: int
    ghosts: int
    median_ns: int | None
    rating: str


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    """The measured outcome of one recording. Numbers are taken from that recording."""

    target_laps: int
    detected: int
    missed: int
    ghosts: int
    sensitivity: int
    block_size: int
    direction: TravelDirection
    check_direction: bool
    roi: NormalizedRoi | None
    camera_fps: float | None
    capture_width: int
    capture_height: int
    saved_width: int
    saved_height: int
    dropped_frames: int
    recorded_frames: int
    bytes_used: int
    analysis_ns: int
    rating: str
    applicable: bool
    reasons: tuple[str, ...]
    compared: int


def assess_intervals(timestamps: Sequence[int], target_laps: int) -> IntervalAssessment:
    """Rate one detector run from its crossing times and the requested lap count."""
    if target_laps < 1:
        raise ValueError("target_laps must be positive")
    ordered = tuple(timestamps)
    if len(ordered) < 3:
        return IntervalAssessment(len(ordered), 0, 0, None, "insufficient")
    gaps = [ordered[index + 1] - ordered[index] for index in range(len(ordered) - 1)]
    if any(gap <= 0 for gap in gaps):
        return IntervalAssessment(len(ordered), 0, 0, None, "unclear")
    median = _median(gaps)
    ghosts = sum(1 for gap in gaps if gap < _SHORT_RATIO * median)
    missed = 0
    for gap in gaps:
        if gap > _LONG_RATIO * median:
            missed += max(1, round(gap / median) - 1)
    count_gap = abs(len(ordered) - target_laps)
    anomalies = ghosts + missed
    close_count = count_gap <= max(1, round(0.02 * target_laps))
    wide_count = count_gap > max(2, round(0.1 * target_laps))
    if anomalies == 0 and close_count:
        rating = "good"
    elif anomalies == 0 and wide_count:
        # Even spacing with the wrong total is not proof that every lap was seen.
        rating = "unclear"
    elif anomalies <= max(2, round(0.05 * target_laps)) and not wide_count:
        rating = "limited"
    else:
        rating = "unclear"
    return IntervalAssessment(len(ordered), missed, ghosts, median, rating)


def analyze_recording(
    tape: CalibrationTape,
    *,
    polygon: CalibrationPolygon,
    position_id: str,
    lane: int,
    other_rois: Sequence[NormalizedRoi],
    direction: TravelDirection,
    check_direction: bool,
    block_size: int,
    target_laps: int,
    saved_width: int,
    saved_height: int,
    margin: float = 0.01,
    progress: Progress | None = None,
    cancelled: Cancelled | None = None,
) -> CalibrationReport:
    """Search inside ``polygon`` and return the best applicable setting, or a refusal."""
    started = time.perf_counter_ns()
    stats = tape.stats()
    if tape.overflow or "not_enough_disk" in stats.errors or "resolution_changed" in stats.errors:
        return _refused(
            tape,
            target_laps,
            direction,
            check_direction,
            block_size,
            saved_width,
            saved_height,
            ("recording_unusable",),
            started,
            0,
        )
    if not polygon.is_valid or stats.frames < 3:
        return _refused(
            tape,
            target_laps,
            direction,
            check_direction,
            block_size,
            saved_width,
            saved_height,
            ("little_motion",),
            started,
            0,
        )

    def frames() -> Iterator[RecordedFrame]:
        """One pass over the tape. A cancel is noticed without finishing the pass."""
        if cancelled is not None and cancelled():
            raise CalibrationCancelledError
        produced = tape.frames()
        for index, sample in enumerate(produced):
            if cancelled is not None and index % 48 == 0 and cancelled():
                raise CalibrationCancelledError
            yield sample

    first = next(frames(), None)
    if first is None:
        return _refused(
            tape,
            target_laps,
            direction,
            check_direction,
            block_size,
            saved_width,
            saved_height,
            ("little_motion",),
            started,
            0,
        )
    width, height = first.width, first.height
    motion = _motion_roi(frames, polygon, width, height, stats.frames)
    candidates = _candidates(polygon, motion, other_rois, width, height, margin)
    if not candidates:
        return _refused(
            tape,
            target_laps,
            direction,
            check_direction,
            block_size,
            saved_width,
            saved_height,
            ("little_motion",),
            started,
            0,
        )
    best: tuple[tuple[int, ...], IntervalAssessment, NormalizedRoi, int, int] | None = None
    compared = 0
    planned = len(candidates) * len(_SENSITIVITIES) + 8
    for roi in candidates:
        for sensitivity in _SENSITIVITIES:
            if cancelled is not None and cancelled():
                raise CalibrationCancelledError
            assessment = _score(
                frames,
                roi,
                width,
                height,
                position_id,
                lane,
                block_size,
                sensitivity,
                direction,
                check_direction,
                target_laps,
            )
            compared += 1
            if progress is not None:
                progress(compared, planned)
            best = _prefer(best, assessment, roi, sensitivity, block_size)
    if best is None:
        return _refused(
            tape,
            target_laps,
            direction,
            check_direction,
            block_size,
            saved_width,
            saved_height,
            ("little_motion",),
            started,
            compared,
        )
    _rank, assessment, roi, sensitivity, chosen_block = best
    sensitivity = _refine_sensitivity(
        frames,
        roi,
        width,
        height,
        position_id,
        lane,
        chosen_block,
        sensitivity,
        direction,
        check_direction,
        target_laps,
    )
    assessment = _score(
        frames,
        roi,
        width,
        height,
        position_id,
        lane,
        chosen_block,
        sensitivity,
        direction,
        check_direction,
        target_laps,
    )
    compared += 3
    if assessment.rating != "good":
        for preset_name, preset_size in RESOLUTION_PRESETS:
            del preset_name
            if preset_size == chosen_block:
                continue
            alternative = _score(
                frames,
                roi,
                width,
                height,
                position_id,
                lane,
                preset_size,
                sensitivity,
                direction,
                check_direction,
                target_laps,
            )
            compared += 1
            best = _prefer(best, alternative, roi, sensitivity, preset_size)
        _rank, assessment, roi, sensitivity, chosen_block = best
        assessment = _score(
            frames,
            roi,
            width,
            height,
            position_id,
            lane,
            chosen_block,
            sensitivity,
            direction,
            check_direction,
            target_laps,
        )
    direction, check_direction, assessment = _direction_probe(
        frames,
        roi,
        width,
        height,
        position_id,
        lane,
        chosen_block,
        sensitivity,
        direction,
        check_direction,
        target_laps,
        assessment,
    )
    compared += 5
    reasons = _reasons(assessment, target_laps, stats, saved_width, saved_height, bool(other_rois))
    applicable = assessment.rating in ("good", "limited")
    if not applicable:
        reasons = (*reasons, "repeat_calibration")
    return CalibrationReport(
        target_laps=target_laps,
        detected=assessment.detected,
        missed=assessment.missed,
        ghosts=assessment.ghosts,
        sensitivity=sensitivity,
        block_size=chosen_block,
        direction=direction,
        check_direction=check_direction,
        roi=roi,
        camera_fps=stats.fps,
        capture_width=width,
        capture_height=height,
        saved_width=saved_width,
        saved_height=saved_height,
        dropped_frames=stats.dropped,
        recorded_frames=stats.frames,
        bytes_used=stats.bytes_used,
        analysis_ns=time.perf_counter_ns() - started,
        rating=assessment.rating,
        applicable=applicable,
        reasons=reasons,
        compared=compared,
    )


class CalibrationCancelledError(Exception):
    """The user left the analysis before it finished. Nothing is applied."""


def _score(
    frames: Callable[[], object],
    roi: NormalizedRoi,
    width: int,
    height: int,
    position_id: str,
    lane: int,
    block_size: int,
    sensitivity: int,
    direction: TravelDirection,
    check_direction: bool,
    target_laps: int,
) -> IntervalAssessment:
    pixels = roi_to_pixels(roi, width, height)
    zone = DetectionZone(position_id, lane, pixels, check_direction=check_direction)
    settings = DetectorSettings(
        (zone,),
        block_size=block_size,
        sensitivity=sensitivity,
        direction=direction,
    )
    detector = LaneCrossingDetector(settings)
    times: list[int] = []
    produced = frames()
    if not isinstance(produced, Iterator):
        raise TypeError("frames must return an iterator")
    for sample in produced:
        crop = _zone_crop(sample, pixels)
        if crop is None:
            continue
        for crossing in detector.observe_crops((crop,), sample.timestamp_ns):
            times.append(crossing.timestamp_ns)
    return assess_intervals(times, target_laps)


def _refine_sensitivity(
    frames: Callable[[], object],
    roi: NormalizedRoi,
    width: int,
    height: int,
    position_id: str,
    lane: int,
    block_size: int,
    sensitivity: int,
    direction: TravelDirection,
    check_direction: bool,
    target_laps: int,
) -> int:
    best = sensitivity
    best_rank = _score(
        frames,
        roi,
        width,
        height,
        position_id,
        lane,
        block_size,
        sensitivity,
        direction,
        check_direction,
        target_laps,
    )
    for candidate in (sensitivity - 10, sensitivity + 10):
        if candidate < 0 or candidate > 100:
            continue
        assessment = _score(
            frames,
            roi,
            width,
            height,
            position_id,
            lane,
            block_size,
            candidate,
            direction,
            check_direction,
            target_laps,
        )
        if _better(assessment, candidate, block_size, best_rank, best, block_size):
            best = candidate
            best_rank = assessment
    return best


def _direction_probe(
    frames: Callable[[], object],
    roi: NormalizedRoi,
    width: int,
    height: int,
    position_id: str,
    lane: int,
    block_size: int,
    sensitivity: int,
    direction: TravelDirection,
    check_direction: bool,
    target_laps: int,
    current: IntervalAssessment,
) -> tuple[TravelDirection, bool, IntervalAssessment]:
    """Reuse the detector's direction check. A direction is kept only when it is clearly better."""
    chosen_direction = direction
    chosen_check = check_direction
    chosen = current
    unchecked = _score(
        frames,
        roi,
        width,
        height,
        position_id,
        lane,
        block_size,
        sensitivity,
        direction,
        False,
        target_laps,
    )
    if _clearly_better(unchecked, chosen):
        chosen = unchecked
        chosen_check = False
    for candidate in TravelDirection:
        checked = _score(
            frames,
            roi,
            width,
            height,
            position_id,
            lane,
            block_size,
            sensitivity,
            candidate,
            True,
            target_laps,
        )
        if _clearly_better(checked, chosen):
            chosen = checked
            chosen_direction = candidate
            chosen_check = True
    return chosen_direction, chosen_check, chosen


def _clearly_better(candidate: IntervalAssessment, current: IntervalAssessment) -> bool:
    if _RATING_RANK[candidate.rating] > _RATING_RANK[current.rating]:
        return False
    if _RATING_RANK[candidate.rating] < _RATING_RANK[current.rating]:
        return True
    return (candidate.ghosts + candidate.missed) + 1 < (current.ghosts + current.missed)


def _prefer(
    best: tuple[tuple[int, ...], IntervalAssessment, NormalizedRoi, int, int] | None,
    assessment: IntervalAssessment,
    roi: NormalizedRoi,
    sensitivity: int,
    block_size: int,
) -> tuple[tuple[int, ...], IntervalAssessment, NormalizedRoi, int, int]:
    key = _key(assessment, roi, block_size)
    if best is None or key < best[0]:
        return (key, assessment, roi, sensitivity, block_size)
    return best


def _better(
    assessment: IntervalAssessment,
    sensitivity: int,
    block_size: int,
    current: IntervalAssessment,
    current_sensitivity: int,
    current_block: int,
) -> bool:
    del sensitivity, current_sensitivity
    return _key(assessment, _unit_roi(), block_size) < _key(current, _unit_roi(), current_block)


def _key(assessment: IntervalAssessment, roi: NormalizedRoi, block_size: int) -> tuple[int, ...]:
    return (
        _RATING_RANK[assessment.rating],
        assessment.ghosts,
        assessment.missed,
        -block_size,
        round(roi.width * roi.height * 1_000_000),
    )


def _unit_roi() -> NormalizedRoi:
    return NormalizedRoi(x=0, y=0, width=1, height=1)


def _motion_roi(
    frames: Callable[[], object],
    polygon: CalibrationPolygon,
    width: int,
    height: int,
    frame_count: int,
) -> NormalizedRoi | None:
    # Pairs, not every Nth frame: a stride that matches the lap can land only on
    # the empty frames and then report that the car never moved.
    step = max(2, frame_count // 40)
    produced = frames()
    if not isinstance(produced, Iterator):
        raise TypeError("frames must return an iterator")
    wanted: set[int] = set()
    index = 0
    while index < frame_count and len(wanted) < 80:
        wanted.add(index)
        if index + 1 < frame_count:
            wanted.add(index + 1)
        index += step
    chosen = [sample for index, sample in enumerate(produced) if index in wanted]
    if len(chosen) < 2:
        return None
    origin_x = min(sample.crop_x for sample in chosen)
    origin_y = min(sample.crop_y for sample in chosen)
    far_x = max(sample.crop_x + sample.crop_width for sample in chosen)
    far_y = max(sample.crop_y + sample.crop_height for sample in chosen)
    box_width = far_x - origin_x
    box_height = far_y - origin_y
    accumulator: np.ndarray | None = None
    previous: np.ndarray | None = None
    for sample in chosen:
        image = np.zeros((box_height, box_width), dtype=np.int16)
        crop = np.frombuffer(sample.pixels, dtype=np.uint8).reshape(
            sample.crop_height, sample.crop_width
        )
        y_value = sample.crop_y - origin_y
        x_value = sample.crop_x - origin_x
        image[y_value : y_value + sample.crop_height, x_value : x_value + sample.crop_width] = crop
        if previous is not None:
            difference = np.abs(image - previous)
            accumulator = difference if accumulator is None else accumulator + difference
        previous = image
    if accumulator is None:
        return None
    active = accumulator[accumulator > 0]
    if active.size < 8:
        return None
    level = float(np.percentile(active, 60))
    rows, columns = np.where(accumulator >= level)
    if rows.size == 0:
        return None
    kept_x: list[int] = []
    kept_y: list[int] = []
    for row, column in zip(rows.tolist(), columns.tolist(), strict=True):
        if contains_point(polygon, (column + origin_x) / width, (row + origin_y) / height):
            kept_x.append(column)
            kept_y.append(row)
    if len(kept_x) < 4:
        return None
    left = min(kept_x) + origin_x
    top = min(kept_y) + origin_y
    right = max(kept_x) + origin_x + 1
    bottom = max(kept_y) + origin_y + 1
    return pixels_to_roi(left, top, max(1, right - left), max(1, bottom - top), width, height)


def _candidates(
    polygon: CalibrationPolygon,
    motion: NormalizedRoi | None,
    others: Sequence[NormalizedRoi],
    width: int,
    height: int,
    margin: float,
) -> tuple[NormalizedRoi, ...]:
    if motion is None:
        return ()
    found: list[NormalizedRoi] = []
    scales = (1.0, 0.75, 1.35)
    shifts = ((0.0, 0.0), (-0.2, 0.0), (0.2, 0.0), (0.0, -0.2), (0.0, 0.2))
    for scale in scales:
        for shift_x, shift_y in shifts:
            if scale != 1.0 and (shift_x or shift_y):
                continue
            roi = _placed(motion, scale, shift_x, shift_y)
            if roi is None:
                continue
            if not rectangle_inside_polygon(polygon, roi, margin=margin):
                continue
            if any(rois_overlap(roi, other) for other in others):
                continue
            try:
                roi_to_pixels(roi, width, height)
            except ValueError:
                continue
            if not any(_same(roi, existing) for existing in found):
                found.append(roi)
            if len(found) == 8:
                return tuple(found)
    return tuple(found)


def _placed(
    motion: NormalizedRoi, scale: float, shift_x: float, shift_y: float
) -> NormalizedRoi | None:
    width = min(1.0, motion.width * scale)
    height = min(1.0, motion.height * scale)
    if width <= 0 or height <= 0:
        return None
    x_value = motion.x + (motion.width - width) / 2 + shift_x * motion.width
    y_value = motion.y + (motion.height - height) / 2 + shift_y * motion.height
    x_value = min(max(0.0, x_value), 1 - width)
    y_value = min(max(0.0, y_value), 1 - height)
    try:
        return NormalizedRoi(x=x_value, y=y_value, width=width, height=height)
    except ValueError:
        return None


def _same(left: NormalizedRoi, right: NormalizedRoi) -> bool:
    return (
        abs(left.x - right.x) < 0.01
        and abs(left.y - right.y) < 0.01
        and abs(left.width - right.width) < 0.01
        and abs(left.height - right.height) < 0.01
    )


def _zone_crop(sample: RecordedFrame, roi: DetectionRoi) -> GrayFrame | None:
    if (
        roi.x < sample.crop_x
        or roi.y < sample.crop_y
        or roi.x + roi.width > sample.crop_x + sample.crop_width
        or roi.y + roi.height > sample.crop_y + sample.crop_height
    ):
        return None
    local_x = roi.x - sample.crop_x
    local_y = roi.y - sample.crop_y
    rows = []
    for y_value in range(local_y, local_y + roi.height):
        start = y_value * sample.crop_width + local_x
        rows.append(sample.pixels[start : start + roi.width])
    return GrayFrame(roi.width, roi.height, b"".join(rows))


def _reasons(
    assessment: IntervalAssessment,
    target_laps: int,
    stats: RecordingStats,
    saved_width: int,
    saved_height: int,
    has_neighbors: bool,
) -> tuple[str, ...]:
    reasons = ["processing_is_block_size"]
    if assessment.ghosts:
        reasons.append("short_intervals")
    if assessment.missed:
        reasons.append("long_intervals")
    if assessment.detected != target_laps:
        reasons.append("count_differs")
    if assessment.rating == "good":
        reasons.append("intervals_plausible")
    if assessment.rating == "insufficient":
        reasons.append("little_motion")
    if has_neighbors:
        reasons.append("neighbor_excluded")
    reasons.append("inside_polygon")
    if stats.width and (stats.width, stats.height) != (saved_width, saved_height):
        reasons.append("capture_resolution_differs")
    reasons.append("shared_detector_settings")
    return tuple(reasons)


def _refused(
    tape: CalibrationTape,
    target_laps: int,
    direction: TravelDirection,
    check_direction: bool,
    block_size: int,
    saved_width: int,
    saved_height: int,
    reasons: tuple[str, ...],
    started: int,
    compared: int,
) -> CalibrationReport:
    stats = tape.stats()
    return CalibrationReport(
        target_laps=target_laps,
        detected=0,
        missed=0,
        ghosts=0,
        sensitivity=50,
        block_size=block_size,
        direction=direction,
        check_direction=check_direction,
        roi=None,
        camera_fps=stats.fps,
        capture_width=stats.width,
        capture_height=stats.height,
        saved_width=saved_width,
        saved_height=saved_height,
        dropped_frames=stats.dropped,
        recorded_frames=stats.frames,
        bytes_used=stats.bytes_used,
        analysis_ns=time.perf_counter_ns() - started,
        rating="insufficient",
        applicable=False,
        reasons=(*reasons, "repeat_calibration", "processing_is_block_size"),
        compared=compared,
    )


def _median(values: list[int]) -> int:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2
