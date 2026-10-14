"""Choose the driver and vehicle of one lane without stopping the session."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from slot_racing.core.catalog import DriverCatalog, VehicleCatalog
from slot_racing.core.domain import DriverId, VehicleId
from slot_racing.core.errors import ValidationError
from slot_racing.core.i18n import Translator
from slot_racing.uikit import StatusLabel, describe_error
from slot_racing.uikit.errors import is_expected
from slot_racing.uikit.theme import SPACE, set_role


class LaneSwitchDialog(QDialog):
    """One lane's assignment. Confirming applies it; closing leaves the lane as it is.

    The dialog is modeless. Timing events keep flowing while it is open.
    """

    def __init__(
        self,
        translator: Translator,
        drivers: DriverCatalog,
        vehicles: VehicleCatalog,
        lane: int,
        *,
        driver_id: DriverId | None,
        vehicle_id: VehicleId | None,
        apply: Callable[[DriverId, VehicleId], None],
    ) -> None:
        super().__init__()
        self.translator = translator
        self._vehicles = vehicles
        self._apply = apply
        self.lane = lane
        tr = translator.translate
        self.setObjectName(f"lane-switch-{lane}")
        self.setWindowTitle(translator.format("race.live.switch.title", lane=lane))
        self.setModal(False)

        self.driver_combo = QComboBox()
        self.driver_combo.setObjectName("switch-driver")
        self.vehicle_combo = QComboBox()
        self.vehicle_combo.setObjectName("switch-vehicle")
        blank = tr("race.wizard.free.empty")
        self.driver_combo.addItem(blank, None)
        for driver in drivers.list_drivers(active_only=True):
            self.driver_combo.addItem(driver.label, driver.id)
        if driver_id is not None:
            self.driver_combo.setCurrentIndex(max(0, self.driver_combo.findData(driver_id)))
        self._fill_vehicles(vehicle_id)
        self.driver_combo.currentIndexChanged.connect(lambda _index: self._fill_vehicles(None))

        self.status = StatusLabel(f"switch-status-{lane}")
        self.confirm_button = QPushButton(tr("race.live.switch.confirm"))
        self.confirm_button.setObjectName("switch-confirm")
        self.cancel_button = QPushButton(tr("race.wizard.cancel"))
        self.cancel_button.setObjectName("switch-cancel")
        set_role(self.confirm_button, "primary")
        set_role(self.cancel_button, "ghost")

        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE.sm)
        layout.addWidget(QLabel(translator.format("race.wizard.free.lane", lane=lane)))
        layout.addWidget(QLabel(tr("race.wizard.driver")))
        layout.addWidget(self.driver_combo)
        layout.addWidget(QLabel(tr("race.wizard.vehicle")))
        layout.addWidget(self.vehicle_combo)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        buttons.addWidget(self.cancel_button)
        buttons.addStretch(1)
        buttons.addWidget(self.confirm_button)
        layout.addLayout(buttons)
        self.confirm_button.clicked.connect(self._confirm)
        self.cancel_button.clicked.connect(self.reject)

    def _fill_vehicles(self, selected: VehicleId | None) -> None:
        blank = self.translator.translate("race.wizard.free.empty")
        self.vehicle_combo.blockSignals(True)
        self.vehicle_combo.clear()
        self.vehicle_combo.addItem(blank, None)
        for vehicle in self._vehicles.list_vehicles(active_only=True):
            self.vehicle_combo.addItem(vehicle.label, vehicle.id)
        if selected is not None:
            self.vehicle_combo.setCurrentIndex(max(0, self.vehicle_combo.findData(selected)))
        self.vehicle_combo.blockSignals(False)

    def _confirm(self) -> None:
        driver_id = self.driver_combo.currentData()
        vehicle_id = self.vehicle_combo.currentData()
        if driver_id is None or vehicle_id is None:
            self.status.show_error(self.translator.translate("race.live.switch.required"))
            return
        try:
            self._apply(DriverId(int(driver_id)), VehicleId(int(vehicle_id)))
        except ValidationError as error:
            self.status.show_error(describe_error(self.translator, error))
            return
        except Exception as error:
            if not is_expected(error):
                raise
            self.status.show_error(describe_error(self.translator, error))
            return
        self.accept()
