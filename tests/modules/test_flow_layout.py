"""FlowHost keeps every wrapped control inside its own rectangle."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QPushButton, QSizePolicy, QVBoxLayout, QWidget
from pytestqt.qtbot import QtBot

from slot_racing.modules.track_planner.ui.flow_layout import FlowHost, retain_content_width


def test_a_narrow_flow_host_grows_to_its_wrapped_rows(qtbot: QtBot) -> None:
    window = QWidget()
    window.setObjectName("flow-host-window")
    layout = QVBoxLayout(window)
    layout.setContentsMargins(0, 0, 0, 0)
    host = FlowHost("flow-host")
    flow = host.layout()
    assert flow is not None
    buttons = [QPushButton(f"Track control {index:02d}") for index in range(8)]
    for button in buttons:
        retain_content_width(button)
        flow.addWidget(button)
    sibling = QWidget()
    sibling.setMinimumHeight(40)
    sibling.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    layout.addWidget(host)
    layout.addWidget(sibling, 1)
    qtbot.addWidget(window)

    window.resize(280, 640)
    window.show()
    _settle(window)
    _assert_inside(host, buttons)
    assert host.height() + 1 >= host.heightForWidth(host.width())
    assert len({button.geometry().top() for button in buttons}) > 1

    window.resize(host.sizeHint().width() + 80, 400)
    _settle(window)
    _assert_inside(host, buttons)
    assert len({button.geometry().top() for button in buttons}) == 1
    assert host.width() + 1 >= host.sizeHint().width()

    window.resize(280, 640)
    _settle(window)
    _assert_inside(host, buttons)
    assert len({button.geometry().top() for button in buttons}) > 1


def _assert_inside(host: QWidget, buttons: list[QPushButton]) -> None:
    for button in buttons:
        rect = button.geometry()
        assert rect.left() >= 0
        assert rect.top() >= 0
        assert rect.right() <= host.width()
        assert rect.bottom() <= host.height()
        assert button.width() >= button.fontMetrics().horizontalAdvance(button.text())


def _settle(window: QWidget) -> None:
    QApplication.processEvents()
    layout = window.layout()
    assert layout is not None
    layout.activate()
    QApplication.processEvents()
    layout.activate()
    QApplication.processEvents()
