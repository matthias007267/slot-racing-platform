"""Start-number selection. The list is the numbers already stored, nothing else."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from slot_racing.core.i18n import Translator
from slot_racing.uikit.theme import set_role


class StartNumberPicker(QWidget):
    """Dropdown of defined start numbers, with up and down arrows.

    A value that is not already in the list cannot be chosen. There is no numeric range.
    """

    def __init__(
        self,
        translator: Translator,
        numbers: Sequence[str],
        current: int | str | None,
        object_name: str,
    ) -> None:
        super().__init__()
        self.combo = QComboBox()
        self.combo.setObjectName(object_name)
        self.combo.addItem(translator.translate("common.none"), "")
        seen: set[str] = set()
        for number in numbers:
            if number and number not in seen:
                seen.add(number)
                self.combo.addItem(number, number)
        if current is not None:
            token = str(current)
            if token and token not in seen:
                self.combo.addItem(token, token)
        self.setValue(current)

        up = QPushButton("\u2191")
        up.setObjectName(f"{object_name}-up")
        down = QPushButton("\u2193")
        down.setObjectName(f"{object_name}-down")
        for button in (up, down):
            set_role(button, "ghost")
            button.setFixedWidth(28)
        up.clicked.connect(lambda: self._step(1))
        down.clicked.connect(lambda: self._step(-1))
        arrows = QVBoxLayout()
        arrows.setContentsMargins(0, 0, 0, 0)
        arrows.setSpacing(0)
        arrows.addWidget(up)
        arrows.addWidget(down)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.combo, 1)
        layout.addLayout(arrows)

    def choices(self) -> list[str]:
        """Defined tokens offered by the field. The empty 'none' entry is omitted."""
        values: list[str] = []
        for index in range(self.combo.count()):
            token = self.combo.itemData(index)
            if token:
                values.append(str(token))
        return values

    def setValue(self, number: int | str | None) -> None:  # noqa: N802
        """Select ``number`` when it is already offered. Anything else stays unselected."""
        token = "" if number is None else str(number)
        index = self.combo.findData(token)
        if index >= 0:
            self.combo.setCurrentIndex(index)

    def value(self) -> int | str | None:
        token = self.combo.currentData()
        if token is None or token == "":
            return None
        text = str(token)
        if text.isdigit():
            return int(text)
        return text

    def _step(self, delta: int) -> None:
        index = self.combo.currentIndex() + delta
        if 0 <= index < self.combo.count():
            self.combo.setCurrentIndex(index)
