"""Dialog that copies a finished or aborted race into a new one."""

from __future__ import annotations

import logging

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.catalog import DriverCatalog, VehicleCatalog
from slot_racing.core.domain import DriverId, RaceMode, VehicleId
from slot_racing.core.errors import ValidationError
from slot_racing.core.i18n import Translator
from slot_racing.modules.races.service import RaceService
from slot_racing.modules.races.types import RestartPreview, RestartSeat
from slot_racing.uikit import StatusLabel, describe_error, provider_label
from slot_racing.uikit.errors import is_expected
from slot_racing.uikit.theme import SPACE, set_role

logger = logging.getLogger(__name__)

_LANE_ISSUES = frozenset(
    {"error.race.lane_invalid", "error.race.lane_missing", "error.race.lane_taken"}
)
_DRIVER_ISSUES = frozenset(
    {"error.race.driver_unknown", "error.race.driver_inactive", "error.race.driver_duplicate"}
)
_VEHICLE_ISSUES = frozenset({"error.race.vehicle_unknown", "error.race.vehicle_inactive"})


class RestartDialog(QDialog):
    """Shows the copied setup. Confirming stores a new race; closing stores nothing."""

    created = Signal(int)

    def __init__(
        self,
        translator: Translator,
        service: RaceService,
        drivers: DriverCatalog,
        vehicles: VehicleCatalog,
        preview: RestartPreview,
    ) -> None:
        super().__init__()
        self.translator = translator
        self._service = service
        self._drivers = drivers
        self._vehicles = vehicles
        self.preview = preview
        self._driver_combos: dict[int, QComboBox] = {}
        self._vehicle_combos: dict[int, QComboBox] = {}
        self._lane_combos: dict[int, QComboBox] = {}
        tr = translator.translate
        self.setObjectName("restart-dialog")
        self.setWindowTitle(tr("race.restart.title"))

        self.name_edit = QLineEdit(preview.suggested_name)
        self.name_edit.setObjectName("restart-name")
        self.summary = QLabel(self._summary())
        self.summary.setObjectName("restart-summary")
        self.summary.setWordWrap(True)
        self.kept = QLabel(tr("race.restart.kept"))
        self.kept.setObjectName("restart-kept")
        self.kept.setWordWrap(True)
        self.status = StatusLabel("restart-status")
        self.create_button = QPushButton(tr("race.restart.create"))
        self.create_button.setObjectName("restart-create")
        self.cancel_button = QPushButton(tr("race.wizard.cancel"))
        self.cancel_button.setObjectName("restart-cancel")
        set_role(self.create_button, "primary")
        set_role(self.cancel_button, "ghost")

        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE.sm)
        layout.addWidget(QLabel(tr("race.wizard.name")))
        layout.addWidget(self.name_edit)
        layout.addWidget(self.summary)
        layout.addWidget(self.kept)
        for index, seat in enumerate(preview.seats):
            layout.addWidget(self._seat_block(index, seat))
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        buttons.addWidget(self.cancel_button)
        buttons.addStretch(1)
        buttons.addWidget(self.create_button)
        layout.addLayout(buttons)
        self.create_button.clicked.connect(self._create)
        self.cancel_button.clicked.connect(self.reject)

    def _summary(self) -> str:
        fmt = self.translator.format
        race = self.preview
        mode = (
            fmt("race.overview.time_trial_duration", minutes=race.duration_minutes or 0)
            if race.mode is RaceMode.TIME_TRIAL
            else fmt("race.overview.laps", laps=race.laps)
        )
        return "\n".join(
            (
                fmt("race.overview.track", track=race.track_name, lanes=race.lane_count),
                fmt("race.overview.active_lanes", lanes=race.active_lanes),
                mode,
                fmt(
                    "race.overview.provider",
                    provider=provider_label(self.translator, race.timing_provider),
                ),
                fmt("race.overview.repetitions", count=race.repetitions),
            )
        )

    def _seat_block(self, index: int, seat: RestartSeat) -> QWidget:
        text = f"{seat.driver_label} — {seat.vehicle_label}"
        if seat.lane is not None:
            text = f"{text}, Spur {seat.lane}"
        block = QWidget()
        block.setObjectName(f"restart-seat-{index}")
        column = QVBoxLayout(block)
        column.setContentsMargins(0, 0, 0, 0)
        kept = QLabel(text)
        kept.setWordWrap(True)
        column.addWidget(kept)
        if seat.issue_key is None:
            return block
        problem = QLabel(self._issue_text(seat))
        problem.setObjectName(f"restart-problem-{index}")
        problem.setWordWrap(True)
        column.addWidget(QLabel(self.translator.translate("race.restart.fix")))
        column.addWidget(problem)
        if seat.issue_key in _DRIVER_ISSUES:
            column.addWidget(self._driver_combo(index))
        if seat.issue_key in _VEHICLE_ISSUES:
            column.addWidget(self._vehicle_combo(index))
        if seat.issue_key in _LANE_ISSUES:
            column.addWidget(self._lane_combo(index))
        return block

    def _issue_text(self, seat: RestartSeat) -> str:
        if seat.issue_key is None:
            return ""
        return self.translator.format(
            seat.issue_key,
            driver=seat.issue_driver,
            vehicle=seat.issue_vehicle,
            lane=0 if seat.issue_lane is None else seat.issue_lane,
            maximum=0 if seat.issue_maximum is None else seat.issue_maximum,
        )

    def _driver_combo(self, index: int) -> QComboBox:
        combo = self._blank_combo(f"restart-driver-{index}")
        for driver in self._drivers.list_drivers(active_only=True):
            combo.addItem(driver.label, driver.id)
        self._driver_combos[index] = combo
        return combo

    def _vehicle_combo(self, index: int) -> QComboBox:
        combo = self._blank_combo(f"restart-vehicle-{index}")
        for vehicle in self._vehicles.list_vehicles(active_only=True):
            combo.addItem(vehicle.label, vehicle.id)
        self._vehicle_combos[index] = combo
        return combo

    def _lane_combo(self, index: int) -> QComboBox:
        combo = self._blank_combo(f"restart-lane-{index}")
        for lane in range(1, self.preview.lane_count + 1):
            combo.addItem(str(lane), lane)
        self._lane_combos[index] = combo
        return combo

    def _blank_combo(self, object_name: str) -> QComboBox:
        combo = QComboBox()
        combo.setObjectName(object_name)
        combo.addItem(self.translator.translate("race.restart.choose"), None)
        return combo

    def _create(self) -> None:
        try:
            created = self._service.restart_aborted(
                self.preview.source_id,
                name=self.name_edit.text(),
                seats=self._seats(),
            )
        except Exception as error:
            if not is_expected(error):
                logger.exception("Could not restart the race")
            self.status.show_error(describe_error(self.translator, error))
            return
        self.created.emit(int(created.id))
        self.accept()

    def _seats(self) -> list[tuple[DriverId, VehicleId, int | None]]:
        chosen: list[tuple[DriverId, VehicleId, int | None]] = []
        for index, seat in enumerate(self.preview.seats):
            driver = self._picked(self._driver_combos.get(index), int(seat.driver_id))
            vehicle = self._picked(
                self._vehicle_combos.get(index),
                None if seat.vehicle_id is None else int(seat.vehicle_id),
            )
            lane = None if self.preview.uses_heats else seat.lane
            lane_combo = self._lane_combos.get(index)
            if lane_combo is not None:
                raw_lane = lane_combo.currentData()
                lane = None if raw_lane is None else int(raw_lane)
            if driver is None or vehicle is None or (lane_combo is not None and lane is None):
                raise ValidationError("error.race.restart_incomplete")
            chosen.append((DriverId(driver), VehicleId(vehicle), lane))
        return chosen

    @staticmethod
    def _picked(combo: QComboBox | None, fallback: int | None) -> int | None:
        if combo is None:
            return fallback
        value = combo.currentData()
        return None if value is None else int(value)
