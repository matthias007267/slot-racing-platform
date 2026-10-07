"""Settings section and dialogs for the local diagnostics bundle.

The log files, breadcrumbs and zip are built in the core. This module only shows
them and asks the user where to save.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QtMsgType, qInstallMessageHandler, qVersion
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from slot_racing.app.runtime import Runtime
from slot_racing.core.diagnostics import (
    current,
    note_fact,
    set_exception_presenter,
    suggested_bundle_name,
)
from slot_racing.core.diagnostics.sanitize import sanitize_text
from slot_racing.uikit.theme import set_role, set_tone

logger = logging.getLogger(__name__)

_qt_guard = threading.local()
_qt_handler_installed = False


def remember_qt_versions() -> None:
    """Store the toolkit versions once a Qt application exists."""
    try:
        import PySide6

        note_fact("pyside", PySide6.__version__)
        note_fact("qt", qVersion())
    except Exception:
        logger.exception("Could not read the Qt version")


def install_qt_messages() -> None:
    """Keep Qt warnings. Debug traffic from paint and timers stays out of the log."""
    global _qt_handler_installed
    if _qt_handler_installed:
        return
    qInstallMessageHandler(_qt_message)
    _qt_handler_installed = True


def bind_crash_dialog(parent: QWidget, translate: Callable[[str], str]) -> None:
    """Show at most the dialogs the core gate allows, and only on the UI thread."""

    def present(crash_id: str) -> None:
        _show_crash_dialog(parent, translate, crash_id)

    set_exception_presenter(present)


def offer_previous_session(
    parent: QWidget,
    translate: Callable[[str], str],
    *,
    on_create: Callable[[], None],
) -> None:
    """Ask once when the previous process did not record a clean shutdown."""
    service = current()
    if service is None or service.smoke_test or not service.previous_unclean:
        return
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Information)
    box.setObjectName("diagnostics-previous-session")
    box.setWindowTitle(translate("diagnostics.previous.title"))
    box.setText(translate("diagnostics.previous.body"))
    create = box.addButton(translate("diagnostics.create"), QMessageBox.ButtonRole.AcceptRole)
    box.addButton(translate("diagnostics.previous.ignore"), QMessageBox.ButtonRole.RejectRole)
    create.setObjectName("diagnostics-previous-create")
    box.exec()
    if box.clickedButton() is create:
        on_create()


class DiagnosticsSettings(QWidget):
    """Version, session and the buttons that open or export the local logs."""

    def __init__(self, runtime: Runtime) -> None:
        super().__init__()
        self.setObjectName("diagnostics-settings")
        self._runtime = runtime
        translate = runtime.translator.translate
        self.message = QLabel()
        self.message.setObjectName("diagnostics-message")
        self.message.setWordWrap(True)
        service = current()
        facts = {} if service is None else service.facts
        form = QFormLayout()
        self._add(
            form, "diagnostics-version", translate("diagnostics.version"), facts.get("version", "—")
        )
        self._add(
            form,
            "diagnostics-session",
            translate("diagnostics.session"),
            facts.get("session_id", "—"),
        )
        self._add(form, "diagnostics-os", translate("diagnostics.os"), facts.get("os", "—"))
        self._add(
            form, "diagnostics-python", translate("diagnostics.python"), facts.get("python", "—")
        )
        self._add(
            form,
            "diagnostics-qt",
            translate("diagnostics.qt"),
            f"{facts.get('pyside', '—')} / {facts.get('qt', '—')}",
        )
        self._add(
            form,
            "diagnostics-schema",
            translate("diagnostics.schema"),
            facts.get("schema_revision", "—"),
        )
        if service is None or service.log_directory is None:
            log_dir = "—"
        else:
            log_dir = str(service.log_directory)
        self._add(form, "diagnostics-log-dir", translate("diagnostics.log_dir"), log_dir)
        self._add(
            form,
            "diagnostics-started",
            translate("diagnostics.started"),
            facts.get("started_at", "—"),
        )
        previous = "—"
        if service is not None:
            key = (
                "diagnostics.previous.no"
                if service.previous_unclean
                else "diagnostics.previous.yes"
            )
            previous = translate(key)
        self._add(form, "diagnostics-previous", translate("diagnostics.previous"), previous)
        open_log = QPushButton(translate("diagnostics.open_log"))
        open_log.setObjectName("diagnostics-open-log")
        set_role(open_log, "ghost")
        open_log.clicked.connect(self._open_log)
        open_folder = QPushButton(translate("diagnostics.open_folder"))
        open_folder.setObjectName("diagnostics-open-folder")
        set_role(open_folder, "ghost")
        open_folder.clicked.connect(self._open_folder)
        create = QPushButton(translate("diagnostics.create"))
        create.setObjectName("diagnostics-bundle")
        create.clicked.connect(self.create_bundle)
        actions = QHBoxLayout()
        actions.addWidget(open_log)
        actions.addWidget(open_folder)
        actions.addWidget(create)
        actions.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel(translate("diagnostics.title"))
        title.setObjectName("settings-section-diagnostics")
        set_role(title, "section")
        layout.addWidget(title)
        layout.addLayout(form)
        layout.addLayout(actions)
        layout.addWidget(self.message)
        self._log_button = open_log
        self._folder_button = open_folder
        available = service is not None and service.log_directory is not None
        open_log.setEnabled(available and service is not None and service.log_file is not None)
        open_folder.setEnabled(available)

    def create_bundle(self) -> None:
        """Ask for a path and write the zip. A failure stays on this page."""
        translate = self._runtime.translator.translate
        service = current()
        if service is None:
            self._show(translate("diagnostics.inactive"), error=True)
            return
        suggested = str(Path.home() / suggested_bundle_name())
        selected, _filter = QFileDialog.getSaveFileName(
            self,
            translate("diagnostics.create"),
            suggested,
            translate("diagnostics.filter"),
        )
        if not selected:
            return
        destination = Path(selected)
        if destination.suffix != ".zip":
            destination = destination.with_suffix(".zip")
        try:
            service.write_bundle(destination, config=self._runtime.config)
        except Exception as error:
            logger.exception("Support bundle failed")
            self._show(translate("diagnostics.failed"), error=True)
            note_fact("last_bundle_error", type(error).__name__)
            return
        self._show(translate("diagnostics.created").format(name=destination.name), error=False)

    def _open_log(self) -> None:
        service = current()
        if service is None or service.log_file is None:
            return
        self._reveal(service.log_file)

    def _open_folder(self) -> None:
        service = current()
        if service is None or service.log_directory is None:
            return
        self._reveal(service.log_directory)

    def _reveal(self, path: Path) -> None:
        try:
            _open_path(path)
        except OSError:
            logger.exception("Could not open %s", path.name)
            self._show(self._runtime.translator.translate("diagnostics.open_failed"), error=True)

    def _show(self, text: str, *, error: bool) -> None:
        self.message.setText(text)
        set_tone(self.message, "error" if error else "")

    @staticmethod
    def _add(form: QFormLayout, object_name: str, label: str, value: str) -> None:
        shown = QLabel(value)
        shown.setObjectName(object_name)
        shown.setWordWrap(True)
        shown.setMinimumWidth(280)
        form.addRow(label, shown)


def _show_crash_dialog(parent: QWidget, translate: Callable[[str], str], crash_id: str) -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setObjectName("diagnostics-crash")
    box.setWindowTitle(translate("diagnostics.crash.title"))
    box.setText(translate("diagnostics.crash.body").format(crash_id=crash_id))
    open_settings = box.addButton(
        translate("diagnostics.crash.open"), QMessageBox.ButtonRole.AcceptRole
    )
    box.addButton(translate("diagnostics.crash.close"), QMessageBox.ButtonRole.RejectRole)
    open_settings.setObjectName("diagnostics-crash-open")
    box.exec()
    if box.clickedButton() is open_settings:
        select = getattr(parent, "select", None)
        if callable(select):
            select("settings")


def _qt_message(mode: QtMsgType, _context: object, message: str) -> None:
    if getattr(_qt_guard, "on", False):
        return
    if mode == QtMsgType.QtDebugMsg:
        return
    _qt_guard.on = True
    try:
        text = sanitize_text(message)
        if mode == QtMsgType.QtInfoMsg:
            logger.debug("QT %s", text)
        elif mode == QtMsgType.QtWarningMsg:
            logger.warning("QT %s", text)
        elif mode == QtMsgType.QtCriticalMsg:
            logger.error("QT %s", text)
        elif mode == QtMsgType.QtFatalMsg:
            logger.critical("QT_FATAL %s", text)
    finally:
        _qt_guard.on = False


def _open_path(path: Path) -> None:
    if sys.platform == "win32":
        startfile = getattr(os, "startfile", None)
        if startfile is None:
            raise OSError("startfile is unavailable")
        startfile(path)  # type: ignore[operator]
        return
    command = ["open", str(path)] if sys.platform == "darwin" else ["xdg-open", str(path)]
    subprocess.run(command, check=False)
