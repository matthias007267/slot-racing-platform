"""Vehicle list and editor."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDialog, QLineEdit, QPushButton, QSpinBox, QWidget

from slot_racing.core.catalog import VehicleInfo
from slot_racing.core.domain import DriverId, VehicleId
from slot_racing.core.i18n import Translator
from slot_racing.modules.drivers_vehicles.service import DriverService, VehicleInput, VehicleService
from slot_racing.uikit import EntityPage, EntityRow, FormDialog, selected_id


class VehicleDialog(FormDialog):
    def __init__(
        self,
        translator: Translator,
        vehicles: VehicleService,
        drivers: DriverService,
        vehicle: VehicleInfo | None,
        parent: QWidget | None = None,
    ) -> None:
        tr = translator.translate
        super().__init__(
            translator, tr("vehicle.dialog.edit" if vehicle else "vehicle.dialog.new"), parent
        )
        self._vehicles = vehicles
        self._vehicle = vehicle
        self.name_edit = QLineEdit(vehicle.name if vehicle else "")
        self.name_edit.setObjectName("vehicle-name")
        self.model_edit = QLineEdit(vehicle.model or "" if vehicle else "")
        self.model_edit.setObjectName("vehicle-model")
        self.manufacturer_edit = QLineEdit(vehicle.manufacturer or "" if vehicle else "")
        self.manufacturer_edit.setObjectName("vehicle-manufacturer")
        self.start_number_edit = QSpinBox()
        self.start_number_edit.setObjectName("vehicle-start-number")
        self.start_number_edit.setRange(0, 9999)
        self.start_number_edit.setSpecialValueText(tr("common.none"))
        self.start_number_edit.setValue(vehicle.start_number or 0 if vehicle else 0)
        self.driver_combo = QComboBox()
        self.driver_combo.setObjectName("vehicle-driver")
        self.driver_combo.addItem(tr("common.none"), None)
        current = None if vehicle is None else vehicle.driver_id
        for driver in drivers.list_drivers():
            if driver.is_active or driver.id == current:
                self.driver_combo.addItem(driver.label, driver.id)
        self.driver_combo.setCurrentIndex(max(0, self.driver_combo.findData(current)))
        self.form.addRow(tr("vehicle.field.name"), self.name_edit)
        self.form.addRow(tr("vehicle.field.model"), self.model_edit)
        self.form.addRow(tr("vehicle.field.manufacturer"), self.manufacturer_edit)
        self.form.addRow(tr("vehicle.field.start_number"), self.start_number_edit)
        self.form.addRow(tr("vehicle.field.driver"), self.driver_combo)

    def submit(self) -> None:
        driver_id = self.driver_combo.currentData()
        data = VehicleInput(
            name=self.name_edit.text(),
            model=self.model_edit.text(),
            manufacturer=self.manufacturer_edit.text(),
            start_number=self.start_number_edit.value() or None,
            driver_id=None if driver_id is None else DriverId(driver_id),
        )
        if self._vehicle is None:
            self._vehicles.create_vehicle(data)
        else:
            self._vehicles.update_vehicle(self._vehicle.id, data)


class VehiclesPage(EntityPage):
    def __init__(
        self, translator: Translator, vehicles: VehicleService, drivers: DriverService
    ) -> None:
        super().__init__(
            translator,
            title_key="nav.vehicles",
            header_keys=[
                "vehicle.field.name",
                "vehicle.field.model",
                "vehicle.field.manufacturer",
                "vehicle.field.start_number",
                "vehicle.field.driver",
                "common.active",
            ],
            name="vehicles",
        )
        self._vehicles = vehicles
        self._drivers = drivers
        self.unassign_button = QPushButton(translator.translate("vehicle.unassign"))
        self.unassign_button.setObjectName("vehicles-unassign")
        self.buttons.insertWidget(self.buttons.count() - 1, self.unassign_button)
        self.unassign_button.clicked.connect(lambda: self.unassign_selected())

    def unassign_selected(self) -> None:
        vehicle_id = selected_id(self.table)
        if vehicle_id is not None:
            self._guard(lambda: self._unassign(VehicleId(vehicle_id)))

    def _unassign(self, vehicle_id: VehicleId) -> None:
        self._vehicles.assign_driver(vehicle_id, None)
        self._reload()
        self.status.show_info(self.translator.translate("common.saved"))

    def load_rows(self) -> list[EntityRow]:
        tr = self.translator.translate
        drivers = {driver.id: driver.label for driver in self._drivers.list_drivers()}
        return [
            EntityRow(
                id=vehicle.id,
                cells=(
                    vehicle.name,
                    vehicle.model or "",
                    vehicle.manufacturer or "",
                    "" if vehicle.start_number is None else str(vehicle.start_number),
                    "" if vehicle.driver_id is None else drivers.get(vehicle.driver_id, ""),
                    tr("common.yes" if vehicle.is_active else "common.no"),
                ),
                active=vehicle.is_active,
            )
            for vehicle in self._vehicles.list_vehicles()
        ]

    def create_dialog(self, entity_id: int | None) -> QDialog:
        vehicle = None if entity_id is None else self._vehicles.get_vehicle(VehicleId(entity_id))
        return VehicleDialog(self.translator, self._vehicles, self._drivers, vehicle, self)

    def set_entity_active(self, entity_id: int, active: bool) -> None:
        self._vehicles.set_active(VehicleId(entity_id), active)

    def delete_entity(self, entity_id: int) -> None:
        self._vehicles.delete_vehicle(VehicleId(entity_id))
