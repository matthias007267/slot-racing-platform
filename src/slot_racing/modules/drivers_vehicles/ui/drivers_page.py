"""Driver list and editor."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QVBoxLayout, QWidget

from slot_racing.core.catalog import DriverInfo, RaceHistoryCatalog, TrackInfo
from slot_racing.core.domain import DriverId
from slot_racing.core.i18n import Translator
from slot_racing.modules.drivers_vehicles.service import DriverInput, DriverService, VehicleService
from slot_racing.modules.drivers_vehicles.ui.start_number_field import StartNumberPicker
from slot_racing.uikit import EntityPage, EntityRow, FormDialog
from slot_racing.uikit.career import CareerPanel
from slot_racing.uikit.widgets import fill_table, make_table, selected_id


class DriverDialog(FormDialog):
    def __init__(
        self,
        translator: Translator,
        service: DriverService,
        driver: DriverInfo | None,
        parent: QWidget | None = None,
    ) -> None:
        tr = translator.translate
        super().__init__(
            translator, tr("driver.dialog.edit" if driver else "driver.dialog.new"), parent
        )
        self._service = service
        self._driver = driver
        self.name_edit = QLineEdit(driver.name if driver else "")
        self.name_edit.setObjectName("driver-name")
        self.display_name_edit = QLineEdit(driver.display_name or "" if driver else "")
        self.display_name_edit.setObjectName("driver-display-name")
        current = None if driver is None else driver.start_number
        self.start_number_edit = StartNumberPicker(
            translator, service.defined_start_numbers(), current, "driver-start-number"
        )
        self.form.addRow(tr("driver.field.name"), self.name_edit)
        self.form.addRow(tr("driver.field.display_name"), self.display_name_edit)
        self.form.addRow(tr("driver.field.start_number"), self.start_number_edit)

    def submit(self) -> None:
        data = DriverInput(
            name=self.name_edit.text(),
            display_name=self.display_name_edit.text(),
            start_number=self.start_number_edit.value(),
        )
        if self._driver is None:
            self._service.create_driver(data)
        else:
            self._service.update_driver(self._driver.id, data)


class DriversPage(EntityPage):
    def __init__(
        self,
        translator: Translator,
        service: DriverService,
        vehicles: VehicleService,
        history: Callable[[], RaceHistoryCatalog | None] | None = None,
        tracks: Callable[[], Sequence[TrackInfo]] | None = None,
    ) -> None:
        super().__init__(
            translator,
            title_key="nav.drivers",
            header_keys=[
                "driver.field.name",
                "driver.field.display_name",
                "driver.field.start_number",
                "common.active",
            ],
            name="drivers",
        )
        self._service = service
        self._vehicles = vehicles
        tr = translator.translate
        self.vehicles_heading = QLabel(tr("driver.vehicles"))
        self.vehicles_heading.setObjectName("driver-vehicles-heading")
        self.vehicles_table = make_table(
            [tr("vehicle.field.name"), tr("vehicle.field.model")], "driver-vehicles-table"
        )
        self.vehicles_empty = QLabel(tr("driver.vehicles.none_selected"))
        self.vehicles_empty.setObjectName("driver-vehicles-empty")
        self.vehicles_empty.setWordWrap(True)
        layout = self.layout()
        assert isinstance(layout, QVBoxLayout)
        layout.removeWidget(self.status)
        layout.addWidget(self.vehicles_heading)
        layout.addWidget(self.vehicles_table)
        layout.addWidget(self.vehicles_empty)
        self.career: CareerPanel | None = None
        if history is not None and tracks is not None:
            self.career = CareerPanel(
                translator,
                history,
                tracks,
                self._vehicle_choices,
                subject="driver",
            )
            layout.addWidget(self.career)
        layout.addWidget(self.status)
        self.table.itemSelectionChanged.connect(self._show_owned_vehicles)

    def refresh(self) -> None:
        super().refresh()
        self._show_owned_vehicles()

    def _show_owned_vehicles(self) -> None:
        """Read-only list of the vehicles assigned to the selected driver."""
        tr = self.translator.translate
        driver_id = selected_id(self.table)
        if driver_id is None:
            fill_table(self.vehicles_table, [], keep_selection=False)
            self.vehicles_empty.setText(tr("driver.vehicles.none_selected"))
            if self.career is not None:
                self.career.show_subject(None)
            return
        owned = self._vehicles.list_vehicles(driver_id=DriverId(driver_id))
        if not owned:
            fill_table(self.vehicles_table, [], keep_selection=False)
            self.vehicles_empty.setText(tr("driver.vehicles.none"))
        else:
            fill_table(
                self.vehicles_table,
                [(vehicle.label, vehicle.model or "") for vehicle in owned],
                keep_selection=False,
            )
            self.vehicles_empty.setText("")
        if self.career is not None:
            self.career.show_subject(driver_id)

    def _vehicle_choices(self) -> list[tuple[int, str]]:
        return [(int(vehicle.id), vehicle.label) for vehicle in self._vehicles.list_vehicles()]

    def load_rows(self) -> list[EntityRow]:
        tr = self.translator.translate
        return [
            EntityRow(
                id=driver.id,
                cells=(
                    driver.name,
                    driver.display_name or "",
                    "" if driver.start_number is None else str(driver.start_number),
                    tr("common.yes" if driver.is_active else "common.no"),
                ),
                active=driver.is_active,
            )
            for driver in self._service.list_drivers()
        ]

    def create_dialog(self, entity_id: int | None) -> QDialog:
        driver = None if entity_id is None else self._service.get_driver(DriverId(entity_id))
        return DriverDialog(self.translator, self._service, driver, self)

    def set_entity_active(self, entity_id: int, active: bool) -> None:
        self._service.set_active(DriverId(entity_id), active)

    def delete_entity(self, entity_id: int) -> None:
        self._service.delete_driver(DriverId(entity_id))
