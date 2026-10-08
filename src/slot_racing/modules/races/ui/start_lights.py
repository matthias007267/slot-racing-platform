"""Five red start lights. This widget only draws a step it is given.

It does not start a race, open a camera, keep a clock, or play a sound. The start
cue owns the sequence and tells the widget how many lamps are lit.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QRadialGradient, QResizeEvent
from PySide6.QtWidgets import QSizePolicy, QWidget

from slot_racing.modules.races.hud import (
    LIGHT_ASPECT,
    LIGHT_GAP,
    LIGHT_PAD_X,
    LIGHT_PAD_Y,
    LIGHT_UNIT_HEIGHT,
    LIGHT_UNIT_WIDTH,
)
from slot_racing.modules.races.ui.start_cue import START_LIGHT_COUNT, StartCueStep, StartPhase
from slot_racing.uikit.theme import COLORS

# A layout that is not an overlay still wants a usable minimum. The overlay
# path sizes the gantry from the frame, with no separate pixel cap.
_MIN_WIDTH = 480


@dataclass(frozen=True, slots=True)
class _Gantry:
    housing: QRectF
    lamps: tuple[QRectF, ...]


class StartLightWidget(QWidget):
    """A horizontal gantry of five lamps, dark until a step lights them.

    ``show_lights`` is the whole interface. ``lit`` counts lamps from the left.
    ``go`` draws every lamp dark. That lights-out step is the start signal the
    cue has already given. It is not a second start.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("start-lights")
        self._lit = 0
        self._go = False
        self._overlay = False
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setMinimumWidth(_MIN_WIDTH)
        self._sync_height()
        self.hide()

    def use_as_overlay(self) -> None:
        """Draw at a geometry the host assigns. Showing the gantry does not reflow a layout."""
        self._overlay = True
        self.setMinimumSize(1, 1)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

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
        """Light ``lit`` lamps from the left, or show the lights-out start signal."""
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
            self._draw_lamp(painter, rect, on=index < self._lit and not self._go)
        painter.end()

    def _sync_height(self) -> None:
        if self._overlay:
            return
        width = self.width() if self.width() > 0 else _MIN_WIDTH
        height = self._height_for(width)
        if self.minimumHeight() != height:
            self.setMinimumHeight(height)

    def _height_for(self, width: int) -> int:
        return max(1, round(width / LIGHT_ASPECT))

    def _gantry(self) -> _Gantry:
        width = max(self.width(), 1)
        if self._overlay and self.height() > 1:
            return _fit_gantry(width, self.height())
        return _fit_gantry(width, self._height_for(width))

    def _draw_housing(self, painter: QPainter, housing: QRectF) -> None:
        radius = housing.height() * 0.22
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


def _fit_gantry(width: int, height: int) -> _Gantry:
    """Scale the whole gantry into the box. Lamps stay round at every size."""
    box_w = max(float(width), 1.0)
    box_h = max(float(height), 1.0)
    lamp = min(box_w / LIGHT_UNIT_WIDTH, box_h / LIGHT_UNIT_HEIGHT)
    housing_w = LIGHT_UNIT_WIDTH * lamp
    housing_h = LIGHT_UNIT_HEIGHT * lamp
    origin_x = (box_w - housing_w) / 2
    origin_y = (box_h - housing_h) / 2
    housing = QRectF(origin_x, origin_y, housing_w, housing_h)
    left = origin_x + LIGHT_PAD_X * lamp
    top = origin_y + LIGHT_PAD_Y * lamp
    step = lamp * (1.0 + LIGHT_GAP)
    lamps = tuple(
        QRectF(left + index * step, top, lamp, lamp) for index in range(START_LIGHT_COUNT)
    )
    return _Gantry(housing, lamps)
