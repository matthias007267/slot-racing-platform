"""The start-light widget draws the cue. It does not start a race."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QColor
from pytestqt.qtbot import QtBot

from slot_racing.modules.races.ui.start_cue import StartCueStep, StartPhase
from slot_racing.modules.races.ui.start_lights import StartLightWidget


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
    lights.resize(lights.minimumWidth(), lights.minimumHeight())
    image = lights.grab().toImage()
    color: QColor = image.pixelColor(int(image.width() * across), image.height() // 2)
    return int(color.red())
