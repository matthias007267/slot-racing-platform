"""Persistent sensor settings and USB device recognition.

No test opens a serial port or assumes a port named COM5.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from PySide6.QtWidgets import QApplication, QGroupBox
from pytestqt.qtbot import QtBot
from sqlalchemy.orm.attributes import flag_modified

from slot_racing.core.storage import Setting
from slot_racing.modules.timing_sensor.clock_map import SyncStatus
from slot_racing.modules.timing_sensor.configuration import DeviceHint, SensorAssignment
from slot_racing.modules.timing_sensor.devices import (
    PortMatch,
    SerialPortFacts,
    attach_remembered_serial,
    classify_port,
)
from slot_racing.modules.timing_sensor.link import SensorLink, TransportMode
from slot_racing.modules.timing_sensor.store import (
    SENSOR_CONFIGURATION_KEY,
    SensorConfigurationError,
    SensorConfigurationStore,
)
from slot_racing.modules.timing_sensor.transport import SerialSensorTransport
from slot_racing.modules.timing_sensor.ui.page import SensorTimingPage
from tests.database import migrated_database
from tests.modules.test_sensor_timing import _click, _Setups, _Tracks, _translator
from tests.modules.test_sensor_usb import _Clock, _identify, _Stream, _sync

_BOARD = SerialPortFacts("COM11", vid=0x2341, pid=0x0042, serial_number="BOARD1", location="1-1.2")
_OTHER = SerialPortFacts("COM5", vid=0x2341, pid=0x0042, serial_number="BOARD2", location="1-1.3")


def test_version_one_documents_load_and_saving_keeps_the_new_fields() -> None:
    database = migrated_database()
    store = SensorConfigurationStore(database)
    with database.session() as session:
        session.add(
            Setting(
                key=SENSOR_CONFIGURATION_KEY,
                value={"schema_version": 1, "tracks": []},
            )
        )
    loaded = store.load()
    assert loaded.schema_version == 1
    assert loaded.mode == "simulation"
    assert loaded.developer_expanded is False
    assert loaded.device == DeviceHint()
    store.save_preferences(
        mode="serial",
        port="COM11",
        developer_expanded=True,
        developer_offset_ns=-20,
        developer_numerator=2,
        developer_denominator=3,
        device=DeviceHint(serial_number="BOARD1", vid=0x2341, pid=0x0042, port="COM11"),
    )
    store.save_track(1, (SensorAssignment(physical_id="s1", position_id="start_finish", lane=1),))
    kept = store.load()
    assert kept.schema_version == 2
    assert kept.mode == "serial"
    assert kept.port == "COM11"
    assert kept.developer_expanded is True
    assert kept.developer_offset_ns == -20
    assert kept.developer_numerator == 2
    assert kept.developer_denominator == 3
    assert kept.device.serial_number == "BOARD1"
    assert kept.for_track(1)[0].physical_id == "s1"
    with pytest.raises(SensorConfigurationError):
        store.save_preferences(developer_denominator=0)
    with pytest.raises(SensorConfigurationError):
        store.save_preferences(mode="usb")
    with pytest.raises(SensorConfigurationError):
        store.save_preferences(developer_offset_ns="0")
    unchanged = store.load()
    assert unchanged.developer_denominator == 3
    assert unchanged.mode == "serial"
    with database.session() as session:
        row = session.get(Setting, SENSOR_CONFIGURATION_KEY)
        assert row is not None
        row.value = {"schema_version": 3, "tracks": []}
        flag_modified(row, "value")
    with pytest.raises(SensorConfigurationError):
        store.load()


def test_device_recognition_uses_stable_facts_and_does_not_guess() -> None:
    hint = DeviceHint(serial_number="BOARD1", vid=0x2341, pid=0x0042, port="COM5")
    status, match = classify_port(hint, (_OTHER, _BOARD))
    assert status is PortMatch.UNIQUE
    assert match is not None and match.device == "COM11"
    ambiguous, guessed = classify_port(
        DeviceHint(vid=0x2341, pid=0x0042),
        (_BOARD, _OTHER),
    )
    assert ambiguous is PortMatch.AMBIGUOUS
    assert guessed is None
    with pytest.raises(ValidationError):
        DeviceHint(port="Arduino Mega")
    weak, port = classify_port(DeviceHint(port="COM11"), (_BOARD,))
    assert weak is PortMatch.UNIQUE
    assert port is not None and port.device == "COM11"
    missing, none = classify_port(DeviceHint(port="COM5"), (_BOARD,))
    assert missing is PortMatch.ABSENT
    assert none is None
    contradicted, rejected = classify_port(
        DeviceHint(port="COM11", vid=0x1111),
        (_BOARD,),
    )
    assert contradicted is PortMatch.ABSENT
    assert rejected is None
    empty, fallback = classify_port(DeviceHint(), (_OTHER, _BOARD))
    assert empty is PortMatch.ABSENT
    assert fallback is None
    assert fallback != _OTHER


def test_a_unique_device_is_opened_only_after_the_protocol_hello() -> None:
    stream = _Stream()
    transport = SerialSensorTransport("COM11", stream=stream, clock=_Clock(), sync_gap_ns=0)
    link = SensorLink()
    store = SensorConfigurationStore(migrated_database())
    store.save_preferences(
        mode="serial",
        port="COM5",
        developer_offset_ns=0,
        developer_numerator=1,
        developer_denominator=1,
        device=DeviceHint(serial_number="BOARD1", vid=0x2341, pid=0x0042, port="COM5"),
    )
    opened = attach_remembered_serial(
        link,
        store.load(),
        (_BOARD, _OTHER),
        hold=True,
        open_port=lambda port: transport if port == "COM11" else SerialSensorTransport(port),
    )
    assert opened is not None and opened.device == "COM11"
    assert link.connected is False
    assert link.sync is SyncStatus.UNSYNCHRONIZED
    _identify(link, transport, stream)
    assert link.identity is not None and link.identity.session_id == "b0001"
    _sync(link, transport)
    mapping = link._state.mapping
    assert link.mapping_origin == "measured"
    status_after_sync: SyncStatus = link.sync
    assert status_after_sync is SyncStatus.SYNCHRONIZED
    assert mapping is not None and mapping.offset_ns != 0
    assert (mapping.offset_ns, mapping.numerator, mapping.denominator) != (0, 1, 1)
    link.release()


def test_an_open_port_is_not_replaced_when_the_page_is_shown(qtbot: QtBot) -> None:
    stream = _Stream()
    transport = SerialSensorTransport("COM11", stream=stream, clock=_Clock(), sync_gap_ns=0)
    link = SensorLink()
    store = SensorConfigurationStore(migrated_database())
    store.save_preferences(
        mode="serial",
        port="COM5",
        device=DeviceHint(serial_number="BOARD1", vid=0x2341, pid=0x0042, port="COM5"),
    )
    opened = attach_remembered_serial(
        link, store.load(), (_BOARD,), hold=True, open_port=lambda _port: transport
    )
    assert opened is not None
    _identify(link, transport, stream)
    _sync(link, transport)
    mapping = link._state.mapping
    assert mapping is not None and link.mapping_origin == "measured"

    def refuse(port: str) -> SerialSensorTransport:
        raise AssertionError(port)

    page = SensorTimingPage(
        _translator(),
        link,
        store,
        _Setups(),
        _Tracks(),
        ports=lambda: (_BOARD,),
        serial_transport=refuse,
    )
    qtbot.addWidget(page)
    page.show()
    QApplication.processEvents()
    assert link._state.mapping == mapping
    assert link.mapping_origin == "measured"
    assert stream.closed is False
    assert page.port_combo.findData("COM11") >= 0
    page.port_combo.setCurrentIndex(page.port_combo.findData("COM11"))
    _click(page, "sensor-connect")
    assert page.status.text() == "Der USB-Leser ist bereits verbunden."
    assert link._state.mapping == mapping
    page.hide()
    assert page._timer.isActive() is True
    assert stream.closed is False
    link.release()


def test_ambiguous_devices_are_not_opened() -> None:
    link = SensorLink()
    store = SensorConfigurationStore(migrated_database())
    store.save_preferences(
        mode="serial",
        device=DeviceHint(vid=0x2341, pid=0x0042),
    )

    def refuse(port: str) -> SerialSensorTransport:
        raise AssertionError(port)

    assert attach_remembered_serial(link, store.load(), (_BOARD, _OTHER), open_port=refuse) is None
    assert link.connected is False
    assert link.mode is TransportMode.SIMULATION


def test_the_page_restores_developer_settings_and_rejects_invalid_text(qtbot: QtBot) -> None:
    store = SensorConfigurationStore(migrated_database())
    store.save_preferences(
        developer_expanded=True,
        developer_offset_ns=-5,
        developer_numerator=2,
        developer_denominator=3,
        track_id=1,
    )
    page = _page(qtbot, store)
    page.show()
    QApplication.processEvents()
    developer = page.findChild(QGroupBox, "sensor-developer")
    assert developer is not None and developer.isChecked() is True
    assert page.offset_edit.text() == "-5"
    assert page.numerator_edit.text() == "2"
    assert page.denominator_edit.text() == "3"
    assert page.track_combo.currentData() == 1
    page.offset_edit.setText("abc")
    _click(page, "sensor-map-apply")
    assert store.load().developer_offset_ns == -5
    assert page.sync_label.text() == "Keine Zeitzuordnung"
    page.offset_edit.setText("40")
    _click(page, "sensor-map-apply")
    assert store.load().developer_offset_ns == 40
    assert page.sync_label.text() == "Synchronisiert"
    restored = _page(qtbot, store)
    restored.show()
    QApplication.processEvents()
    assert restored.offset_edit.text() == "40"
    box = restored.findChild(QGroupBox, "sensor-developer")
    assert box is not None and box.isChecked() is True
    payload = restored.diagnostic_payload()
    assert payload["protocol"] == "PSF/1"
    assert payload["race"] is None
    text = json.dumps(payload)
    assert "Driver" not in text
    assert "Übernommen" not in text


def _page(qtbot: QtBot, store: SensorConfigurationStore) -> SensorTimingPage:
    page = SensorTimingPage(_translator(), SensorLink(), store, _Setups(), _Tracks())
    qtbot.addWidget(page)
    return page
