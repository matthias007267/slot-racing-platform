"""Five red start lights. This widget only draws a step it is given.

It does not start a race, open a camera, keep a clock, or play a sound. The start
cue owns the sequence and tells the widget how many lamps are lit.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPaintEvent, QRadialGradient, QResizeEvent
from PySide6.QtWidgets import QSizePolicy, QWidget

from slot_racing.modules.races.ui.start_cue import START_LIGHT_COUNT, StartCueStep, StartPhase
from slot_racing.uikit.theme import COLORS

# Proportions of one lamp. The housing is derived from the widget width, then
# capped so a large window does not hand the whole screen to the gantry.
_MAX_LAMP = 92.0
_SIDE_MARGIN = 12.0
_PAD_X = 0.50
_PAD_Y = 0.42
_GAP = 0.40
_SPAN = 2 * _PAD_X + START_LIGHT_COUNT + (START_LIGHT_COUNT - 1) * _GAP
_MIN_WIDTH = 480


@dataclass(frozen=True, slots=True)
class _Gantry:
    housing: QRectF
    lamps: tuple[QRectF, ...]
    go_band: float


class StartLightWidget(QWidget):
    """A horizontal gantry of five lamps, dark until a step lights them.

    ``show_lights`` is the whole interface. ``lit`` counts lamps from the left.
    ``go`` draws every lamp dark and shows GO. That word is feedback for the
    start signal the cue has already given. It is not a second start.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("start-lights")
        self._lit = 0
        self._go = False
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setMinimumWidth(_MIN_WIDTH)
        self._sync_height()
        self.hide()

    @property
    def lit_lights(self) -> int:
        return self._lit

    @property
    def showing_go(self) -> bool:
        return self._go

    def lamp_rects(self) -> tuple[QRectF, ...]:
        """The five lamp faces, left to right, in widget coordinates."""
        return self._gantry().lamps

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
        self._sync_height()
        self.show()
        self.update()

    def clear(self) -> None:
        """Back to five dark lamps, and leave the live view clear."""
        self._lit = 0
        self._go = False
        self._sync_height()
        self.hide()

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._height_for(max(width, 1))

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_height()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gantry = self._gantry()
        self._draw_housing(painter, gantry.housing)
        for index, rect in enumerate(gantry.lamps):
            self._draw_chamber(painter, rect)
            self._draw_lamp(painter, rect, on=index < self._lit)
        if self._go:
            self._draw_go(painter, gantry)
        painter.end()

    def _sync_height(self) -> None:
        width = self.width() if self.width() > 0 else _MIN_WIDTH
        height = self._height_for(width)
        if self.minimumHeight() != height:
            self.setMinimumHeight(height)

    def _height_for(self, width: int) -> int:
        gantry = self._measure(width)
        extra = gantry.go_band if self._go else 0.0
        return int(gantry.housing.bottom() + extra + 8)

    def _gantry(self) -> _Gantry:
        return self._measure(max(self.width(), 1))

    def _measure(self, width: int) -> _Gantry:
        span = max(float(width) - _SIDE_MARGIN * 2, 1.0)
        lamp = min(_MAX_LAMP, span / _SPAN)
        pad_x = _PAD_X * lamp
        pad_y = _PAD_Y * lamp
        gap = _GAP * lamp
        housing_w = pad_x * 2 + START_LIGHT_COUNT * lamp + (START_LIGHT_COUNT - 1) * gap
        housing_h = pad_y * 2 + lamp
        origin_x = (float(width) - housing_w) / 2
        housing = QRectF(origin_x, 6.0, housing_w, housing_h)
        left = origin_x + pad_x
        top = housing.top() + pad_y
        lamps = tuple(
            QRectF(left + index * (lamp + gap), top, lamp, lamp)
            for index in range(START_LIGHT_COUNT)
        )
        return _Gantry(housing, lamps, lamp * 0.78)

    def _draw_housing(self, painter: QPainter, housing: QRectF) -> None:
        radius = min(housing.height() * 0.22, 22.0)
        painter.setPen(QColor(COLORS.border))
        painter.setBrush(QColor(COLORS.background).lighter(118))
        painter.drawRoundedRect(housing, radius, radius)

    def _draw_chamber(self, painter: QPainter, lamp: QRectF) -> None:
        inset = lamp.width() * 0.08
        bezel = lamp.adjusted(-inset, -inset, inset, inset)
        painter.setPen(QColor(COLORS.border).darker(140))
        painter.setBrush(QColor(COLORS.background))
        painter.drawEllipse(bezel)

    def _draw_lamp(self, painter: QPainter, rect: QRectF, *, on: bool) -> None:
        center = rect.center()
        radius = rect.width() / 2
        if on:
            glow = QRadialGradient(center, radius * 1.35)
            color = QColor(COLORS.error)
            color.setAlpha(70)
            glow.setColorAt(0.0, color)
            glow.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(center, radius * 1.35, radius * 1.35)
            body = QRadialGradient(center, radius)
            body.setColorAt(0.0, QColor(COLORS.error).lighter(165))
            body.setColorAt(0.45, QColor(COLORS.error))
            body.setColorAt(1.0, QColor(COLORS.error).darker(140))
            painter.setBrush(body)
            painter.drawEllipse(rect)
            painter.setBrush(QColor(255, 236, 230, 180))
            highlight = rect.adjusted(radius * 0.42, radius * 0.28, -radius * 0.95, -radius * 1.05)
            painter.drawEllipse(highlight)
            return
        body = QRadialGradient(center, radius)
        body.setColorAt(0.0, QColor(COLORS.error).darker(220))
        body.setColorAt(1.0, QColor(COLORS.error).darker(380))
        painter.setPen(QColor(COLORS.border).darker(150))
        painter.setBrush(body)
        painter.drawEllipse(rect)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 28))
        reflection = rect.adjusted(radius * 0.55, radius * 0.38, -radius * 1.15, -radius * 1.25)
        painter.drawEllipse(reflection)

    def _draw_go(self, painter: QPainter, gantry: _Gantry) -> None:
        font = QFont(self.font())
        font.setPixelSize(max(18, int(gantry.lamps[0].height() * 0.62)))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(COLORS.accent))
        painter.drawText(
            0,
            int(gantry.housing.bottom()),
            self.width(),
            int(gantry.go_band),
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
            "GO",
        )
