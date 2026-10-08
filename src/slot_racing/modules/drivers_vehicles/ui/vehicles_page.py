"""Vehicle list and editor."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.catalog import RaceHistoryCatalog, TrackInfo, VehicleInfo
from slot_racing.core.domain import DriverId, VehicleId
from slot_racing.core.i18n import Translator
from slot_racing.modules.drivers_vehicles.service import DriverService, VehicleInput, VehicleService
from slot_racing.uikit import EntityPage, EntityRow, FormDialog, selected_id
from slot_racing.uikit.career import CareerPanel
from slot_racing.uikit.theme import set_role


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
        self.model_edit = QLineEdit(vehicle.model or "" if vehicle else "")
        self.model_edit.setObjectName("vehicle-model")
        self.manufacturer_edit = QLineEdit(vehicle.manufacturer or "" if vehicle else "")
        self.manufacturer_edit.setObjectName("vehicle-manufacturer")
        self.scale_edit = QLineEdit(vehicle.scale or "" if vehicle else "")
        self.scale_edit.setObjectName("vehicle-scale")
        self.notes_edit = QPlainTextEdit(vehicle.notes or "" if vehicle else "")
        self.notes_edit.setObjectName("vehicle-notes")
        self.notes_edit.setFixedHeight(70)
        self.driver_combo = QComboBox()
        self.driver_combo.setObjectName("vehicle-driver")
        self.driver_combo.addItem(tr("common.none"), None)
        current = None if vehicle is None else vehicle.driver_id
        for driver in drivers.list_drivers():
            if driver.is_active or driver.id == current:
                self.driver_combo.addItem(driver.label, driver.id)
        self.driver_combo.setCurrentIndex(max(0, self.driver_combo.findData(current)))
        self.favorite_check = QCheckBox(tr("vehicle.field.favorite"))
        self.favorite_check.setObjectName("vehicle-favorite")
        self.driver_combo.currentIndexChanged.connect(lambda _: self._sync_favorite())
        self._sync_favorite()
        if vehicle is not None and vehicle.is_favorite and vehicle.driver_id is not None:
            self.favorite_check.setChecked(True)
        self.form.addRow(tr("vehicle.field.manufacturer"), self.manufacturer_edit)
        self.form.addRow(tr("vehicle.field.model"), self.model_edit)
        self.form.addRow(tr("vehicle.field.scale"), self.scale_edit)
        self.form.addRow(tr("vehicle.field.notes"), self.notes_edit)
        self.form.addRow(tr("vehicle.field.driver"), self.driver_combo)
        self.form.addRow(tr("vehicle.field.favorite"), self.favorite_check)

    def _sync_favorite(self) -> None:
        """A favorite belongs to a driver, so the box stays off while none is selected."""
        has_driver = self.driver_combo.currentData() is not None
        self.favorite_check.setEnabled(has_driver)
        if not has_driver:
            self.favorite_check.setChecked(False)

    def _stored_name(self) -> str:
        """Manufacturer and model form the name. An existing name stays without a manufacturer."""
        manufacturer = self.manufacturer_edit.text().strip()
        model = self.model_edit.text().strip()
        if manufacturer and model:
            composed = f"{manufacturer} {model}"
            return composed if len(composed) <= 100 else model
        if self._vehicle is not None and not manufacturer:
            return self._vehicle.name
        return model or manufacturer

    def submit(self) -> None:
        driver_id = self.driver_combo.currentData()
        start_number = None if self._vehicle is None else self._vehicle.start_number
        data = VehicleInput(
            name=self._stored_name(),
            model=self.model_edit.text(),
            manufacturer=self.manufacturer_edit.text(),
            scale=self.scale_edit.text(),
            notes=self.notes_edit.toPlainText(),
            start_number=start_number,
            driver_id=None if driver_id is None else DriverId(driver_id),
            is_favorite=self.favorite_check.isChecked(),
        )
        if self._vehicle is None:
            self._vehicles.create_vehicle(data)
        else:
            self._vehicles.update_vehicle(self._vehicle.id, data)


class VehiclesPage(EntityPage):
    def __init__(
        self,
        translator: Translator,
        vehicles: VehicleService,
        drivers: DriverService,
        history: Callable[[], RaceHistoryCatalog | None] | None = None,
        tracks: Callable[[], Sequence[TrackInfo]] | None = None,
    ) -> None:
        super().__init__(
            translator,
            title_key="nav.vehicles",
            header_keys=[
                "vehicle.field.name",
                "vehicle.field.model",
                "vehicle.field.manufacturer",
                "vehicle.field.driver",
                "vehicle.field.favorite",
                "common.active",
            ],
            name="vehicles",
        )
        self._vehicles = vehicles
        self._drivers = drivers
        self.unassign_button = QPushButton(translator.translate("vehicle.unassign"))
        self.unassign_button.setObjectName("vehicles-unassign")
        set_role(self.unassign_button, "ghost")
        self.buttons.insertWidget(self.buttons.count() - 1, self.unassign_button)
        self.unassign_button.clicked.connect(lambda: self.unassign_selected())
        self.career: CareerPanel | None = None
        if history is not None and tracks is not None:
            self.career = CareerPanel(
                translator,
                history,
                tracks,
                self._driver_choices,
                subject="vehicle",
            )
            layout = self.layout()
            assert isinstance(layout, QVBoxLayout)
            layout.insertWidget(layout.count() - 1, self.career)
            self.table.itemSelectionChanged.connect(self._show_career)

    def refresh(self) -> None:
        super().refresh()
        self._show_career()

    def _show_career(self) -> None:
        if self.career is not None:
            self.career.show_subject(selected_id(self.table))

    def _driver_choices(self) -> list[tuple[int, str]]:
        return [(int(driver.id), driver.label) for driver in self._drivers.list_drivers()]

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
                    vehicle.label,
                    vehicle.model or "",
                    vehicle.manufacturer or "",
                    "" if vehicle.driver_id is None else drivers.get(vehicle.driver_id, ""),
                    tr("common.yes" if vehicle.is_favorite else "common.no"),
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
