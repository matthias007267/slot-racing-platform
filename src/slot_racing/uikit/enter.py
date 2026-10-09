"""Enter confirms one action. Multiline fields keep the line break.

A focused button keeps Space. Enter on that button does not run the window
action and does not close it. Modifier shortcuts, an open popup, and a dialog
stay with their existing handlers, including a dialog's default button.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSpinBox,
    QApplication,
    QDialog,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)

_MODIFIERS = (
    Qt.KeyboardModifier.ControlModifier
    | Qt.KeyboardModifier.AltModifier
    | Qt.KeyboardModifier.MetaModifier
)


def bind_enter(root: QWidget, action: Callable[[], object]) -> None:
    """Run ``action`` when Enter is pressed inside ``root``, except in the cases above."""
    watcher = _EnterWatcher(root, action)
    watcher.setParent(root)
    app = QApplication.instance()
    if app is not None:
        app.installEventFilter(watcher)


class _EnterWatcher(QObject):
    def __init__(self, root: QWidget, action: Callable[[], object]) -> None:
        super().__init__()
        self._root = root
        self._action = action

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() != QEvent.Type.KeyPress or not isinstance(event, QKeyEvent):
            return False
        if event.key() not in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            return False
        if not isinstance(watched, QWidget) or not _inside(self._root, watched):
            return False
        if event.modifiers() & _MODIFIERS:
            return False
        if _blocked(watched):
            # Enter on a button is ignored and would walk up into the window
            # action. Swallow it outside a dialog. Inside a dialog it keeps
            # walking so the default button still runs.
            return isinstance(watched, QAbstractButton) and not _in_dialog(watched)
        if event.isAutoRepeat():
            return True
        _commit(watched)
        self._action()
        return True


def _inside(root: QWidget, focus: QWidget) -> bool:
    return focus is root or root.isAncestorOf(focus)


def _in_dialog(widget: QWidget) -> bool:
    parent: QWidget | None = widget
    while parent is not None:
        if isinstance(parent, QDialog):
            return True
        parent = parent.parentWidget()
    return False


def _blocked(focus: QWidget) -> bool:
    if QApplication.activePopupWidget() is not None:
        return True
    widget: QWidget | None = focus
    while widget is not None:
        if isinstance(widget, (QTextEdit, QPlainTextEdit, QDialog, QAbstractButton)):
            return True
        widget = widget.parentWidget()
    return False


def _commit(focus: QWidget) -> None:
    """Write a typed value before the window action reads it."""
    if isinstance(focus, QLineEdit):
        focus.editingFinished.emit()
        return
    if isinstance(focus, QAbstractSpinBox):
        focus.clearFocus()
        focus.setFocus(Qt.FocusReason.OtherFocusReason)
