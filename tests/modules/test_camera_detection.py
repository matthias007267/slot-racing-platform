"""Synthetic-frame tests for camera lane detection.

The picture used throughout is 100 by 40 pixels. Each lane is a horizontal band
10 pixels tall. The detection line is the vertical strip x=40..43, so a zone is
that strip clipped to one lane. A car is a bright 8 by 6 block. Placed at x=38
it covers the whole strip; placed to the left or right of the strip it does not.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import cast

import pytest

from slot_racing.modules.timing_camera.detection import (
    DetectionZone,
    DetectorSettings,
    LaneCrossing,
    LaneCrossingDetector,
    ZoneState,
)
from slot_racing.modules.timing_camera.frames import GrayFrame
from slot_racing.modules.timing_camera.geometry import DetectionRoi

WIDTH = 100
HEIGHT = 40
LINE_X = 40
LINE_WIDTH = 4
LANE_HEIGHT = 10
CAR_WIDTH = 8
CAR_HEIGHT = 6
CAR = 255
POSITION = "start_finish"


def lane_roi(lane: int, x: int = LINE_X, width: int = LINE_WIDTH) -> DetectionRoi:
    return DetectionRoi(x, (lane - 1) * LANE_HEIGHT, width, LANE_HEIGHT)


def zones(*lanes: int, position_id: str = POSITION, x: int = LINE_X) -> tuple[DetectionZone, ...]:
    return tuple(DetectionZone(position_id, lane, lane_roi(lane, x)) for lane in lanes)


def settings(
    *lanes: int,
    position_id: str = POSITION,
    x: int = LINE_X,
    threshold: int = 32,
    min_foreground_pixels: int = 1,
) -> DetectorSettings:
    return DetectorSettings(
        zones(*lanes, position_id=position_id, x=x),
        threshold=threshold,
        min_foreground_pixels=min_foreground_pixels,
    )


def blank() -> GrayFrame:
    return GrayFrame.blank(WIDTH, HEIGHT)


def car_at(lane: int, x: int, value: int = CAR) -> GrayFrame:
    """A bright block on one lane. ``x`` is its left edge."""
    y = (lane - 1) * LANE_HEIGHT + 2
    return blank().paint(DetectionRoi(x, y, CAR_WIDTH, CAR_HEIGHT), value)


def detector(
    *lanes: int,
    background: GrayFrame | None = None,
    threshold: int = 32,
    min_foreground_pixels: int = 1,
) -> LaneCrossingDetector:
    image = blank() if background is None else background
    configured = settings(*lanes, threshold=threshold, min_foreground_pixels=min_foreground_pixels)
    return LaneCrossingDetector(configured, background=image)


def test_entering_the_zone_emits_one_crossing() -> None:
    found = detector(1)
    assert found.observe(car_at(1, x=10), 1_000_000_000) == ()
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR

    crossings = found.observe(car_at(1, x=38), 1_000_000_000)
    assert crossings == (
        LaneCrossing(
            position_id=POSITION,
            lane=1,
            timestamp_ns=1_000_000_000,
            foreground_pixels=LINE_WIDTH * CAR_HEIGHT,
        ),
    )
    assert found.zone_state(POSITION, 1) is ZoneState.OCCUPIED


def test_staying_in_the_zone_does_not_emit_again() -> None:
    found = detector(1)
    inside = car_at(1, x=38)
    assert len(found.observe(inside, 100)) == 1
    assert found.observe(inside, 200) == ()
    assert found.observe(car_at(1, x=39), 300) == ()
    assert found.zone_state(POSITION, 1) is ZoneState.OCCUPIED


def test_leaving_the_zone_returns_to_clear() -> None:
    found = detector(1)
    assert len(found.observe(car_at(1, x=38), 100)) == 1
    assert found.observe(car_at(1, x=50), 200) == ()
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR


def test_synchronize_marks_a_zone_occupied_without_a_crossing() -> None:
    found = LaneCrossingDetector(settings(1), background=blank())
    found.synchronize(car_at(1, x=38))
    assert found.zone_state(POSITION, 1) is ZoneState.OCCUPIED
    assert found.observe(car_at(1, x=38), 2) == ()
    assert found.observe(blank(), 3) == ()
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR
    assert len(found.observe(car_at(1, x=38), 4)) == 1


def test_entering_again_emits_a_second_crossing() -> None:
    found = detector(1)
    first = found.observe(car_at(1, x=38), 100)
    assert found.observe(blank(), 200) == ()
    second = found.observe(car_at(1, x=38), 300)
    assert [crossing.timestamp_ns for crossing in (*first, *second)] == [100, 300]
    assert all(crossing.lane == 1 for crossing in (*first, *second))
    assert found.zone_state(POSITION, 1) is ZoneState.OCCUPIED


def test_only_the_occupied_lane_is_reported() -> None:
    found = detector(1, 2)
    crossings = found.observe(car_at(2, x=38), 500)
    assert [(crossing.lane, crossing.position_id) for crossing in crossings] == [(2, POSITION)]
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR
    assert found.zone_state(POSITION, 2) is ZoneState.OCCUPIED


def test_two_lanes_follow_enter_stay_and_leave() -> None:
    found = detector(1, 2)
    empty = blank()
    lane1_approaching = car_at(1, x=10)
    both = car_at(1, x=38).paint(DetectionRoi(38, 12, CAR_WIDTH, CAR_HEIGHT), CAR)
    lane2_only = car_at(2, x=38)

    assert found.observe(empty, 1) == ()
    assert found.observe(lane1_approaching, 2) == ()
    crossings = found.observe(both, 3)
    assert [(crossing.lane, crossing.timestamp_ns) for crossing in crossings] == [(1, 3), (2, 3)]
    assert found.observe(lane2_only, 4) == ()
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR
    assert found.zone_state(POSITION, 2) is ZoneState.OCCUPIED


def test_simultaneous_crossings_share_the_timestamp_and_are_sorted_by_lane() -> None:
    configured = DetectorSettings((zones(4)[0], zones(1)[0], zones(3)[0], zones(2)[0]))
    found = LaneCrossingDetector(configured, background=blank())
    frame = blank()
    for lane in (1, 3, 4):
        y = (lane - 1) * LANE_HEIGHT + 2
        frame = frame.paint(DetectionRoi(38, y, CAR_WIDTH, CAR_HEIGHT), CAR)

    crossings = found.observe(frame, 123_456_789)
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

    start_only = found.observe(car_at(1, x=38), 10)
    assert [(crossing.position_id, crossing.lane) for crossing in start_only] == [(POSITION, 1)]
    assert found.zone_state("sector_1", 1) is ZoneState.CLEAR
    assert found.zone_state(POSITION, 2) is ZoneState.CLEAR

    both_positions = car_at(1, x=38).paint(DetectionRoi(68, 2, CAR_WIDTH, CAR_HEIGHT), CAR)
    assert found.observe(blank(), 20) == ()
    crossings = found.observe(both_positions, 30)
    reported = [
        (crossing.position_id, crossing.lane, crossing.timestamp_ns) for crossing in crossings
    ]
    assert reported == [("sector_1", 1, 30), (POSITION, 1, 30)]


def test_a_crossing_reports_the_position_that_owns_the_zone() -> None:
    found = LaneCrossingDetector(
        DetectorSettings((DetectionZone("sector_1", 3, lane_roi(3, x=70)),)),
        background=blank(),
    )
    crossings = found.observe(car_at(3, x=68), 77)
    assert len(crossings) == 1
    assert crossings[0].position_id == "sector_1"
    assert crossings[0].lane == 3
    assert crossings[0].timestamp_ns == 77
    assert found.zone_state("sector_1", 3) is ZoneState.OCCUPIED


def test_empty_frames_emit_nothing() -> None:
    found = detector(1, 2, 3, 4)
    empty = blank()
    for timestamp in range(5):
        assert found.observe(empty, timestamp) == ()
    for lane in (1, 2, 3, 4):
        assert found.zone_state(POSITION, lane) is ZoneState.CLEAR


def test_motion_outside_the_zone_is_ignored() -> None:
    found = detector(1, 2)
    assert found.observe(car_at(1, x=0), 1) == ()
    assert found.observe(car_at(1, x=80), 2) == ()
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR
    assert found.zone_state(POSITION, 2) is ZoneState.CLEAR


def test_motion_on_another_lane_does_not_cross_this_lane() -> None:
    found = detector(1, 2, 3)
    crossings = found.observe(car_at(3, x=38), 9)
    assert [crossing.lane for crossing in crossings] == [3]
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR
    assert found.zone_state(POSITION, 2) is ZoneState.CLEAR


def test_static_object_in_the_zone_is_not_a_crossing() -> None:
    static = blank().paint(lane_roi(1), CAR)
    found = LaneCrossingDetector(settings(1, 2))
    assert found.observe(static, 1) == ()
    assert found.observe(static, 2) == ()
    assert found.observe(static, 3) == ()
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR
    assert found.zone_state(POSITION, 2) is ZoneState.CLEAR

    parked = LaneCrossingDetector(settings(1), background=static)
    assert parked.observe(static, 4) == ()
    assert parked.zone_state(POSITION, 1) is ZoneState.CLEAR


def test_pixels_below_the_threshold_are_ignored() -> None:
    found = detector(1, threshold=32)
    assert found.observe(car_at(1, x=38, value=31), 1) == ()
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR
    assert len(found.observe(car_at(1, x=38, value=32), 2)) == 1


def test_too_few_foreground_pixels_do_not_cross() -> None:
    found = detector(1, min_foreground_pixels=10)
    speck = blank().paint(DetectionRoi(LINE_X, 0, 2, 2), CAR)
    assert found.observe(speck, 1) == ()
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR
    assert len(found.observe(car_at(1, x=38), 2)) == 1


def test_a_single_frame_in_the_zone_is_one_crossing() -> None:
    found = detector(1)
    assert len(found.observe(car_at(1, x=38), 10)) == 1
    assert found.observe(blank(), 11) == ()
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR


def test_a_long_stay_emits_only_once() -> None:
    found = detector(1)
    inside = car_at(1, x=38)
    seen: list[LaneCrossing] = []
    for timestamp in range(50):
        seen.extend(found.observe(inside, timestamp))
    assert [crossing.timestamp_ns for crossing in seen] == [0]
    assert found.zone_state(POSITION, 1) is ZoneState.OCCUPIED


def test_reversing_out_and_back_emits_a_second_crossing() -> None:
    found = detector(1)
    before_the_line = car_at(1, x=60)
    on_the_line = car_at(1, x=38)

    assert found.observe(before_the_line, 1) == ()
    first = found.observe(on_the_line, 2)
    assert found.observe(before_the_line, 3) == ()
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR
    second = found.observe(on_the_line, 4)
    assert [crossing.timestamp_ns for crossing in (*first, *second)] == [2, 4]


def test_successive_cars_each_emit_one_crossing() -> None:
    found = detector(1, 2)
    seen: list[LaneCrossing] = []
    for timestamp, lane in ((100, 1), (200, 1), (300, 2), (400, 2)):
        seen.extend(found.observe(car_at(lane, x=38), timestamp))
        seen.extend(found.observe(blank(), timestamp + 50))
    assert [(crossing.lane, crossing.timestamp_ns) for crossing in seen] == [
        (1, 100),
        (1, 200),
        (2, 300),
        (2, 400),
    ]
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR
    assert found.zone_state(POSITION, 2) is ZoneState.CLEAR


def test_crossing_contains_only_technical_fields() -> None:
    names = {field.name for field in LaneCrossing.__dataclass_fields__.values()}
    assert names == {"position_id", "lane", "timestamp_ns", "foreground_pixels"}


def test_first_frame_without_background_calibrates_and_emits_nothing() -> None:
    found = LaneCrossingDetector(settings(1))
    assert found.observe(blank(), 1) == ()
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR
    crossings = found.observe(car_at(1, x=38), 2)
    assert [crossing.timestamp_ns for crossing in crossings] == [2]
    assert found.zone_state(POSITION, 1) is ZoneState.OCCUPIED


def test_a_rejected_frame_does_not_change_state() -> None:
    found = detector(1)
    with pytest.raises(ValueError, match="frame size"):
        found.observe(GrayFrame.blank(90, HEIGHT), 1)
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR
    assert len(found.observe(car_at(1, x=38), 2)) == 1

    uncalibrated = LaneCrossingDetector(settings(1))
    with pytest.raises(ValueError, match="timestamp_ns"):
        uncalibrated.observe(car_at(1, x=38), -1)
    assert uncalibrated.observe(car_at(1, x=38), 5) == ()


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
    with pytest.raises(ValueError, match="threshold"):
        DetectorSettings(zones(1), threshold=0)
    with pytest.raises(ValueError, match="min_foreground_pixels"):
        DetectorSettings(zones(1), min_foreground_pixels=lane_roi(1).area + 1)
    with pytest.raises(ValueError, match="pixel count"):
        GrayFrame(1, 1, ())
    with pytest.raises(ValueError, match="outside"):
        blank().paint(DetectionRoi(WIDTH - 1, 0, 2, 1), CAR)
    outside = DetectionZone(POSITION, 1, DetectionRoi(WIDTH - 1, 0, 2, 1))
    with pytest.raises(ValueError, match="outside"):
        LaneCrossingDetector(DetectorSettings((outside,)), background=blank())


def test_detection_modules_do_not_know_races_events_or_ui() -> None:
    root = Path("src/slot_racing/modules/timing_camera")
    forbidden = (
        "PySide6",
        "cv2",
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
    detection_only = {"_checks.py", "detection.py", "frames.py", "geometry.py", "frame_source.py"}
    for path in sorted(root.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert "VideoCapture" not in source
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
