"""Hand a closed dialog to Qt while Python still holds it.

Closing a dialog only hides it. The planner dialogs also form a Python reference
cycle (the dialog owns rows, and a row keeps a bound method of the dialog), so
the last local variable does not destroy them. The cyclic collector can then
delete the C++ object while the next dialog is being built. That is the native
crash, not a Python exception.

``deleteLater`` makes Qt destroy the object. The posted event is flushed before
the caller drops its reference, so the next open does not meet a wrapper whose
C++ half is about to be collected.
"""

from __future__ import annotations

from typing import TypeGuard

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QDialog, QWidget
from shiboken6 import Shiboken


def dialog_is_alive(dialog: QDialog | None) -> TypeGuard[QDialog]:
    return dialog is not None and bool(Shiboken.isValid(dialog))


def destroy_widget(widget: QWidget | None) -> None:
    """Destroy ``widget`` on the Qt thread. A dead or missing widget is ignored."""
    if widget is None or not bool(Shiboken.isValid(widget)):
        return
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(widget, int(QEvent.Type.DeferredDelete))


def destroy_dialog(dialog: QDialog | None) -> None:
    """Destroy ``dialog`` on the Qt thread. A dead or missing dialog is ignored."""
    destroy_widget(dialog)
