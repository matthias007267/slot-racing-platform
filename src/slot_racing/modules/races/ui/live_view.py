"""Live view of the running race. Shows what the engine reports; computes nothing itself."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.clock import format_duration
from slot_racing.core.domain import RaceId, RaceStatus
from slot_racing.core.events import (
    Event,
    LapCompleted,
    LapStarted,
    RaceFinished,
    RacePaused,
    RaceResumed,
    RaceStarted,
    RaceStarting,
    SectorCompleted,
    Subscription,
    WinnerDetermined,
)
from slot_racing.core.i18n import Translator
from slot_racing.modules.races.runner import LiveRow, RaceController, RaceRunner, RaceSnapshot
from slot_racing.modules.races.ui.formatting import participant_status_key, start_number_text
from slot_racing.uikit import (
    StatusLabel,
    describe_error,
    fill_table,
    heading,
    make_table,
    provider_label,
    selected_id,
)
from slot_racing.uikit.errors import is_expected

logger = logging.getLogger(__name__)

REFRESH_INTERVAL_MS = 100

# Race events that change what the live view shows. Sensor events stay inside the timing
# provider; the view only redraws once the engine has published one of these.
_RACE_EVENTS = (
    RaceStarting,
    RaceStarted,
    RacePaused,
    RaceResumed,
    RaceFinished,
    LapStarted,
    LapCompleted,
    SectorCompleted,
    WinnerDetermined,
)


class LiveRaceView(QWidget):
    race_over = Signal(int)
    back_requested = Signal()

    def __init__(self, translator: Translator, controller: RaceController) -> None:
        super().__init__()
        self.translator = translator
        self._controller = controller
        self._runner: RaceRunner | None = None
        self._snapshot: RaceSnapshot | None = None
        self._announced_end = False
        self._busy = False
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
        self.provider_label = QLabel()
        self.provider_label.setObjectName("live-provider")
        self.laps_label = QLabel()
        self.laps_label.setObjectName("live-laps")
        self.table = make_table(
            [
                tr("race.column.position"),
                tr("race.column.driver"),
                tr("race.column.vehicle"),
                tr("race.column.start_number"),
                tr("race.column.lane"),
                tr("race.column.laps_done"),
                tr("race.column.current_lap"),
                tr("race.column.last_lap"),
                tr("race.column.best_lap"),
                tr("race.column.total_time"),
                tr("race.column.progress"),
                tr("race.column.status"),
            ],
            "live-table",
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.detail_title = QLabel(tr("race.live.detail"))
        self.detail_title.setObjectName("live-detail-title")
        self.detail = QLabel(tr("race.live.detail_empty"))
        self.detail.setObjectName("live-detail")
        self.detail.setWordWrap(True)
        self.warning = StatusLabel("live-warning")
        self.pause_button = QPushButton(tr("race.live.pause"))
        self.pause_button.setObjectName("live-pause")
        self.stop_button = QPushButton(tr("race.live.stop"))
        self.stop_button.setObjectName("live-stop")
        self.results_button = QPushButton(tr("race.live.results"))
        self.results_button.setObjectName("live-results")
        self.back_button = QPushButton(tr("race.live.back"))
        self.back_button.setObjectName("live-back")

        info = QHBoxLayout()
        for label in (
            self.track_label,
            self.status_label,
            self.time_label,
            self.provider_label,
            self.laps_label,
        ):
            info.addWidget(label)
        info.addStretch(1)
        buttons = QHBoxLayout()
        for button in (self.pause_button, self.stop_button, self.results_button, self.back_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addWidget(self.name_label)
        layout.addLayout(info)
        layout.addLayout(buttons)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.detail_title)
        layout.addWidget(self.detail)
        layout.addWidget(self.warning)

        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_INTERVAL_MS)
        self._timer.timeout.connect(self.refresh)
        self.pause_button.clicked.connect(lambda: self.toggle_pause())
        self.stop_button.clicked.connect(lambda: self.stop_race())
        self.results_button.clicked.connect(lambda: self._show_results())
        self.back_button.clicked.connect(self.back_requested.emit)
        self.table.itemSelectionChanged.connect(self._show_detail)
        self._subscriptions = [self._listen(event_type) for event_type in _RACE_EVENTS]
        self.destroyed.connect(lambda *_args: self._unsubscribe())
        self._update_buttons()

    @property
    def runner(self) -> RaceRunner | None:
        return self._runner

    def show_runner(self, runner: RaceRunner) -> None:
        self._runner = runner
        self._snapshot = None
        self._announced_end = False
        self.warning.clear_message()
        self.refresh()
        if runner.is_active:
            self._timer.start()

    def refresh(self) -> None:
        """Poll the timing source, then redraw.

        The timer is the host poll for providers such as the simulation. It is not a status
        poll: race events redraw the same snapshot as soon as the engine publishes them.
        """
        runner = self._runner
        if runner is None or self._busy:
            return
        self._busy = True
        try:
            self._guard(runner.tick)
            self._render()
        finally:
            self._busy = False

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

    def _listen[E: Event](self, event_type: type[E]) -> Subscription:
        def handler(event: E) -> None:
            race_id = getattr(event, "race_id", None)
            if isinstance(race_id, int):
                self._redraw_for(RaceId(race_id))

        return self._controller.events.subscribe(event_type, handler)

    def _redraw_for(self, race_id: RaceId) -> None:
        runner = self._runner
        if runner is None or race_id != runner.race.id or self._busy:
            return
        self._busy = True
        try:
            self._render()
        finally:
            self._busy = False

    def _render(self) -> None:
        runner = self._runner
        if runner is None:
            return
        snapshot = runner.snapshot()
        self._snapshot = snapshot
        tr = self.translator.translate
        status_text = tr(f"race.status.{snapshot.status.value}")
        if snapshot.status is RaceStatus.FINISHED and snapshot.aborted:
            status_text = tr("race.status.aborted")
        self.name_label.setText(snapshot.name)
        self.track_label.setText(f"{tr('race.live.track')}: {snapshot.track_name}")
        self.status_label.setText(f"{tr('race.live.status')}: {status_text}")
        self.time_label.setText(f"{tr('race.live.time')}: {format_duration(snapshot.elapsed_ns)}")
        provider = provider_label(self.translator, snapshot.timing_provider)
        self.provider_label.setText(f"{tr('race.live.provider')}: {provider}")
        self.laps_label.setText(f"{tr('race.live.laps')}: {snapshot.laps}")
        ended = snapshot.status is RaceStatus.FINISHED
        paused = snapshot.status is RaceStatus.PAUSED
        fill_table(
            self.table,
            [self._cells(snapshot, row, ended=ended, paused=paused) for row in snapshot.rows],
            [row.lane for row in snapshot.rows],
        )
        if self.table.rowCount() and selected_id(self.table) is None:
            self.table.selectRow(0)
        self._show_detail()
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

    def _cells(
        self, snapshot: RaceSnapshot, row: LiveRow, *, ended: bool, paused: bool
    ) -> tuple[str, ...]:
        tr = self.translator.translate
        return (
            str(row.position),
            row.driver_label,
            row.vehicle_label,
            start_number_text(row.start_number),
            str(row.lane),
            str(row.laps_completed),
            f"{row.current_lap}/{snapshot.laps}",
            format_duration(row.last_lap_ns),
            format_duration(row.best_lap_ns),
            format_duration(row.total_time_ns),
            f"{row.laps_completed}/{snapshot.laps}",
            tr(participant_status_key(finished=row.finished, paused=paused, ended=ended)),
        )

    def _show_detail(self) -> None:
        snapshot = self._snapshot
        tr = self.translator.translate
        lane = None if snapshot is None else selected_id(self.table)
        row = (
            None
            if snapshot is None
            else next((item for item in snapshot.rows if item.lane == lane), None)
        )
        if snapshot is None or row is None:
            self.detail.setText(tr("race.live.detail_empty"))
            return
        times = ", ".join(format_duration(lap) for lap in row.lap_times_ns) or "-"
        status = tr(
            participant_status_key(
                finished=row.finished,
                paused=snapshot.status is RaceStatus.PAUSED,
                ended=snapshot.status is RaceStatus.FINISHED,
            )
        )
        self.detail.setText(
            "\n".join(
                (
                    f"{tr('race.column.driver')}: {row.driver_label}",
                    f"{tr('race.column.vehicle')}: {row.vehicle_label}",
                    f"{tr('race.column.start_number')}: {start_number_text(row.start_number)}",
                    f"{tr('race.column.position')}: {row.position}",
                    f"{tr('race.column.lane')}: {row.lane}",
                    f"{tr('race.column.current_lap')}: {row.current_lap}/{snapshot.laps}",
                    f"{tr('race.column.laps_done')}: {row.laps_completed}",
                    f"{tr('race.column.last_lap')}: {format_duration(row.last_lap_ns)}",
                    f"{tr('race.column.best_lap')}: {format_duration(row.best_lap_ns)}",
                    f"{tr('race.live.lap_times')}: {times}",
                    f"{tr('race.column.status')}: {status}",
                )
            )
        )

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

    def _unsubscribe(self) -> None:
        for subscription in self._subscriptions:
            subscription.cancel()
        self._subscriptions.clear()
