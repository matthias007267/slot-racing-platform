"""Surface cards. Darker than the page background, with a quiet border and no glow."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout

from slot_racing.uikit.theme import SPACE, set_role


class Card(QFrame):
    def __init__(self, title: str | None = None) -> None:
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        set_role(self, "card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(SPACE.md, SPACE.md, SPACE.md, SPACE.md)
        self.body.setSpacing(SPACE.sm)
        if title is not None:
            label = QLabel(title)
            set_role(label, "card-title")
            self.body.addWidget(label)


class MetricCard(Card):
    """A short label, a large number and one line of context. The number is real data."""

    def __init__(self, title: str, value: str, caption: str, object_name: str) -> None:
        super().__init__(title)
        number = QLabel(value)
        number.setObjectName(object_name)
        set_role(number, "metric")
        note = QLabel(caption)
        set_role(note, "caption")
        self.body.addWidget(number)
        self.body.addWidget(note)
