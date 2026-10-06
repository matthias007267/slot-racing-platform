"""Live view of the running race. The HUD arranges existing data; it computes nothing itself."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QCloseEvent, QHideEvent
from PySide6.QtWidgets import QMessageBox, QVBoxLayout, QWidget

from slot_racing.core.clock import format_duration
from slot_racing.core.domain import RaceId, RaceMode, RaceStatus
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
from slot_racing.modules.races.service import RaceService
from slot_racing.modules.races.time_trial_board import build_time_trial_board
from slot_racing.modules.races.ui.formatting import (
    EMPTY_DISPLAY,
    format_lap_progress,
    format_progress_cell,
    participant_status_key,
    start_number_text,
)
from slot_racing.modules.races.ui.heat_gate import HeatGate
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
from slot_racing.modules.races.ui.lane_board import LiveLaneBoard
from slot_racing.modules.races.ui.start_cue import (
    StartCue,
    StartCueStep,
    StartPhase,
    uses_start_cue,
)
from slot_racing.modules.races.ui.start_lights import StartLightWidget
from slot_racing.modules.races.ui.time_trial_board_view import TimeTrialBoardView
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
        service: RaceService | None = None,
    ) -> None:
        super().__init__()
        self.translator = translator
        self._controller = controller
        self._store = store
        self._service = service
        self._runner: RaceRunner | None = None
        self._snapshot: RaceSnapshot | None = None
        self._announced_end = False
        self._busy = False
        self._last_lap: tuple[int, int] | None = None
        self._lap_notice: tuple[int, int] | None = None
        self._announced_finishers: set[int] = set()
        self._message = ""
        self._cue: StartCue | None = None
        self.cue_interval_ms = 1000
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
        self.resume_button = self.controls.resume_button
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

        self.board = TimeTrialBoardView(translator)
        self.board.hide()
        self.lane_board = LiveLaneBoard(translator)
        self.heat_gate = HeatGate(translator)

        self.start_lights = StartLightWidget()

        layout = QVBoxLayout(self)
        configure_page(layout)
        layout.setSpacing(0)
        layout.addWidget(self.start_lights)
        layout.addWidget(self.lane_board)
        layout.addWidget(self.heat_gate)
        layout.addWidget(self.stage, 1)
        layout.addWidget(self.board, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_INTERVAL_MS)
        self._timer.timeout.connect(self.refresh)
        self.pause_button.clicked.connect(self.pause_race)
        self.resume_button.clicked.connect(self.resume_race)
        self.stop_button.clicked.connect(lambda: self.stop_race())
        self.results_button.clicked.connect(lambda: self._show_results())
        self.back_button.clicked.connect(self._leave)
        self.board.pause_button.clicked.connect(self.pause_race)
        self.board.resume_button.clicked.connect(self.resume_race)
        self.board.stop_button.clicked.connect(lambda: self.stop_race())
        self.board.results_button.clicked.connect(lambda: self._show_results())
        self.board.back_button.clicked.connect(self._leave)
        self.heat_gate.start_requested.connect(self._start_next_heat)
        self.heat_gate.postpone_requested.connect(self._postpone_driver)
        self.heat_gate.disqualify_requested.connect(self._disqualify_driver)
        self.table.itemSelectionChanged.connect(self._show_detail)
        self._subscriptions = [self._listen(event_type) for event_type in _RACE_EVENTS]
        self.destroyed.connect(lambda *_args: self._release())
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
        self._cancel_cue()
        self._runner = runner
        self._snapshot = None
        self._announced_end = False
        self.heat_gate.hide()
        self._last_lap = None
        self._lap_notice = None
        self._announced_finishers = set()
        self._message = ""
        self.messages.clear_warning()
        self.refresh()
        if runner.is_active:
            self._timer.start()

    def open_for_start(self, runner: RaceRunner) -> None:
        """Show the race. A camera race shows the start lights before the engine starts."""
        self.show_runner(runner)
        if uses_start_cue(runner.race.timing_provider) and not runner.is_active:
            self.begin_start_cue()

    def begin_start_cue(self, interval_ms: int | None = None) -> None:
        """Light the five lamps, then start the race when they go out."""
        self._cancel_cue()
        interval = self.cue_interval_ms if interval_ms is None else interval_ms
        cue = StartCue(self._commit_start, interval_ms=interval, parent=self)
        cue.changed.connect(self._show_cue)
        cue.finished.connect(self._hide_cue)
        self._cue = cue
        cue.begin()

    def advance_start_cue(self) -> None:
        """Move the lights on. The race starts only when they go out."""
        cue = self._cue
        if cue is not None:
            cue.advance()

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        """Leaving the live view must not let a hidden light sequence start the race."""
        self._cancel_cue()
        super().hideEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._cancel_cue()
        super().closeEvent(event)

    def _show_cue(self, step: object) -> None:
        if not isinstance(step, StartCueStep):
            return
        self.start_lights.show_lights(step.lit_lights, go=step.phase is StartPhase.START_SIGNAL)

    def _hide_cue(self) -> None:
        self.start_lights.clear()

    def _leave(self) -> None:
        self._cancel_cue()
        self.back_requested.emit()

    def _release(self) -> None:
        self._cancel_cue()
        self._unsubscribe()

    def _cancel_cue(self) -> None:
        cue = self._cue
        self._cue = None
        if cue is not None:
            cue.blockSignals(True)
            cue.stop()
        self._hide_cue()

    def _commit_start(self) -> None:
        """Start signal: hand control to the race engine and start its clock."""
        runner = self._runner
        if runner is None or runner.status is not RaceStatus.CREATED:
            return
        try:
            self._controller.start_prepared()
        except Exception as error:
            if not is_expected(error):
                logger.exception("Could not start the race after the countdown")
            self.messages.show_warning(describe_error(self.translator, error))
            self._cancel_cue()
            return
        self._timer.start()
        self.refresh()

    def refresh(self) -> None:
        """Forward events a source has already produced, then redraw.

        A camera detects on its own worker. This timer is not that detector. It delivers
        the crossings the worker stored, and it still drives sources that have no thread
        of their own. It does not wait for either of them. Race events redraw the same
        snapshot as soon as the engine publishes them. The clock reads ``snapshot.elapsed_ns``,
        the race engine's own time.
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

    def pause_race(self) -> None:
        runner = self._runner
        if runner is None or runner.status is not RaceStatus.RUNNING:
            return
        self._guard(runner.pause)
        self.refresh()

    def resume_race(self) -> None:
        runner = self._runner
        if runner is None or runner.status is not RaceStatus.PAUSED:
            return
        self._guard(runner.resume)
        self.refresh()

    def toggle_pause(self) -> None:
        """Pause or resume through the runner. Kept for callers that use one action."""
        runner = self._runner
        if runner is None:
            return
        if runner.status is RaceStatus.PAUSED:
            self.resume_race()
        elif runner.status is RaceStatus.RUNNING:
            self.pause_race()

    def stop_race(self) -> None:
        runner = self._runner
        if runner is None or not runner.is_active:
            return
        confirm_key = (
            "race.live.confirm_stop_time_trial"
            if runner.race.mode is RaceMode.TIME_TRIAL
            else "race.live.confirm_stop"
        )
        if self.confirm(self.translator.translate(confirm_key)):
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
            self._lap_notice = (event.lane, event.lap_number)
            return
        if isinstance(event, RaceStarted):
            self._message = tr("hud.message.started")
        elif isinstance(event, RacePaused):
            self._message = tr("hud.message.paused")
        elif isinstance(event, RaceResumed):
            self._message = tr("hud.message.resumed")
        elif isinstance(event, RaceFinished):
            key = "hud.message.aborted" if event.aborted else "hud.message.finished"
            self._message = tr(key)
        else:
            return
        # A later race-status event wins over a lap line from the same tick.
        self._lap_notice = None

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
        self.track_label.setText(snapshot.track_name)
        self.header.header_status.setText(status_text)
        self.header.participants_label.setText(
            self.translator.format("hud.header.participants", count=len(snapshot.rows))
        )
        provider = provider_label(self.translator, snapshot.timing_provider)
        self.provider_label.setText(provider)
        self.status_label.setText(f"● {status_text}")
        tone = _status_tone(snapshot)
        set_tone(self.status_label, tone)
        self.status.emphasize(tone)
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
        self.ranking.present()
        self._show_detail()
        self._show_laps(snapshot)
        lap_message = self._consume_lap_message(snapshot)
        self._announce_finishers(snapshot)
        if lap_message:
            self._message = lap_message
        if not self._message:
            self._message = _status_message(tr, snapshot)
        self.messages.message_label.setText(self._message or EMPTY_DISPLAY)
        if snapshot.source_errors:
            self.messages.show_warning(
                self.translator.format(
                    "race.live.warning", detail="; ".join(snapshot.source_errors)
                )
            )
        time_trial = runner.race.mode is RaceMode.TIME_TRIAL
        self.stage.setVisible(not time_trial)
        self.board.setVisible(time_trial)
        if time_trial:
            self._show_time_trial(runner, snapshot, status_text, tone)
        self.lane_board.show_snapshot(
            snapshot, lane_count=runner.race.lane_count, mode=runner.race.mode
        )
        self._update_buttons()
        if snapshot.status is not RaceStatus.FINISHED:
            self.heat_gate.hide()
            return
        self._timer.stop()
        # The engine session can end while another heat is still planned, or while the next
        # heat has already been started and this runner has not been replaced yet.
        if not self._stored_race_is_over(snapshot.race_id):
            if self._pending_heats(snapshot.race_id) and not snapshot.aborted:
                self._show_heat_gate(snapshot.race_id)
            return
        self.heat_gate.hide()
        if not self._announced_end:
            self._announced_end = True
            self.race_over.emit(snapshot.race_id)

    def _show_time_trial(
        self, runner: RaceRunner, snapshot: RaceSnapshot, status_text: str, tone: str
    ) -> None:
        """Redraw the time-trial board from stored measurements. The lap HUD stays hidden."""
        race = runner.race
        measurements = (
            []
            if self._service is None or race.track_id is None
            else self._service.list_time_measurements(track_id=race.track_id)
        )
        self.board.show_board(
            build_time_trial_board(
                lane_count=race.lane_count,
                race_id=race.id,
                measurements=measurements,
                participants=race.participants,
            )
        )
        warning = ""
        if snapshot.source_errors:
            warning = self.translator.format(
                "race.live.warning", detail="; ".join(snapshot.source_errors)
            )
        self.board.show_status(
            name=snapshot.name,
            status=status_text,
            tone=tone,
            elapsed_ns=snapshot.elapsed_ns,
            warning=warning,
        )

    def _show_progress(self, snapshot: RaceSnapshot) -> None:
        leader = min(snapshot.rows, key=lambda row: row.position, default=None)
        current = 0 if leader is None else leader.current_lap
        completed = max((row.laps_completed for row in snapshot.rows), default=0)
        self.progress.show_counts(current, snapshot.laps, completed)

    def _consume_lap_message(self, snapshot: RaceSnapshot) -> str:
        notice = self._lap_notice
        if notice is None:
            return ""
        self._lap_notice = None
        lane, lap_number = notice
        return self.translator.format(
            "hud.message.lap_completed",
            driver=_name_on_lane(snapshot, lane),
            lap=lap_number,
        )

    def _show_laps(self, snapshot: RaceSnapshot) -> None:
        if self._last_lap is None:
            self.last_lap.value_label.setText(EMPTY_DISPLAY)
        else:
            lane, lap_time_ns = self._last_lap
            name = _name_on_lane(snapshot, lane)
            self.last_lap.value_label.setText(f"{name}\n{format_duration(lap_time_ns)}")
        timed = [row for row in snapshot.rows if row.best_lap_ns is not None]
        if not timed:
            self.best_lap.value_label.setText(EMPTY_DISPLAY)
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
            format_progress_cell(row.current_lap, snapshot.laps),
            format_duration(row.last_lap_ns),
            format_duration(row.best_lap_ns),
            format_duration(row.total_time_ns),
            format_progress_cell(row.laps_completed, snapshot.laps),
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
            self.highlight.clear_driver()
            self.detail.setText(tr("race.live.detail_empty"))
            return
        self.highlight.show_driver(
            position=f"P{row.position}",
            name=row.driver_label,
            vehicle=row.vehicle_label,
            lap=format_lap_progress(row.current_lap, snapshot.laps),
            last=_shown_time(row.last_lap_ns),
            best=_shown_time(row.best_lap_ns),
        )
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
                    f"{tr('race.column.current_lap')}: "
                    f"{format_progress_cell(row.current_lap, snapshot.laps)}",
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
        status = None if runner is None else runner.status
        running = status is RaceStatus.RUNNING
        paused = status is RaceStatus.PAUSED
        active = runner is not None and runner.is_active
        finished = runner is not None and runner.is_finished
        self.pause_button.setEnabled(running)
        self.resume_button.setEnabled(paused)
        self.stop_button.setEnabled(active)
        self.results_button.setEnabled(finished)
        self.back_button.setEnabled(True)
        self.board.set_controls(pause=running, resume=paused, stop=active, results=finished)

    def _stored_race_is_over(self, race_id: RaceId) -> bool:
        if self._service is None:
            return True
        race = self._service.get_race(race_id)
        return race is not None and race.is_over

    def _pending_heats(self, race_id: RaceId) -> bool:
        if self._service is None:
            return False
        return self._service.has_pending_heats(race_id)

    def _show_heat_gate(self, race_id: RaceId) -> None:
        if self._service is None:
            return
        briefing = self._service.heat_briefing(race_id)
        if briefing is None:
            self.heat_gate.hide()
            return
        self.heat_gate.show_briefing(briefing)

    def _start_next_heat(self) -> None:
        runner = self._runner
        if runner is None:
            return
        try:
            if uses_start_cue(runner.race.timing_provider):
                started = self._controller.prepare_race(runner.race.id)
            else:
                started = self._controller.start_race(runner.race.id)
        except Exception as error:
            if not is_expected(error):
                logger.exception("Could not start the next heat")
            self.messages.show_warning(describe_error(self.translator, error))
            return
        self.open_for_start(started)

    def _postpone_driver(self, participant_id: int) -> None:
        self._change_heat(
            lambda race_id: self._require_service().postpone_driver(race_id, participant_id)
        )

    def _disqualify_driver(self, participant_id: int) -> None:
        name = self.heat_gate.driver_combo.currentText()
        if not self.confirm(self.translator.format("race.heat.confirm_dq", driver=name)):
            return
        runner = self._runner
        if runner is None or self._service is None:
            return
        try:
            finished = self._service.disqualify_driver(runner.race.id, participant_id)
        except Exception as error:
            if not is_expected(error):
                logger.exception("Could not disqualify the driver")
            self.messages.show_warning(describe_error(self.translator, error))
            return
        if finished:
            self.heat_gate.hide()
            if not self._announced_end:
                self._announced_end = True
                self.race_over.emit(runner.race.id)
            return
        self._show_heat_gate(runner.race.id)

    def _change_heat(self, action: Callable[[RaceId], object]) -> None:
        runner = self._runner
        if runner is None:
            return
        try:
            action(runner.race.id)
        except Exception as error:
            if not is_expected(error):
                logger.exception("Could not change the heat plan")
            self.messages.show_warning(describe_error(self.translator, error))
            return
        self._show_heat_gate(runner.race.id)

    def _require_service(self) -> RaceService:
        if self._service is None:
            raise RuntimeError("race service is not available")
        return self._service

    def _guard(self, action: Callable[[], None]) -> None:
        try:
            action()
        except Exception as error:
            if not is_expected(error):
                logger.exception("Live race action failed")
            self.messages.show_warning(describe_error(self.translator, error))

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


def _shown_time(value: int | None) -> str:
    return EMPTY_DISPLAY if value is None else format_duration(value)
