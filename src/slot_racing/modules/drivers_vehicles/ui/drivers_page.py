"""Driver list and editor."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QLineEdit, QSpinBox, QWidget

from slot_racing.core.catalog import DriverInfo
from slot_racing.core.domain import DriverId
from slot_racing.core.i18n import Translator
from slot_racing.modules.drivers_vehicles.service import DriverInput, DriverService
from slot_racing.uikit import EntityPage, EntityRow, FormDialog


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
        self.start_number_edit = QSpinBox()
        self.start_number_edit.setObjectName("driver-start-number")
        self.start_number_edit.setRange(0, 9999)
        self.start_number_edit.setSpecialValueText(tr("common.none"))
        self.start_number_edit.setValue(driver.start_number or 0 if driver else 0)
        self.form.addRow(tr("driver.field.name"), self.name_edit)
        self.form.addRow(tr("driver.field.display_name"), self.display_name_edit)
        self.form.addRow(tr("driver.field.start_number"), self.start_number_edit)

    def submit(self) -> None:
        data = DriverInput(
            name=self.name_edit.text(),
            display_name=self.display_name_edit.text(),
            start_number=self.start_number_edit.value() or None,
        )
        if self._driver is None:
            self._service.create_driver(data)
        else:
            self._service.update_driver(self._driver.id, data)


class DriversPage(EntityPage):
    def __init__(self, translator: Translator, service: DriverService) -> None:
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
