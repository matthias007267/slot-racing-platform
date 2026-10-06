"""The one part library. Cards can be dragged onto the plan; definitions stay here."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QMimeData, QSize, Qt
from PySide6.QtGui import QDrag, QPainter, QResizeEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from slot_racing.modules.track_planner.parts import PartRecord, PartSpec
from slot_racing.modules.track_planner.ui.track_paint import apply_preview_transform, paint_part

PART_MIME = "application/x-slot-racing-part"
_CAPTION_ROLE = Qt.ItemDataRole.UserRole + 1


class PartPreview(QWidget):
    """Small top view of a definition. The plan paints the same way."""

    def __init__(self, spec: PartSpec, *, color_coding: bool = False) -> None:
        super().__init__()
        self.setObjectName("planner-part-preview")
        line = max(self.fontMetrics().height(), 16)
        self.setFixedSize(line * 5, line * 3)
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
        self._fitting = False
        self.setObjectName("planner-library")
        self.setMinimumWidth(340)
        self.setUniformItemSizes(False)
        self.setSpacing(6)
        self.setDragEnabled(True)
        self.setDragDropMode(QListWidget.DragDropMode.DragOnly)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def set_records(self, records: Sequence[PartRecord]) -> None:
        self.clear()
        for record in records:
            spec = record.spec
            item = QListWidgetItem()
            item.setText("")
            item.setData(Qt.ItemDataRole.UserRole, record.id)
            item.setData(_CAPTION_ROLE, f"{spec.name} ({spec.article_number})")
            card = _card(record, color_coding=self._color_coding)
            item.setSizeHint(_card_size(card))
            self.addItem(item)
            self.setItemWidget(item, card)
        self._fit_item_widths()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit_item_widths()

    def _fit_item_widths(self) -> None:
        """Lay each card out at the visible width so a long name can ellipsize."""
        if self._fitting:
            return
        width = self.viewport().width()
        if width <= 0:
            return
        self._fitting = True
        try:
            for row in range(self.count()):
                item = self.item(row)
                if item is None:
                    continue
                hint = item.sizeHint()
                if hint.width() == width:
                    continue
                item.setSizeHint(QSize(width, hint.height()))
        finally:
            self._fitting = False

    def mimeData(self, items: Sequence[QListWidgetItem]) -> QMimeData:  # noqa: N802
        mime = QMimeData()
        if items:
            part_id = items[0].data(Qt.ItemDataRole.UserRole)
            mime.setData(PART_MIME, str(int(part_id)).encode("ascii"))
            caption = items[0].data(_CAPTION_ROLE)
            mime.setText("" if caption is None else str(caption))
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


def _card(record: PartRecord, *, color_coding: bool) -> QWidget:
    """Preview on the left. The text beside it is designation, article number and scale."""
    spec = record.spec
    card = QWidget()
    card.setObjectName("planner-part-card")
    preview = PartPreview(spec, color_coding=color_coding)
    name = _NameLabel(spec.name)
    article = QLabel(spec.article_number)
    article.setObjectName("planner-part-article")
    scale = QLabel(spec.scale)
    scale.setObjectName("planner-part-scale")
    text = QVBoxLayout()
    text.setContentsMargins(8, 0, 0, 0)
    text.setSpacing(1)
    text.addWidget(name)
    text.addWidget(article)
    text.addWidget(scale)
    text.addStretch(1)
    row = QHBoxLayout(card)
    row.setContentsMargins(6, 4, 6, 4)
    row.setSpacing(8)
    row.addWidget(preview, 0, Qt.AlignmentFlag.AlignVCenter)
    row.addLayout(text, 1)
    return card


class _NameLabel(QLabel):
    """Designation beside the preview. A narrow column ellipsizes instead of overflowing."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setObjectName("planner-part-name")
        self.setToolTip(text)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def paintEvent(self, _event: object) -> None:  # noqa: N802
        painter = QPainter(self)
        rect = self.contentsRect()
        shown = self.fontMetrics().elidedText(
            self.text(), Qt.TextElideMode.ElideRight, max(rect.width(), 0)
        )
        painter.drawText(
            rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            shown,
        )
        painter.end()


def _card_size(card: QWidget) -> QSize:
    """Three text lines beside the preview. The height follows the current font."""
    preview = card.findChild(PartPreview)
    name_label = card.findChild(QLabel, "planner-part-name")
    if preview is None or name_label is None:
        return QSize(320, 72)
    line = max(name_label.fontMetrics().height(), 1)
    height = max(preview.height() + 12, line * 3 + 16)
    return QSize(320, height)
