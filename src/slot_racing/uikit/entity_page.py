"""List page with add / edit / (de)activate / delete, shared by drivers, vehicles and tracks."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.i18n import Translator
from slot_racing.uikit.errors import describe_error, is_expected
from slot_racing.uikit.theme import SPACE, configure_page, set_role
from slot_racing.uikit.widgets import StatusLabel, fill_table, make_table, selected_id

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EntityRow:
    id: int
    cells: tuple[str, ...]
    active: bool = True


def _ask_yes_no(parent: QWidget, title: str, text: str) -> bool:
    answer = QMessageBox.question(parent, title, text)
    return answer == QMessageBox.StandardButton.Yes


class EntityPage(QWidget):
    """Table of entities plus action buttons.

    Subclasses provide the data and the operations. Every action runs through :meth:`_guard`, so
    a failing action is shown as a message on the page and never reaches the application.
    ``dialog_runner`` and ``confirm`` can be replaced, which keeps the page testable without
    blocking modal dialogs.
    """

    def __init__(
        self,
        translator: Translator,
        *,
        title_key: str,
        header_keys: Sequence[str],
        name: str,
    ) -> None:
        super().__init__()
        self.translator = translator
        self.dialog_runner: Callable[[QDialog], int] = lambda dialog: dialog.exec()
        self.confirm: Callable[[str], bool] = lambda text: _ask_yes_no(
            self, translator.translate("common.confirm"), text
        )
        self._rows: list[EntityRow] = []
        tr = translator.translate
        self.setAccessibleDescription(tr(title_key))

        self.table = make_table([tr(key) for key in header_keys], f"{name}-table")
        self.status = StatusLabel(f"{name}-status")
        self.add_button = QPushButton(tr("common.add"))
        self.add_button.setObjectName(f"{name}-add")
        self.edit_button = QPushButton(tr("common.edit"))
        self.edit_button.setObjectName(f"{name}-edit")
        self.toggle_button = QPushButton(tr("common.deactivate"))
        self.toggle_button.setObjectName(f"{name}-toggle")
        self.delete_button = QPushButton(tr("common.delete"))
        self.delete_button.setObjectName(f"{name}-delete")

        set_role(self.add_button, "primary")
        set_role(self.edit_button, "secondary")
        set_role(self.toggle_button, "ghost")
        set_role(self.delete_button, "danger")

        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(SPACE.sm)
        for button in (self.add_button, self.edit_button, self.toggle_button, self.delete_button):
            self.buttons.addWidget(button)
        self.buttons.addStretch(1)

        layout = QVBoxLayout(self)
        configure_page(layout)
        layout.addLayout(self.buttons)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.status)

        self.add_button.clicked.connect(lambda: self.add())
        self.edit_button.clicked.connect(lambda: self.edit_selected())
        self.toggle_button.clicked.connect(lambda: self.toggle_active_selected())
        self.delete_button.clicked.connect(lambda: self.delete_selected())
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.cellDoubleClicked.connect(lambda *_: self.edit_selected())

    def load_rows(self) -> list[EntityRow]:
        raise NotImplementedError

    def create_dialog(self, entity_id: int | None) -> QDialog:
        raise NotImplementedError

    def set_entity_active(self, entity_id: int, active: bool) -> None:
        raise NotImplementedError

    def delete_entity(self, entity_id: int) -> None:
        raise NotImplementedError

    def delete_confirmation(self, row: EntityRow) -> str:
        return self.translator.format("common.confirm_delete", name=row.cells[0])

    def refresh(self) -> None:
        self._guard(self._reload)

    def row_count(self) -> int:
        return self.table.rowCount()

    def select_id(self, entity_id: int) -> None:
        for index, row in enumerate(self._rows):
            if row.id == entity_id:
                self.table.selectRow(index)
                return

    def add(self) -> None:
        self._guard(lambda: self._edit(None))

    def edit_selected(self) -> None:
        entity_id = selected_id(self.table)
        if entity_id is not None:
            self._guard(lambda: self._edit(entity_id))

    def toggle_active_selected(self) -> None:
        row = self._selected_row()
        if row is not None:
            self._guard(lambda: self._toggle(row))

    def delete_selected(self) -> None:
        row = self._selected_row()
        if row is not None:
            self._guard(lambda: self._delete(row))

    def showEvent(self, event: object) -> None:  # noqa: N802
        super().showEvent(event)  # type: ignore[arg-type]
        self.refresh()

    def _edit(self, entity_id: int | None) -> None:
        dialog = self.create_dialog(entity_id)
        if self.dialog_runner(dialog) == QDialog.DialogCode.Accepted:
            self._reload()
            self.status.show_info(self.translator.translate("common.saved"))

    def _toggle(self, row: EntityRow) -> None:
        self.set_entity_active(row.id, not row.active)
        self._reload()
        self.status.show_info(self.translator.translate("common.saved"))

    def _delete(self, row: EntityRow) -> None:
        if not self.confirm(self.delete_confirmation(row)):
            return
        self.delete_entity(row.id)
        self._reload()
        self.status.show_info(self.translator.translate("common.deleted"))

    def _reload(self) -> None:
        self._rows = self.load_rows()
        fill_table(self.table, [row.cells for row in self._rows], [row.id for row in self._rows])
        self._update_buttons()

    def _selected_row(self) -> EntityRow | None:
        entity_id = selected_id(self.table)
        return next((row for row in self._rows if row.id == entity_id), None)

    def _update_buttons(self) -> None:
        row = self._selected_row()
        has_selection = row is not None
        self.edit_button.setEnabled(has_selection)
        self.delete_button.setEnabled(has_selection)
        self.toggle_button.setEnabled(has_selection)
        active = row.active if row is not None else True
        self.toggle_button.setText(
            self.translator.translate("common.deactivate" if active else "common.activate")
        )

    def _guard(self, action: Callable[[], None]) -> None:
        try:
            action()
        except Exception as error:
            if not is_expected(error):
                logger.exception("Action failed on %s", type(self).__name__)
            self.status.show_error(describe_error(self.translator, error))
