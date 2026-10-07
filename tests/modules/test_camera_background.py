"""Adaptive background reference and release after a car has left.

The picture is 400 by 200 pixels with 20-pixel blocks, so the zone is a 20 by 10
grid. A car covers 48 of those blocks. A static leftover covers 12, which is
still more than ``required_blocks`` and would have kept the old detector
occupied.
"""

from __future__ import annotations

import time

from slot_racing.modules.timing_camera.detection import (
    DetectionZone,
    DetectorSettings,
    LaneCrossingDetector,
    TravelDirection,
    ZoneState,
    create_lane_detector,
)
from slot_racing.modules.timing_camera.diagnostic import DiagnosticView, render_picture
from slot_racing.modules.timing_camera.frames import GrayFrame
from slot_racing.modules.timing_camera.geometry import DetectionRoi
from tests.modules.test_camera_detection import blob, square_detector

WIDTH = 400
HEIGHT = 200
BLOCK = 20
_PX = 5
POSITION = "start_finish"
CAR = 220
REST = 50
FRAME_NS = 40_000_000


def zone_settings(
    *zones: DetectionZone,
    sensitivity: int = 50,
    block_size: int = BLOCK,
    direction: TravelDirection = TravelDirection.LEFT_TO_RIGHT,
) -> DetectorSettings:
    return DetectorSettings(
        zones,
        block_size=block_size,
        sensitivity=sensitivity,
        direction=direction,
    )


def one_zone(
    sensitivity: int = 50,
    direction: TravelDirection = TravelDirection.LEFT_TO_RIGHT,
    block_size: int = BLOCK,
) -> DetectorSettings:
    return zone_settings(
        DetectionZone(POSITION, 1, DetectionRoi(0, 0, WIDTH, HEIGHT)),
        sensitivity=sensitivity,
        block_size=block_size,
        direction=direction,
    )


def picture(value: int = 0) -> GrayFrame:
    return GrayFrame.blank(WIDTH, HEIGHT, value)


def car_at(x: int, *, value: int = CAR, base: int = 0) -> GrayFrame:
    return picture(base).paint(DetectionRoi(x * _PX, 0, 32 * _PX, 24 * _PX), value)


def residual(base: int = 0) -> GrayFrame:
    return picture(base).paint(
        DetectionRoi(4 * _PX, 4 * _PX, 16 * _PX, 12 * _PX),
        REST if base == 0 else base + 40,
    )


def detector(
    sensitivity: int = 50,
    direction: TravelDirection = TravelDirection.LEFT_TO_RIGHT,
    *,
    background: GrayFrame | None = None,
    block_size: int = BLOCK,
) -> LaneCrossingDetector:
    found = LaneCrossingDetector(
        one_zone(sensitivity, direction, block_size),
        background=picture() if background is None else background,
    )
    found.set_inspection(True)
    return found


def pass_car(found: LaneCrossingDetector, started: int) -> int:
    """Move the car one zone-width step. Returns the timestamp of the crossing."""
    assert found.observe(car_at(0), started) == ()
    later = started + FRAME_NS
    assert len(found.observe(car_at(32), later)) == 1
    assert found.zone_state(POSITION, 1) is ZoneState.OCCUPIED
    return later


def reference_at(found: LaneCrossingDetector, row: int, col: int) -> float:
    inspection = found.last_inspection()
    assert inspection is not None
    return inspection.zones[0].reference[row][col]


def test_a_normal_pass_is_still_accepted() -> None:
    found = detector()
    pass_car(found, 0)
    assert found.zone_state(POSITION, 1) is ZoneState.OCCUPIED


def test_each_travel_direction_accepts_at_25_fps() -> None:
    cases = (
        (TravelDirection.LEFT_TO_RIGHT, (0, 0), (20, 0)),
        (TravelDirection.RIGHT_TO_LEFT, (40, 0), (20, 0)),
        (TravelDirection.TOP_TO_BOTTOM, (0, 0), (0, 20)),
        (TravelDirection.BOTTOM_TO_TOP, (0, 40), (0, 20)),
    )
    for direction, start, end in cases:
        found = square_detector(direction)
        assert found.observe(blob(*start), 0) == ()
        assert len(found.observe(blob(*end), FRAME_NS)) == 1


def test_static_remainder_does_not_hold_the_zone_for_30_seconds() -> None:
    found = detector()
    left = pass_car(found, 0)
    stamp = left + FRAME_NS
    found.observe(residual(), stamp)
    released_at: int | None = None
    deadline = stamp + 30_000_000_000
    while stamp < deadline:
        stamp += 100_000_000
        assert found.observe(residual(), stamp) == ()
        if released_at is None and found.zone_state(POSITION, 1) is ZoneState.CLEAR:
            released_at = stamp
            inspection = found.last_inspection()
            assert inspection is not None
            zone = inspection.zones[0]
            assert zone.component_blocks > zone.required_blocks
            assert zone.reason == "released_to_background"
    assert released_at is not None
    assert released_at - left < 500_000_000
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR
    second = pass_car(found, deadline + FRAME_NS)
    assert found.zone_state(POSITION, 1) is ZoneState.OCCUPIED
    assert second > deadline


def test_a_second_car_after_a_short_pause_is_accepted() -> None:
    found = detector()
    left = pass_car(found, 0)
    stamp = left
    while found.zone_state(POSITION, 1) is ZoneState.OCCUPIED and stamp < left + 1_000_000_000:
        stamp += FRAME_NS
        found.observe(residual(), stamp)
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR
    assert stamp - left < 500_000_000
    assert len(found.observe(car_at(0), stamp + FRAME_NS)) == 0
    assert len(found.observe(car_at(32), stamp + 2 * FRAME_NS)) == 1


def test_a_stopped_car_is_not_learned_into_the_background() -> None:
    found = detector()
    left = pass_car(found, 0)
    parked = car_at(32)
    stamp = left
    for _ in range(125):
        stamp += FRAME_NS
        assert found.observe(parked, stamp) == ()
    assert found.zone_state(POSITION, 1) is ZoneState.OCCUPIED
    assert reference_at(found, 0, 8) < 1.0
    inspection = found.last_inspection()
    assert inspection is not None
    assert inspection.zones[0].reference_frozen is True
    assert inspection.zones[0].reason == "occupied"


def test_a_slow_car_stays_visible_to_the_detector() -> None:
    found = detector()
    stamp = 0
    crossings = 0
    for step in range(8):
        stamp += 200_000_000
        crossings += len(found.observe(car_at(step * 4), stamp))
        assert reference_at(found, 0, 0) < 1.0
    assert crossings == 1
    assert found.zone_state(POSITION, 1) is ZoneState.OCCUPIED


def test_slow_lighting_follows_the_reference_without_a_crossing() -> None:
    found = detector(background=picture(40))
    stamp = 0
    found.observe(picture(40), stamp)
    for step in range(1, 81):
        stamp += 100_000_000
        assert found.observe(picture(40 + step), stamp) == ()
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR
    followed = reference_at(found, 0, 0)
    assert followed > 100
    inspection = found.last_inspection()
    assert inspection is not None
    assert inspection.zones[0].max_difference < inspection.zones[0].difference_threshold


def test_an_abrupt_brightness_jump_is_not_a_directed_crossing() -> None:
    found = detector(background=picture(40))
    stamp = 0
    found.observe(picture(40), stamp)
    for _ in range(100):
        stamp += FRAME_NS
        assert found.observe(picture(160), stamp) == ()
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR
    early = found.last_inspection()
    assert early is not None
    assert early.zones[0].reason in {
        "insufficient_motion",
        "too_short",
        "background_adapting",
        "clear",
    }
    for _ in range(80):
        stamp += 50_000_000
        assert found.observe(picture(160), stamp) == ()
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR
    inspection = found.last_inspection()
    assert inspection is not None
    assert inspection.zones[0].max_difference < inspection.zones[0].difference_threshold


def test_zones_adapt_their_references_independently() -> None:
    settings = zone_settings(
        DetectionZone("start", 1, DetectionRoi(0, 0, WIDTH, HEIGHT)),
        DetectionZone("sector", 2, DetectionRoi(WIDTH, 0, WIDTH, HEIGHT)),
    )
    wide = GrayFrame.blank(WIDTH * 2, HEIGHT, 0)
    found = LaneCrossingDetector(settings, background=wide)
    found.set_inspection(True)
    entered = wide.paint(DetectionRoi(0, 0, 32 * _PX, 24 * _PX), CAR)
    parked = wide.paint(DetectionRoi(32 * _PX, 0, 32 * _PX, 24 * _PX), CAR)
    assert found.observe(entered, 0) == ()
    assert len(found.observe(parked, FRAME_NS)) == 1
    for step in range(1, 40):
        brighter = GrayFrame.blank(WIDTH * 2, HEIGHT, 0)
        brighter = brighter.paint(DetectionRoi(32 * _PX, 0, 32 * _PX, 24 * _PX), CAR)
        brighter = brighter.paint(DetectionRoi(WIDTH, 0, WIDTH, HEIGHT), step)
        found.observe(brighter, step * 100_000_000)
    inspection = found.last_inspection()
    assert inspection is not None
    occupied, quiet = inspection.zones
    assert occupied.state is ZoneState.OCCUPIED
    assert occupied.reference[0][8] < 1.0
    assert quiet.state is ZoneState.CLEAR
    assert quiet.reference[0][0] > 20
    assert found.zone_state("sector", 2) is ZoneState.CLEAR


def test_low_and_high_sensitivity_still_cross() -> None:
    strict = square_detector(sensitivity=0)
    assert strict.observe(blob(0, 0), 0) == ()
    assert strict.observe(blob(20, 0), 100_000_000) == ()
    assert len(strict.observe(blob(40, 0), 200_000_000)) == 1

    loose = square_detector(sensitivity=100)
    assert loose.observe(blob(0, 0, value=20), 0) == ()
    assert len(loose.observe(blob(20, 0, value=20), FRAME_NS)) == 1


def test_block_size_and_a_small_zone_still_cross() -> None:
    small = square_detector(block_size=10)
    assert small.observe(blob(0, 0), 0) == ()
    assert len(small.observe(blob(20, 0), FRAME_NS)) == 1

    coarse = detector(block_size=8)
    assert coarse.observe(car_at(0), 0) == ()
    assert len(coarse.observe(car_at(32), FRAME_NS)) == 1


def test_irregular_frame_spacing_still_crosses_and_uses_real_time() -> None:
    found = detector()
    assert found.observe(car_at(0), 0) == ()
    assert len(found.observe(car_at(32), 40_000_000)) == 1

    short = LaneCrossingDetector(one_zone(), background=picture(0))
    short.set_inspection(True)
    long = LaneCrossingDetector(one_zone(), background=picture(0))
    long.set_inspection(True)
    short.observe(picture(0), 0)
    long.observe(picture(0), 0)
    short.observe(picture(10), 40_000_000)
    long.observe(picture(10), 80_000_000)
    # The inspection shows the reference this frame compared, which is the
    # reference after the previous step.
    short.observe(picture(10), 80_000_000)
    long.observe(picture(10), 160_000_000)
    assert reference_at(long, 0, 0) > reference_at(short, 0, 0)


def test_diagnosis_shows_the_live_reference_freeze_and_release() -> None:
    found = detector()
    found.observe(car_at(0), 0)
    found.observe(car_at(32), FRAME_NS)
    frozen = found.last_inspection()
    assert frozen is not None
    assert frozen.zones[0].reference_frozen is True
    assert frozen.zones[0].reference[0][8] < 1.0
    reference_view = render_picture(frozen, DiagnosticView.REFERENCE, scale=2)
    analysis_view = render_picture(frozen, DiagnosticView.ANALYSIS, scale=2)
    assert reference_view.pixels[16] == 0
    assert analysis_view.pixels[16] == CAR

    stamp = FRAME_NS
    reason = ""
    for _ in range(20):
        stamp += FRAME_NS
        found.observe(residual(), stamp)
        inspection = found.last_inspection()
        assert inspection is not None
        reason = inspection.zones[0].reason
        if reason == "released_to_background":
            assert inspection.zones[0].release_candidate is False
            assert inspection.zones[0].state is ZoneState.CLEAR
            break
    assert reason == "released_to_background"


def test_inspection_does_not_change_the_crossings() -> None:
    settings = one_zone()
    quiet = LaneCrossingDetector(settings, background=picture())
    watched = LaneCrossingDetector(settings, background=picture())
    watched.set_inspection(True)
    stamps = [0, FRAME_NS, 2 * FRAME_NS, 200_000_000, 30_000_000_000]
    frames = [car_at(0), car_at(32), residual(), residual(), car_at(0)]
    for frame, stamp in zip(frames, stamps, strict=True):
        assert quiet.observe(frame, stamp) == watched.observe(frame, stamp)
    assert len(quiet.observe(car_at(32), 30_000_000_000 + FRAME_NS)) == 1


def test_the_race_factory_uses_the_same_release() -> None:
    found = create_lane_detector(one_zone(), background=picture())
    left = pass_car(found, 0)
    stamp = left
    while found.zone_state(POSITION, 1) is ZoneState.OCCUPIED and stamp < left + 1_000_000_000:
        stamp += FRAME_NS
        found.observe(residual(), stamp)
    assert found.zone_state(POSITION, 1) is ZoneState.CLEAR
    assert found.observe(car_at(0), stamp + FRAME_NS) == ()
    assert len(found.observe(car_at(32), stamp + 2 * FRAME_NS)) == 1


def test_analysis_stays_fast_on_a_camera_sized_frame() -> None:
    width, height = 640, 480
    settings = DetectorSettings(
        (DetectionZone(POSITION, 1, DetectionRoi(200, 160, 160, 80)),),
        block_size=20,
        sensitivity=50,
    )
    background = GrayFrame(width, height, bytes(width * height))
    found = LaneCrossingDetector(settings, background=background)
    frame = background
    started = time.perf_counter_ns()
    for index in range(1, 301):
        found.observe(frame, index * FRAME_NS)
    elapsed = time.perf_counter_ns() - started
    average = elapsed / 300
    assert average < 5_000_000, f"average analysis {average / 1_000_000:.3f} ms"
