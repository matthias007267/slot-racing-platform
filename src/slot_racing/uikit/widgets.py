"""Basic widgets and table helpers."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
)

from slot_racing.uikit.theme import set_role, set_tone

ID_ROLE = Qt.ItemDataRole.UserRole
_NUMERIC = re.compile(r"^[\d:./,\-\s/]+$")
_ROW_HEIGHT = 34


def heading(text: str) -> QLabel:
    """Section title inside a page. The shell header already names the page itself."""
    label = QLabel(text)
    set_role(label, "page-title")
    return label


class StatusLabel(QLabel):
    """One line for feedback. Errors use the error tone, information stays neutral."""

    def __init__(self, object_name: str = "status-message") -> None:
        super().__init__()
        self.setObjectName(object_name)
        self.setWordWrap(True)

    def show_error(self, message: str) -> None:
        set_tone(self, "error")
        self.setText(message)

    def show_info(self, message: str) -> None:
        set_tone(self, "")
        self.setText(message)

    def clear_message(self) -> None:
        set_tone(self, "")
        self.setText("")


class StatusPill(QLabel):
    """Status that is readable from the word, with a tone for the color."""

    def __init__(self, object_name: str = "status-pill") -> None:
        super().__init__()
        self.setObjectName(object_name)
        set_role(self, "status")

    def set_status(self, text: str, tone: str) -> None:
        self.setText(f"● {text}")
        set_tone(self, tone)


def make_table(headers: Sequence[str], object_name: str) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setObjectName(object_name)
    table.setHorizontalHeaderLabels(list(headers))
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(_ROW_HEIGHT)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    return table


def fill_table(
    table: QTableWidget,
    rows: Sequence[Sequence[str]],
    ids: Sequence[int] | None = None,
    *,
    keep_selection: bool = True,
) -> None:
    """Replace the content of ``table``. ``ids`` are attached to the first column of each row."""
    selected = selected_id(table) if keep_selection else None
    table.setRowCount(len(rows))
    for row_index, cells in enumerate(rows):
        for column, text in enumerate(cells):
            item = QTableWidgetItem(text)
            item.setTextAlignment(_alignment(text))
            if column == 0 and ids is not None:
                item.setData(ID_ROLE, ids[row_index])
            table.setItem(row_index, column, item)
    if selected is not None and ids is not None and selected in ids:
        table.selectRow(list(ids).index(selected))


def selected_id(table: QTableWidget) -> int | None:
    row = table.currentRow()
    if row < 0 or table.selectionModel() is None or not table.selectionModel().hasSelection():
        return None
    item = table.item(row, 0)
    if item is None:
        return None
    value = item.data(ID_ROLE)
    return value if isinstance(value, int) else None


def format_datetime(value: datetime | None) -> str:
    return "-" if value is None else value.strftime("%d.%m.%Y %H:%M")


def _alignment(text: str) -> Qt.AlignmentFlag:
    """Times, counts and plain numbers line up on the right. Names stay on the left."""
    stripped = text.strip()
    horizontal = Qt.AlignmentFlag.AlignLeft
    numeric = stripped == "-" or (
        bool(stripped)
        and any(char.isdigit() for char in stripped)
        and _NUMERIC.fullmatch(stripped) is not None
    )
    if numeric:
        horizontal = Qt.AlignmentFlag.AlignRight
    return horizontal | Qt.AlignmentFlag.AlignVCenter
