"""Detection diagnosis uses the race detector and does not open a camera."""

from __future__ import annotations

import inspect
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFileDialog
from pytestqt.qtbot import QtBot

from slot_racing.core.domain import TimingLayout, TimingSensor, TimingSetup
from slot_racing.core.timing import TimingSessionSpec
from slot_racing.modules.timing_camera import diagnostic as diagnostic_module
from slot_racing.modules.timing_camera.detection import (
    LaneCrossingDetector,
    block_means,
    create_lane_detector,
    sensitivity_profile,
)
from slot_racing.modules.timing_camera.diagnostic import (
    CaptureCounters,
    DiagnosticSession,
    DiagnosticView,
    diagnostic_config,
    format_header,
    render_picture,
)
from slot_racing.modules.timing_camera.frame_source import ManualFrameSource, TimedFrame
from slot_racing.modules.timing_camera.frames import GrayFrame
from slot_racing.modules.timing_camera.geometry import DetectionRoi
from slot_racing.modules.timing_camera.provider import CameraTimingProvider
from slot_racing.modules.timing_camera.store import CameraConfigurationStore
from slot_racing.modules.timing_camera.ui.diagnostic_dialog import DetectionDiagnosticDialog
from tests.modules.test_camera_configuration import database
from tests.modules.test_camera_detection import (
    HEIGHT,
    LINE_X,
    POSITION,
    SQUARE,
    WIDTH,
    blank,
    blob,
    column,
    detector,
    settings,
    square_detector,
    square_frame,
)
from tests.modules.test_camera_setup_ui import (
    open_page,
    saved_configuration,
    translator,
)

_NS = 1_000_000_000


def _config_for(configured: object, width: int, height: int, *, fps: int = 30) -> object:
    from slot_racing.modules.timing_camera.detection import DetectorSettings

    assert isinstance(configured, DetectorSettings)
    return diagnostic_config(
        app_version="test-version",
        started_at="2026-10-06T12:00:00+00:00",
        device_index=2,
        requested_width=width,
        requested_height=height,
        requested_fps=fps,
        settings=configured,
    )


def _session(
    configured: object,
    width: int,
    height: int,
    *,
    max_lines: int = 12_000,
    journal_path: Path | None = None,
    max_frame_records: int = 50_000,
) -> DiagnosticSession:
    config = _config_for(configured, width, height)
    from slot_racing.modules.timing_camera.detection import DetectorSettings
    from slot_racing.modules.timing_camera.diagnostic import DiagnosticConfig

    assert isinstance(configured, DetectorSettings)
    assert isinstance(config, DiagnosticConfig)
    return DiagnosticSession(
        configured,
        config,
        max_lines=max_lines,
        journal_path=journal_path,
        max_frame_records=max_frame_records,
    )


def _feed(session: DiagnosticSession, pictures: tuple[GrayFrame, ...], start_ns: int = 0) -> None:
    session.start()
    for index, picture in enumerate(pictures):
        session.submit(TimedFrame(picture, start_ns + index * 20_000_000))
        assert session.wait_idle()


def _race_spec() -> TimingSessionSpec:
    setup = TimingSetup(
        TimingLayout.from_position_ids([POSITION]),
        (TimingSensor("sensor-sf", POSITION),),
    )
    return TimingSessionSpec(setup, (1,), 1)


def test_inspection_uses_the_race_detector_and_keeps_the_same_crossings() -> None:
    assert "create_lane_detector" in inspect.getsource(CameraTimingProvider._reset_detector)
    assert "create_lane_detector" in inspect.getsource(DiagnosticSession._detector_for)
    quiet = detector(1)
    watched = create_lane_detector(settings(1), background=blank())
    assert isinstance(watched, LaneCrossingDetector)
    watched.set_inspection(True)
    pictures = (column(1, LINE_X), column(1, LINE_X + 2), blank())
    for index, picture in enumerate(pictures):
        assert quiet.observe(picture, index) == watched.observe(picture, index)
    assert watched.last_inspection() is not None


def test_diagnosis_and_the_race_report_the_same_crossings() -> None:
    configured = settings(1)
    frames = ManualFrameSource()
    provider = CameraTimingProvider(_race_spec(), frames, configured)
    received: list[tuple[int, int, str]] = []

    def collect(event: object) -> None:
        from slot_racing.core.events import SensorTriggered

        assert isinstance(event, SensorTriggered)
        received.append((event.timestamp_ns, event.lane, event.position_id))

    provider.start(collect)
    session = _session(configured, WIDTH, HEIGHT)
    pictures = (blank(), column(1, LINE_X), column(1, LINE_X + 2))
    try:
        session.start()
        for index, picture in enumerate(pictures):
            frames.submit(picture, index * 20_000_000)
            session.submit(TimedFrame(picture, index * 20_000_000))
            assert session.wait_idle()
        provider.poll()
    finally:
        provider.stop()
        session.close()
    assert received
    reported = [(item.timestamp_ns, item.lane, item.position_id) for item in session.crossings()]
    assert reported == received


def test_diagnosis_opens_no_camera_and_runs_without_a_race(qtbot: QtBot, tmp_path: Path) -> None:
    source = inspect.getsource(diagnostic_module)
    assert "VideoCapture" not in source
    assert "OpenCVCapture" not in source
    stored = database()
    CameraConfigurationStore(stored).save(saved_configuration())
    page, opener = open_page(qtbot, CameraConfigurationStore(stored))
    page._timer.stop()
    assert len(opener.opened) == 1
    page.diagnostic.click()
    dialog = page.findChild(DetectionDiagnosticDialog)
    assert isinstance(dialog, DetectionDiagnosticDialog)
    assert dialog.windowTitle() == "Erkennungsdiagnose"
    dialog.start_diagnosis()
    assert len(opener.opened) == 1
    assert dialog._session is not None
    assert dialog._session.running
    page._pull_frame()
    assert dialog._session.wait_idle()
    dialog.stop_diagnosis()
    assert not dialog._session.running
    assert "FRAME 1" in dialog.log_text()
    page._pull_frame()
    assert dialog._session.snapshot().performance.frames_submitted == 1
    dialog.copy_log()
    clipboard = QGuiApplication.clipboard()
    assert clipboard is not None
    assert clipboard.text() == dialog.log_text()
    dialog.reference_lane.setValue(1)
    dialog.reference_laps.setValue(100)
    dialog.apply_reference()
    saved = dialog.save_log(tmp_path / "camera-diagnostic-log.txt")
    assert saved is not None
    exported = saved.read_text(encoding="utf-8")
    assert "=== Diagnoseabschluss ===" in exported
    assert "events_complete=" in exported
    assert "reference_laps=100" in exported
    assert "keine Erkennungsquote" in exported
    assert "FRAME 1" in dialog.log_text()
    assert dialog.save_button.text() == "Diagnose exportieren"
    bundle = dialog.save_diagnosis(tmp_path / "bundle.txt")
    assert bundle is not None
    summary_path, report_path, events_path = bundle
    assert summary_path.read_text(encoding="utf-8")
    assert "events_complete" in report_path.read_text(encoding="utf-8")
    assert '"kind":' in events_path.read_text(encoding="utf-8")
    assert exported != dialog.log_text()
    dialog.refresh()
    picture = dialog.save_snapshot(tmp_path / "camera-diagnostic.png")
    assert picture is not None
    assert picture.read_bytes().startswith(b"\x89PNG")
    session = dialog._session
    dialog.close()
    assert not session.alive()
    assert len(opener.opened) == 1


def test_diagnosis_can_start_and_stop_and_a_new_session_drops_the_old_log() -> None:
    session = _session(settings(1), WIDTH, HEIGHT)
    _feed(session, (blank(),))
    session.stop()
    assert "t_ns=0" in session.text()
    assert not session.running
    session.submit(TimedFrame(column(1, LINE_X), 99))
    assert session.snapshot().frame_index == 1
    session.start()
    assert "t_ns=0" not in session.text()
    assert session.snapshot().phase == "initializing"
    assert session.snapshot().reference_ready is False
    session.submit(TimedFrame(blank(), 50_000_000))
    assert session.wait_idle()
    assert "t_ns=50000000" in session.text()
    assert "t_ns=0\n" not in session.text()
    session.close()
    assert not session.alive()


def test_the_analysis_image_is_the_detector_block_matrix() -> None:
    found = detector(1)
    found.set_inspection(True)
    picture = column(1, LINE_X)
    assert found.observe(picture, 1) == ()
    inspection = found.last_inspection()
    assert inspection is not None
    zone = inspection.zones[0]
    roi = settings(1).zones[0].roi
    image = np.frombuffer(picture.to_bytes(), dtype=np.uint8).reshape(HEIGHT, WIDTH)
    crop = np.ascontiguousarray(image[roi.y : roi.y + roi.height, roi.x : roi.x + roi.width])
    expected = block_means(crop, zone.block_size)
    assert zone.analysis == tuple(tuple(float(value) for value in row) for row in expected)
    assert zone.reference == tuple(tuple(0.0 for _ in row) for row in zone.analysis)
    assert zone.difference == zone.analysis


def test_difference_and_threshold_views_use_the_real_matrices() -> None:
    found = square_detector()
    found.set_inspection(True)
    assert found.observe(blob(0, 0), 1) == ()
    inspection = found.last_inspection()
    assert inspection is not None
    zone = inspection.zones[0]
    profile = sensitivity_profile(50)
    assert zone.difference_threshold == profile.difference
    for row, active_row in enumerate(zone.active):
        for col, active in enumerate(active_row):
            assert active is (zone.difference[row][col] >= profile.difference)
    difference = render_picture(inspection, DiagnosticView.DIFFERENCE, scale=4)
    threshold = render_picture(inspection, DiagnosticView.THRESHOLD, scale=4)
    analysis = render_picture(inspection, DiagnosticView.ANALYSIS, scale=4)
    zones = render_picture(inspection, DiagnosticView.ZONES, scale=4)
    assert analysis.pixels == zones.pixels
    assert _block(difference, 0, 0, 4) == _clip(zone.difference[0][0])
    assert len(set(_block_values(difference, 0, 0, 4))) == 1
    assert _block(threshold, 0, 0, 4) == (255 if zone.active[0][0] else 0)
    assert zones.zones[0].lane == 1
    assert zones.zones[0].position_id == POSITION


def test_initialization_then_ready_and_a_zone_reports_activity() -> None:
    session = _session(square_settings(), SQUARE, SQUARE)
    session.start()
    try:
        assert session.snapshot().phase == "initializing"
        assert session.snapshot().reference_ready is False
        assert "Actual resolution: waiting" in session.text()
        assert "Actual FPS: waiting" in session.text()
        session.submit(TimedFrame(square_frame(), 0))
        assert session.wait_idle()
        ready = session.snapshot()
        assert ready.phase == "ready"
        assert ready.reference_ready is True
        assert ready.inspection is not None
        assert ready.inspection.zones[0].reason == "calibrated"
        session.submit(TimedFrame(blob(0, 0), 20_000_000))
        assert session.wait_idle()
        active = session.snapshot()
        assert active.inspection is not None
        zone = active.inspection.zones[0]
        assert zone.changed_blocks > 0
        assert zone.reason == "too_short"
        assert zone.accepted is False
    finally:
        session.close()


def test_a_crossing_is_logged_and_the_wrong_direction_keeps_a_reason() -> None:
    crossing = _session(square_settings(), SQUARE, SQUARE)
    try:
        _feed(crossing, (square_frame(), blob(0, 0), blob(20, 0)))
        assert len(crossing.crossings()) == 1
        assert "ZONE 1 DETECTION" in crossing.text()
        assert "reason=accepted" in crossing.text()
    finally:
        crossing.close()

    rejected = _session(square_settings(), SQUARE, SQUARE)
    try:
        _feed(rejected, (square_frame(), blob(20, 0), blob(0, 0)))
        assert rejected.crossings() == ()
        assert "reason=wrong_direction" in rejected.text()
    finally:
        rejected.close()


def test_small_changes_are_below_size_and_old_samples_expire() -> None:
    found = square_detector()
    found.set_inspection(True)
    assert found.observe(blob(0, 0, 10, 10), 1) == ()
    inspection = found.last_inspection()
    assert inspection is not None
    assert inspection.zones[0].reason == "below_size"
    assert inspection.zones[0].accepted is False
    assert found.trace() is None

    moving = square_detector()
    moving.set_inspection(True)
    assert moving.observe(blob(0, 0), 0) == ()
    assert moving.observe(blob(0, 0), 10 * _NS) == ()
    later = moving.last_inspection()
    assert later is not None
    assert later.zones[0].samples_expired == 1
    assert later.zones[0].reason == "too_short"


def test_peaks_record_the_highest_activity_when_no_event_is_emitted() -> None:
    session = _session(square_settings(), SQUARE, SQUARE)
    try:
        _feed(session, (square_frame(), blob(0, 0), blob(0, 0), square_frame()))
        summaries = session.activities()
        assert len(summaries) == 1
        summary = summaries[0]
        assert summary.detection_event is False
        assert summary.direction_confirmed is False
        assert summary.reason == "below_shift"
        assert summary.peak_changed_blocks > 0
        assert summary.peak_difference > 0
        assert "ACTIVITY_END" in session.text()
        assert "peak_changed_blocks=" in session.text()
    finally:
        session.close()


def test_the_log_header_quotes_the_real_configuration_and_caps_its_size() -> None:
    configured = settings(1, 2)
    config = _config_for(configured, WIDTH, HEIGHT, fps=17)
    from slot_racing.modules.timing_camera.diagnostic import DiagnosticConfig

    assert isinstance(config, DiagnosticConfig)
    header = format_header(config)
    assert "App version: test-version" in header
    assert "Device index: 2" in header
    assert f"Requested resolution: {WIDTH} x {HEIGHT}" in header
    assert "Requested FPS: 17" in header
    assert "Actual FPS: waiting" in header
    assert "Actual resolution: waiting" in header
    profile = sensitivity_profile(configured.sensitivity)
    assert f"Difference threshold: {profile.difference:.2f}" in header
    assert "Zone 1:" in header
    assert "Zone 2:" in header
    assert f"Lane: {configured.zones[0].lane}" in header
    assert "Logitech" not in header
    assert "1920 x 1080" not in header
    session = _session(configured, WIDTH, HEIGHT, max_lines=len(header.splitlines()) + 8)
    try:
        _feed(session, (blank(), column(2, LINE_X), blank()))
        text = session.text()
        assert session.truncated()
        assert text.rstrip().endswith("[diagnostic log truncated]")
        assert "Z1 " in text
        assert "Z2 " in text
        assert "Actual FPS: 30" not in text.split("FRAME", maxsplit=1)[0]
    finally:
        session.close()


def test_two_zones_are_measured_separately() -> None:
    session = _session(settings(1, 2), WIDTH, HEIGHT)
    try:
        _feed(session, (blank(), column(2, LINE_X)))
        inspection = session.snapshot().inspection
        assert inspection is not None
        assert [zone.lane for zone in inspection.zones] == [1, 2]
        assert inspection.zones[0].changed_blocks == 0
        assert inspection.zones[1].changed_blocks > 0
        assert "lane=1" in session.text()
        assert "lane=2" in session.text()
    finally:
        session.close()


def test_submit_skips_unread_frames_and_does_not_detect_on_the_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    caller = threading.get_ident()
    seen: list[int] = []
    original = create_lane_detector

    def wrapped(configured: object, background: object = None) -> LaneCrossingDetector:
        from slot_racing.modules.timing_camera.detection import DetectorSettings
        from slot_racing.modules.timing_camera.frames import GrayFrame as Frame

        assert isinstance(configured, DetectorSettings)
        assert background is None or isinstance(background, Frame)
        built = original(configured, background if isinstance(background, Frame) else None)
        observe = built.observe

        def blocked(frame: object, timestamp_ns: int) -> object:
            from slot_racing.modules.timing_camera.frames import GrayFrame as Delivered

            assert isinstance(frame, Delivered)
            seen.append(threading.get_ident())
            entered.set()
            assert release.wait(2)
            return observe(frame, timestamp_ns)

        built.observe = blocked  # type: ignore[assignment]
        return built

    monkeypatch.setattr(diagnostic_module, "create_lane_detector", wrapped)
    session = _session(settings(1), WIDTH, HEIGHT)
    session.start()
    try:
        session.submit(TimedFrame(blank(), 0))
        assert entered.wait(2)
        started = time.perf_counter()
        for index in range(5):
            session.submit(TimedFrame(blank(), (index + 1) * 10))
        elapsed = time.perf_counter() - started
        assert elapsed < 0.5
        release.set()
        assert session.wait_idle()
    finally:
        release.set()
        session.close()
    assert seen
    assert seen[0] != caller
    performance = session.snapshot().performance
    assert performance.frames_skipped >= 1
    assert performance.frames_analyzed + performance.frames_skipped == performance.frames_submitted


def test_reading_the_snapshot_does_not_change_the_crossings() -> None:
    configured = square_settings()
    session = _session(configured, SQUARE, SQUARE)
    pictures = (square_frame(), blob(0, 0), blob(20, 0))
    session.start()
    try:
        for index, picture in enumerate(pictures):
            session.submit(TimedFrame(picture, index * 20_000_000))
            assert session.wait_idle()
            session.snapshot()
            session.text()
            got = [
                (item.timestamp_ns, item.lane, item.foreground_pixels)
                for item in session.crossings()
            ]
            assert got == _replay(configured, pictures[: index + 1])
    finally:
        session.close()


def test_analysis_time_and_capture_drops_are_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    ticks = iter((0, 4_000_000, 4_000_000, 7_000_000))
    monkeypatch.setattr(time, "perf_counter_ns", lambda: next(ticks))
    session = _session(settings(1), WIDTH, HEIGHT)
    session.start()
    try:
        session.submit(TimedFrame(blank(), 0), CaptureCounters(captured=10, dropped=2))
        assert session.wait_idle()
        session.submit(TimedFrame(blank(), 40_000_000), CaptureCounters(captured=12, dropped=3))
        assert session.wait_idle()
        performance = session.snapshot().performance
        assert performance.last_analysis_ns == 3_000_000
        assert performance.maximum_analysis_ns == 4_000_000
        assert performance.average_analysis_ns == 3_500_000
        assert performance.capture_dropped == 3
        assert performance.source_overwrites == 3
        assert performance.frames_captured == 12
        assert performance.camera_fps is not None
        assert performance.analysis_fps is not None
        session.stop()
        assert "average_analysis_ms=3.500" in session.text()
        assert "source_overwrites=3" in session.text()
        assert "capture_dropped=" not in session.text()
    finally:
        session.close()


def test_closing_the_dialog_stops_the_worker(qtbot: QtBot) -> None:
    page, opener = open_page(qtbot, CameraConfigurationStore(database()))
    page.diagnostic.click()
    dialog = page.findChild(DetectionDiagnosticDialog)
    assert isinstance(dialog, DetectionDiagnosticDialog)
    assert page.zones.count() == 0
    dialog.start_diagnosis()
    assert dialog._session is None
    assert "Erkennungszone" in dialog.status.text()
    assert len(opener.opened) == 1
    dialog.close()
    assert page._diagnostic_listener is None


def _square_zone() -> object:
    from slot_racing.modules.timing_camera.detection import DetectionZone, DetectorSettings

    return DetectorSettings(
        (DetectionZone(POSITION, 1, DetectionRoi(0, 0, SQUARE, SQUARE)),),
        block_size=20,
    )


def square_settings() -> object:
    return _square_zone()


def _replay(configured: object, pictures: tuple[GrayFrame, ...]) -> list[tuple[int, int, int]]:
    from slot_racing.modules.timing_camera.detection import DetectorSettings

    assert isinstance(configured, DetectorSettings)
    found = create_lane_detector(configured)
    found_crossings: list[tuple[int, int, int]] = []
    for index, picture in enumerate(pictures):
        for crossing in found.observe(picture, index * 20_000_000):
            found_crossings.append(
                (crossing.timestamp_ns, crossing.lane, crossing.foreground_pixels)
            )
    return found_crossings


def _clip(value: float) -> int:
    rounded = round(value)
    return min(255, max(0, rounded))


def _block(picture: object, row: int, col: int, scale: int) -> int:
    from slot_racing.modules.timing_camera.diagnostic import DiagnosticPicture

    assert isinstance(picture, DiagnosticPicture)
    offset = (row * picture.width + col) * scale
    return picture.pixels[offset]


def _block_values(picture: object, row: int, col: int, scale: int) -> list[int]:
    from slot_racing.modules.timing_camera.diagnostic import DiagnosticPicture

    assert isinstance(picture, DiagnosticPicture)
    values: list[int] = []
    for y in range(row * scale, (row + 1) * scale):
        start = y * picture.width + col * scale
        values.extend(picture.pixels[start : start + scale])
    return values


def test_the_setup_page_names_the_diagnostic_button(qtbot: QtBot) -> None:
    page, _opener = open_page(qtbot, CameraConfigurationStore(database()))
    assert page.diagnostic.text() == "Erkennungsdiagnose"
    assert translator().translate("camera.diagnostic.stop") == "Diagnose stoppen"


def _feed_at(
    session: DiagnosticSession, pictures: tuple[GrayFrame, ...], stamps: tuple[int, ...]
) -> None:
    session.start()
    for picture, stamp in zip(pictures, stamps, strict=True):
        session.submit(TimedFrame(picture, stamp))
        assert session.wait_idle()


def test_direction_reason_codes_name_one_sample_without_guessing_a_direction() -> None:
    from slot_racing.modules.timing_camera.diagnostic_store import (
        direction_reason_code,
        percentile_nearest,
    )

    assert (
        direction_reason_code(
            detected=False,
            max_samples=1,
            samples_expired=0,
            below_size_frames=0,
            frames=1,
            algorithm_reason="too_few_direction_samples",
        )
        == "single_sample_one_frame"
    )
    assert (
        direction_reason_code(
            detected=False,
            max_samples=1,
            samples_expired=1,
            below_size_frames=0,
            frames=2,
            algorithm_reason="too_few_direction_samples",
        )
        == "direction_window_expired"
    )
    assert (
        direction_reason_code(
            detected=False,
            max_samples=1,
            samples_expired=0,
            below_size_frames=2,
            frames=3,
            algorithm_reason="below_component_size",
        )
        == "single_sample_below_size"
    )
    assert (
        direction_reason_code(
            detected=False,
            max_samples=1,
            samples_expired=0,
            below_size_frames=0,
            frames=2,
            algorithm_reason="wrong_direction",
            wrong_direction_frames=1,
        )
        == "wrong_direction"
    )
    assert percentile_nearest([10, 20, 30, 40], 95) == 40
    assert percentile_nearest([], 95) is None


def test_a_long_recording_keeps_every_attempt_when_frame_detail_is_capped(tmp_path: Path) -> None:
    path = tmp_path / "diagnostic.jsonl"
    session = _session(
        settings(1),
        WIDTH,
        HEIGHT,
        max_lines=200,
        journal_path=path,
        max_frame_records=50,
    )
    picture = blank()
    session.start()
    try:
        for index in range(10_000):
            session.submit(TimedFrame(picture, index * 40_000_000, sequence=index + 1))
            assert session.wait_idle()
        session.stop()
        report = session.report()
        assert report is not None
        assert report.frames_analyzed == 10_000
        assert report.frames_submitted == 10_000
        assert report.frames_skipped == 0
        assert report.text_log_truncated is True
        assert report.events_complete is True
        assert report.frame_detail_complete is False
        assert report.frame_records_kept == 50
        assert report.events_lost == 0
        assert report.write_errors == 0
        assert report.dt_min_ns == 40_000_000
        assert report.dt_max_ns == 40_000_000
        assert report.dt_average_ns == 40_000_000
        assert report.analysis_min_ns is not None
        assert report.analysis_p95_ns is not None
        assert report.analysis_max_ns >= report.analysis_min_ns
        assert session.text().rstrip().endswith("[diagnostic log truncated]")
        exported = session.export_text()
        assert "events_complete=true" in exported
        assert "frame_detail_complete=false" in exported
        assert "text_log_truncated=true" in exported
        assert "frames_analyzed=10000" in exported
        assert "Das Textprotokoll wurde gekürzt" in exported
        assert "Ereignisse und Abschlussstatistik decken die ganze Aufzeichnung ab." in exported
        assert '"kind":"frame"' not in exported
        detailed = session.export_text(include_frames=True)
        assert detailed.count('"kind":"frame"') == 50
        assert path.is_file()
        assert '"kind":"summary"' in path.read_text(encoding="utf-8")
    finally:
        session.close()


def test_one_motion_is_one_rejection_and_a_single_sample_names_its_cause(tmp_path: Path) -> None:
    path = tmp_path / "attempts.jsonl"
    single = _session(square_settings(), SQUARE, SQUARE, journal_path=path)
    try:
        _feed_at(
            single,
            (square_frame(), blob(0, 0), square_frame()),
            (0, 20_000_000, 40_000_000),
        )
        single.stop()
        report = single.report()
        assert report is not None
        assert len(single.activities()) == 1
        summary = single.activities()[0]
        assert summary.lane == 1
        assert summary.detection_event is False
        assert summary.direction_samples == 1
        assert summary.direction_reason == "single_sample_one_frame"
        assert summary.reason == "too_few_direction_samples"
        assert report.zones[0].lane == 1
        assert report.zones[0].rejected == 1
        assert report.zones[0].confirmed == 0
        assert report.zones[0].single_sample == 1
        assert dict(report.zones[0].reasons)["single_sample_one_frame"] == 1
        exported = single.export_text()
        assert '"kind":"attempt"' in exported
        assert "single_sample_one_frame" in exported
        assert '"result":"rejected"' in exported
        assert exported.count('"kind":"attempt"') == 1
    finally:
        single.close()

    repeated = _session(square_settings(), SQUARE, SQUARE, journal_path=tmp_path / "repeat.jsonl")
    try:
        _feed_at(
            repeated,
            (square_frame(), blob(0, 0), blob(0, 0), blob(0, 0), square_frame()),
            (0, 20_000_000, 40_000_000, 60_000_000, 80_000_000),
        )
        repeated.stop()
        report = repeated.report()
        assert report is not None
        assert len(repeated.activities()) == 1
        assert report.zones[0].rejected == 1
        assert report.zones[0].confirmed == 0
        assert repeated.activities()[0].direction_reason == "below_shift"
        assert "reason=below_shift" in repeated.text()
    finally:
        repeated.close()


def test_a_direction_window_expiry_is_recorded_on_the_closed_attempt(tmp_path: Path) -> None:
    session = _session(square_settings(), SQUARE, SQUARE, journal_path=tmp_path / "window.jsonl")
    try:
        _feed_at(
            session,
            (square_frame(), blob(0, 0), blob(0, 0), square_frame()),
            (0, 20_000_000, 10 * _NS, 10 * _NS + 20_000_000),
        )
        session.stop()
        summary = session.activities()[0]
        assert summary.direction_samples == 1
        assert summary.direction_reason == "direction_window_expired"
        assert len(session.activities()) == 1
        report = session.report()
        assert report is not None
        assert report.zones[0].single_sample == 1
        assert dict(report.zones[0].reasons)["direction_window_expired"] == 1
        exported = session.export_text()
        assert "direction_window_expired" in exported
        assert '"samples_discarded":' in exported
        assert '"samples_valid":1' in exported
    finally:
        session.close()


def test_small_follow_up_frames_are_named_when_they_add_no_second_sample(tmp_path: Path) -> None:
    session = _session(square_settings(), SQUARE, SQUARE, journal_path=tmp_path / "size.jsonl")
    try:
        _feed_at(
            session,
            (square_frame(), blob(0, 0), blob(0, 0, 10, 10), square_frame()),
            (0, 20_000_000, 40_000_000, 60_000_000),
        )
        session.stop()
        summary = session.activities()[0]
        assert summary.direction_reason == "single_sample_below_size"
        assert summary.direction_samples == 1
        report = session.report()
        assert report is not None
        assert report.zones[0].rejected == 1
        assert report.zones[0].single_sample == 1
    finally:
        session.close()


def test_confirmed_crossings_stay_separate_from_rejections_and_reference_laps(
    tmp_path: Path,
) -> None:
    session = _session(square_settings(), SQUARE, SQUARE, journal_path=tmp_path / "laps.jsonl")
    try:
        session.set_reference_laps(1, 100)
        session.set_reference_laps(2, 0)
        _feed_at(
            session,
            (square_frame(), blob(0, 0), blob(20, 0), square_frame()),
            (0, 20_000_000, 40_000_000, 60_000_000),
        )
        assert len(session.crossings()) == 1
        session.stop()
        report = session.report()
        assert report is not None
        assert report.zones[0].confirmed == 1
        assert report.zones[0].rejected == 0
        assert session.activities()[0].direction_reason == "accepted"
        assert "reason=accepted" in session.text()
        lane_1 = report.references[0]
        lane_2 = report.references[1]
        assert lane_1.lane == 1
        assert lane_1.reference_laps == 100
        assert lane_1.confirmed_events == 1
        assert lane_1.difference == -99
        assert lane_1.ratio == 0.01
        assert lane_2.reference_laps == 0
        assert lane_2.ratio is None
        exported = session.export_text()
        assert "reference_laps=100" in exported
        assert "confirmed_events=1" in exported
        assert "difference=-99" in exported
        assert "ratio=0.010" in exported
        assert "ratio=n/a" in exported
        assert "keine Erkennungsquote" in exported
        assert len(session.crossings()) == 1
    finally:
        session.close()


def test_attempts_keep_the_zone_that_produced_them(tmp_path: Path) -> None:
    session = _session(settings(1, 2), WIDTH, HEIGHT, journal_path=tmp_path / "zones.jsonl")
    try:
        _feed(session, (blank(), column(2, LINE_X), blank()))
        session.stop()
        report = session.report()
        assert report is not None
        assert report.zones
        assert {zone.lane for zone in report.zones} == {2}
        assert all(activity.lane == 2 for activity in session.activities())
        exported = session.export_text()
        assert '"lane":2' in exported
        assert '"lane":1' not in exported
    finally:
        session.close()


def test_two_confirmed_crossings_record_the_gap_between_them(tmp_path: Path) -> None:
    session = _session(settings(1), WIDTH, HEIGHT, journal_path=tmp_path / "gaps.jsonl")
    try:
        _feed_at(
            session,
            (
                blank(),
                column(1, LINE_X),
                column(1, LINE_X + 2),
                blank(),
                column(1, LINE_X),
                column(1, LINE_X + 2),
                blank(),
            ),
            (0, 100, 200, 240, 300, 400, 440),
        )
        session.stop()
        report = session.report()
        assert report is not None
        zone = report.zones[0]
        assert zone.lane == 1
        assert zone.confirmed == 2
        assert len(zone.confirmed_gaps_ns) == 1
        assert zone.confirmed_gaps_ns[0] > 0
        assert "confirmed_gaps_ms" in session.export_text()
    finally:
        session.close()


def test_an_unwritable_journal_is_visible_and_detection_still_runs(tmp_path: Path) -> None:
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("blocked", encoding="utf-8")
    session = _session(
        square_settings(),
        SQUARE,
        SQUARE,
        journal_path=blocked / "diagnostic.jsonl",
    )
    try:
        _feed_at(session, (square_frame(), blob(0, 0), square_frame()), (0, 20_000_000, 40_000_000))
        assert len(session.activities()) == 1
        session.stop()
        report = session.report()
        assert report is not None
        assert report.journal_opened is False
        assert report.events_complete is False
        assert report.write_errors >= 1
        assert report.events_lost >= 1
        assert "FRAME 1" in session.text()
        exported = session.export_text()
        assert "events_complete=false" in exported
        assert "Ereignisaufzeichnung unvollständig" in exported
        assert "frame_detail_complete=false" in exported
    finally:
        session.close()


def test_stopping_flushes_the_summary_and_a_new_session_starts_clean(tmp_path: Path) -> None:
    path = tmp_path / "again.jsonl"
    session = _session(square_settings(), SQUARE, SQUARE, journal_path=path)
    try:
        _feed(session, (square_frame(),))
        session.stop()
        first = session.report()
        assert first is not None
        assert first.frames_analyzed == 1
        assert '"kind":"summary"' in path.read_text(encoding="utf-8")
        session.start()
        session.submit(TimedFrame(square_frame(), 5_000_000))
        assert session.wait_idle()
        session.stop()
        second = session.report()
        assert second is not None
        assert second.frames_analyzed == 1
        assert "t_ns=0\n" not in session.text()
        assert session.text().count("FRAME ") == 1
    finally:
        session.close()


def test_a_full_queue_drops_frames_before_attempts(tmp_path: Path) -> None:
    from slot_racing.modules.timing_camera.diagnostic_store import DiagnosticJournal

    journal = DiagnosticJournal(tmp_path / "queue.jsonl", max_frame_records=100)
    journal.open()
    journal._stop_writer()
    for index in range(2048 - 256):
        journal._queue.put_nowait({"kind": "pad", "seq": index})
    journal.offer("frame", {"n": 1})
    journal.offer("attempt", {"lane": 1})
    assert journal.frame_detail_limited is True
    assert journal.events_lost == 0
    kinds = [item.get("kind") for item in list(journal._queue.queue)]
    assert "attempt" in kinds
    assert "frame" not in kinds
    journal.finish("summary", {"ok": True})


def test_disabled_direction_check_is_named_in_the_closing_report(tmp_path: Path) -> None:
    from slot_racing.modules.timing_camera.detection import DetectionZone, DetectorSettings
    from slot_racing.modules.timing_camera.geometry import DetectionRoi

    configured = DetectorSettings(
        (
            DetectionZone(
                POSITION,
                1,
                DetectionRoi(0, 0, SQUARE, SQUARE),
                check_direction=False,
            ),
        ),
        block_size=20,
    )
    session = _session(configured, SQUARE, SQUARE, journal_path=tmp_path / "open.jsonl")
    try:
        _feed_at(
            session,
            (square_frame(), blob(0, 0), blob(20, 0), blob(0, 0), square_frame()),
            (0, 20_000_000, 40_000_000, 60_000_000, 80_000_000),
        )
        assert len(session.crossings()) == 1
        session.stop()
        report = session.report()
        assert report is not None
        assert report.direction_check == "disabled"
        assert report.zone_checks == ((POSITION, 1, False),)
        assert len(session.activities()) == 1
        summary = session.activities()[0]
        assert summary.detection_event is True
        assert summary.direction_reason == "accepted_without_direction_check"
        assert report.zones[0].confirmed == 1
        assert report.zones[0].rejected == 0
        assert report.zones[0].direction_check_enabled is False
        assert report.zones[0].mean_samples == 1
        exported = session.export_text()
        assert "direction_check=disabled" in exported
        assert "accepted_without_direction_check" in exported
        assert "keine Erkennungsquote" in exported
        assert "Sperrzeit ist nicht gesetzt" in exported
        summary_path, report_path, events_path = session.write_export(tmp_path / "bundle.txt")
        assert "events_complete=true" in summary_path.read_text(encoding="utf-8")
        assert '"direction_check": "disabled"' in report_path.read_text(encoding="utf-8")
        events = events_path.read_text(encoding="utf-8")
        assert '"kind":"attempt"' in events
        assert '"kind":"summary"' in events
        assert '"direction_check_enabled":false' in events
        assert events.count('"kind":"attempt"') == 1
    finally:
        session.close()


def test_write_export_accepts_a_string_and_a_path(tmp_path: Path) -> None:
    session = _session(square_settings(), SQUARE, SQUARE, journal_path=tmp_path / "live.jsonl")
    try:
        _feed(session, (square_frame(), blob(0, 0)))
        session.stop()
        journal = session.journal_path()
        assert journal is not None and journal.is_file()
        journal_before = journal.read_text(encoding="utf-8")
        assert '"kind":"summary"' in journal_before
        assert '"kind":"frame"' in journal_before

        from_text = session.write_export(str(tmp_path / "from-text.txt"))
        from_path = session.write_export(tmp_path / "from-path")
        for summary_path, report_path, events_path in (from_text, from_path):
            summary = summary_path.read_text(encoding="utf-8")
            assert summary_path.suffix == ".txt"
            assert "=== Diagnoseabschluss ===" in summary
            assert "events_complete=" in summary
            assert f"journal_path={journal}" in summary
            report = report_path.read_text(encoding="utf-8")
            assert '"events_complete"' in report
            assert '"journal_path"' in report
            events = events_path.read_text(encoding="utf-8")
            assert '"kind":"summary"' in events
            assert '"kind":"session"' in events
            assert '"kind":"frame"' not in events
        assert journal.read_text(encoding="utf-8") == journal_before
        with_frames = session.write_export(tmp_path / "frames.txt", include_frames=True)
        assert '"kind":"frame"' in with_frames[2].read_text(encoding="utf-8")
        assert journal.read_text(encoding="utf-8") == journal_before
        assert not list(tmp_path.glob(".*.export-tmp"))
    finally:
        session.close()


def test_write_export_rejects_a_button_flag_and_an_empty_stem(tmp_path: Path) -> None:
    session = _session(square_settings(), SQUARE, SQUARE, journal_path=tmp_path / "kept.jsonl")
    try:
        _feed(session, (square_frame(),))
        session.stop()
        journal = session.journal_path()
        assert journal is not None
        before = journal.read_bytes()
        with pytest.raises(TypeError, match="str or Path"):
            session.write_export(False)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="empty"):
            session.write_export("   ")
        with pytest.raises(FileNotFoundError):
            session.write_export(tmp_path / "missing" / "bundle.txt")
        blocker = tmp_path / "not-a-directory"
        blocker.write_text("blocked", encoding="utf-8")
        with pytest.raises(OSError):
            session.write_export(blocker / "bundle.txt")
        assert journal.read_bytes() == before
        assert session.text()
        assert not (tmp_path / "missing").exists()
    finally:
        session.close()


def test_a_failed_export_keeps_the_journal_and_can_be_repeated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(square_settings(), SQUARE, SQUARE, journal_path=tmp_path / "again.jsonl")
    try:
        _feed(session, (square_frame(),))
        session.stop()
        journal = session.journal_path()
        assert journal is not None
        before = journal.read_bytes()
        first = session.write_export(tmp_path / "bundle.txt")
        saved = tuple(path.read_bytes() for path in first)
        real_write = Path.write_text

        def fail_json(
            self: Path,
            data: str,
            encoding: str | None = None,
            errors: str | None = None,
            newline: str | None = None,
        ) -> int:
            if self.name.endswith(".json.export-tmp"):
                raise OSError("disk full")
            return real_write(self, data, encoding=encoding, errors=errors, newline=newline)

        monkeypatch.setattr(Path, "write_text", fail_json)
        with pytest.raises(OSError, match="disk full"):
            session.write_export(tmp_path / "bundle.txt")
        assert journal.read_bytes() == before
        assert tuple(path.read_bytes() for path in first) == saved
        assert not list(tmp_path.glob(".*.export-tmp"))
        monkeypatch.undo()
        again = session.write_export(str(tmp_path / "bundle.txt"))
        assert again[0].read_bytes() == saved[0]
        assert '"kind":"summary"' in again[2].read_text(encoding="utf-8")
        assert journal.read_bytes() == before
    finally:
        session.close()


def test_export_button_uses_the_dialog_string_and_reports_a_failed_write(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored = database()
    CameraConfigurationStore(stored).save(saved_configuration())
    page, _opener = open_page(qtbot, CameraConfigurationStore(stored))
    page._timer.stop()
    page.diagnostic.click()
    dialog = page.findChild(DetectionDiagnosticDialog)
    assert isinstance(dialog, DetectionDiagnosticDialog)
    dialog.start_diagnosis()
    page._pull_frame()
    assert dialog._session is not None
    assert dialog._session.wait_idle()
    dialog.stop_diagnosis()
    session = dialog._session
    journal = session.journal_path()
    assert journal is not None and journal.is_file()
    journal_before = journal.read_bytes()
    assert b'"kind":"summary"' in journal_before
    text_before = session.text()
    chosen = tmp_path / "clicked.txt"

    def choose_string(*_args: object, **_kwargs: object) -> tuple[str, str]:
        return str(chosen), "Text (*.txt)"

    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose_string)
    dialog.save_button.click()
    assert chosen.is_file()
    assert chosen.with_suffix(".json").is_file()
    assert '"kind":"summary"' in chosen.with_suffix(".jsonl").read_text(encoding="utf-8")
    assert "Zusammenfassung, Ereignisse und Statistik gespeichert." in dialog.status.text()
    assert journal.read_bytes() == journal_before
    assert session.text() == text_before

    def choose_missing(*_args: object, **_kwargs: object) -> tuple[str, str]:
        return str(tmp_path / "nowhere" / "bundle.txt"), "Text (*.txt)"

    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose_missing)
    dialog.save_button.click()
    assert "Export fehlgeschlagen" in dialog.status.text()
    assert str(journal) in dialog.status.text()
    assert "erneuter Export" in dialog.status.text()
    assert journal.read_bytes() == journal_before
    assert session.text() == text_before
    assert not (tmp_path / "nowhere").exists()

    retried = tmp_path / "retried.txt"

    def choose_retry(*_args: object, **_kwargs: object) -> tuple[str, str]:
        return str(retried), "Text (*.txt)"

    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose_retry)
    written = dialog.save_diagnosis(False)  # type: ignore[arg-type]
    assert written is not None
    assert retried.is_file()
    assert '"kind":"summary"' in written[2].read_text(encoding="utf-8")
    assert journal.read_bytes() == journal_before
    dialog.close()
