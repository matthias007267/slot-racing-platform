"""Six step wizard for the timing configuration of a track.

1 track, 2 positions, 3 sensors, 4 order, 5 test, 6 save. The wizard edits a
:class:`TimingDraft`; validation and persistence are done by the domain and the timing service.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from carrera.core.catalog import TrackInfo
from carrera.core.clock import Clock
from carrera.core.domain import default_timing_setup
from carrera.core.errors import ValidationError
from carrera.core.i18n import Translator
from carrera.core.timing import TimingSetupService, TimingSourceFactory
from carrera.modules.tracks.timing_editor import TimingDraft
from carrera.modules.tracks.ui.common import entry_text, run_guarded, type_text, yes_no
from carrera.modules.tracks.ui.timing_dialog import PositionDialog
from carrera.modules.tracks.ui.timing_test_view import TimingTestView
from carrera.uikit import StatusLabel, fill_table, heading, make_table, selected_id

TRACK, POSITIONS, SENSORS, ORDER, TEST, SAVE = range(6)
STEP_KEYS = (
    "timing.wizard.step.track",
    "timing.wizard.step.positions",
    "timing.wizard.step.sensors",
    "timing.wizard.step.order",
    "timing.wizard.step.test",
    "timing.wizard.step.save",
)


class TimingWizard(QWidget):
    finished = Signal(int)
    """Emitted with the track id after the configuration was saved."""
    cancelled = Signal()

    def __init__(
        self,
        translator: Translator,
        tracks: Callable[[], list[TrackInfo]],
        setups: Callable[[], TimingSetupService | None],
        factories: Callable[[], Sequence[TimingSourceFactory]],
        clock: Clock,
    ) -> None:
        super().__init__()
        self.translator = translator
        self._tracks = tracks
        self._setups = setups
        self.dialog_runner: Callable[[QDialog], int] = lambda dialog: dialog.exec()
        self.draft = TimingDraft([])
        self.track: TrackInfo | None = None
        tr = translator.translate

        self.step_label = heading("")
        self.step_label.setObjectName("wizard-step")
        self.status = StatusLabel("wizard-status")
        self.stack = QStackedWidget()

        self.track_combo = QComboBox()
        self.track_combo.setObjectName("wizard-track")
        track_page = QWidget()
        track_layout = QVBoxLayout(track_page)
        track_layout.addWidget(QLabel(tr("timing.wizard.track_hint")))
        track_layout.addWidget(self.track_combo)
        track_layout.addStretch(1)

        self.positions_table = make_table(
            [tr("timing.column.number"), tr("timing.column.position"), tr("timing.column.type")],
            "wizard-positions",
        )
        self.add_button = QPushButton(tr("timing.add_position"))
        self.add_button.setObjectName("wizard-add")
        self.remove_button = QPushButton(tr("timing.remove_position"))
        self.remove_button.setObjectName("wizard-remove")
        positions_page = self._page(
            "timing.wizard.positions_hint",
            self.positions_table,
            [self.add_button, self.remove_button],
        )

        self.sensors_table = make_table(
            [
                tr("timing.column.position"),
                tr("timing.column.sensor_id"),
                tr("timing.column.hardware_id"),
                tr("timing.column.active"),
            ],
            "wizard-sensors",
        )
        self.assign_button = QPushButton(tr("timing.wizard.assign"))
        self.assign_button.setObjectName("wizard-assign")
        self.default_ids_button = QPushButton(tr("timing.wizard.default_ids"))
        self.default_ids_button.setObjectName("wizard-default-ids")
        sensors_page = self._page(
            "timing.wizard.sensors_hint",
            self.sensors_table,
            [self.assign_button, self.default_ids_button],
        )

        self.order_table = make_table(
            [
                tr("timing.column.number"),
                tr("timing.column.position"),
                tr("timing.column.sensor_id"),
            ],
            "wizard-order",
        )
        self.up_button = QPushButton(tr("timing.move_up"))
        self.up_button.setObjectName("wizard-up")
        self.down_button = QPushButton(tr("timing.move_down"))
        self.down_button.setObjectName("wizard-down")
        order_page = self._page(
            "timing.wizard.order_hint", self.order_table, [self.up_button, self.down_button]
        )

        self.test_view = TimingTestView(translator, factories, clock, show_back=False)

        self.summary_table = make_table(
            [
                tr("timing.column.number"),
                tr("timing.column.position"),
                tr("timing.column.sensor_id"),
                tr("timing.column.hardware_id"),
                tr("timing.column.active"),
            ],
            "wizard-summary",
        )
        save_page = self._page("timing.wizard.save_hint", self.summary_table, [])

        for page in (
            track_page,
            positions_page,
            sensors_page,
            order_page,
            self.test_view,
            save_page,
        ):
            self.stack.addWidget(page)

        self.cancel_button = QPushButton(tr("timing.wizard.cancel"))
        self.cancel_button.setObjectName("wizard-cancel")
        self.back_button = QPushButton(tr("timing.wizard.back"))
        self.back_button.setObjectName("wizard-back")
        self.next_button = QPushButton(tr("timing.wizard.next"))
        self.next_button.setObjectName("wizard-next")
        self.save_button = QPushButton(tr("timing.save"))
        self.save_button.setObjectName("wizard-save")
        navigation = QHBoxLayout()
        navigation.addWidget(self.cancel_button)
        navigation.addStretch(1)
        for button in (self.back_button, self.next_button, self.save_button):
            navigation.addWidget(button)

        layout = QVBoxLayout(self)
        layout.addWidget(heading(tr("timing.wizard.title")))
        layout.addWidget(self.step_label)
        layout.addWidget(self.stack, 1)
        layout.addWidget(self.status)
        layout.addLayout(navigation)

        self.cancel_button.clicked.connect(lambda: self.cancel())
        self.back_button.clicked.connect(lambda: self.back())
        self.next_button.clicked.connect(lambda: self.next())
        self.save_button.clicked.connect(lambda: self.save())
        self.add_button.clicked.connect(lambda: self.add_position())
        self.remove_button.clicked.connect(lambda: self.remove_selected())
        self.assign_button.clicked.connect(lambda: self.assign_selected())
        self.default_ids_button.clicked.connect(lambda: self.assign_default_ids())
        self.up_button.clicked.connect(lambda: self.move_selected(-1))
        self.down_button.clicked.connect(lambda: self.move_selected(1))
        self.sensors_table.cellDoubleClicked.connect(lambda *_: self.assign_selected())

    def _page(self, hint_key: str, table: QWidget, buttons: list[QPushButton]) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        hint = QLabel(self.translator.translate(hint_key))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        row = QHBoxLayout()
        for button in buttons:
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addWidget(table, 1)
        return page

    @property
    def step(self) -> int:
        return self.stack.currentIndex()

    def start(self, track: TrackInfo | None = None) -> None:
        """Begin at step 1, optionally with ``track`` preselected."""
        self.status.clear_message()
        self.track = None
        self.draft = TimingDraft([])
        self.track_combo.clear()
        for info in self._tracks():
            self.track_combo.addItem(info.name, info.id)
        if track is not None:
            index = self.track_combo.findData(track.id)
            if index >= 0:
                self.track_combo.setCurrentIndex(index)
        self._go(TRACK)

    def next(self) -> None:
        run_guarded(self.translator, self.status, self, self._next)

    def back(self) -> None:
        self.status.clear_message()
        if self.step > TRACK:
            self._go(self.step - 1)

    def cancel(self) -> None:
        self.test_view.stop()
        self.cancelled.emit()

    def add_position(self) -> None:
        entry = self.draft.add_sector()
        self._refresh()
        self._select(self.positions_table, self.draft.entries.index(entry))

    def remove_selected(self) -> None:
        position_id = self._selected(self.positions_table)
        if position_id is not None:
            self._run(lambda: self.draft.remove(position_id))

    def assign_selected(self) -> None:
        position_id = self._selected(self.sensors_table)
        if position_id is not None:
            self._run(lambda: self.dialog_runner(self._dialog(position_id)))

    def assign_default_ids(self) -> None:
        self.draft.assign_default_sensor_ids()
        self._refresh()

    def move_selected(self, offset: int) -> None:
        position_id = self._selected(self.order_table)
        if position_id is not None:
            self._run(lambda: self.draft.move(position_id, offset))
            self._select(self.order_table, self._index_of(position_id))

    def save(self) -> None:
        run_guarded(self.translator, self.status, self, self._save)

    def _dialog(self, position_id: str) -> PositionDialog:
        return PositionDialog(self.translator, self.draft, position_id, self)

    def _run(self, action: Callable[[], object]) -> None:
        def run() -> None:
            action()
            self._refresh()

        run_guarded(self.translator, self.status, self, run)

    def _next(self) -> None:
        self.status.clear_message()
        if self.step == TRACK:
            self._load_track()
        elif self.step in (SENSORS, ORDER):
            self.draft.to_setup()
        if self.step == ORDER:
            self.test_view.open_setup(self.draft.to_setup(), self.track.id if self.track else None)
        self._go(min(self.step + 1, SAVE))

    def _load_track(self) -> None:
        track_id = self.track_combo.currentData()
        track = next((t for t in self._tracks() if t.id == track_id), None)
        if track is None:
            raise ValidationError("error.timing.no_track")
        service = self._setups()
        if service is None:
            raise ValidationError("error.timing.service_missing")
        if self.track is not None and self.track.id == track.id:
            return
        self.track = track
        stored = service.get_setup(track.id)
        self.draft = TimingDraft.from_setup(stored or default_timing_setup())

    def _save(self) -> None:
        service = self._setups()
        if service is None or self.track is None:
            return
        setup = self.draft.to_setup()
        service.save_setup(self.track.id, setup)
        self.test_view.stop()
        self.finished.emit(self.track.id)

    def _go(self, step: int) -> None:
        self.stack.setCurrentIndex(step)
        tr = self.translator.translate
        self.step_label.setText(
            self.translator.format(
                "timing.wizard.step_title",
                number=step + 1,
                total=len(STEP_KEYS),
                title=tr(STEP_KEYS[step]),
            )
        )
        self.back_button.setEnabled(step > TRACK)
        self.next_button.setVisible(step < SAVE)
        self.save_button.setVisible(step == SAVE)
        self._refresh()

    def _refresh(self) -> None:
        tr = self.translator
        entries = self.draft.entries
        ids = list(range(len(entries)))
        names = [entry_text(tr, entries, e) for e in entries]
        fill_table(
            self.positions_table,
            [
                (str(i), name, type_text(tr, e.type, entries.index(e)))
                for i, (name, e) in enumerate(zip(names, entries, strict=True), start=1)
            ],
            ids,
        )
        fill_table(
            self.sensors_table,
            [
                (name, e.sensor_id, e.hardware_id or "", yes_no(tr, e.active))
                for name, e in zip(names, entries, strict=True)
            ],
            ids,
        )
        fill_table(
            self.order_table,
            [
                (str(i), name, e.sensor_id)
                for i, (name, e) in enumerate(zip(names, entries, strict=True), start=1)
            ],
            ids,
        )
        fill_table(
            self.summary_table,
            [
                (str(i), name, e.sensor_id, e.hardware_id or "", yes_no(tr, e.active))
                for i, (name, e) in enumerate(zip(names, entries, strict=True), start=1)
            ],
            ids,
            keep_selection=False,
        )

    def _selected(self, table: QTableWidget) -> str | None:
        index = selected_id(table)
        if index is None or not 0 <= index < len(self.draft.entries):
            return None
        return self.draft.entries[index].position_id

    def _index_of(self, position_id: str) -> int:
        return self.draft.entries.index(self.draft.entry(position_id))

    @staticmethod
    def _select(table: QTableWidget, index: int) -> None:
        table.selectRow(index)
