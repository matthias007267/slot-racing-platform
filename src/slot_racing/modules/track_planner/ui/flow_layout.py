"""A row that wraps instead of squeezing its widgets.

Qt has no flow layout in Widgets. This one keeps each item at its size hint,
so a button stays as wide as its text, and starts another row when the line
is full. A wide window therefore stays one row.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QLabel,
    QLayout,
    QLayoutItem,
    QPushButton,
    QSizePolicy,
    QStyle,
    QWidget,
)


class FlowLayout(QLayout):
    def __init__(self, parent: QWidget | None = None, *, spacing: int = 6) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        """Width of one unwrapped row. The layout may still be given less and wrap."""
        margins = self.contentsMargins()
        hints = [self._hint(item) for item in self._taken()]
        if not hints:
            return QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        gaps = self.spacing() * (len(hints) - 1)
        width = sum(hint.width() for hint in hints) + gaps
        height = max(hint.height() for hint in hints)
        return QSize(
            width + margins.left() + margins.right(),
            height + margins.top() + margins.bottom(),
        )

    def minimumSize(self) -> QSize:  # noqa: N802
        """The widest single control. Narrower than that, a label would be clipped."""
        size = QSize()
        for item in self._taken():
            size = size.expandedTo(self._hint(item))
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _arrange(self, rect: QRect, *, apply: bool) -> int:
        margins = self.contentsMargins()
        space_x = self.spacing()
        space_y = self.spacing()
        left = rect.x() + margins.left()
        limit = rect.right() - margins.right()
        x = left
        y = rect.y() + margins.top()
        line_height = 0
        line: list[tuple[QLayoutItem, int, QSize]] = []

        def flush(row_y: int, row_height: int) -> None:
            if not apply or not line:
                return
            used = sum(hint.width() for _item, _x, hint in line)
            used += space_x * (len(line) - 1)
            extra = max(0, limit - left - used + 1)
            growing = [index for index, (item, _x, _hint) in enumerate(line) if _expands(item)]
            share = extra // len(growing) if growing else 0
            shift = 0
            for item, item_x, hint in line:
                width = hint.width()
                if share and _expands(item):
                    width += share
                item.setGeometry(
                    QRect(item_x + shift, row_y, width, max(hint.height(), row_height))
                )
                if share and _expands(item):
                    shift += share

        for item in self._taken():
            hint = self._hint(item)
            if line and x + hint.width() > limit + 1:
                flush(y, line_height)
                line = []
                x = left
                y += line_height + space_y
                line_height = 0
            line.append((item, x, hint))
            x += hint.width() + space_x
            line_height = max(line_height, hint.height())
        flush(y, line_height)
        return y + line_height + margins.bottom() - rect.y()

    def _taken(self) -> list[QLayoutItem]:
        """Hidden controls must not reserve a gap in the row."""
        return [item for item in self._items if not item.isEmpty()]

    @staticmethod
    def _hint(item: QLayoutItem) -> QSize:
        hint = item.sizeHint()
        minimum = item.minimumSize()
        return QSize(max(hint.width(), minimum.width()), max(hint.height(), minimum.height()))


def _expands(item: QLayoutItem) -> bool:
    widget = item.widget()
    if widget is None:
        return False
    return widget.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding


def retain_content_width(widget: QWidget) -> None:
    """Stop a toolbar control shrinking below its text, icon and padding."""
    if isinstance(widget, QPushButton):
        widget.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        icon = widget.iconSize().width() if not widget.icon().isNull() else 0
        text_width = widget.fontMetrics().horizontalAdvance(widget.text())
        style = widget.style()
        pad = style.pixelMetric(QStyle.PixelMetric.PM_ButtonMargin, None, widget)
        frame = style.pixelMetric(QStyle.PixelMetric.PM_DefaultFrameWidth, None, widget)
        widget.setMinimumWidth(text_width + icon + pad * 2 + frame * 2)
        return
    if isinstance(widget, QLabel):
        widget.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        widget.setMinimumWidth(widget.fontMetrics().horizontalAdvance(widget.text()) + 8)
        return
    if isinstance(widget, QComboBox):
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        widget.setMinimumWidth(max(160, widget.minimumSizeHint().width()))
