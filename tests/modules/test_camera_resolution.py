"""Detection resolution is separate from sensitivity, and small zones are rated.

The pictures are synthetic. They check area and pixel-shift normalisation, the
one-frame limit, zone quality after scaling, and the setup control. They do not
retune the motion test that already passes at 20 px.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from pytestqt.qtbot import QtBot

from slot_racing.core.domain import TimingLayout, TimingSensor, TimingSetup
from slot_racing.core.timing import TimingSessionSpec
from slot_racing.modules.timing_camera.configuration import (
    CameraConfiguration,
    NormalizedRoi,
    StoredCamera,
    StoredDetection,
    StoredDetectionZone,
    roi_to_pixels,
)
from slot_racing.modules.timing_camera.detection import (
    REFERENCE_BLOCK_PX,
    RESOLUTION_PRESETS,
    DetectionZone,
    DetectorSettings,
    LaneCrossingDetector,
    TravelDirection,
    ZoneRating,
    evaluate_detection_zone,
    minimum_component_area_px,
    required_blocks_for_area,
    resolution_preset_name,
    sensitivity_profile,
    shift_threshold_blocks,
)
from slot_racing.modules.timing_camera.diagnostic import (
    DiagnosticPerformance,
    DiagnosticSession,
    DiagnosticSnapshot,
    DiagnosticView,
    diagnostic_config,
    format_header,
    render_zone,
)
from slot_racing.modules.timing_camera.frame_source import ManualFrameSource, TimedFrame
from slot_racing.modules.timing_camera.frames import GrayFrame
from slot_racing.modules.timing_camera.geometry import DetectionRoi
from slot_racing.modules.timing_camera.provider import CameraTimingFactory, CameraTimingProvider
from slot_racing.modules.timing_camera.store import CameraConfigurationStore
from slot_racing.modules.timing_camera.ui.diagnostic_dialog import (
    DetectionDiagnosticDialog,
    _fit_zone,
)
from tests.modules.test_camera_configuration import database
from tests.modules.test_camera_setup_ui import open_page, translator

WIDTH = 240
HEIGHT = 120
CAR = 255
POSITION = "start_finish"


def _frame(value: int = 0) -> GrayFrame:
    return GrayFrame.blank(WIDTH, HEIGHT, value)


def _car(x: int, *, width: int = 40, height: int = 40, value: int = CAR) -> GrayFrame:
    return _frame().paint(DetectionRoi(x, 40, width, height), value)


def _detector(block_size: int, sensitivity: int = 100) -> LaneCrossingDetector:
    settings = DetectorSettings(
        (DetectionZone(POSITION, 1, DetectionRoi(0, 0, WIDTH, HEIGHT)),),
        block_size=block_size,
        sensitivity=sensitivity,
        direction=TravelDirection.LEFT_TO_RIGHT,
    )
    found = LaneCrossingDetector(settings, background=_frame())
    found.set_inspection(True)
    return found


def _spec() -> TimingSessionSpec:
    setup = TimingSetup(
        TimingLayout.from_position_ids([POSITION]),
        (TimingSensor("sensor-sf", POSITION),),
    )
    return TimingSessionSpec(setup, (1,), 1)


def test_resolution_presets_and_old_documents_stay_at_20_px() -> None:
    assert [size for _name, size in RESOLUTION_PRESETS] == [20, 15, 10, 5]
    assert resolution_preset_name(20) == "coarse"
    assert resolution_preset_name(15) == "medium"
    assert resolution_preset_name(10) == "fine"
    assert resolution_preset_name(5) == "very_fine"
    assert resolution_preset_name(12) == "custom"
    document = {
        "version": 1,
        "camera": {"device_index": 0, "width": 640, "height": 480, "fps": 30},
        "detection": {"zones": []},
    }
    loaded = CameraConfiguration.model_validate(document)
    assert loaded.detection.block_size == 20


def test_higher_sensitivity_is_never_stricter_at_the_same_block_size() -> None:
    for block in (20, 15, 10, 5):
        previous: tuple[float, int, float, int] | None = None
        for level in (0, 50, 100):
            profile = sensitivity_profile(level)
            current = (
                profile.difference,
                required_blocks_for_area(profile.min_blocks, block),
                shift_threshold_blocks(profile.min_shift, block, block),
                profile.window_ns,
            )
            if previous is not None:
                assert current[0] <= previous[0]
                assert current[1] <= previous[1]
                assert current[2] <= previous[2]
                assert current[3] >= previous[3]
            previous = current
    loose = sensitivity_profile(100)
    assert loose.difference == 16
    assert required_blocks_for_area(loose.min_blocks, 20) == 2
    assert required_blocks_for_area(loose.min_blocks, 15) == 4
    assert required_blocks_for_area(loose.min_blocks, 10) == 8
    assert required_blocks_for_area(loose.min_blocks, 5) == 32
    assert shift_threshold_blocks(loose.min_shift, 20, 20) == 0.75
    assert minimum_component_area_px(loose.min_blocks) == (
        2 * REFERENCE_BLOCK_PX * REFERENCE_BLOCK_PX
    )
    assert loose.min_shift * REFERENCE_BLOCK_PX == 15


def test_the_same_pixel_motion_crosses_at_20_10_and_5() -> None:
    for block in (20, 10, 5):
        found = _detector(block)
        assert found.observe(_car(10), 0) == ()
        crossings = found.observe(_car(40), 40_000_000)
        assert len(crossings) == 1
        assert crossings[0].lane == 1
        assert crossings[0].position_id == POSITION


def test_a_short_pixel_step_does_not_become_easier_at_a_finer_grid() -> None:
    """Eight pixels is below the 15 px shift. Finer tiles must not invent it."""
    for block in (20, 10, 5):
        found = _detector(block)
        assert found.observe(_car(0), 0) == ()
        assert found.observe(_car(8), 40_000_000) == ()
        inspection = found.last_inspection()
        assert inspection is not None
        assert inspection.zones[0].accepted is False
        assert inspection.zones[0].reason in {"below_shift", "insufficient_motion", "too_short"}


def test_coarse_tiles_can_hide_a_twelve_pixel_step_that_ten_pixel_tiles_keep() -> None:
    """A 40 px car steps 12 px from the origin.

    At 20 px the centroid moves half a tile, 10 px, which is under the 15 px
    shift. At 10 px the same pixels move 1.5 tiles and the crossing is
    accepted. At 5 px the measured shift is 12.5 px and stays under that same
    pixel distance. A finer grid is not uniformly easier.
    """
    for block, expected in ((20, False), (10, True), (5, False)):
        found = _detector(block)
        assert found.observe(_car(0), 0) == ()
        crossings = found.observe(_car(12), 40_000_000)
        assert (len(crossings) == 1) is expected
        inspection = found.last_inspection()
        assert inspection is not None
        if not expected:
            assert inspection.zones[0].reason == "insufficient_motion"


def test_one_visible_frame_does_not_invent_a_direction() -> None:
    for block in (20, 10, 5):
        found = _detector(block)
        assert found.observe(_car(20), 0) == ()
        assert found.observe(_frame(), 40_000_000) == ()
        inspection = found.last_inspection()
        assert inspection is not None
        assert inspection.zones[0].accepted is False


def test_one_frame_activity_reports_too_few_direction_samples() -> None:
    settings = DetectorSettings(
        (DetectionZone(POSITION, 1, DetectionRoi(0, 0, WIDTH, HEIGHT)),),
        block_size=10,
        sensitivity=100,
    )
    config = diagnostic_config(
        app_version="test",
        started_at="2026-10-07T12:00:00+00:00",
        device_index=0,
        requested_width=WIDTH,
        requested_height=HEIGHT,
        requested_fps=25,
        settings=settings,
    )
    header = format_header(config)
    assert "Detection resolution: fine" in header
    assert "Block size: 10 x 10" in header
    assert "Normalized minimum component area: 800 px" in header
    assert "Minimum shift px: 15.00" in header
    assert "Sensitivity: 100" in header
    session = DiagnosticSession(settings, config)
    try:
        session.start()
        for index, picture in enumerate((_frame(), _car(20), _frame())):
            session.submit(TimedFrame(picture, index * 40_000_000))
            assert session.wait_idle()
        text = session.text()
        assert "activity_frames=" in text
        assert "activity_duration_ms=" in text
        assert "direction_samples=" in text
        assert "reason=too_few_direction_samples" in text
        assert "Zone 1 scaled:" in text
        summaries = session.activities()
        assert len(summaries) == 1
        assert summaries[0].detection_event is False
        assert summaries[0].direction_samples < 2
        assert summaries[0].activity_frames >= 1
    finally:
        session.close()


def test_two_frames_complete_one_crossing_at_each_preset() -> None:
    for block in (20, 10, 5):
        found = _detector(block, sensitivity=100)
        assert found.observe(_frame(), 0) == ()
        assert found.observe(_car(0), 40_000_000) == ()
        crossings = found.observe(_car(30), 80_000_000)
        assert len(crossings) == 1
        assert found.observe(_frame(), 120_000_000) == ()


def test_moderate_noise_does_not_cross_at_5_px() -> None:
    width, height = 80, 40
    zone = DetectionZone(POSITION, 1, DetectionRoi(0, 0, width, height))
    settings = DetectorSettings((zone,), block_size=5, sensitivity=100)

    def noisy(offset: int) -> GrayFrame:
        pixels = bytearray(width * height)
        for index in range(width * height):
            pixels[index] = 100 + ((index * 17 + offset * 13) % 17) - 8
        return GrayFrame(width, height, bytes(pixels))

    found = LaneCrossingDetector(settings, background=noisy(0))
    for step in range(1, 25):
        assert found.observe(noisy(step), step * 40_000_000) == ()


def test_zone_quality_uses_the_scaled_capture_and_the_travel_axis() -> None:
    critical = evaluate_detection_zone(200, 100, 20, TravelDirection.BOTTOM_TO_TOP, 25)
    assert critical.blocks_in_travel_direction == 5
    assert critical.blocks_cross_direction == 10
    assert critical.rating is ZoneRating.CRITICAL
    assert "travel_critical" in critical.warnings
    assert critical.frame_interval_ms == 40
    limited = evaluate_detection_zone(160, 120, 20, TravelDirection.LEFT_TO_RIGHT, None)
    assert limited.blocks_in_travel_direction == 8
    assert limited.rating is ZoneRating.LIMITED
    assert "travel_limited" in limited.warnings
    good = evaluate_detection_zone(240, 120, 20, TravelDirection.LEFT_TO_RIGHT, 25)
    assert good.blocks_in_travel_direction == 12
    assert good.blocks_cross_direction == 6
    assert good.rating is ZoneRating.GOOD
    assert good.warnings == ()
    narrow = evaluate_detection_zone(240, 80, 20, TravelDirection.LEFT_TO_RIGHT, 25)
    assert narrow.blocks_in_travel_direction == 12
    assert narrow.blocks_cross_direction == 4
    assert narrow.rating is ZoneRating.LIMITED
    assert "cross_narrow" in narrow.warnings
    requested = roi_to_pixels(NormalizedRoi(x=0, y=0, width=0.2, height=0.15), 1920, 1080)
    actual = roi_to_pixels(NormalizedRoi(x=0, y=0, width=0.2, height=0.15), 1280, 720)
    on_request = evaluate_detection_zone(
        requested.width, requested.height, 20, TravelDirection.LEFT_TO_RIGHT, 25
    )
    on_capture = evaluate_detection_zone(
        actual.width, actual.height, 20, TravelDirection.LEFT_TO_RIGHT, 25
    )
    assert (actual.width, actual.height) != (requested.width, requested.height)
    assert on_capture.usable_blocks_x == actual.width // 20
    assert on_capture.usable_blocks_y == actual.height // 20
    assert on_capture.blocks_in_travel_direction < on_request.blocks_in_travel_direction


def test_analysis_grid_matches_the_diagnostic_preview() -> None:
    for block in (20, 10, 5):
        found = _detector(block)
        found.observe(_car(10), 1)
        inspection = found.last_inspection()
        assert inspection is not None
        zone = inspection.zones[0]
        rows = len(zone.analysis)
        cols = len(zone.analysis[0])
        assert rows == HEIGHT // block
        assert cols == WIDTH // block
        picture = render_zone(zone, DiagnosticView.ANALYSIS, scale=2)
        assert picture.width == cols * 2
        assert picture.height == rows * 2
        assert picture.zones[0].width == picture.width
        assert picture.zones[0].height == picture.height


def test_a_zone_preview_keeps_its_aspect_ratio() -> None:
    found = _detector(10)
    found.observe(_car(10), 1)
    inspection = found.last_inspection()
    assert inspection is not None
    picture = render_zone(inspection.zones[0], DiagnosticView.ANALYSIS, scale=1)
    fitted = _fit_zone(picture, QSize(300, 170), active=False)
    assert fitted.width() == 300
    assert fitted.height() == 170
    # The grid is wider than it is tall (240/10 by 120/10). The fitted content
    # uses the full width and leaves bars above and below.
    assert picture.width > picture.height
    assert fitted.width() / fitted.height() != picture.width / picture.height


def test_two_diagnostic_zones_are_labelled_beside_each_other(qtbot: QtBot) -> None:
    class Host:
        def diagnostic_document(self) -> CameraConfiguration:
            zone = StoredDetectionZone(
                position_id=POSITION,
                lane=1,
                roi=NormalizedRoi(x=0, y=0, width=0.5, height=1),
            )
            other = StoredDetectionZone(
                position_id="sector_1",
                lane=2,
                roi=NormalizedRoi(x=0.5, y=0, width=0.5, height=1),
            )
            return CameraConfiguration(
                camera=StoredCamera(width=WIDTH, height=HEIGHT, fps=25),
                detection=StoredDetection(zones=(zone, other), block_size=10, sensitivity=100),
            )

        def attach_diagnostic(self, listener: object) -> None:
            return None

        def detach_diagnostic(self, listener: object) -> None:
            return None

    dialog = DetectionDiagnosticDialog(translator(), Host())
    dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    dialog.resize(1180, 760)
    dialog.show()
    qtbot.addWidget(dialog)
    settings = DetectorSettings(
        (
            DetectionZone(POSITION, 1, DetectionRoi(0, 0, WIDTH // 2, HEIGHT)),
            DetectionZone("sector_1", 2, DetectionRoi(WIDTH // 2, 0, WIDTH // 2, HEIGHT)),
        ),
        block_size=10,
        sensitivity=100,
    )
    found = LaneCrossingDetector(settings, background=_frame())
    found.set_inspection(True)
    found.observe(_car(10), 1)
    inspection = found.last_inspection()
    assert inspection is not None
    dialog._config = diagnostic_config(
        app_version="test",
        started_at="2026-10-07T12:00:00+00:00",
        device_index=0,
        requested_width=WIDTH,
        requested_height=HEIGHT,
        requested_fps=25,
        settings=settings,
    )
    dialog._show_picture(
        DiagnosticSnapshot(
            phase="ready",
            reference_ready=True,
            frame_index=1,
            timestamp_ns=1,
            elapsed_ns=0,
            width=WIDTH,
            height=HEIGHT,
            inspection=inspection,
            crossings=(),
            performance=dialog._session.snapshot().performance
            if dialog._session is not None
            else _empty_performance(),
            activities=(),
        )
    )
    visible = [card for card in dialog._cards if card.isVisible()]
    assert len(visible) == 2
    assert visible[1].pos().x() > visible[0].pos().x()
    assert visible[0].caption.text() == "Zone 1 · Spur 1 · start_finish"
    assert visible[1].caption.text() == "Zone 2 · Spur 2 · sector_1"
    assert "Analyseraster:" in visible[0].meta.text()
    assert "Qualität:" in visible[0].meta.text()
    pixmap = visible[0].image.pixmap()
    assert pixmap is not None
    assert pixmap.width() == visible[0].image.width()
    assert pixmap.height() == visible[0].image.height()


def _empty_performance() -> DiagnosticPerformance:
    return DiagnosticPerformance(
        frames_submitted=0,
        frames_analyzed=0,
        frames_skipped=0,
        frames_captured=None,
        capture_dropped=None,
        camera_fps=None,
        analysis_fps=None,
        average_analysis_ns=None,
        maximum_analysis_ns=0,
        last_analysis_ns=0,
        last_dt_ns=None,
    )


def test_the_resolution_slider_is_saved_and_a_custom_size_is_kept(qtbot: QtBot) -> None:
    stored = database()
    CameraConfigurationStore(stored).save(
        CameraConfiguration(
            detection=StoredDetection(
                zones=(
                    StoredDetectionZone(
                        position_id=POSITION,
                        lane=1,
                        roi=NormalizedRoi(x=0, y=0, width=0.1, height=0.2),
                    ),
                ),
                block_size=12,
                sensitivity=40,
            )
        )
    )
    page, _preview = open_page(qtbot, CameraConfigurationStore(stored))
    assert page.resolution_value.text().startswith("12")
    assert "kritisch" in page.zone_hint.text().casefold() or "sehr klein" in page.zone_hint.text()
    # A custom size sits visually on the nearest tick without rewriting the value.
    # Moving away and back is what selects that preset.
    page.detection_resolution.setValue(0)
    page.detection_resolution.setValue(2)
    assert "Fein" in page.resolution_value.text()
    assert "10 × 10" in page.resolution_value.text()  # noqa: RUF001
    page.save.click()
    loaded = CameraConfigurationStore(stored).load()
    assert loaded.detection.block_size == 10
    assert loaded.detection.sensitivity == 40


def test_a_created_source_keeps_its_resolution_if_the_store_changes() -> None:
    stored = database()
    store = CameraConfigurationStore(stored)
    zone = StoredDetectionZone(
        position_id=POSITION,
        lane=1,
        roi=NormalizedRoi(x=0.1, y=0.1, width=0.4, height=0.4),
    )
    store.save(
        CameraConfiguration(
            camera=StoredCamera(width=WIDTH, height=HEIGHT, fps=25),
            detection=StoredDetection(zones=(zone,), block_size=10, sensitivity=100),
        )
    )
    frames = ManualFrameSource()
    source = CameraTimingFactory(frames=frames, configurations=store).create_source(_spec())
    assert isinstance(source, CameraTimingProvider)
    store.save(
        CameraConfiguration(
            camera=StoredCamera(width=WIDTH, height=HEIGHT, fps=25),
            detection=StoredDetection(zones=(zone,), block_size=5, sensitivity=100),
        )
    )
    assert source._settings is not None
    assert source._settings.block_size == 10
    source.start(lambda _event: None)
    try:
        assert source._detector is not None
        source._detector.set_inspection(True)
        frames.submit(_frame(), 1)
        source.poll()
        inspection = source._detector.last_inspection()
        assert inspection is not None
        assert inspection.zones[0].block_size == 10
    finally:
        source.stop()


def test_analysis_of_a_720p_frame_observes_every_frame() -> None:
    width, height = 1280, 720
    settings = DetectorSettings(
        (
            DetectionZone(POSITION, 1, DetectionRoi(400, 260, 240, 160)),
            DetectionZone("sector_1", 2, DetectionRoi(700, 260, 240, 160)),
        ),
        block_size=5,
        sensitivity=100,
    )
    background = GrayFrame(width, height, bytes(width * height))
    found = LaneCrossingDetector(settings, background=background)
    before = found.pixels_compared
    for index in range(1, 21):
        assert found.observe(background, index * 40_000_000) == ()
    assert found.pixels_compared > before
