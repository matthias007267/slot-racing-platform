"""The start-light widget draws the cue. It does not start a race."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QColor
from pytestqt.qtbot import QtBot

from slot_racing.modules.races.hud import LIGHT_ASPECT
from slot_racing.modules.races.ui.start_cue import StartCueStep, StartPhase
from slot_racing.modules.races.ui.start_lights import StartLightWidget


@pytest.mark.parametrize("width", [560, 1100, 1920])
def test_five_round_chambers_fit_the_gantry_at_each_window_width(qtbot: QtBot, width: int) -> None:
    lights = StartLightWidget()
    qtbot.addWidget(lights)
    lights.resize(width, lights.heightForWidth(width))
    lights.show_lights(0)
    rects = lights.lamp_rects()
    assert len(rects) == 5
    gaps = [rects[index + 1].left() - rects[index].right() for index in range(4)]
    for rect in rects:
        assert abs(rect.width() - rect.height()) < 0.6
        assert rect.width() > 12
        assert rect.left() >= 0
        assert rect.right() <= lights.width() + 0.5
        assert rect.bottom() <= lights.height()
    assert max(gaps) - min(gaps) < 1.0
    assert min(gaps) > 4
    assert _active_count(lights) == 0
    assert all(color.red() > 8 for color in _lamp_colors(lights))


def test_lit_lamps_are_the_only_ones_that_glow(qtbot: QtBot) -> None:
    lights = StartLightWidget()
    qtbot.addWidget(lights)
    lights.resize(1100, lights.heightForWidth(1100))
    lights.show_lights(1)
    assert _active_count(lights) == 1
    colors = _lamp_colors(lights)
    assert colors[0].red() > colors[1].red() + 40
    lights.show_lights(5)
    assert _active_count(lights) == 5
    lights.show_step(StartCueStep(StartPhase.START_SIGNAL, 0, "go"))
    assert lights.showing_go
    assert _active_count(lights) == 0


def test_the_gantry_starts_dark_and_lights_one_lamp_at_a_time(qtbot: QtBot) -> None:
    lights = StartLightWidget()
    qtbot.addWidget(lights)
    assert lights.lit_lights == 0
    assert not lights.showing_go
    assert lights.isHidden()
    assert lights.minimumWidth() > 400

    lights.show_lights(1)
    assert lights.lit_lights == 1
    assert lights.isVisible()
    dark = _redness(lights, 0.87)
    lit = _redness(lights, 0.13)
    assert lit > dark + 40

    for count in range(2, 6):
        lights.show_lights(count)
        assert lights.lit_lights == count
        assert not lights.showing_go
    assert _redness(lights, 0.87) > 80

    with pytest.raises(ValueError, match="between 0 and 5"):
        lights.show_lights(6)


def test_an_overlay_grows_past_the_old_lamp_cap(qtbot: QtBot) -> None:
    lights = StartLightWidget()
    qtbot.addWidget(lights)
    lights.use_as_overlay()
    width = 1400
    height = round(width / LIGHT_ASPECT)
    lights.resize(width, height)
    lights.show_lights(2)
    lamps = lights.lamp_rects()
    assert lamps[0].width() > 92
    for lamp in lamps:
        assert abs(lamp.width() - lamp.height()) < 0.6
        assert lamp.right() <= width + 0.5
        assert lamp.bottom() <= height + 0.5
    assert lights.heightForWidth(width) == height


def test_go_puts_every_lamp_out_and_then_the_gantry_can_leave(qtbot: QtBot) -> None:
    lights = StartLightWidget()
    qtbot.addWidget(lights)
    lights.show_step(StartCueStep(StartPhase.START_SIGNAL, 0, "go"))
    assert lights.lit_lights == 0
    assert lights.showing_go
    assert lights.isVisible()
    assert _redness(lights, 0.13) < 140
    lights.clear()
    assert lights.isHidden()
    assert lights.lit_lights == 0
    assert not lights.showing_go


def _redness(lights: StartLightWidget, across: float) -> int:
    lights.resize(lights.minimumWidth(), lights.heightForWidth(lights.minimumWidth()))
    image = lights.grab().toImage()
    center_y = int(lights.lamp_rects()[0].center().y())
    color: QColor = image.pixelColor(int(image.width() * across), center_y)
    return int(color.red())


def _lamp_colors(lights: StartLightWidget) -> list[QColor]:
    image = lights.grab().toImage()
    return [
        image.pixelColor(int(rect.center().x()), int(rect.center().y()))
        for rect in lights.lamp_rects()
    ]


def _active_count(lights: StartLightWidget) -> int:
    return sum(1 for color in _lamp_colors(lights) if color.red() > 150)
