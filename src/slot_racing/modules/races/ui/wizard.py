"""Six step flow for configuring a race."""

from __future__ import annotations

import logging

from PySide6.QtCore import Signal
from PySide6.QtGui import QStandardItemModel
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.catalog import DriverCatalog, TrackCatalog, VehicleCatalog
from slot_racing.core.domain import DriverId, RaceId, RaceMode, TrackId, VehicleId
from slot_racing.core.errors import ValidationError
from slot_racing.core.i18n import Translator
from slot_racing.core.timing_registry import TimingProviderRegistry
from slot_racing.modules.races.service import MAX_LAPS, RaceService, parse_duration_minutes
from slot_racing.modules.races.types import RaceInfo
from slot_racing.modules.races.ui.race_briefing import (
    BriefingBoard,
    OverviewBoard,
    apply_text_size,
)
from slot_racing.uikit import (
    StatusLabel,
    availability_text,
    describe_error,
    fill_table,
    heading,
    make_table,
    provider_item_text,
    provider_label,
    selected_id,
)
from slot_racing.uikit.errors import is_expected
from slot_racing.uikit.theme import FONT_CAPTION, FONT_STEP, SPACE, configure_page, set_role

logger = logging.getLogger(__name__)

STEP_KEYS = ("name", "track", "mode", "participants", "overview", "start")
NAME, TRACK, MODE, PARTICIPANTS, OVERVIEW, START = range(6)


class RaceWizard(QWidget):
    """Collects name, track, mode, participants and offers the start.

    The race is stored when leaving the mode step; participants are added through the service
    one by one, so every rule violation is reported at the moment it happens. Leaving the wizard
    early keeps the stored draft, which can be continued or deleted from the race list.
    """

    start_requested = Signal(int)
    closed = Signal()

    def __init__(
        self,
        translator: Translator,
        service: RaceService,
        drivers: DriverCatalog,
        vehicles: VehicleCatalog,
        tracks: TrackCatalog,
        providers: TimingProviderRegistry,
    ) -> None:
        super().__init__()
        self.setObjectName("race-wizard")
        self.translator = translator
        self._service = service
        self._drivers = drivers
        self._vehicles = vehicles
        self._tracks = tracks
        self._providers = providers
        self._race: RaceInfo | None = None
        self._editing_participant_id: int | None = None
        tr = translator.translate

        self.title_label = heading(tr("race.wizard.title"))
        self.step_label = QLabel()
        self.step_label.setObjectName("wizard-step")
        self.step_label.setWordWrap(True)
        set_role(self.step_label, "wizard-step")
        self.stack = QStackedWidget()
        self.status = StatusLabel("wizard-status")

        self.name_edit = QLineEdit()
        self.name_edit.setObjectName("race-name")
        self.track_combo = QComboBox()
        self.track_combo.setObjectName("race-track")
        self.provider_combo = QComboBox()
        self.provider_combo.setObjectName("race-provider")
        self.provider_status = QLabel()
        self.provider_status.setObjectName("race-provider-status")
        self.provider_status.setWordWrap(True)
        self.mode_combo = QComboBox()
        self.mode_combo.setObjectName("race-mode")
        self.mode_combo.addItem(tr("race.wizard.mode.laps"), RaceMode.LAPS.value)
        self.mode_combo.addItem(tr("race.wizard.mode.time_trial"), RaceMode.TIME_TRIAL.value)
        self.laps_caption = QLabel(tr("race.wizard.laps"))
        self.laps_caption.setObjectName("race-laps-label")
        self.laps_caption.setWordWrap(True)
        self.laps_spin = QSpinBox()
        self.laps_spin.setObjectName("race-laps")
        self.laps_spin.setRange(1, MAX_LAPS)
        self.laps_spin.setValue(5)
        self.duration_caption = QLabel(tr("race.wizard.duration"))
        self.duration_caption.setObjectName("race-duration-label")
        self.duration_caption.setWordWrap(True)
        self.duration_edit = QLineEdit("5")
        self.duration_edit.setObjectName("race-duration")
        self.duration_unit = QLabel(tr("race.wizard.minutes"))
        self.duration_unit.setObjectName("race-duration-unit")
        self.driver_combo = QComboBox()
        self.driver_combo.setObjectName("race-driver")
        self.vehicle_combo = QComboBox()
        self.vehicle_combo.setObjectName("race-vehicle")
        self.lane_combo = QComboBox(self)
        self.lane_combo.setObjectName("race-lane")
        self.lane_combo.hide()
        self.add_participant_button = QPushButton(tr("race.wizard.add_participant"))
        self.add_participant_button.setObjectName("race-add-participant")
        self.edit_participant_button = QPushButton(tr("race.wizard.edit_participant"))
        self.edit_participant_button.setObjectName("race-edit-participant")
        self.remove_participant_button = QPushButton(tr("race.wizard.remove_participant"))
        self.remove_participant_button.setObjectName("race-remove-participant")
        self.participant_table = make_table(
            [
                tr("race.column.driver"),
                tr("race.column.vehicle"),
            ],
            "race-participants",
        )
        self.overview_label = QLabel()
        self.overview_label.setObjectName("race-overview")
        self.overview_label.hide()
        self.overview_board = OverviewBoard()
        self.ready_label = QLabel(tr("race.wizard.ready"))
        self.ready_label.setWordWrap(True)
        self.ready_label.hide()
        self.briefing_board = BriefingBoard()
        self.heat_driver = QComboBox()
        self.heat_driver.setObjectName("wizard-heat-driver")
        self.postpone_button = QPushButton(tr("race.heat.postpone"))
        self.postpone_button.setObjectName("wizard-heat-postpone")
        self.disqualify_button = QPushButton(tr("race.heat.disqualify"))
        self.disqualify_button.setObjectName("wizard-heat-disqualify")
        set_role(self.postpone_button, "secondary")
        set_role(self.disqualify_button, "danger")
        self.start_button = QPushButton(tr("race.wizard.start"))
        self.start_button.setObjectName("race-start")

        self.back_button = QPushButton(tr("race.wizard.back"))
        self.back_button.setObjectName("wizard-back")
        self.next_button = QPushButton(tr("race.wizard.next"))
        self.next_button.setObjectName("wizard-next")
        self.cancel_button = QPushButton(tr("race.wizard.cancel"))
        self.cancel_button.setObjectName("wizard-cancel")
        set_role(self.start_button, "primary")
        set_role(self.next_button, "primary")
        set_role(self.back_button, "secondary")
        set_role(self.cancel_button, "ghost")
        set_role(self.add_participant_button, "secondary")
        set_role(self.edit_participant_button, "secondary")
        set_role(self.remove_participant_button, "danger")

        self._build_pages()
        buttons = QHBoxLayout()
        buttons.setSpacing(SPACE.sm)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch(1)
        buttons.addWidget(self.back_button)
        buttons.addWidget(self.next_button)
        layout = QVBoxLayout(self)
        configure_page(layout)
        layout.addWidget(self.title_label)
        layout.addWidget(self.step_label)
        layout.addWidget(self.stack, 1)
        layout.addWidget(self.status)
        layout.addLayout(buttons)

        self.back_button.clicked.connect(lambda: self.go_back())
        self.next_button.clicked.connect(lambda: self.go_next())
        self.cancel_button.clicked.connect(lambda: self.closed.emit())
        self.start_button.clicked.connect(lambda: self.request_start())
        self.add_participant_button.clicked.connect(lambda: self.add_participant())
        self.edit_participant_button.clicked.connect(lambda: self.edit_selected_participant())
        self.remove_participant_button.clicked.connect(lambda: self.remove_selected_participant())
        self.driver_combo.currentIndexChanged.connect(lambda _: self._reload_vehicles())
        self.provider_combo.currentIndexChanged.connect(lambda _: self._update_provider_status())
        self.mode_combo.currentIndexChanged.connect(lambda _: self._sync_lap_target())
        self.postpone_button.clicked.connect(lambda: self._guard(self._postpone_selected))
        self.disqualify_button.clicked.connect(lambda: self._guard(self._disqualify_selected))
        self._show_step(NAME)

    @property
    def step(self) -> int:
        return self.stack.currentIndex()

    @property
    def race(self) -> RaceInfo | None:
        return self._race

    def begin(self, race_id: RaceId | None = None) -> None:
        """Start a new race, or continue the configuration of a stored one."""
        self.status.clear_message()
        self._race = None if race_id is None else self._service.require_race(race_id)
        self._reload_choices()
        if self._race is None:
            self.name_edit.clear()
            self.mode_combo.setCurrentIndex(self.mode_combo.findData(RaceMode.LAPS.value))
            self.laps_spin.setValue(5)
            self.duration_edit.setText("5")
        else:
            self.name_edit.setText(self._race.name)
            self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(self._race.mode.value)))
            if self._race.mode is RaceMode.LAPS and self._race.laps >= 1:
                self.laps_spin.setValue(self._race.laps)
            if self._race.duration_minutes is not None:
                self.duration_edit.setText(str(self._race.duration_minutes))
            else:
                self.duration_edit.setText("5")
            self.track_combo.setCurrentIndex(max(0, self.track_combo.findData(self._race.track_id)))
        self._sync_lap_target()
        self._cancel_participant_edit()
        self._refresh_participants()
        self._show_step(NAME)

    def go_next(self) -> bool:
        """Validate the current step and move on. Returns False if the step is invalid."""
        return self._guard(self._advance)

    def go_back(self) -> None:
        self.status.clear_message()
        if self.step > NAME:
            self._show_step(self.step - 1)

    def add_participant(self) -> bool:
        return self._guard(self._add_participant)

    def edit_selected_participant(self) -> bool:
        """Load the selected participant, or write the open correction back onto that row."""
        return self._guard(self._edit_participant)

    def remove_selected_participant(self) -> bool:
        return self._guard(self._remove_participant)

    def request_start(self) -> bool:
        return self._guard(self._request_start)

    def show_error(self, message: str) -> None:
        """Show a reason that appeared after the start was handed to the race page."""
        self.status.show_error(message)

    def _build_pages(self) -> None:
        tr = self.translator.translate
        for index, key in enumerate(STEP_KEYS):
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.addWidget(heading(tr(f"race.wizard.step.{key}")))
            if index == NAME:
                layout.addWidget(_caption(tr("race.wizard.name")))
                layout.addWidget(self.name_edit)
            elif index == TRACK:
                layout.addWidget(_caption(tr("race.wizard.track")))
                layout.addWidget(self.track_combo)
                layout.addWidget(_caption(tr("race.wizard.provider")))
                layout.addWidget(self.provider_combo)
                layout.addWidget(self.provider_status)
            elif index == MODE:
                layout.addWidget(_caption(tr("race.wizard.mode")))
                layout.addWidget(self.mode_combo)
                layout.addWidget(self.laps_caption)
                layout.addWidget(self.laps_spin)
                duration_row = QHBoxLayout()
                duration_row.addWidget(self.duration_caption)
                duration_row.addWidget(self.duration_edit, 1)
                duration_row.addWidget(self.duration_unit)
                layout.addLayout(duration_row)
            elif index == PARTICIPANTS:
                row = QHBoxLayout()
                for label, combo in (
                    ("race.wizard.driver", self.driver_combo),
                    ("race.wizard.vehicle", self.vehicle_combo),
                ):
                    column = QVBoxLayout()
                    column.addWidget(_caption(tr(label)))
                    column.addWidget(combo)
                    row.addLayout(column, 1)
                layout.addLayout(row)
                actions = QHBoxLayout()
                actions.addWidget(self.add_participant_button)
                actions.addWidget(self.edit_participant_button)
                actions.addWidget(self.remove_participant_button)
                actions.addStretch(1)
                layout.addLayout(actions)
                layout.addWidget(self.participant_table, 1)
            elif index == OVERVIEW:
                layout.addWidget(_scroll(self.overview_board), 1)
            else:
                layout.addWidget(_scroll(self.briefing_board), 1)
                layout.addWidget(self.ready_label)
                layout.addWidget(self.heat_driver)
                layout.addWidget(self.postpone_button)
                layout.addWidget(self.disqualify_button)
                layout.addWidget(self.start_button)
            self.lane_combo.hide()
            if index not in (OVERVIEW, START):
                layout.addStretch(1)
            self.stack.addWidget(page)

    def _reload_providers(self, keep: str | None = None) -> None:
        """List every registered provider; unusable ones are shown but cannot be selected."""
        chosen = keep or self.provider_combo.currentData() or self._service.default_provider_id()
        infos = self._providers.providers()
        model = self.provider_combo.model()
        assert isinstance(model, QStandardItemModel)
        self.provider_combo.blockSignals(True)
        self.provider_combo.clear()
        for info in infos:
            self.provider_combo.addItem(provider_item_text(self.translator, info), info.provider_id)
            item = model.item(self.provider_combo.count() - 1)
            if item is not None:
                item.setEnabled(info.available)
        self.provider_combo.blockSignals(False)
        usable = [info.provider_id for info in infos if info.available]
        known = {info.provider_id for info in infos}
        self._select_provider(chosen if chosen in known else (usable[0] if usable else None))

    def _select_provider(self, provider_id: str | None) -> None:
        self.provider_combo.setCurrentIndex(
            -1 if provider_id is None else self.provider_combo.findData(provider_id)
        )
        self._update_provider_status()

    def _update_provider_status(self) -> None:
        provider_id = self.provider_combo.currentData()
        if provider_id is None:
            self.provider_status.setText(self.translator.translate("race.wizard.provider_none"))
            return
        status = availability_text(self.translator, self._providers.info(provider_id))
        self.provider_status.setText(
            self.translator.format("race.wizard.provider_status", status=status)
        )

    def _reload_choices(self) -> None:
        self._reload_providers(None if self._race is None else self._race.timing_provider)
        self.track_combo.clear()
        for track in self._tracks.list_tracks(active_only=True):
            self.track_combo.addItem(track.name, track.id)
        self.driver_combo.blockSignals(True)
        self.driver_combo.clear()
        for driver in self._drivers.list_drivers(active_only=True):
            self.driver_combo.addItem(driver.label, driver.id)
        self.driver_combo.blockSignals(False)
        self._reload_vehicles()
        self._reload_lanes()

    def _reload_lanes(self) -> None:
        track_id = self.track_combo.currentData()
        track = None if track_id is None else self._tracks.get_track(TrackId(track_id))
        self.lane_combo.clear()
        for lane in range(1, (track.lane_count if track else 0) + 1):
            self.lane_combo.addItem(str(lane), lane)

    def _reload_vehicles(self) -> None:
        """Offer every active vehicle. Assignment only chooses the suggested default."""
        self.vehicle_combo.clear()
        for vehicle in self._vehicles.list_vehicles(active_only=True):
            self.vehicle_combo.addItem(vehicle.label, vehicle.id)
        self._preselect_vehicle()

    def _preselect_vehicle(self) -> None:
        """Suggest the driver's favorite, otherwise the first vehicle assigned to them."""
        driver_id = self.driver_combo.currentData()
        if driver_id is None:
            return
        owned = self._vehicles.list_vehicles(active_only=True, driver_id=DriverId(driver_id))
        if not owned:
            return
        favorite = next((vehicle for vehicle in owned if vehicle.is_favorite), None)
        chosen = owned[0] if favorite is None else favorite
        self.vehicle_combo.setCurrentIndex(max(0, self.vehicle_combo.findData(chosen.id)))

    def _show_step(self, step: int) -> None:
        tr = self.translator.translate
        self.stack.setCurrentIndex(step)
        self.step_label.setText(
            self.translator.format(
                "race.wizard.step",
                number=step + 1,
                total=len(STEP_KEYS),
                title=tr(f"race.wizard.step.{STEP_KEYS[step]}"),
            )
        )
        self._fit_step_label()
        self.back_button.setEnabled(step > NAME)
        self.next_button.setEnabled(step < START)
        if step == TRACK:
            self._reload_providers()
        if step == OVERVIEW:
            self.overview_label.setText(self._overview_text())
            self._show_overview_board()
        if step == START:
            self._show_start_briefing()

    def _advance(self) -> None:
        step = self.step
        if step == NAME:
            if not self.name_edit.text().strip():
                raise ValidationError("error.race.name.required")
        elif step == TRACK:
            if self.track_combo.currentData() is None:
                raise ValidationError("error.race.track_required")
            self._check_provider()
        elif step == MODE:
            self._save_basics()
            self._reload_lanes()
        elif step == PARTICIPANTS:
            if not self._drivers.list_drivers():
                raise ValidationError("error.race.no_drivers")
            if self._race is None or not self._race.participants:
                raise ValidationError("error.race.no_participants")
            self._service.plan_heats(self._race_id())
            self._race = self._service.require_race(self._race_id())
        elif step == OVERVIEW:
            self._race = self._service.validate_startable(self._race_id())
        self.status.clear_message()
        self._show_step(step + 1)

    def _check_provider(self) -> str:
        provider_id = self.provider_combo.currentData()
        if provider_id is None:
            raise ValidationError("error.race.provider_required")
        self._providers.check(provider_id)
        return str(provider_id)

    def _selected_mode(self) -> RaceMode:
        value = self.mode_combo.currentData()
        return RaceMode.LAPS if value is None else RaceMode(str(value))

    def _sync_lap_target(self) -> None:
        show_laps = self._selected_mode() is RaceMode.LAPS
        self.laps_caption.setVisible(show_laps)
        self.laps_spin.setVisible(show_laps)
        self.duration_caption.setVisible(not show_laps)
        self.duration_edit.setVisible(not show_laps)
        self.duration_unit.setVisible(not show_laps)

    def _save_basics(self) -> None:
        name = self.name_edit.text()
        track_id = TrackId(self.track_combo.currentData())
        provider = self._check_provider()
        mode = self._selected_mode()
        laps = self.laps_spin.value()
        duration = (
            None if mode is RaceMode.LAPS else parse_duration_minutes(self.duration_edit.text())
        )
        if self._race is None:
            self._race = (
                self._service.create_time_trial(name, track_id, provider, duration)
                if mode is RaceMode.TIME_TRIAL
                else self._service.create_race(name, track_id, laps, provider)
            )
        else:
            self._race = self._service.update_race(
                self._race.id,
                name,
                track_id,
                laps,
                provider,
                mode,
                duration_minutes=duration,
                set_duration=True,
            )
        self._refresh_participants()

    def _add_participant(self) -> None:
        driver_id = self.driver_combo.currentData()
        vehicle_id = self.vehicle_combo.currentData()
        if driver_id is None or vehicle_id is None:
            raise ValidationError("error.race.participant_required")
        self._service.enroll_driver(self._race_id(), DriverId(driver_id), VehicleId(vehicle_id))
        self._race = self._service.require_race(self._race_id())
        self._cancel_participant_edit()
        self._refresh_participants()
        self.status.clear_message()

    def _edit_participant(self) -> None:
        if self._editing_participant_id is None:
            participant_id = selected_id(self.participant_table)
            if participant_id is None or self._race is None:
                raise ValidationError("error.race.participant_unknown")
            participant = next(
                (item for item in self._race.participants if item.id == participant_id), None
            )
            if participant is None:
                raise ValidationError("error.race.participant_unknown")
            self.driver_combo.setCurrentIndex(self.driver_combo.findData(participant.driver_id))
            self._reload_vehicles()
            if participant.vehicle_id is not None:
                self.vehicle_combo.setCurrentIndex(
                    self.vehicle_combo.findData(participant.vehicle_id)
                )
            self._editing_participant_id = participant.id
            self.edit_participant_button.setText(
                self.translator.translate("race.wizard.update_participant")
            )
            self.status.clear_message()
            return
        driver_id = self.driver_combo.currentData()
        vehicle_id = self.vehicle_combo.currentData()
        if driver_id is None or vehicle_id is None:
            raise ValidationError("error.race.participant_required")
        self._service.update_enrolled(
            self._race_id(),
            self._editing_participant_id,
            DriverId(driver_id),
            VehicleId(vehicle_id),
        )
        self._race = self._service.require_race(self._race_id())
        self._cancel_participant_edit()
        self._refresh_participants()
        self.status.clear_message()

    def _cancel_participant_edit(self) -> None:
        self._editing_participant_id = None
        self.edit_participant_button.setText(
            self.translator.translate("race.wizard.edit_participant")
        )

    def _remove_participant(self) -> None:
        participant_id = selected_id(self.participant_table)
        if participant_id is None:
            raise ValidationError("error.race.participant_unknown")
        self._service.remove_participant(self._race_id(), participant_id)
        self._race = self._service.require_race(self._race_id())
        self._cancel_participant_edit()
        self._refresh_participants()
        self.status.clear_message()

    def _request_start(self) -> None:
        race = self._service.validate_startable(self._race_id())
        self.start_requested.emit(race.id)

    def _race_id(self) -> RaceId:
        if self._race is None:
            raise ValidationError("error.race.not_found")
        return self._race.id

    def _refresh_participants(self) -> None:
        participants = () if self._race is None else self._race.participants
        fill_table(
            self.participant_table,
            [(p.driver_label, p.vehicle_label) for p in participants],
            [p.id for p in participants],
        )

    def _overview_text(self) -> str:
        fmt = self.translator.format
        race = self._race
        if race is None:
            return ""
        lines = [
            fmt("race.overview.race", name=race.name),
            fmt("race.overview.track", track=race.track_name, lanes=race.lane_count),
            (
                fmt("race.overview.time_trial_duration", minutes=race.duration_minutes or 0)
                if race.mode is RaceMode.TIME_TRIAL
                else fmt("race.overview.laps", laps=race.laps)
            ),
            fmt(
                "race.overview.provider",
                provider=provider_label(self.translator, race.timing_provider),
            ),
            "",
            self.translator.translate("race.overview.participants"),
        ]
        lines.extend(
            fmt(
                "race.overview.participant",
                driver=p.driver_label,
                vehicle=p.vehicle_label,
            )
            for p in race.participants
        )
        plan = self._service.heat_plan(race.id)
        if plan:
            lines.append("")
            lines.append(self.translator.translate("race.overview.heats"))
            names = {participant.id: participant.driver_label for participant in race.participants}
            for heat in plan:
                seated = ", ".join(
                    fmt("race.heat.seat", lane=lane, driver=names.get(participant_id, ""))
                    for participant_id, lane in heat.seats
                )
                lines.append(fmt("race.overview.heat", sequence=heat.sequence, seats=seated))
        return "\n".join(lines)

    def _show_overview_board(self) -> None:
        race = self._race
        if race is None:
            return
        self.overview_board.show_race(
            race,
            tuple(self._service.heat_plan(race.id)),
            self.translator,
            provider_label(self.translator, race.timing_provider),
        )

    def _fit_step_label(self) -> None:
        apply_text_size(self.step_label, FONT_STEP, max(self.width(), 640), bold=True)
        apply_text_size(self.laps_caption, FONT_CAPTION, max(self.width(), 640), bold=True)
        apply_text_size(self.duration_caption, FONT_CAPTION, max(self.width(), 640), bold=True)
        apply_text_size(self.duration_unit, FONT_CAPTION, max(self.width(), 640), bold=False)
        apply_text_size(self.provider_status, FONT_CAPTION, max(self.width(), 640), bold=False)

    def resizeEvent(self, event: object) -> None:  # noqa: N802
        super().resizeEvent(event)  # type: ignore[arg-type]
        self._fit_step_label()

    def _show_start_briefing(self) -> None:
        race = self._race
        if race is None:
            return
        briefing = self._service.heat_briefing(race.id)
        if briefing is None:
            ready = self.translator.translate("race.wizard.ready")
            self.ready_label.setText(ready)
            self.briefing_board.show_message(ready)
            self.briefing_board.show()
            self.heat_driver.hide()
            self.postpone_button.hide()
            self.disqualify_button.hide()
            return
        fmt = self.translator.format
        free = self.translator.translate("race.time_trial.free")
        lines = [fmt("race.heat.title", sequence=briefing.sequence), ""]
        for seat in briefing.seats:
            lines.append(fmt("race.heat.seat", lane=seat.lane, driver=seat.driver_label or free))
        lines.extend(["", self.translator.translate("race.heat.ready_hint")])
        self.ready_label.setText("\n".join(lines))
        self.briefing_board.show()
        self.briefing_board.show_briefing(briefing, self.translator)
        self.heat_driver.show()
        self.postpone_button.show()
        self.disqualify_button.show()
        self.heat_driver.clear()
        for participant_id, label in briefing.drivers:
            self.heat_driver.addItem(label, participant_id)

    def _postpone_selected(self) -> None:
        participant_id = self.heat_driver.currentData()
        if participant_id is None:
            raise ValidationError("error.race.participant_unknown")
        self._service.postpone_driver(self._race_id(), int(participant_id))
        self._race = self._service.require_race(self._race_id())
        self._show_start_briefing()
        self.status.clear_message()

    def _disqualify_selected(self) -> None:
        participant_id = self.heat_driver.currentData()
        if participant_id is None:
            raise ValidationError("error.race.participant_unknown")
        self._service.disqualify_driver(self._race_id(), int(participant_id))
        self._race = self._service.require_race(self._race_id())
        self._show_start_briefing()
        self.status.clear_message()

    def _guard(self, action: object) -> bool:
        assert callable(action)
        try:
            action()
        except Exception as error:
            if not is_expected(error):
                logger.exception("Race wizard action failed")
            self.status.show_error(describe_error(self.translator, error))
            return False
        return True


def _caption(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    set_role(label, "wizard-caption")
    apply_text_size(label, FONT_CAPTION, 960, bold=True)
    return label


def _scroll(widget: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll.setWidget(widget)
    return scroll
