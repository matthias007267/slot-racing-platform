"""Five red start lights. This widget only draws a step it is given.

It does not start a race, open a camera, or keep a clock. The start cue owns
the sequence and tells the widget how many lamps are lit.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPaintEvent
from PySide6.QtWidgets import QSizePolicy, QWidget

from slot_racing.modules.races.ui.start_cue import START_LIGHT_COUNT, StartCueStep, StartPhase
from slot_racing.uikit.theme import COLORS

_LAMP = 78
_GAP = 26
_PAD_X = 36
_PAD_Y = 28
_GO_BAND = 72


class StartLightWidget(QWidget):
    """A horizontal gantry of five lamps, dark until a step lights them.

    ``show_lights`` is the whole interface. ``lit`` counts lamps from the left.
    ``go`` draws the lamps dark and shows GO. That word is feedback for the
    start signal the cue has already given. It is not a second start.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("start-lights")
        self._lit = 0
        self._go = False
        lamps = START_LIGHT_COUNT * _LAMP + (START_LIGHT_COUNT - 1) * _GAP
        self._housing_width = _PAD_X * 2 + lamps
        self._housing_height = _PAD_Y * 2 + _LAMP
        self.setMinimumWidth(self._housing_width)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._fit_height()
        self.hide()

    @property
    def lit_lights(self) -> int:
        return self._lit

    @property
    def showing_go(self) -> bool:
        return self._go

    def show_step(self, step: StartCueStep) -> None:
        """Draw one cue step. The race start stays with the cue."""
        self.show_lights(step.lit_lights, go=step.phase is StartPhase.START_SIGNAL)

    def show_lights(self, lit: int, *, go: bool = False) -> None:
        """Light ``lit`` lamps from the left, or show the lights-out GO mark."""
        if lit < 0 or lit > START_LIGHT_COUNT:
            raise ValueError("lit lights must be between 0 and 5")
        if go and lit != 0:
            raise ValueError("the start signal shows every light off")
        self._lit = lit
        self._go = go
        self._fit_height()
        self.show()
        self.update()

    def clear(self) -> None:
        """Back to five dark lamps, and leave the live view clear."""
        self._lit = 0
        self._go = False
        self._fit_height()
        self.hide()

    def _fit_height(self) -> None:
        extra = _GO_BAND if self._go else 0
        self.setMinimumHeight(self._housing_height + extra + 8)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        housing = self._housing_rect()
        painter.setPen(QColor(COLORS.border))
        painter.setBrush(QColor(COLORS.elevated))
        painter.drawRoundedRect(housing, 18, 18)
        top = housing.top() + _PAD_Y
        left = housing.left() + _PAD_X
        for index in range(START_LIGHT_COUNT):
            x = left + index * (_LAMP + _GAP)
            self._draw_lamp(painter, x, top, on=index < self._lit)
        if self._go:
            self._draw_go(painter, housing.bottom())
        painter.end()

    def _housing_rect(self) -> QRect:
        x = max(0, (self.width() - self._housing_width) // 2)
        return QRect(x, 4, self._housing_width, self._housing_height)

    def _draw_lamp(self, painter: QPainter, x: int, y: int, *, on: bool) -> None:
        rect = QRectF(x, y, _LAMP, _LAMP)
        if on:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(COLORS.error))
            painter.drawEllipse(rect)
            painter.setBrush(QColor(COLORS.error).lighter(150))
            inset = rect.adjusted(_LAMP * 0.22, _LAMP * 0.16, -_LAMP * 0.42, -_LAMP * 0.48)
            painter.drawEllipse(inset)
            return
        painter.setPen(QColor(COLORS.border))
        painter.setBrush(QColor(COLORS.error).darker(320))
        painter.drawEllipse(rect)

    def _draw_go(self, painter: QPainter, housing_bottom: int) -> None:
        font = QFont(self.font())
        font.setPixelSize(54)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(COLORS.accent))
        painter.drawText(
            0,
            housing_bottom,
            self.width(),
            _GO_BAND,
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
            "GO",
        )
