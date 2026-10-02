"""Basic widgets and table helpers."""

from __future__ import annotations

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

ID_ROLE = Qt.ItemDataRole.UserRole


def heading(text: str) -> QLabel:
    label = QLabel(text)
    font = label.font()
    font.setPointSize(font.pointSize() + 6)
    font.setBold(True)
    label.setFont(font)
    return label


class StatusLabel(QLabel):
    """One line for feedback. Errors are red, information is neutral."""

    def __init__(self, object_name: str = "status-message") -> None:
        super().__init__()
        self.setObjectName(object_name)
        self.setWordWrap(True)

    def show_error(self, message: str) -> None:
        self.setStyleSheet("color: #b00020;")
        self.setText(message)

    def show_info(self, message: str) -> None:
        self.setStyleSheet("")
        self.setText(message)

    def clear_message(self) -> None:
        self.setStyleSheet("")
        self.setText("")


def make_table(headers: Sequence[str], object_name: str) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setObjectName(object_name)
    table.setHorizontalHeaderLabels(list(headers))
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.verticalHeader().setVisible(False)
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
