"""Camera setup page. The preview is a fake, so no device is opened."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from pytestqt.qtbot import QtBot

from slot_racing.core.i18n import Translator
from slot_racing.core.storage import Setting
from slot_racing.modules.timing_camera.camera_config import CameraConfig
from slot_racing.modules.timing_camera.capture import CameraOpenError
from slot_racing.modules.timing_camera.configuration import (
    CameraConfiguration,
    NormalizedRoi,
    StoredCamera,
    StoredDetection,
    StoredDetectionZone,
    roi_to_pixels,
)
from slot_racing.modules.timing_camera.detection import TravelDirection
from slot_racing.modules.timing_camera.frame_source import FrameSource, TimedFrame
from slot_racing.modules.timing_camera.frames import GrayFrame
from slot_racing.modules.timing_camera.geometry import DetectionRoi
from slot_racing.modules.timing_camera.lease import CameraBusyError
from slot_racing.modules.timing_camera.plugin import CameraTimingPlugin
from slot_racing.modules.timing_camera.store import (
    CAMERA_CONFIGURATION_KEY,
    CameraConfigurationError,
    CameraConfigurationStore,
)
from slot_racing.modules.timing_camera.ui.page import CameraSetupPage, zone_limit
from slot_racing.modules.timing_camera.ui.stage import CameraStage
from tests.modules.test_camera_configuration import database

FRAME = (640, 480)


class FakeFrames(FrameSource):
    def __init__(self, frame: GrayFrame) -> None:
        self.frame = frame
        self.running = False
        self.stops = 0

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False
        self.stops += 1

    def poll_frame(self) -> TimedFrame | None:
        if not self.running:
            return None
        return TimedFrame(self.frame, 1)

    def poll_latest(self) -> TimedFrame | None:
        return self.poll_frame()


class FakeOpener:
    def __init__(
        self,
        indices: tuple[int, ...] = (0,),
        error: BaseException | None = None,
    ) -> None:
        self.indices = indices
        self.error = error
        self.opened: list[CameraConfig] = []
        self.sources: list[FakeFrames] = []

    def device_indices(self) -> tuple[int, ...]:
        return self.indices

    def open(self, config: CameraConfig) -> FrameSource:
        self.opened.append(config)
        if self.error is not None:
            raise self.error
        source = FakeFrames(GrayFrame.blank(config.width, config.height, 30))
        source.start()
        self.sources.append(source)
        return source


class RejectingStore:
    def __init__(self, inner: CameraConfigurationStore) -> None:
        self.inner = inner

    def load(self) -> CameraConfiguration:
        return self.inner.load()

    def save(self, configuration: CameraConfiguration) -> None:
        raise CameraConfigurationError("disk full")


def translator() -> Translator:
    result = Translator()
    result.add_catalog("de", CameraTimingPlugin.translations["de"])
    return result


def saved_configuration() -> CameraConfiguration:
    return CameraConfiguration(
        camera=StoredCamera(device_index=1, width=640, height=480, fps=12),
        detection=StoredDetection(
            zones=(
                StoredDetectionZone(
                    position_id="start_finish",
                    lane=2,
                    roi=NormalizedRoi(x=0.1, y=0.25, width=0.2, height=0.1),
                ),
            )
        ),
    )


def open_page(
    qtbot: QtBot,
    store: CameraConfigurationStore | RejectingStore,
    opener: FakeOpener | None = None,
    lane_limit: int | None = None,
) -> tuple[CameraSetupPage, FakeOpener]:
    preview = opener if opener is not None else FakeOpener()
    limit = None if lane_limit is None else (lambda: lane_limit)
    page = CameraSetupPage(translator(), store, preview, lane_limit=limit)
    page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    page.stage.setFixedSize(*FRAME)
    page.show()
    qtbot.addWidget(page)
    qtbot.waitUntil(lambda: page.isVisible() and page.stage.width() == FRAME[0])
    return page, preview


def drag(qtbot: QtBot, stage: CameraStage, x0: int, y0: int, x1: int, y1: int) -> None:
    start = stage.image_to_widget(x0, y0)
    end = stage.image_to_widget(x1, y1)
    assert stage.rect().contains(start)
    assert stage.rect().contains(end)
    qtbot.mousePress(stage, Qt.MouseButton.LeftButton, pos=start)  # type: ignore[no-untyped-call]
    qtbot.mouseMove(stage, pos=end)  # type: ignore[no-untyped-call]
    qtbot.mouseRelease(stage, Qt.MouseButton.LeftButton, pos=end)  # type: ignore[no-untyped-call]


def pixels(page: CameraSetupPage, index: int = 0) -> DetectionRoi:
    return roi_to_pixels(page.stage.zones()[index], *FRAME)


def draw_sample(qtbot: QtBot, page: CameraSetupPage) -> None:
    page.add_zone.click()


def test_the_saved_configuration_and_its_zones_are_shown(qtbot: QtBot) -> None:
    stored = database()
    CameraConfigurationStore(stored).save(saved_configuration())
    page, opener = open_page(qtbot, CameraConfigurationStore(stored), FakeOpener(indices=(0,)))
    assert page.device.currentData() == 1
    assert page.device.currentText() == "Kamera 1"
    assert page.device.count() == 2
    assert page.resolution.currentText() == "640 × 480"  # noqa: RUF001
    assert page.fps.currentData() == 12
    assert page.status.text() == "Status: ● Live"
    assert opener.opened[0].device_index == 1
    assert len(page.stage.zones()) == 1
    assert pixels(page) == DetectionRoi(64, 120, 128, 48)
    assert page.zones.item(0) is not None
    assert page.zones.item(0).text() == "Zone 1: start_finish – Lane 2"  # noqa: RUF001
    assert page.position.text() == "start_finish"


def test_adding_a_zone_assigns_the_next_lane_and_a_draggable_rectangle(qtbot: QtBot) -> None:
    page, _opener = open_page(qtbot, CameraConfigurationStore(database()))
    draw_sample(qtbot, page)
    roi = page.stage.zones()[0]
    assert 0 < roi.width < 1
    assert 0 < roi.height < 1
    assert page.position.text() == "start_finish"
    assert page.zones.item(0) is not None
    assert page.zones.item(0).text() == "Zone 1: start_finish – Lane 1"  # noqa: RUF001
    assert "Änderungen nicht gespeichert" in page.message.text()


def test_a_zone_past_the_lane_count_is_rejected(qtbot: QtBot) -> None:
    page, _opener = open_page(qtbot, CameraConfigurationStore(database()), lane_limit=2)
    page.add_zone.click()
    page.add_zone.click()
    assert len(page.stage.zones()) == 2
    page.add_zone.click()
    assert len(page.stage.zones()) == 2
    assert "keine weiteren Zonen" in page.message.text()


def test_a_zone_can_be_moved_and_stays_inside_the_picture(qtbot: QtBot) -> None:
    page, _opener = open_page(qtbot, CameraConfigurationStore(database()))
    draw_sample(qtbot, page)
    origin = pixels(page)
    center_x = origin.x + origin.width // 2
    center_y = origin.y + origin.height // 2
    drag(qtbot, page.stage, center_x, center_y, center_x + 32, center_y + 16)
    moved = pixels(page)
    assert moved.x == origin.x + 32
    assert moved.y == origin.y + 16
    assert moved.width == origin.width
    assert moved.height == origin.height
    drag(qtbot, page.stage, moved.x + moved.width // 2, moved.y + moved.height // 2, 0, moved.y)
    bounded = pixels(page)
    assert bounded.x == 0
    assert bounded.width == origin.width
    assert bounded.height == origin.height
    assert 0 <= bounded.y <= FRAME[1] - bounded.height


def test_a_zone_can_be_resized_down_to_the_minimum_and_not_past_the_picture(
    qtbot: QtBot,
) -> None:
    page, _opener = open_page(qtbot, CameraConfigurationStore(database()))
    draw_sample(qtbot, page)
    origin = pixels(page)
    drag(
        qtbot,
        page.stage,
        origin.x + origin.width,
        origin.y + origin.height,
        origin.x + origin.width + 40,
        origin.y + origin.height + 24,
    )
    grown = pixels(page)
    assert grown.width > origin.width
    assert grown.height > origin.height
    drag(qtbot, page.stage, grown.x + grown.width, grown.y + grown.height, grown.x + 2, grown.y + 2)
    shrunk = pixels(page)
    assert shrunk.width >= 8
    assert shrunk.height >= 8
    assert shrunk.x >= 0 and shrunk.y >= 0
    drag(qtbot, page.stage, shrunk.x + shrunk.width, shrunk.y + shrunk.height, 639, 479)
    inside = pixels(page)
    assert inside.x >= 0 and inside.y >= 0
    assert inside.x + inside.width <= FRAME[0]
    assert inside.y + inside.height <= FRAME[1]
    assert inside.width >= 8 and inside.height >= 8


def test_position_can_be_renamed_and_the_lane_stays_automatic(qtbot: QtBot) -> None:
    page, _opener = open_page(qtbot, CameraConfigurationStore(database()))
    draw_sample(qtbot, page)
    page.position.setText("sector_1")
    assert page.zones.item(0) is not None
    assert page.zones.item(0).text() == "Zone 1: sector_1 – Lane 1"  # noqa: RUF001


def test_deleting_a_zone_is_kept_only_after_save(qtbot: QtBot) -> None:
    stored = database()
    CameraConfigurationStore(stored).save(saved_configuration())
    page, _opener = open_page(qtbot, CameraConfigurationStore(stored))
    page.delete_zone.click()
    assert page.stage.zones() == ()
    assert len(CameraConfigurationStore(stored).load().detection.zones) == 1
    page.save.click()
    assert "Konfiguration gespeichert." in page.message.text()
    assert CameraConfigurationStore(stored).load().detection.zones == ()
    reopened, _preview = open_page(qtbot, CameraConfigurationStore(stored))
    assert reopened.stage.zones() == ()


def test_25_fps_can_be_chosen_and_is_saved(qtbot: QtBot) -> None:
    stored = database()
    page, opener = open_page(qtbot, CameraConfigurationStore(stored), FakeOpener(indices=(0, 1)))
    rates = [page.fps.itemData(index) for index in range(page.fps.count())]
    assert rates == [15, 25, 30, 60]
    draw_sample(qtbot, page)
    page.fps.setCurrentIndex(page.fps.findData(25))
    assert opener.opened[-1].fps == 25
    page.save.click()
    assert "Konfiguration gespeichert." in page.message.text()
    assert CameraConfigurationStore(stored).load().camera.fps == 25
    again, _preview = open_page(qtbot, CameraConfigurationStore(stored), FakeOpener(indices=(0, 1)))
    assert again.fps.currentData() == 25
    assert [again.fps.itemData(index) for index in range(again.fps.count())] == [15, 25, 30, 60]


def test_save_reloads_from_a_new_store(qtbot: QtBot) -> None:
    stored = database()
    page, _opener = open_page(qtbot, CameraConfigurationStore(stored), FakeOpener(indices=(0, 1)))
    draw_sample(qtbot, page)
    page.device.setCurrentIndex(1)
    page.fps.setCurrentIndex(page.fps.findData(30))
    page.save.click()
    assert "Konfiguration gespeichert." in page.message.text()
    loaded = CameraConfigurationStore(stored).load()
    assert loaded.camera.device_index == 1
    assert loaded.camera.fps == 30
    assert loaded.camera.width == 640
    assert loaded.camera.height == 480
    zone = loaded.detection.zones[0]
    assert zone.position_id == "start_finish"
    assert zone.lane == 1
    assert zone.roi == page.stage.zones()[0]
    again, _preview = open_page(qtbot, CameraConfigurationStore(stored), FakeOpener(indices=(0, 1)))
    assert again.device.currentData() == 1
    assert again.position.text() == "start_finish"
    assert pixels(again) == roi_to_pixels(zone.roi, *FRAME)


def test_direction_check_is_per_zone_and_older_pages_keep_it_enabled(qtbot: QtBot) -> None:
    stored = database()
    CameraConfigurationStore(stored).save(saved_configuration())
    page, _opener = open_page(qtbot, CameraConfigurationStore(stored))
    assert page.check_direction.text() == "Fahrtrichtung prüfen"
    assert page.check_direction.isChecked()
    assert page.direction.isEnabled()
    page.check_direction.setChecked(False)
    assert not page.direction.isEnabled()
    page.save.click()
    loaded = CameraConfigurationStore(stored).load()
    assert loaded.detection.zones[0].check_direction is False
    assert loaded.detection.direction is TravelDirection.LEFT_TO_RIGHT
    again, _preview = open_page(qtbot, CameraConfigurationStore(stored))
    assert not again.check_direction.isChecked()
    assert not again.direction.isEnabled()


def test_direction_and_sensitivity_are_saved_and_block_size_is_kept(qtbot: QtBot) -> None:
    stored = database()
    base = saved_configuration()
    CameraConfigurationStore(stored).save(
        base.model_copy(
            update={
                "detection": StoredDetection(
                    zones=base.detection.zones,
                    block_size=12,
                    sensitivity=20,
                    direction=TravelDirection.BOTTOM_TO_TOP,
                )
            }
        )
    )
    page, _opener = open_page(qtbot, CameraConfigurationStore(stored))
    assert page.direction.currentData() == TravelDirection.BOTTOM_TO_TOP.value
    assert "↑" in page.direction.currentText()
    assert "↓" in page.direction.itemText(0)
    assert page.sensitivity.value() == 20
    assert page.sensitivity_less.text() == "weniger"
    assert page.sensitivity_more.text() == "mehr"
    page.direction.setCurrentIndex(page.direction.findData(TravelDirection.RIGHT_TO_LEFT.value))
    page.sensitivity.setValue(80)
    page.save.click()
    loaded = CameraConfigurationStore(stored).load()
    assert loaded.detection.block_size == 12
    assert loaded.detection.sensitivity == 80
    assert loaded.detection.direction is TravelDirection.RIGHT_TO_LEFT
    assert "←" in page.direction.currentText()


def test_cancel_restores_the_saved_configuration(qtbot: QtBot) -> None:
    stored = database()
    CameraConfigurationStore(stored).save(saved_configuration())
    page, _opener = open_page(qtbot, CameraConfigurationStore(stored))
    page.position.setText("sector_9")
    page.cancel.click()
    assert page.position.text() == "start_finish"
    assert "Änderungen nicht gespeichert" not in page.message.text()
    fresh, _preview = open_page(qtbot, CameraConfigurationStore(stored))
    assert fresh.position.text() == "start_finish"
    assert fresh.zones.item(0) is not None
    assert "Lane 2" in fresh.zones.item(0).text()
    assert pixels(fresh) == DetectionRoi(64, 120, 128, 48)


def test_a_missing_camera_keeps_the_saved_zones(qtbot: QtBot) -> None:
    stored = database()
    CameraConfigurationStore(stored).save(saved_configuration())
    opener = FakeOpener(indices=(), error=CameraOpenError("missing"))
    page, _preview = open_page(qtbot, CameraConfigurationStore(stored), opener)
    assert "Kamera nicht verfügbar" in page.status.text()
    assert "nicht geöffnet" in page.message.text()
    assert pixels(page) == DetectionRoi(64, 120, 128, 48)
    assert page.device.currentData() == 1
    assert CameraConfigurationStore(stored).load() == saved_configuration()


def test_a_camera_used_by_a_race_is_not_opened_for_the_preview(qtbot: QtBot) -> None:
    stored = database()
    CameraConfigurationStore(stored).save(saved_configuration())
    opener = FakeOpener(error=CameraBusyError("race"))
    page, _preview = open_page(qtbot, CameraConfigurationStore(stored), opener)
    assert "Rennen" in page.message.text()
    assert pixels(page) == DetectionRoi(64, 120, 128, 48)
    assert opener.sources == []


def test_a_broken_document_is_shown_and_not_overwritten(qtbot: QtBot) -> None:
    stored = database()
    with stored.session() as session:
        session.add(Setting(key=CAMERA_CONFIGURATION_KEY, value={"version": 99}))
    page, _opener = open_page(qtbot, CameraConfigurationStore(stored))
    assert "nicht gelesen" in page.message.text()
    assert page.device.currentData() == 0
    assert page.stage.zones() == ()
    with stored.session() as session:
        row = session.get(Setting, CAMERA_CONFIGURATION_KEY)
        assert row is not None
        assert row.value == {"version": 99}
    page.cancel.click()
    assert "nicht gelesen" in page.message.text()
    with pytest.raises(CameraConfigurationError):
        CameraConfigurationStore(stored).load()


def test_an_incomplete_zone_is_not_saved(qtbot: QtBot) -> None:
    stored = database()
    page, _opener = open_page(qtbot, CameraConfigurationStore(stored))
    draw_sample(qtbot, page)
    page.position.clear()
    page.save.click()
    assert "Position" in page.message.text()
    assert CameraConfigurationStore(stored).load().detection.zones == ()


def test_overlapping_zones_can_be_saved(qtbot: QtBot) -> None:
    stored = database()
    page, _opener = open_page(qtbot, CameraConfigurationStore(stored))
    draw_sample(qtbot, page)
    page.add_zone.click()
    page.zones.setCurrentRow(1)
    first_box = pixels(page, 0)
    second_box = pixels(page, 1)
    drag(
        qtbot,
        page.stage,
        second_box.x + second_box.width // 2,
        second_box.y + second_box.height // 2,
        first_box.x + first_box.width // 2,
        first_box.y + first_box.height // 2,
    )
    page.position.setText("sector_1")
    page.save.click()
    loaded = CameraConfigurationStore(stored).load()
    assert len(loaded.detection.zones) == 2
    first, second = loaded.detection.zones
    assert first.roi.x < second.roi.x + second.roi.width
    assert second.roi.x < first.roi.x + first.roi.width
    assert first.roi.y < second.roi.y + second.roi.height
    assert second.roi.y < first.roi.y + first.roi.height


def test_a_failing_save_leaves_the_stored_document_unchanged(qtbot: QtBot) -> None:
    stored = database()
    CameraConfigurationStore(stored).save(saved_configuration())
    page, _opener = open_page(qtbot, RejectingStore(CameraConfigurationStore(stored)))
    page.position.setText("sector_1")
    page.save.click()
    assert "konnte nicht gespeichert" in page.message.text()
    assert CameraConfigurationStore(stored).load() == saved_configuration()


def test_refresh_reopens_the_camera_without_dropping_zones(qtbot: QtBot) -> None:
    stored = database()
    CameraConfigurationStore(stored).save(saved_configuration())
    page, opener = open_page(qtbot, CameraConfigurationStore(stored))
    assert len(opener.sources) == 1
    page.refresh.click()
    assert len(page.stage.zones()) == 1
    assert len(opener.sources) == 2
    assert opener.sources[0].stops == 1
    assert opener.sources[1].running


def test_leaving_the_page_stops_the_preview_and_returning_opens_it_again(qtbot: QtBot) -> None:
    page, opener = open_page(qtbot, CameraConfigurationStore(database()))
    draw_sample(qtbot, page)
    page.hide()
    assert opener.sources[0].stops == 1
    assert not opener.sources[0].running
    page.show()
    assert len(opener.sources) == 2
    assert opener.sources[1].running
    assert len(page.stage.zones()) == 1
    assert page.position.text() == "start_finish"


def test_zones_follow_the_track_lane_count_and_keep_a_saved_lane(qtbot: QtBot) -> None:
    stored = database()
    CameraConfigurationStore(stored).save(saved_configuration())
    page, _opener = open_page(qtbot, CameraConfigurationStore(stored), lane_limit=4)
    assert page.zones.item(0) is not None
    assert "Lane 2" in page.zones.item(0).text()
    saved_roi = page.stage.zones()[0]
    page.add_zone.click()
    page.add_zone.click()
    page.add_zone.click()
    assert [index + 1 for index in range(page.zones.count())]
    labels = [page.zones.item(index).text() for index in range(page.zones.count())]
    assert labels[0].endswith("Lane 2")
    assert "Lane 1" in labels[1]
    assert "Lane 3" in labels[2]
    assert "Lane 4" in labels[3]
    assert page.stage.zones()[0] == saved_roi
    page.add_zone.click()
    assert page.zones.count() == 4
    assert "keine weiteren Zonen" in page.message.text()


def test_zone_limit_follows_the_widest_track_and_never_exceeds_four() -> None:
    assert zone_limit(()) == 2
    assert zone_limit((2,)) == 2
    assert zone_limit((2, 3)) == 3
    assert zone_limit((4,)) == 4
    assert zone_limit((6, 2)) == 4
    assert zone_limit((1,)) == 2


def test_a_three_lane_track_accepts_three_zones(qtbot: QtBot) -> None:
    page, _opener = open_page(qtbot, CameraConfigurationStore(database()), lane_limit=3)
    for _ in range(3):
        page.add_zone.click()
    assert [text[-1] for text in (page.zones.item(i).text() for i in range(3))] == ["1", "2", "3"]
    page.add_zone.click()
    assert page.zones.count() == 3


def test_image_points_round_trip_through_the_stage(qtbot: QtBot) -> None:
    stage = CameraStage()
    stage.setFixedSize(*FRAME)
    qtbot.addWidget(stage)
    assert stage.image_to_widget(64, 120) == QPoint(64, 120)
    assert stage.image_to_widget(192, 168) == QPoint(192, 168)


def _two_lane_configuration() -> CameraConfiguration:
    return CameraConfiguration(
        camera=StoredCamera(device_index=0, width=640, height=480, fps=30),
        detection=StoredDetection(
            zones=(
                StoredDetectionZone(
                    position_id="start_finish",
                    lane=1,
                    roi=NormalizedRoi(x=0.1, y=0.1, width=0.2, height=0.2),
                ),
                StoredDetectionZone(
                    position_id="sector_1",
                    lane=2,
                    roi=NormalizedRoi(x=0.6, y=0.6, width=0.2, height=0.2),
                ),
            )
        ),
    )


def test_a_detection_flashes_only_that_zone_and_then_clears(qtbot: QtBot) -> None:
    """Preview keeps the picture and does not run lane detection on it.

    The stage can still flash one zone on its own. That timer is not fed by the
    preview, so a car in the picture leaves every zone dark.
    """
    stored = database()
    CameraConfigurationStore(stored).save(_two_lane_configuration())
    page, opener = open_page(qtbot, CameraConfigurationStore(stored))
    assert page.stage.highlighted() == ()
    source = opener.sources[-1]
    lane_one = roi_to_pixels(page.stage.zones()[0], *FRAME)
    source.frame = GrayFrame.blank(*FRAME, 30).paint(lane_one, 255)
    page._pull_frame()
    page._pull_frame()
    assert page.stage.highlighted() == ()
    assert not hasattr(page, "_detector")
    page.stage.highlight(0, duration_ms=40)
    assert page.stage.highlighted() == (0,)
    page._timer.stop()
    qtbot.waitUntil(lambda: page.stage.highlighted() == (), timeout=1000)
    assert page.stage.highlighted() == ()


def test_highlighting_one_zone_leaves_the_other_and_expires(qtbot: QtBot) -> None:
    page, _opener = open_page(qtbot, CameraConfigurationStore(database()))
    page.add_zone.click()
    page.add_zone.click()
    page.stage.highlight(0, duration_ms=40)
    assert page.stage.highlighted() == (0,)
    page._timer.stop()
    qtbot.waitUntil(lambda: page.stage.highlighted() == (), timeout=1000)
    page.stage.highlight(1, duration_ms=60_000)
    assert page.stage.highlighted() == (1,)
