"""Live view of the running race. Shows what the engine reports; computes nothing itself."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from slot_racing.core.clock import format_duration
from slot_racing.core.domain import RaceStatus
from slot_racing.core.i18n import Translator
from slot_racing.modules.races.runner import RaceController, RaceRunner
from slot_racing.uikit import StatusLabel, describe_error, fill_table, heading, make_table
from slot_racing.uikit.errors import is_expected

logger = logging.getLogger(__name__)

REFRESH_INTERVAL_MS = 100


class LiveRaceView(QWidget):
    race_over = Signal(int)

    def __init__(self, translator: Translator, controller: RaceController) -> None:
        super().__init__()
        self.translator = translator
        self._controller = controller
        self._runner: RaceRunner | None = None
        self._announced_end = False
        self.confirm: Callable[[str], bool] = lambda text: (
            QMessageBox.question(self, translator.translate("common.confirm"), text)
            == QMessageBox.StandardButton.Yes
        )
        tr = translator.translate

        self.name_label = heading(tr("race.live.title"))
        self.name_label.setObjectName("live-name")
        self.track_label = QLabel()
        self.track_label.setObjectName("live-track")
        self.status_label = QLabel()
        self.status_label.setObjectName("live-status")
        self.time_label = QLabel()
        self.time_label.setObjectName("live-time")
        self.table = make_table(
            [
                tr("race.column.position"),
                tr("race.column.lane"),
                tr("race.column.driver"),
                tr("race.column.vehicle"),
                tr("race.column.current_lap"),
                tr("race.column.last_lap"),
                tr("race.column.total_time"),
                tr("race.column.laps_done"),
                tr("race.column.best_lap"),
            ],
            "live-table",
        )
        self.warning = StatusLabel("live-warning")
        self.pause_button = QPushButton(tr("race.live.pause"))
        self.pause_button.setObjectName("live-pause")
        self.stop_button = QPushButton(tr("race.live.stop"))
        self.stop_button.setObjectName("live-stop")
        self.results_button = QPushButton(tr("race.live.results"))
        self.results_button.setObjectName("live-results")

        info = QHBoxLayout()
        for label in (self.track_label, self.status_label, self.time_label):
            info.addWidget(label)
        info.addStretch(1)
        buttons = QHBoxLayout()
        for button in (self.pause_button, self.stop_button, self.results_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addWidget(self.name_label)
        layout.addLayout(info)
        layout.addLayout(buttons)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.warning)

        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_INTERVAL_MS)
        self._timer.timeout.connect(self.refresh)
        self.pause_button.clicked.connect(lambda: self.toggle_pause())
        self.stop_button.clicked.connect(lambda: self.stop_race())
        self.results_button.clicked.connect(lambda: self._show_results())
        self._update_buttons()

    @property
    def runner(self) -> RaceRunner | None:
        return self._runner

    def show_runner(self, runner: RaceRunner) -> None:
        self._runner = runner
        self._announced_end = False
        self.warning.clear_message()
        self.refresh()
        if runner.is_active:
            self._timer.start()

    def refresh(self) -> None:
        """Let the timing source deliver due events, then redraw from the runner's snapshot."""
        runner = self._runner
        if runner is None:
            return
        self._guard(runner.tick)
        snapshot = runner.snapshot()
        tr = self.translator.translate
        status_text = tr(f"race.status.{snapshot.status.value}")
        if snapshot.status is RaceStatus.FINISHED and snapshot.aborted:
            status_text = tr("race.status.aborted")
        self.name_label.setText(snapshot.name)
        self.track_label.setText(f"{tr('race.live.track')}: {snapshot.track_name}")
        self.status_label.setText(f"{tr('race.live.status')}: {status_text}")
        self.time_label.setText(f"{tr('race.live.time')}: {format_duration(snapshot.elapsed_ns)}")
        fill_table(
            self.table,
            [
                (
                    str(row.position),
                    str(row.lane),
                    row.driver_label,
                    row.vehicle_label,
                    f"{row.current_lap}/{snapshot.laps}",
                    format_duration(row.last_lap_ns),
                    format_duration(row.total_time_ns),
                    str(row.laps_completed),
                    format_duration(row.best_lap_ns),
                )
                for row in snapshot.rows
            ],
            [row.lane for row in snapshot.rows],
            keep_selection=False,
        )
        if snapshot.source_errors:
            self.warning.show_error(
                self.translator.format(
                    "race.live.warning", detail="; ".join(snapshot.source_errors)
                )
            )
        self.pause_button.setText(
            tr("race.live.resume" if snapshot.status is RaceStatus.PAUSED else "race.live.pause")
        )
        self._update_buttons()
        if snapshot.status is RaceStatus.FINISHED:
            self._timer.stop()
            if not self._announced_end:
                self._announced_end = True
                self.race_over.emit(snapshot.race_id)

    def toggle_pause(self) -> None:
        runner = self._runner
        if runner is None:
            return
        if runner.status is RaceStatus.PAUSED:
            self._guard(runner.resume)
        elif runner.status is RaceStatus.RUNNING:
            self._guard(runner.pause)
        self.refresh()

    def stop_race(self) -> None:
        runner = self._runner
        if runner is None or not runner.is_active:
            return
        if self.confirm(self.translator.translate("race.live.confirm_stop")):
            self._guard(runner.stop)
            self.refresh()

    def _show_results(self) -> None:
        if self._runner is not None:
            self.race_over.emit(self._runner.race.id)

    def _update_buttons(self) -> None:
        runner = self._runner
        active = runner is not None and runner.is_active
        self.pause_button.setEnabled(active)
        self.stop_button.setEnabled(active)
        self.results_button.setEnabled(runner is not None and runner.is_finished)

    def _guard(self, action: Callable[[], None]) -> None:
        try:
            action()
        except Exception as error:
            if not is_expected(error):
                logger.exception("Live race action failed")
            self.warning.show_error(describe_error(self.translator, error))
