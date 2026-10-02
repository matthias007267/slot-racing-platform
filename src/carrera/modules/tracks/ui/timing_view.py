"""Timing configuration of one track: logical positions and the sensors assigned to them."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from carrera.core.catalog import TrackInfo
from carrera.core.domain import TimingPositionType, TimingSetup, default_timing_setup
from carrera.core.i18n import Translator
from carrera.core.timing import TimingSetupService
from carrera.modules.tracks.timing_editor import TimingDraft
from carrera.modules.tracks.ui.common import entry_text, run_guarded, type_label, yes_no
from carrera.modules.tracks.ui.timing_dialog import PositionDialog
from carrera.uikit import StatusLabel, fill_table, heading, make_table, selected_id

COLUMN_KEYS = (
    "timing.column.number",
    "timing.column.position",
    "timing.column.type",
    "timing.column.sensor_id",
    "timing.column.sensor_name",
    "timing.column.hardware_id",
    "timing.column.active",
)


def draft_rows(translator: Translator, draft: TimingDraft) -> list[tuple[str, ...]]:
    return [
        (
            str(index),
            entry_text(translator, draft.entries, entry),
            type_label(translator, entry.type),
            entry.sensor_id,
            entry.sensor_name or "",
            entry.hardware_id or "",
            yes_no(translator, entry.active),
        )
        for index, entry in enumerate(draft.entries, start=1)
    ]


class TimingConfigView(QWidget):
    back_requested = Signal()
    test_requested = Signal()
    wizard_requested = Signal()

    def __init__(
        self,
        translator: Translator,
        setups: Callable[[], TimingSetupService | None],
    ) -> None:
        super().__init__()
        self.translator = translator
        self._setups = setups
        self.dialog_runner: Callable[[QDialog], int] = lambda dialog: dialog.exec()
        self.confirm: Callable[[str], bool] = lambda text: (
            QMessageBox.question(self, translator.translate("common.confirm"), text)
            == QMessageBox.StandardButton.Yes
        )
        self.track: TrackInfo | None = None
        self.draft = TimingDraft([])
        self.is_default = False
        tr = translator.translate

        self.title = heading(tr("timing.title"))
        self.title.setObjectName("timing-title")
        self.hint = QLabel()
        self.hint.setObjectName("timing-hint")
        self.hint.setWordWrap(True)
        self.table = make_table([tr(key) for key in COLUMN_KEYS], "timing-table")
        self.status = StatusLabel("timing-status")

        def button(key: str, name: str) -> QPushButton:
            widget = QPushButton(tr(key))
            widget.setObjectName(name)
            return widget

        self.add_button = button("timing.add_position", "timing-add")
        self.remove_button = button("timing.remove_position", "timing-remove")
        self.up_button = button("timing.move_up", "timing-up")
        self.down_button = button("timing.move_down", "timing-down")
        self.edit_button = button("timing.edit_sensor", "timing-edit")
        self.toggle_button = button("timing.toggle_sensor", "timing-toggle")
        self.save_button = button("timing.save", "timing-save")
        self.reset_button = button("timing.reset", "timing-reset")
        self.test_button = button("timing.open_test", "timing-test")
        self.wizard_button = button("timing.open_wizard", "timing-wizard")
        self.back_button = button("timing.back", "timing-back")
        self._selection_buttons = (
            self.remove_button,
            self.up_button,
            self.down_button,
            self.edit_button,
            self.toggle_button,
        )
        self._edit_buttons = (
            self.add_button,
            *self._selection_buttons,
            self.save_button,
            self.reset_button,
            self.test_button,
            self.wizard_button,
        )

        edit_row = QHBoxLayout()
        for widget in (
            self.add_button,
            self.remove_button,
            self.up_button,
            self.down_button,
            self.edit_button,
            self.toggle_button,
        ):
            edit_row.addWidget(widget)
        edit_row.addStretch(1)
        action_row = QHBoxLayout()
        for widget in (self.save_button, self.reset_button, self.test_button, self.wizard_button):
            action_row.addWidget(widget)
        action_row.addStretch(1)
        action_row.addWidget(self.back_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.title)
        layout.addWidget(self.hint)
        layout.addLayout(edit_row)
        layout.addWidget(self.table, 1)
        layout.addLayout(action_row)
        layout.addWidget(self.status)

        self.add_button.clicked.connect(lambda: self.add_position())
        self.remove_button.clicked.connect(lambda: self.remove_selected())
        self.up_button.clicked.connect(lambda: self.move_selected(-1))
        self.down_button.clicked.connect(lambda: self.move_selected(1))
        self.edit_button.clicked.connect(lambda: self.edit_selected())
        self.toggle_button.clicked.connect(lambda: self.toggle_selected())
        self.save_button.clicked.connect(lambda: self.save())
        self.reset_button.clicked.connect(lambda: self.reset())
        self.test_button.clicked.connect(lambda: self.test_requested.emit())
        self.wizard_button.clicked.connect(lambda: self.wizard_requested.emit())
        self.back_button.clicked.connect(lambda: self.back_requested.emit())
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.cellDoubleClicked.connect(lambda *_: self.edit_selected())
        self._refresh()

    def open_track(self, track: TrackInfo) -> None:
        """Load the stored timing setup of ``track``, or the default one if none is stored."""
        self.track = track
        self.status.clear_message()
        self.title.setText(self.translator.format("timing.title_for", name=track.name))
        self._guarded(self._load)

    def row_count(self) -> int:
        return self.table.rowCount()

    def select_position(self, position_id: str) -> None:
        for index, entry in enumerate(self.draft.entries):
            if entry.position_id == position_id:
                self.table.selectRow(index)
                return

    def current_setup(self) -> TimingSetup:
        """The setup as currently edited. Raises ``ValidationError`` if it is invalid."""
        return self.draft.to_setup()

    def add_position(self) -> None:
        self._guarded(self._add)

    def remove_selected(self) -> None:
        position_id = self._selected_position()
        if position_id is not None:
            self._guarded(lambda: self._change(lambda: self.draft.remove(position_id)))

    def move_selected(self, offset: int) -> None:
        position_id = self._selected_position()
        if position_id is not None:
            self._guarded(lambda: self._move(position_id, offset))

    def edit_selected(self) -> None:
        position_id = self._selected_position()
        if position_id is not None:
            self._guarded(lambda: self._edit(position_id))

    def toggle_selected(self) -> None:
        position_id = self._selected_position()
        if position_id is not None:
            self._guarded(lambda: self._toggle(position_id))

    def save(self) -> None:
        self._guarded(self._save)

    def reset(self) -> None:
        """Discard the stored setup of the track and fall back to the default layout."""
        self._guarded(self._reset)

    def _guarded(self, action: Callable[[], None]) -> None:
        run_guarded(self.translator, self.status, self, action)

    def _service(self) -> TimingSetupService | None:
        return self._setups()

    def _load(self) -> None:
        assert self.track is not None
        service = self._service()
        stored = service.get_setup(self.track.id) if service is not None else None
        self.is_default = stored is None
        self.draft = TimingDraft.from_setup(stored or default_timing_setup())
        self._refresh()
        if service is None:
            self.status.show_error(self.translator.translate("timing.service_missing"))

    def _add(self) -> None:
        entry = self.draft.add_sector()
        self._refresh()
        self.select_position(entry.position_id)

    def _change(self, change: Callable[[], None]) -> None:
        change()
        self._refresh()

    def _move(self, position_id: str, offset: int) -> None:
        self.draft.move(position_id, offset)
        self._refresh()
        self.select_position(position_id)

    def _edit(self, position_id: str) -> None:
        dialog = PositionDialog(self.translator, self.draft, position_id, self)
        self.dialog_runner(dialog)
        self._refresh()
        self.select_position(position_id)

    def _toggle(self, position_id: str) -> None:
        entry = self.draft.entry(position_id)
        self.draft.update(
            position_id,
            name=entry.name,
            sensor_id=entry.sensor_id,
            sensor_name=entry.sensor_name,
            hardware_id=entry.hardware_id,
            active=not entry.active,
        )
        self._refresh()
        self.select_position(position_id)

    def _save(self) -> None:
        service = self._service()
        if service is None:
            self.status.show_error(self.translator.translate("timing.service_missing"))
            return
        assert self.track is not None
        setup = self.draft.to_setup()
        service.save_setup(self.track.id, setup)
        self._load()
        key = "timing.saved" if setup.is_usable else "timing.saved_with_inactive"
        self.status.show_info(self.translator.translate(key))

    def _reset(self) -> None:
        service = self._service()
        if service is None or self.track is None:
            return
        if not self.confirm(self.translator.translate("timing.confirm_reset")):
            return
        service.clear_setup(self.track.id)
        self._load()
        self.status.show_info(self.translator.translate("timing.reset_done"))

    def _selected_position(self) -> str | None:
        index = selected_id(self.table)
        if index is None or not 0 <= index < len(self.draft.entries):
            return None
        return self.draft.entries[index].position_id

    def _refresh(self) -> None:
        fill_table(
            self.table,
            draft_rows(self.translator, self.draft),
            list(range(len(self.draft.entries))),
            keep_selection=False,
        )
        self.hint.setText(
            self.translator.translate("timing.default_hint") if self.is_default else ""
        )
        self._update_buttons()

    def _update_buttons(self) -> None:
        available = self._service() is not None and self.track is not None
        for widget in self._edit_buttons:
            widget.setEnabled(available)
        position_id = self._selected_position()
        has_selection = position_id is not None
        fixed = (
            position_id is not None
            and self.draft.entry(position_id).type is TimingPositionType.START_FINISH
        )
        for widget in self._selection_buttons:
            widget.setEnabled(available and has_selection)
        for widget in (self.remove_button, self.up_button, self.down_button):
            widget.setEnabled(available and has_selection and not fixed)
