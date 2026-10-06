"""The one part library. Cards can be dragged onto the plan; definitions stay here."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QMimeData, QSize, Qt
from PySide6.QtGui import QDrag, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.i18n import Translator
from slot_racing.modules.track_planner.parts import PartRecord, PartSpec
from slot_racing.modules.track_planner.ui.track_paint import apply_preview_transform, paint_part

PART_MIME = "application/x-slot-racing-part"


class PartPreview(QWidget):
    """Small top view of a definition. The plan paints the same way."""

    def __init__(self, spec: PartSpec, *, color_coding: bool = False) -> None:
        super().__init__()
        self.setObjectName("planner-part-preview")
        self.setFixedSize(72, 48)
        self._spec = spec
        self.color_coding = color_coding

    def set_color_coding(self, enabled: bool) -> None:
        if self.color_coding == enabled:
            return
        self.color_coding = enabled
        self.update()

    def paintEvent(self, _event: object) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        apply_preview_transform(painter, self._spec, self.width(), self.height())
        paint_part(
            painter,
            self._spec,
            color_coding=self.color_coding,
            selected=False,
            start_straight=False,
        )


class PartLibrary(QListWidget):
    """Scrollable catalogue. Each row is one stored definition."""

    def __init__(self) -> None:
        super().__init__()
        self._color_coding = False
        self.setObjectName("planner-library")
        self.setMinimumWidth(340)
        self.setUniformItemSizes(False)
        self.setSpacing(6)
        self.setDragEnabled(True)
        self.setDragDropMode(QListWidget.DragDropMode.DragOnly)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def set_records(self, records: Sequence[PartRecord], translator: Translator) -> None:
        self.clear()
        for record in records:
            spec = record.spec
            item = QListWidgetItem(f"{spec.name} ({spec.article_number})")
            item.setData(Qt.ItemDataRole.UserRole, record.id)
            card = _card(record, translator, color_coding=self._color_coding)
            item.setSizeHint(card.sizeHint().expandedTo(QSize(300, 64)))
            self.addItem(item)
            self.setItemWidget(item, card)

    def mimeData(self, items: Sequence[QListWidgetItem]) -> QMimeData:  # noqa: N802
        mime = QMimeData()
        if items:
            part_id = items[0].data(Qt.ItemDataRole.UserRole)
            mime.setData(PART_MIME, str(int(part_id)).encode("ascii"))
            mime.setText(items[0].text())
        return mime

    def startDrag(self, supported_actions: Qt.DropAction) -> None:  # noqa: N802
        item = self.currentItem()
        if item is None:
            return
        drag = QDrag(self)
        drag.setMimeData(self.mimeData([item]))
        card = self.itemWidget(item)
        if card is not None:
            drag.setPixmap(
                card.grab().scaledToWidth(160, Qt.TransformationMode.SmoothTransformation)
            )
        drag.exec(supported_actions)

    def set_color_coding(self, enabled: bool) -> None:
        """Restyle the visible cards. The list itself is not rebuilt."""
        self._color_coding = enabled
        for row in range(self.count()):
            item = self.item(row)
            card = self.itemWidget(item) if item is not None else None
            if card is None:
                continue
            preview = card.findChild(PartPreview)
            if preview is not None:
                preview.set_color_coding(enabled)


def _card(record: PartRecord, translator: Translator, *, color_coding: bool) -> QWidget:
    spec = record.spec
    card = QWidget()
    card.setObjectName("planner-part-card")
    preview = PartPreview(spec, color_coding=color_coding)
    name = QLabel(spec.name)
    name.setObjectName("planner-part-name")
    system = QLabel(spec.system)
    system.setObjectName("planner-part-system")
    article = QLabel(spec.article_number)
    article.setObjectName("planner-part-article")
    scale = QLabel(spec.scale)
    scale.setObjectName("planner-part-scale")
    category = QLabel(translator.translate(f"planner.category.{spec.category}"))
    category.setObjectName("planner-part-category")
    text = QVBoxLayout()
    text.setContentsMargins(0, 0, 0, 0)
    text.setSpacing(0)
    text.addWidget(name)
    text.addWidget(system)
    text.addWidget(article)
    text.addWidget(scale)
    text.addWidget(category)
    row = QHBoxLayout(card)
    row.setContentsMargins(4, 4, 4, 4)
    row.addWidget(preview)
    row.addLayout(text, 1)
    return card
