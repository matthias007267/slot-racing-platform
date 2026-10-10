"""Calibration polygon, recording, scoring and the assistant."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot
from sqlalchemy.orm.attributes import flag_modified

from slot_racing.core.i18n import Translator
from slot_racing.core.storage import Database
from slot_racing.modules.timing_camera.calibration import (
    LAP_DEFAULT,
    LAP_MAXIMUM,
    LAP_MINIMUM,
    LAP_STEP,
    CalibrationAttempt,
)
from slot_racing.modules.timing_camera.calibration_analysis import (
    analyze_recording,
    assess_intervals,
)
from slot_racing.modules.timing_camera.calibration_polygon import (
    CalibrationPolygon,
    contains_point,
    rectangle_inside_polygon,
    rois_overlap,
)
from slot_racing.modules.timing_camera.calibration_recording import (
    CalibrationTape,
    RecordingError,
    RecordingOverflowError,
)
from slot_racing.modules.timing_camera.configuration import (
    CameraConfiguration,
    NormalizedRoi,
    StoredCamera,
    StoredDetection,
    StoredDetectionZone,
    roi_to_pixels,
)
from slot_racing.modules.timing_camera.detection import TravelDirection
from slot_racing.modules.timing_camera.frames import GrayFrame
from slot_racing.modules.timing_camera.geometry import DetectionRoi
from slot_racing.modules.timing_camera.plugin import CameraTimingPlugin
from slot_racing.modules.timing_camera.store import CameraConfigurationStore
from slot_racing.modules.timing_camera.ui.calibration_wizard import CalibrationWizard, Poll
from slot_racing.modules.timing_camera.ui.page import CameraSetupPage
from tests.modules.test_camera_configuration import database
from tests.modules.test_camera_setup_ui import FakeOpener, open_page

WIDTH = 100
HEIGHT = 40
LANE = NormalizedRoi(x=0.0, y=0.0, width=1.0, height=0.25)
OTHER = NormalizedRoi(x=0.0, y=0.5, width=1.0, height=0.25)
POLYGON = CalibrationPolygon(((0.0, 0.0), (1.0, 0.0), (1.0, 0.25), (0.0, 0.25)))


def test_points_can_be_added_moved_and_removed() -> None:
    polygon = CalibrationPolygon(()).added(0.1, 0.1).added(0.4, 0.1).added(0.2, 0.4)
    assert polygon.is_valid
    moved = polygon.moved(1, 0.5, 0.2)
    assert moved.points[1] == (0.5, 0.2)
    assert polygon.points[1] == (0.4, 0.1)
    assert not moved.without(0).is_valid


def test_a_self_crossing_polygon_is_rejected() -> None:
    bowtie = CalibrationPolygon(((0.1, 0.1), (0.9, 0.9), (0.9, 0.1), (0.1, 0.9)))
    assert bowtie.problem == "self_intersection"
    assert not rectangle_inside_polygon(bowtie, LANE)


def test_normalized_points_map_onto_each_camera_resolution() -> None:
    small = roi_to_pixels(LANE, 100, 40)
    large = roi_to_pixels(LANE, 1920, 1080)
    assert (small.x, small.y, small.width, small.height) == (0, 0, 100, 10)
    assert (large.x, large.y, large.width, large.height) == (0, 0, 1920, 270)
    assert contains_point(POLYGON, 0.5, 0.1)
    assert not contains_point(POLYGON, 0.5, 0.9)


def test_a_rectangle_must_lie_inside_the_polygon_and_off_the_other_lane() -> None:
    inside = NormalizedRoi(x=0.2, y=0.05, width=0.2, height=0.1)
    assert rectangle_inside_polygon(POLYGON, inside, margin=0.01)
    spilling = NormalizedRoi(x=0.2, y=0.1, width=0.2, height=0.45)
    assert not rectangle_inside_polygon(POLYGON, spilling, margin=0.0)
    assert rois_overlap(spilling, OTHER)
    assert not rois_overlap(inside, OTHER)


def test_interval_assessment_sees_misses_ghosts_and_an_unclear_count() -> None:
    second = 1_000_000_000
    clean = [index * second for index in range(10)]
    assert assess_intervals(clean, 10).rating == "good"
    missed = [0, second, 3 * second, 4 * second, 5 * second]
    assessed = assess_intervals(missed, 5)
    assert assessed.missed >= 1
    ghost = [0, second, second + second // 10, 2 * second, 3 * second]
    assert assess_intervals(ghost, 4).ghosts >= 1
    even_but_wrong = [index * second for index in range(10)]
    assert assess_intervals(even_but_wrong, 40).rating == "unclear"
    assert assess_intervals([0, second], 10).rating == "insufficient"
    # Five events for five laps, but one gap is a double trigger and another hides a miss.
    cancelled = [0, second, second + second // 10, 2 * second, 4 * second]
    hidden = assess_intervals(cancelled, 5)
    assert hidden.detected == 5
    assert hidden.ghosts >= 1
    assert hidden.missed >= 1
    assert hidden.rating != "good"


def test_recording_keeps_order_timestamps_gaps_and_drops_the_files(tmp_path: Path) -> None:
    tape = CalibrationTape(tmp_path / "tape", max_bytes=50_000)
    frame = GrayFrame.blank(WIDTH, HEIGHT, 10)
    tape.append(frame, 1_000_000, 1, (0, 0, 20, 10), process_ns=2_000_000)
    tape.append(
        frame.paint(DetectionRoi(4, 0, 8, 8), 200),
        2_000_000,
        4,
        (0, 0, 20, 10),
        process_ns=4_000_000,
    )
    stats = tape.stats()
    assert stats.dropped == 2
    assert stats.fps == pytest.approx(1000.0)
    assert stats.mean_process_ns == 3_000_000
    replay = list(tape.frames())
    assert [item.timestamp_ns for item in replay] == [1_000_000, 2_000_000]
    assert [item.sequence for item in replay] == [1, 4]
    assert replay[1].gap_before == 2
    assert replay[0].width == WIDTH and replay[1].pixels[0] == 10
    wide = CalibrationTape(tmp_path / "wide")
    wide.append(GrayFrame.blank(1920, 1080), 5, 1, (0, 0, 8, 8))
    assert next(wide.frames()).width == 1920
    wide.release()
    tape.close()
    tape.release()
    assert not (tmp_path / "tape").exists()


def test_a_full_recording_refuses_another_frame_instead_of_dropping_it(tmp_path: Path) -> None:
    tape = CalibrationTape(tmp_path / "tape", max_bytes=80)
    frame = GrayFrame.blank(32, 32, 40)
    tape.append(frame, 1, 1, (0, 0, 16, 16))
    with pytest.raises(RecordingOverflowError):
        for index in range(20):
            tape.append(frame, index + 2, index + 2, (0, 0, 16, 16))
    assert tape.overflow
    assert tape.frame_count >= 1


def test_a_resolution_change_is_not_turned_into_a_result(tmp_path: Path) -> None:
    del tmp_path
    attempt = CalibrationAttempt(POLYGON, max_bytes=50_000)
    attempt.start()
    attempt.note_frame(GrayFrame.blank(WIDTH, HEIGHT), 1_000, 1)
    with pytest.raises(RecordingError) as caught:
        attempt.note_frame(GrayFrame.blank(WIDTH // 2, HEIGHT), 2_000, 2)
    assert caught.value.code == "resolution_changed"
    assert attempt.failed == "resolution_changed"
    tape = attempt.finish()
    report = analyze_recording(
        tape,
        polygon=POLYGON,
        position_id="start_finish",
        lane=1,
        other_rois=(),
        direction=TravelDirection.LEFT_TO_RIGHT,
        check_direction=True,
        block_size=20,
        target_laps=10,
        saved_width=WIDTH,
        saved_height=HEIGHT,
    )
    assert not report.applicable
    assert "recording_unusable" in report.reasons
    attempt.cancel()


def test_restart_discards_the_previous_attempt(tmp_path: Path) -> None:
    del tmp_path
    attempt = CalibrationAttempt(POLYGON, max_bytes=10_000)
    attempt.start()
    attempt.note_frame(GrayFrame.blank(WIDTH, HEIGHT), 10, 1)
    assert attempt.frame_count == 1
    first = attempt.identity
    attempt.restart()
    assert attempt.frame_count == 0
    assert attempt.identity != first
    attempt.note_frame(GrayFrame.blank(WIDTH, HEIGHT, 30), 20, 1)
    tape = attempt.finish()
    assert tape.frame_count == 1
    assert next(iter(tape.frames())).timestamp_ns == 20
    attempt.cancel()


def test_a_clear_drive_recommends_a_zone_on_the_used_lane_only(tmp_path: Path) -> None:
    tape = _drive(tmp_path / "clear", laps=6, skip=(), ghosts=())
    report = analyze_recording(
        tape,
        polygon=POLYGON,
        position_id="start_finish",
        lane=1,
        other_rois=(OTHER,),
        direction=TravelDirection.LEFT_TO_RIGHT,
        check_direction=True,
        block_size=20,
        target_laps=6,
        saved_width=WIDTH,
        saved_height=HEIGHT,
        margin=0.0,
    )
    assert report.applicable
    assert report.roi is not None
    assert report.detected == 6
    assert report.missed == 0
    assert report.ghosts == 0
    assert rectangle_inside_polygon(POLYGON, report.roi, margin=0.0)
    assert not rois_overlap(report.roi, OTHER)
    assert "neighbor_excluded" in report.reasons
    tape.release()


def test_missed_and_ghost_laps_stay_visible_in_the_report(tmp_path: Path) -> None:
    missed = analyze_recording(
        _drive(tmp_path / "miss", laps=6, skip=(3,), ghosts=()),
        polygon=POLYGON,
        position_id="start_finish",
        lane=1,
        other_rois=(),
        direction=TravelDirection.LEFT_TO_RIGHT,
        check_direction=False,
        block_size=20,
        target_laps=6,
        saved_width=WIDTH,
        saved_height=HEIGHT,
        margin=0.0,
    )
    assert missed.missed >= 1
    ghost = analyze_recording(
        _drive(tmp_path / "ghost", laps=6, skip=(), ghosts=(2,)),
        polygon=POLYGON,
        position_id="start_finish",
        lane=1,
        other_rois=(),
        direction=TravelDirection.LEFT_TO_RIGHT,
        check_direction=False,
        block_size=20,
        target_laps=6,
        saved_width=640,
        saved_height=480,
        margin=0.0,
    )
    assert ghost.ghosts >= 1
    assert "capture_resolution_differs" in ghost.reasons
    assert "processing_is_block_size" in ghost.reasons
    assert "short_intervals" in ghost.reasons
    assert ghost.rating != "good"


def test_a_faint_car_is_not_kept_on_a_sensitivity_that_misses_it(tmp_path: Path) -> None:
    tape = _drive(tmp_path / "faint", laps=6, skip=(), ghosts=(), level=40)
    report = analyze_recording(
        tape,
        polygon=POLYGON,
        position_id="start_finish",
        lane=1,
        other_rois=(),
        direction=TravelDirection.LEFT_TO_RIGHT,
        check_direction=False,
        block_size=20,
        target_laps=6,
        saved_width=WIDTH,
        saved_height=HEIGHT,
        margin=0.0,
    )
    assert report.applicable
    assert report.detected == 6
    assert report.sensitivity >= 80
    assert report.roi is not None
    assert report.roi.width < 0.5
    tape.release()


def test_a_hundred_laps_stay_inside_the_recording_budget(tmp_path: Path) -> None:
    width, height = 640, 480
    polygon = CalibrationPolygon(((0.0, 0.0), (1.0, 0.0), (1.0, 0.2), (0.0, 0.2)))
    blank = GrayFrame.blank(width, height)
    left = blank.paint(DetectionRoi(240, 8, 36, 70), 220)
    right = blank.paint(DetectionRoi(320, 8, 36, 70), 220)
    tape = CalibrationTape(tmp_path / "tape", max_bytes=32 * 1024 * 1024)
    clock = 0
    sequence = 1
    crop = (0, 0, width, 96)
    tape.append(blank, clock, sequence, crop)
    for _lap in range(100):
        for frame in (left, right, blank):
            clock += 200_000_000
            sequence += 1
            tape.append(frame, clock, sequence, crop)
    tape.close()
    stats = tape.stats()
    assert stats.frames == 301
    assert stats.bytes_used < 1024 * 1024
    report = analyze_recording(
        tape,
        polygon=polygon,
        position_id="start_finish",
        lane=1,
        other_rois=(),
        direction=TravelDirection.LEFT_TO_RIGHT,
        check_direction=False,
        block_size=20,
        target_laps=100,
        saved_width=width,
        saved_height=height,
        margin=0.0,
    )
    assert report.applicable
    assert report.detected == 100
    assert report.camera_fps == pytest.approx(5.0)
    assert report.analysis_ns > 0
    tape.release()
    assert not (tmp_path / "tape").exists()


def test_too_little_motion_is_not_presented_as_a_confident_result(tmp_path: Path) -> None:
    tape = CalibrationTape(tmp_path / "still" / "tape")
    for index in range(4):
        tape.append(GrayFrame.blank(WIDTH, HEIGHT), index * 1_000_000, index + 1, (0, 0, WIDTH, 10))
    tape.close()
    report = analyze_recording(
        tape,
        polygon=POLYGON,
        position_id="start_finish",
        lane=1,
        other_rois=(),
        direction=TravelDirection.LEFT_TO_RIGHT,
        check_direction=True,
        block_size=20,
        target_laps=10,
        saved_width=WIDTH,
        saved_height=HEIGHT,
    )
    assert not report.applicable
    assert report.rating == "insufficient"
    tape.release()


def test_the_stage_moves_and_removes_points_from_the_picture(qtbot: QtBot) -> None:
    from slot_racing.modules.timing_camera.ui.polygon_stage import PolygonStage

    stage = PolygonStage()
    stage.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    stage.set_frame(GrayFrame.blank(WIDTH, HEIGHT))
    stage.resize(400, 200)
    stage.show()
    qtbot.addWidget(stage)
    qtbot.waitUntil(lambda: stage.width() == 400)
    for point in ((0.1, 0.1), (0.8, 0.1), (0.75, 0.8)):
        qtbot.mouseClick(  # type: ignore[no-untyped-call]
            stage,
            Qt.MouseButton.LeftButton,
            pos=stage._to_widget(point[0], point[1]),
        )
    assert stage.polygon().is_valid
    origin = stage.polygon().points[0]
    qtbot.mousePress(  # type: ignore[no-untyped-call]
        stage, Qt.MouseButton.LeftButton, pos=stage._to_widget(*origin)
    )
    moved = stage._to_widget(0.2, 0.25)
    qtbot.mouseMove(stage, pos=moved)  # type: ignore[no-untyped-call]
    qtbot.mouseRelease(stage, Qt.MouseButton.LeftButton, pos=moved)  # type: ignore[no-untyped-call]
    assert stage.polygon().points[0] != origin
    qtbot.mouseClick(  # type: ignore[no-untyped-call]
        stage,
        Qt.MouseButton.RightButton,
        pos=stage._to_widget(*stage.polygon().points[2]),
    )
    assert len(stage.polygon().points) == 2
    stage.reset()
    assert stage.polygon().points == ()


def test_the_wizard_keeps_settings_until_the_result_is_accepted(qtbot: QtBot) -> None:
    stored = database()
    original = _configuration()
    CameraConfigurationStore(stored).save(original)
    page, _opener = open_page(qtbot, CameraConfigurationStore(stored), FakeOpener(indices=(0,)))
    assert page.calibrate.objectName() == "camera-calibrate"
    wizard = _wizard(page)
    qtbot.addWidget(wizard)
    wizard.show()
    assert wizard.laps.value() == LAP_DEFAULT
    assert wizard.laps.singleStep() == LAP_STEP
    assert wizard.laps.minimum() == LAP_MINIMUM
    assert wizard.laps.maximum() == LAP_MAXIMUM
    wizard.laps.setValue(40)
    assert wizard.findChild(type(wizard.laps), "calibration-laps").value() == 40  # type: ignore[union-attr]
    wizard.laps.setValue(15)
    assert wizard.laps.value() == 20
    _prepare_polygon(qtbot, wizard)
    assert wizard.stage.polygon().is_valid
    before = CameraConfigurationStore(stored).load()
    cancel = wizard.findChildren(type(wizard._start), "calibration-cancel")[0]
    cancel.click()
    assert wizard.proposal is None
    assert CameraConfigurationStore(stored).load() == before


def test_finish_restart_and_apply_only_change_the_chosen_zone(qtbot: QtBot) -> None:
    stored = database()
    CameraConfigurationStore(stored).save(_configuration())
    page, _opener = open_page(qtbot, CameraConfigurationStore(stored), FakeOpener(indices=(0,)))
    before = CameraConfigurationStore(stored).load()
    neighbor = before.detection.zones[1]
    wizard = _wizard(page, poll=lambda: None)
    qtbot.addWidget(wizard)
    wizard.show()
    wizard.laps.setValue(LAP_MINIMUM)
    _prepare_polygon(qtbot, wizard)
    wizard.findChild(type(wizard._start), "calibration-start").click()  # type: ignore[union-attr]
    assert f"{LAP_MINIMUM}" in wizard._prompt.text()
    assert "Bahn 1" in wizard._prompt.text()
    for index, frame in enumerate(_lap_frames(4)):
        wizard.feed(frame, index * 1_000_000, index + 1)
    assert wizard._attempt is not None
    assert wizard._attempt.frame_count > 0
    discarded = wizard._attempt.identity
    wizard.findChild(type(wizard._start), "calibration-restart").click()  # type: ignore[union-attr]
    assert wizard._attempt is not None
    assert wizard._attempt.frame_count == 0
    assert wizard._attempt.identity != discarded
    for index, frame in enumerate(_lap_frames(LAP_MINIMUM)):
        wizard.feed(frame, (index + 1) * 1_000_000_000, index + 1)
    wizard.findChild(type(wizard._start), "calibration-done").click()  # type: ignore[union-attr]
    qtbot.waitUntil(lambda: wizard._report is not None, timeout=20000)
    report = wizard._report
    assert report is not None
    assert report.applicable
    assert report.detected == LAP_MINIMUM
    wizard._shared.setChecked(False)
    wizard.findChild(type(wizard._start), "calibration-apply").click()  # type: ignore[union-attr]
    assert wizard.proposal is not None
    assert wizard.proposal.apply_shared is False
    page._apply_proposal(wizard.proposal)
    saved = CameraConfigurationStore(stored).load()
    assert saved.detection.zones[1] == neighbor
    assert saved.detection.sensitivity == before.detection.sensitivity
    assert saved.detection.block_size == before.detection.block_size
    assert saved.detection.direction == before.detection.direction
    assert saved.detection.zones[0].roi == wizard.proposal.roi
    assert saved.calibration_polygons[0].lane == 1
    assert not rois_overlap(saved.detection.zones[0].roi, neighbor.roi)


def test_without_a_camera_the_recording_does_not_start(qtbot: QtBot) -> None:
    stored = database()
    CameraConfigurationStore(stored).save(_configuration())
    page, _opener = open_page(qtbot, CameraConfigurationStore(stored), FakeOpener(indices=(0,)))
    wizard = _wizard(page, poll=None)
    qtbot.addWidget(wizard)
    wizard.show()
    _prepare_polygon(qtbot, wizard)
    wizard.findChild(type(wizard._start), "calibration-start").click()  # type: ignore[union-attr]
    assert wizard._attempt is None
    assert wizard._polygon_problem.text()
    assert CameraConfigurationStore(stored).load().detection.zones[0].roi == LANE


def test_a_stored_polygon_is_offered_on_the_next_calibration(qtbot: QtBot) -> None:
    stored = database()
    CameraConfigurationStore(stored).save(_configuration())
    page, _opener = open_page(qtbot, CameraConfigurationStore(stored), FakeOpener(indices=(0,)))
    wizard = _wizard(page, polygons={("start_finish", 1): POLYGON})
    qtbot.addWidget(wizard)
    wizard.show()
    wizard._show_polygon()
    assert wizard.stage.polygon().points == POLYGON.points
    bowtie = CalibrationPolygon(((0.1, 0.1), (0.9, 0.9), (0.9, 0.1), (0.1, 0.9)))
    wizard.stage.set_polygon(bowtie)
    assert not wizard._start.isEnabled()


def _wizard(
    page: CameraSetupPage,
    poll: Poll | None = None,
    polygons: dict[tuple[str, int], CalibrationPolygon] | None = None,
) -> CalibrationWizard:
    zones = [
        (draft.position_id, draft.lane, draft.roi, draft.check_direction) for draft in page._drafts
    ]
    return CalibrationWizard(
        _translator(),
        zones,
        {} if polygons is None else polygons,
        direction=TravelDirection.LEFT_TO_RIGHT,
        sensitivity=page.sensitivity.value(),
        block_size=page._block_size,
        saved_width=WIDTH,
        saved_height=HEIGHT,
        poll=poll,
        parent=page,
    )


def _prepare_polygon(qtbot: QtBot, wizard: CalibrationWizard) -> None:
    wizard._show_polygon()
    qtbot.waitUntil(lambda: wizard.stage.isVisible() and wizard.stage.width() > 200)
    _draw_lane(qtbot, wizard)


def _draw_lane(qtbot: QtBot, wizard: CalibrationWizard) -> None:
    wizard.stage.set_frame(GrayFrame.blank(WIDTH, HEIGHT))
    for point in ((0.02, 0.02), (0.98, 0.02), (0.98, 0.30), (0.02, 0.30)):
        qtbot.mouseClick(  # type: ignore[no-untyped-call]
            wizard.stage,
            Qt.MouseButton.LeftButton,
            pos=wizard.stage._to_widget(point[0], point[1]),
        )


def _configuration() -> CameraConfiguration:
    return CameraConfiguration(
        camera=StoredCamera(width=WIDTH, height=HEIGHT, fps=30),
        detection=StoredDetection(
            zones=(
                StoredDetectionZone(position_id="start_finish", lane=1, roi=LANE),
                StoredDetectionZone(position_id="start_finish", lane=2, roi=OTHER),
            ),
            sensitivity=50,
            block_size=20,
            direction=TravelDirection.LEFT_TO_RIGHT,
        ),
    )


def _translator() -> Translator:
    result = Translator()
    result.add_catalog("de", CameraTimingPlugin.translations["de"])
    return result


def _drive(
    directory: Path,
    *,
    laps: int,
    skip: tuple[int, ...],
    ghosts: tuple[int, ...],
    level: int = 255,
) -> CalibrationTape:
    """Normal laps are 200 ms apart. A ghost pass sits a few tens of milliseconds later."""
    tape = CalibrationTape(directory / "tape")
    clock = 0
    sequence = 1
    normal = 200_000_000
    burst = 20_000_000
    planned: list[tuple[GrayFrame, int]] = [(GrayFrame.blank(WIDTH, HEIGHT), 0)]
    for lap in range(laps):
        if lap in skip:
            blank = GrayFrame.blank(WIDTH, HEIGHT)
            planned.append((blank, normal))
            planned.append((blank, normal))
            continue
        step = burst if lap in ghosts else normal
        for frame in _pass_frames(level):
            planned.append((frame, step))
        if lap in ghosts:
            for frame in _pass_frames(level):
                planned.append((frame, burst))
            # Settle so the lap after the ghost is not itself a short interval.
            planned.append((GrayFrame.blank(WIDTH, HEIGHT), normal * 2))
    for frame, step in planned:
        clock += step
        tape.append(frame, clock, sequence, (0, 0, WIDTH, 10))
        sequence += 1
    tape.close()
    return tape


def _lap_frames(
    laps: int, *, skip: tuple[int, ...] = (), ghosts: tuple[int, ...] = ()
) -> list[GrayFrame]:
    """One background, then each lap as a left tile and a right tile on lane 1."""
    frames = [GrayFrame.blank(WIDTH, HEIGHT)]
    for lap in range(laps):
        if lap in skip:
            frames.append(GrayFrame.blank(WIDTH, HEIGHT))
            frames.append(GrayFrame.blank(WIDTH, HEIGHT))
            continue
        frames.extend(_pass_frames())
        if lap in ghosts:
            frames.extend(_pass_frames())
    return frames


def _pass_frames(level: int = 255) -> tuple[GrayFrame, GrayFrame, GrayFrame]:
    blank = GrayFrame.blank(WIDTH, HEIGHT)
    left = blank.paint(DetectionRoi(40, 0, 4, 10), level)
    right = blank.paint(DetectionRoi(48, 0, 4, 10), level)
    return left, right, blank


def test_an_older_document_without_a_polygon_still_loads() -> None:
    stored: Database = database()
    CameraConfigurationStore(stored).save(_configuration())
    with stored.session() as session:
        from slot_racing.core.storage import Setting
        from slot_racing.modules.timing_camera.store import CAMERA_CONFIGURATION_KEY

        row = session.get(Setting, CAMERA_CONFIGURATION_KEY)
        assert row is not None
        assert isinstance(row.value, dict)
        payload = dict(row.value)
        payload.pop("calibration_polygons", None)
        row.value = payload
        flag_modified(row, "value")
    loaded = CameraConfigurationStore(stored).load()
    assert loaded.calibration_polygons == ()
    assert loaded.detection.zones[0].roi == LANE
