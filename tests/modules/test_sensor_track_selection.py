"""Track choice on the sensor page: every track stays listed, and the map is editable."""

from __future__ import annotations

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QGroupBox,
    QLabel,
    QPushButton,
    QScrollArea,
    QWidget,
)
from pytestqt.qtbot import QtBot

from slot_racing.app.main_window import MainWindow
from slot_racing.app.runtime import Runtime
from slot_racing.core.catalog import TrackCatalog, TrackInfo
from slot_racing.core.config import AppConfig
from slot_racing.core.domain import TimingSetup, TrackId, default_timing_setup
from slot_racing.core.timing import TimingSetupService
from slot_racing.modules.timing.service import TimingSetupManager
from slot_racing.modules.timing_sensor.configuration import SensorAssignment
from slot_racing.modules.timing_sensor.link import SensorLink, TransportMode
from slot_racing.modules.timing_sensor.protocol import format_hello
from slot_racing.modules.timing_sensor.store import SensorConfigurationStore
from slot_racing.modules.timing_sensor.transport import SerialSensorTransport
from slot_racing.modules.timing_sensor.ui.page import SensorTimingPage
from slot_racing.modules.tracks.service import TrackInput, TrackService
from slot_racing.modules.tracks.ui.tracks_area import TracksArea
from slot_racing.modules.tracks.ui.tracks_page import TrackDialog
from tests.database import migrated_database
from tests.modules.test_sensor_timing import _choose, _translator
from tests.modules.test_sensor_usb import _HELLO, _Clock, _Stream


class _MutableTracks(TrackCatalog):
    def __init__(self) -> None:
        self.tracks = [
            TrackInfo(TrackId(1), "Oval", None, 2, True),
            TrackInfo(TrackId(2), "Gerade", None, 4, False),
        ]

    def list_tracks(self, *, active_only: bool = False) -> list[TrackInfo]:
        if active_only:
            return [track for track in self.tracks if track.is_active]
        return list(self.tracks)

    def get_track(self, track_id: TrackId) -> TrackInfo | None:
        for track in self.tracks:
            if int(track.id) == int(track_id):
                return track
        return None


class _PartialSetups(TimingSetupService):
    """Track 1 has a stored setup. Track 2 has none. Further tracks follow ``extra``."""

    def __init__(self) -> None:
        self.extra: dict[int, TimingSetup] = {}

    def get_setup(self, track_id: TrackId) -> TimingSetup | None:
        if int(track_id) == 1:
            return default_timing_setup()
        return self.extra.get(int(track_id))

    def save_setup(self, track_id: TrackId, setup: TimingSetup) -> None:
        self.extra[int(track_id)] = setup

    def clear_setup(self, track_id: TrackId) -> None:
        self.extra.pop(int(track_id), None)


def test_tracks_with_and_without_timing_stay_selectable_and_keep_assignments(
    qtbot: QtBot,
) -> None:
    database = migrated_database()
    store = SensorConfigurationStore(database)
    store.save_track(
        1,
        (
            SensorAssignment(physical_id="s1", position_id="start_finish", lane=1),
            SensorAssignment(physical_id="gate-extra", position_id="sector_1", lane=1),
        ),
    )
    catalog = _MutableTracks()
    setups = _PartialSetups()
    page = SensorTimingPage(_translator(), SensorLink(), store, setups, catalog)
    qtbot.addWidget(page)
    page.resize(480, 360)
    page.show()
    QApplication.processEvents()

    names = _names(page)
    assert names == ["Oval", "Gerade (inaktiv)"]
    assert page.findChild(QLabel, "sensor-name-s1") is not None
    assert page.findChild(QLabel, "sensor-name-s2") is not None
    assert page.findChild(QLabel, "sensor-name-gate-extra") is not None
    assert _current(page, "sensor-position-s1") == "start_finish"
    assert _current(page, "sensor-lane-s1") == 1
    start = page.findChild(QComboBox, "sensor-position-s1")
    assert start is not None and "Start/Ziel" in _items(start)

    page.track_combo.setCurrentIndex(1)
    QApplication.processEvents()
    assert "Gerade" in page.setup_hint.text()
    assert "Start/Ziel" in page.setup_hint.text()
    timing = page.findChild(QPushButton, "sensor-open-timing")
    assert timing is not None and timing.isVisible()
    assert page.findChild(QComboBox, "sensor-position-s1") is not None
    assert _items(page.findChild(QComboBox, "sensor-position-s1")) == ["Messpunkt wählen"]

    catalog.tracks.append(TrackInfo(TrackId(3), "Neu", None, 3, True))
    setups.extra[3] = default_timing_setup()
    page.refresh()
    assert _names(page) == ["Oval", "Gerade (inaktiv)", "Neu"]
    assert page.track_combo.currentData() == 2

    page.track_combo.setCurrentIndex(page.track_combo.findData(1))
    _choose(page, "sensor-position-s2", "start_finish")
    _choose(page, "sensor-lane-s2", 2)
    _choose(page, "sensor-position-gate-extra", "sector_1")
    save = page.findChild(QPushButton, "sensor-save")
    assert save is not None
    save.click()
    QApplication.processEvents()
    stored = store.track_assignments(1)
    assert [(item.physical_id, item.position_id, item.lane) for item in stored] == [
        ("s1", "start_finish", 1),
        ("s2", "start_finish", 2),
        ("gate-extra", "sector_1", 1),
    ]
    assert "Für Sensor s2 ist kein Messpunkt gewählt." not in page.prerequisites.text()

    log_bottom = page.log.mapTo(page, page.log.rect().bottomLeft())
    assert page.log.isVisible()
    assert page.log.height() >= 100
    assert log_bottom.y() <= page.height()
    scroll = page.findChild(QScrollArea, "sensor-controls-scroll")
    assert scroll is not None
    assert scroll.verticalScrollBar().maximum() > 0
    developer = page.findChild(QGroupBox, "sensor-developer")
    body = page.findChild(QWidget, "sensor-developer-body")
    assert developer is not None and body is not None
    assert developer.isChecked() is False
    assert body.isVisible() is False


def test_a_handshake_starts_clock_sync_without_pressing_the_button(qtbot: QtBot) -> None:
    stream = _Stream()
    transport = SerialSensorTransport("COM7", stream=stream, clock=_Clock(), sync_gap_ns=0)
    link = SensorLink(transport, mode=TransportMode.SERIAL)
    page = SensorTimingPage(
        _translator(),
        link,
        SensorConfigurationStore(migrated_database()),
        _PartialSetups(),
        _MutableTracks(),
    )
    qtbot.addWidget(page)
    page.show()
    QApplication.processEvents()
    stream.push((format_hello(_HELLO) + "\n").encode("ascii"))
    transport.pump()
    page.refresh()
    assert page.identity_label.text().startswith("mega2560")
    assert "gestartet" in page.status.text()
    transport.pump()
    assert b"PSF/1 SYNC 1" in bytes(stream.outgoing)


def test_the_shell_lists_tracks_created_before_and_while_the_page_is_open(qtbot: QtBot) -> None:
    database = migrated_database()
    service = TrackService(database)
    ready = service.create_track(TrackInput(name="Oval", lane_count=2))
    bare = service.create_track(TrackInput(name="Gerade", lane_count=4))
    TimingSetupManager(database).save_setup(ready.id, default_timing_setup())
    SensorConfigurationStore(database).save_track(
        int(ready.id),
        (SensorAssignment(physical_id="s1", position_id="start_finish", lane=2),),
    )
    runtime = Runtime.create(
        AppConfig(plugin_overrides={"timing_sensor": True}),
        database=database,
    )
    try:
        window = MainWindow(runtime)
        qtbot.addWidget(window)
        window.resize(640, 420)
        window.show()
        QApplication.processEvents()
        window.select("timing")
        page = window.current_page()
        assert isinstance(page, SensorTimingPage)
        assert _names(page) == ["Gerade", "Oval"]
        page.track_combo.setCurrentIndex(page.track_combo.findText("Gerade"))
        QApplication.processEvents()
        assert "Gerade" in page.setup_hint.text()
        assert "Arduino" not in page.setup_hint.text()
        assert "COM" not in page.setup_hint.text()
        dialog = TrackDialog(runtime.translator, service, None)
        qtbot.addWidget(dialog)
        dialog_text = " ".join(label.text() for label in dialog.findChildren(QLabel))
        assert "Arduino" not in dialog_text
        assert "COM" not in dialog_text
        assert " s1" not in dialog_text
        open_timing = page.findChild(QPushButton, "sensor-open-timing")
        assert open_timing is not None
        open_timing.click()
        QApplication.processEvents()
        assert window.current_id() == "tracks"
        area = window.current_page()
        assert isinstance(area, TracksArea)
        assert area.current_page() is area.config_view
        assert area.config_view.track is not None
        assert int(area.config_view.track.id) == int(bare.id)

        window.select("timing")
        service.create_track(TrackInput(name="Neu", lane_count=3))
        page.refresh()
        QApplication.processEvents()
        assert "Neu" in _names(page)

        page.track_combo.setCurrentIndex(page.track_combo.findText("Oval"))
        QApplication.processEvents()
        assert _current(page, "sensor-position-s1") == "start_finish"
        assert _current(page, "sensor-lane-s1") == 2
        assert page.findChild(QLabel, "sensor-name-s2") is not None
        _choose(page, "sensor-position-s2", "sector_1")
        _choose(page, "sensor-lane-s2", 1)
        save = page.findChild(QPushButton, "sensor-save")
        assert save is not None
        save.click()
        QApplication.processEvents()
        stored = SensorConfigurationStore(database).track_assignments(int(ready.id))
        assert [(item.physical_id, item.position_id, item.lane) for item in stored] == [
            ("s1", "start_finish", 2),
            ("s2", "sector_1", 1),
        ]
        log_bottom = page.log.mapTo(window, QPoint(0, page.log.height()))
        assert page.log.isVisible()
        assert page.log.height() >= 80
        assert log_bottom.y() <= window.height()
    finally:
        runtime.shutdown()


def _names(page: SensorTimingPage) -> list[str]:
    return [page.track_combo.itemText(index) for index in range(page.track_combo.count())]


def _items(combo: QComboBox | None) -> list[str]:
    assert combo is not None
    return [combo.itemText(index) for index in range(combo.count())]


def _current(page: SensorTimingPage, name: str) -> object:
    combo = page.findChild(QComboBox, name)
    assert combo is not None
    return combo.currentData()
