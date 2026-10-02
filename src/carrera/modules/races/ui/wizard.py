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
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from carrera.core.catalog import DriverCatalog, TrackCatalog, VehicleCatalog
from carrera.core.domain import DriverId, RaceId, TrackId, VehicleId
from carrera.core.errors import ValidationError
from carrera.core.i18n import Translator
from carrera.core.timing_registry import TimingProviderRegistry
from carrera.modules.races.service import MAX_LAPS, RaceService
from carrera.modules.races.types import RaceInfo
from carrera.uikit import (
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
from carrera.uikit.errors import is_expected

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
        self.translator = translator
        self._service = service
        self._drivers = drivers
        self._vehicles = vehicles
        self._tracks = tracks
        self._providers = providers
        self._race: RaceInfo | None = None
        tr = translator.translate

        self.title_label = heading(tr("race.wizard.title"))
        self.step_label = QLabel()
        self.step_label.setObjectName("wizard-step")
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
        self.mode_combo = QComboBox()
        self.mode_combo.setObjectName("race-mode")
        self.mode_combo.addItem(tr("race.wizard.mode.laps"), "laps")
        self.laps_spin = QSpinBox()
        self.laps_spin.setObjectName("race-laps")
        self.laps_spin.setRange(1, MAX_LAPS)
        self.laps_spin.setValue(5)
        self.driver_combo = QComboBox()
        self.driver_combo.setObjectName("race-driver")
        self.vehicle_combo = QComboBox()
        self.vehicle_combo.setObjectName("race-vehicle")
        self.lane_combo = QComboBox()
        self.lane_combo.setObjectName("race-lane")
        self.add_participant_button = QPushButton(tr("race.wizard.add_participant"))
        self.add_participant_button.setObjectName("race-add-participant")
        self.remove_participant_button = QPushButton(tr("race.wizard.remove_participant"))
        self.remove_participant_button.setObjectName("race-remove-participant")
        self.participant_table = make_table(
            [
                tr("race.column.lane"),
                tr("race.column.driver"),
                tr("race.column.vehicle"),
            ],
            "race-participants",
        )
        self.overview_label = QLabel()
        self.overview_label.setObjectName("race-overview")
        self.ready_label = QLabel(tr("race.wizard.ready"))
        self.start_button = QPushButton(tr("race.wizard.start"))
        self.start_button.setObjectName("race-start")

        self.back_button = QPushButton(tr("race.wizard.back"))
        self.back_button.setObjectName("wizard-back")
        self.next_button = QPushButton(tr("race.wizard.next"))
        self.next_button.setObjectName("wizard-next")
        self.cancel_button = QPushButton(tr("race.wizard.cancel"))
        self.cancel_button.setObjectName("wizard-cancel")

        self._build_pages()
        buttons = QHBoxLayout()
        buttons.addWidget(self.cancel_button)
        buttons.addStretch(1)
        buttons.addWidget(self.back_button)
        buttons.addWidget(self.next_button)
        layout = QVBoxLayout(self)
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
        self.remove_participant_button.clicked.connect(lambda: self.remove_selected_participant())
        self.driver_combo.currentIndexChanged.connect(self._preselect_vehicle)
        self.provider_combo.currentIndexChanged.connect(lambda _: self._update_provider_status())
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
            self.laps_spin.setValue(5)
        else:
            self.name_edit.setText(self._race.name)
            self.laps_spin.setValue(self._race.laps)
            self.track_combo.setCurrentIndex(max(0, self.track_combo.findData(self._race.track_id)))
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

    def remove_selected_participant(self) -> bool:
        return self._guard(self._remove_participant)

    def request_start(self) -> bool:
        return self._guard(self._request_start)

    def _build_pages(self) -> None:
        tr = self.translator.translate
        for index, key in enumerate(STEP_KEYS):
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.addWidget(heading(tr(f"race.wizard.step.{key}")))
            if index == NAME:
                layout.addWidget(QLabel(tr("race.wizard.name")))
                layout.addWidget(self.name_edit)
            elif index == TRACK:
                layout.addWidget(QLabel(tr("race.wizard.track")))
                layout.addWidget(self.track_combo)
                layout.addWidget(QLabel(tr("race.wizard.provider")))
                layout.addWidget(self.provider_combo)
                layout.addWidget(self.provider_status)
            elif index == MODE:
                layout.addWidget(QLabel(tr("race.wizard.mode")))
                layout.addWidget(self.mode_combo)
                layout.addWidget(QLabel(tr("race.wizard.laps")))
                layout.addWidget(self.laps_spin)
            elif index == PARTICIPANTS:
                row = QHBoxLayout()
                for label, combo in (
                    ("race.wizard.driver", self.driver_combo),
                    ("race.wizard.vehicle", self.vehicle_combo),
                    ("race.wizard.lane", self.lane_combo),
                ):
                    column = QVBoxLayout()
                    column.addWidget(QLabel(tr(label)))
                    column.addWidget(combo)
                    row.addLayout(column, 1)
                layout.addLayout(row)
                actions = QHBoxLayout()
                actions.addWidget(self.add_participant_button)
                actions.addWidget(self.remove_participant_button)
                actions.addStretch(1)
                layout.addLayout(actions)
                layout.addWidget(self.participant_table, 1)
            elif index == OVERVIEW:
                layout.addWidget(self.overview_label)
            else:
                layout.addWidget(self.ready_label)
                layout.addWidget(self.start_button)
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
        self.vehicle_combo.clear()
        for vehicle in self._vehicles.list_vehicles(active_only=True):
            self.vehicle_combo.addItem(vehicle.label, vehicle.id)
        self._preselect_vehicle()
        self._reload_lanes()

    def _reload_lanes(self) -> None:
        track_id = self.track_combo.currentData()
        track = None if track_id is None else self._tracks.get_track(TrackId(track_id))
        self.lane_combo.clear()
        for lane in range(1, (track.lane_count if track else 0) + 1):
            self.lane_combo.addItem(str(lane), lane)

    def _preselect_vehicle(self) -> None:
        """Offer the driver's own vehicle first when the driver owns one."""
        driver_id = self.driver_combo.currentData()
        if driver_id is None:
            return
        owned = self._vehicles.list_vehicles(active_only=True, driver_id=DriverId(driver_id))
        if owned:
            self.vehicle_combo.setCurrentIndex(max(0, self.vehicle_combo.findData(owned[0].id)))

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
        self.back_button.setEnabled(step > NAME)
        self.next_button.setEnabled(step < START)
        if step == TRACK:
            self._reload_providers()
        if step == OVERVIEW:
            self.overview_label.setText(self._overview_text())

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
            if self._race is None or not self._race.participants:
                raise ValidationError("error.race.no_participants")
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

    def _save_basics(self) -> None:
        name = self.name_edit.text()
        track_id = TrackId(self.track_combo.currentData())
        laps = self.laps_spin.value()
        provider = self._check_provider()
        if self._race is None:
            self._race = self._service.create_race(name, track_id, laps, provider)
        else:
            self._race = self._service.update_race(self._race.id, name, track_id, laps, provider)
        self._refresh_participants()

    def _add_participant(self) -> None:
        driver_id = self.driver_combo.currentData()
        vehicle_id = self.vehicle_combo.currentData()
        lane = self.lane_combo.currentData()
        if driver_id is None or vehicle_id is None or lane is None:
            raise ValidationError("error.race.participant_required")
        self._service.add_participant(
            self._race_id(), DriverId(driver_id), VehicleId(vehicle_id), lane
        )
        self._race = self._service.require_race(self._race_id())
        self._refresh_participants()
        self.status.clear_message()

    def _remove_participant(self) -> None:
        participant_id = selected_id(self.participant_table)
        if participant_id is None:
            raise ValidationError("error.race.participant_unknown")
        self._service.remove_participant(self._race_id(), participant_id)
        self._race = self._service.require_race(self._race_id())
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
            [(str(p.lane), p.driver_label, p.vehicle_label) for p in participants],
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
            fmt("race.overview.laps", laps=race.laps),
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
                lane=p.lane,
                driver=p.driver_label,
                vehicle=p.vehicle_label,
            )
            for p in race.participants
        )
        return "\n".join(lines)

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
