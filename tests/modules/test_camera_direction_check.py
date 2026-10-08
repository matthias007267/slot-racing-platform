"""Direction checking can be turned off per zone without relaxing size or difference."""

from __future__ import annotations

from slot_racing.modules.timing_camera.configuration import scale_detector_settings
from slot_racing.modules.timing_camera.detection import (
    DetectionZone,
    DetectorSettings,
    LaneCrossingDetector,
    TravelDirection,
    ZoneState,
)
from slot_racing.modules.timing_camera.geometry import DetectionRoi
from tests.modules.test_camera_detection import POSITION, SQUARE, blob, square_frame

_NS = 1_000_000_000


def _detector(check: bool) -> LaneCrossingDetector:
    configured = DetectorSettings(
        (
            DetectionZone(
                POSITION,
                1,
                DetectionRoi(0, 0, SQUARE, SQUARE),
                check_direction=check,
            ),
        ),
        block_size=20,
        direction=TravelDirection.LEFT_TO_RIGHT,
    )
    return LaneCrossingDetector(configured, background=square_frame())


def test_direction_check_keeps_one_sample_and_the_opposite_direction() -> None:
    found = _detector(True)
    found.set_inspection(True)
    assert found.observe(blob(0, 0), 20_000_000) == ()
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR
    inspection = found.last_inspection()
    assert inspection is not None
    assert inspection.zones[0].reason == "too_short"
    assert inspection.zones[0].direction_check is True

    opposite = _detector(True)
    assert opposite.observe(blob(20, 0), 20_000_000) == ()
    assert opposite.observe(blob(0, 0), 40_000_000) == ()
    assert opposite.zone_state(POSITION, 1) is ZoneState.CLEAR


def test_without_direction_check_one_large_sample_counts_once() -> None:
    found = _detector(False)
    found.set_inspection(True)
    crossings = found.observe(blob(0, 0), 20_000_000)
    assert len(crossings) == 1
    assert crossings[0].lane == 1
    inspection = found.last_inspection()
    assert inspection is not None
    assert inspection.zones[0].reason == "accepted_without_direction_check"
    assert inspection.zones[0].direction_check is False
    assert found.zone_state(POSITION, 1) is ZoneState.OCCUPIED
    assert found.observe(blob(20, 0), 40_000_000) == ()
    assert found.observe(blob(0, 0), 60_000_000) == ()
    assert found.observe(square_frame(), 80_000_000) == ()
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR
    again = found.observe(blob(40, 0), 100_000_000)
    assert len(again) == 1
    assert again[0].timestamp_ns == 100_000_000


def test_without_direction_check_a_small_change_and_the_window_do_not_invent_laps() -> None:
    small = _detector(False)
    assert small.observe(blob(0, 0, 10, 10), 20_000_000) == ()
    assert small.zone_state(POSITION, 1) is ZoneState.CLEAR

    held = _detector(False)
    assert len(held.observe(blob(0, 0), 0)) == 1
    assert held.observe(blob(0, 0), 10 * _NS) == ()
    assert held.zone_state(POSITION, 1) is ZoneState.OCCUPIED
    assert held.observe(square_frame(), 10 * _NS + 20_000_000) == ()
    assert len(held.observe(blob(0, 0), 10 * _NS + 40_000_000)) == 1


def test_the_two_modes_do_not_share_detector_state_and_scaling_keeps_the_flag() -> None:
    checked = _detector(True)
    open_zone = _detector(False)
    assert checked.observe(blob(0, 0), 1) == ()
    assert len(open_zone.observe(blob(0, 0), 1)) == 1
    assert checked.zone_state(POSITION, 1) is ZoneState.CLEAR
    assert open_zone.zone_state(POSITION, 1) is ZoneState.OCCUPIED

    configured = DetectorSettings(
        (
            DetectionZone(
                POSITION,
                1,
                DetectionRoi(0, 0, 40, 40),
                check_direction=False,
            ),
        ),
        block_size=20,
        sensitivity=80,
        direction=TravelDirection.RIGHT_TO_LEFT,
    )
    scaled = scale_detector_settings(configured, 80, 80, 160, 160)
    assert scaled.zones[0].check_direction is False
    assert scaled.sensitivity == 80
    assert scaled.direction is TravelDirection.RIGHT_TO_LEFT
