"""Live view of the running race. The HUD arranges existing data; it computes nothing itself."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QMessageBox, QVBoxLayout, QWidget

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
from slot_racing.modules.races.hud import (
    HudConfiguration,
    HudConfigurationStore,
    default_hud_configuration,
)
from slot_racing.modules.races.runner import LiveRow, RaceController, RaceRunner, RaceSnapshot
from slot_racing.modules.races.ui.formatting import participant_status_key, start_number_text
from slot_racing.modules.races.ui.hud_stage import HudStage
from slot_racing.modules.races.ui.hud_widgets import (
    BestLapWidget,
    DriverHighlightWidget,
    LapProgressWidget,
    LastLapWidget,
    LiveRankingWidget,
    RaceClockWidget,
    RaceControlsWidget,
    RaceHeaderWidget,
    RaceMessageWidget,
    RaceStatusWidget,
)
from slot_racing.uikit import describe_error, fill_table, provider_label, selected_id
from slot_racing.uikit.errors import is_expected
from slot_racing.uikit.theme import configure_page, set_tone

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

    def __init__(
        self,
        translator: Translator,
        controller: RaceController,
        store: HudConfigurationStore | None = None,
    ) -> None:
        super().__init__()
        self.translator = translator
        self._controller = controller
        self._store = store
        self._runner: RaceRunner | None = None
        self._snapshot: RaceSnapshot | None = None
        self._announced_end = False
        self._busy = False
        self._last_lap: tuple[int, int] | None = None
        self._announced_finishers: set[int] = set()
        self._message = ""
        self._unsubscribe_hud: Callable[[], None] | None = None
        self.confirm: Callable[[str], bool] = lambda text: (
            QMessageBox.question(self, translator.translate("common.confirm"), text)
            == QMessageBox.StandardButton.Yes
        )

        self.header = RaceHeaderWidget(translator)
        self.clock = RaceClockWidget(translator)
        self.progress = LapProgressWidget(translator)
        self.ranking = LiveRankingWidget(translator)
        self.highlight = DriverHighlightWidget(translator)
        self.last_lap = LastLapWidget(translator)
        self.best_lap = BestLapWidget(translator)
        self.status = RaceStatusWidget(translator)
        self.messages = RaceMessageWidget(translator)
        self.controls = RaceControlsWidget(translator)

        self.name_label = self.header.name_label
        self.track_label = self.header.track_label
        self.provider_label = self.header.provider_label
        self.status_label = self.status.status_label
        self.time_label = self.clock.time_label
        self.laps_label = self.progress.laps_label
        self.table = self.ranking.table
        self.detail_title = self.highlight.detail_title
        self.detail = self.highlight.detail
        self.warning = self.messages.warning
        self.pause_button = self.controls.pause_button
        self.stop_button = self.controls.stop_button
        self.results_button = self.controls.results_button
        self.back_button = self.controls.back_button

        self.stage = HudStage()
        self.stage.bind(self.header.widget_id, self.header)
        self.stage.bind(self.clock.widget_id, self.clock)
        self.stage.bind(self.progress.widget_id, self.progress)
        self.stage.bind(self.ranking.widget_id, self.ranking)
        self.stage.bind(self.highlight.widget_id, self.highlight)
        self.stage.bind(self.last_lap.widget_id, self.last_lap)
        self.stage.bind(self.best_lap.widget_id, self.best_lap)
        self.stage.bind(self.status.widget_id, self.status)
        self.stage.bind(self.messages.widget_id, self.messages)
        self.stage.bind(self.controls.widget_id, self.controls)

        layout = QVBoxLayout(self)
        configure_page(layout)
        layout.addWidget(self.stage, 1)

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
        initial = default_hud_configuration() if store is None else store.load()
        self.apply_configuration(initial)
        if store is not None:
            self._unsubscribe_hud = store.add_listener(self.apply_configuration)
        self._update_buttons()

    @property
    def runner(self) -> RaceRunner | None:
        return self._runner

    def apply_configuration(self, configuration: HudConfiguration) -> None:
        """Move the panels. Hidden panels stay up to date and simply are not shown."""
        self.stage.apply(configuration)

    def show_runner(self, runner: RaceRunner) -> None:
        self._runner = runner
        self._snapshot = None
        self._announced_end = False
        self._last_lap = None
        self._announced_finishers = set()
        self._message = ""
        self.warning.clear_message()
        self.refresh()
        if runner.is_active:
            self._timer.start()

    def refresh(self) -> None:
        """Poll the timing source, then redraw.

        The timer is the host poll for providers such as the simulation. It is not a status
        poll: race events redraw the same snapshot as soon as the engine publishes them.
        The clock reads ``snapshot.elapsed_ns``, the race engine's own time.
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
            self._remember(event)
            race_id = getattr(event, "race_id", None)
            if isinstance(race_id, int):
                self._redraw_for(RaceId(race_id))

        return self._controller.events.subscribe(event_type, handler)

    def _remember(self, event: Event) -> None:
        """Keep the latest message and the latest completed lap. Both come from race events."""
        tr = self.translator.translate
        if isinstance(event, LapCompleted):
            self._last_lap = (event.lane, event.lap_time_ns)
        elif isinstance(event, RaceStarted):
            self._message = tr("hud.message.started")
        elif isinstance(event, RacePaused):
            self._message = tr("hud.message.paused")
        elif isinstance(event, RaceResumed):
            self._message = tr("hud.message.resumed")
        elif isinstance(event, RaceFinished):
            key = "hud.message.aborted" if event.aborted else "hud.message.finished"
            self._message = tr(key)

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
        self.header.header_status.setText(f"{tr('race.live.status')}: {status_text}")
        provider = provider_label(self.translator, snapshot.timing_provider)
        self.provider_label.setText(f"{tr('race.live.provider')}: {provider}")
        self.status_label.setText(f"● {status_text}")
        set_tone(self.status_label, _status_tone(snapshot))
        self.time_label.setText(format_duration(snapshot.elapsed_ns))
        self._show_progress(snapshot)
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
        self._show_laps(snapshot)
        self._announce_finishers(snapshot)
        if not self._message:
            self._message = _status_message(tr, snapshot)
        self.messages.message_label.setText(self._message or "-")
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

    def _show_progress(self, snapshot: RaceSnapshot) -> None:
        leader = min(snapshot.rows, key=lambda row: row.position, default=None)
        current = 0 if leader is None else leader.current_lap
        self.laps_label.setText(f"{current} / {snapshot.laps}")
        completed = max((row.laps_completed for row in snapshot.rows), default=0)
        target = max(snapshot.laps, 1)
        self.progress.progress.setRange(0, target)
        self.progress.progress.setValue(min(completed, target))
        self.progress.progress.setFormat(f"{completed}/{snapshot.laps}")

    def _show_laps(self, snapshot: RaceSnapshot) -> None:
        if self._last_lap is None:
            self.last_lap.value_label.setText("-")
        else:
            lane, lap_time_ns = self._last_lap
            name = _name_on_lane(snapshot, lane)
            self.last_lap.value_label.setText(f"{name}\n{format_duration(lap_time_ns)}")
        timed = [row for row in snapshot.rows if row.best_lap_ns is not None]
        if not timed:
            self.best_lap.value_label.setText("-")
            return
        best = min(timed, key=lambda row: (row.best_lap_ns or 0, row.position))
        self.best_lap.value_label.setText(
            f"{best.driver_label}\n{format_duration(best.best_lap_ns)}"
        )

    def _announce_finishers(self, snapshot: RaceSnapshot) -> None:
        if snapshot.status is RaceStatus.FINISHED:
            return
        for row in snapshot.rows:
            if row.finished and row.lane not in self._announced_finishers:
                self._announced_finishers.add(row.lane)
                self._message = self.translator.format(
                    "hud.message.driver_finished", driver=row.driver_label
                )

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
        if self._unsubscribe_hud is not None:
            self._unsubscribe_hud()
            self._unsubscribe_hud = None


def _status_tone(snapshot: RaceSnapshot) -> str:
    if snapshot.status is RaceStatus.FINISHED and snapshot.aborted:
        return "error"
    if snapshot.status is RaceStatus.RUNNING:
        return "ok"
    if snapshot.status is RaceStatus.PAUSED:
        return "warn"
    if snapshot.status is RaceStatus.FINISHED:
        return "info"
    return "muted"


def _status_message(translate: Callable[[str], str], snapshot: RaceSnapshot) -> str:
    if snapshot.status is RaceStatus.RUNNING:
        return translate("hud.message.started")
    if snapshot.status is RaceStatus.PAUSED:
        return translate("hud.message.paused")
    if snapshot.status is RaceStatus.FINISHED and snapshot.aborted:
        return translate("hud.message.aborted")
    if snapshot.status is RaceStatus.FINISHED:
        return translate("hud.message.finished")
    return ""


def _name_on_lane(snapshot: RaceSnapshot, lane: int) -> str:
    row = next((item for item in snapshot.rows if item.lane == lane), None)
    return str(lane) if row is None else row.driver_label
