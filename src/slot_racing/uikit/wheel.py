"""Keep the mouse wheel from changing values while a settings page scrolls.

Combo boxes, spin boxes and sliders normally consume the wheel and edit their
value. A scrollable page should move instead. Widgets that implement their own
wheel behaviour, such as the track-plan zoom, are left alone.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDial,
    QSlider,
    QWidget,
)

_GUARDS: dict[int, QObject] = {}


def install_wheel_guard(app: QApplication) -> None:
    """Install the guard once on this application."""
    if id(app) in _GUARDS:
        return
    guard = _WheelGuard(app)
    app.installEventFilter(guard)
    _GUARDS[id(app)] = guard


class _WheelGuard(QObject):
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() != QEvent.Type.Wheel or not isinstance(event, QWheelEvent):
            return super().eventFilter(watched, event)
        if not isinstance(watched, (QComboBox, QAbstractSpinBox, QSlider, QDial)):
            return super().eventFilter(watched, event)
        if isinstance(watched, QWidget):
            _scroll_parent(watched, event)
        return True


def _scroll_parent(widget: QWidget, event: QWheelEvent) -> None:
    scroll = _scroll_ancestor(widget)
    if scroll is None:
        return
    viewport = scroll.viewport()
    if viewport is None:
        return
    local = widget.mapTo(viewport, event.position().toPoint())
    forwarded = QWheelEvent(
        QPointF(local),
        event.globalPosition(),
        event.pixelDelta(),
        event.angleDelta(),
        event.buttons(),
        event.modifiers(),
        event.phase(),
        event.inverted(),
    )
    QApplication.sendEvent(viewport, forwarded)


def _scroll_ancestor(widget: QWidget) -> QAbstractScrollArea | None:
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QAbstractScrollArea):
            return parent
        parent = parent.parentWidget()
    return None
