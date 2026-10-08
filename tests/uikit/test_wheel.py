"""The mouse wheel scrolls a page and leaves setting values alone."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from pytestqt.qtbot import QtBot

from slot_racing.modules.track_planner.ui.canvas import PlanCanvas
from slot_racing.uikit.theme import apply_theme


def test_the_wheel_scrolls_the_page_and_leaves_inputs_unchanged(qtbot: QtBot) -> None:
    app = QApplication.instance()
    assert isinstance(app, QApplication)
    apply_theme(app)
    combo = QComboBox()
    combo.addItems(["Eins", "Zwei", "Drei"])
    combo.setCurrentIndex(0)
    spin = QSpinBox()
    spin.setRange(0, 100)
    spin.setValue(10)
    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setRange(0, 100)
    slider.setValue(20)
    content = QWidget()
    content.setMinimumHeight(1600)
    column = QVBoxLayout(content)
    column.addWidget(combo)
    column.addWidget(spin)
    column.addWidget(slider)
    column.addStretch(1)
    scroll = QScrollArea()
    qtbot.addWidget(scroll)
    scroll.setWidget(content)
    scroll.setWidgetResizable(True)
    scroll.resize(320, 180)
    scroll.show()
    qtbot.waitUntil(lambda: scroll.viewport().height() > 40)

    _wheel(combo, -120)
    _wheel(spin, -120)
    _wheel(slider, -120)
    assert combo.currentIndex() == 0
    assert spin.value() == 10
    assert slider.value() == 20
    assert scroll.verticalScrollBar().value() > 0

    bar = scroll.verticalScrollBar()
    before = bar.value()
    _wheel(bar, -120)
    assert bar.value() > before

    spin.setFocus()
    qtbot.keyClick(spin, Qt.Key.Key_Up)
    assert spin.value() == 11
    edit = spin.lineEdit()
    assert edit is not None
    edit.setText("40")
    qtbot.keyClick(edit, Qt.Key.Key_Enter)
    assert spin.value() == 40
    combo.setFocus()
    qtbot.keyClick(combo, Qt.Key.Key_Down)
    assert combo.currentIndex() == 1


def test_the_track_plan_still_zooms_with_the_wheel(qtbot: QtBot) -> None:
    app = QApplication.instance()
    assert isinstance(app, QApplication)
    apply_theme(app)
    canvas = PlanCanvas()
    qtbot.addWidget(canvas)
    canvas.resize(800, 600)
    canvas.show()
    scale = canvas.transform().m11()
    viewport = canvas.viewport()
    assert viewport is not None
    _wheel(viewport, 120)
    assert canvas.transform().m11() == scale
    _wheel(viewport, 120, Qt.KeyboardModifier.ControlModifier)
    assert canvas.transform().m11() > scale


def _wheel(
    widget: QWidget,
    delta_y: int,
    modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
) -> None:
    QApplication.sendEvent(
        widget,
        QWheelEvent(
            QPointF(12, 12),
            QPointF(12, 12),
            QPoint(0, 0),
            QPoint(0, delta_y),
            Qt.MouseButton.NoButton,
            modifiers,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        ),
    )
