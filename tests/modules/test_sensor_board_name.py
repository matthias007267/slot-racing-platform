"""A sensor board is chosen by the name of its USB interface, not by COM number."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from slot_racing.modules.timing_sensor.devices import (
    SerialPortFacts,
    assign_board_name,
    board_name_for,
    list_serial_devices,
    port_choice_label,
    usb_interface_label,
)
from slot_racing.modules.timing_sensor.link import SensorLink, TransportMode
from slot_racing.modules.timing_sensor.store import SensorConfigurationStore
from slot_racing.modules.timing_sensor.transport import SensorTransport, SerialSensorTransport
from slot_racing.modules.timing_sensor.ui.page import SensorTimingPage
from tests.database import migrated_database
from tests.modules.test_sensor_timing import _choose, _click, _Setups, _Tracks, _translator
from tests.modules.test_sensor_usb import _Clock, _Stream

_LEFT = SerialPortFacts(
    "COM5",
    vid=0x1A86,
    pid=0x7523,
    serial_number="LEFT",
    location="1-1",
    product="USB-SERIAL CH340",
)
_RIGHT = SerialPortFacts(
    "COM6",
    vid=0x1A86,
    pid=0x7523,
    serial_number="RIGHT",
    location="1-2",
    product="USB-SERIAL CH340",
)


def test_a_board_name_follows_the_usb_identity_when_the_com_port_changes() -> None:
    ports = (_LEFT, _RIGHT)
    named = assign_board_name((), _LEFT, "  Start-Ziel  ", ports)
    assert port_choice_label(_LEFT, named, ports) == "Start-Ziel"
    assert port_choice_label(_RIGHT, named, ports) == "USB-SERIAL CH340 · RIGHT"
    assert "COM5" not in port_choice_label(_LEFT, named, ports)
    moved = SerialPortFacts(
        "COM11",
        vid=0x1A86,
        pid=0x7523,
        serial_number="LEFT",
        location="2-1",
        product="USB-SERIAL CH340",
    )
    later = (moved, _RIGHT)
    assert board_name_for(moved, named, later) == "Start-Ziel"
    assert port_choice_label(moved, named, later) == "Start-Ziel"
    with pytest.raises(ValueError, match="taken"):
        assign_board_name(named, _RIGHT, "start-ziel", later)
    with pytest.raises(ValueError, match="invalid"):
        assign_board_name(named, _RIGHT, "   ", later)
    renamed = assign_board_name(named, moved, "Zielgerade", later)
    assert board_name_for(moved, renamed, later) == "Zielgerade"
    assert len(renamed) == 1


def test_an_unnamed_interface_uses_the_usb_title_and_not_the_com_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facts = SerialPortFacts(
        "COM5",
        vid=0x2341,
        pid=0x0042,
        serial_number="BOARD1",
        location="1-1.2",
        manufacturer="Arduino",
    )
    assert usb_interface_label(facts) == "Arduino · BOARD1"
    assert usb_interface_label(SerialPortFacts("COM5")) == "COM5"

    class _Port:
        device = "COM5"
        vid = 0x1A86
        pid = 0x7523
        serial_number = "A1"
        location = "1-2"
        product = None
        description = "USB-SERIAL CH340 (COM5)"
        manufacturer = "wch.cn"

    import serial.tools.list_ports as list_ports

    monkeypatch.setattr(list_ports, "comports", lambda: [_Port()])
    listed = list_serial_devices()
    assert len(listed) == 1
    assert listed[0].device == "COM5"
    assert listed[0].product == "USB-SERIAL CH340"
    assert listed[0].manufacturer == "wch.cn"
    assert usb_interface_label(listed[0]) == "USB-SERIAL CH340 · A1"


def test_the_page_connects_the_named_board_after_the_com_port_changes(qtbot: QtBot) -> None:
    store = SensorConfigurationStore(migrated_database())
    opened: list[str] = []

    def opener(port: str) -> SensorTransport:
        opened.append(port)
        return SerialSensorTransport(port, stream=_Stream(), clock=_Clock(), sync_gap_ns=0)

    page = _page(qtbot, store, (_LEFT, _RIGHT), opener)
    labels = [page.port_combo.itemText(index) for index in range(page.port_combo.count())]
    assert labels == ["USB-SERIAL CH340 · LEFT", "USB-SERIAL CH340 · RIGHT"]
    _choose(page, "sensor-port", "COM5")
    page.board_name.setText("  Start-Ziel  ")
    _click(page, "sensor-board-name-save")
    QApplication.processEvents()
    assert page.port_combo.currentText() == "Start-Ziel"
    assert page.port_combo.currentData() == "COM5"
    assert page.board_name.text() == "Start-Ziel"
    assert page.status.text() == "Der Name „Start-Ziel“ ist dieser Platine zugeordnet."
    assert store.load().device.serial_number == "LEFT"
    _choose(page, "sensor-port", "COM6")
    page.board_name.setText("Start-Ziel")
    _click(page, "sensor-board-name-save")
    assert page.status.text() == "Dieser Name ist bereits einer anderen Platine zugeordnet."
    assert len(store.load().board_names) == 1

    moved = SerialPortFacts(
        "COM11",
        vid=0x1A86,
        pid=0x7523,
        serial_number="LEFT",
        location="2-1",
        product="USB-SERIAL CH340",
    )
    again = _page(qtbot, store, (moved, _RIGHT), opener)
    assert again.port_combo.currentText() == "Start-Ziel"
    assert again.port_combo.currentData() == "COM11"
    assert again.board_name.text() == "Start-Ziel"
    _choose(again, "sensor-transport", TransportMode.SERIAL.value)
    _click(again, "sensor-connect")
    assert opened == ["COM11"]


def _page(
    qtbot: QtBot,
    store: SensorConfigurationStore,
    ports: tuple[SerialPortFacts, ...],
    opener: Callable[[str], SensorTransport],
) -> SensorTimingPage:
    page = SensorTimingPage(
        _translator(),
        SensorLink(),
        store,
        _Setups(),
        _Tracks(),
        ports=lambda: ports,
        serial_transport=opener,
    )
    qtbot.addWidget(page)
    return page
