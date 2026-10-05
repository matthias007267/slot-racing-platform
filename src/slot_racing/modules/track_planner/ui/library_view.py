"""The one part library. Cards can be dragged onto the plan; definitions stay here."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QMimeData, QPointF, QSize, Qt
from PySide6.QtGui import QColor, QDrag, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from slot_racing.core.i18n import Translator
from slot_racing.modules.track_planner.parts import PartRecord
from slot_racing.uikit.theme import COLORS

PART_MIME = "application/x-slot-racing-part"


class PartPreview(QWidget):
    """Small top view of a definition. The outline is the one stored on the part."""

    def __init__(self, outline: tuple[tuple[float, float], ...]) -> None:
        super().__init__()
        self.setObjectName("planner-part-preview")
        self.setFixedSize(72, 48)
        self._outline = outline

    def paintEvent(self, _event: object) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor(COLORS.accent), 1.5))
        painter.setBrush(QColor(COLORS.elevated))
        painter.drawPolygon(_fitted(self._outline, self.width(), self.height()))


class PartLibrary(QListWidget):
    """Scrollable catalogue. Each row is one stored definition."""

    def __init__(self) -> None:
        super().__init__()
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
            card = _card(record, translator)
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


def _card(record: PartRecord, translator: Translator) -> QWidget:
    spec = record.spec
    card = QWidget()
    card.setObjectName("planner-part-card")
    preview = PartPreview(spec.outline)
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


def _fitted(outline: tuple[tuple[float, float], ...], width: int, height: int) -> QPolygonF:
    points = outline or ((-20.0, -10.0), (20.0, -10.0), (20.0, 10.0), (-20.0, 10.0))
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    span_x = max(max(xs) - min(xs), 1.0)
    span_y = max(max(ys) - min(ys), 1.0)
    scale = min((width - 8) / span_x, (height - 8) / span_y)
    center_x = (min(xs) + max(xs)) / 2
    center_y = (min(ys) + max(ys)) / 2
    polygon = QPolygonF()
    for x, y in points:
        polygon.append(
            QPointF(width / 2 + (x - center_x) * scale, height / 2 + (y - center_y) * scale)
        )
    return polygon
