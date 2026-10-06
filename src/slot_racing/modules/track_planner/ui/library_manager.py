"""Manage stored part definitions. Placed instances stay on their plans."""

from __future__ import annotations

from collections.abc import Collection

from PySide6.QtCore import Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.i18n import Translator
from slot_racing.modules.track_planner.parts import PartRecord
from slot_racing.modules.track_planner.service import TrackPlannerService
from slot_racing.modules.track_planner.ui.library_dialog import PartDialog
from slot_racing.modules.track_planner.ui.library_view import PartPreview
from slot_racing.uikit.errors import describe_error
from slot_racing.uikit.theme import set_role
from slot_racing.uikit.widgets import StatusLabel


class LibraryManager(QDialog):
    """List, search, edit and delete definitions. A used definition cannot be deleted."""

    def __init__(
        self,
        translator: Translator,
        planner: TrackPlannerService,
        used_part_ids: Collection[int] = (),
        *,
        color_coding: bool = False,
    ) -> None:
        super().__init__()
        self.setObjectName("library-manager")
        self.setWindowTitle(translator.translate("planner.library.manage"))
        self.setMinimumSize(560, 480)
        self._translator = translator
        self._planner = planner
        self._used = frozenset(used_part_ids)
        self.color_coding = color_coding
        self._selected: int | None = None
        self._rows: list[_PartRow] = []
        translate = translator.translate
        self.search = QLineEdit()
        self.search.setObjectName("library-manager-search")
        self.search.setPlaceholderText(translate("planner.library.search"))
        self.search.textChanged.connect(self._apply_search)
        self._list = QWidget()
        self._list.setObjectName("library-manager-list")
        self._rows_layout = QVBoxLayout(self._list)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setObjectName("library-manager-scroll")
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._list)
        self.edit_button = QPushButton(translate("planner.library.edit"))
        self.edit_button.setObjectName("library-manager-edit")
        self.edit_button.clicked.connect(self.edit_selected)
        self.delete_button = QPushButton(translate("planner.library.delete"))
        self.delete_button.setObjectName("library-manager-delete")
        set_role(self.delete_button, "ghost")
        self.delete_button.clicked.connect(self.delete_selected)
        self.edit_button.setEnabled(False)
        self.delete_button.setEnabled(False)
        self.status = StatusLabel("library-manager-message")
        actions = QHBoxLayout()
        actions.addWidget(self.edit_button)
        actions.addWidget(self.delete_button)
        actions.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addWidget(self.search)
        layout.addWidget(scroll, 1)
        layout.addLayout(actions)
        layout.addWidget(self.status)
        self.reload()

    def reload(self) -> None:
        records = self._planner.list_parts()
        while self._rows_layout.count() > 1:
            item = self._rows_layout.takeAt(0)
            widget = None if item is None else item.widget()
            if widget is not None:
                widget.deleteLater()
        self._rows = []
        known = {record.id for record in records}
        if self._selected not in known:
            self._selected = None
        for record in records:
            row = _PartRow(record, self)
            self._rows.append(row)
            self._rows_layout.insertWidget(self._rows_layout.count() - 1, row)
            row.set_current(row.part_id == self._selected)
        self.edit_button.setEnabled(self._selected is not None)
        self.delete_button.setEnabled(self._selected is not None)
        self._apply_search()

    def select_part(self, part_id: int) -> None:
        self._selected = part_id
        for row in self._rows:
            row.set_current(row.part_id == part_id)
        self.edit_button.setEnabled(True)
        self.delete_button.setEnabled(True)

    def edit_selected(self) -> None:
        record = self._current()
        if record is None:
            return
        dialog = PartDialog(self._translator, self._planner, record)
        if dialog.exec():
            self.reload()
            self.status.show_info(self._translator.translate("planner.library.updated"))

    def delete_selected(self) -> None:
        record = self._current()
        if record is None:
            return
        if record.id in self._used:
            self.status.show_error(self._translator.format("error.planner.part_in_use", count=1))
            return
        try:
            self._planner.delete_part(record.id)
        except Exception as error:
            self.status.show_error(describe_error(self._translator, error))
            return
        self._selected = None
        self.reload()
        self.status.show_info(self._translator.translate("planner.library.deleted"))

    def _current(self) -> PartRecord | None:
        if self._selected is None:
            return None
        for record in self._planner.list_parts():
            if record.id == self._selected:
                return record
        return None

    def _apply_search(self) -> None:
        query = self.search.text().strip().casefold()
        for row in self._rows:
            row.setVisible(not query or query in row.haystack)


class _PartRow(QWidget):
    """Preview on the left, name and scale on the right. Nothing else shares that row."""

    def __init__(self, record: PartRecord, owner: LibraryManager) -> None:
        super().__init__()
        self.setObjectName("library-manager-row")
        self.part_id = record.id
        spec = record.spec
        self.haystack = " ".join(
            (spec.name, spec.scale, spec.article_number, spec.system, spec.category)
        ).casefold()
        self._owner = owner
        preview = PartPreview(spec, color_coding=owner.color_coding)
        preview.setObjectName("library-manager-preview")
        self.name = QLabel(spec.name)
        self.name.setObjectName("library-manager-name")
        self.name.setWordWrap(True)
        self.scale = QLabel(spec.scale)
        self.scale.setObjectName("library-manager-scale")
        text = QVBoxLayout()
        text.setContentsMargins(8, 0, 0, 0)
        text.setSpacing(2)
        text.addWidget(self.name)
        text.addWidget(self.scale)
        text.addStretch(1)
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 6, 8, 6)
        row.addWidget(preview, 0, Qt.AlignmentFlag.AlignTop)
        row.addLayout(text, 1)

    def set_current(self, current: bool) -> None:
        self.setProperty("current", current)
        self.setAutoFillBackground(current)
        if current:
            self.setStyleSheet("background: rgba(80, 140, 255, 0.18);")
        else:
            self.setStyleSheet("")

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._owner.select_part(self.part_id)
        super().mousePressEvent(event)
