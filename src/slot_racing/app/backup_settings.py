"""Backup controls on the settings page."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from slot_racing.app.runtime import Runtime
from slot_racing.core.backup import create_backup, restore_backup
from slot_racing.core.config import save_config
from slot_racing.core.events import PersistentStoreReplacing
from slot_racing.uikit.errors import describe_error
from slot_racing.uikit.theme import set_role, set_tone

_SCHEDULES = ("off", "daily", "on_exit")


class BackupSettings(QWidget):
    """Create a backup, restore one, and choose where automatic backups go."""

    def __init__(self, runtime: Runtime, on_restored: Callable[[], None]) -> None:
        super().__init__()
        self.setObjectName("backup-settings")
        self._runtime = runtime
        self._on_restored = on_restored
        translate = runtime.translator.translate
        self.message = QLabel()
        self.message.setObjectName("backup-message")
        self.message.setWordWrap(True)
        self.directory = QLabel(str(runtime.config.resolved_backup_directory()))
        self.directory.setObjectName("backup-directory")
        self.directory.setWordWrap(True)
        choose = QPushButton(translate("backup.choose"))
        choose.setObjectName("backup-choose-directory")
        set_role(choose, "ghost")
        choose.clicked.connect(self._choose_directory)
        directory_row = QHBoxLayout()
        directory_row.addWidget(self.directory, 1)
        directory_row.addWidget(choose)
        self.schedule = QComboBox()
        self.schedule.setObjectName("backup-schedule")
        for key in _SCHEDULES:
            self.schedule.addItem(translate(f"backup.schedule.{key}"), key)
        self.schedule.setCurrentIndex(
            max(0, self.schedule.findData(runtime.config.backup_schedule))
        )
        self.schedule.currentIndexChanged.connect(self._save_schedule)
        self.keep = QSpinBox()
        self.keep.setObjectName("backup-keep")
        self.keep.setRange(1, 100)
        self.keep.setValue(runtime.config.backup_keep)
        self.keep.valueChanged.connect(self._save_keep)
        create = QPushButton(translate("backup.create"))
        create.setObjectName("backup-create")
        create.clicked.connect(self._create)
        restore = QPushButton(translate("backup.restore"))
        restore.setObjectName("backup-restore")
        restore.clicked.connect(self._choose_restore)
        actions = QHBoxLayout()
        actions.addWidget(create)
        actions.addWidget(restore)
        actions.addStretch(1)
        form = QFormLayout()
        form.addRow(translate("backup.directory"), directory_row)
        form.addRow(translate("backup.schedule"), self.schedule)
        form.addRow(translate("backup.keep"), self.keep)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel(translate("backup.title"))
        title.setObjectName("settings-section-backup")
        set_role(title, "section")
        layout.addWidget(title)
        layout.addLayout(form)
        layout.addLayout(actions)
        layout.addWidget(self.message)

    def restore_from(self, path: Path) -> None:
        """Ask, then replace the current data with ``path``."""
        if not self._confirm():
            return
        try:
            restore_backup(
                path,
                self._runtime.database,
                self._runtime.config,
                self._runtime.config_path,
                backup_directory=self._runtime.config.resolved_backup_directory(),
                before_swap=self._release_live_state,
            )
        except Exception as error:
            self._show_error(error)
            return
        self._on_restored()

    def _choose_directory(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            self._runtime.translator.translate("backup.directory"),
            str(self._runtime.config.resolved_backup_directory()),
        )
        if not selected:
            return
        self._runtime.config.backup_directory = Path(selected)
        self.directory.setText(selected)
        self._persist()
        self._show_info("")

    def _choose_restore(self) -> None:
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            self._runtime.translator.translate("backup.restore.title"),
            str(self._runtime.config.resolved_backup_directory()),
            self._runtime.translator.translate("backup.file_filter"),
        )
        if not selected:
            return
        self.restore_from(Path(selected))

    def _create(self) -> None:
        try:
            path = create_backup(
                self._runtime.database,
                self._runtime.config,
                self._runtime.config.resolved_backup_directory(),
            )
        except Exception as error:
            self._show_error(error)
            return
        self._show_info(self._runtime.translator.format("backup.created", name=path.name))

    def _release_live_state(self) -> None:
        self._runtime.bus.publish(
            PersistentStoreReplacing(timestamp_ns=self._runtime.clock.now_ns())
        )

    def _save_schedule(self) -> None:
        value = self.schedule.currentData()
        if value == "off" or value == "daily" or value == "on_exit":
            self._runtime.config.backup_schedule = value
            self._persist()

    def _save_keep(self) -> None:
        self._runtime.config.backup_keep = self.keep.value()
        self._persist()

    def _confirm(self) -> bool:
        translate = self._runtime.translator.translate
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(translate("backup.restore.title"))
        box.setText(translate("backup.restore.confirm"))
        accept = box.addButton(
            translate("backup.restore.accept"), QMessageBox.ButtonRole.AcceptRole
        )
        cancel = box.addButton(
            translate("backup.restore.cancel"), QMessageBox.ButtonRole.RejectRole
        )
        accept.setObjectName("backup-restore-confirm")
        cancel.setObjectName("backup-restore-cancel")
        box.setDefaultButton(cancel)
        _keep_text_visible(box)
        box.exec()
        return box.clickedButton() is accept

    def _persist(self) -> None:
        path = self._runtime.config_path
        if path is None:
            return
        try:
            save_config(self._runtime.config, path)
        except OSError as error:
            self._show_error(error)

    def _show_error(self, error: Exception) -> None:
        set_tone(self.message, "error")
        self.message.setText(describe_error(self._runtime.translator, error))

    def _show_info(self, text: str) -> None:
        set_tone(self.message, "")
        self.message.setText(text)


def _keep_text_visible(box: QMessageBox) -> None:
    label = box.findChild(QLabel, "qt_msgbox_label")
    if label is None or not label.text():
        return
    label.ensurePolished()
    widest = max(label.fontMetrics().horizontalAdvance(line) for line in label.text().splitlines())
    label.setMinimumWidth(widest + 12)
