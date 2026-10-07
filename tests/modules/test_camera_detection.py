"""Synthetic-frame tests for directional block detection.

The lane picture is 100 by 40 pixels. Each lane is a band 10 pixels tall. The
detection line is x=40..43. At the default block size that strip is a 5 by 2
grid of 2-pixel tiles, so a car is a full column moving from the left tile to
the right tile.

A separate 80 by 80 picture with 20 px tiles is used where the test needs a
larger block grid: connectivity, travel direction, sensitivity and ghost
patterns. One tile step is 20 pixels, the reference size of the sensitivity.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import cast

import numpy as np
import pytest

from slot_racing.modules.timing_camera.detection import (
    DetectionTrace,
    DetectionZone,
    DetectorSettings,
    LaneCrossing,
    LaneCrossingDetector,
    TravelDirection,
    ZoneState,
    block_means,
    effective_block_size,
    sensitivity_profile,
)
from slot_racing.modules.timing_camera.frames import GrayFrame
from slot_racing.modules.timing_camera.geometry import DetectionRoi

WIDTH = 100
HEIGHT = 40
LINE_X = 40
LINE_WIDTH = 4
LANE_HEIGHT = 10
CAR = 255
POSITION = "start_finish"
SQUARE = 80


def lane_roi(lane: int, x: int = LINE_X, width: int = LINE_WIDTH) -> DetectionRoi:
    return DetectionRoi(x, (lane - 1) * LANE_HEIGHT, width, LANE_HEIGHT)


def zones(*lanes: int, position_id: str = POSITION, x: int = LINE_X) -> tuple[DetectionZone, ...]:
    return tuple(DetectionZone(position_id, lane, lane_roi(lane, x)) for lane in lanes)


def settings(*lanes: int, position_id: str = POSITION, x: int = LINE_X) -> DetectorSettings:
    return DetectorSettings(zones(*lanes, position_id=position_id, x=x))


def blank() -> GrayFrame:
    return GrayFrame.blank(WIDTH, HEIGHT)


def column(lane: int, x: int, value: int = CAR) -> GrayFrame:
    """One full tile column on a lane. ``x`` is the left edge of the strip."""
    y = (lane - 1) * LANE_HEIGHT
    return blank().paint(DetectionRoi(x, y, 2, LANE_HEIGHT), value)


def columns(lanes: tuple[int, ...], x: int, value: int = CAR) -> GrayFrame:
    frame = blank()
    for lane in lanes:
        y = (lane - 1) * LANE_HEIGHT
        frame = frame.paint(DetectionRoi(x, y, 2, LANE_HEIGHT), value)
    return frame


def detector(*lanes: int, background: GrayFrame | None = None) -> LaneCrossingDetector:
    image = blank() if background is None else background
    return LaneCrossingDetector(settings(*lanes), background=image)


def sweep(
    found: LaneCrossingDetector,
    lane: int,
    timestamp_ns: int,
    *,
    earlier_ns: int | None = None,
    value: int = CAR,
    x: int = LINE_X,
) -> tuple[LaneCrossing, ...]:
    """Move one lane from the left tile column to the right one."""
    started = timestamp_ns - 1 if earlier_ns is None else earlier_ns
    assert found.observe(column(lane, x, value), started) == ()
    return found.observe(column(lane, x + 2, value), timestamp_ns)


def square_frame() -> GrayFrame:
    return GrayFrame.blank(SQUARE, SQUARE)


def blob(x: int, y: int, width: int = 40, height: int = 40, value: int = CAR) -> GrayFrame:
    return square_frame().paint(DetectionRoi(x, y, width, height), value)


def square_detector(
    direction: TravelDirection = TravelDirection.LEFT_TO_RIGHT,
    sensitivity: int = 50,
    block_size: int = 20,
    *,
    debug: bool = False,
    background: GrayFrame | None = None,
) -> LaneCrossingDetector:
    configured = DetectorSettings(
        (DetectionZone(POSITION, 1, DetectionRoi(0, 0, SQUARE, SQUARE)),),
        block_size=block_size,
        sensitivity=sensitivity,
        direction=direction,
        debug=debug,
    )
    image = square_frame() if background is None else background
    return LaneCrossingDetector(configured, background=image)


def test_block_means_average_each_full_tile_and_drop_the_remainder() -> None:
    image = np.zeros((5, 5), dtype=np.uint8)
    image[:2, :2] = 10
    image[:2, 2:4] = 30
    image[2:4, :2] = 0
    image[2:4, 2:3] = 0
    image[2:4, 3:4] = 100
    image[4, :] = 255
    image[:, 4] = 255
    means = block_means(image, 2)
    assert means.shape == (2, 2)
    assert means[0, 0] == 10
    assert means[0, 1] == 30
    assert means[1, 0] == 0
    assert float(means[1, 1]) == 50


def test_block_means_has_no_python_pixel_loop_and_shrinks_with_the_tile() -> None:
    """The reduction is a reshape plus one mean. Output grows with area/block²."""
    tree = ast.parse(inspect.getsource(block_means))
    walked = list(ast.walk(tree))
    assert not any(isinstance(node, (ast.For, ast.While, ast.ListComp)) for node in walked)
    source = inspect.getsource(block_means)
    assert "reshape" in source
    assert ".mean(" in source

    wide = np.zeros((600, 800), dtype=np.uint8)
    wide[:20, :20] = 40
    wide[:20, 20:40] = 80
    reduced = block_means(wide, 20)
    assert reduced.shape == (30, 40)
    assert reduced.size == 1_200
    assert wide.size / reduced.size == 400
    assert reduced[0, 0] == 40
    assert reduced[0, 1] == 80
    assert reduced[1, 0] == 0

    smaller = block_means(np.zeros((100, 200), dtype=np.uint8), 20)
    larger = block_means(np.zeros((200, 400), dtype=np.uint8), 20)
    assert larger.size == smaller.size * 4


def test_a_large_zone_is_compared_as_blocks_not_pixels() -> None:
    zone = DetectionZone(POSITION, 1, DetectionRoi(0, 0, 800, 600))
    frame = GrayFrame(800, 600, bytes(800 * 600))
    found = LaneCrossingDetector(DetectorSettings((zone,)), background=frame)
    assert found.reference_pixels == 30 * 40
    assert found.reference_pixels < 800 * 600
    assert found.observe(frame, 1) == ()
    assert found.pixels_compared == 30 * 40
    assert found.trace() is None


def test_effective_block_size_keeps_two_steps_on_a_thin_zone() -> None:
    assert effective_block_size(20, 800, 600, TravelDirection.LEFT_TO_RIGHT) == 20
    assert effective_block_size(20, 4, 10, TravelDirection.LEFT_TO_RIGHT) == 2
    assert effective_block_size(20, 10, 10, TravelDirection.TOP_TO_BOTTOM) == 5
    assert effective_block_size(100, 40, 40, TravelDirection.LEFT_TO_RIGHT) == 20


def test_sensitivity_profile_relaxes_every_internal_threshold() -> None:
    strict = sensitivity_profile(0)
    normal = sensitivity_profile(50)
    loose = sensitivity_profile(100)
    assert strict.difference == 60
    assert normal.difference == 38
    assert loose.difference == 16
    assert (strict.min_blocks, normal.min_blocks, loose.min_blocks) == (4, 3, 2)
    assert strict.min_shift == 1.25
    assert normal.min_shift == 1
    assert loose.min_shift == 0.75
    assert strict.window_ns < normal.window_ns < loose.window_ns


def test_entering_the_zone_emits_one_crossing() -> None:
    found = detector(1)
    assert found.observe(column(1, x=10), 1_000_000_000) == ()
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR

    crossings = sweep(found, 1, 1_000_000_000)
    assert crossings == (
        LaneCrossing(
            position_id=POSITION,
            lane=1,
            timestamp_ns=1_000_000_000,
            foreground_pixels=5,
        ),
    )
    assert found.zone_state(POSITION, 1) is ZoneState.OCCUPIED


def test_a_brighter_and_a_darker_car_both_cross() -> None:
    brighter = detector(1)
    assert len(sweep(brighter, 1, 10, value=255)) == 1

    base = GrayFrame.blank(WIDTH, HEIGHT, 180)
    dark = LaneCrossingDetector(settings(1), background=base)
    y = 0
    left = base.paint(DetectionRoi(LINE_X, y, 2, LANE_HEIGHT), 30)
    right = base.paint(DetectionRoi(LINE_X + 2, y, 2, LANE_HEIGHT), 30)
    assert dark.observe(left, 19) == ()
    assert len(dark.observe(right, 20)) == 1
    assert dark.zone_state(POSITION, 1) is ZoneState.OCCUPIED


def test_a_change_below_the_difference_is_ignored() -> None:
    found = detector(1)
    assert sweep(found, 1, 2, value=37) == ()
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR
    assert len(sweep(found, 1, 4, value=38)) == 1


def test_staying_in_the_zone_does_not_emit_again() -> None:
    found = detector(1)
    assert len(sweep(found, 1, 100)) == 1
    assert found.observe(column(1, LINE_X + 2), 200) == ()
    assert found.observe(column(1, LINE_X + 2), 300) == ()
    assert found.zone_state(POSITION, 1) is ZoneState.OCCUPIED


def test_leaving_the_zone_returns_to_clear() -> None:
    found = detector(1)
    assert len(sweep(found, 1, 100)) == 1
    assert found.observe(blank(), 200) == ()
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR


def test_a_single_frame_does_not_cross() -> None:
    found = detector(1)
    assert found.observe(column(1, LINE_X), 10) == ()
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR
    assert found.trace() is None
    crossings = found.observe(column(1, LINE_X + 2), 11)
    assert [crossing.timestamp_ns for crossing in crossings] == [11]
    assert found.observe(blank(), 12) == ()
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR


def test_synchronize_marks_a_zone_occupied_without_a_crossing() -> None:
    found = LaneCrossingDetector(settings(1), background=blank())
    found.synchronize(column(1, LINE_X))
    assert found.zone_state(POSITION, 1) is ZoneState.OCCUPIED
    assert found.observe(column(1, LINE_X), 2) == ()
    assert found.observe(blank(), 3) == ()
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR
    assert len(sweep(found, 1, 4)) == 1


def test_entering_again_emits_a_second_crossing() -> None:
    found = detector(1)
    first = sweep(found, 1, 100)
    assert found.observe(blank(), 200) == ()
    second = sweep(found, 1, 300)
    assert [crossing.timestamp_ns for crossing in (*first, *second)] == [100, 300]
    assert found.zone_state(POSITION, 1) is ZoneState.OCCUPIED


def test_only_the_occupied_lane_is_reported() -> None:
    found = detector(1, 2)
    crossings = sweep(found, 2, 500)
    assert [(crossing.lane, crossing.position_id) for crossing in crossings] == [(2, POSITION)]
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR
    assert found.zone_state(POSITION, 2) is ZoneState.OCCUPIED


def test_two_lanes_follow_enter_stay_and_leave() -> None:
    found = detector(1, 2)
    assert found.observe(blank(), 1) == ()
    assert found.observe(column(1, x=10), 2) == ()
    assert found.observe(columns((1, 2), LINE_X), 3) == ()
    crossings = found.observe(columns((1, 2), LINE_X + 2), 4)
    assert [(crossing.lane, crossing.timestamp_ns) for crossing in crossings] == [(1, 4), (2, 4)]
    assert found.observe(column(2, LINE_X + 2), 5) == ()
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR
    assert found.zone_state(POSITION, 2) is ZoneState.OCCUPIED


def test_simultaneous_crossings_share_the_timestamp_and_are_sorted_by_lane() -> None:
    configured = DetectorSettings((zones(4)[0], zones(1)[0], zones(3)[0], zones(2)[0]))
    found = LaneCrossingDetector(configured, background=blank())
    present = (1, 3, 4)
    assert found.observe(columns(present, LINE_X), 1) == ()
    crossings = found.observe(columns(present, LINE_X + 2), 123_456_789)
    assert [(crossing.lane, crossing.timestamp_ns) for crossing in crossings] == [
        (1, 123_456_789),
        (3, 123_456_789),
        (4, 123_456_789),
    ]
    assert found.zone_state(POSITION, 2) is ZoneState.CLEAR


def test_same_lane_at_two_positions_is_independent() -> None:
    configured = DetectorSettings(
        (
            DetectionZone("sector_1", 1, lane_roi(1, x=70)),
            DetectionZone(POSITION, 1, lane_roi(1)),
            DetectionZone("sector_1", 2, lane_roi(2, x=70)),
            DetectionZone(POSITION, 2, lane_roi(2)),
        )
    )
    found = LaneCrossingDetector(configured, background=blank())
    start_only = sweep(found, 1, 10)
    assert [(crossing.position_id, crossing.lane) for crossing in start_only] == [(POSITION, 1)]
    assert found.zone_state("sector_1", 1) is ZoneState.CLEAR
    assert found.observe(blank(), 20) == ()

    def both(x_start: int, x_sector: int) -> GrayFrame:
        frame = column(1, x_start)
        return frame.paint(DetectionRoi(x_sector, 0, 2, LANE_HEIGHT), CAR)

    assert found.observe(both(LINE_X, 70), 30) == ()
    crossings = found.observe(both(LINE_X + 2, 72), 40)
    reported = [
        (crossing.position_id, crossing.lane, crossing.timestamp_ns) for crossing in crossings
    ]
    assert reported == [("sector_1", 1, 40), (POSITION, 1, 40)]


def test_a_crossing_reports_the_position_that_owns_the_zone() -> None:
    found = LaneCrossingDetector(
        DetectorSettings((DetectionZone("sector_1", 3, lane_roi(3, x=70)),)),
        background=blank(),
    )
    assert found.observe(column(3, 70), 70) == ()
    crossings = found.observe(column(3, 72), 77)
    assert len(crossings) == 1
    assert crossings[0].position_id == "sector_1"
    assert crossings[0].lane == 3
    assert crossings[0].timestamp_ns == 77
    assert found.zone_state("sector_1", 3) is ZoneState.OCCUPIED


def test_empty_frames_and_motion_outside_the_zone_emit_nothing() -> None:
    found = detector(1, 2, 3, 4)
    for timestamp in range(5):
        assert found.observe(blank(), timestamp) == ()
    assert found.observe(column(1, x=0), 6) == ()
    assert found.observe(column(1, x=80), 7) == ()
    for lane in (1, 2, 3, 4):
        assert found.zone_state(POSITION, lane) is ZoneState.CLEAR


def test_motion_on_another_lane_does_not_cross_this_lane() -> None:
    found = detector(1, 2, 3)
    crossings = sweep(found, 3, 9)
    assert [crossing.lane for crossing in crossings] == [3]
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR
    assert found.zone_state(POSITION, 2) is ZoneState.CLEAR


def test_a_static_object_in_the_zone_is_not_a_crossing() -> None:
    static = blank().paint(lane_roi(1), CAR)
    found = LaneCrossingDetector(settings(1, 2))
    assert found.observe(static, 1) == ()
    assert found.observe(static, 2) == ()
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR

    parked = LaneCrossingDetector(settings(1), background=static)
    assert parked.observe(static, 4) == ()
    assert parked.zone_state(POSITION, 1) is ZoneState.CLEAR


def test_isolated_blocks_and_a_single_pixel_do_not_cross() -> None:
    found = square_detector(debug=True)
    speck = square_frame().paint(DetectionRoi(0, 0, 1, 1), CAR)
    assert found.observe(speck, 1) == ()
    assert found.observe(speck.paint(DetectionRoi(10, 0, 1, 1), CAR), 2) == ()
    scattered = blob(0, 0, 20, 20).paint(DetectionRoi(60, 0, 20, 20), CAR)
    scattered = scattered.paint(DetectionRoi(0, 60, 20, 20), CAR)
    moved = blob(20, 0, 20, 20).paint(DetectionRoi(60, 20, 20, 20), CAR)
    moved = moved.paint(DetectionRoi(20, 60, 20, 20), CAR)
    assert found.observe(scattered, 3) == ()
    assert found.observe(moved, 4) == ()
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR
    decision = found.trace()
    assert decision is not None
    assert decision[0].reason == "below_size"
    assert decision[0].accepted is False


def test_connected_blocks_moving_the_right_way_cross_once() -> None:
    found = square_detector(debug=True)
    assert found.observe(blob(0, 0), 1) == ()
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR
    crossings = found.observe(blob(20, 0), 2)
    assert len(crossings) == 1
    assert crossings[0].foreground_pixels == 4
    assert found.zone_state(POSITION, 1) is ZoneState.OCCUPIED
    assert found.observe(blob(40, 0), 3) == ()
    decision = found.trace()
    assert decision is not None
    assert decision[0].reason == "occupied"
    assert decision[0].changed_blocks >= 4


def test_motion_in_each_travel_direction_is_accepted() -> None:
    cases = (
        (TravelDirection.LEFT_TO_RIGHT, blob(0, 0), blob(20, 0)),
        (TravelDirection.RIGHT_TO_LEFT, blob(40, 0), blob(20, 0)),
        (TravelDirection.TOP_TO_BOTTOM, blob(0, 0), blob(0, 20)),
        (TravelDirection.BOTTOM_TO_TOP, blob(0, 40), blob(0, 20)),
    )
    for direction, first, second in cases:
        found = square_detector(direction)
        assert found.observe(first, 1) == ()
        assert len(found.observe(second, 2)) == 1
        assert found.zone_state(POSITION, 1) is ZoneState.OCCUPIED


def test_motion_against_the_travel_direction_is_rejected() -> None:
    found = square_detector(TravelDirection.RIGHT_TO_LEFT, debug=True)
    assert found.observe(blob(0, 0), 1) == ()
    assert found.observe(blob(20, 0), 2) == ()
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR
    decision = found.trace()
    assert decision is not None
    assert decision[0].reason == "wrong_direction"
    shift = decision[0].shift
    assert shift is not None
    assert shift < 0


def test_a_change_that_does_not_move_is_not_a_car() -> None:
    found = square_detector(debug=True)
    parked = blob(0, 0)
    assert found.observe(parked, 1) == ()
    assert found.observe(parked, 2) == ()
    decision = found.trace()
    assert decision is not None
    assert decision[0].reason == "insufficient_motion"
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR


def test_fast_slow_and_too_slow_motion() -> None:
    fast = square_detector()
    assert fast.observe(blob(0, 0), 5) == ()
    assert len(fast.observe(blob(20, 0), 6)) == 1

    slow = square_detector()
    assert slow.observe(blob(0, 0), 0) == ()
    assert len(slow.observe(blob(20, 0), 1_000_000_000)) == 1

    late = square_detector(debug=True)
    assert late.observe(blob(0, 0), 0) == ()
    assert late.observe(blob(20, 0), 2_000_000_000) == ()
    decision = late.trace()
    assert decision is not None
    assert decision[0].reason == "too_short"
    assert late.zone_state(POSITION, 1) is ZoneState.CLEAR


def test_a_one_frame_gap_does_not_drop_the_pass() -> None:
    found = square_detector()
    assert found.observe(blob(0, 0), 0) == ()
    assert found.observe(square_frame(), 100) == ()
    assert len(found.observe(blob(20, 0), 200)) == 1


def test_a_creep_across_three_frames_still_crosses() -> None:
    found = square_detector()
    assert found.observe(blob(0, 0), 1) == ()
    assert found.observe(blob(0, 0, width=60), 2) == ()
    assert len(found.observe(blob(20, 0), 3)) == 1


def test_low_sensitivity_demands_a_larger_shift_and_high_sensitivity_a_faint_car() -> None:
    strict = square_detector(sensitivity=0)
    assert strict.observe(blob(0, 0), 0) == ()
    assert strict.observe(blob(20, 0), 100_000_000) == ()
    assert len(strict.observe(blob(40, 0), 200_000_000)) == 1

    faint = square_detector(sensitivity=50)
    assert faint.observe(blob(0, 0, value=20), 1) == ()
    assert faint.observe(blob(20, 0, value=20), 2) == ()

    loose = square_detector(sensitivity=100)
    assert loose.observe(blob(0, 0, value=20), 1) == ()
    assert len(loose.observe(blob(20, 0, value=20), 2)) == 1


def test_small_and_large_groups_follow_the_sensitivity() -> None:
    small = blob(0, 0, 20, 40)
    small_next = blob(20, 0, 20, 40)
    normal = square_detector(sensitivity=50)
    assert normal.observe(small, 1) == ()
    assert normal.observe(small_next, 2) == ()

    loose = square_detector(sensitivity=80)
    assert loose.observe(small, 1) == ()
    crossings = loose.observe(small_next, 2)
    assert len(crossings) == 1
    assert crossings[0].foreground_pixels == 2

    large = square_detector(block_size=5)
    assert large.observe(blob(0, 0), 1) == ()
    assert len(large.observe(blob(20, 0), 2)) == 1

    coarse = square_detector(block_size=100)
    assert coarse.observe(blob(0, 0, 40, 80), 1) == ()
    assert len(coarse.observe(blob(40, 0, 40, 80), 2)) == 1


def test_a_long_stay_emits_only_once() -> None:
    found = detector(1)
    assert len(sweep(found, 1, 1, earlier_ns=0)) == 1
    seen: list[LaneCrossing] = []
    inside = column(1, LINE_X + 2)
    for timestamp in range(2, 50):
        seen.extend(found.observe(inside, timestamp))
    assert seen == []
    assert found.zone_state(POSITION, 1) is ZoneState.OCCUPIED


def test_successive_cars_each_emit_one_crossing() -> None:
    found = detector(1, 2)
    seen: list[LaneCrossing] = []
    for timestamp, lane in ((100, 1), (200, 1), (300, 2), (400, 2)):
        seen.extend(sweep(found, lane, timestamp))
        seen.extend(found.observe(blank(), timestamp + 40))
    assert [(crossing.lane, crossing.timestamp_ns) for crossing in seen] == [
        (1, 100),
        (1, 200),
        (2, 300),
        (2, 400),
    ]
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR
    assert found.zone_state(POSITION, 2) is ZoneState.CLEAR


def test_a_bytes_frame_matches_the_tuple_frame() -> None:
    packed = LaneCrossingDetector(settings(1), background=blank())
    left = GrayFrame(WIDTH, HEIGHT, column(1, LINE_X).to_bytes())
    right = GrayFrame(WIDTH, HEIGHT, column(1, LINE_X + 2).to_bytes())
    assert packed.observe(left, 1) == ()
    crossings = packed.observe(right, 2)
    assert len(crossings) == 1
    assert crossings[0].foreground_pixels == 5


def test_crossing_contains_only_technical_fields() -> None:
    names = {field.name for field in LaneCrossing.__dataclass_fields__.values()}
    assert names == {"position_id", "lane", "timestamp_ns", "foreground_pixels"}
    assert {item.value for item in TravelDirection} == {
        "top_to_bottom",
        "bottom_to_top",
        "left_to_right",
        "right_to_left",
    }


def test_first_frame_without_background_calibrates_and_emits_nothing() -> None:
    found = LaneCrossingDetector(settings(1))
    assert found.observe(blank(), 1) == ()
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR
    crossings = sweep(found, 1, 3, earlier_ns=2)
    assert [crossing.timestamp_ns for crossing in crossings] == [3]
    assert found.zone_state(POSITION, 1) is ZoneState.OCCUPIED


def test_a_rejected_frame_does_not_change_state() -> None:
    found = detector(1)
    with pytest.raises(ValueError, match="frame size"):
        found.observe(GrayFrame.blank(90, HEIGHT), 1)
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR
    assert len(sweep(found, 1, 2)) == 1

    uncalibrated = LaneCrossingDetector(settings(1))
    with pytest.raises(ValueError, match="timestamp_ns"):
        uncalibrated.observe(column(1, LINE_X), -1)
    assert uncalibrated.observe(column(1, LINE_X), 5) == ()


def test_unknown_zone_and_bad_arguments_are_rejected() -> None:
    found = detector(1)
    with pytest.raises(ValueError, match="no detection zone"):
        found.zone_state("missing", 1)
    with pytest.raises(TypeError, match="timestamp_ns"):
        found.observe(blank(), cast(int, True))
    with pytest.raises(TypeError, match="frame"):
        found.observe(cast(GrayFrame, "nope"), 1)


def test_geometry_and_settings_reject_invalid_values() -> None:
    with pytest.raises(ValueError, match="width"):
        DetectionRoi(0, 0, 0, 1)
    with pytest.raises(TypeError, match="x"):
        DetectionRoi(cast(int, True), 0, 1, 1)
    with pytest.raises(ValueError, match="position_id"):
        DetectionZone("  ", 1, lane_roi(1))
    with pytest.raises(ValueError, match="lane"):
        DetectionZone(POSITION, 0, lane_roi(1))
    with pytest.raises(ValueError, match="at least one"):
        DetectorSettings(())
    with pytest.raises(ValueError, match="duplicate"):
        DetectorSettings((zones(1)[0], zones(1)[0]))
    with pytest.raises(ValueError, match="block_size"):
        DetectorSettings(zones(1), block_size=0)
    with pytest.raises(ValueError, match="sensitivity"):
        DetectorSettings(zones(1), sensitivity=101)
    with pytest.raises(TypeError, match="direction"):
        DetectorSettings(zones(1), direction=cast(TravelDirection, "left_to_right"))
    with pytest.raises(TypeError, match="debug"):
        DetectorSettings(zones(1), debug=cast(bool, 1))
    with pytest.raises(ValueError, match="pixel count"):
        GrayFrame(1, 1, ())
    with pytest.raises(ValueError, match="outside"):
        blank().paint(DetectionRoi(WIDTH - 1, 0, 2, 1), CAR)
    outside = DetectionZone(POSITION, 1, DetectionRoi(WIDTH - 1, 0, 2, 1))
    with pytest.raises(ValueError, match="outside"):
        LaneCrossingDetector(DetectorSettings((outside,)), background=blank())


def test_debug_reports_the_decision_and_stays_empty_when_disabled() -> None:
    quiet = detector(1)
    assert quiet.trace() is None
    assert len(sweep(quiet, 1, 2)) == 1
    assert quiet.trace() is None

    traced = square_detector(debug=True)
    assert traced.observe(blob(0, 0), 1) == ()
    first = traced.trace()
    assert first is not None
    assert isinstance(first[0], DetectionTrace)
    assert first[0].reason == "too_short"
    assert first[0].changed_blocks == 4
    assert first[0].strength > 0
    assert traced.observe(blob(20, 0), 2)
    accepted = traced.trace()
    assert accepted is not None
    assert accepted[0].accepted is True
    assert accepted[0].reason == "accepted"
    assert accepted[0].state is ZoneState.OCCUPIED
    assert accepted[0].shift is not None
    assert accepted[0].shift >= 1


def test_detection_compares_only_the_zone_blocks() -> None:
    found = LaneCrossingDetector(settings(1, 2), background=blank())
    assert found.reference_pixels == 20
    assert found.reference_pixels < WIDTH * HEIGHT
    assert found.pixels_compared == 0
    assert found.observe(column(1, LINE_X), 1) == ()
    assert found.pixels_compared == 20
    with pytest.raises(ValueError, match="outside"):
        blank().crop(DetectionRoi(WIDTH - 1, 0, 2, 1))


def test_precut_zones_detect_without_the_full_frame() -> None:
    configured = settings(1)
    found = LaneCrossingDetector(configured, background=blank())
    roi = configured.zones[0].roi
    carrier = GrayFrame(1, 1, b"\x00")
    left = GrayFrame.blank(roi.width, roi.height).paint(DetectionRoi(0, 0, 2, roi.height), CAR)
    right = GrayFrame.blank(roi.width, roi.height).paint(DetectionRoi(2, 0, 2, roi.height), CAR)
    assert found.observe_crops((left,), 1) == ()
    crossings = found.observe_crops((right,), 7)
    assert len(crossings) == 1
    assert crossings[0].foreground_pixels == 5
    assert found.pixels_compared == 20
    assert found.pixels_compared < roi.area
    assert len(carrier.pixels) == 1


def test_a_thin_zone_still_sees_a_block_move_and_ignores_one_frame() -> None:
    roi = DetectionRoi(0, 0, 4, 2)
    found = LaneCrossingDetector(
        DetectorSettings((DetectionZone(POSITION, 1, roi),)),
        background=GrayFrame.blank(4, 2),
    )
    left = GrayFrame.blank(4, 2).paint(DetectionRoi(0, 0, 2, 2), CAR)
    right = GrayFrame.blank(4, 2).paint(DetectionRoi(2, 0, 2, 2), CAR)
    assert found.observe(left, 1) == ()
    crossings = found.observe(right, 2)
    assert len(crossings) == 1
    assert crossings[0].foreground_pixels == 1


def test_detection_modules_do_not_know_races_events_or_ui() -> None:
    root = Path("src/slot_racing/modules/timing_camera")
    forbidden = (
        "PySide6",
        "slot_racing.app",
        "slot_racing.uikit",
        "slot_racing.core.domain",
        "slot_racing.modules.races",
        "slot_racing.modules.timing",
        "slot_racing.modules.drivers_vehicles",
        "slot_racing.modules.tracks",
    )
    detection_only = {
        "_checks.py",
        "camera_config.py",
        "capture.py",
        "detection.py",
        "frames.py",
        "geometry.py",
        "frame_source.py",
        "opencv_device.py",
        "configuration.py",
        "store.py",
        "lease.py",
        "preview.py",
    }
    for path in sorted(root.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if path.name != "opencv_device.py":
            assert "VideoCapture" not in source
            assert "import cv2" not in source
        tree = ast.parse(source)
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.append(node.module)
        blocked: tuple[str, ...] = forbidden
        if path.name in detection_only:
            blocked = (*forbidden, "slot_racing.core.events")
        for name in imported:
            for prefix in blocked:
                assert name != prefix and not name.startswith(prefix + "."), (path.name, name)
    ui_forbidden = (
        "cv2",
        "sqlalchemy",
        "slot_racing.app",
        "slot_racing.modules.races",
        "slot_racing.modules.timing_camera.opencv_device",
    )
    for path in sorted((root / "ui").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert "import cv2" not in source
        assert "VideoCapture" not in source
        assert ".session(" not in source
        assert "RaceEngine" not in source
        assert "RaceController" not in source
        assert "SensorTriggered" not in source
        tree = ast.parse(source)
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.append(node.module)
        for name in imported:
            for prefix in ui_forbidden:
                assert name != prefix and not name.startswith(prefix + "."), (path.name, name)
